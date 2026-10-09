# plan-scenic-route · 景区游览地图生成

**一个 Agent Skill：对话式输入「景区名 + 时长」，产出基于真实路网、自包含、手机友好的游览地图网页，并可一键发布到 GitHub Pages。**

英文一句话：*An agent skill that turns "scenic spot + hours" into a self-contained, mobile-first walking-tour map built on verified OpenStreetMap data.*

---

## 效果演示（本技能的真实产出）

| 地图 | 说明 |
|---|---|
| [天坛 · A 线](https://insiderf.github.io/scenic-maps/maps/tiantan-a/) | 13 站点，全程带双人访谈语音讲解（约 17.7 分钟音频） |
| [天坛 · C 线](https://insiderf.github.io/scenic-maps/maps/tiantan-c/) | 9 站点精华线，带语音讲解 |
| [故宫](https://insiderf.github.io/scenic-maps/maps/gugong/) | 中轴线经典路线 |

全部为单个自包含 HTML，国内手机浏览器直连打开，无境外 CDN 依赖。

---

## 特性

- **开放数据核证**：地图与路网几何来自 OpenStreetMap（Overpass API），开放时段、出入口、票务等事实对照景区官网/政府公开资料核实，每条来源记录 URL + 核实日期。不复制商业底图或无许可的官方导览图。
- **真实路网规划**：沿道路原始节点建图，Dijkstra 计算连接；景点访问点落在核实过的入口附近——不把景点中心直接吸附到道路，不用跨水直线补路。
- **起终点候选制**：先给 2-3 个时间可行的方案（表格对比景点覆盖/距离/时长），**用户选定后才生成**，绝不擅自替选。
- **自包含交付**：Leaflet 1.9.4 随包打包，矢量地图、路线、行程卡、JS/CSS 全部内嵌单文件 HTML。地图几何不用 AI 图像生成。
- **手机优先界面**：320px 宽度可用、单行标题栏、可展开行程卡、分段「怎么走」点击高亮真实路段、方向箭头高对比配色、HTTPS 主动授权定位（不上传位置历史）。
- **可选语音讲解**：每个站点生成中文双人访谈（edge-tts：Xiaoxiao 主持 × Yunxi 讲解）→ ffmpeg 拼接 + loudnorm → MP3；稿件经资料研究和用户确认两道关，sha256 缓存避免重合成。
- **诚实交付原则**：必要数据拿不到就停止并说明缺失，不编造连接；开发预览显式带测试标记；没发布就明说未发布，不虚构链接。
- **中国景区实战坑位清单**：Overpass 探活重试、票务核心区路网孤岛诊断与访问点迁移、由边界穿越推导大门、无 OSM 对象的 POI 处理等（详见 `references/cn-scenic-pitfalls.md`）。

---

## 工作流程

```
收集需求（景区/时长/日期/偏好）
   ↓
fetch 取 OSM 数据 → inspect 核实景点访问点 → 官网核实开放信息
   ↓
生成 2-3 个起终点候选（candidates）→ 用户选择
   ↓
plan 沿真实路网规划 → build 生成自包含 HTML
   ↓
check 静态检查 + 浏览器空间审核（多尺寸/避让/路线连续）
   ↓
发布 GitHub Pages / Cloudflare Pages → curl 验证 200 → 交付链接
```

---

## 安装

**Hermes Agent：**

```bash
hermes skills install https://raw.githubusercontent.com/InsiderF/plan-scenic-route/main/SKILL.md
```

**手动（或其他 Agent Skills 兼容宿主）：**

```bash
git clone https://github.com/InsiderF/plan-scenic-route ~/.hermes/skills/travel/plan-scenic-route
```

标准 SKILL.md 目录结构，Cursor / Cline 等支持 Agent Skills 的宿主直接把整个目录放进对应 skills 目录即可。

**依赖**：Python 3.10+（仅标准库，无第三方 Python 依赖）。语音讲解功能另需 `edge-tts` + `ffmpeg`。

---

## 使用

装好后直接对话触发：

```
> 帮我规划西湖，6小时，全程步行，主要看沿岸景点
> 天坛半天，带爸妈，别太累，要有语音讲解
```

技能会先给出起终点候选方案让你选，选完才生成最终地图。

---

## scenic.py 命令参考

```bash
# 取 OSM 数据（bbox：南 西 北 东，最大 150 km²，默认复用缓存）
python3 scripts/scenic.py fetch --bbox 30.205 120.10 30.28 120.18 --out work/osm.json

# 检查景点附近的道路节点候选
python3 scripts/scenic.py inspect --osm work/osm.json --name 断桥 苏堤 --out work/poi.json

# 检查起终点候选的预算与连接
python3 scripts/scenic.py candidates --osm work/osm.json --spec work/spec.json --out work/candidates.json

# 规划路线（--choice 为用户选定的候选 ID；--test 仅限开发预览）
python3 scripts/scenic.py plan --osm work/osm.json --spec work/spec.json --choice A --out work/route.json

# 生成自包含 HTML（--audio-manifest 可选，语音讲解）
python3 scripts/scenic.py build --route work/route.json --out public/index.html

# 静态检查（覆盖率/几何/编号/音频引用一致性）
python3 scripts/scenic.py check --route work/route.json --html public/index.html

# 合成语音讲解（逐行 edge-tts → ffmpeg concat + loudnorm → MP3 64kbps）
python3 scripts/scenic.py audio-build --route work/route.json --scripts work/audio-scripts --out work/audio
```

`plan` 失败会删除本次目标旧文件并写 `.failure.json`——绝不把旧结果冒充新成品。

---

## 目录结构

```
plan-scenic-route/
├── SKILL.md                        # 技能入口：适用范围、流程、验收标准
├── references/
│   ├── workflow.md                 # 取数、行程 spec 格式、路线算法、手机核查
│   ├── cn-scenic-pitfalls.md       # 中国景区实战坑位清单
│   ├── audio-guide.md              # 语音讲解规范（研究→访谈稿→合成三段式）
│   └── hosting.md                  # GitHub Pages / Cloudflare Pages 发布流程
├── scripts/
│   ├── scenic.py                   # 核心 CLI：fetch/inspect/candidates/plan/build/check/audio-build
│   ├── publish.py                  # Cloudflare Pages 发布（需已装 Wrangler）
│   └── selftest.py                 # 自测
└── assets/
    ├── map.html                    # 地图页面模板
    ├── leaflet.js / leaflet.css    # Leaflet 1.9.4 随包（BSD-2-Clause）
    └── icon.svg
```

---

## 设计原则

1. **地图几何必须来自开放数据**，不用 AI 图像生成；只使用许可明确的数据（ODbL / CC0 / CC BY-SA），无开放许可的来源（如百度百科）禁用。
2. **估算透明**：步行速度、台阶倍率、停留时间都是 spec 里的显式参数；不通过调高速度让超时路线"通过"，不宣称全局最优。
3. **静态检查不能代替空间审核**：桥梁/水面关系、标签避让、路线连续性必须真浏览器过一遍。
4. **不装懂**：路网连通不等于当日开放；"附近"只代表坐标靠近；道路无名就说"地图未标道路名"。

## 已知边界

- 不做逐转弯语音导航和手机实时重算（第一版定位是"可信赖的游览地图"）。
- 语音合成依赖 edge-tts 的微软非公开端点，失效时会触发停止协议询问用户，不会静默换引擎。
- Overpass 免费公共实例有限速，取数控制范围并复用缓存。

---

## 许可

MIT。随包分发的 Leaflet 遵循其原许可（BSD-2-Clause，见 `assets/leaflet-LICENSE.txt`）。

地图数据与成品页面须遵守 [OpenStreetMap 许可](https://www.openstreetmap.org/copyright)（ODbL）署名要求——发布的网页已内置署名。
