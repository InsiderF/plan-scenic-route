#!/usr/bin/env python3
"""Bounded open-map extraction, evidence-gated routing, and self-contained maps.

Python 3.10+, standard library only. See references/workflow.md for JSON schemas.
"""
import argparse
import datetime as dt
import hashlib
import heapq
import html
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODES = {"walk": "步行", "shuttle": "接驳车", "cablecar": "索道", "boat": "游船"}
BLOCKED = {"motorway", "motorway_link", "construction", "proposed", "raceway"}

# 语音讲解仅此两项外部工具，缺一即按停止协议暂停并向用户问询（见 references/audio-guide.md）
AUDIO_REQUIREMENTS = ("edge-tts（微软 TTS 命令行，构建时需联网）", "ffmpeg/ffprobe（音频拼接与响度归一）")


class Insufficient(ValueError):
    pass


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, obj):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def distance(a, b):
    """Metres, coordinate order longitude/latitude."""
    x1, y1, x2, y2 = map(math.radians, (*a, *b))
    h = math.sin((y2-y1)/2)**2 + math.cos(y1)*math.cos(y2)*math.sin((x2-x1)/2)**2
    return 12742000 * math.asin(min(1, math.sqrt(h)))


def source_ok(src):
    if not isinstance(src, dict):
        return False
    url = urllib.parse.urlparse(str(src.get("url", "")))
    try:
        dt.date.fromisoformat(str(src.get("checked_at", ""))[:10])
    except ValueError:
        return False
    return url.scheme == "https" and bool(url.netloc)


def get_elements(raw):
    if raw.get("remark"):
        raise Insufficient("数据服务返回不完整结果：" + raw["remark"])
    if not isinstance(raw.get("elements"), list) or not raw["elements"]:
        raise Insufficient("开放地图为空或格式错误")
    index = {}
    for e in raw["elements"]:
        key = (e["type"], e["id"])
        index[key] = {**index.get(key, {}), **e}
    return index


