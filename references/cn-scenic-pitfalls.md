# 中文景区数据核证坑位清单

来自 2026-10 天坛（票务核心区孤岛）会话的实战教训，适用于所有"检票院落型"中式景区（天坛/故宫/颐和园类）。

## 取数：Overpass 超时的正确重试姿势

主接口偶发 504 多为瞬时过载。正确顺序：
1. 先探活：`curl -m 20 https://overpass-api.de/api/status`（返回 406 HTML 也算可达，说明能连上）。
2. 可达则直接重试原接口，通常一次就过。
3. 切镜像前必须先用同样方式探活——kumi.systems 在部分网络完全不可达，把唯一一次切换浪费在死镜像上就没了退路。

## 票务核心区 = 路网孤岛（最重要）

检票核心院落在 OSM 中常测绘为：庭院 `highway=pedestrian + area=yes` 多边形 + 门洞是无 highway 标签的 building/entrance way。scenic.py 排除无 highway、条件通行（access:conditional）要素；`area=yes` 仅在 highway 属于可步行线型（pedestrian/footway/path/steps/track）时保留其**闭合边界环**参与路由（2026-10 修复），其余 area 一律剔除。

## area=yes 长桥/广场链 = 静默绕路（天坛丹陛桥案例，2026-10）

修复 area 排除前，中轴全线（丹陛桥 29182843 → 成贞门广场 43921124 → 圜丘前广场 43922698）是一串 area=yes 面，路由图里整条消失。**此 bug 不会让 candidates/plan 报错**——Dijkstra 在残缺图上照样给出"真·最短"，祈年殿→回音壁给出 1890m 东侧环线绕路（直线仅 467m），地图忠实渲染错误路线。症状签名：**某 leg 距离远超两端点直线距离（>2.5x）且途经全是无名步道**。诊断：对可疑 leg 算 `distance(access_a, access_b)` 对比 `walk_metres`；用 `inspect`/直接查 OSM way tags 看两端连线是否被 area=yes 吞掉。修复后同类线路自动恢复（1890m→505m，走丹陛桥 ring 边界）。注意 ring 边界只是贴边走：门洞没建线时庭院仍不可穿越，access_node 迁移规则不变。

诊断（不要靠猜，直接看分量）：

```python
import importlib.util, sys
sp = importlib.util.spec_from_file_location('scenic', '<SKILL_DIR>/scripts/scenic.py')
S = importlib.util.module_from_spec(sp); sys.modules['scenic']=S; sp.loader.exec_module(S)
spec=S.read('work/spec.json'); raw=S.read('work/osm.json')
index=S.get_elements(raw); coords,adj,_,_,_ = S.validate_spec(spec, index, True)
def comp_of(s):
    seen={s}; st=[s]
    while st:
        c=st.pop()
        for nb,_ in adj.get(c,()):
            if nb not in seen: seen.add(nb); st.append(nb)
    return seen
# 对全部 access_node 求分量，按 min(comp) 分组 → 孤岛一目了然
```

修复原则——**不许编造连线，也不必放弃行程**：
- 把孤岛 POI 的 access_node 迁到检票门外最近的主网节点（实测距门 10–40m），要求：`nid in main_comp` 且该节点至少属于一条非 area 的普通道路（footway/steps/pedestrian）。
- 门位坐标**不许用记忆**（本会话记忆坐标偏了 200m+，数据核查才救回来）。推算法：找 walkable way 与公园边界多边形的跨边界边（pip 判断两端内外不一致），<100m 聚类即门口；选园内侧 degree≥2 且连通主分量的节点。
- 院内参观方向写进 POI description 与 data_gate.notes，明确声明"地图线画到检票门外，院内连线为避免虚构数据不绘制"。access_source 的 note 里写清迁移依据。

## 无 OSM 对象的 POI 是合法的

verify_poi 只在 osm_type/osm_id **存在时**才校验 OSM 对象。大门、无名设施可以不填 OSM 对象，只需 name + verified access_node + 双来源：access_source 引门口处的道路 way（note 写明"由园内路径穿越公园边界多边形的开口核验"），availability_source 引官方页面。dwell 0，required/optional 均为 False（required 会强制出现在所有候选里，optional 会被 plan() 自动插进路线中途，两者都不合适）。

## 同理并入，不单列

回音壁（皇穹宇围墙）、九龙柏这类没有独立 OSM 命名对象的知名景点，并入邻近 POI 的 description 说明。地标只来自开放数据中确有名称及位置的对象。

## 执行环境：heredoc 审批坑

批量写含中文+排版字符（× · — "" 等）的 spec 时，优先用 write_file/patch 工具改文件，再用单独一条短命令跑脚本。终端 heredoc 大段中文会触发安全扫描（Confusable Unicode，HIGH）要求人工审批，审批超时命令直接作废且不许原样重试。

## 联票口径的路线编辑模式（孤岛确诊后的"怎么画"）

孤岛问题解决后，还有一组编辑决策决定成图质量（天坛 A/C 双图实测）：

- **同院多景点合并为一个停留点**：祈年殿+皇乾殿合一（dwell 60min 覆盖全院），避免两站之间画出 900m 绕行假腿。
- **线状体验路段降级为地图地标**：丹陛桥这类"途中穿过"的 OSM 命名 way 会被脚本自动标注，不当独立站点； Transit 机制只支持 shuttle/cablecar/boat，把院内步行伪装成接驳会渲染成"乘坐游船"，绝对不行。
- **双口径数字，两头诚实**：图上画检票院外的保底环路（无票也照走），预算/表格时间按图示路径算（保守上限）；持票穿行捷径写进 POI description（【联票走法】前缀 + 具体省时省距数字），目录页 meta 另给持票实际值（天坛例：图示 6.3h / 持联票约 5.5h）。用户说"按联票重新计算"时用这套双口径，不要虚改腿时。
- **optional 自动插入只看图上增量成本，不看持票实际体验**：月季园曾被自动插到祈年殿→皇穹宇之间（图上便宜，持票者 1.4km 折返）。不想被自动插入的 POI 一律 `optional:false`——候选 stops 里显式包含的不受影响。
