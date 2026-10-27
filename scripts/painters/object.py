"""按物体涂：一个物体整块涂完再换下一个。

四步：
  1. 墨线围出的色块各自成块（`label_components`）
  2. 挨得近的色块并成物体（`_cluster_objects`）—— 车身+车窗+轮子 = 一辆车
  3. 排顺序：`focus` 给的框（按口播顺序）优先 → 重要性 → 纸面垫底
  4. 物体内部按「就近」逐块涂，笔不空跑

这是默认策略，也是目前成片效果最好的一个。
"""

from __future__ import annotations

import math

import numpy as np

from .base import (Painter, PaintContext, label_components, prepare, register,
                   scanline_paths)


def _cluster_objects(ids: list[int], x0, y0, x1, y1, area,
                     w: int, h: int, gap_tol: float = 14.0,
                     max_share: float = 0.32) -> dict[int, list[int]]:
    """把挨在一起的色块并成「物体」。

    一辆车由车身、车窗、轮子几个色块组成，彼此只隔着一条墨线；
    一个人由西装、衬衫、皮肤、头发组成，同样挨得很近。
    包围盒间隙 ≤ gap_tol 就并成一体，但合并后的外框不许超过画面 max_share
    —— 否则整幅背景会被并成一坨。

    返回 {根编号: [成员色块, ...]}。
    """
    parent = {i: i for i in ids}

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    cap = max_share * w * h
    for ai in range(len(ids)):
        a = ids[ai]
        for bi in range(ai + 1, len(ids)):
            b = ids[bi]
            if find(a) == find(b):
                continue
            dx = max(0, max(x0[a], x0[b]) - min(x1[a], x1[b]))
            dy = max(0, max(y0[a], y0[b]) - min(y1[a], y1[b]))
            if math.hypot(dx, dy) > gap_tol:
                continue
            ga = [i for i in ids if find(i) == find(a)]
            gb = [i for i in ids if find(i) == find(b)]
            ux0 = min(min(x0[i] for i in ga), min(x0[i] for i in gb))
            uy0 = min(min(y0[i] for i in ga), min(y0[i] for i in gb))
            ux1 = max(max(x1[i] for i in ga), max(x1[i] for i in gb))
            uy1 = max(max(y1[i] for i in ga), max(y1[i] for i in gb))
            if (ux1 - ux0) * (uy1 - uy0) > cap:
                continue
            union(a, b)

    groups: dict[int, list[int]] = {}
    for i in ids:
        groups.setdefault(find(i), []).append(i)
    return groups


