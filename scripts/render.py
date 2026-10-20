"""
render.py — 把笔画序列渲染成「逐笔手绘」视频

按累积弧长推进笔尖：所有笔画首尾相接成一条总长 L 的虚拟轨迹，
第 t 帧画出其中 progress(t) * L 的长度。已画完的笔画缓存在底图上，
每帧只重绘「当前这一笔」的部分，因此速度与笔画数无关。
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
from typing import Sequence

import numpy as np
from PIL import Image, ImageDraw

from paint import background_mask, build_paint_paths
from strokes import Stroke, StrokeSet, build_strokes, build_context


def find_ffmpeg() -> str | None:
    """找 ffmpeg：环境变量 → PATH → 常见安装位置。

    不写死任何用户目录，换机器 / 换系统都能用。
    装在奇怪的地方时设环境变量 FFMPEG_BIN 指过去就行。
    """
    env = os.environ.get("FFMPEG_BIN")
    if env and os.path.isfile(env):
        return env
    p = shutil.which("ffmpeg")
    if p:
        return p
    candidates = (
        # Windows
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        os.path.expanduser(r"~\bin\ffmpeg.exe"),
        # macOS（Apple Silicon / Intel 两套 Homebrew 路径）
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        # Linux
        "/usr/bin/ffmpeg",
        "/snap/bin/ffmpeg",
    )
    for cand in candidates:
        if os.path.isfile(cand):
            return cand
    return None


def _fit(strokes: Sequence[Stroke], src_w: int, src_h: int,
         out_w: int, out_h: int, margin: int):
    """等比缩放并居中，返回 (缩放后笔画坐标, 缩放比, 偏移)。"""
    avail_w, avail_h = out_w - 2 * margin, out_h - 2 * margin
    scale = min(avail_w / max(src_w, 1), avail_h / max(src_h, 1))
    off_x = (out_w - src_w * scale) / 2.0
    off_y = (out_h - src_h * scale) / 2.0
    out = []
    for s in strokes:
        pts = [(x * scale + off_x, y * scale + off_y) for x, y in s.points]
        out.append(Stroke(points=pts, length=s.length * scale, order=s.order))
    return out, scale, (off_x, off_y)


def _draw_segment(draw: ImageDraw.ImageDraw, pts, upto: float | None, color, width):
    """画折线；upto 不为 None 时只画前 upto 长度。"""
    if len(pts) < 2:
        return None
    if upto is None:
        draw.line(pts, fill=color, width=width, joint="curve")
        return pts[-1]
    acc = 0.0
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        seg = math.hypot(x1 - x0, y1 - y0)
        if acc + seg >= upto:
            r = 0.0 if seg < 1e-9 else (upto - acc) / seg
            tip = (x0 + (x1 - x0) * r, y0 + (y1 - y0) * r)
            if i > 0:
                draw.line(pts[:i + 1], fill=color, width=width, joint="curve")
            if math.hypot(tip[0] - x0, tip[1] - y0) > 0.3:
                draw.line([(x0, y0), tip], fill=color, width=width)
            return tip
        acc += seg
    draw.line(pts, fill=color, width=width, joint="curve")
    return pts[-1]


def render_video(
    stroke_set: StrokeSet,
    out_path: str,
    size: tuple[int, int] = (1280, 720),
    fps: int = 30,
    duration: float = 8.0,
    hold: float = 1.2,
    ink: tuple[int, int, int] = (32, 32, 32),
    paper: tuple[int, int, int] = (255, 255, 255),
    line_width: int = 3,
    margin: int = 56,
    pen_tip: bool = True,
    pen_color: tuple[int, int, int] = (214, 69, 65),
    lead_in: float = 0.0,
    keep_frames: bool = False,
    crf: int = 20,
) -> str:
    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        raise RuntimeError("找不到 ffmpeg，无法合成视频")

    out_w, out_h = size
    strokes, _scale, _off = _fit(stroke_set.strokes, stroke_set.width, stroke_set.height,
                                 out_w, out_h, margin)
    if not strokes:
        raise RuntimeError("没有可绘制的笔画，检查输入图或阈值设置")

    cum = [0.0]
    for s in strokes:
        cum.append(cum[-1] + s.length)
    total = cum[-1]

    draw_frames = max(1, int(round(duration * fps)))
    hold_frames = max(0, int(round(hold * fps)))
    n_frames = draw_frames + hold_frames

    tmp = tempfile.mkdtemp(prefix="xm-handdraw-")
    base = Image.new("RGB", (out_w, out_h), paper)
    base_draw = ImageDraw.Draw(base)
    done = 0

    try:
        for f in range(n_frames):
            if f < draw_frames:
                p = f / max(1, draw_frames - 1)
                # 起笔略微慢一点，其余匀速，接近真实手感
                p = p * p * (3 - 2 * p) * 0.12 + p * 0.88
                target = p * total
            else:
                target = total

            while done < len(strokes) and cum[done + 1] <= target:
                _draw_segment(base_draw, strokes[done].points, None, ink, line_width)
                done += 1

            frame = base.copy()
            fd = ImageDraw.Draw(frame)
            tip = None
            if done < len(strokes):
                tip = _draw_segment(fd, strokes[done].points,
                                    target - cum[done], ink, line_width)
            else:
                tip = strokes[-1].points[-1] if strokes else None

            if pen_tip and tip and f < n_frames - 1:
                tx, ty = tip
                r = max(3, line_width + 2)
                fd.ellipse([tx - r, ty - r, tx + r, ty + r], fill=pen_color)

            frame.save(os.path.join(tmp, f"f_{f:05d}.png"))

        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
        cmd = [
            ffmpeg, "-y", "-loglevel", "error",
            "-framerate", str(fps),
            "-i", os.path.join(tmp, "f_%05d.png"),
            "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            os.path.abspath(out_path),
        ]
        subprocess.run(cmd, check=True)
    finally:
        if not keep_frames:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print(f"[render] 帧文件保留在 {tmp}")

    return os.path.abspath(out_path)


# ---------------------------------------------------------------- 彩色渲染
#
# 与黑白渲染的根本区别：不是「用固定颜色重绘笔画」，而是给每个像素记录一个
# 「被画到的时刻」，渲染时该像素直接取自原图 —— 所以颜色保留下来了。
#
# 时序是先勾线、后上色，和真人作画一致：
#     0 ──────────── ink_ratio ── gap ────────────── 1.0
#       逐笔勾出墨线              马克笔把色块涂满

def _plen(pts: np.ndarray) -> float:
    """折线总弧长。"""
    if len(pts) < 2:
        return 0.0
    d = np.diff(pts, axis=0)
    return float(np.hypot(d[:, 0], d[:, 1]).sum())


def _place(src_w: int, src_h: int, out_w: int, out_h: int, margin: int):
    """等比 contain 的缩放比与偏移。"""
    scale = min((out_w - 2 * margin) / max(src_w, 1),
                (out_h - 2 * margin) / max(src_h, 1))
    off_x = (out_w - src_w * scale) / 2.0
    off_y = (out_h - src_h * scale) / 2.0
    return scale, off_x, off_y


def _stamp_disk(reveal: np.ndarray, cx: float, cy: float, radius: float, t: float):
    """在 (cx,cy) 盖一个半径 radius 的圆，把圆内像素的显影时刻压到 t。"""
    h, w = reveal.shape
    x0, x1 = max(0, int(cx - radius)), min(w, int(cx + radius) + 2)
    y0, y1 = max(0, int(cy - radius)), min(h, int(cy + radius) + 2)
    if x1 <= x0 or y1 <= y0:
        return
    ys, xs = np.ogrid[y0:y1, x0:x1]
    m = (xs - cx) ** 2 + (ys - cy) ** 2 <= radius * radius
    patch = reveal[y0:y1, x0:x1]
    np.minimum(patch, np.float32(t), out=patch, where=m)


def _stamp_path(reveal: np.ndarray, points, radius: float,
                t0: float, t1: float, step: float = 1.0):
    """沿路径按弧长推进，给经过的像素打上递增的显影时刻。"""
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) == 0:
        return
    if len(pts) == 1:
        _stamp_disk(reveal, pts[0, 0], pts[0, 1], radius, t0)
        return

    seg = np.diff(pts, axis=0)
    cum = np.r_[0.0, np.cumsum(np.hypot(seg[:, 0], seg[:, 1]))]
    total = float(cum[-1])
    if total <= 1e-6:
        _stamp_disk(reveal, pts[0, 0], pts[0, 1], radius, t0)
        return

    n = max(2, int(total / max(step, 0.5)) + 1)
    s = np.linspace(0.0, total, n)
    xs = np.interp(s, cum, pts[:, 0])
    ys = np.interp(s, cum, pts[:, 1])
    span = t1 - t0
    for x, y, frac in zip(xs, ys, s / total):
        _stamp_disk(reveal, float(x), float(y), radius, t0 + span * float(frac))


def _tip_at(segments, t: float, start: int = 0):
    """当前时刻笔尖位置；返回 (点, 命中的段序号)。"""
    for i in range(start, len(segments)):
        t0, t1, pts = segments[i]
        if t < t0:
            return None, i
        if t <= t1:
            if len(pts) == 0:
                return None, i
            if len(pts) == 1 or t1 - t0 <= 1e-9:
                return (float(pts[0][0]), float(pts[0][1])), i
            d = np.diff(pts, axis=0)
            cum = np.r_[0.0, np.cumsum(np.hypot(d[:, 0], d[:, 1]))]
            total = float(cum[-1])
            if total <= 1e-9:
                return (float(pts[0][0]), float(pts[0][1])), i
            target = ((t - t0) / (t1 - t0)) * total
            k = int(np.searchsorted(cum, target))
            k = min(max(k, 1), len(pts) - 1)
            seg_len = cum[k] - cum[k - 1]
            f = 0.0 if seg_len <= 1e-9 else (target - cum[k - 1]) / seg_len
            return (float(pts[k - 1][0] + (pts[k][0] - pts[k - 1][0]) * f),
                    float(pts[k - 1][1] + (pts[k][1] - pts[k - 1][1]) * f)), i
    return None, len(segments)


def render_colored(
    rgb: np.ndarray,
    ink_mask: np.ndarray | None,
    stroke_set: StrokeSet,
    out_path: str,
    size: tuple[int, int] = (1280, 720),
    fps: int = 30,
    duration: float = 8.0,
    hold: float = 1.2,
    ink_ratio: float = 0.55,
    gap: float = 0.03,
    line_width: int = 3,
    margin: int = 56,
    tile: int = 220,
    brush_radius: float | None = None,
    skip_background: bool = True,
    pen_tip: bool = True,
    pen_color: tuple[int, int, int] = (214, 69, 65),
    paper: tuple[int, int, int] | None = None,
    crf: int = 20,
) -> dict:
    """
    彩色手绘渲染：先勾线，再上色，逐像素显影。

    rgb / ink_mask / stroke_set 必须来自同一个 build_context()，
    三者坐标一致，否则上色会涂到错位置。

    paper 为 None 时会自动推断：如果背景是浅色且被跳过不涂，就用背景色当纸色，
    否则用纯白。这一步能消掉「背景一部分被涂、一部分没涂」造成的斑驳感。
    """
    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        raise RuntimeError("找不到 ffmpeg，无法合成视频")

    if paper is None:
        paper = (255, 255, 255)
        if skip_background and ink_mask is not None:
            bg = background_mask(rgb)
            if bg.any():
                avg = np.rint(rgb[bg].mean(axis=0)).astype(int)
                if avg.min() > 200:          # 只有浅色背景才拿它当纸色
                    paper = tuple(int(v) for v in avg)

    src_h, src_w = rgb.shape[:2]
    out_w, out_h = size
    scale, off_x, off_y = _place(src_w, src_h, out_w, out_h, margin)

    # 原图铺到输出画布（这一步就是最终画面的颜色来源）
    art_im = Image.fromarray(rgb).resize(
        (max(1, int(round(src_w * scale))), max(1, int(round(src_h * scale)))),
        Image.LANCZOS)
    canvas = Image.new("RGB", (out_w, out_h), tuple(paper))
    canvas.paste(art_im, (int(round(off_x)), int(round(off_y))))
    art = np.asarray(canvas, dtype=np.uint8)

    def to_out(pts):
        a = np.asarray(pts, dtype=np.float64)
        return np.c_[a[:, 0] * scale + off_x, a[:, 1] * scale + off_y]

    reveal = np.full((out_h, out_w), np.inf, dtype=np.float32)
    segments: list[tuple[float, float, np.ndarray]] = []

    # ① 勾线：笔尖细，半径取线宽一半
    ink_paths = [(np.asarray(s.points, dtype=np.float64),
                  max(1.0, line_width / 2.0)) for s in stroke_set.strokes]

    # ② 上色：马克笔扫描线
    paint_paths: list[tuple[np.ndarray, float]] = []
    if ink_mask is not None:
        for pts, radius in build_paint_paths(ink_mask, rgb=rgb, tile=tile,
                                             brush_radius=brush_radius,
                                             skip_background=skip_background):
            paint_paths.append((pts, max(2.0, radius * scale)))

    has_paint = bool(paint_paths)
    ink_end = ink_ratio if has_paint else 1.0
    paint_start = (ink_end + gap) if has_paint else 1.0

    ink_total = sum(_plen(p) for p, _ in ink_paths) or 1.0
    cursor = 0.0
    for pts, radius in ink_paths:
        span = ink_end * _plen(pts) / ink_total
        outp = to_out(pts)
        _stamp_path(reveal, outp, radius, cursor, cursor + span)
        segments.append((cursor, cursor + span, outp))
        cursor += span

    if has_paint:
        paint_total = sum(_plen(p) for p, _ in paint_paths) or 1.0
        cursor = paint_start
        room = 1.0 - paint_start
        for pts, radius in paint_paths:
            span = room * _plen(pts) / paint_total
            outp = to_out(pts)
            _stamp_path(reveal, outp, radius, cursor, cursor + span)
            segments.append((cursor, cursor + span, outp))
            cursor += span

    draw_frames = max(1, int(round(duration * fps)))
    hold_frames = max(0, int(round(hold * fps)))
    n_frames = draw_frames + hold_frames

    paper_arr = np.array(paper, dtype=np.uint8)
    tmp = tempfile.mkdtemp(prefix="xm-handdraw-")
    buf = np.empty_like(art)
    tip_idx = 0
    try:
        for f in range(n_frames):
            if f < draw_frames:
                p = f / max(1, draw_frames - 1)
                p = p * p * (3 - 2 * p) * 0.12 + p * 0.88
            else:
                p = 1.0

            shown = reveal <= np.float32(p)
            buf[:] = paper_arr
            np.copyto(buf, art, where=shown[:, :, None])
            im = Image.fromarray(buf)

            if pen_tip and f < n_frames - 1:
                tip, tip_idx = _tip_at(segments, p, tip_idx)
                if tip:
                    fd = ImageDraw.Draw(im)
                    r = max(3, line_width + 2)
                    fd.ellipse([tip[0] - r, tip[1] - r, tip[0] + r, tip[1] + r],
                               fill=pen_color)
            im.save(os.path.join(tmp, f"f_{f:05d}.png"))

        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
        subprocess.run([
            ffmpeg, "-y", "-loglevel", "error",
            "-framerate", str(fps),
            "-i", os.path.join(tmp, "f_%05d.png"),
            "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            os.path.abspath(out_path),
        ], check=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    painted = int(np.isfinite(reveal).sum())
    return {
        "video": os.path.abspath(out_path),
        "ink_paths": len(ink_paths),
        "paint_paths": len(paint_paths),
        "painted_pixels": painted,
        "total_pixels": out_w * out_h,
        "coverage": round(painted / max(1, out_w * out_h), 4),
    }


def make_colored_video(
    image_path: str,
    out_path: str,
    duration: float = 8.0,
    **kwargs,
) -> str:
    """彩色一条龙：图片 → 勾线 + 上色 → 彩色 MP4。"""
    rgb, mask, ss = build_context(
        image_path,
        max_side=kwargs.pop("max_side", 1100),
        mode=kwargs.pop("ink_mode", "auto"),
        threshold=kwargs.pop("threshold", None),
        invert=kwargs.pop("invert", False),
        simplify_eps=kwargs.pop("simplify_eps", 1.1),
        crop=kwargs.pop("crop", True),
        blob_thickness=kwargs.pop("blob_thickness", 6),
    )
    print(f"[render] 笔画 {len(ss.strokes)} 条，总弧长 {ss.total_length:.0f}px")
    info = render_colored(rgb, mask, ss, out_path, duration=duration, **kwargs)
    print(f"[render] 勾线 {info['ink_paths']} 段 + 上色 {info['paint_paths']} 段，"
          f"画面覆盖 {info['coverage'] * 100:.1f}%")
    return info["video"]


def make_video(
    image_path: str,
    out_path: str,
    duration: float = 8.0,
    **kwargs,
) -> str:
    ss = build_strokes(
        image_path,
        max_side=kwargs.pop("max_side", 1100),
        mode=kwargs.pop("ink_mode", "auto"),
        threshold=kwargs.pop("threshold", None),
        invert=kwargs.pop("invert", False),
        simplify_eps=kwargs.pop("simplify_eps", 1.1),
        crop=kwargs.pop("crop", True),
        blob_thickness=kwargs.pop("blob_thickness", 6),
    )
    print(f"[render] 笔画 {len(ss.strokes)} 条，总弧长 {ss.total_length:.0f}px")
    return render_video(ss, out_path, duration=duration, **kwargs)


MOTIONS = ("in", "out", "pan-lr", "pan-ud", "breathe", "auto")


def make_motion(
    image_path: str,
    out_path: str,
    duration: float = 8.0,
    **kwargs,
) -> str:
    """静态图 + 镜头运动（Ken Burns）。不矢量化，原图的渐变与柔光一个像素都不改。

    逐笔管线按色块涂色，会把半写实厚涂的柔和渐变压成硬边色块；
    镜头运动则完全绕开上色，只推拉摇，质感 100% 保留。
    代价是没有「一笔一笔画出来」的过程感。

    motion：in 推近 / out 拉远 / pan-lr 左右平移 / pan-ud 上下平移 / auto
    """
    exe = find_ffmpeg()
    if not exe:
        raise RuntimeError("找不到 ffmpeg，无法生成镜头运动视频")

    out_w, out_h = kwargs.pop("size", (720, 1280))
    fps = int(kwargs.pop("fps", 30))
    kind = str(kwargs.pop("motion", "in") or "in").lower()
    zoom_max = float(kwargs.pop("zoom", 1.12))
    hold = float(kwargs.pop("hold", 0.0))
    seed = int(kwargs.pop("seed", 0) or 0)

    if kind not in MOTIONS:
        raise ValueError(f"未知的 motion：{kind}（可用：{', '.join(MOTIONS)}）")
    if kind == "auto":
        kind = ("in", "out", "pan-lr", "pan-ud")[seed % 4]

    move_frames = max(1, int(round((duration - max(0.0, hold)) * fps)))
    # 先放大 2 倍再 zoompan：zoompan 每帧都要重采样，输入太小会看出像素抖动
    big_w, big_h = out_w * 2, out_h * 2
    d = max(0.02, zoom_max - 1.0)

    if kind == "in":
        z = f"min(1+{d / move_frames:.8f}*on,{zoom_max})"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    elif kind == "out":
        z = f"max({zoom_max}-{d / move_frames:.8f}*on,1.0)"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    elif kind == "breathe":
        # 呼吸：一个缓慢的正弦缩放循环，幅度很小，像画面在轻轻起伏
        period = max(12, int(round(fps * 3.2)))
        amp = max(0.004, d / 2)
        z = f"1+{amp:.6f}*(1-cos(2*PI*on/{period}))/2"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    elif kind == "pan-lr":
        z = "1.06"
        x = f"min((iw-iw/zoom)*on/{move_frames},iw-iw/zoom)"
        y = "ih/2-(ih/zoom/2)"
    else:
        z = "1.06"
        x = "iw/2-(iw/zoom/2)"
        y = f"min((ih-ih/zoom)*on/{move_frames},ih-ih/zoom)"

    vf = (
        f"scale={big_w}:{big_h}:force_original_aspect_ratio=increase,"
        f"crop={big_w}:{big_h},"
        f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={out_w}x{out_h}:fps={fps},"
        f"format=yuv420p"
    )
    cmd = [
        exe, "-y", "-loop", "1", "-i", image_path,
        "-vf", vf, "-t", f"{duration:.3f}",
        "-r", str(fps), "-c:v", "libx264", "-preset", "medium",
        "-pix_fmt", "yuv420p", out_path,
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg 生成镜头运动失败：{(p.stderr or '').strip()[-400:]}")
    print(f"[motion] {kind}｜{duration:.1f}s｜{out_w}x{out_h}", flush=True)
    return out_path


def attach_motion(
    clip_path: str,
    out_path: str,
    after_duration: float = 2.0,
    motion: str = "in",
    zoom: float = 1.06,
    size=(720, 1280),
    fps: int = 30,
) -> str:
    """在逐笔视频后面接一段镜头运动：画完不要立刻定格，让画面继续活着。

    以逐笔视频的**最后一帧**为起点做推拉，所以衔接是无缝的——
    观众看到的是「画完 → 画面缓缓推近」，而不是两段硬拼。
    """
    exe = find_ffmpeg()
    if not exe:
        raise RuntimeError("找不到 ffmpeg，无法追加镜头运动")

    tmpdir = tempfile.mkdtemp(prefix="aftermotion_")
    try:
        last = os.path.join(tmpdir, "last.png")
        p = subprocess.run(
            [exe, "-y", "-sseof", "-0.1", "-i", clip_path,
             "-frames:v", "1", last, "-loglevel", "error"],
            capture_output=True, text=True, errors="replace",
        )
        if p.returncode != 0 or not os.path.isfile(last):
            raise RuntimeError(f"抽取逐笔视频末帧失败：{(p.stderr or '').strip()[-300:]}")

        tail = os.path.join(tmpdir, "tail.mp4")
        make_motion(last, tail, duration=after_duration, size=size, fps=fps,
                    motion=motion, zoom=zoom, hold=0.0)

        lst = os.path.join(tmpdir, "list.txt")
        with open(lst, "w", encoding="utf-8") as f:
            for seg in (clip_path, tail):
                f.write("file '" + os.path.abspath(seg).replace("\\", "/") + "'\n")

        cmd = [
            exe, "-y", "-f", "concat", "-safe", "0", "-i", lst,
            "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
            "-r", str(fps), out_path,
        ]
        p = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
        if p.returncode != 0:
            raise RuntimeError(f"拼接镜头运动失败：{(p.stderr or '').strip()[-400:]}")
        print(f"[motion] 绘制完成后接 {motion} {after_duration:.1f}s", flush=True)
        return out_path
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="图片 → 逐笔手绘视频")
    ap.add_argument("image")
    ap.add_argument("-o", "--out", default="handdraw.mp4")
    ap.add_argument("--duration", type=float, default=8.0)
    ap.add_argument("--size", default="1280x720")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--width", type=int, default=3)
    ap.add_argument("--ink-mode", default="auto", choices=["auto", "dark", "edge"])
    a = ap.parse_args()
    w, h = (int(v) for v in a.size.lower().split("x"))
    print(make_video(a.image, a.out, duration=a.duration, size=(w, h),
                     fps=a.fps, line_width=a.width, ink_mode=a.ink_mode))
