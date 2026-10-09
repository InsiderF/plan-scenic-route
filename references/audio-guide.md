# 语音讲解（Audio Guide）规范

为路线上的每个站点生成中文语音访谈（主持人 × 讲解员），并内嵌到地图页面点击播放。**全流程可选**：spec 无 `audio_guide` 块时，行为与旧版完全一致。

## 配置（spec.json，可选块）

```json
"audio_guide": {
  "enabled": true,
  "voices": {"host": "zh-CN-XiaoxiaoNeural", "expert": "zh-CN-YunxiNeural"}
}
```

- `voices` 可省略（用默认值）。合成引擎固定为 **edge-tts**，不用其他引擎。
- 覆盖范围固定为**路线全部站点 + 1 条总览片头**（`_overview`），不提供子集选项。

## 工作流（agent 执行 A、B，脚本执行 C）

### A. 资料研究 → `work/research/<poi_id>.md`

对 route 每个站点 + 总览（用景区整体词条）收集事实。来源优先级：

1. OSM 要素自带的 `wikipedia`/`wikidata` 标签 → Wikidata API（CC0）
2. `zh.wikipedia.org` REST API 按名称+景区名消歧（CC BY-SA，须归属）
3. 景区官网/政府公开资料（workflow.md 已核实的 availability_source 复用）

每条事实标注级别：`sourced`（带来源 URL + checked_at）或 `general`（公认通识，无单一条目来源）。**百度百科等无开放许可的来源禁止使用。** 维基不可达（如国内直连被墙）时不必停止：全部改用 `general` 通识并在页面标注，但存在 `sourced` 机会时必须先尝试。写出的 research 文件是 B 阶段唯一事实来源。

抓取技巧（2026-10 实测）：REST summary（`/api/rest_v1/page/summary/<题>`）只返回约百字摘要，且祈年殿、圜丘这类小节主题会重定向到景区主词条，单独抓全是 404/重定向。正确姿势是 action API 抓全文：`/w/api.php?action=query&prop=extracts&explaintext=1&redirects=1&format=json&titles=<景区名>`——一篇主词条即覆盖全部殿宇小节（天坛例：4200 字含各殿沿革、尺寸、数字象征），按站点拆分进各自 research 文件。

### B. 访谈稿 → `work/audio-scripts/<poi_id>.json`（总览为 `_overview.json`）

```json
{
  "poi_id": "qiniandian",
  "title": "祈年殿",
  "lines": [
    {"speaker": "host", "text": "……"},
    {"speaker": "expert", "text": "……"}
  ],
  "sources": [{"url": "https://…", "checked_at": "2026-10-06"}],
  "general_knowledge_used": false
}
```

写作要求：
- 双人访谈：主持人提问（2-5 问），讲解员回答。**时长不设上限，以讲透景点主要信息为准**：沿革、建造者、功能用途、建筑特点、掌故典故。
- 开场白报景点名；除总览外，收尾衔接"下一站"（用 route 顺序）。
- **事实纪律：年份、人名、数字只能来自 research 文件。** research 里没有的通识内容可以讲，但对应稿件 `general_knowledge_used` 必须为 true；research 有 sourced 事实的稿件 `sources` 必须非空。
- 总览片头内容：景区概况（世界遗产/等级）、路线结构（起终点、站数、预算）、已核实的票务与开放时段（来自 spec 的 availability 数据）。
- 稿件先全部落盘，经用户确认（或用户明确免审）后才进入 C。

### C. 合成 → `scenic.py audio-build`

```bash
python3 "$SKILL_DIR/scripts/scenic.py" audio-build \
  --route work/route.json --scripts work/audio-scripts \
  --out work/audio [--spec work/spec.json]
```

- 逐行调用 edge-tts（host→XiaoxiaoNeural，expert→YunxiNeural），ffmpeg concat（轮次间 450ms 静默）+ loudnorm（-16 LUFS）+ MP3 64kbps 单声道。
- 缓存：`audio-manifest.json` 记录每条稿件 sha256，稿件未变则跳过重合成。
- **停止协议：edge-tts 不可用/单行重试仍失败 → 非零退出 + `work/audio/.failure.json`（列明景点/行号/错误）→ agent 必须停下来问用户**（检查网络/VPN？跳过该景点？放弃音频？），不得静默降级到其他引擎，不得交付缺音频的成品。

## build / check 接口

```bash
# build：--audio-manifest 指向 audio-manifest.json；mp3 复制到 HTML 同目录 audio/ 下
build --route work/route.json --out public/index.html --audio-manifest work/audio/audio-manifest.json
# check：同样传 --audio-manifest，校验覆盖率/时长偏差/体积/HTML 引用
check --route work/route.json --html public/index.html --audio-manifest work/audio/audio-manifest.json
```

build 注入 `route.audio_guide`（文件路径、时长、文字稿、来源、general 标记），模板判空渲染播放器。发布时 `public/audio/` 整目录随 index.html 上传。

## 多路线变体（同一景区 A/B/C 线）

同一景区多条候选路线都开语音时，**每条路线单独一套 scripts 目录和音频输出目录**（如 `work/audio-scripts-a/`、`work/audio-a/`），因为：

- 总览片头是路线级的，讲的是本线的起终点与结构，各线内容不同；
- 共用站点的**收尾串词按本线站序衔接**（C 线"祈年殿→沿桥南下皇穹宇"，A 线可能是"祈年殿→东去神厨"），稿件 sha256 不同即视为不同条目；
- `audio-build` 的缓存按 manifest 目录隔离，新目录是冷缓存——**热启动方法**：把已有路线的 `audio/`（含 manifest）先复制到新目录，sha 相同的条目自动跳过，只有改动/新增的站点重新合成。

共用站点改写收尾时只动最后 1-2 行（与下一站衔接的问答），正文事实保持一致；开头若含方向性描述（如"沿桥南下"）也要按本线方向校对。

## 页面行为与验收

- 站点卡「▶ 收听讲解 · M:SS」、行程卡顶「▶ 收听路线总览」；同一时间只播一条；文字稿可展开，`general_knowledge_used` 的段落尾注「※ 本段含通识性介绍，未经逐条来源核实」，来源列表固定在文字稿底部。
- check 强制项：route 每个 stop + overview 都有音频；实测时长与稿件 est_seconds 偏差 >30% 警告；单文件 <10MB、每图总音频 <80MB；HTML 引用与 manifest 一致。
- 音频不改变 test_only 语义；最终验收必须真人试听，未试听不得声称"已验收音质"。
- **无头浏览器验收的假警报（2026-10 实测）**：程序化 `.click()` 不是可信手势，自动播放策略会拒绝 `play()`，按钮显示「▶ 重试」并弹 toast——这是浏览器策略限制而非产品缺陷。正确判据：互斥切换正常（点另一条时旧按钮复位）、error 监听零 JS 报错、`r.audio_guide` 数据完整。另外 `file://` 页面里 `fetch` 音频会被浏览器安全策略直接拒绝，也不要当成故障。真实验收 = HTTPS 真机点按 + 人耳试听。

## 已知边界

- edge-tts 依赖微软非公开端点，随时可能失效（届时触发停止协议）。
- 音频文件与页面分离托管（同目录发布），页面本体仍无外部运行时依赖；离线场景音频依赖 HTTP 缓存，首版不做 service worker。
- 不做自动连播、逐段定位、背景音乐。
