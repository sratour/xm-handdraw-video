"""
paint.py — 兼容层。

上色策略已经拆到 `painters/` 包（scan / importance / object 各一个模块），
这里保留原有的函数名，老代码 `from paint import build_paint_paths` 继续可用。

新代码请直接用 `painters`：
    from painters import build_paint_paths, names
"""

from __future__ import annotations

from painters import (REGISTRY, Painter, PaintContext, background_mask,
                      build_paint_paths, dilate3, get, label_components, names,
                      prepare, register, scanline_paths)
from painters.importance import _tile_importance
from painters.object import _cluster_objects, _component_paths

__all__ = [
    "REGISTRY", "Painter", "PaintContext", "background_mask",
    "build_paint_paths", "dilate3", "get", "label_components", "names",
    "prepare", "register", "scanline_paths",
    "_cluster_objects", "_component_paths", "_tile_importance",
]
