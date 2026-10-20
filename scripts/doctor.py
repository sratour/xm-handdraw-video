"""换电脑后先跑这个：检查环境缺什么。

    python scripts/doctor.py

只读检查，不改任何东西。每一项给出 ✓ / ✗ 和具体的修复命令。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OK, BAD, WARN = "✓", "✗", "△"


def line(mark: str, title: str, detail: str = "", fix: str = "") -> None:
    print(f"{mark} {title}" + (f"  {detail}" if detail else ""))
    if fix:
        for ln in fix.strip().splitlines():
            print(f"     → {ln.strip()}")


def check_python() -> bool:
    v = sys.version_info
    s = f"{v.major}.{v.minor}.{v.micro}"
    if v < (3, 9):
        line(BAD, "Python 版本", f"{s}（需要 3.9+）",
             "装 Python 3.10 或更高：https://www.python.org/downloads/")
        return False
    line(OK, "Python 版本", s)
    return True


def check_pkg(mod: str, pip_name: str) -> bool:
    try:
        m = __import__(mod)
        ver = getattr(m, "__version__", "")
        line(OK, f"依赖 {pip_name}", ver)
        return True
    except ImportError:
        line(BAD, f"依赖 {pip_name}", "未安装",
             f"{sys.executable} -m pip install {pip_name}")
        return False


def check_ffmpeg() -> bool:
    from render import find_ffmpeg
    exe = find_ffmpeg()
    if not exe:
        line(BAD, "ffmpeg", "找不到",
             "Windows: 下载 https://www.gyan.dev/ffmpeg/builds/ 的 release-full 版，\n"
             "解压后把 bin 目录加进 PATH，或直接把 ffmpeg.exe 放到 C:\\Users\\<你>\\bin\\\n"
             "macOS: brew install ffmpeg\n"
             "Linux: sudo apt install ffmpeg")
        return False
    line(OK, "ffmpeg", exe)

    # 没有 libass 就烧不了字幕，会静默出无声无字的成片
    try:
        p = subprocess.run([exe, "-hide_banner", "-filters"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        has = "subtitles" in p.stdout
    except Exception:
        has = False
    if has:
        line(OK, "ffmpeg 字幕滤镜", "libass 可用")
    else:
        line(BAD, "ffmpeg 字幕滤镜", "缺 libass，字幕烧不进去",
             "换一个 full 版 ffmpeg（gyan.dev 的 release-full 自带 libass）")
    return has


def check_font() -> bool:
    from compose import FONT_DIR, FONT_NAME
    try:
        fonts = [f for f in os.listdir(FONT_DIR) if f.lower().endswith((".otf", ".ttf", ".ttc"))]
    except OSError:
        fonts = []
    if not fonts:
        line(WARN, "字幕字体", "assets/fonts 为空，会用系统黑体兜底",
             "想要抖音美好体：把 .otf 放进 assets/fonts/，"
             "并把 compose.FONT_NAME 改成该字体的 family 名")
        return True
    from PIL import ImageFont
    try:
        f = ImageFont.truetype(os.path.join(FONT_DIR, fonts[0]), 40)
        real = f.getname()[0]
    except Exception as e:
        line(BAD, "字幕字体", f"{fonts[0]} 读不出来：{e}", "重新拷一份字体文件")
        return False
    if real != FONT_NAME:
        line(WARN, "字幕字体", f"文件是 {fonts[0]}，但 family 名是 {real}，"
                               f"compose.FONT_NAME 写的是 {FONT_NAME}",
             f"把 compose.FONT_NAME 改成 {real!r}，否则会静默回退系统字体")
        return True
    line(OK, "字幕字体", f"{fonts[0]}（{real}）")
    return True


def check_anchor() -> bool:
    import recipe
    missing = []
    for sid in ("handdrawn-explainer", "anime-jp-flat", "digital-flat", "retro-mystery-manga"):
        try:
            r = recipe.find(sid)
        except Exception:
            continue
        p = getattr(r, "anchor_path", None)
        if p and not os.path.isfile(p):
            missing.append(f"{sid}: {p}")
    if missing:
        line(WARN, "画风锚点图", "缺 " + "；".join(missing),
             "缺锚点图不影响出图，只是画风只能靠文字兜底")
    else:
        line(OK, "画风配方", "4 个配方齐全")
    return True


def check_keys() -> bool:
    import providers
    try:
        st = providers.status()
    except Exception as e:
        line(WARN, "生图平台", f"检查失败：{e}")
        return True
    ready = [s.name for s in st if getattr(s, "ready", False)]
    if ready:
        line(OK, "生图平台", "、".join(ready))
        return True
    line(BAD, "生图平台", "一个都没配好，出不了图",
         "任选一个免费平台配密钥（详见 references/free-providers.md）：\n"
         "Agnes：设环境变量 AGNES_API_KEY\n"
         "或在 WorkBuddy 的模型设置里配同款服务，脚本会自动读取")
    return False


def check_write() -> bool:
    try:
        with tempfile.NamedTemporaryFile(dir=ROOT, suffix=".tmp", delete=True):
            pass
        line(OK, "目录可写", ROOT)
        return True
    except Exception as e:
        line(BAD, "目录可写", f"写不进去：{e}",
             "把 Skill 放到你有权限的目录，别放 C:\\Program Files")
        return False


def main() -> int:
    print(f"xm-handdraw-video 环境自检\n目录：{ROOT}\n")
    results = [
        check_python(),
        check_pkg("PIL", "Pillow"),
        check_pkg("numpy", "numpy"),
        check_pkg("edge_tts", "edge-tts"),
        check_ffmpeg(),
        check_font(),
        check_anchor(),
        check_keys(),
        check_write(),
    ]
    print()
    if all(results):
        print("环境就绪，可以出片了。")
        print(f"试跑一条：{sys.executable} scripts/xm_handdraw.py styles")
        return 0
    print("有项目没通过，按上面的 → 修完再跑一次。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
