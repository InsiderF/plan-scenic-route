# 数据格式与执行

Python 3.10+，标准库，无第三方Python依赖；随包Leaflet 1.9.4。根据道路原始节点建立图，使用Dijkstra计算时间最短的连接；以候选骨架和可选点增量插入组织行程，不声称全局最优。

确定本技能绝对路径为 `SKILL_DIR`；输出到用户工作目录，不能写入技能目录。

## 取数

```bash
python3 "$SKILL_DIR/scripts/scenic.py" fetch --bbox 30.205 120.10 30.28 120.18 --out work/osm.json
python3 "$SKILL_DIR/scripts/scenic.py" inspect --osm work/osm.json --name 断桥 平湖秋月 曲院风荷 苏堤 花港观鱼 柳浪闻莺 长桥 --out work/poi-inspection.json
```

bbox为南、西、北、东，最大150平方公里。覆盖必要连接道路。默认复用缓存，必要时用 `--refresh`；失败后最多切换一次合适公开服务，例如 `--endpoint https://overpass.kumi.systems/api/interpreter`，不要无限请求。允许用户提供许可明确的完整OSM JSON。记录快照与取数日期，不能把旧快照当作当日通行证明。

`inspect` 返回附近道路节点候选，不证明入口可通行。对照道路、水体、边界、实际入口核实；不能用中心点到路网的直线补路。确认后填写 `access_node`、`access_verified` 和 `access_source`。来源结构统一为 `{"url":"https://…","checked_at":"实际日期","note":"核实内容"}`。

## 行程格式

创建 `spec.json`，下例只是结构，节点编号必须来自本次OSM。日期和引用中的文字占位须替换。

```json
{
  "title":"西湖 · 湖岸漫游",
  "budget_minutes":360,
  "walk_speed_kmh":3.5,
  "rest_minutes":40,
  "allowed_modes":["walk"],
  "date":"实际出行日期",
  "start_time":"09:00",
  "user_choice_confirmed":false,
  "data_gate":{"status":"sufficient","notes":"记录位置、访问点、道路连接与关键限制的具体检查及证据"},
  "pois":[{
    "id":"duanqiao","name":"断桥残雪",
    "osm_type":"node","osm_id":1468743794,
    "access_node":0,"access_verified":true,
    "access_source":{"url":"https://www.openstreetmap.org/way/实际道路编号","checked_at":"实际日期"},
    "source":{"url":"https://www.openstreetmap.org/node/1468743794","checked_at":"实际日期"},
    "availability":"verified",
    "availability_source":{"url":"https://实际官方网址","checked_at":"实际日期"},
    "description":"自行撰写的简短事实介绍",
    "dwell_minutes":20,"required":false,"optional":false,"priority":3
  }],
  "candidates":[
    {"id":"A","name":"北岸至南岸","reason":"适用理由","stops":["duanqiao","实际终点标识"]},
    {"id":"B","name":"另一种起终点","reason":"适用理由","stops":["其他起点标识","实际终点标识"]}
  ]
}
```

优先按用户偏好确定主要景点骨架，再生成两个或三个可行起终点候选。`optional:true`、`priority` 为预算内插入候选；`required:true` 的必去点必须已在推荐骨架中。距离沿路网计算，停留和休息为透明估算。普通步行默认3.5km/h，台阶时间倍率1.35；高山等环境需另行合理估算。要求无台阶时在spec设置 `avoid_steps:true`，从路网中排除steps，不能只改介绍文字。

存在营业时间的景点使用 `time_sensitive:true`、`opening_intervals:[[540,1020]]`（09:00至17:00，当地分钟数）。跨午夜、预约或复杂班次由代理先确认，不能填为全天。实际行程必须核实日期、时间、景点和涉及道路的已知限制。不要只填 `verified` 或 `sufficient` 字样代替研究与空间检查。

## 候选与成品

```bash
python3 "$SKILL_DIR/scripts/scenic.py" candidates --osm work/osm.json --spec work/spec.json --out work/candidates.json
```

