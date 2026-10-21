# 在别的 AI Agent 里能用吗

`SKILL.md` 是 Anthropic 在 2025 年 12 月开放的**跨平台标准**，不是 Claude 专属。
但「能读规范」和「能跑起来」是两件事——这个 Skill 要执行 Python 和 ffmpeg，
所以**只有能跑本地命令的 agent 才完整可用**。

> **实测范围**：本项目只在 **Windows 11** 上验证过完整出片流程。
> 下面关于 macOS / Linux 和各 agent 平台的判断，依据是这些平台的能力文档
> 和代码里的跨平台处理，**不是实测结果**。跑通了欢迎反馈，跑不通也欢迎提 Issue。

## 一句话结论

| 平台 | 能跑吗 | 原因 |
|---|---|---|
| Claude Code | ✅ 完整 | 原生 Skill + 本地命令 |
| OpenAI Codex CLI | ✅ 完整 | 支持 SKILL.md / AGENTS.md + 本地命令 |
| Gemini CLI | ✅ 完整 | 支持 skills + 本地命令 |
| Cursor / Cline / Windsurf | ✅ 完整 | 读 AGENTS.md / skills，有终端 |
| GitHub Copilot（Agent 模式） | ✅ 完整 | 同上 |
| **ChatGPT GPTs（网页）** | ❌ 不行 | 不执行本地代码，只吃 Instructions + Knowledge |
| **Gemini Gems（网页）** | ❌ 不行 | 同上 |
| **Claude.ai 网页普通对话** | ❌ 不行 | 同上（Skills 需付费版 + 代码执行环境） |

核心判断标准只有一条：**这个 agent 能不能在你这台电脑上敲 `python` 和 `ffmpeg`。**
能，就完整可用；不能，就只能当提示词参考。

## 各平台装在哪

把整个 `xm-handdraw-video` 目录放到下面对应的位置，重启 agent 即可。

可以直接 clone 到目标位置：

```bash
git clone https://github.com/sratour/xm-handdraw-video.git ~/.claude/skills/xm-handdraw-video
```

| 平台 | 目录 |
|---|---|
| Claude Code | `~/.claude/skills/xm-handdraw-video/`（全局）<br>或 `<项目>/.claude/skills/xm-handdraw-video/`（项目级） |
| OpenAI Codex CLI | `<项目>/.agents/skills/xm-handdraw-video/` |
| Gemini CLI | `~/.gemini/skills/xm-handdraw-video/`（全局）<br>或 `<项目>/.gemini/skills/xm-handdraw-video/` |
| Cursor | `~/.cursor/skills/xm-handdraw-video/` |
| WorkBuddy | `~/.workbuddy-ai/skills/xm-handdraw-video/` |

> 各平台的目录约定仍在变，**以官方文档为准**。找不到就直接问 agent
> 「你的 skill 目录在哪」，或者看它有没有 `/skills` 这类命令。

装好后验证：让 agent 说一句「列出你有哪些 skills」，能出现 `xm-handdraw-video` 就成。

## 为什么网页版 GPTs / Gems 跑不了

这个 Skill 的产出链路是：

```
口播稿 → 分镜(JSON) → 配音(mp3) → 出图(png) → 逐笔动画(mp4) → 合成(mp4)
         本地脚本      本地+网络     网络      本地 ffmpeg     本地 ffmpeg
```

中间要读写文件、要调 ffmpeg 编码视频、要跑几分钟。GPTs 和 Gems 是**纯对话沙箱**：
不给你文件系统，也不给命令行，最多能读你上传的文件。所以它连第 ① 步都做不了。

它们能做的事：帮你**写口播稿、设计分镜、生成画风提示词**。
真要出片，还得在本地跑。

## 一定要在 GPTs 里用怎么办

两条路，都不轻松：

1. **把 Skill 包成 MCP Server**，让 GPTs 通过 Actions 调用远程服务。
   这要求你有一台能跑 Python + ffmpeg 的服务器，本项目的脚本可以直接复用。
2. **拆成两段**：GPTs 只负责生成 `storyboard.json`，你拿回本地跑
   `xm_handdraw.py build`。这是最省事的折中。

## 跨平台要注意的三件事

1. **ffmpeg 要带 libass**。没有的话字幕会**静默丢失**，不报错。
   macOS `brew install ffmpeg` 自带；Linux `apt install ffmpeg` 一般也有；
   Windows 必须下 **full** 版。
2. **装在奇怪路径时**设 `FFMPEG_BIN` 环境变量指过去，脚本会优先用它。
3. **字体不用装进系统**。`assets/fonts/` 里的字体通过 libass 的 `fontsdir`
   直接加载。这是 libass 本身的能力，各平台应当一致，但本项目只在 Windows 上验证过。

## 换电脑 / 换平台后的自检

不管装到哪个 agent，先让它跑这一条：

```bash
python scripts/doctor.py
```

十项全绿就说明环境齐了，跟宿主 agent 是谁无关——**这个 Skill 的能力来自
本地脚本，不依赖任何特定 agent 的功能**。
