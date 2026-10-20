# 参与

三种方式，从轻到重。**只提问题不写代码也很有用**——这个项目的价值一半在
`SKILL.md` 那堆踩坑记录里，多一个翻车案例就少一个人踩。

## 一、报一个翻车（最有价值）

出图/渲染翻车时，光说「画错了」没法修。请带上这三样：

| 要带的 | 为什么 |
|---|---|
| `storyboard.json` 里那一场的完整 JSON | 问题基本都出在 `subject` 描述不够，要看原文才能判断该补哪句否定项 |
| 出图结果（`shots/scene_XX.png`） | 决定是「模型没听懂」还是「描述本身有歧义」 |
| 你用的画风 id + 是否带 `--character`/`--anchor` | 同一个描述在不同画风下表现完全不同 |

如果是动画/成片的问题，再补一条抽帧命令的输出：

```bash
ffmpeg -ss <该场 start+0.3> -i final.mp4 -frames:v 1 -y check.png
```

## 二、加一个新画风

这是最欢迎的贡献类型。

1. 复制 `styles/retro-mystery-manga.md`，改 `id` 和内容
2. 按 `references/style-recipe-spec.md` 的**五段式**写（缺段会被 `recipe.py` 判为无效）
3. 抽卡 30~50 张，挑一张最满意的存成 `assets/<你的id>/anchor.png`
4. 用同一段口播稿跑通一遍 `build`，确认能出片
5. 把成片截图放进 PR 描述——**没有截图的画风 PR 没法评审**

两条硬要求，违反会让成片发脏：

- **不要网点、排线、网屏纹理、渐变、喷枪。** 上色是把原图量化成几种颜色后逐块涂的，
  网点会把一个色块拆成几十种灰阶，涂出来又碎又脏；渐变会让扫描线失去明确边界
- **线条要闭合。** 上色按线条划分区域，破口会让颜色漏到隔壁
- **背景别画栏杆、栅栏、书架、窗格**这类密集线条，涂色时间要留给主体

## 三、改代码

```bash
git clone https://github.com/sratour/xm-handdraw-video.git
cd xm-handdraw-video
python -m venv .venv && . .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/doctor.py                        # 十项全绿再动手
```

改完自测：

```bash
python scripts/xm_handdraw.py animate --image 任意一张图.png -o /tmp/t.mp4 --duration 4
python scripts/xm_handdraw.py check --storyboard storyboard.json
```

第一条验证渲染链没坏（不需要生图密钥），第二条验证饱满度检查没坏。

### PR 检查清单

- [ ] `python scripts/doctor.py` 全绿
- [ ] `animate` 能出一条 4 秒片段
- [ ] 改了 `compose.py` / `render.py` 的话，横版和竖版都试一遍（字幕位置按画幅自动切）
- [ ] 改了 `VERSION` 的话，`python scripts/xm_handdraw.py --version` 输出一致
- [ ] 没有提交 `*.mp4`、`*.srt`、`frames/`、`shots/`、`clips/`（`.gitignore` 已挡，别强推）

### 跨平台注意

项目在 Windows / macOS / Linux 都要能跑，改动时注意：

- **不要写死 ffmpeg 路径**，走 `render.find_ffmpeg()`（`FFMPEG_BIN` 环境变量 → `which` → 各平台常见位置）
- **`subprocess.run` 一律带 `encoding="utf-8", errors="replace"`**。Windows 默认 GBK，
  解 UTF-8 输出会抛 `UnicodeDecodeError`
- **路径用 `os.path.join`**，别手拼 `/` 或 `\`

## 这些是刻意设计的，别当 bug 改

| 现象 | 为什么这样 |
|---|---|
| 背景默认不涂 | 让涂色时间集中在主体上，保留「白纸 + 彩色主体」的手绘感。要涂加 `--paint-bg` |
| `_fit()` 默认留 56px 边 | contain 缩放不裁内容。满幅插画要显式给 `--margin 0` |
| 竖版字幕在 70% 高度（距底 30%） | 底部要留给平台 UI 和人物脚部。试过 2/3，特写镜头会压脸 |
| 字幕不显示标点 | 切分仍按标点做，只洗掉写进 SRT 那行。显示标点会占掉近 1/10 的字宽 |
| `auto` 墨线模式默认偏向 `dark` | `edge` 遇到网点会失控。出图偏灰阶时 `dark` 反而更安全 |
| 实心色块会被挖空 | 纯黑区域原样细化会变成横七竖八的骨架，糊成一团。`--blob-thickness 0` 可关 |

## 许可

提交 PR 即表示同意以 [MIT](LICENSE) 授权你的贡献。
新增字体请确认许可允许再分发，并在 `assets/fonts/NOTICE.md` 补上版权方与许可类型。
