# xm-handdraw-video

给 AI agent 的项目说明。**动手前先读 `SKILL.md`**——完整工作流、参数、踩过的坑都在里面。
本文件只负责让不认识 Skill 格式的工具（Codex、Cursor、Cline 等）知道这里有什么。

## 这是什么

把一段口播稿变成「一笔一笔画出来」的讲解视频。不调用视频生成模型：
先把静态插画矢量化成笔画，再按累积弧长逐帧推进笔尖。

**画风完全由静态图决定**，动画层与画风解耦——换画风只需换配方和锚点图。

## 30 秒上手

```bash
python scripts/doctor.py                    # 先自检环境，缺什么它会说
python scripts/xm_handdraw.py providers     # 看生图平台密钥是否就绪
python scripts/xm_handdraw.py build \
  --script 口播稿.txt --style handdrawn-explainer \
  --workdir ./project -o final.mp4
```

## 关键约束（违反会静默出错）

1. **ffmpeg 必须带 libass**。缺了不会报错，成片只是没字幕。
2. **满幅场景插画要加 `--paint-bg --margin 0`**。否则浅色背景被判成纸底整片跳过，
   覆盖率只有 40% 左右，四周还留一圈纸边。
3. **换画风前先把角色图重画成目标画风**。参考图只有一个名额，
   `--character` 会顶掉画风锚点；拿另一个角色的立绘顶替，人物就换了。
4. **字幕字体名不是文件名**。`compose.FONT_NAME` 要填字体的 family 名，
   填错会静默回退系统黑体。
5. **出图后必须逐张看图**。`subject` 里没写否定项时，模型会把「三轮汽车」画成马车、
   把「车厢内部」画成地铁。

## 目录

| 路径 | 内容 |
|---|---|
| `SKILL.md` | 完整工作流与全部经验（**主入口**） |
| `INSTALL.md` | 换电脑安装 |
| `COMPATIBILITY.md` | 各 AI Agent 的兼容性与安装位置 |
| `scripts/` | 全部实现，纯标准库 + Pillow + numpy + edge-tts |
| `styles/` | 画风配方，一个 `.md` 一个画风 |
| `assets/fonts/` | 字幕字体，走 libass `fontsdir` 加载，不用装系统 |
| `references/` | 道具库、平台说明、配方规范 |

## 改代码时注意

- 别写死用户目录或系统路径；ffmpeg 走 `render.find_ffmpeg()`，
  它认 `FFMPEG_BIN` 环境变量
- 字幕位置按画幅自动判断（竖屏距底 30%，横屏贴底），见 `compose.default_subtitle_pos()`
- 改完跑 `python scripts/doctor.py` 和一次 `build`，别只看语法没报错
