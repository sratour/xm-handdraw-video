"""出图前检查分镜的「画面饱满度」。

用户要求：每场至少 2-3 件与主题相关的具体道具／环境元素。
`subject` 只写「背景是浅米色墙面」时，模型会真的只画一面白墙加一个人，
人物占满画面、四周全是空色块——技术上没出错，成片看着廉价。

这个脚本在 build 之前扫一遍，把偏空的场次挑出来，别等出完图才发现。

用法：
    python check_subjects.py storyboard.json
    python check_subjects.py storyboard.json --strict   # 有 thin/empty 就返回 1

判定只看三件事（都是能机械判的，不做语义理解）：
    1. 描述长度够不够
    2. 画面分了几层（按标点切分句）
    3. 有没有具体的量词短语（「一张沙发」「两台机器」）和环境词（墙／地／窗外）
"""
from __future__ import annotations

import json
import re
import sys

# 中文量词是「具体物件」的强信号：一张沙发、两台机器、三只轮子
_QUANT = re.compile(r"[一二两三四五六七八九十几半]\s*[个张台把辆只条块幅杯盏盆件根片扇座排层]")
# 环境层次
_ENV = ("墙", "地面", "地板", "瓷砖", "窗外", "天空", "街道", "背景", "柜", "桌", "架", "沙发", "床", "椅")
# 不按「空」处理：金句卡和 CTA 收尾本来就该干净
_SKIP_HINT = ("card", "summary", "cta")


def split_clauses(text: str) -> list[str]:
    return [c for c in re.split(r"[；;，,。.！!？?、]", text) if c.strip()]


def judge(subject: str) -> tuple[str, list[str]]:
    """返回 (等级, 提示列表)。等级：ok / thin / empty。"""
    s = subject.strip()
    if not s:
        return "empty", ["subject 为空，会拿旁白原文当画面描述"]

    n = len(s)
    clauses = split_clauses(s)
    quants = _QUANT.findall(s)
    has_env = any(w in s for w in _ENV)

    notes: list[str] = []
    if n < 40:
        notes.append(f"描述只有 {n} 字，太短")
    if len(clauses) < 3:
        notes.append(f"只有 {len(clauses)} 层画面，建议拆成「人物动作 / 周围道具 / 背景」")
    if len(quants) < 2:
        notes.append(f"具体物件只有 {len(quants)} 件，建议补到 2-3 件")
    if not has_env:
        notes.append("没写环境（墙面／地面／窗外），容易只有白底")

    if not notes:
        return "ok", []
    if n < 25 or not has_env:
        return "empty", notes
    return "thin", notes


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = sys.argv[1]
    strict = "--strict" in sys.argv

    with open(path, encoding="utf-8") as f:
        sb = json.load(f)

def check_storyboard(sb: dict) -> list[tuple[str, str, list[str]]]:
    """扫描分镜，返回 [(场景 id, 等级, 提示)]。等级：ok / thin / empty / skip。"""
    rows = []
    for sc in sb.get("scenes", []):
        sid = sc.get("id", "?")
        subject = sc.get("subject", "")
        image = str(sc.get("image", "")).lower()
        # 金句卡是 PIL 排的，subject 天然为空；CTA 收尾也该干净。
        # 认这三条线索，而不是认 id 名字。
        is_card = (not subject.strip()) and ("card" in image
                                            or sc.get("paint_bg")
                                            or sc.get("subtitle_off"))
        if is_card or any(h in sid.lower() for h in _SKIP_HINT):
            rows.append((sid, "skip", ["金句卡／CTA，允许留白"]))
            continue
        level, notes = judge(subject)
        rows.append((sid, level, notes))
    return rows


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = sys.argv[1]
    strict = "--strict" in sys.argv

    with open(path, encoding="utf-8") as f:
        sb = json.load(f)

    rows = check_storyboard(sb)
    bad = [r for r in rows if r[1] in ("thin", "empty")]
    for sid, level, notes in rows:
        mark = {"ok": "✓", "thin": "△", "empty": "✗", "skip": "–"}[level]
        print(f"{mark} {sid:10s} {level:5s}  {'；'.join(notes)}")

    print()
    if not bad:
        print(f"全部 {len(rows)} 场画面都够饱满。")
        return 0
    print(f"有 {len(bad)} 场偏空，出图前建议补道具：")
    for sid, level, notes in bad:
        print(f"  - {sid}：{notes[0]}")
    return 1 if strict else 0


if __name__ == "__main__":
    sys.exit(main())
