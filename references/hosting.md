# 平台使用与免费发布

## 安装与调用

Hermes 技能放入 `~/.hermes/skills/` 即生效，本技能位于用户级分类目录：

| 范围 | 目录 | 说明 |
| --- | --- | --- |
| 用户级 | `~/.hermes/skills/travel/plan-scenic-route/` | 即装即用，本机安装位置 |
| 项目级 | `<项目>/.hermes/skills/plan-scenic-route/` | 需 `hermes skills trust` 一次性信任 |
| 项目级（通用） | `<项目>/.agents/skills/plan-scenic-route/` | 同样需要信任 |

优先级：项目级高于用户级。新技能对新会话生效，当前会话可用 `/reset` 立即加载。代理先确认本机Python3.10+和命令名；核心无第三方Python依赖。不擅自修改全局配置。

调用方式：`/plan-scenic-route 西湖，6小时，全程步行，主要看沿岸景点`（斜杠命令），或自然语言描述景区行程需求，由技能发现机制自动加载。

## 文件与发布

`build` 输出单个自包含HTML，内嵌选定区域的地图、路线、卡片、JS/CSS及第三方许可；完整交付此文件。本地安卓的打开方式和定位权限需单独验证，不能承诺完整离线导航。

用户无需买域名或租服务器。本机已配置 GitHub Pages 专用仓库作为默认托管（见下节）；Cloudflare Pages 仅作备选。实施时核实当前条款和国内访问，不预认任何候选国内可靠。用户选择项目并授权发布后继续执行常规发布，不重复询问已给授权。缺少账号认证或目标项目时，先完成可审阅文件，再指导必要的一次性配置。

发布目录只放地图页面；不上传原始数据、spec、密钥、日志或私密配置。

### GitHub Pages（默认托管）

首次使用前做一次性配置：创建一个专用公开仓库（下文以 `<your-username>/scenic-maps` 为例）并开启 Pages。配置完成后，生成新地图并验收通过即可默认发布到该仓库，无需再次询问。

发布流程：

1. 本地克隆 `gh repo clone <your-username>/scenic-maps <工作目录>/scenic-maps`。**不要用 `~/Desktop` 下的路径**——网络盘挂载会挂起超时。
2. 每张地图放入独立子目录 `maps/<景区拼音或英文短名>/index.html`；同一景区更新覆盖同目录，不混入其他景区文件。带语音讲解的地图还需 `maps/<短名>/audio/*.mp3` 子目录（build `--audio-manifest` 会生成在输出目录的 `audio/` 下，随 index.html 一起复制）。
3. 同步更新根 `index.html` 目录页：在 `ul.maps` 列表顶部追加 `<li><a href="maps/<短名>/">景区 · 路线名</a></li>` 条目（含日期、时长等 meta）；同景区更新则改写原条目；有地图后删除 `.empty` 空态提示。
4. 提交身份建议用仓库级 git 配置（`git config user.name` / `user.email` 用 GitHub noreply 邮箱），不修改全局 git 配置。
5. `git add` 仅限本次地图子目录与根目录页；推送后等待 Pages 部署，用 `curl` 验证子目录网址返回200再交付链接。
6. 仓库只存放地图页面与目录页，不上传原始数据、spec、缓存、密钥或日志。

**Pages 两个已踩过的坑（2026-10）**：
- 仓库根必须有 `.nojekyll`：Jekyll 默认排除下划线开头的文件，语音总览 `_overview.mp3` 因此 404；`.nojekyll` 已在仓库根提交，勿删。
- 复制音频目录不要写 `cp -R src/audio/ dst/`（尾斜杠会把 mp3 倒进 dst 根目录），应 `cp -R src/audio dst/<slug>/audio` 并发布后 `curl` 抽查至少一条 mp3 返回200。

不得覆盖其他网站、公开已有私有项目或修改无关内容。相同HTML无需改地图代码。

### Cloudflare Pages（备选）

依官方Direct Upload流程注册账号、选择项目并上传静态目录，或使用已安装Wrangler。本机认证完成后：

```bash
python3 "$SKILL_DIR/scripts/publish.py" --directory public --project 用户选定项目 --authorized --out work/publication.json
```

脚本调用已有 `wrangler pages deploy`，不自动安装CLI、创建账号或输出凭据。默认网址由平台提供，不需要自有域名。不要把密钥贴到对话或生成文件。

## 验收

检查HTTPS网址返回本次地图、无外部运行资源，再用安卓普通浏览器和国内移动网络（关闭代理）验证首次打开、缩放、卡片、定位授权与位置显示。服务器成功、电脑模拟均不能证明国内手机实测；未实测需标记。

托管不可达时报告具体阻碍，保留文件；换候选需实际测试，涉及费用或代理的需求变更需用户决定。不可虚构网址或称发布成功。仅在有真实网址后用本地开源二维码工具生成二维码，不依赖在线二维码服务。可用本地已有 `qrcode`，缺失时在用户工作目录建立隔离环境安装，不修改全局依赖。

官方参考：
- https://developers.cloudflare.com/pages/get-started/direct-upload/
- https://developers.cloudflare.com/pages/platform/limits/
- https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages
- https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site