展示候选表，等用户选择后把 `user_choice_confirmed` 改为true，再执行：

```bash
python3 "$SKILL_DIR/scripts/scenic.py" plan --osm work/osm.json --spec work/spec.json --choice A --out work/route.json
python3 "$SKILL_DIR/scripts/scenic.py" build --route work/route.json --out public/index.html
python3 "$SKILL_DIR/scripts/scenic.py" check --route work/route.json --html public/index.html
```

不默认替用户选A。不同景点的访问节点不能重合，远距离吸附需明确入口证据。检查脚本总时间、道路几何和编号一致，再用浏览器检查桥梁、堤岸、是否穿水、标签遮挡和交互；静态检查不能代替空间审核。

`--test` 仅供开发自测或用户明确要求的非实地预览，输出一直带测试标识。没有日期的开发样例采用测试模式；不能用测试模式绕过实际通行核查。失败会删除本次目标旧文件，并写 `.failure.json`，不把旧结果当新成品。

## 景区交通

在 `allowed_modes` 允许shuttle/cablecar/boat，且spec `transit` 填入真实开放几何与运营来源：

```json
{
 "name":"实际交通线路","mode":"boat",
 "from_access_node":0,"to_access_node":1,
 "geometry":[[120.0,30.0],[120.001,30.001]],
 "geometry_license":"ODbL-1.0",
 "geometry_source":{"url":"https://开放几何来源","checked_at":"实际日期"},
 "service_source":{"url":"https://官方运营信息","checked_at":"实际日期"},
 "availability":"verified","travel_minutes":15,"wait_minutes":10,"bidirectional":false
}
```

端点需与步行节点重合；缺少实际连接路段、运营或开放几何时停止。不能把游船站点间直线当路线。等候和行驶时间分别核实或说明估算；代理还需核实班次、日期、购票、时段并记录，脚本不模拟完整时刻表。交通费用与地图免费是不同概念。

## 手机核查

`plan` 输出 `landmarks`（名称、真实坐标、OSM对象与来源、类型、显示优先级、最低缩放级别），以及每段 `legs[].directions`。地标取自本次开放数据，排除同名行程站点；不能要求并非必经的地标加入路线。取数额外包含有名称的出入口、码头和寺庙。装饰性地标缺失可以省略，实际行路必须依赖的路口或连接信息不能省略。

每个步骤含 `start_edge/end_edge`（原始边的半开区间）、`geometry`、道路名称、动作、距离、时间、桥梁/台阶和文字说明。按连续同名道路归组；道路/交通/桥梁/台阶状态改变或可用路口发生明显转向时分段。同名道路的普通弯曲不产生转弯提示；“附近”只代表坐标靠近，不宣称一定能看到或经过景点。脚本的转向与方向是几何估算，必须检查重要岔路、短碎段、入口及衔接点。网页逐段说明与地图高亮应一一对应；不宣称实时导航。必要时按已核实证据优化 `instruction`，保留覆盖同一批原始边的几何、距离和时长。

验收时除行程点外应能看见重要地名；缩放后补充次要名称，隐藏重叠标签，不挡编号和界面。检查每段“怎么走”的展开、步骤点击高亮、全览恢复、手机滚动与无名称道路提示。道路名/参照名称来自地图快照，不能当成现场路牌证据。

界面中文、真实位置、路线与编号对应。网页仅包含选定景区范围，全部地图与前端资源内嵌。检查360/390/412宽屏、横屏和桌面，缩放、卡片、全览、方向、比例尺、许可署名。HTTPS定位须由用户主动允许，显示精度并处理拒绝、超时、范围外和页面关闭；不上传保存位置历史。坐标异常先查来源，不能盲目整体平移。

技术参考：
- https://www.openstreetmap.org/copyright
- https://operations.osmfoundation.org/policies/tiles/
- https://dev.overpass-api.de/overpass-doc/en/preface/commons.html
- https://leafletjs.com/reference.html
- https://developer.mozilla.org/en-US/docs/Web/API/Geolocation_API
