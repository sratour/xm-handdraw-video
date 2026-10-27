"""逐行扫描：按网格从上到下、从左到右涂。

跟画面内容无关，主体在画面下半部时必然最后才上色。
留作对照和兜底，正常出片不用它。
"""

from __future__ import annotations

import numpy as np

from .base import Painter, PaintContext, prepare, register, scanline_paths


def build(ctx: PaintContext) -> list[tuple[np.ndarray, float]]:
    h, w = ctx.ink_mask.shape
    fillable, _ = prepare(ctx)
    paths: list[tuple[np.ndarray, float]] = []
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
    return paths


register(Painter(
    name="scan",
    label="逐行扫描",
    desc="按网格从上到下涂，跟画面内容无关，主体可能最后才上色",
    build=build,
))
