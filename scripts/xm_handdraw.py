#!/usr/bin/env python3
"""
xm_handdraw.py — xm-handdraw-video 命令行入口

    python xm_handdraw.py providers                     看生图平台密钥状态
    python xm_handdraw.py styles                        列出画风配方
    python xm_handdraw.py prompt  --style retro-mystery-manga --subject "少年侦探"
    python xm_handdraw.py draw    --style retro-mystery-manga --subject "少年侦探" -o shot.png
    python xm_handdraw.py animate --image shot.png -o shot.mp4
    python xm_handdraw.py make    --style retro-mystery-manga --subject "少年侦探" -o scene.mp4
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import providers  # noqa: E402
import recipe as recipe_mod  # noqa: E402
import compose as compose_mod  # noqa: E402
import narration as narration_mod  # noqa: E402
import check_subjects as check_mod  # noqa: E402
from render import attach_motion, make_colored_video, make_motion, make_video  # noqa: E402
from scenes import Storyboard  # noqa: E402
from strokes import build_strokes  # noqa: E402

VERSION = "0.14.1"


def _read_version_file() -> str:
    """VERSION 文件是版本号的唯一真源，代码里这份只是兜底。"""
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "VERSION")
    try:
        with open(p, encoding="utf-8") as f:
            v = f.read().strip()
        return v or VERSION
    except OSError:
        return VERSION


def _size(s: str) -> tuple[int, int]:
    w, _, h = s.lower().partition("x")
    return int(w), int(h)


def cmd_providers(a):
    print(f"配置文件：{providers.CONFIG_PATH}"
          f"{'（存在）' if os.path.isfile(providers.CONFIG_PATH) else '（不存在）'}")
    print(f"复用来源：{providers.WB_MODELS}"
          f"{'（存在，可自动读取已配的 Agnes Key）' if os.path.isfile(providers.WB_MODELS) else '（不存在）'}\n")
    for s in providers.status():
        mark = "就绪" if s.ready else "缺 " + "、".join(s.missing)
        print(f"  {s.name:<12} {mark:<38} {s.note}")
    print("\n能传锚点参考图的平台：" + "、".join(sorted(providers.SUPPORTS_REFERENCE)))
    print("图生图会让模型照搬参考图内容，配方里「只继承风格、不复制内容」那句必须写清楚。")


def cmd_styles(a):
    recs = {r.id: r for r in recipe_mod.load_all().values()}
    if not recs:
        print("还没有任何画风配方，往 styles/ 里放 .md 文件即可")
        return
    for k in sorted(recs):
        v = recs[k]
        ap = v.anchor_path
        print(f"  {k:<24} {v.name}")
        print(f"  {'':<24} 别名：{'、'.join(v.aliases) or '—'}")
        print(f"  {'':<24} 锚点：{'✓ ' + os.path.relpath(ap, recipe_mod.SKILL_ROOT) if ap else '✗ 未就绪'}")
        print(f"  {'':<24} 配方哈希：{v.sha256()[:16]}")
        print()


def cmd_prompt(a):
    r = recipe_mod.find(a.style)
    ref, ref_mode = _resolve_ref(a, r)
    txt = r.render(a.subject, text=a.text, has_ref=bool(ref), ref_mode=ref_mode)
    print(txt)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(txt + "\n")
        print(f"\n[已写入 {a.out}]")


def _resolve_ref(a, r) -> tuple[str | None, str]:
    """决定这一轮生图带哪张参考图，以及它该怎么用。

    用户给的 --character 优先：那是「固定角色设定图」，要求复刻长相与着装；
    配方自带的 anchor 是「画风锚点」，只继承笔法、不继承人物。
    """
    char = getattr(a, "character", None)
    if char:
        if not os.path.isfile(char):
            print(f"[错误] 找不到角色参考图 {char}", file=sys.stderr)
            sys.exit(1)
        return char, "character"
    if getattr(a, "no_ref", False):
        return None, "style"
    own = getattr(a, "anchor", None)
    if own:
        if not os.path.isfile(own):
            print(f"[错误] 找不到锚点图 {own}", file=sys.stderr)
            sys.exit(1)
        return own, "style"
    return r.anchor_path, "style"


def _gen_image(a) -> str:
    r = recipe_mod.find(a.style)
    ref, ref_mode = _resolve_ref(a, r)
    prompt = r.render(a.subject, text=a.text, has_ref=bool(ref), ref_mode=ref_mode)
    if a.prompt_out:
        with open(a.prompt_out, "w", encoding="utf-8") as f:
            f.write(prompt + "\n")
    if ref:
        kind = "角色" if ref_mode == "character" else "画风"
        print(f"[draw] 画风 {r.id}｜{kind}参考图 {os.path.relpath(ref, recipe_mod.SKILL_ROOT)}")
    else:
        print(f"[draw] 画风 {r.id}｜无参考图（纯提示词）")
    return providers.generate(prompt, a.out, provider=a.provider, size=a.size,
                              seed=a.seed, ref_image=ref, model=a.model)


def cmd_draw(a):
    print(_gen_image(a))


def _video_kwargs(a, scene=None) -> dict:
    """黑白与彩色渲染共用的参数。单场景可以覆盖留白和背景涂色。"""
    margin = a.margin
    if scene is not None and scene.margin is not None:
        margin = scene.margin
    return dict(
        size=_size(a.video_size), fps=a.fps, line_width=a.width,
        margin=margin, ink_mode=a.ink_mode, threshold=a.threshold,
        invert=a.invert, pen_tip=not a.no_pen, max_side=a.max_side,
        crop=not a.no_crop, blob_thickness=a.blob_thickness,
    )


def _render_one(a, image: str, out: str, duration: float, hold: float,
                scene=None) -> str:
    """三种出片方式：镜头运动 / 黑白逐笔 / 彩色逐笔。

    镜头运动（--motion）不矢量化，原图的渐变柔光全部保留，
    适合半写实厚涂这类逐笔上色会变脏的画面。
    """
    kw = _video_kwargs(a, scene)

    motion = getattr(a, "motion", None)
    if scene is not None and getattr(scene, "motion", ""):
        motion = scene.motion
    if motion:
        sid = getattr(scene, "id", "") or os.path.basename(image)
        return make_motion(
            image, out, duration=duration, hold=hold,
            size=kw["size"], fps=kw["fps"], motion=motion,
            zoom=float(getattr(a, "zoom", 1.12) or 1.12),
            seed=sum(ord(c) for c in sid),
        )

    # 画完之后再动一会儿：以末帧为起点接一段推拉，衔接无缝
    after = getattr(a, "after_motion", None)
    if scene is not None and getattr(scene, "after_motion", ""):
        after = scene.after_motion
    base = out
    if after:
        base = out + ".base.mp4"

    paint_bg = a.paint_bg or bool(scene is not None and scene.paint_bg)
    if a.mono:
        res = make_video(image, base, duration=duration, hold=hold, **kw)
    else:
        res = make_colored_video(
            image, base, duration=duration, hold=hold,
            ink_ratio=a.ink_ratio, tile=a.tile,
            skip_background=not paint_bg, **kw)

    if not after:
        return res
    try:
        return attach_motion(
            res, out,
            after_duration=float(getattr(a, "after_duration", 2.0) or 2.0),
            motion=after,
            zoom=float(getattr(a, "after_zoom", 1.06) or 1.06),
            size=kw["size"], fps=kw["fps"],
        )
    finally:
        if os.path.isfile(base):
            try:
                os.remove(base)
            except OSError:
                pass


def cmd_animate(a):
    print(_render_one(a, a.image, a.out, a.duration, a.hold))


def cmd_make(a):
    if a.image:
        img = a.image
        print(f"[make] 复用已有图片 {img}")
    else:
        img = os.path.splitext(a.out)[0] + ".png"
        a_img = argparse.Namespace(**vars(a))
        a_img.out = img
        print(_gen_image(a_img))
    print(_render_one(a, img, a.out, a.duration, a.hold))


def cmd_strokes(a):
    ss = build_strokes(a.image, max_side=a.max_side, mode=a.ink_mode,
                       threshold=a.threshold, invert=a.invert,
                       blob_thickness=a.blob_thickness)
    print(f"画布 {ss.width}x{ss.height}｜笔画 {len(ss.strokes)} 条｜总弧长 {ss.total_length:.0f}px")
    if a.out:
        ss.save(a.out)
        print(f"已写入 {a.out}")


# ------------------------------------------------------------------ 成片流程

def cmd_check(a):
    """出图前检查每场画面够不够饱满（道具、环境层次）。"""
    import json as _json
    with open(a.storyboard, encoding="utf-8") as f:
        sb = _json.load(f)
    rows = check_mod.check_storyboard(sb)
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
    print("\n写法：每场至少 2-3 件与主题相关的具体道具，并写清位置"
          "（头顶／茶几上／身旁／身后／地上）。背景给墙面 + 地面 + 一件家具。")
    if a.strict:
        return 1
    return 0


def cmd_scenes(a):
    """从口播稿机械切出分镜骨架，供后续手工/模型调整。"""
    text = open(a.script, encoding="utf-8").read() if os.path.isfile(a.script) else a.script
    sb = Storyboard.from_script(text, title=a.title, style=a.style or "",
                                voice=a.voice, max_chars=a.max_chars,
                                max_scenes=a.max_scenes)
    sb.path = a.out
    sb.save()
    print(f"切出 {len(sb.scenes)} 个场景 → {a.out}")
    for s in sb.scenes:
        print(f"  {s.id}  ({len(s.text):>3} 字)  {s.text[:46]}")
    print("\n提示：subject 字段留空时会用旁白原文出图。想让画面更准，"
          "就逐条把 subject 改写成「画面描述」。")


def cmd_voice(a):
    sb = Storyboard.load(a.storyboard)
    errs = sb.validate()
    if errs:
        print("[错误] 分镜有问题：", file=sys.stderr)
        for e in errs:
            print("   -", e, file=sys.stderr)
        sys.exit(1)
    narration_mod.synth_storyboard(sb, a.workdir, voice=a.voice or sb.voice, rate=a.rate)
    sb.save()
    srt = narration_mod.write_subtitles(sb, os.path.join(a.workdir, "subtitles.srt"))
    print(f"分镜已回填时长：{a.storyboard}")
    print(f"字幕预览：{srt}")


def _render_scene_clip(sb, scene, a, hold: float):
    """按场景片段总长渲染（片段长度 = 配音时长 + 停顿，与音轨一致）。"""
    clip_len = scene.duration + sb.gap
    hold = scene.hold or hold
    dur = max(0.8, clip_len - hold)
    return _render_one(a, scene.image, scene.clip, dur, hold, scene=scene)


def cmd_build(a):
    """全流程：分镜 → 配音 → 出图 → 动画 → 合成。"""
    if os.path.isfile(a.storyboard):
        sb = Storyboard.load(a.storyboard)
        print(f"[build] 读取分镜 {a.storyboard}（{len(sb.scenes)} 个场景）")
    else:
        text = open(a.script, encoding="utf-8").read() if a.script and os.path.isfile(a.script) else (a.script or "")
        if not text.strip():
            print("[错误] 既没有现成分镜，也没有口播稿。用 --script 指定口播稿，"
                  "或先跑 scenes 生成分镜。", file=sys.stderr)
            sys.exit(1)
        sb = Storyboard.from_script(text, title=a.title, style=a.style or "",
                                    voice=a.voice, max_chars=a.max_chars,
                                    max_scenes=a.max_scenes)
        sb.path = a.storyboard
        print(f"[build] 从口播稿切出 {len(sb.scenes)} 个场景")

    if a.style:
        sb.style = a.style
    if not sb.style:
        print("[错误] 没有指定画风。用 --style 指定，或先跑 styles 看有哪些。", file=sys.stderr)
        sys.exit(1)
    r = recipe_mod.find(sb.style)
    # --anchor 用你自己的图当画风/角色参考（优先于配方自带的锚点图）
    anchor = None if a.no_ref else (a.anchor or r.anchor_path)
    if a.anchor:
        if not os.path.isfile(a.anchor):
            print(f"[错误] 找不到锚点图 {a.anchor}", file=sys.stderr)
            sys.exit(1)
        print(f"[build] 锚点参考图：{a.anchor}")
    # --character 是「固定角色设定图」：复刻长相与着装，优先于画风锚点
    ref, ref_mode = _resolve_ref(a, r)
    if ref_mode == "character":
        anchor = ref
        print(f"[build] 角色参考图：{a.character}（每张图都要求复刻此人）")
    sb.voice = a.voice or sb.voice

    errs = sb.validate()
    if errs:
        print("[错误] 分镜有问题：", file=sys.stderr)
        for e in errs:
            print("   -", e, file=sys.stderr)
        sys.exit(1)

    wd = a.workdir
    for d in ("audio", "shots", "clips"):
        os.makedirs(os.path.join(wd, d), exist_ok=True)

    # ① 配音
    print("\n=== ① 配音 ===")
    narration_mod.synth_storyboard(sb, wd, voice=sb.voice, rate=a.rate)
    sb.save()

    # ② 出图
    print("\n=== ② 出图 ===")
    given = list(a.images or [])
    if given and len(given) > len(sb.scenes):
        print(f"[draw] 你给了 {len(given)} 张图，但只有 {len(sb.scenes)} 个场景，多余的忽略")
    for i, s in enumerate(sb.scenes):
        s.image = os.path.join(wd, "shots", f"{s.id}.png")
        if i < len(given):
            src = given[i]
            if not os.path.isfile(src):
                print(f"[错误] 找不到你指定的图片 {src}", file=sys.stderr)
                sys.exit(1)
            # 用 --images 指到自己工作目录里的图是常见用法，别当成复制失败
            if os.path.abspath(src) != os.path.abspath(s.image):
                shutil.copyfile(src, s.image)
            print(f"[draw] {s.id} 用你指定的图：{os.path.basename(src)}")
            continue
        if os.path.isfile(s.image) and not a.redraw:
            print(f"[draw] {s.id} 已存在，跳过（--redraw 可强制重出）")
            continue
        prompt = r.render(s.image_prompt_subject(), text=s.text_overlay or None,
                          has_ref=bool(anchor), ref_mode=ref_mode)
        providers.generate(prompt, s.image, provider=a.provider, size=a.size,
                           seed=a.seed, ref_image=anchor, model=a.model)
        print(f"[draw] {s.id} → {os.path.basename(s.image)}")

    # ③ 动画
    print("\n=== ③ 手绘动画 ===")
    for s in sb.scenes:
        s.clip = os.path.join(wd, "clips", f"{s.id}.mp4")
        _render_scene_clip(sb, s, a, a.hold)

    # ④ 合成
    print("\n=== ④ 合成成片 ===")
    sb.save()
    info = compose_mod.build_film(sb, a.out, wd, keep_srt=a.keep_srt,
                                  video_size=_size(a.video_size),
                                  subtitle_pos=a.subtitle_pos)
    print(f"\n成片：{info['video']}")
    print(f"时长：{info['duration']}s｜{len(sb.scenes)} 个场景｜"
          f"字幕{'已烧录' if info['burned'] else '未烧录'}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="xm-handdraw",
        description="用自己的画风做逐笔手绘视频（免费模型 + 本地渲染）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--version", action="version",
                    version=f"xm-handdraw-video {_read_version_file()}")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("providers", help="查看生图平台密钥状态").set_defaults(func=cmd_providers)
    sub.add_parser("styles", help="列出画风配方").set_defaults(func=cmd_styles)

    p = sub.add_parser("prompt", help="只渲染提示词，不调模型")
    p.add_argument("--style", required=True)
    p.add_argument("--subject", required=True)
    p.add_argument("--text")
    p.add_argument("--character", metavar="图",
                   help="角色设定图：验证 character 模式的提示词")
    p.add_argument("--anchor", metavar="图", help="画风锚点图")
    p.add_argument("--no-ref", action="store_true")
    p.add_argument("-o", "--out", help="把提示词写进文件")
    p.set_defaults(func=cmd_prompt)

    common_draw = lambda sp: (  # noqa: E731
        sp.add_argument("--style", required=True),
        sp.add_argument("--subject", required=True),
        sp.add_argument("--text"),
        sp.add_argument("--provider", default="auto"),
        sp.add_argument("--model"),
        sp.add_argument("--size", default="1024x1024"),
        sp.add_argument("--seed", type=int),
        sp.add_argument("--no-ref", action="store_true", help="忽略锚点图，只发提示词"),
        sp.add_argument("--anchor", metavar="图",
                        help="画风锚点图：只继承笔法上色，不复制人物"),
        sp.add_argument("--character", metavar="图",
                        help="角色设定图：画面里的人物必须长成这张图的样子"),
        sp.add_argument("--prompt-out", help="保存本次实际使用的提示词"),
    )

    def common_render(sp):
        """彩色渲染相关开关（默认就是彩色）。"""
        sp.add_argument("--mono", action="store_true",
                        help="退回黑白线稿模式，不做上色")
        sp.add_argument("--ink-ratio", type=float, default=0.55,
                        help="勾线占总时长的比例，剩下的时间用来上色")
        sp.add_argument("--tile", type=int, default=220,
                        help="上色扫描的网格大小，越小笔触越细")
        sp.add_argument("--paint-bg", action="store_true",
                        help="连背景一起涂（默认只涂主体，背景保持白纸）")
        sp.add_argument("--motion",
                        choices=["in", "out", "pan-lr", "pan-ud", "breathe", "auto"],
                        help="改用镜头运动代替逐笔绘制：保留厚涂的渐变柔光，"
                             "但没有一笔一笔画出来的过程感")
        sp.add_argument("--zoom", type=float, default=1.12,
                        help="镜头运动的推拉幅度，1.12 = 放大到 112%%")
        sp.add_argument("--after-motion",
                        choices=["in", "out", "pan-lr", "pan-ud", "breathe"],
                        help="逐笔绘制完成后再接一段运动：画完不定格，画面继续活着")
        sp.add_argument("--after-duration", type=float, default=2.0,
                        help="收尾运动时长（秒），默认 2.0")
        sp.add_argument("--after-zoom", type=float, default=1.06,
                        help="收尾运动的幅度，默认 1.06（比整体运动更克制）")

    d = sub.add_parser("draw", help="调用免费生图平台出一张图")
    common_draw(d)
    d.add_argument("-o", "--out", default="shot.png")
    d.set_defaults(func=cmd_draw)

    an = sub.add_parser("animate", help="把一张图变成逐笔手绘视频")
    an.add_argument("--image", required=True)
    an.add_argument("-o", "--out", default="handdraw.mp4")
    an.add_argument("--duration", type=float, default=8.0)
    an.add_argument("--hold", type=float, default=1.2)
    an.add_argument("--video-size", default="1280x720", help="输出分辨率，如 1280x720")
    an.add_argument("--fps", type=int, default=30)
    an.add_argument("--width", type=int, default=3)
    an.add_argument("--margin", type=int, default=56)
    an.add_argument("--max-side", type=int, default=1100)
    an.add_argument("--ink-mode", default="auto", choices=["auto", "dark", "edge"])
    an.add_argument("--threshold", type=int)
    an.add_argument("--invert", action="store_true")
    an.add_argument("--no-pen", action="store_true", help="不画笔尖指示点")
    an.add_argument("--no-crop", action="store_true", help="不裁掉画面空白边")
    an.add_argument("--blob-thickness", type=int, default=6,
                    help="挖空实心色块（黑大衣/纯黑头发）的深度，0 关闭")
    common_render(an)
    an.set_defaults(func=cmd_animate)

    m = sub.add_parser("make", help="一条龙：出图 + 手绘动画")
    common_draw(m)
    m.add_argument("--image", help="复用已有图片，跳过生图")
    m.add_argument("-o", "--out", default="scene.mp4")
    m.add_argument("--duration", type=float, default=8.0)
    m.add_argument("--hold", type=float, default=1.2)
    m.add_argument("--video-size", default="1280x720", help="输出分辨率，如 1280x720")
    m.add_argument("--fps", type=int, default=30)
    m.add_argument("--width", type=int, default=3)
    m.add_argument("--margin", type=int, default=56)
    m.add_argument("--max-side", type=int, default=1100)
    m.add_argument("--ink-mode", default="auto", choices=["auto", "dark", "edge"])
    m.add_argument("--threshold", type=int)
    m.add_argument("--invert", action="store_true")
    m.add_argument("--no-pen", action="store_true")
    m.add_argument("--no-crop", action="store_true")
    m.add_argument("--blob-thickness", type=int, default=6,
                   help="挖空实心色块（黑大衣/纯黑头发）的深度，0 关闭")
    common_render(m)
    m.set_defaults(func=cmd_make)

    s = sub.add_parser("strokes", help="只做矢量化，导出笔画 JSON（调试用）")
    s.add_argument("--image", required=True)
    s.add_argument("-o", "--out")
    s.add_argument("--max-side", type=int, default=1100)
    s.add_argument("--ink-mode", default="auto", choices=["auto", "dark", "edge"])
    s.add_argument("--threshold", type=int)
    s.add_argument("--invert", action="store_true")
    s.add_argument("--blob-thickness", type=int, default=6)
    s.set_defaults(func=cmd_strokes)

    # ---- 成片流程 ----
    ck = sub.add_parser("check", help="出图前检查画面是否够饱满（道具／环境层次）")
    ck.add_argument("--storyboard", default="storyboard.json")
    ck.add_argument("--strict", action="store_true", help="有偏空的场次就返回非零")
    ck.set_defaults(func=cmd_check)

    sc = sub.add_parser("scenes", help="从口播稿切出分镜骨架（storyboard.json）")
    sc.add_argument("--script", required=True, help="口播稿文件路径或直接给文本")
    sc.add_argument("-o", "--out", default="storyboard.json")
    sc.add_argument("--title", default="")
    sc.add_argument("--style", default="")
    sc.add_argument("--voice", default="zh-CN-YunxiNeural")
    sc.add_argument("--max-chars", type=int, default=42, help="每段旁白的目标字数")
    sc.add_argument("--max-scenes", type=int, default=0, help="场景数上限，0 为不限制")
    sc.set_defaults(func=cmd_scenes)

    vo = sub.add_parser("voice", help="给分镜配音，回填每段真实时长")
    vo.add_argument("--storyboard", default="storyboard.json")
    vo.add_argument("--workdir", default=".")
    vo.add_argument("--voice", help="音色 ShortName，如 zh-CN-YunxiNeural")
    vo.add_argument("--rate", default="+0%")
    vo.set_defaults(func=cmd_voice)

    bd = sub.add_parser("build", help="全流程：分镜 → 配音 → 出图 → 动画 → 合成")
    bd.add_argument("--storyboard", default="storyboard.json")
    bd.add_argument("--script", help="没有现成分镜时，从这份口播稿切分")
    bd.add_argument("-o", "--out", default="final.mp4")
    bd.add_argument("--workdir", default=".")
    bd.add_argument("--style", help="画风 id")
    bd.add_argument("--title", default="")
    bd.add_argument("--voice", help="音色 ShortName")
    bd.add_argument("--rate", default="+0%")
    bd.add_argument("--max-chars", type=int, default=42)
    bd.add_argument("--max-scenes", type=int, default=0)
    bd.add_argument("--provider", default="auto")
    bd.add_argument("--model")
    bd.add_argument("--size", default="1024x576", help="出图尺寸，默认与 16:9 画面一致")
    bd.add_argument("--seed", type=int)
    bd.add_argument("--no-ref", action="store_true", help="出图时不带锚点图")
    bd.add_argument("--anchor", metavar="图",
                    help="用你自己的图当画风/角色参考图，优先于配方自带的锚点图")
    bd.add_argument("--character", metavar="图",
                    help="角色设定图：画面里出现的人物必须长成这张图的样子")
    bd.add_argument("--images", nargs="+", metavar="图",
                    help="直接指定各场景画面，按顺序对应；不足的场景才自动出图")
    bd.add_argument("--redraw", action="store_true", help="已存在的图也重新生成")
    bd.add_argument("--video-size", default="1280x720")
    bd.add_argument("--fps", type=int, default=30)
    bd.add_argument("--width", type=int, default=3)
    bd.add_argument("--margin", type=int, default=56)
    bd.add_argument("--max-side", type=int, default=1100)
    bd.add_argument("--hold", type=float, default=0.8, help="每段画完后停留的秒数")
    bd.add_argument("--ink-mode", default="auto", choices=["auto", "dark", "edge"])
    bd.add_argument("--threshold", type=int)
    bd.add_argument("--invert", action="store_true")
    bd.add_argument("--no-pen", action="store_true")
    bd.add_argument("--no-crop", action="store_true", help="不裁掉画面空白边")
    bd.add_argument("--blob-thickness", type=int, default=6,
                    help="挖空实心色块（黑大衣/纯黑头发）的深度，0 关闭")
    common_render(bd)
    bd.add_argument("--keep-srt", action="store_true", help="保留 SRT 文件（默认删掉避免双字幕）")
    bd.add_argument("--subtitle-pos", type=float,
                    help="字幕底边距画面底部的比例 0~1。默认竖屏 2/3、横屏/方形 0.055")
    bd.set_defaults(func=cmd_build)

    return ap


def main():
    ap = build_parser()
    a = ap.parse_args()
    if not getattr(a, "func", None):
        ap.print_help()
        return
    try:
        a.func(a)
    except (providers.ProviderError, KeyError, RuntimeError) as e:
        print(f"\n[错误] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
