"""上色策略包。

每种「先涂谁」的做法是一个独立模块，实现 `Painter` 接口并自注册：

    painters/scan.py        逐行扫描（对照用）
    painters/importance.py  按格打分（早期行为，对照用）
    painters/object.py      按物体涂（默认）

三种开关（主名与中文别名都认：`object`/按物体涂、`importance`/按格打分、
`scan`/逐行扫描）：

    CLI     --paint-order {object,importance,scan}   也可写中文别名
    分镜    "paint_order": "scan"   单场覆盖，其他场次不受影响，也可写中文别名
    代码    painters.build_paint_paths(..., order="scan")   也可写中文别名

新增一种方式：在 painters/ 下新建模块，末尾 `register(Painter(...))`，
再把模块名加进本文件底部的 import 列表，命令行和分镜会自动多出这个取值。
"""

from __future__ import annotations

import numpy as np

from .base import (ALIASES, REGISTRY, Painter, PaintContext, background_mask,
                   choices, dilate3, get, label_components, names, prepare,
                   register, resolve, scanline_paths)

__all__ = [
    "ALIASES", "REGISTRY", "Painter", "PaintContext", "background_mask",
    "build_paint_paths", "choices", "dilate3", "get", "label_components",
    "names", "prepare", "register", "resolve", "scanline_paths",
]


def build_paint_paths(
    ink_mask: np.ndarray,
    rgb: np.ndarray | None = None,
    tile: int = 220,
    min_pixels: int = 260,
    brush_radius: float | None = None,
    ink_pad: int = 2,
    skip_background: bool = True,
    order: str = "object",
    focus: list | None = None,
) -> list[tuple[np.ndarray, float]]:
    """统一入口：按 order 选一种上色策略，返回涂抹路径。

    参数含义见 `PaintContext`；`order` 不认识时会退回 object 并提示。
    """
    ctx = PaintContext(
        ink_mask=ink_mask, rgb=rgb, tile=tile, min_pixels=min_pixels,
        brush_radius=brush_radius, ink_pad=ink_pad,
        skip_background=skip_background, focus=list(focus or []),
    )
    return get(order).build(ctx)


# 导入即注册。顺序决定 --help 里 choices 的排列。
from . import scan        # noqa: E402,F401
from . import importance  # noqa: E402,F401
from . import object as _object  # noqa: E402,F401
