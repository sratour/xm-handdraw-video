#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""角色库：把角色设定图切成单张立绘，并按 id 调取。

角色库放在 `assets/char/<角色id>/`：

    assets/char/office-lady/
    ├── card.md           # 人看的角色档案
    ├── manifest.json     # 机器读的索引
    ├── source-01.png     # 原始设定图
    └── poses/
        ├── 01-front-hold-papers.png
        └── ...

`--character` 既可以直接给图片路径，也可以给库里的 id：

    --character office-lady        # 该角色的默认姿态（manifest 里的 default_pose）
    --character office-lady:02     # 指定姿态，可用序号或文件名前缀

用法：
    python scripts/char_lib.py list
    python scripts/char_lib.py list --json
    python scripts/char_lib.py extract 设定图.png --id office-lady \
        --names 01-front-hold-papers,02-front-arms-crossed
"""
from __future__ import annotations

import argparse
import json
import os
import sys

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHAR_ROOT = os.path.join(SKILL_ROOT, "assets", "char")


# ---------------------------------------------------------------- 读库

def _manifest_path(char_id: str) -> str:
    return os.path.join(CHAR_ROOT, char_id, "manifest.json")


def load(char_id: str) -> dict | None:
    p = _manifest_path(char_id)
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def all_chars() -> list[dict]:
    """列出所有带 manifest 的角色。没有 manifest 的目录跳过。"""
    out = []
    if not os.path.isdir(CHAR_ROOT):
        return out
    for name in sorted(os.listdir(CHAR_ROOT)):
        m = load(name)
        if m:
            m["_dir"] = os.path.join(CHAR_ROOT, name)
            out.append(m)
    return out


def _match_pose(poses: list[dict], key: str) -> dict | None:
    """按序号、文件名或文件名前缀找姿态。"""
    key = key.strip()
    if not key:
        return None
    if key.isdigit():
        i = int(key)
        # 1-based 序号，和文件名前缀一致
        for p in poses:
            if int(p["file"].split("/")[-1][:2]) == i:
                return p
    for p in poses:
        base = os.path.basename(p["file"])
        if base == key or base == key + ".png" or base.startswith(key):
            return p
    return None


def resolve(spec: str) -> str | None:
    """把 `office-lady` / `office-lady:02` 解析成图片绝对路径。

    解析不出来返回 None，交给调用方按原路径处理。
    """
    if os.path.isfile(spec):
        return None
    cid, _, pose_key = spec.partition(":")
    cid = cid.strip()
    m = load(cid)
    if not m:
        return None
    poses = m.get("poses", [])
    if pose_key:
        pose = _match_pose(poses, pose_key)
        if pose is None:
            # 姿态写错时不要静默回落到默认姿态——成片里用错姿态比报错更难查
            print(f"[错误] 角色 {cid} 没有姿态 '{pose_key}'，可选："
                  + "、".join(os.path.basename(p["file"])[:-4] for p in poses),
                  file=sys.stderr)
            return None
    else:
        pose = None
        default = m.get("default_pose")
        if default:
            pose = _match_pose(poses, str(default))
    if pose is None:
        pose = poses[0] if poses else None
    if pose is None:
        return None
    return os.path.join(CHAR_ROOT, cid, pose["file"])


# ---------------------------------------------------------------- 切图

def _bg_color(arr):
    """用四周边缘的中位数估计纸底颜色。设定图普遍是浅色纸底。"""
    import numpy as np
    h, w = arr.shape[:2]
    k = max(4, min(h, w) // 100)
    strips = [
        arr[:k].reshape(-1, arr.shape[2]),
        arr[-k:].reshape(-1, arr.shape[2]),
        arr[:, :k].reshape(-1, arr.shape[2]),
        arr[:, -k:].reshape(-1, arr.shape[2]),
    ]
    return np.median(np.concatenate(strips), axis=0)


def split_poses(img, th: int = 28, min_gap: int = 6, min_width: int = 60):
    """按列投影把横排设定图切成若干张单独立绘。

    返回 [(x0, y0, x1, y1), ...]，坐标已含内容外接框（不含 padding）。

    `min_gap` 默认只留 6px：设定图里相邻两个姿态常常贴得很近（实测有一张
    只隔 5px），间隙给大了会把两个人并成一张。姿态内部的空隙（手臂与身体之间）
    不会造成整列无墨，所以这个值取小是安全的。
    """
    import numpy as np
    a = np.asarray(img.convert("RGB")).astype(np.float32)
    bg = _bg_color(a)
    diff = np.abs(a - bg).max(axis=2)
    ink = diff > th

    col = ink.sum(axis=0)
    thr = max(2, int(col.max() * 0.02))
    on = col > thr

    # 先取连续墨柱段，再把间隔小于 min_gap 的合并（手臂与身体之间的缝）
    runs, s = [], None
    for i, v in enumerate(on):
        if v and s is None:
            s = i
        elif not v and s is not None:
            runs.append((s, i))
            s = None
    if s is not None:
        runs.append((s, len(on)))

    merged = []
    for r in runs:
        if merged and r[0] - merged[-1][1] < min_gap:
            merged[-1] = (merged[-1][0], r[1])
        else:
            merged.append(list(r))
    merged = [(a0, b0) for a0, b0 in merged if b0 - a0 >= min_width]

    boxes = []
    for x0, x1 in merged:
        rows = ink[:, x0:x1].sum(axis=1)
        ys = np.where(rows > 0)[0]
        if len(ys) == 0:
            continue
        boxes.append((x0, int(ys.min()), x1, int(ys.max()) + 1))
    return boxes


def _drop_edge_slivers(img, th: int = 28, ratio: float = 0.02):
    """抹掉贴着左右边缘的碎块——相邻姿态漏进来的一角。

    只处理「碰到边缘」且「面积不到最大连通域的 2%」的块。角色自己的
    手臂、文件即使贴边也不会被删，因为它们跟主体连通、面积大。
    """
    import numpy as np
    from PIL import Image, ImageDraw

    a = np.asarray(img.convert("RGB")).astype(np.int16)
    h, w = a.shape[:2]
    k = max(2, min(h, w) // 50)
    corners = np.concatenate([
        a[:k, :k].reshape(-1, 3), a[:k, -k:].reshape(-1, 3),
        a[-k:, :k].reshape(-1, 3), a[-k:, -k:].reshape(-1, 3),
    ])
    bg = np.median(corners, axis=0)
    ink = np.abs(a - bg).max(axis=2) > th
    total = int(ink.sum())
    if total == 0:
        return img

    # fromarray 得到的是只读图像，floodfill 写不进去还会静默返回，必须 copy
    mask = Image.fromarray((ink * 255).astype(np.uint8), "L").copy()
    seeds = []
    for y in range(h):
        for x in (0, 1, w - 2, w - 1):
            if ink[y, x]:
                seeds.append((x, y))
    if not seeds:
        return img

    erased = 0
    for x, y in seeds:
        if mask.getpixel((x, y)) != 255:
            continue
        ImageDraw.floodfill(mask, (x, y), 128, thresh=0)
        m = np.array(mask)
        area = int((m == 128).sum())
        if area < total * ratio:
            m[m == 128] = 0
            erased += 1
        else:
            m[m == 128] = 255
        mask = Image.fromarray(m, "L").copy()

    if not erased:
        return img
    keep = np.asarray(mask) > 0
    out = np.asarray(img.convert("RGB")).copy()
    out[~keep] = bg.astype(np.uint8)
    return Image.fromarray(out, "RGB")


def extract(src: str, char_id: str, names: list[str], pad: int = 24,
            th: int = 28, alpha: bool = False, gap: int = 6,
            clean: bool = True) -> list[str]:
    """切图并写入 `assets/char/<id>/poses/`，返回写出的文件路径。"""
    from PIL import Image

    img = Image.open(src).convert("RGB")
    boxes = split_poses(img, th=th, min_gap=gap)
    if not boxes:
        raise SystemExit(f"[错误] 没在 {src} 里找到任何主体，试试调 --threshold")
    if names and len(names) != len(boxes):
        print(f"[警告] 给了 {len(names)} 个名字，但切出 {len(boxes)} 张，"
              f"按顺序套用，多余的自动编号", file=sys.stderr)

    out_dir = os.path.join(CHAR_ROOT, char_id, "poses")
    os.makedirs(out_dir, exist_ok=True)

    written = []
    for i, (x0, y0, x1, y1) in enumerate(boxes):
        # 留白不能越过相邻姿态的中缝，否则会把邻居的一角裁进来
        left_lim = (boxes[i - 1][2] + x0) // 2 if i > 0 else 0
        right_lim = ((x1 + boxes[i + 1][0]) // 2
                     if i + 1 < len(boxes) else img.width)
        X0, Y0 = max(left_lim, x0 - pad), max(0, y0 - pad)
        X1, Y1 = min(right_lim, x1 + pad), min(img.height, y1 + pad)
        crop = img.crop((X0, Y0, X1, Y1))
        if clean:
            crop = _drop_edge_slivers(crop, th=th)
        if alpha:
            crop = _to_alpha(crop, th)
        name = names[i] if names and i < len(names) else f"{i + 1:02d}-pose"
        name = name if name.endswith(".png") else name + ".png"
        dst = os.path.join(out_dir, name)
        crop.save(dst)
        written.append(dst)
        print(f"  {os.path.relpath(dst, SKILL_ROOT)}  {crop.width}x{crop.height}"
              f"  ← 源区域 x[{x0},{x1}] y[{y0},{y1}]")
    return written


def _to_alpha(img, th: int):
    """从四边泛洪填充纸底，转成透明背景。只从边缘扩散，不会挖穿衣服的白色。"""
    import numpy as np
    from PIL import Image, ImageDraw
    from collections import deque

    a = np.asarray(img).astype(np.int16)
    bg = np.median(np.concatenate([
        a[:4].reshape(-1, 3), a[-4:].reshape(-1, 3),
        a[:, :4].reshape(-1, 3), a[:, -4:].reshape(-1, 3),
    ]), axis=0)
    h, w = a.shape[:2]
    near = np.abs(a - bg).max(axis=2) <= th

    mask = Image.new("L", (w, h), 0)
    px = mask.load()
    seen = bytearray(w * h)
    dq = deque()
    for x in range(w):
        dq.append((x, 0)); dq.append((x, h - 1))
    for y in range(h):
        dq.append((0, y)); dq.append((w - 1, y))
    while dq:
        x, y = dq.popleft()
        if x < 0 or y < 0 or x >= w or y >= h:
            continue
        idx = y * w + x
        if seen[idx]:
            continue
        seen[idx] = 1
        if not near[y, x]:
            continue
        px[x, y] = 255
        dq.append((x + 1, y)); dq.append((x - 1, y))
        dq.append((x, y + 1)); dq.append((x, y - 1))

    out = img.convert("RGBA")
    out.putalpha(Image.eval(mask, lambda v: 255 - v))
    return out


# ---------------------------------------------------------------- 命令行

def cmd_extract(a):
    names = [n.strip() for n in a.names.split(",")] if a.names else []
    files = extract(a.sheet, a.id, names, pad=a.pad, th=a.threshold,
                    alpha=a.alpha, gap=a.gap, clean=not a.no_clean)
    print(f"\n[完成] {len(files)} 张 → assets/char/{a.id}/poses/")


def cmd_list(a):
    chars = all_chars()
    if a.json:
        print(json.dumps(chars, ensure_ascii=False, indent=2))
        return
    if not chars:
        print("角色库是空的。用 `char_lib.py extract` 加一个。")
        return
    for m in chars:
        print(f"\n{m.get('display_name', m['id'])}  (id: {m['id']})")
        print(f"  画风  {m.get('style', '-')}")
        if m.get("summary"):
            print(f"  简介  {m['summary']}")
        for p in m.get("poses", []):
            mark = "★" if str(m.get("default_pose")) == p["file"].split("/")[-1][:2] else " "
            print(f"  {mark} {os.path.basename(p['file']):34s} {p.get('name', '')}")
        poses = m.get("poses", [])
        if len(poses) > 1:
            second = os.path.basename(poses[1]["file"])[:2]
            print(f"  用法  --character {m['id']}   或   --character {m['id']}:{second}")
        else:
            print(f"  用法  --character {m['id']}（只有一个姿态）")


def main():
    ap = argparse.ArgumentParser(description="角色库：切图入库 / 按 id 调取")
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract", help="把横排角色设定图切成单张立绘")
    e.add_argument("sheet", help="设定图路径（横排多个姿态）")
    e.add_argument("--id", required=True, help="角色 id，如 office-lady")
    e.add_argument("--names", default="", help="逗号分隔的输出文件名（不含扩展名）")
    e.add_argument("--pad", type=int, default=24, help="裁切外留白像素，默认 24")
    e.add_argument("--threshold", type=int, default=28, help="与纸底的颜色差阈值，默认 28")
    e.add_argument("--gap", type=int, default=6,
                   help="两姿态间小于该宽度就并成一张，默认 6；切多了就调大")
    e.add_argument("--alpha", action="store_true", help="输出透明背景 PNG")
    e.add_argument("--no-clean", action="store_true",
                   help="不清理贴边的邻图碎块")
    e.set_defaults(func=cmd_extract)

    l = sub.add_parser("list", help="列出角色库")
    l.add_argument("--json", action="store_true", help="输出 JSON")
    l.set_defaults(func=cmd_list)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
