"""
scenes.py — 分镜（storyboard）的数据结构与机械切分

一个分镜 = 一串场景。每个场景同时带着「旁白文本」和「画面主体描述」，
配音与出图各取所需。

设计原则：**语义切分由模型做，脚本只做结构与机械切分。**
模型负责想「这段话配什么画面」，脚本负责保证字段齐全、句数对得上、路径合法。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, asdict
from typing import Iterable

# 中文句子结束符（含英文标点与省略号）
_SENT_END = re.compile(r"(?<=[。！？!?；;…])\s*")
# 分句用：标点后切开但保留标点
_SPLIT_KEEP = re.compile(r"[^。！？!?；;…\n]+[。！？!?；;…]?")


def split_sentences(text: str) -> list[str]:
    """把一段文字切成句子，保留标点，去掉空段。"""
    parts = [s.strip() for s in _SPLIT_KEEP.findall(text)]
    return [s for s in parts if s and not s.isspace()]


def split_scenes(text: str, max_chars: int = 42, max_scenes: int = 0) -> list[str]:
    """
    机械切分：按句切，再把短句合并到 max_chars 以内，让每段适合一张画面。

    这只是给模型一个起点，不是最终分镜。模型可以覆盖它。
    """
    sents = split_sentences(text)
    if not sents:
        return []
    out: list[str] = []
    buf = ""
    for s in sents:
        if buf and len(buf) + len(s) > max_chars:
            out.append(buf)
            buf = s
        else:
            buf += s
    if buf:
        out.append(buf)

    if max_scenes and len(out) > max_scenes:
        # 均分合并
        per = (len(out) + max_scenes - 1) // max_scenes
        out = ["".join(out[i:i + per]) for i in range(0, len(out), per)]
    return out


@dataclass
class Scene:
    id: str
    text: str                 # 该段旁白
    subject: str = ""         # 画面主体描述（给生图用）；留空则用 text
    text_overlay: str = ""    # 画面内要写的字
    duration: float = 0.0     # 配音后回填（秒）
    rate: str = ""            # 本场语速覆盖，如 "-12%"；空则用全局
    hold: float = 0.0         # 本场画完后停留秒数覆盖；0 则用全局
    paint_bg: bool = False    # 本场是否连背景一起涂（字幕卡这类满幅图需要）
    margin: int | None = None  # 本场留白覆盖；字幕卡用 0 做成满幅
    subtitle_off: bool = False  # 本场不出字幕（画面本身就是字幕卡）
    motion: str = ""          # 本场改用镜头运动：in/out/pan-lr/pan-ud/auto；空则逐笔绘制
    after_motion: str = ""    # 本场绘制完成后再接的收尾运动（breathe 最自然）
    # 上色焦点：按口播顺序给 1-3 个归一化框 [x0,y0,x1,y1]（0~1），
    # 框里的物体先涂。例如讲「人」的那场先涂人物所在的框，再涂车、房。
    paint_focus: list = field(default_factory=list)
    # 本场改用哪种上色方式（object / importance / scan），空则跟命令行
    paint_order: str = ""
    audio: str = ""
    image: str = ""
    clip: str = ""
    start: float = 0.0        # 合成时回填（秒）

    def image_prompt_subject(self) -> str:
        return (self.subject or self.text).strip()

    def subtitle(self) -> str:
        """字幕文本：画面内文字优先，否则用旁白。"""
        return (self.text_overlay or self.text).strip()


@dataclass
class Storyboard:
    title: str = ""
    style: str = ""
    voice: str = "zh-CN-YunxiNeural"
    # 场景之间的停顿。配音尾巴已经剪到 0.15s，这里只要留一个换气的口子，
    # 再大就会让镜头之间「断开」（实测 0.35 时每个切点有 1.3s 死区）。
    gap: float = 0.12
    scenes: list[Scene] = field(default_factory=list)
    path: str = ""

    # ---- 读写
    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("path", None)
        return d

    def save(self, path: str | None = None):
        p = path or self.path
        if not p:
            raise ValueError("没有指定保存路径")
        self.path = p
        os.makedirs(os.path.dirname(os.path.abspath(p)) or ".", exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        return p

    @staticmethod
    def load(path: str) -> "Storyboard":
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        scenes = [Scene(**{k: v for k, v in s.items() if k in Scene.__dataclass_fields__})
                  for s in d.get("scenes", [])]
        sb = Storyboard(
            title=d.get("title", ""), style=d.get("style", ""),
            voice=d.get("voice", "zh-CN-YunxiNeural"),
            gap=float(d.get("gap", 0.12)), scenes=scenes, path=path,
        )
        return sb

    # ---- 构造
    @staticmethod
    def from_script(text: str, title: str = "", style: str = "",
                    voice: str = "zh-CN-YunxiNeural",
                    max_chars: int = 42, max_scenes: int = 0) -> "Storyboard":
        chunks = split_scenes(text, max_chars=max_chars, max_scenes=max_scenes)
        scenes = [Scene(id=f"scene_{i+1:02d}", text=c) for i, c in enumerate(chunks)]
        return Storyboard(title=title, style=style, voice=voice, scenes=scenes)

    # ---- 校验
    def validate(self) -> list[str]:
        errs: list[str] = []
        if not self.scenes:
            errs.append("分镜里没有任何场景")
            return errs
        seen = set()
        for i, s in enumerate(self.scenes, 1):
            if not s.id:
                errs.append(f"第 {i} 个场景缺少 id")
            elif s.id in seen:
                errs.append(f"场景 id 重复：{s.id}")
            seen.add(s.id)
            if not s.text.strip():
                errs.append(f"场景 {s.id} 的旁白文本为空")
            if not s.image_prompt_subject():
                errs.append(f"场景 {s.id} 既没有 subject 也没有 text，无法出图")
            if s.text_overlay and len(s.text_overlay) > 24:
                errs.append(f"场景 {s.id} 的画面文字过长（{len(s.text_overlay)} 字），建议 ≤24 字")
        return errs

    # ---- 时间轴
    def clip_lengths(self) -> list[float]:
        """每个场景的片段总长 = 配音时长 + 停顿。视频与音轨都用这个长度。"""
        return [round(s.duration + self.gap, 3) for s in self.scenes]

    def total_duration(self) -> float:
        return round(sum(self.clip_lengths()), 3)

    def assign_timeline(self):
        """按每个场景的实际配音时长，算出各自在成片里的起始时刻。"""
        t = 0.0
        for s in self.scenes:
            s.start = round(t, 3)
            t += s.duration + self.gap

    def srt_cues(self, max_chars: int = 17) -> list[tuple[float, float, str]]:
        """一句一屏。

        一个场景的旁白可能有五六十字，整句塞进一条字幕会占掉半屏。
        这里按标点把每段旁白切成短句，再按字数把配音时长分下去——
        切得越短越接近「说一句、出一句」的节奏。

        字幕里**不显示标点**：短视频的通行做法，画面更干净。切分仍然按标点做，
        时长也仍按原句长度分配，只是最后写进字幕的那行洗掉了标点。
        """
        self.assign_timeline()
        cues: list[tuple[float, float, str]] = []
        for s in self.scenes:
            if s.subtitle_off:
                continue
            text = s.subtitle()
            parts = split_cues(text, max_chars)
            total = sum(len(p) for p in parts) or 1
            t = s.start
            for p in parts:
                dt = s.duration * len(p) / total
                shown = strip_punct(p) or p.strip()
                cues.append((round(t, 3), round(t + dt, 3), shown))
                t += dt
        return cues


_PUNCT = "，。！？；：、,.!?;:"

# 「第一，」「其二，」「3.」这类序号句：必须自己起一屏，不能粘到上一句尾巴上
_ORDINAL = re.compile(r"^(第[一二三四五六七八九十百零\d]+|[其\d])\s*[，、,.．]?$")

# 字幕里要洗掉的标点：中英文标点、引号、括号、省略号、破折号、间隔号、波浪号
_STRIP = "，。！？；：、,.!?;:…‥—–－·\"'“”‘’（）()《》〈〉【】[]「」『』〔〕～~ 　"


def strip_punct(text: str) -> str:
    """把字幕里的标点换成空格，只留文字、数字、字母。

    以前是直接把标点删掉，结果两个短句会粘成一句病句：
    「存钱的哭」+「至于咱们身边」→「存钱的哭至于咱们身边」。
    现在换成空格，断句的位置留了个口子，读起来顺得多。

    英文单词之间的半角空格要留下：「Small Cell」洗成「SmallCell」就是一个
    不存在的词了。连续标点（——……）只留一个空格。
    """
    out: list[str] = []
    for i, ch in enumerate(text):
        if ch not in _STRIP:
            out.append(ch)
            continue
        prev = text[i - 1] if i > 0 else ""
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if ch == " " and prev.isascii() and prev.isalnum() and nxt.isascii() and nxt.isalnum():
            out.append(ch)
            continue
        out.append(" ")
    s = "".join(out)
    while "  " in s:
        s = s.replace("  ", " ")
    return s.strip()


def _clauses(text: str) -> list[str]:
    """按标点把旁白切成小句，标点留在句尾。"""
    out: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in _PUNCT:
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return out


def split_cues(text: str, max_chars: int = 18) -> list[str]:
    """把一段旁白切成若干屏字幕，每屏不超过 max_chars。

    做法是先按标点切成小句，再贪心地把小句拼进同一屏，拼不下才换屏。
    这样既不会出现「机顶盒、/ 路由器、」这种一秒一闪的碎屏，
    也不会把「教你自己家」切成「教你 / 给自己家」。
    只有单个小句本身就超长（中间没标点）才会被对半切开。
    """
    text = text.strip()
    if not text:
        return [""]
    if len(text) <= max_chars:
        return [text]

    cues: list[str] = []
    cur = ""
    for cl in _clauses(text):
        while len(cl) > max_chars:
            cut = len(cl) // 2 if len(cl) <= max_chars * 2 else max_chars
            if cur:
                cues.append(cur)
                cur = ""
            cues.append(cl[:cut])
            cl = cl[cut:]
        if not cur:
            cur = cl
        elif _ORDINAL.match(cl.strip()):
            # 「第一，」「第二，」这类序号句不能拼到上一句尾巴上，
            # 否则会切出「…放中间第一」这种读不通的字幕
            cues.append(cur)
            cur = cl
        elif len(cur) + len(cl) <= max_chars:
            cur += cl
        else:
            cues.append(cur)
            cur = cl
    if cur:
        cues.append(cur)

    # 收尾太短就并回上一屏，避免最后闪一个字。
    # 注意：必须先把尾巴 pop 出来再拼。写成 `cues[-2] += cues.pop()` 会出事——
    # 负索引在 pop 之后指向的是另一个元素，结果把拼接值写到了上一屏身上，
    # 导致字幕整条重复（实测：两句并成「A、A的前半段」）。
    if len(cues) >= 2 and len(cues[-1]) < 4:
        tail = cues.pop()
        cues[-1] += tail
    # 开头太短就并进下一屏，避免一上来闪三个字（如「路边、」「关注我，」）
    if len(cues) >= 2 and len(cues[0]) < 5 and len(cues[0]) + len(cues[1]) <= max_chars + 4:
        first = cues.pop(0)
        cues[0] = first + cues[0]
    return [c for c in cues if c.strip()]


def _wrap(text: str, max_line: int = 17) -> str:
    """
    字幕自动折行。SRT 里用 \\N 表示换行。
    不折行的话，长句会横跨整个画面、顶到边缘，很难读。
    """
    text = text.strip()
    if len(text) <= max_line:
        return text
    # 优先在标点处断
    for i in range(max_line, max(4, max_line - 8), -1):
        if i < len(text) and text[i - 1] in "，。！？；：、,.":
            return text[:i] + r"\N" + _wrap(text[i:], max_line)
    mid = len(text) // 2
    # 两行时尽量均分
    if len(text) <= max_line * 2:
        for i in range(mid, max(4, mid - 6), -1):
            if text[i - 1] in "，。！？；：、,.":
                return text[:i] + r"\N" + text[i:]
        return text[:mid] + r"\N" + text[mid:]
    return text[:max_line] + r"\N" + _wrap(text[max_line:], max_line)


def _fmt_ts(sec: float) -> str:
    if sec < 0:
        sec = 0.0
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(cues: Iterable[tuple[float, float, str]], path: str,
              max_line: int = 17) -> str:
    lines = []
    for i, (a, b, text) in enumerate(cues, 1):
        lines.append(str(i))
        lines.append(f"{_fmt_ts(a)} --> {_fmt_ts(b)}")
        lines.append(_wrap(text, max_line) if max_line else text)
        lines.append("")
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


if __name__ == "__main__":
    import sys
    txt = sys.stdin.read() if len(sys.argv) < 2 else open(sys.argv[1], encoding="utf-8").read()
    sb = Storyboard.from_script(txt)
    print(f"切出 {len(sb.scenes)} 个场景：")
    for s in sb.scenes:
        print(f"  {s.id}  ({len(s.text)} 字)  {s.text[:50]}")
