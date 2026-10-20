# 装到另一台电脑

整个 Skill 是**纯本地脚本 + 一个免费生图平台的密钥**，没有服务端、没有数据库，
拷过去配好环境就能用。目录 5 MB（大头是内置字体和预览图）。

## 要准备什么

| 东西 | 必须 | 说明 |
|---|---|---|
| Python 3.9+ | 必须 | 3.10 或更高更省事 |
| Pillow / numpy / edge-tts | 必须 | 三条 `pip install` 搞定 |
| ffmpeg（带 libass） | 必须 | 没 libass 就烧不了字幕，而且**不会报错**，成片只是没字幕 |
| 生图平台密钥 | 必须 | Agnes / ModelScope 任选，免费额度够用 |
| 字体 | 已内置 | `assets/fonts/DouyinSans.otf`，不用装进系统 |
| 画风配方、锚点图 | 已内置 | `styles/`、`assets/` 一起拷过去 |

---

## 第 1 步：拿到目录

三种方式，任选一种。

**A. 下载 zip（最简单，不用装 git）**

打开 https://github.com/sratour/xm-handdraw-video → 点绿色 `Code` 按钮 →
`Download ZIP` → 解压。得到的文件夹就是整个 Skill。

**B. git clone（之后能用 `git pull` 拿更新）**

复制这一行：

```bash
git clone https://github.com/sratour/xm-handdraw-video.git
```

> 提示「不是内部或外部命令」说明这台电脑没装 git。
> 装 Git for Windows（https://git-scm.com/download/win）或者直接走方式 A。

**C. 从旧电脑拷**

把整个 `xm-handdraw-video` 文件夹拷过去。**别只拷 `scripts/`**——
`styles/`（画风配方）、`assets/`（锚点图 + 字体）、`references/` 都是运行时要用。

放一个路径里**没有空格和中文**的位置，比如 `D:\xm-handdraw-video`。
（有空格也能跑，但偶尔会在命令行转义上踩坑。）

---

## 第 2 步：装 Python 和依赖

### 装 Python

去 https://www.python.org/downloads/ 下 3.12 或 3.13。

安装时**务必勾上 `Add python.exe to PATH`**，否则后面敲 `python` 会提示找不到。

装完验证，复制这一行：

```bash
python --version
```

出现 `Python 3.12.x` 之类的就对了。提示「不是内部或外部命令」说明 PATH 没配上，
重装一次并勾选那个选项。

### 装依赖

进入 Skill 目录（把路径换成你自己的），复制这一行：

```bash
cd D:\xm-handdraw-video
```

> 在 cmd 里跨盘符要写成 `cd /d D:\xm-handdraw-video`，PowerShell 不用加。

再复制这一行：

```bash
python -m pip install -r requirements.txt
```

装的是 `Pillow`、`numpy`、`edge-tts` 三个包。看到 `Successfully installed ...` 就成功了。

---

## 第 3 步：装 ffmpeg

这一步最容易漏，也最容易出错。

### 下载

去 https://www.gyan.dev/ffmpeg/builds/ ，下载 **`ffmpeg-release-full.7z`**。

> 一定要 **full** 版。essentials 版不带 libass，字幕会静默丢失。

### 解压并加进 PATH

1. 解压到 `C:\ffmpeg`，确认里面有 `C:\ffmpeg\bin\ffmpeg.exe`
2. 按 `Win` 键，输入「环境变量」，打开「编辑系统环境变量」
3. 点「环境变量」→ 在「用户变量」里选中 `Path` → 「编辑」→ 「新建」
4. 粘贴 `C:\ffmpeg\bin` → 一路「确定」
5. **关掉命令行窗口重新打开**（PATH 改动只对新窗口生效）

验证，复制这一行：

```bash
ffmpeg -version
```

出现版本号就对了。

---

## 第 4 步：配生图密钥

Skill 本身不出图，靠免费平台。任选一个，详见 `references/free-providers.md`。

以 Agnes 为例，设置环境变量后**重新打开命令行**：

```bash
setx AGNES_API_KEY "你的密钥"
```

> `setx` 写的是永久环境变量，只对新开的窗口生效。设完记得关掉再开。

如果这台电脑上装了 WorkBuddy 并且配过同款服务，脚本会自动读取，
这一步可以跳过。

---

## 第 5 步：自检

复制这一行：

```bash
python scripts\doctor.py
```

它会逐项检查并给出修复命令。全绿的样子：

```
✓ Python 版本  3.13.14
✓ 依赖 Pillow  12.3.0
✓ 依赖 numpy  2.5.4
✓ 依赖 edge-tts  7.3.1
✓ ffmpeg  C:\ffmpeg\bin\ffmpeg.exe
✓ ffmpeg 字幕滤镜  libass 可用
✓ 字幕字体  DouyinSans.otf（Douyin Sans）
✓ 画风配方  4 个配方齐全
✓ 生图平台  agnes
✓ 目录可写  D:\xm-handdraw-video

环境就绪，可以出片了。
```

有 `✗` 就照着下面的 `→` 修，修完再跑一次。

---

## 出第一条片子

```bash
python scripts\xm_handdraw.py providers
```

先确认平台密钥读到了，然后复制这一行（整条命令别换行）：

```bash
python scripts\xm_handdraw.py make --style handdrawn-explainer --subject "一位穿黑色西装的女性和一台白色洗衣机" -o demo.mp4 --duration 5
```

跑完会得到 `demo.mp4`，5 秒，逐笔画出这个场景。

---

## 常见问题

| 现象 | 原因 | 怎么办 |
|---|---|---|
| `python` 不是内部或外部命令 | 装 Python 时没勾 Add to PATH | 重装并勾上 |
| 成片没有字幕，也不报错 | ffmpeg 缺 libass | 换 gyan.dev 的 **full** 版 |
| 字幕字体不是抖音体 | `compose.FONT_NAME` 和字体 family 名不一致 | 跑 `doctor.py`，它会告诉你真实 family 名 |
| 出图报「缺少 AGNES_API_KEY」 | 环境变量没生效 | `setx` 之后必须**新开**命令行窗口 |
| `ModuleNotFoundError: edge_tts` | 只装了 Pillow 和 numpy | `python -m pip install edge-tts` |
| 中文路径报错 | 某些 ffmpeg 版本对非 ASCII 路径敏感 | 把 Skill 挪到纯英文路径 |
| 出图一直重试 503 | 免费平台限流 | 脚本已内置重试；持续失败就换平台 |

---

## 想在 WorkBuddy 里当 Skill 用

除了命令行，也可以让 WorkBuddy 直接调用。把整个目录放到：

- **用户级**（所有项目都能用）：`C:\Users\<你>\.workbuddy-ai\skills\xm-handdraw-video\`
- **项目级**（只有这个项目能用）：`<项目目录>\.workbuddy-ai\skills\xm-handdraw-video\`

放好后重启 WorkBuddy，它会读到 `SKILL.md` 里的 `name` 和 `description`。

---

## 换个地方要改的东西

只有一处：如果你把 `assets/fonts/` 里的字体换了，记得同步改
`scripts/compose.py` 里的 `FONT_NAME`，改成字体的 family 名（不是文件名）。

```python
from PIL import ImageFont
print(ImageFont.truetype("新字体.otf", 40).getname())   # ('字体family名', 'Bold')
```
