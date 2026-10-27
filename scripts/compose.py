"""
compose.py — 把逐场景的手绘片段合成为成片

流程：
  各场景 mp3 → 补齐到各自片段长度 → 拼成一条音轨
  各场景 mp4 → 直接拼接（编码参数一致，可无损 copy）
  音轨 + 视频 + 字幕 → 成片

字幕默认**烧录进画面**。这样成片目录里不会留 SRT 文件，
也不会出现「播放器自动加载字幕 → 双重字幕」的问题。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

from render import find_ffmpeg
from scenes import Storyboard, write_srt


class ComposeError(RuntimeError):
    pass


# 字幕字体：优先用 Skill 自带的字体（assets/fonts），没有就回退系统黑体。
# libass 靠 fontconfig 找字，不注册系统也能用——只要把目录传给 fontsdir。
_SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_DIR = os.path.join(_SKILL_ROOT, "assets", "fonts")
FONT_NAME = "Douyin Sans"          # 抖音美好体，family name
FONT_FALLBACK = "Microsoft YaHei"


def subtitle_font() -> tuple[str, str | None]:
    """返回 (FontName, fontsdir)。自带字体目录为空时回退系统黑体。"""
    try:
        has_font = any(f.lower().endswith((".otf", ".ttf", ".ttc"))
                       for f in os.listdir(FONT_DIR))
    except OSError:
        has_font = False
    return (FONT_NAME, FONT_DIR) if has_font else (FONT_FALLBACK, None)


def _run(cmd: list[str], what: str):
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    if p.returncode != 0:
        tail = (p.stderr or "")[-1200:]
        raise ComposeError(f"{what} 失败（ffmpeg 退出码 {p.returncode}）：\n{tail}")
    return p


def has_libass(ffmpeg: str | None = None) -> bool:
    exe = ffmpeg or find_ffmpeg()
    if not exe:
        return False
    try:
        p = subprocess.run([exe, "-hide_banner", "-filters"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        return "subtitles" in p.stdout
    except Exception:
        return False


def concat_video(clips: list[str], out_path: str, workdir: str) -> str:
    """编码参数一致时用 concat demuxer 直接拼，不重编码。"""
    if not clips:
        raise ComposeError("没有可拼接的片段")
    if len(clips) == 1:
        shutil.copyfile(clips[0], out_path)
        return out_path
    lst = os.path.join(workdir, "concat.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for c in clips:
            f.write("file '" + os.path.abspath(c).replace("\\", "/").replace("'", "'\\''") + "'\n")
    _run([find_ffmpeg(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
          "-i", lst, "-c", "copy", os.path.abspath(out_path)], "拼接视频")
    return out_path


def build_audio_track(sb: Storyboard, out_path: str) -> str:
    """
    每个场景的配音用 apad + atrim 补齐到该片段的总长度（配音 + 停顿），
    再一次性 concat。这样音轨长度与视频长度天然一致，不会错位。
    """
    exe = find_ffmpeg()
    segs = [s for s in sb.scenes if s.audio and os.path.isfile(s.audio)]
    if not segs:
        raise ComposeError("没有任何配音文件，先跑 voice")

    # 每个场景补齐到该片段的总长度，与视频片段严格一一对应
    clip_len = sb.clip_lengths()

    cmd = [exe, "-y", "-loglevel", "error"]
    for s in sb.scenes:
        cmd += ["-i", os.path.abspath(s.audio)]

    parts = []
    for i, ln in enumerate(clip_len):
        parts.append(f"[{i}:a]apad,atrim=0:{ln:.3f},asetpts=N/SR/TB[a{i}]")
    chain = "".join(f"[a{i}]" for i in range(len(clip_len)))
    parts.append(f"{chain}concat=n={len(clip_len)}:v=0:a=1[out]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[out]",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", os.path.abspath(out_path)]
    _run(cmd, "合成音轨")
    return out_path


def default_subtitle_pos(video_size: tuple[int, int]) -> float:
    """字幕基线「距画面底部」的比例。

    竖屏（9:16）字幕不贴底：底部 30% 留给平台 UI（进度条、标题、评论按钮）
    和人物的脚，字幕放在画面约 70% 高度处。
    横屏和方形视频没有这个顾虑，保持贴底（5.5%）。
    """
    w, h = video_size
    return 0.30 if h > w else 0.055


def subtitle_metrics(video_size: tuple[int, int],
                     pos: float | None = None) -> tuple[int, int, int]:
    """按画面尺寸算字幕字号、描边、下边距。

    libass 把 SRT 当成 PlayResY=288 的脚本，字号会按 画面高/288 放大。
    所以横屏写 FontSize=20 正好，竖屏同样写 20 就会变成 90px 的巨字、
    一句话占掉半屏。这里统一按「短边 5.2%」反算，两种画幅视觉大小一致。

    pos 是字幕底边距画面底部的比例，默认见 default_subtitle_pos。
    MarginV 同样活在 288 坐标系里，所以直接 pos*288 就是它该取的值。
    """
    w, h = video_size
    px = max(12.0, min(w, h) * 0.048)
    font_size = max(6, round(px * 288 / max(h, 1)))
    outline = max(1, round(font_size / 9))
    if pos is None:
        pos = default_subtitle_pos(video_size)
    margin_v = max(8, min(280, round(pos * 288)))
    return font_size, outline, margin_v


def subtitle_max_line(video_size: tuple[int, int]) -> int:
    """一行最多几个字。留 6% 边距，中文按方块字算宽度。"""
    w, h = video_size
    px = max(12.0, min(w, h) * 0.048)
    return max(8, int(w * 0.94 / px))


def mux(video: str, audio: str, out_path: str,
        srt: str | None = None, burn_subtitles: bool = True,
        font_size: int | None = None, margin_v: int | None = None,
        video_size: tuple[int, int] | None = None,
        subtitle_pos: float | None = None) -> str:
    """把音轨和字幕合到视频上。字幕默认烧录。"""
    exe = find_ffmpeg()
    cmd = [exe, "-y", "-loglevel", "error", "-i", os.path.abspath(video),
           "-i", os.path.abspath(audio)]

    if video_size:
        fs, outline, mv = subtitle_metrics(video_size, pos=subtitle_pos)
    else:
        fs, outline, mv = 20, 2, 40
    font_size = font_size or fs
    margin_v = margin_v if margin_v is not None else mv
    outline = max(1, round(outline * font_size / fs)) if fs else outline

    burn = bool(srt and burn_subtitles and has_libass(exe))
    if burn:
        srt_esc = os.path.abspath(srt).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        fname, fdir = subtitle_font()
        style = (f"FontName={fname},FontSize={font_size},"
                 f"PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
                 f"BorderStyle=1,Outline={outline},Shadow=0,Alignment=2,MarginV={margin_v}")
        subs = f"subtitles=filename='{srt_esc}'"
        if fdir:
            d_esc = fdir.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
            subs += f":fontsdir='{d_esc}'"
        subs += f":force_style='{style}'"
        cmd += ["-vf", subs,
                "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p"]
    else:
        cmd += ["-c:v", "copy"]

    cmd += ["-c:a", "aac", "-b:a", "128k", "-shortest",
            "-movflags", "+faststart", os.path.abspath(out_path)]
    _run(cmd, "合成成片")
    return out_path


def media_duration(path: str) -> float:
    """读成片容器的实际时长（秒）。用来核对音画有没有错位。"""
    exe = find_ffmpeg()
    if not exe or not os.path.isfile(path):
        return 0.0
    p = subprocess.run([exe, "-hide_banner", "-i", path],
                       capture_output=True, text=True, errors="replace")
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", p.stderr or "")
    if not m:
        return 0.0
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def build_film(sb: Storyboard, out_path: str, workdir: str,
               burn_subtitles: bool = True, keep_srt: bool = False,
               video_size: tuple[int, int] | None = None,
               subtitle_pos: float | None = None,
               verbose: bool = True) -> dict:
    """把已经渲染好的各场景片段合成成片。"""
    clips = [s.clip for s in sb.scenes if s.clip and os.path.isfile(s.clip)]
    if len(clips) != len(sb.scenes):
        missing = [s.id for s in sb.scenes if not (s.clip and os.path.isfile(s.clip))]
        raise ComposeError(f"这些场景还没有片段：{', '.join(missing)}")

    os.makedirs(workdir, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="xm-compose-", dir=workdir)
    try:
        silent = os.path.join(tmp, "silent.mp4")
        concat_video(clips, silent, tmp)
        if verbose:
            print(f"[compose] 拼接 {len(clips)} 段 → {os.path.basename(silent)}")

        audio = os.path.join(tmp, "narration.m4a")
        build_audio_track(sb, audio)
        if verbose:
            print(f"[compose] 音轨 {sb.total_duration():.1f}s")

        srt = os.path.join(workdir, os.path.splitext(os.path.basename(out_path))[0] + ".srt")
        max_line = subtitle_max_line(video_size) if video_size else 17
        write_srt(sb.srt_cues(max_chars=max_line), srt, max_line=max_line)
        burn = burn_subtitles and has_libass()
        mux(silent, audio, out_path, srt=srt, burn_subtitles=burn,
            video_size=video_size, subtitle_pos=subtitle_pos)
        if verbose:
            pos = subtitle_pos
            if pos is None and video_size:
                pos = default_subtitle_pos(video_size)
            where = f"（距底部 {pos * 100:.0f}%）" if pos is not None else ""
            fname, _ = subtitle_font()
            print(f"[compose] 字幕{'已烧录' if burn else '未烧录（ffmpeg 缺 subtitles 滤镜）'}"
                  f"{where}｜字体 {fname}")

        if not keep_srt:
            # 避免播放器自动加载字幕造成双字幕
            try:
                os.remove(srt)
            except OSError:
                pass

        # 音画自检：视频比音轨短时 -shortest 会把片尾旁白剪掉，必须喊出来
        real = media_duration(out_path)
        plan = sb.total_duration()
        if real and plan - real > 0.15:
            print(f"[warn] 成片 {real:.2f}s 比分镜计划 {plan:.2f}s 短 {plan - real:.2f}s，"
                  f"片尾旁白可能被剪掉；检查各片段长度是否都等于 duration+gap",
                  file=sys.stderr)
        return {"video": os.path.abspath(out_path), "srt": srt if keep_srt else None,
                "burned": burn, "duration": round(plan, 2)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    import argparse

    def _size(s: str) -> tuple[int, int]:
        w, _, h = s.lower().partition("x")
        return int(w), int(h)

    ap = argparse.ArgumentParser(
        description="把分镜片段合成为成片（片段已渲染好时，改字幕不用重跑动画）")
    ap.add_argument("storyboard")
    ap.add_argument("-o", "--out", default="final.mp4")
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--video-size", help="如 720x1280；决定字幕位置与字号")
    ap.add_argument("--subtitle-pos", type=float,
                    help="字幕底边距画面底部的比例，0~1。默认竖屏 2/3、横屏 0.055")
    ap.add_argument("--keep-srt", action="store_true")
    a = ap.parse_args()

    sb = Storyboard.load(a.storyboard)
    for s in sb.scenes:
        if not s.clip:
            s.clip = os.path.join(a.workdir, "clips", f"{s.id}.mp4")
    vs = _size(a.video_size) if a.video_size else None
    print(build_film(sb, a.out, a.workdir, keep_srt=a.keep_srt,
                     video_size=vs, subtitle_pos=a.subtitle_pos))
