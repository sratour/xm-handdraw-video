"""按格子重要性涂：给每个网格打分，分数高的先涂。

分数 = 饱和度 0.50 + 墨线密度 0.13 + 中心度 0.37。

这是早期版本的行为。分数接近的格子会交错上色，同一辆车可能涂一半就去涂别处，
看着像笔在东跳西跳 —— 现在只作对照，正常出片用 object。
"""

from __future__ import annotations

import numpy as np

from .base import Painter, PaintContext, prepare, register, scanline_paths


def _tile_importance(rgb: np.ndarray, ink_mask: np.ndarray,
                     tx: int, ty: int, tw: int, th: int,
                     W: int, H: int) -> float:
    """一个格子的「重要性」——只用于 order="importance"。

    三项加权，权重是试出来的：

    - 颜色饱和度（0.50）：主体（车漆、皮肤、衣服）饱和度高，
      墙面、路面、天空都很灰。但**背景里的绿树饱和度也高**，
      光靠饱和度会让树排到人物前面，所以要配合中心度。
    - 墨线密度（0.13）：细节多的区域分高，但**线条多不等于主体**——
      路灯杆、栏杆、窗格同样线条密。权重给大了会把路灯杆排到前面去。
    - 中心度（0.37）：主体通常居中。这一项也是压住「边缘绿植」的关键。
    """
    sub = rgb[ty:ty + th, tx:tx + tw].astype(np.float32)
    mx = sub.max(axis=2)
    mn = sub.min(axis=2)
    sat = float(((mx - mn) / np.maximum(mx, 1)).mean())

    dens = float(ink_mask[ty:ty + th, tx:tx + tw].mean()) * 4.0

    cx, cy = tx + tw / 2, ty + th / 2
    dist = float(np.hypot(cx - W / 2, cy - H / 2) / np.hypot(W / 2, H / 2))
    center = 1.0 - min(1.0, dist)

    return 0.50 * sat + 0.13 * min(dens, 1.0) + 0.37 * center


def build(ctx: PaintContext) -> list[tuple[np.ndarray, float]]:
    h, w = ctx.ink_mask.shape
    fillable, _ = prepare(ctx)
    paths: list[tuple[np.ndarray, float]] = []
    scores: dict[tuple[int, int], float] = {}
    region_id = 0
    for ty in range(0, h, ctx.tile):
        for tx in range(0, w, ctx.tile):
            sub = fillable[ty:min(ty + ctx.tile, h), tx:min(tx + ctx.tile, w)]
            if sub.sum() < ctx.min_pixels:
                continue
            region_id += 1
            offset = np.array([tx, ty], dtype=np.float64)
            for pts, radius in scanline_paths(sub, radius=ctx.brush_radius,
                                              region_id=region_id):
                paths.append((pts + offset, radius))
            if ctx.rgb is not None:
                tw, th = min(ctx.tile, w - tx), min(ctx.tile, h - ty)
                scores[(tx, ty)] = _tile_importance(
                    ctx.rgb, ctx.ink_mask, tx, ty, tw, th, w, h)

    if scores:
        def _rank(item: tuple[np.ndarray, float]) -> float:
            pts = item[0]
            cx = int(pts[:, 0].mean() // ctx.tile) * ctx.tile
            cy = int(pts[:, 1].mean() // ctx.tile) * ctx.tile
            return -scores.get((cx, cy), 0.0)

        paths.sort(key=_rank)
    return paths


register(Painter(
    name="importance",
    label="按格打分",
    desc="按网格饱和度打分排序，同一物体会被切碎、与其他物体交错上色",
    build=build,
))
