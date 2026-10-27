"""上色策略的公共底座。

这里放三样东西：
  1. 画笔工具：`scanline_paths()` 生成马克笔扫描线，`dilate3()` 把墨线加粗
  2. 画面分析：`background_mask()` 认背景色，`label_components()` 做连通标记
  3. 策略注册表：`Painter` + `register()` + `get()`，各策略模块启动时自注册

新增一种上色方式 = 在 painters/ 下加一个模块，模块末尾 `register(...)` 即可，
命令行和分镜会自动多出这个选项。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

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


def label_components(mask: np.ndarray) -> tuple[np.ndarray, int]:
    """4-连通标记（按行游程 + 并查集），不依赖 scipy。

    墨线把画面围成一个个色块，色块就是**涂色单位**：一辆车的车身、一扇车窗、
    一面墙各是一块。逐块涂完再换下一块，笔才不会东跳西跳。

    返回 (labels, 游程数)。labels 里 0 表示不可涂，其余是「根编号 + 1」。
    """
    h, w = mask.shape
    labels = np.zeros((h, w), dtype=np.int32)
    parent: list[int] = []
    runs: list[tuple[int, int, int, int]] = []      # (y, x0, x1, 游程 id)

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    prev: list[tuple[int, int, int]] = []
    for y in range(h):
        row = mask[y].astype(np.int8)
        edges = np.flatnonzero(np.diff(np.r_[0, row, 0]))
        cur: list[tuple[int, int, int]] = []
        for x0, x1 in zip(edges[0::2], edges[1::2]):
            rid = len(parent)
            parent.append(rid)
            cur.append((int(x0), int(x1), rid))
            runs.append((y, int(x0), int(x1), rid))
        # 与上一行有重叠的游程并成一类
        i = j = 0
        while i < len(cur) and j < len(prev):
            a0, a1, aid = cur[i]
            b0, b1, bid = prev[j]
            if a0 < b1 and b0 < a1:
                union(aid, bid)
            if a1 <= b1:
                i += 1
            else:
                j += 1
        prev = cur

    for y, x0, x1, rid in runs:
        labels[y, x0:x1] = find(rid) + 1
    return labels, len(parent)


@dataclass
class PaintContext:
    """一次上色请求的全部输入。策略模块只依赖它，不碰全局状态。"""
    ink_mask: np.ndarray
    rgb: np.ndarray | None = None
    tile: int = 220
    min_pixels: int = 260
    brush_radius: float | None = None
    ink_pad: int = 2
    skip_background: bool = True
    focus: list = field(default_factory=list)


@dataclass
class Painter:
    """一种上色顺序。`build` 返回 [(路径点(N,2), 笔刷半径), ...]。"""
    name: str            # 命令行取值，如 "object"
    label: str           # 中文名，给 --help 和 painters 子命令用
    desc: str            # 一句话说明
    build: Callable[[PaintContext], list]


REGISTRY: dict[str, Painter] = {}
# 别名表：label -> name。注册时自动建，让用户既能敲英文也能敲中文。
ALIASES: dict[str, str] = {}


def register(painter: Painter) -> Painter:
    REGISTRY[painter.name] = painter
    if painter.label and painter.label != painter.name:
        ALIASES[painter.label] = painter.name
    return painter


def names() -> list[str]:
    """只返回英文主名（向后兼容）。"""
    return list(REGISTRY)


def choices() -> list[str]:
    """命令行 --paint-order 的可选值：英文主名 + 中文别名都收。"""
    return sorted(list(REGISTRY) + list(ALIASES))


def resolve(name: str) -> str | None:
    """把用户输入（可能是英文主名，也可能是中文别名）归一成主名。"""
    if name in REGISTRY:
        return name
    if name in ALIASES:
        return ALIASES[name]
    return None


def get(name: str) -> Painter:
    """按名字或别名取策略；都不认识时退回 object 并提示，不直接崩。"""
    key = resolve(name)
    if key is not None:
        return REGISTRY[key]
    print(f"[paint] 未知的上色方式 {name!r}，改用 object")
    return REGISTRY["object"]


def prepare(ctx: PaintContext) -> tuple[np.ndarray, np.ndarray | None]:
    """算出「可涂区域」和背景掩膜。

    背景掩膜有两个用途：`skip_background` 时跳过不涂，排序时压到最后。
    开了 --paint-bg 也要算 —— 纸面和天空必须最后才上色。
    """
    fillable = ~dilate3(ctx.ink_mask, ctx.ink_pad)
    bg = background_mask(ctx.rgb) if ctx.rgb is not None else None
    if ctx.skip_background and bg is not None:
        fillable &= ~bg
    return fillable, bg
