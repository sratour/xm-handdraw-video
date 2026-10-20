"""
paint.py — 从原图提取「马克笔涂色路径」

手绘动画的上色阶段：不是逐像素填充，而是让笔尖在色块上来回扫，
像用马克笔把一块区域涂满。

思路参考 sweety-handdraw-video 的 paint_motion.py：
对一个区域做 PCA 求主轴，沿垂直于主轴的方向按行扫描，蛇形折返，
行距约等于笔刷直径，路径上加一点正弦弯曲模拟手写抖动。

与它不同的两点：
  1. 它按调色板分量 + 连通域分组（依赖 scipy.ndimage.label / binary_closing）。
     这里改成把画面切成网格，每格独立扫描 —— 只依赖 numpy，且分散的色块
     天然被分开处理，省掉了连通域标记。
  2. 它把颜色分层后再涂（每层一个纯色）。这里不分层，直接让笔尖扫过，
     颜色在渲染时逐像素取自原图，所以笔刷扫过时颜色是自然过渡的。
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image


def dilate3(mask: np.ndarray, iterations: int = 1) -> np.ndarray:
    """3×3 膨胀。用来把墨线加粗一圈，涂色时避开它。"""
    out = mask
    for _ in range(iterations):
        gh, gw = out.shape
        p = np.zeros((gh + 2, gw + 2), dtype=bool)
        p[1:-1, 1:-1] = out
        grown = np.zeros_like(out, dtype=bool)
        for dy in range(3):
            for dx in range(3):
                grown |= p[dy:dy + gh, dx:dx + gw]
        out = grown
    return out


def scanline_paths(
    mask: np.ndarray,
    radius: float | None = None,
    region_id: int = 0,
    bend: float = 1.5,
) -> list[tuple[np.ndarray, float]]:
    """
    对一块区域生成马克笔扫描线，返回 [(路径点(N,2), 笔刷半径), ...]。

    路径点坐标与 mask 同一坐标系（x 在前）。
    """
    ys, xs = np.where(mask)
    n = len(xs)
    if n == 0:
        return []

    pts = np.c_[xs, ys].astype(np.float64)
    center = pts.mean(axis=0)

    # 太小的一块，一笔点掉
    if n < 50:
        p = pts[int(np.argmin(np.linalg.norm(pts - center, axis=1)))]
        return [(np.array([p, p + [0.3, 0.2]]), 10.0)]

    if radius is None:
        radius = float(np.clip(math.sqrt(n) / 7.0, 10.0, 20.0))

    # PCA 主轴：笔触沿着物体的长边走
    cov = np.cov(pts.T)
    _evals, evecs = np.linalg.eigh(cov)
    axis = evecs[:, -1]
    # 每块给一点角度扰动，避免所有笔触方向整齐划一
    angle = math.atan2(axis[1], axis[0]) + 0.16 * math.sin(region_id * 1.71)
    direction = np.array([math.cos(angle), math.sin(angle)])
    across = np.array([-direction[1], direction[0]])

    along_v = (pts - center) @ direction
    across_v = (pts - center) @ across

    lower, upper = float(across_v.min()), float(across_v.max())
    span = upper - lower
    rows = max(1, math.ceil(span / (radius * 1.35)))
    inset = min(radius * 0.42, span / 2.0)
    offsets = np.linspace(lower + inset, upper - inset, rows)

    out: list[tuple[np.ndarray, float]] = []
    for row_index, offset in enumerate(offsets):
        band = np.abs(across_v - offset) <= radius * 0.86
        if not band.any():
            continue
        projected = np.sort(np.unique(np.rint(along_v[band])))
        if not len(projected):
            continue
        # 断开的地方抬笔，分成多条
        gaps = np.where(np.diff(projected) > radius * 1.7)[0] + 1
        runs = np.split(projected, gaps)
        if row_index % 2:
            runs = runs[::-1]          # 蛇形折返，笔不用空跑回去

        for run in runs:
            first, last = float(run[0]) - 1.0, float(run[-1]) + 1.0
            count = max(2, math.ceil((last - first) / 2.5) + 1)
            along = np.linspace(first, last, count)
            if row_index % 2:
                along = along[::-1]
            curve = bend * np.sin(np.linspace(0.0, math.pi, count))
            path = (center
                    + along[:, None] * direction
                    + (offset + curve)[:, None] * across)
            out.append((path, radius))
    return out


def background_mask(rgb: np.ndarray, colors: int = 6, edge_ratio: float = 0.5) -> np.ndarray:
    """
    找出「背景色」的像素。

    判据：把原图量化成几种颜色，看画面最外一圈由哪种颜色主导。如果某个颜色
    在边缘占了半数以上，它就是背景（纸面/天空/墙）。这样不必猜，也不依赖
    具体的颜色值 —— 深色夜景图同样适用。
    """
    h, w = rgb.shape[:2]
    im = Image.fromarray(rgb).quantize(colors=colors, method=Image.Quantize.FASTOCTREE)
    pal = np.asarray(im, dtype=np.int16)

    band = max(2, min(h, w) // 40)
    edge = np.concatenate([pal[:band, :].ravel(), pal[-band:, :].ravel(),
                           pal[:, :band].ravel(), pal[:, -band:].ravel()])
    vals, counts = np.unique(edge, return_counts=True)
    top = int(vals[int(np.argmax(counts))])
    if counts.max() / max(1, edge.size) < edge_ratio:
        return np.zeros((h, w), dtype=bool)      # 边缘颜色不统一，不当背景处理
    return pal == top


def build_paint_paths(
    ink_mask: np.ndarray,
    rgb: np.ndarray | None = None,
    tile: int = 220,
    min_pixels: int = 260,
    brush_radius: float | None = None,
    ink_pad: int = 2,
    skip_background: bool = True,
) -> list[tuple[np.ndarray, float]]:
    """
    墨线掩膜 → 涂抹路径。

    把画面切成 tile×tile 的格子，每格独立做 PCA 扫描线。网格分块代替了
    连通域标记：同一格内的内容局部方向一致，扫描方向也就合理；分散的色块
    天然落在不同格里，不会互相干扰。

    skip_background 打开时会跳过背景色区域（见 background_mask），
    让涂色时间集中在主体上，画面保持「白纸 + 彩色主体」的手绘感。
    """
    h, w = ink_mask.shape
    fillable = ~dilate3(ink_mask, ink_pad)
    if skip_background and rgb is not None:
        fillable &= ~background_mask(rgb)

    paths: list[tuple[np.ndarray, float]] = []
    region_id = 0
    for ty in range(0, h, tile):
        for tx in range(0, w, tile):
            sub = fillable[ty:min(ty + tile, h), tx:min(tx + tile, w)]
            if sub.sum() < min_pixels:
                continue
            region_id += 1
            offset = np.array([tx, ty], dtype=np.float64)
            for pts, radius in scanline_paths(sub, radius=brush_radius,
                                              region_id=region_id):
                paths.append((pts + offset, radius))
    return paths