def fetch(args):
    s, w, n, e = args.bbox
    if not (-85 <= s < n <= 85 and -180 <= w < e <= 180):
        raise Insufficient("bbox 顺序必须是 south west north east")
    area = distance([w, s], [e, s]) * distance([w, s], [w, n]) / 1e6
    if area > 150:
        raise Insufficient("查询超过150平方公里；缩小到单个景区及连接道路")
    box = f"({s},{w},{n},{e})"
    filters = ['way["highway"]', 'nwr["natural"="water"]',
               'nwr["landuse"~"forest|grass|meadow|recreation_ground"]',
               'nwr["leisure"~"park|garden"]', 'nwr["tourism"~"attraction|viewpoint"]',
               'nwr["historic"]', 'nwr["amenity"~"toilets|drinking_water|bench|place_of_worship"]',
               'nwr["entrance"]["name"]', 'nwr["man_made"="pier"]["name"]']
    query = '[out:json][timeout:90];(' + ''.join(f+box+';' for f in filters) + ');out body;>;out skel qt;'
    if args.out.exists() and not args.refresh:
        get_elements(read(args.out))
        print(json.dumps({"status": "cached", "file": str(args.out)}, ensure_ascii=False))
        return
    if not args.endpoint.startswith("https://"):
        raise Insufficient("数据服务必须使用 HTTPS")
    req = urllib.request.Request(args.endpoint, data=urllib.parse.urlencode({"data": query}).encode(),
                                 headers={"User-Agent": "plan-scenic-route/1.0 (bounded OSM extract)",
                                          "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=110) as response:
        raw = json.load(response)
    get_elements(raw)
    raw["_provenance"] = {"endpoint": args.endpoint, "fetched_at": now(), "bbox": args.bbox,
                           "query": query, "license": "ODbL-1.0", "attribution": "© OpenStreetMap contributors"}
    write(args.out, raw)
    print(json.dumps({"status": "fetched", "elements": len(raw["elements"]), "snapshot": raw.get("osm3s", {})}, ensure_ascii=False))


# area=yes 但属于可步行线型的（广场/桥面/庭院铺装面），保留其边界环参与路由：
# 沿边走真实可行（如丹陛桥桥面）；纯庭院墙圈仍不可穿越——门洞没建线时图上不连通，
# 入口必须人工核实 access_node，不得靠 ring 边界"贴"进去。
AREA_WALKABLE = {"pedestrian", "footway", "path", "steps", "track"}


def walk_allowed(t):
    h = t.get("highway")
    if not h or h in BLOCKED:
        return False
    if t.get("area") == "yes" and h not in AREA_WALKABLE:
        return False
    if h in {"trunk", "trunk_link"} and t.get("foot") not in {"yes", "designated", "permissive"}:
        return False
    if t.get("foot") in {"no", "private", "use_sidepath"}:
        return False
    if t.get("access") in {"no", "private", "customers"} and t.get("foot") not in {"yes", "designated", "permissive"}:
        return False
    if t.get("foot:conditional") or t.get("access:conditional") or t.get("highway") == "via_ferrata":
        return False
    return True


def graph(index, speed=3.5, steps_penalty=1.35, avoid_steps=False):
    coords = {i: [e["lon"], e["lat"]] for (typ, i), e in index.items() if typ == "node"}
    adj = {}
    blocked_nodes = {i for (typ, i), e in index.items() if typ == "node" and
                     (e.get("tags", {}).get("foot") in {"no", "private"} or
                      (e.get("tags", {}).get("access") in {"no", "private"} and
                       e.get("tags", {}).get("foot") not in {"yes", "designated", "permissive"}))}
    for (typ, wid), e in index.items():
        t = e.get("tags", {})
        if typ != "way" or not walk_allowed(t) or (avoid_steps and t.get("highway") == "steps"):
            continue
        ns = e.get("nodes", [])
        if any(i not in coords for i in ns):
            raise Insufficient(f"道路 {wid} 的节点数据不完整")
        for a, b in zip(ns, ns[1:]):
            if a in blocked_nodes or b in blocked_nodes:
                continue
            metres = distance(coords[a], coords[b])
            minutes = metres / (speed*1000/60) * (steps_penalty if t.get("highway") == "steps" else 1)
            metadata = {"mode": "walk", "metres": metres, "minutes": minutes, "way_id": wid,
                        "name": t.get("name:zh", t.get("name", "步道")),
                        "highway": t.get("highway"), "bridge": t.get("bridge") not in {None, "no"},
                        "from_node": a, "to_node": b, "geometry": [coords[a], coords[b]]}
            direction = t.get("oneway:foot", "no")
            if direction != "-1":
                adj.setdefault(a, []).append((b, metadata))
            if direction not in {"yes", "1", "true"}:
                adj.setdefault(b, []).append((a, {**metadata, "from_node": b, "to_node": a,
                                                 "geometry": [coords[b], coords[a]]}))
    if not adj:
        raise Insufficient("没有可用步行路网")
    return coords, adj


def snap_options(coords, adj, p, count=5):
    return [{"access_node": i, "distance_m": round(d, 1), "coordinate": coords[i]}
            for d, i in sorted((distance(p, coords[i]), i) for i in adj)[:count]]


def inspect(args):
    raw = read(args.osm)
    index = get_elements(raw)
    coords, adj = graph(index)
    pois = []
    for (typ, i), e in index.items():
        t = e.get("tags", {})
        name = t.get("name:zh", t.get("name"))
        if not name or not (t.get("tourism") or t.get("historic") or t.get("leisure") in {"park", "garden"}):
            continue
        if args.name and not any(k in name for k in args.name):
            continue
        if typ == "node":
            p = coords[i]
        elif typ == "way":
            ns = [coords[x] for x in e.get("nodes", []) if x in coords]
            if not ns:
                continue
            p = [sum(a[0] for a in ns)/len(ns), sum(a[1] for a in ns)/len(ns)]
        else:
            continue
        pois.append({"osm_type": typ, "osm_id": i, "name": name, "coordinate": p,
                     "tags": t, "access_candidates": snap_options(coords, adj, p),
                     "warning": "邻近节点仅是候选；需核实可通行入口，不能把中心点到路网的直线当作道路。"})
    out = {"snapshot": raw.get("osm3s", {}), "graph_nodes": len(adj), "pois": pois}
    if args.out:
        write(args.out, out)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def add_transit(spec, coords, adj, allowed):
    for t in spec.get("transit", []):
        if t["mode"] not in allowed:
            continue
        if t["mode"] not in {"shuttle", "cablecar", "boat"}:
            raise Insufficient("不支持的景区交通方式")
        if not source_ok(t.get("service_source")) or not source_ok(t.get("geometry_source")):
            raise Insufficient("景区交通缺少运营与开放几何证据")
        if t.get("geometry_license") not in {"ODbL-1.0", "CC0-1.0", "CC-BY-4.0", "public-domain"}:
            raise Insufficient("景区交通几何缺少受支持的开放许可")
        if t.get("availability") != "verified":
            raise Insufficient("景区交通运营状态未核实")
        a, b = t["from_access_node"], t["to_access_node"]
        if a not in adj or b not in adj or not t.get("geometry"):
            raise Insufficient("景区交通端点未连接步行路网")
        geom = t["geometry"]
        if len(geom) < 2 or distance(geom[0], coords[a]) > 25 or distance(geom[-1], coords[b]) > 25:
            raise Insufficient("景区交通几何与访问节点不一致")
        # Keep the known connector distances visible and timed; do not draw fictitious connector lines.
        # Require exact endpoints to avoid cutting a shoreline or barrier with an implicit connection.
        if distance(geom[0], coords[a]) > 1 or distance(geom[-1], coords[b]) > 1:
            raise Insufficient("交通几何端点需是路网节点；补充真实连接路段后重试")
        travel, wait = float(t["travel_minutes"]), float(t["wait_minutes"])
        if travel <= 0 or wait < 0:
            raise Insufficient("景区交通时间参数无效")
        metadata = {"mode": t["mode"], "metres": sum(distance(x, y) for x, y in zip(geom, geom[1:])),
                    "minutes": travel+wait, "name": t["name"], "geometry": geom,
                    "source": t["service_source"], "wait_minutes": wait}
        adj[a].append((b, metadata))
        if t.get("bidirectional"):
            adj[b].append((a, {**metadata, "geometry": list(reversed(geom))}))


def shortest(adj, start, end):
    best = {start: 0}
    previous = {}
    q = [(0, start)]
    while q:
        cost, node = heapq.heappop(q)
        if cost != best.get(node):
            continue
        if node == end:
            edges = []
            while node != start:
                before, data = previous[node]
                edges.append(data)
                node = before
            return list(reversed(edges)), cost
        for nxt, data in adj.get(node, []):
            new = cost + data["minutes"]
            if new < best.get(nxt, math.inf):
                best[nxt], previous[nxt] = new, (node, data)
                heapq.heappush(q, (new, nxt))
    raise Insufficient(f"访问节点 {start} 与 {end} 之间无可用道路连接")


def verify_poi(p, coords, adj, index, test):
    if not p.get("id") or not p.get("name") or not source_ok(p.get("source")):
        raise Insufficient("景点缺少名称、标识或来源")
    if not p.get("access_verified") or not source_ok(p.get("access_source")):
        raise Insufficient(p["name"]+"：尚未核实可通行访问点")
    node = p.get("access_node")
    if node not in adj:
        raise Insufficient(p["name"]+"：访问点不在可用路网中")
    if p.get("osm_type") and p.get("osm_id"):
        osm = index.get((p["osm_type"], p["osm_id"]))
        if not osm:
            raise Insufficient(p["name"]+"：来源要素不在地图数据中")
        if osm["type"] == "node" and distance(coords[osm["id"]], coords[node]) > p.get("max_access_distance_m", 80):
            raise Insufficient(p["name"]+"：访问点距离过远，需要明确入口证据")
    if p.get("availability") == "closed":
        raise Insufficient(p["name"]+"：已知关闭")
    if not test and (p.get("availability") != "verified" or not source_ok(p.get("availability_source"))):
        raise Insufficient(p["name"]+"：实际行程需核实通行状态")
    if p.get("dwell_minutes", 0) < 0:
        raise Insufficient("停留时间不能为负")
    return {**p, "coordinate": coords[node]}


def validate_spec(spec, index, test):
    if spec.get("data_gate", {}).get("status") != "sufficient" or not spec["data_gate"].get("notes"):
        raise Insufficient("未通过地图数据充分性检查")
    if not test and (not spec.get("date") or not spec.get("start_time")):
        raise Insufficient("实际行程需提供出行日期与开始时间")
    if not test:
        dt.date.fromisoformat(spec["date"])
    budget = float(spec["budget_minutes"])
    rest = float(spec.get("rest_minutes", 30))
    speed = float(spec.get("walk_speed_kmh", 3.5))
    if not (30 <= budget <= 1440 and 0 <= rest < budget and 1 <= speed <= 6):
        raise Insufficient("时长、休息或步行速度参数无效")
    coords, adj = graph(index, speed, avoid_steps=spec.get("avoid_steps", False))
    allowed = spec.get("allowed_modes", ["walk"])
    if "walk" not in allowed or any(m not in MODES for m in allowed):
        raise Insufficient("交通方式配置无效")
    if spec.get("transit"):
        add_transit(spec, coords, adj, allowed)
    pois = {p["id"]: verify_poi(p, coords, adj, index, test) for p in spec["pois"]}
    if len(pois) != len(spec["pois"]):
        raise Insufficient("景点标识重复")
    return coords, adj, pois, budget, rest


def schedule(stops, adj, pois, rest, start_time=None):
    if len(stops) < 2:
        raise Insufficient("至少需要起点和终点")
    if any(s not in pois for s in stops):
        raise Insufficient("路线包含未登记景点")
    legs, elapsed = [], 0
    for a, b in zip(stops, stops[1:]):
        edges, minutes = shortest(adj, pois[a]["access_node"], pois[b]["access_node"])
        if not edges and a != b:
            raise Insufficient("不同景点的访问点重合，请明确行程")
        legs.append({"from": a, "to": b, "minutes": round(minutes, 2),
                     "walk_metres": round(sum(x["metres"] for x in edges if x["mode"] == "walk"), 1),
                     "total_metres": round(sum(x["metres"] for x in edges), 1), "edges": edges})
    elapsed = sum(x["minutes"] for x in legs) + sum(pois[s].get("dwell_minutes", 0) for s in stops) + rest
    # Check each restricted stop at its predicted arrival; do not silently move beyond closing time.
    arrivals = {}
    if start_time:
        hh, mm = map(int, start_time.split(":"))
        if not (0 <= hh < 24 and 0 <= mm < 60):
            raise Insufficient("开始时间必须为HH:MM")
        cursor = hh*60+mm
        for i, sid in enumerate(stops):
            p = pois[sid]
            if i:
                cursor += legs[i-1]["minutes"]
            arrivals[sid] = round(cursor)
            for interval in p.get("opening_intervals", []):
                if len(interval) != 2:
                    raise Insufficient("开放时间区间格式错误")
            if p.get("opening_intervals") and not any(lo <= cursor and cursor+p.get("dwell_minutes", 0) <= hi for lo, hi in p["opening_intervals"]):
                raise Insufficient(p["name"]+"：到达/停留时间超出已核实的开放时段")
            if p.get("time_sensitive") and not p.get("opening_intervals"):
                raise Insufficient(p["name"]+"：缺少已核实开放区间")
            cursor += p.get("dwell_minutes", 0)
        # Rest is deliberately unassigned; require a conservative closing-time check as well.
        for sid in stops:
            p = pois[sid]
            if p.get("opening_intervals") and not any(lo <= arrivals[sid] and arrivals[sid]+rest+p.get("dwell_minutes", 0) <= hi for lo, hi in p["opening_intervals"]):
                raise Insufficient(p["name"]+"：考虑休息后的时段可行性不足")
    return legs, round(elapsed, 1), arrivals


def candidates(args):
    spec, raw = read(args.spec), read(args.osm)
    index = get_elements(raw)
    _, adj, pois, budget, rest = validate_spec(spec, index, args.test)
    plans = []
    for c in spec.get("candidates", []):
        if len(plans) >= 3:
            break
        try:
            if any(p.get("required") and p["id"] not in c["stops"] for p in pois.values()):
                raise Insufficient("候选遗漏必去景点")
            legs, total, _ = schedule(c["stops"], adj, pois, rest, spec.get("start_time"))
            if total > budget:
                raise Insufficient(f"预计{total:.0f}分钟，超过预算{budget:.0f}分钟")
            plans.append({"id": c["id"], "name": c["name"], "reason": c.get("reason", ""),
                          "start": pois[c["stops"][0]]["name"], "end": pois[c["stops"][-1]]["name"],
                          "stops": [pois[s]["name"] for s in c["stops"]],
                          "estimated_minutes": total, "walk_km": round(sum(l["walk_metres"] for l in legs)/1000, 2)})
        except Insufficient as exc:
            plans.append({"id": c["id"], "status": "unavailable", "reason": str(exc)})
    usable = sum(x.get("status") != "unavailable" for x in plans)
    if usable < 2:
        raise Insufficient("无法提供至少两个数据充分且时间可行的起终点方案；调整范围或查明缺失")
    out = {"status": "awaiting_choice", "test_only": args.test, "title": spec["title"], "candidates": plans}
    write(args.out, out)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def plan(args):
    spec, raw = read(args.spec), read(args.osm)
    index = get_elements(raw)
    coords, adj, pois, budget, rest = validate_spec(spec, index, args.test)
    chosen = next((c for c in spec.get("candidates", []) if c["id"] == args.choice), None)
    if not chosen:
        raise Insufficient("所选起终点方案不存在")
    if not args.test and not spec.get("user_choice_confirmed"):
        raise Insufficient("尚未获得用户的起终点选择")
    stops = list(chosen["stops"])
    required = [p["id"] for p in pois.values() if p.get("required")]
    if any(x not in stops for x in required):
        raise Insufficient("所选方案遗漏必去景点")
    # Add worthwhile optional stops by incremental cost; preserve candidate's deliberate order.
    optional = sorted((p for p in pois.values() if p.get("optional") and p["id"] not in stops),
                      key=lambda p: p.get("priority", 1), reverse=True)
    for p in optional:
        possibilities = []
        for pos in range(1, len(stops)):
            trial = stops[:pos]+[p["id"]]+stops[pos:]
            try:
                _, total, _ = schedule(trial, adj, pois, rest, spec.get("start_time"))
                if total <= budget:
                    possibilities.append((total, trial))
            except Insufficient:
                pass
        if possibilities:
            stops = min(possibilities, key=lambda x: x[0])[1]
    legs, total, arrivals = schedule(stops, adj, pois, rest, spec.get("start_time"))
    if total > budget:
        raise Insufficient(f"路线需要{total:.0f}分钟，超过{budget:.0f}分钟预算")
    features = map_features(index, coords)
    landmarks = map_landmarks(index, coords, features, legs, [pois[s] for s in stops])
    for leg in legs:
        leg["directions"] = directions(leg["edges"], adj, landmarks, pois[leg["from"]]["name"], pois[leg["to"]]["name"])
    route = {"title": spec["title"], "subtitle": chosen["name"], "choice": chosen["id"],
             "test_only": args.test, "generated_at": now(), "date": spec.get("date"),
             "budget_minutes": budget, "total_minutes": total, "rest_minutes": rest,
             "walk_speed_kmh": spec.get("walk_speed_kmh", 3.5), "arrivals": arrivals,
             "walk_km": round(sum(l["walk_metres"] for l in legs)/1000, 2),
             "stops": [pois[s] for s in stops], "legs": legs, "features": features, "landmarks": landmarks,
             "osm_snapshot": raw.get("osm3s", {}).get("timestamp_osm_base"),
             "provenance": {**raw.get("_provenance", {}), "sha256": hashlib.sha256(args.osm.read_bytes()).hexdigest(),
                            "license": "ODbL-1.0", "data_gate": spec["data_gate"]},
             "notice": "界面与算法测试样例，尚未核实出行当日通行情况。" if args.test else "估算时间含步行、停留及休息；请结合现场指示游览。"}
    write(args.out, route)
    print(json.dumps({k: route[k] for k in ["title", "test_only", "walk_km", "total_minutes", "osm_snapshot"]}, ensure_ascii=False))


def join_rings(parts):
    parts = [p[:] for p in parts if len(p) >= 2]
    rings = []
    while parts:
        chain = parts.pop()
        while chain[0] != chain[-1]:
            found = False
            for i, p in enumerate(parts):
                if chain[-1] == p[0]:
                    chain.extend(p[1:])
                elif chain[-1] == p[-1]:
                    chain.extend(list(reversed(p))[1:])
                elif chain[0] == p[-1]:
                    chain = p[:-1]+chain
                elif chain[0] == p[0]:
                    chain = list(reversed(p))[:-1]+chain
                else:
                    continue
                parts.pop(i)
                found = True
                break
            if not found:
                break
        if len(chain) >= 4 and chain[0] == chain[-1]:
            rings.append(chain)
    return rings


def contains(ring, point):
    x, y = point
    inside = False
    for (a, b), (c, d) in zip(ring, ring[1:]):
        if (b > y) != (d > y) and x < (c-a)*(y-b)/(d-b)+a:
            inside = not inside
    return inside


def map_features(index, coords):
    features, used = [], set()
    def kind(t):
        if t.get("natural") == "water" or t.get("water"):
            return "water"
        if t.get("landuse") in {"forest", "grass", "meadow", "recreation_ground"} or t.get("leisure") in {"park", "garden"}:
            return "green"
        if t.get("highway"):
            return "road"
    def feature(geometry, t, typ, fid):
        features.append({"type": "Feature", "properties": {"kind": typ, "name": t.get("name:zh", t.get("name", "")),
                         "highway": t.get("highway", ""), "osm_id": fid,
                         "osm_type": "relation" if geometry["type"] == "MultiPolygon" else "way"}, "geometry": geometry})
    for (typ, rid), rel in index.items():
        t = rel.get("tags", {})
        k = kind(t)
        if typ != "relation" or k not in {"water", "green"} or t.get("type") != "multipolygon":
            continue
        outer, inner, member_ids = [], [], []
        for m in rel.get("members", []):
            way = index.get(("way", m["ref"])) if m["type"] == "way" else None
            if not way:
                continue
            ns = way.get("nodes", [])
            if any(n not in coords for n in ns):
                continue
            (inner if m.get("role") == "inner" else outer).append(ns)
            member_ids.append(m["ref"])
        outers = [[coords[n] for n in ring] for ring in join_rings(outer)]
        inners = [[coords[n] for n in ring] for ring in join_rings(inner)]
        if outers:
            polygons = [[o]+[h for h in inners if contains(o, h[0])] for o in outers]
            feature({"type": "MultiPolygon", "coordinates": polygons}, t, k, rid)
            used.update(member_ids)
    for (typ, wid), e in index.items():
        t = e.get("tags", {})
        k = kind(t)
        if typ != "way" or not k or (wid in used and k != "road"):
            continue
        ns = e.get("nodes", [])
        if len(ns) < 2 or any(n not in coords for n in ns):
            continue
        points = [coords[n] for n in ns]
        if k in {"water", "green"}:
            if len(points) < 4 or ns[0] != ns[-1]:
                continue
            geometry = {"type": "Polygon", "coordinates": [points]}
        else:
            geometry = {"type": "LineString", "coordinates": points}
        feature(geometry, t, k, wid)
    return {"type": "FeatureCollection", "features": features}


def map_landmarks(index, coords, features, legs, stops):
    """Named references from OSM, distinct from itinerary stops; no inferred names."""
    route_points = [e["geometry"][0] for l in legs for e in l["edges"]]
    sampled = route_points[::5] + [s["coordinate"] for s in stops]
    stop_names = {s["name"] for s in stops}
    found = []
    def add(name, coordinate, kind, priority, zoom, typ, fid, span=0):
        if not name or name in stop_names or not coordinate or not sampled:
            return
        near = min(distance(coordinate, p) for p in sampled)
        if near > (3000 if kind == "water" else 1200):
            return
        found.append({"name": name, "coordinate": coordinate, "kind": kind,
                      "priority": priority, "min_zoom": zoom, "near_route_metres": round(near),
                      "osm_type": typ, "osm_id": fid, "label_span_metres": round(span),
                      "source_url": f"https://www.openstreetmap.org/{typ}/{fid}"})
    for f in features["features"]:
        p, g = f["properties"], f["geometry"]
        name = p["name"]
        if not name:
            continue
        if p["kind"] == "road":
            pts = g["coordinates"]
            # Label at a real road point near this itinerary, not an off-map centroid.
            anchor = pts[len(pts)//2]
            if min(distance(anchor, b) for b in sampled) > 1200:
                anchor = min(pts, key=lambda a: min(distance(a, b) for b in sampled))
            t = index.get(("way", p["osm_id"]), {}).get("tags", {})
            bridge = t.get("bridge") not in {None, "no"}
            causeway = name.endswith("堤")
            add(name, anchor, "bridge" if bridge else "road", 85 if bridge or causeway else 45,
                13 if causeway else 15, "way", p["osm_id"], sum(distance(a,b) for a,b in zip(pts,pts[1:])))
        else:
            rings = g["coordinates"] if g["type"] == "Polygon" else max(g["coordinates"], key=lambda poly: len(poly[0]))
            ring = rings[0]
            anchor = [(min(p[0] for p in ring)+max(p[0] for p in ring))/2,
                      (min(p[1] for p in ring)+max(p[1] for p in ring))/2]
            if not contains(ring, anchor) or any(contains(h, anchor) for h in rings[1:]):
                anchor = ring[len(ring)//2]
            area = abs(sum((a[0]*b[1]-b[0]*a[1]) for a, b in zip(ring, ring[1:]))) / 2
            major_water = p["kind"] == "water" and area > .00002
            add(name, anchor, p["kind"], 100 if major_water else (50 if p["kind"] == "water" else 75),
                12 if major_water else (16 if p["kind"] == "water" else 14), p["osm_type"], p["osm_id"])
    for (typ, fid), e in index.items():
        t = e.get("tags", {})
        name = t.get("name:zh", t.get("name", ""))
        if not name or not (t.get("tourism") in {"attraction", "viewpoint"} or t.get("historic") or
                            t.get("entrance") or t.get("man_made") == "pier" or t.get("amenity") == "place_of_worship"):
            continue
        pts = [coords[n] for n in e.get("nodes", []) if n in coords]
        anchor = coords.get(fid) if typ == "node" else (pts[len(pts)//2] if pts else None)
        add(name, anchor, "landmark", 70, 14, typ, fid)
    unique = {}
    for p in sorted(found, key=lambda p: (-p["priority"], -p["label_span_metres"], p["near_route_metres"], p["name"])):
        unique.setdefault(p["name"], p)
    return list(unique.values())[:160]


def bearing(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, [*a, *b])
    return math.degrees(math.atan2(math.sin(lon2-lon1)*math.cos(lat2),
                       math.cos(lat1)*math.sin(lat2)-math.sin(lat1)*math.cos(lat2)*math.cos(lon2-lon1))) % 360


def turn_action(previous, current):
    delta = (bearing(*current["geometry"][:2])-bearing(*previous["geometry"][-2:])+180) % 360-180
    if abs(delta) >= 150:
        return "折返"
    if abs(delta) < 35:
        return "继续前行"
    return ("向右" if delta > 0 else "向左") + ("转" if abs(delta) >= 65 else "稍转")


def directions(edges, adj, landmarks, origin, destination):
    """Group actual edges; only issue turns at junctions or road changes, not bends."""
    groups = []
    def key(e):
        return (e["mode"], e.get("name", "步道"), e.get("bridge", False), e.get("highway") == "steps")
    for i, e in enumerate(edges):
        prev = edges[i-1] if i else None
        neighbours = {n for n, _ in adj.get(e.get("from_node"), [])}
        junction = prev and len(neighbours) >= 3 and turn_action(prev, e) != "继续前行"
        # Short segmented OSM ways with the same name remain one walking instruction.
        if not groups or key(e) != key(prev) or junction:
            groups.append({"start_edge": i, "end_edge": i+1, "mode": e["mode"], "name": e.get("name", "步道"),
                           "action": "出发" if not prev else (turn_action(prev, e) if e["mode"] == prev["mode"] == "walk" else "换乘"),
                           "geometry": list(e["geometry"]), "metres": e["metres"], "minutes": e["minutes"],
                           "bridge": e.get("bridge", False), "steps": e.get("highway") == "steps"})
        else:
            g = groups[-1]
            g["end_edge"] = i+1
            g["geometry"].extend(e["geometry"][1:])
            g["metres"] += e["metres"]
            g["minutes"] += e["minutes"]
    compass = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
    for i, g in enumerate(groups):
        g["metres"], g["minutes"] = round(g["metres"], 1), round(g["minutes"], 2)
        pts = g["geometry"]
        # Heading describes departure only; it never calls a curved segment a straight road.
        target = next((p for p in pts[1:] if distance(pts[0], p) >= 15), pts[-1])
        heading = compass[round(bearing(pts[0], target)/45) % 8]
        length = f"约{round(g['metres']/10)*10:.0f}米" if g["metres"] >= 20 else f"约{max(1, round(g['metres']))}米"
        duration = "不足1分钟" if g["minutes"] < 1 else f"约{math.ceil(g['minutes'])}分钟"
        near = [p for p in landmarks if p["kind"] not in {"water", "green", "road"} and p["name"] != g["name"] and
                min(distance(p["coordinate"], a) for a in pts) <= 35]
        refs = list(dict.fromkeys(p["name"] for p in near))[:2]
        g["landmarks"] = refs
        road = g["name"] if g["name"] != "步道" else "图中标出的步道（地图未标道路名）"
        if g["mode"] == "walk":
            pivot = next((p["name"] for p in landmarks if p["kind"] in {"landmark", "bridge"} and distance(p["coordinate"], pts[0]) <= 40), None)
            junction = f"在{pivot}附近的图示衔接点" if pivot else "在图示衔接点"
            lead = f"从{origin}的路线起点出发，起步朝{heading}，" if i == 0 else f"{junction}{g['action']}，起步朝{heading}，"
            g["instruction"] = lead + f"沿{road}走{length}（{duration}）"
            if g["bridge"]:
                g["instruction"] += "，通过桥梁"
            if g["steps"]:
                g["instruction"] += "，此段有台阶"
            if refs:
                g["instruction"] += "；途中可参考附近地标：" + "、".join(refs)
        else:
            g["instruction"] = f"在图示接驳点乘坐{MODES[g['mode']]}：{g['name']}，约{math.ceil(g['minutes'])}分钟（含已计入的等候）"
        if i == len(groups)-1:
            g["instruction"] += f"；到达{destination}的游览访问点"
        g["instruction"] += "。"
    return groups


# ---- 语音讲解（audio guide）----

DEFAULT_VOICES = {"host": "zh-CN-XiaoxiaoNeural", "expert": "zh-CN-YunxiNeural"}
SPEAKERS = {"host", "expert"}
GAP_SECONDS = 0.45          # 对话轮次间静默
EDGE_SECONDS = 0.3          # 片头/片尾静默
CHARS_PER_SECOND = 3.8      # 与 est_seconds 对账用
LINE_TIMEOUT = 90           # 单行合成超时（秒）


def find_edge_tts():
    exe = shutil.which("edge-tts")
    if exe:
        return exe
    fallback = Path.home()/".hermes/hermes-agent/venv/bin/edge-tts"
    if fallback.exists():
        return str(fallback)
    return None


def silence_wav(path, seconds, rate=24000):
    frames = b"\x00\x00" * int(rate*seconds)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)


def validate_script(sid, script):
    if not isinstance(script, dict) or not script.get("lines"):
        raise Insufficient(f"{sid}：访谈稿缺少对话内容")
    for i, line in enumerate(script["lines"]):
        if line.get("speaker") not in SPEAKERS:
            raise Insufficient(f"{sid}：第{i+1}行 speaker 必须是 host 或 expert")
        if not str(line.get("text", "")).strip():
            raise Insufficient(f"{sid}：第{i+1}行内容为空")
    if not script.get("general_knowledge_used") and not script.get("sources"):
        raise Insufficient(f"{sid}：既无来源又标注通识，稿件无效")
    if script.get("poi_id") and script["poi_id"] != sid:
        raise Insufficient(f"{sid}：稿件 poi_id 与文件名不一致")
    est = sum(len(l["text"]) for l in script["lines"])/CHARS_PER_SECOND \
        + (len(script["lines"])-1)*GAP_SECONDS + 2*EDGE_SECONDS
    return est


def synth_line(exe, voice, text, dest):
    """合成单行；失败重试一次；返回 None 表示成功，否则返回错误描述。"""
    last = "未知错误"
    for attempt in (1, 2):
        try:
            r = subprocess.run([exe, "--voice", voice, "--text", text, "--write-media", str(dest)],
                               capture_output=True, text=True, timeout=LINE_TIMEOUT)
            if r.returncode == 0 and dest.exists() and dest.stat().st_size > 100:
                return None
            last = f"exit={r.returncode} {(r.stderr or r.stdout or '')[-200:]}"
        except subprocess.TimeoutExpired:
            last = f"超时（>{LINE_TIMEOUT}s）"
        except OSError as exc:
            last = str(exc)
        time.sleep(1.5)
    return last


def concat_item(workdir, lines, voices, exe, dest):
    """逐行合成→stdlib 拼接（含静默）→单次 ffmpeg loudnorm 编码。返回 (秒, 字节)。"""
    rate = 24000
    parts = []  # wav 文件路径序列（含静默）
    silence = workdir/"gap.wav"
    silence_wav(silence, GAP_SECONDS, rate)
    edge = workdir/"edge.wav"
    silence_wav(edge, EDGE_SECONDS, rate)
    parts.append(edge)
    for i, line in enumerate(lines):
        if i:
            parts.append(silence)
        raw = workdir/f"line{i:03d}.mp3"
        err = synth_line(exe, voices[line["speaker"]], line["text"], raw)
        if err:
            raise Insufficient(f"edge-tts 合成失败（第{i+1}行，声线 {voices[line['speaker']]}）：{err}")
        wav = workdir/f"line{i:03d}.wav"
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-ar", str(rate),
                            "-ac", "1", str(wav)], capture_output=True, text=True, timeout=120)
        if r.returncode != 0 or not wav.exists():
            raise Insufficient(f"ffmpeg 解码失败（第{i+1}行）：{(r.stderr or '')[-200:]}")
        raw.unlink(missing_ok=True)
        parts.append(wav)
    parts.append(edge)
    joined = workdir/"joined.wav"
    with wave.open(str(joined), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        for p in parts:
            with wave.open(str(p), "rb") as w:
                if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (rate, 1, 2):
                    raise Insufficient(f"音频参数不一致：{p.name}")
                out.writeframes(w.readframes(w.getnframes()))
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(joined),
                        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                        "-ar", str(rate), "-ac", "1", "-codec:a", "libmp3lame", "-b:a", "64k",
                        str(dest)], capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not dest.exists() or dest.stat().st_size < 1000:
        raise Insufficient(f"ffmpeg 编码失败：{(r.stderr or '')[-200:]}")
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nk=1:nw=1", str(dest)], capture_output=True, text=True, timeout=60)
    try:
        seconds = float(probe.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise Insufficient("ffprobe 无法读取合成时长")
    return seconds, dest.stat().st_size


def audio_build(args):
    route = read(args.route)
    stops = route.get("stops", [])
    if not stops:
        raise Insufficient("路线中没有站点，无法生成语音讲解")
    items = [("_overview", route.get("title", "路线总览"), True)]
    items += [(s["id"], s.get("name", s["id"]), False) for s in stops]
    missing = [sid for sid, _, _ in items if not (args.scripts/f"{sid}.json").exists()]
    if missing:
        raise Insufficient("缺少访谈稿："+", ".join(missing))
    voices = dict(DEFAULT_VOICES)
    if args.spec and args.spec.exists():
        cfg = read(args.spec).get("audio_guide", {})
        voices.update(cfg.get("voices") or {})
    exe = find_edge_tts()
    if not exe:
        raise Insufficient("edge-tts 不可用：未找到可执行文件。请安装后重试，或与用户确认放弃语音讲解")
    args.out.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out/"audio-manifest.json"
    manifest = read(manifest_path) if manifest_path.exists() else {"items": {}}
    synthesized, reused, total_seconds, total_bytes = [], [], 0.0, 0
    for sid, title, is_overview in items:
        raw = (args.scripts/f"{sid}.json").read_bytes()
        script = json.loads(raw)
        est = validate_script(sid, script)
        sha = hashlib.sha256(raw).hexdigest()
        old = manifest["items"].get(sid)
        if old and old.get("sha256") == sha and (args.out/old["file"]).exists():
            reused.append(sid)
            total_seconds += old["seconds"]
            total_bytes += old["bytes"]
            continue
        with tempfile.TemporaryDirectory(prefix=f"cm-{sid}-") as td:
            seconds, size = concat_item(Path(td), script["lines"], voices, exe, args.out/f"{sid}.mp3")
        manifest["items"][sid] = {
            "title": script.get("title", title), "file": f"{sid}.mp3", "sha256": sha,
            "seconds": round(seconds, 2), "bytes": size, "est_seconds": round(est, 2),
            "overview": is_overview, "transcript": script["lines"],
            "sources": script.get("sources", []),
            "general_knowledge_used": bool(script.get("general_knowledge_used")),
        }
        synthesized.append(sid)
        total_seconds += seconds
        total_bytes += size
        if abs(seconds-est)/max(est, 1) > 0.30:
            print(json.dumps({"warning": f"{sid} 实测 {seconds:.0f}s 与预估 {est:.0f}s 偏差超过30%，请核对稿件"} , ensure_ascii=False))
        time.sleep(0.3)
    manifest["generated_at"] = now()
    manifest["voices"] = voices
    write(manifest_path, manifest)
    (args.out/".failure.json").unlink(missing_ok=True)
    print(json.dumps({"status": "passed", "items": len(items), "synthesized": synthesized,
                      "reused": reused, "total_minutes": round(total_seconds/60, 1),
                      "total_mb": round(total_bytes/1e6, 1)},
                     ensure_ascii=False))


def safe_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def attach_audio(route, manifest_path, html_dir):
    """校验 manifest 覆盖率，复制音频到 HTML 同目录，返回注入 route 的 audio_guide 结构。"""
    manifest = read(manifest_path)
    items = manifest.get("items", {})
    need = ["_overview"]+[s["id"] for s in route.get("stops", [])]
    missing = [i for i in need if i not in items]
    if missing:
        raise Insufficient("音频清单缺少条目："+", ".join(missing))
    dst = html_dir/"audio"
    dst.mkdir(parents=True, exist_ok=True)
    guide = {}
    for sid in need:
        item = dict(items[sid])
        src_file = manifest_path.parent/item["file"]
        if not src_file.exists():
            raise Insufficient(f"音频文件缺失：{item['file']}")
        target = dst/item["file"]
        if not target.exists() or target.stat().st_size != src_file.stat().st_size:
            shutil.copyfile(src_file, target)
        item["file"] = f"audio/{item['file']}"
        guide[sid] = item
    overview = guide.pop("_overview")
    return {"overview": overview, "stops": guide}


def build(args):
    route = read(args.route)
    if not route.get("legs") or not route.get("features", {}).get("features"):
        raise Insufficient("路线或底图为空")
    audio_bytes = 0
    audio_count = 0
    if getattr(args, "audio_manifest", None):
        route["audio_guide"] = attach_audio(route, args.audio_manifest, args.out.parent)
        audio_count = 1+len(route["stops"])
        audio_bytes = sum(v["bytes"] for v in route["audio_guide"]["stops"].values())+route["audio_guide"]["overview"]["bytes"]
    template = (ROOT/"assets/map.html").read_text(encoding="utf-8")
    css = (ROOT/"assets/leaflet.css").read_text(encoding="utf-8")
    # divIcon markers and CSS zoom controls need no bitmap icons or layer-control sprites.
    css = re.sub(r"url\([^)]*\)", "none", css)
    js = (ROOT/"assets/leaflet.js").read_text(encoding="utf-8")
    js = re.sub(r"//# sourceMappingURL=.*", "", js)
    license_text = (ROOT/"assets/leaflet-LICENSE.txt").read_text(encoding="utf-8").replace("--", "—")
    out = template.replace("__TITLE__", html.escape(route["title"]))
    out = out.replace("/*__LEAFLET_CSS__*/", css).replace("/*__LEAFLET_JS__*/", js)
    out = out.replace("__ROUTE_DATA__", safe_json(route)).replace("__LEAFLET_LICENSE__", license_text)
    if re.search(r'<(?:script|link|img)[^>]+(?:src|href)=[\"\']https?://', out):
        raise Insufficient("网页存在运行时外部资源依赖")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(out, encoding="utf-8")
    print(json.dumps({"file": str(args.out), "bytes": args.out.stat().st_size, "self_contained": True,
                      "audio": {"items": audio_count, "mb": round(audio_bytes/1e6, 1)} if audio_count else None},
                     ensure_ascii=False))


def check(args):
    r = read(args.route)
    if len(r["stops"]) != len(r["legs"])+1:
        raise Insufficient("景点与路段数量不一致")
    for i, leg in enumerate(r["legs"]):
        if leg["from"] != r["stops"][i]["id"] or leg["to"] != r["stops"][i+1]["id"]:
            raise Insufficient("路线顺序不一致")
        last = r["stops"][i]["coordinate"]
        for edge in leg["edges"]:
            if distance(last, edge["geometry"][0]) > 1:
                raise Insufficient("路线几何断连")
            if edge["mode"] == "walk" and not edge.get("way_id"):
                raise Insufficient("步行路段缺少开放道路来源")
            last = edge["geometry"][-1]
        if distance(last, r["stops"][i+1]["coordinate"]) > 1:
            raise Insufficient("路线未到达访问点")
        cursor = 0
        for step in leg.get("directions", []):
            if step["start_edge"] != cursor or not cursor < step["end_edge"] <= len(leg["edges"]):
                raise Insufficient("行走步骤遗漏或重复路段")
            subset = leg["edges"][cursor:step["end_edge"]]
            expected = subset[0]["geometry"][:]
            for e in subset[1:]:
                expected.extend(e["geometry"][1:])
            if step["geometry"] != expected or not step.get("instruction"):
                raise Insufficient("行走说明与路线几何不一致")
            if abs(step["metres"]-sum(e["metres"] for e in subset)) > .2 or abs(step["minutes"]-sum(e["minutes"] for e in subset)) > .02:
                raise Insufficient("行走步骤距离或时间不一致")
            cursor = step["end_edge"]
        if cursor != len(leg["edges"]):
            raise Insufficient("缺少完整逐段行走说明")
    total = sum(l["minutes"] for l in r["legs"])+sum(s.get("dwell_minutes", 0) for s in r["stops"])+r["rest_minutes"]
    if abs(total-r["total_minutes"]) > 0.2 or total > r["budget_minutes"]:
        raise Insufficient("时间预算或汇总不一致")
    if args.html and "__ROUTE_DATA__" in args.html.read_text(encoding="utf-8"):
        raise Insufficient("网页模板未填充")
    warnings = []
    audio_summary = None
    if getattr(args, "audio_manifest", None):
        manifest = read(args.audio_manifest)
        items = manifest.get("items", {})
        need = ["_overview"]+[s["id"] for s in r["stops"]]
        missing = [i for i in need if i not in items]
        if missing:
            raise Insufficient("音频覆盖缺失（要求全部站点+总览）："+", ".join(missing))
        total_bytes = 0
        html_text = args.html.read_text(encoding="utf-8") if args.html else ""
        for sid in need:
            it = items[sid]
            if not it.get("transcript"):
                raise Insufficient(f"音频 {sid} 缺少文字稿")
            if not it.get("sources") and not it.get("general_knowledge_used"):
                raise Insufficient(f"音频 {sid} 既无来源也未标注通识")
            if it.get("est_seconds") and abs(it["seconds"]-it["est_seconds"])/max(it["est_seconds"], 1) > 0.30:
                warnings.append(f"{sid} 音频时长 {it['seconds']:.0f}s 与稿件预估 {it['est_seconds']:.0f}s 偏差超过30%")
            if args.html:
                f = args.html.parent/"audio"/it["file"]
                if not f.exists():
                    raise Insufficient(f"音频文件不存在：audio/{it['file']}")
                if f.stat().st_size > 10_000_000:
                    raise Insufficient(f"音频文件超过10MB：{it['file']}")
                if f"audio/{it['file']}" not in html_text:
                    raise Insufficient(f"网页未引用音频：{it['file']}")
                total_bytes += f.stat().st_size
        if args.html and total_bytes > 80_000_000:
            raise Insufficient("音频总体积超过80MB")
        audio_summary = {"items": len(need), "mb": round(total_bytes/1e6, 1)}
    print(json.dumps({"status": "passed", "scope": "静态数据与路线检查；不代表现场通行、手机定位或国内网络验收", "test_only": r["test_only"],
                      "audio": audio_summary, **({"warnings": warnings} if warnings else {})}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("fetch")
    p.add_argument("--bbox", type=float, nargs=4, required=True)
    p.add_argument("--endpoint", default="https://overpass-api.de/api/interpreter")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--refresh", action="store_true")
    p = sub.add_parser("inspect")
    p.add_argument("--osm", type=Path, required=True)
    p.add_argument("--name", nargs="*")
    p.add_argument("--out", type=Path)
    for command in ["candidates", "plan"]:
        p = sub.add_parser(command)
        p.add_argument("--osm", type=Path, required=True)
        p.add_argument("--spec", type=Path, required=True)
        p.add_argument("--out", type=Path, required=True)
        p.add_argument("--test", action="store_true", help="仅用于算法/界面测试，输出必须带测试标记")
        if command == "plan":
            p.add_argument("--choice", required=True)
    p = sub.add_parser("build")
    p.add_argument("--route", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--audio-manifest", type=Path)
    p = sub.add_parser("check")
    p.add_argument("--route", type=Path, required=True)
    p.add_argument("--html", type=Path)
    p.add_argument("--audio-manifest", type=Path)
    p = sub.add_parser("audio-build")
    p.add_argument("--route", type=Path, required=True)
    p.add_argument("--scripts", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--spec", type=Path)
    args = parser.parse_args()
    try:
        globals()[args.command.replace("-", "_")](args)
    except (Insufficient, KeyError, ValueError, urllib.error.URLError, OSError) as exc:
        # A failed plan must never leave a stale result that could be mistaken for this run.
        if args.command == "audio-build":
            # 输出是目录：不删除（保留缓存已合成条目），只写失败标记供重跑参考。
            if getattr(args, "out", None):
                try:
                    args.out.mkdir(parents=True, exist_ok=True)
                    write(args.out/".failure.json", {"status": "failed", "reason": str(exc), "created_at": now()})
                except OSError:
                    pass
        elif hasattr(args, "out") and args.command in {"plan", "candidates", "build"}:
            args.out.unlink(missing_ok=True)
            write(args.out.with_suffix(".failure.json"), {"status": "failed", "reason": str(exc), "created_at": now()})
        print(json.dumps({"status": "failed", "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
