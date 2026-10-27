"""
recipe.py — 画风配方的解析与提示词渲染

一个「画风」= 提示词配方 + 锚点参考图 + 可选的阶段性修正。
配方文件用 markdown 写，结构见 references/style-recipe-spec.md。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Iterable

def _rel(p: str) -> str:
    """跨盘符时 relpath 会抛 ValueError，退回原路径。"""
    try:
        return os.path.relpath(p, SKILL_ROOT)
    except ValueError:
        return p


SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STYLES_DIR = os.path.join(SKILL_ROOT, "styles")

_HEADING = re.compile(r"^##\s+(.+?)\s*$")
_FIELD = re.compile(r"^>\s*(?:\*\*)?([^：:*]+)(?:\*\*)?\s*[：:]\s*(.*)$")
_FENCE = re.compile(r"^```")
_NUM_PREFIX = re.compile(r"^(\d+(?:\.\d+)?)\s+(.*)$")
_ASCII_ID = re.compile(r"^([A-Za-z][A-Za-z0-9._-]*)\s+(.*)$")


def parse_heading(text: str) -> tuple[str | None, str]:
    """`## 1 retro-mystery-manga 九十年代周刊少年推理漫画` → (id, 名称)"""
    m = _NUM_PREFIX.match(text.strip())
    rest = m.group(2).strip() if m else text.strip()
    m2 = _ASCII_ID.match(rest)
    if m2:
        return m2.group(1), m2.group(2).strip()
    return None, rest


@dataclass
class Recipe:
    id: str
    name: str
    aliases: list[str] = field(default_factory=list)
    anchor: str | None = None
    contract: str = ""
    keys: str = ""
    text_rule: str = ""
    body: str = ""
    path: str = ""
    extras: dict = field(default_factory=dict)

    @property
    def anchor_path(self) -> str | None:
        if not self.anchor:
            return None
        if os.path.isabs(self.anchor):
            return self.anchor if os.path.isfile(self.anchor) else None
        p = os.path.join(SKILL_ROOT, self.anchor)
        return p if os.path.isfile(p) else None

    def sha256(self) -> str:
        h = hashlib.sha256()
        h.update(self.body.encode("utf-8"))
        h.update(self.keys.encode("utf-8"))
        ap = self.anchor_path
        if ap:
            with open(ap, "rb") as f:
                h.update(f.read())
        return h.hexdigest()

    def render(self, subject: str, text: str | None = None,
               has_ref: bool = False, ratio: str | None = None,
               ref_mode: str = "style") -> str:
        """把配方 + 主体 + 文字要求，渲染成最终投喂给生图模型的提示词。

        ref_mode 决定参考图怎么用：
          - "style"     画风锚点：只继承笔法、上色、气质，**不复制人物身份**
          - "character" 角色锚点：**必须**保持参考图人物的脸、发型、服装不变，
                        只换动作、姿势、场景。用于用户上传的角色设定图。
        """
        body = self.body.strip()
        body = body.replace("【主体】", subject.strip())
        body = body.replace("【SUBJECT】", subject.strip())

        if "【文字】" in body:
            if text:
                rule = self.text_rule or ""
                rule = rule.replace("……", text).replace("...", text)
                body = body.replace("【文字】", text)
            else:
                body = body.replace("【文字】", "")
        elif text:
            body += f"\n\n画面文字要求：{text}。文字须准确清晰，笔画完整，不得出现错字或多余字符。"
        elif self.text_rule and "【文字】" not in body:
            body += "\n\n不加任何文字、logo 或水印。"

        parts = []
        if has_ref and ref_mode == "character":
            parts.append(
                "随请求附带的参考图是**本片固定角色**的人物设定图。画面里出现的这个人，"
                "必须和参考图是同一个人：\n"
                "脸型、眉眼形状、瞳孔颜色、发际线、发长与扎法、发色全部照搬；\n"
                "服装、鞋、配饰也照搬参考图里的那一套，款式、颜色、长度都不要改。\n"
                "只改变这个人的动作、姿势、朝向和所处场景，"
                "**不要**改变长相、年龄和着装。\n"
                "不要把参考图的版式、文字、边框、拼贴布局画进新画面——"
                "参考图只是角色档案，不是构图参考。\n"
                "**画法严格服从本片画风**：参考图的线条与上色方式只用于识别这个人，"
                "新画面的笔法、线条粗细、上色方式一律按下面的画风配方执行，"
                "不要照搬参考图的画法。"
            )
        elif has_ref:
            parts.append(
                "把随请求附带的参考图当作本画风的唯一主视觉锚点：新画首先要像同一位画师"
                "用同一支笔、同一套上色方式画出来。只继承参考图的线条、五官画法、人物比例、"
                "上色方式和留白气质；不要复制参考图里的人物身份、服装、站位或情节。"
                "参考图不可用时，只按下面的文字规则执行。"
            )
        parts.append(body)
        if ratio:
            parts.append(f"画面比例：{ratio}。")
        return "\n\n".join(parts)


def parse_recipe(path: str) -> Recipe:
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    rid, name = os.path.splitext(os.path.basename(path))[0], ""
    in_fence = False
    body_lines: list[str] = []

    # 单遍解析：围栏内的内容进 body，围栏外的标题与 > 字段进元数据
    aliases: list[str] = []
    anchor = None
    contract = ""
    keys = ""
    text_rule = ""
    cur_field = None
    for ln in lines:
        if _FENCE.match(ln.strip()):
            in_fence = not in_fence
            continue
        if in_fence:
            body_lines.append(ln)
            continue
        m = _HEADING.match(ln)
        if m:
            hid, hname = parse_heading(m.group(1))
            if hid:
                rid = hid
            if hname:
                name = hname
            continue
        m = _FIELD.match(ln)
        if m:
            key, val = m.group(1).strip(), m.group(2).strip()
            cur_field = key
            if "别名" in key:
                aliases = [a for a in re.split(r"[、,，/\s]+", val) if a]
            elif "真源" in key or "锚点" in key:
                anchor = val
            elif "合同" in key:
                contract = val
            elif "命门" in key:
                keys = val
            elif "文字" in key:
                text_rule = val
            continue
        if cur_field and ln.strip() and ln.strip() != ">" and not ln.strip().startswith("#"):
            val = ln.strip()
            if "合同" in cur_field:
                contract += " " + val
            elif "命门" in cur_field:
                keys += " " + val
            elif "文字" in cur_field:
                text_rule += " " + val

    body = "\n".join(body_lines).strip()
    if not name:
        name = rid

    return Recipe(id=rid, name=name, aliases=aliases, anchor=anchor,
                  contract=contract.strip(), keys=keys.strip(),
                  text_rule=text_rule.strip(), body=body, path=path)


def load_all(styles_dir: str | None = None) -> dict[str, Recipe]:
    d = styles_dir or STYLES_DIR
    out: dict[str, Recipe] = {}
    if not os.path.isdir(d):
        return out
    for fn in sorted(os.listdir(d)):
        if not fn.lower().endswith(".md") or fn.lower().startswith("_"):
            continue
        rec = parse_recipe(os.path.join(d, fn))
        out[rec.id] = rec
        for a in rec.aliases:
            out.setdefault(a, rec)
    return out


def find(key: str, styles_dir: str | None = None) -> Recipe:
    all_ = load_all(styles_dir)
    if key in all_:
        return all_[key]
    low = key.lower()
    for k, v in all_.items():
        if k.lower() == low or v.name == key:
            return v
    raise KeyError(f"找不到画风：{key}（可用：{', '.join(sorted(set(r.id for r in all_.values())))})")


if __name__ == "__main__":
    import sys
    for k, v in sorted({r.id: r for r in load_all().values()}.items()):
        ap = v.anchor_path
        print(f"{k:<24} {v.name}")
        print(f"{'':<24} 别名 {', '.join(v.aliases) or '-'}")
        print(f"{'':<24} 锚点 {'✓ ' + _rel(ap) if ap else '✗ 未就绪'}")
    if len(sys.argv) > 1:
        r = find(sys.argv[1])
        print("\n--- 渲染示例 ---")
        print(r.render("一个少年侦探指向远方", text="真相只有一个"))
