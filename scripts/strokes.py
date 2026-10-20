"""
strokes.py — 把一张静态图变成「可逐笔绘制的笔画序列」

这是手绘视频的核心：不是用视频模型，而是
    图片 → 墨线掩膜 → 骨架化(1px 中心线) → 追踪成折线 → 排序
之后由 render.py 按累积弧长逐帧推进笔尖。

无 OpenCV 依赖，只用 numpy + Pillow。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np
from PIL import Image

Point = tuple[float, float]


# ---------------------------------------------------------------- 图像 → 墨线掩膜

def _sobel(gray: np.ndarray) -> np.ndarray:
    """梯度幅值，用来抓「轮廓」而不是「深色块」。"""
    g = gray.astype(np.float32)
    kx = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=np.float32)
    ky = kx.T

    def conv(a: np.ndarray, k: np.ndarray) -> np.ndarray:
        ph, pw = a.shape[0] + 2, a.shape[1] + 2
        p = np.zeros((ph, pw), dtype=np.float32)
        p[1:-1, 1:-1] = a
        out = np.zeros_like(a)
        for dy in range(3):
            for dx in range(3):
                out += k[dy, dx] * p[dy:dy + a.shape[0], dx:dx + a.shape[1]]
        return out

    gx, gy = conv(g, kx), conv(g, ky)
    return np.sqrt(gx * gx + gy * gy)


def _otsu(values: np.ndarray) -> float:
    """Otsu 阈值（只在 0-255 的样本上算）。"""
    hist, _ = np.histogram(values, bins=256, range=(0, 256))
    total = hist.sum()
    if total == 0:
        return 128.0
    idx = np.arange(256, dtype=np.float64)
    w0 = np.cumsum(hist).astype(np.float64)
    w1 = total - w0
    s0 = np.cumsum(hist * idx)
    s1 = s0[-1] - s0
    with np.errstate(divide="ignore", invalid="ignore"):
        m0 = np.where(w0 > 0, s0 / np.maximum(w0, 1), 0.0)
        m1 = np.where(w1 > 0, s1 / np.maximum(w1, 1), 0.0)
        between = w0 * w1 * (m0 - m1) ** 2
    between[~np.isfinite(between)] = -1.0
    return float(np.argmax(between))


def load_rgb(image_path: str, max_side: int = 1100) -> np.ndarray:
    """读图 → 合成透明底 → 等比缩放到 max_side → RGB uint8 数组。"""
    im = Image.open(image_path)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im).convert("RGB")
    else:
        im = im.convert("RGB")

    w, h = im.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    return np.asarray(im, dtype=np.uint8)


def mask_from_rgb(
    rgb: np.ndarray,
    mode: str = "auto",
    threshold: int | None = None,
    invert: bool = False,
    border: int = 3,
    blob_thickness: int = 6,
) -> tuple[np.ndarray, float]:
    """
    RGB 数组 → 墨线掩膜。返回 (mask, threshold)。

    mode:
      dark — 阈值法抓深色像素（适合线稿、黑白图）
      edge — Sobel 梯度抓轮廓（适合彩色插画）
      auto — 先判断图是否接近线稿，再决定用哪种
    """
    gray = np.asarray(Image.fromarray(rgb).convert("L"), dtype=np.uint8)
    gh, gw = gray.shape

    if mode == "auto":
        # 默认走 dark：只取真正的深色墨线，实心块交给 strip_blobs 挖空。
        #
        # 不用 edge 的原因是它会把「色块边缘、反光、纹理」都当成线条。实测一张
        # 带夜景路面的彩色插画在 edge 下描出 3181 条笔画（正常是 100~300 条），
        # 人物脸上糊满黑斑、背景斑驳。颜色由上色通道负责，描线只需要墨线。
        #
        # 唯一的例外：整张图几乎没有深色像素（浅色水彩、淡彩插画），
        # 说明它压根没有墨线，这时才退回 edge 去抓轮廓。
        probe = min(max(_otsu(gray.ravel()), 60.0), 235.0)
        if float((gray < probe).mean()) < 0.004:
            mode = "edge"
        else:
            mode = "dark"

    if mode == "dark":
        thr = float(threshold) if threshold is not None else _otsu(gray.ravel())
        thr = min(max(thr, 60.0), 235.0)
        mask = gray < thr
    else:
        grad = _sobel(gray)
        thr = float(threshold) if threshold is not None else max(24.0, float(np.percentile(grad, 92)))
        mask = grad > thr

    if invert:
        mask = ~mask

    # 抹掉图片自身的外框线：edge 模式会把画布边缘也当成轮廓描出来
    if border > 0:
        mask[:border, :] = False
        mask[-border:, :] = False
        mask[:, :border] = False
        mask[:, -border:] = False

    # 去掉孤立噪点：某像素的 8 邻域里至少要有 1 个同伴
    padded = np.zeros((gh + 2, gw + 2), dtype=bool)
    padded[1:-1, 1:-1] = mask
    nb = np.zeros_like(mask, dtype=np.int16)
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            if dy == 1 and dx == 1:
                continue
            nb += padded[dy:dy + gh, dx:dx + gw]
    mask = mask & (nb > 0)

    # 挖空实心色块内部（黑色大衣、纯黑头发这类），否则细化后糊成一团
    mask = strip_blobs(mask, blob_thickness)

    return mask, thr


def load_ink_mask(
    image_path: str,
    max_side: int = 1100,
    mode: str = "auto",
    threshold: int | None = None,
    invert: bool = False,
    border: int = 3,
    blob_thickness: int = 6,
) -> tuple[np.ndarray, tuple[int, int], float]:
    """
    返回 (mask, (w,h), threshold)。
    mask 为 bool，True 表示「需要画的墨」。

    blob_thickness:
      实心色块挖空厚度。大面积纯黑会被细化成一团乱骨架，
      这个值控制挖多深，见 strip_blobs。设 0 关闭。
    """
    rgb = load_rgb(image_path, max_side)
    mask, thr = mask_from_rgb(rgb, mode=mode, threshold=threshold, invert=invert,
                              border=border, blob_thickness=blob_thickness)
    return mask, (rgb.shape[1], rgb.shape[0]), thr


def load_context(
    image_path: str,
    max_side: int = 1100,
    mode: str = "auto",
    threshold: int | None = None,
    invert: bool = False,
    border: int = 3,
    blob_thickness: int = 6,
) -> tuple[np.ndarray, np.ndarray, tuple[int, int]]:
    """
    返回 (rgb, mask, (w,h))，三者同一坐标系。

    描线（骨架化）和上色（马克笔扫描）必须共用这套坐标，
    否则上色会涂到错位置。
    """
    rgb = load_rgb(image_path, max_side)
    mask, _thr = mask_from_rgb(rgb, mode=mode, threshold=threshold, invert=invert,
                               border=border, blob_thickness=blob_thickness)
    return rgb, mask, (rgb.shape[1], rgb.shape[0])


def _erode3(mask: np.ndarray) -> np.ndarray:
    """3×3 腐蚀：8 邻域全为前景时才保留。"""
    gh, gw = mask.shape
    p = np.zeros((gh + 2, gw + 2), dtype=bool)
    p[1:-1, 1:-1] = mask
    out = np.ones_like(mask, dtype=bool)
    for dy in range(3):
        for dx in range(3):
            out &= p[dy:dy + gh, dx:dx + gw]
    return out


def strip_blobs(mask: np.ndarray, max_thickness: int = 6) -> np.ndarray:
    """
    挖空实心色块的内部，只留薄的部分（线条 + 色块边缘）。

    为什么要这一步：线稿里出现大面积纯黑（例如黑色大衣、纯黑头发）时，
    dark 模式会把整块黑当成前景，细化后变成一团横七竖八的骨架——
    画面上看就是几道粗条纹把人物糊成一团。

    做法是用重复腐蚀近似距离变换：腐蚀 t 次后仍存活的像素，其到背景的距离
    大于 t，也就是「色块内部」。把这些内部像素挖掉，剩下的就是色块的外圈
    环和所有细线；再骨架化，环自然变成一条干净的轮廓线。

    对细线（宽度 <= 2t）完全没有影响——腐蚀 t 次就消失了，不会被挖。
    """
    if max_thickness <= 0:
        return mask
    core = mask.copy()
    for _ in range(max_thickness):
        core = _erode3(core)
        if not core.any():
            return mask  # 没有实心块，原样返回
    return mask & ~core


# ---------------------------------------------------------------- 骨架化（Zhang-Suen）

def _neighbours(p: np.ndarray) -> list[np.ndarray]:
    """按顺时针返回 p2..p9 八个方向的移位数组（已处理边界，不会环绕）。"""
    n = np.zeros_like(p);  n[1:, :] = p[:-1, :]          # p2 北
    ne = np.zeros_like(p); ne[1:, :-1] = p[:-1, 1:]      # p3 东北
    e = np.zeros_like(p);  e[:, :-1] = p[:, 1:]          # p4 东
    se = np.zeros_like(p); se[:-1, :-1] = p[1:, 1:]      # p5 东南
    s = np.zeros_like(p);  s[:-1, :] = p[1:, :]          # p6 南
    sw = np.zeros_like(p); sw[:-1, 1:] = p[1:, :-1]      # p7 西南
    w = np.zeros_like(p);  w[:, 1:] = p[:, :-1]          # p8 西
    nw = np.zeros_like(p); nw[1:, 1:] = p[:-1, :-1]      # p9 西北
    return [n, ne, e, se, s, sw, w, nw]


def thin(mask: np.ndarray, max_iter: int = 80) -> np.ndarray:
    """Zhang-Suen 细化，把墨线压成 1 像素宽的中心线。"""
    p = mask.copy()
    for _ in range(max_iter):
        removed_any = False
        for step in (0, 1):
            seq = _neighbours(p)
            trans = np.zeros_like(p, dtype=np.int16)
            for i in range(8):
                trans += ((~seq[i]) & seq[(i + 1) % 8]).astype(np.int16)
            count = np.zeros_like(p, dtype=np.int16)
            for a in seq:
                count += a.astype(np.int16)

            p2, p3, p4, p5, p6, p7, p8, p9 = seq
            cond = (p & (trans == 1) & (count >= 2) & (count <= 6))
            if step == 0:
                cond &= ~(p2 & p4 & p6) & ~(p4 & p6 & p8)
            else:
                cond &= ~(p2 & p4 & p8) & ~(p2 & p6 & p8)
            if cond.any():
                p = p & ~cond
                removed_any = True
        if not removed_any:
            break
    return p


# ---------------------------------------------------------------- 骨架 → 折线

def _neighbour_offsets():
    return [(dy, dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if not (dy == 0 and dx == 0)]


def trace_paths(skel: np.ndarray) -> list[list[tuple[int, int]]]:
    """
    把骨架追踪成一串折线（像素坐标，y 在前）。

    做法是「沿方向贪心走」而不是「遇交叉点就停」：8 邻域下，斜线和曲线会
    产生大量伪交叉点，按度数切会把一笔拆成上百个碎片。这里在分支处选与当前
    方向最接近的邻居继续走，因此一条连续的线能得到一条完整的长笔画。
    断开的线自然抬笔。
    """
    offs = _neighbour_offsets()
    fg = {(int(y), int(x)) for y, x in np.argwhere(skel)}
    if not fg:
        return []

    def nbrs(p):
        y, x = p
        return [(y + dy, x + dx) for dy, dx in offs if (y + dy, x + dx) in fg]

    deg = {p: len(nbrs(p)) for p in fg}
    used: set[tuple] = set()

    def edge(a, b):
        return (a, b) if a < b else (b, a)

    def walk(start, first):
        used.add(edge(start, first))
        path = [start, first]
        prev, cur = start, first
        while True:
            cands = [q for q in nbrs(cur) if edge(cur, q) not in used]
            if not cands:
                break
            if len(cands) == 1:
                nxt = cands[0]
            else:
                dy, dx = cur[0] - prev[0], cur[1] - prev[1]
                n1 = math.hypot(dy, dx) or 1.0
                nxt, best = None, -9.0
                for q in cands:
                    vy, vx = q[0] - cur[0], q[1] - cur[1]
                    n2 = math.hypot(vy, vx) or 1.0
                    s = (dy * vy + dx * vx) / (n1 * n2)
                    if s > best:
                        best, nxt = s, q
            used.add(edge(cur, nxt))
            path.append(nxt)
            prev, cur = cur, nxt
            if deg.get(cur, 0) == 1:
                break
        return path

    paths: list[list[tuple[int, int]]] = []

    # 先从端点起笔——每个线头都是自然的落笔点
    for s in sorted(p for p, d in deg.items() if d == 1):
        for n0 in nbrs(s):
            if edge(s, n0) in used:
                continue
            paths.append(walk(s, n0))

    # 再收尾：孤立闭环和残留边
    for s in sorted(fg):
        for n0 in nbrs(s):
            if edge(s, n0) in used:
                continue
            paths.append(walk(s, n0))

    return paths


def rdp(points: Sequence[Point], eps: float = 1.1) -> list[Point]:
    """Douglas-Peucker 简化，迭代实现（避免深递归）。"""
    if len(points) < 3:
        return list(points)
    keep = np.zeros(len(points), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    pts = np.asarray(points, dtype=np.float64)
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = pts[i], pts[j]
        seg = b - a
        norm = math.hypot(seg[0], seg[1])
        sub = pts[i + 1:j]
        if norm < 1e-9:
            d = np.hypot(sub[:, 0] - a[0], sub[:, 1] - a[1])
        else:
            d = np.abs(seg[0] * (a[1] - sub[:, 1]) - (a[0] - sub[:, 0]) * seg[1]) / norm
        k = int(np.argmax(d))
        if d[k] > eps:
            m = i + 1 + k
            keep[m] = True
            stack.append((i, m))
            stack.append((m, j))
    return [tuple(p) for p, k in zip(points, keep) if k]


# ---------------------------------------------------------------- 组装

@dataclass
class Stroke:
    points: list[Point]
    length: float
    order: int = 0

    def to_dict(self):
        return {"points": [[round(x, 2), round(y, 2)] for x, y in self.points],
                "length": round(self.length, 2), "order": self.order}


@dataclass
class StrokeSet:
    strokes: list[Stroke] = field(default_factory=list)
    width: int = 0
    height: int = 0
    total_length: float = 0.0
    source: str = ""
    crop_box: tuple[float, float, float, float] | None = None

    def to_dict(self):
        return {
            "source": self.source,
            "width": self.width, "height": self.height,
            "stroke_count": len(self.strokes),
            "total_length": round(self.total_length, 2),
            "crop_box": list(self.crop_box) if self.crop_box else None,
            "strokes": [s.to_dict() for s in self.strokes],
        }

    def save(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=1)


def _path_length(pts: Sequence[Point]) -> float:
    if len(pts) < 2:
        return 0.0
    a = np.asarray(pts, dtype=np.float64)
    d = np.diff(a, axis=0)
    return float(np.hypot(d[:, 0], d[:, 1]).sum())


def _crop_to_content(strokes: list[Stroke], w: int, h: int,
                     pad_ratio: float = 0.07
                     ) -> tuple[list[Stroke], int, int, tuple[float, float, float, float] | None]:
    """
    按墨迹外接框裁掉空白边，让主体填满画面。
    返回 (笔画, 新宽, 新高, 裁切框)；没裁时裁切框为 None。

    生成的插画常常留白过多（配方里就有「上方保留大片留白」这类要求），
    直接整张铺到画面里会让主体缩得很小。这里只裁真空白的部分。

    关键：裁完之后必须把外接框**撑回原图的宽高比**。否则一个竖长条主体
    （例如只取到人物轮廓、没有背景元素）会被裁成 1:2 的窄条，等比缩放
    铺进 16:9 画面时只占中间一小竖条，左右全是空白。撑回原比例后，裁切
    的效果就纯粹是「把主体放大」，构图关系与原图一致。
    """
    if not strokes:
        return strokes, w, h, None
    xs = [p[0] for s in strokes for p in s.points]
    ys = [p[1] for s in strokes for p in s.points]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    bw, bh = x1 - x0, y1 - y0
    if bw <= 1 or bh <= 1:
        return strokes, w, h, None

    pad = pad_ratio * min(bw, bh)
    x0, y0 = x0 - pad, y0 - pad
    x1, y1 = x1 + pad, y1 + pad

    # 撑回原图宽高比
    ratio = w / max(h, 1)
    nw, nh = x1 - x0, y1 - y0
    if nw / max(nh, 1e-9) < ratio:
        grow = (nh * ratio - nw) / 2.0
        x0, x1 = x0 - grow, x1 + grow
    else:
        grow = (nw / ratio - nh) / 2.0
        y0, y1 = y0 - grow, y1 + grow

    # 越界时整体平移（不能用 clamp，那会把比例又改回去）
    if x0 < 0:
        x1 -= x0
        x0 = 0.0
    if x1 > w:
        x0 -= x1 - w
        x1 = float(w)
    if y0 < 0:
        y1 -= y0
        y0 = 0.0
    if y1 > h:
        y0 -= y1 - h
        y1 = float(h)
    x0, y0 = max(0.0, x0), max(0.0, y0)
    x1, y1 = min(float(w), x1), min(float(h), y1)
    nw, nh = x1 - x0, y1 - y0

    # 裁得太狠说明内容本来就分散，宁可不动
    if nw * nh < 0.22 * w * h:
        return strokes, w, h, None

    out = [Stroke(points=[(x - x0, y - y0) for x, y in s.points],
                  length=s.length, order=s.order) for s in strokes]
    return out, int(round(nw)), int(round(nh)), (x0, y0, x1, y1)


def _strokes_from_mask(mask: np.ndarray, simplify_eps: float = 1.1,
                       min_length: float = 4.0) -> list[Stroke]:
    """墨线掩膜 → 排序好的笔画列表。"""
    skel = thin(mask)
    raw = trace_paths(skel)

    strokes: list[Stroke] = []
    for p in raw:
        pts = [(float(x), float(y)) for y, x in p]
        pts = rdp(pts, eps=simplify_eps)
        ln = _path_length(pts)
        if ln >= min_length and len(pts) >= 2:
            strokes.append(Stroke(points=pts, length=ln))

    # 每笔统一从「上左」端起笔，读起来更像手绘
    for s in strokes:
        if (s.points[-1][1], s.points[-1][0]) < (s.points[0][1], s.points[0][0]):
            s.points.reverse()

    # 从上到下、从左到右排序
    strokes.sort(key=lambda s: (round(s.points[0][1] / 28), s.points[0][0]))
    return strokes


def _assemble(strokes: list[Stroke], w: int, h: int, crop: bool,
              source: str) -> StrokeSet:
    """裁切 + 编号 + 打包成 StrokeSet。"""
    box = None
    cw, ch = w, h
    if crop:
        strokes, cw, ch, box = _crop_to_content(strokes, w, h)

    total = sum(s.length for s in strokes)
    for i, s in enumerate(strokes):
        s.order = i

    return StrokeSet(strokes=strokes, width=cw, height=ch,
                     total_length=total, source=source, crop_box=box)


def build_strokes(
    image_path: str,
    max_side: int = 1100,
    mode: str = "auto",
    threshold: int | None = None,
    invert: bool = False,
    simplify_eps: float = 1.1,
    min_length: float = 4.0,
    crop: bool = True,
    blob_thickness: int = 6,
) -> StrokeSet:
    """完整管线：图片 → 笔画序列。"""
    mask, (w, h), _thr = load_ink_mask(image_path, max_side=max_side, mode=mode,
                                       threshold=threshold, invert=invert,
                                       blob_thickness=blob_thickness)
    strokes = _strokes_from_mask(mask, simplify_eps, min_length)
    return _assemble(strokes, w, h, crop, str(image_path))


def build_context(
    image_path: str,
    max_side: int = 1100,
    mode: str = "auto",
    threshold: int | None = None,
    invert: bool = False,
    simplify_eps: float = 1.1,
    min_length: float = 4.0,
    crop: bool = True,
    blob_thickness: int = 6,
) -> tuple[np.ndarray, np.ndarray, StrokeSet]:
    """
    一次拿到描线 + 上色所需的全部素材，且**共用同一套坐标**。

    返回 (rgb, ink_mask, stroke_set)：
      rgb       — 已按裁切框裁过的原图（彩色，上色阶段直接取色用）
      ink_mask  — 同尺寸的墨线掩膜（上色时要避开线条）
      stroke_set— 笔画序列（坐标已减去裁切偏移）

    裁切偏移必须一起应用，否则上色会涂到错位置。
    """
    rgb, mask, (w, h) = load_context(image_path, max_side=max_side, mode=mode,
                                     threshold=threshold, invert=invert,
                                     blob_thickness=blob_thickness)
    strokes = _strokes_from_mask(mask, simplify_eps, min_length)
    ss = _assemble(strokes, w, h, crop, str(image_path))

    if ss.crop_box:
        x0, y0, x1, y1 = ss.crop_box
        ix0, iy0 = int(math.floor(x0)), int(math.floor(y0))
        ix1, iy1 = int(math.ceil(x1)), int(math.ceil(y1))
        rgb = np.ascontiguousarray(rgb[iy0:iy1, ix0:ix1])
        mask = np.ascontiguousarray(mask[iy0:iy1, ix0:ix1])

    return rgb, mask, ss


if __name__ == "__main__":
    import sys
    ss = build_strokes(sys.argv[1])
    print(f"画布 {ss.width}x{ss.height}｜笔画 {len(ss.strokes)} 条｜总弧长 {ss.total_length:.0f}px")
    for s in ss.strokes[:8]:
        print(f"  #{s.order:>3} 长度 {s.length:>7.1f}  起点 {s.points[0]}")