def _component_paths(
    rgb: np.ndarray,
    fillable: np.ndarray,
    bg: np.ndarray | None,
    min_pixels: int,
    brush_radius: float | None,
    focus: list | None = None,
) -> list[tuple[np.ndarray, float]]:
    """按「物体」生成涂抹路径：一个物体整块涂完再涂下一个。

    步骤：
      1. 墨线围出的色块（`_label_components`）
      2. 挨得近的色块并成物体（`_cluster_objects`）—— 车身+车窗+轮子 = 一辆车
      3. 排顺序：`focus` 里给的框（按口播顺序）优先，其余按重要性
         （饱和度 0.50 + 中心度 0.30 + 面积 0.20 − 背景占比 × 1.5），
         纸面、天空永远最后
      4. 物体内部再按就近顺序逐块涂，笔不空跑
    """
    h, w = fillable.shape
    labels, n = label_components(fillable)
    if n == 0:
        return []

    ys, xs = np.nonzero(labels)
    lab = labels[ys, xs]

    area = np.bincount(lab, minlength=n + 1).astype(np.float64)
    x0 = np.full(n + 1, w, dtype=np.int64)
    x1 = np.zeros(n + 1, dtype=np.int64)
    y0 = np.full(n + 1, h, dtype=np.int64)
    y1 = np.zeros(n + 1, dtype=np.int64)
    np.minimum.at(x0, lab, xs)
    np.maximum.at(x1, lab, xs)
    np.minimum.at(y0, lab, ys)
    np.maximum.at(y1, lab, ys)

    px = rgb[ys, xs].astype(np.float32)
    cnt = np.maximum(area, 1.0)
    sat_px = (px.max(axis=1) - px.min(axis=1)) / np.maximum(px.max(axis=1), 1.0)
    sat = np.bincount(lab, weights=sat_px, minlength=n + 1) / cnt
    bgfrac = (np.bincount(lab, weights=bg[ys, xs].astype(np.float32), minlength=n + 1) / cnt
              if bg is not None else np.zeros(n + 1))
    cx = np.bincount(lab, weights=xs, minlength=n + 1) / cnt
    cy = np.bincount(lab, weights=ys, minlength=n + 1) / cnt

    ids = [i for i in range(1, n + 1) if area[i] >= min_pixels]
    if not ids:
        return []

    # ② 并成物体
    groups = _cluster_objects(ids, x0, y0, x1, y1, area, w, h)

    # ③ 焦点框优先（按口播顺序），纸面最后。
    # 先按**单个色块**的中心点判断落在哪个框里，再按 (簇, 优先级) 重新分组 ——
    # 这样「脸」即使和背后的楼房并成了一簇，也会被焦点框拆出来跟人物一起涂，
    # 而不是拖着楼房一起等到最后。
    comp_prio: dict[int, int] = {}
    for k, fb in enumerate(focus or []):
        try:
            fx0, fy0, fx1, fy1 = [float(v) for v in fb]
        except (TypeError, ValueError):
            continue
        fx0, fx1 = sorted((fx0 * w, fx1 * w))
        fy0, fy1 = sorted((fy0 * h, fy1 * h))
        for i in ids:
            if fx0 <= cx[i] <= fx1 and fy0 <= cy[i] <= fy1:
                comp_prio.setdefault(i, k)

    split: dict[tuple[int, int], list[int]] = {}
    for root, mem in groups.items():
        for i in mem:
            split.setdefault((root, comp_prio.get(i, 99)), []).append(i)
    groups = split

    big = max(1.0, float(area.max()))
    boxes = []
    for root, mem in groups.items():
        a = sum(area[i] for i in mem)
        bx0 = min(x0[i] for i in mem); bx1 = max(x1[i] for i in mem)
        by0 = min(y0[i] for i in mem); by1 = max(y1[i] for i in mem)
        mx = sum(cx[i] * area[i] for i in mem) / max(a, 1)
        my = sum(cy[i] * area[i] for i in mem) / max(a, 1)
        s = sum(sat[i] * area[i] for i in mem) / max(a, 1)
        bf = sum(bgfrac[i] * area[i] for i in mem) / max(a, 1)
        dist = math.hypot(mx - w / 2, my - h / 2) / math.hypot(w / 2, h / 2)
        center = 1.0 - min(1.0, dist)
        size = min(1.0, math.log(max(a, 1.0) / min_pixels) /
                   math.log(max(2.0, big / min_pixels)))
        score = 0.50 * s + 0.30 * center + 0.20 * size - 1.5 * bf
        boxes.append(dict(root=root, mem=mem, area=a, score=score, bgfrac=bf,
                          box=(bx0, by0, bx1, by1), cx=mx, cy=my))

    # ④ 排顺序：焦点框优先级 → 重要性 → 纸面垫底
    prio = {key: key[1] for key in groups}

    def rank(b) -> tuple[int, int, float]:
        # 背景色判定会把「肤色、米色衣服」误判成纸面（量化后同色），
        # 所以被焦点框点名的物体不吃这个惩罚，否则脸会等到最后才涂。
        p = prio.get(b["root"], 99)
        is_paper = b["bgfrac"] >= 0.5 and p == 99
        return (1 if is_paper else 0, p, -b["score"])

    boxes.sort(key=rank)

    # ④ 物体内部就近排序，逐块涂
    def chain(mem: list[int]) -> list[int]:
        rest = list(mem)
        rest.sort(key=lambda i: -area[i])
        seq = [rest.pop(0)]
        while rest:
            cur = seq[-1]
            def g(i: int) -> float:
                dx = max(0, max(x0[cur], x0[i]) - min(x1[cur], x1[i]))
                dy = max(0, max(y0[cur], y0[i]) - min(y1[cur], y1[i]))
                return math.hypot(dx, dy)
            nxt = min(rest, key=g)
            rest.remove(nxt)
            seq.append(nxt)
        return seq

    out: list[tuple[np.ndarray, float]] = []
    for b in boxes:
        for i in chain(b["mem"]):
            ax0, ax1 = int(x0[i]), int(x1[i]) + 1
            ay0, ay1 = int(y0[i]), int(y1[i]) + 1
            sub = labels[ay0:ay1, ax0:ax1] == i
            offset = np.array([ax0, ay0], dtype=np.float64)
            for pts, radius in scanline_paths(sub, radius=brush_radius, region_id=i):
                out.append((pts + offset, radius))
    return out


def build(ctx: PaintContext) -> list[tuple[np.ndarray, float]]:
    if ctx.rgb is None:                      # 没有彩色原图就退回逐行扫描
        from .scan import build as scan_build
        return scan_build(ctx)
    fillable, bg = prepare(ctx)
    return _component_paths(ctx.rgb, fillable, bg, ctx.min_pixels,
                            ctx.brush_radius, ctx.focus)


register(Painter(
    name="object",
    label="按物体涂",
    desc="色块并成物体，一个涂完再下一个；focus 框按口播顺序优先，纸面最后",
    build=build,
))
