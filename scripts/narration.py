"""
narration.py — 配音与时间轴

用 Edge TTS（免费、无需 Key）把整篇口播稿合成旁白，并拿到**句级时间戳**。
声音决定节奏，画面适配声音——所以时间轴必须来自真实配音，不能拍脑袋。

edge-tts 7.x 的 Communicate 有 boundary 参数：
    SentenceBoundary（默认）→ 每个句子一条时间戳，正好用来切场景
    WordBoundary            → 词级，需要更细的字幕时用
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field

from scenes import Storyboard, write_srt

try:
    import edge_tts
except ImportError:  # pragma: no cover
    edge_tts = None

TICKS = 1e7   # edge-tts 的时间单位是 100 纳秒
DEFAULT_VOICE = "zh-CN-YunxiNeural"


@dataclass
class Boundary:
    start: float
    duration: float
    text: str

    @property
    def end(self) -> float:
        return self.start + self.duration


@dataclass
class SynthResult:
    audio: str
    duration: float
    boundaries: list[Boundary] = field(default_factory=list)


def probe_duration(path: str, ffmpeg: str | None = None) -> float:
    """用 ffmpeg 读真实时长，避免只靠时间戳估算。"""
    from render import find_ffmpeg
    exe = ffmpeg or find_ffmpeg()
    if not exe or not os.path.isfile(path):
        return 0.0
    try:
        p = subprocess.run([exe, "-i", path, "-f", "null", "-"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        txt = p.stderr
        m = None
        for m in re.finditer(r"time=(\d+):(\d+):(\d+\.\d+)", txt):
            pass
        if m:
            h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
            return h * 3600 + mi * 60 + s
    except Exception:
        pass
    return 0.0


def synth(text: str, out_mp3: str, voice: str = DEFAULT_VOICE,
          rate: str = "+0%", volume: str = "+0%", pitch: str = "+0Hz",
          boundary: str = "SentenceBoundary") -> SynthResult:
    """把一段文本合成为 mp3，同时返回句级（或词级）时间戳。"""
    if edge_tts is None:
        raise RuntimeError("缺少 edge-tts，请先 pip install edge-tts")
    if not text.strip():
        raise ValueError("要合成的文本为空")

    async def run():
        comm = edge_tts.Communicate(text, voice, rate=rate, volume=volume,
                                    pitch=pitch, boundary=boundary)
        audio = bytearray()
        bounds: list[Boundary] = []
        async for ch in comm.stream():
            if ch["type"] == "audio":
                audio += ch["data"]
            elif ch["type"] in ("SentenceBoundary", "WordBoundary"):
                bounds.append(Boundary(ch["offset"] / TICKS,
                                       ch["duration"] / TICKS,
                                       ch.get("text", "")))
        return bytes(audio), bounds

    data, bounds = asyncio.run(run())
    if not data:
        raise RuntimeError("Edge TTS 没有返回音频，检查网络或代理设置")

    os.makedirs(os.path.dirname(os.path.abspath(out_mp3)) or ".", exist_ok=True)
    with open(out_mp3, "wb") as f:
        f.write(data)

    real = probe_duration(out_mp3)
    est = (bounds[-1].end + 0.30) if bounds else 0.0
    dur = real if real > 0.05 else est
    return SynthResult(audio=out_mp3, duration=round(dur, 3), boundaries=bounds)


def synth_storyboard(sb: Storyboard, workdir: str, voice: str | None = None,
                     rate: str = "+0%", verbose: bool = True,
                     reuse: bool = False) -> Storyboard:
    """
    逐场景合成配音，把每个场景的真实时长回填进分镜。

    为什么逐场景而不是整篇一次：
      场景与配音要严格一一对应，逐场景能得到**确定**的时长，不需要事后做文本对齐。
      代价是跨句语调衔接略弱于整篇生成；解释类视频里听不出来。

    reuse=True 时，audio/ 里已有且非空的 mp3 直接复用、只回填时长。
    默认 False（每次都重合成）是刻意的：改了文案却拿到旧音频，比多花两分钟糟糕得多。
    """
    v = voice or sb.voice or DEFAULT_VOICE
    adir = os.path.join(workdir, "audio")
    os.makedirs(adir, exist_ok=True)

    for s in sb.scenes:
        out = os.path.join(adir, f"{s.id}.mp3")
        if reuse and os.path.isfile(out) and os.path.getsize(out) > 2048:
            dur = probe_duration(out) or s.duration
            s.audio = out
            s.duration = round(dur, 3)
            if verbose:
                print(f"[voice] {s.id}  {s.duration:6.2f}s  复用已有音频  {s.text[:28]}")
            continue
        # 单场景可以覆盖语速：总结句、字幕卡这类地方需要放慢
        r = synth(s.text, out, voice=v, rate=s.rate or rate)
        s.audio = out
        s.duration = r.duration
        if verbose:
            print(f"[voice] {s.id}  {r.duration:6.2f}s  {s.text[:28]}")

    sb.voice = v
    sb.assign_timeline()
    if verbose:
        print(f"[voice] 共 {len(sb.scenes)} 段，含停顿总长 {sb.total_duration():.1f}s")
    return sb


def write_subtitles(sb: Storyboard, path: str) -> str:
    return write_srt(sb.srt_cues(), path)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="配音 + 句级时间戳")
    ap.add_argument("text")
    ap.add_argument("-o", "--out", default="narration.mp3")
    ap.add_argument("--voice", default=DEFAULT_VOICE)
    ap.add_argument("--srt")
    a = ap.parse_args()
    txt = open(a.text, encoding="utf-8").read() if os.path.isfile(a.text) else a.text
    r = synth(txt, a.out, voice=a.voice)
    print(f"音频 {a.out}  时长 {r.duration:.2f}s")
    for b in r.boundaries:
        print(f"  {b.start:6.2f}s +{b.duration:5.2f}s  {b.text}")
    if a.srt:
        from scenes import _fmt_ts
        cues = [(b.start, b.end, b.text) for b in r.boundaries]
        write_srt(cues, a.srt)
        print(f"字幕 {a.srt}")
