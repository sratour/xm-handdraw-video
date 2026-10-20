"""
providers.py — 免费生图平台适配器

统一接口，底层可切换。所有平台都走免费额度，密钥从环境变量或
配置文件 ~/.xm-handdraw/config.json 读取。

    python providers.py status          # 看哪些平台已就绪
    python providers.py gen --provider modelscope --prompt "..." -o out.png
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".xm-handdraw", "config.json")
# WorkBuddy 的模型配置：用户可能已经在这里配好 Agnes，直接复用，不必再填一次密钥
WB_MODELS = os.path.join(os.path.expanduser("~"), ".workbuddy-ai", "models.json")
TIMEOUT = 180
UA = "xm-handdraw/0.1"


class ProviderError(RuntimeError):
    pass


def _from_workbuddy_models(host_hint: str) -> dict | None:
    """在 WorkBuddy 的 models.json 里找已配置的同类服务，返回 {base_url, api_key}。"""
    if not os.path.isfile(WB_MODELS):
        return None
    try:
        entries = json.load(open(WB_MODELS, encoding="utf-8"))
    except Exception:
        return None
    if isinstance(entries, dict):
        entries = [entries]
    for e in entries if isinstance(entries, list) else []:
        url = str(e.get("url") or "")
        key = str(e.get("apiKey") or "")
        if host_hint in url and key:
            return {"base_url": url.rstrip("/"), "api_key": key}
    return None


def _request(url: str, method: str = "GET", headers: dict | None = None,
             body: dict | None = None, timeout: int = TIMEOUT) -> bytes:
    """极简 HTTP，只用标准库，省掉一个依赖。"""
    hdrs = {"User-Agent": UA}
    if headers:
        hdrs.update(headers)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        detail = b""
        try:
            detail = e.read()
        except Exception:
            pass
        raise ProviderError(
            f"HTTP {e.code} {e.reason}｜{detail[:400].decode('utf-8', 'ignore')}"
        ) from e
    except urllib.error.URLError as e:
        raise ProviderError(f"网络不可达：{e.reason}") from e


def _request_json(url: str, **kw) -> dict:
    raw = _request(url, **kw)
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise ProviderError(f"返回内容不是 JSON：{raw[:300]!r}") from e


def _download(url: str, out_path: str, headers: dict | None = None):
    _write(out_path, _request(url, headers=headers, timeout=TIMEOUT))


def load_config() -> dict:
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def cfg(key: str, default: str | None = None) -> str | None:
    """环境变量优先，其次配置文件。"""
    v = os.environ.get(key)
    if v:
        return v
    return load_config().get(key, default)


def _parse_size(size: str | tuple[int, int]) -> tuple[int, int]:
    if isinstance(size, tuple):
        return size
    w, _, h = size.lower().partition("x")
    return int(w), int(h)


# ---------------------------------------------------------------- 各平台实现

def _gen_modelscope(prompt: str, size, seed, ref_image, out_path, **kw) -> str:
    token = cfg("MODELSCOPE_API_TOKEN")
    if not token:
        raise ProviderError("缺少 MODELSCOPE_API_TOKEN")
    model = kw.get("model") or cfg("MODELSCOPE_MODEL", "Tongyi-MAI/Z-Image-Turbo")
    w, h = _parse_size(size)
    headers = {"Authorization": f"Bearer {token}",
               "Content-Type": "application/json",
               "X-ModelScope-Async-Mode": "true"}
    body: dict[str, Any] = {"model": model, "prompt": prompt, "n": 1,
                            "size": f"{w}x{h}"}
    if seed is not None:
        body["seed"] = int(seed)

    data = _request_json(
        "https://api-inference.modelscope.cn/v1/images/generations",
        method="POST", headers=headers, body=body)
    task = data.get("task_id")
    if not task:
        raise ProviderError(f"ModelScope 未返回 task_id：{json.dumps(data)[:300]}")

    poll = {"Authorization": f"Bearer {token}", "X-ModelScope-Task-Type": "image_generation"}
    deadline = time.time() + 300
    while time.time() < deadline:
        time.sleep(3)
        info = _request_json(f"https://api-inference.modelscope.cn/v1/tasks/{task}",
                             headers=poll, timeout=60)
        st = info.get("task_status")
        if st == "SUCCEED":
            urls = info.get("output_images") or []
            if not urls:
                raise ProviderError("ModelScope 任务成功但没有图片")
            _download(urls[0], out_path)
            return out_path
        if st in ("FAILED", "CANCELED"):
            raise ProviderError(f"ModelScope 任务失败：{json.dumps(info)[:300]}")
    raise ProviderError("ModelScope 轮询超时")


def _gen_cloudflare(prompt: str, size, seed, ref_image, out_path, **kw) -> str:
    token = cfg("CLOUDFLARE_API_TOKEN")
    acct = cfg("CLOUDFLARE_ACCOUNT_ID")
    if not token or not acct:
        raise ProviderError("缺少 CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID")
    model = kw.get("model") or cfg("CLOUDFLARE_MODEL",
                                   "@cf/black-forest-labs/flux-1-schnell")
    w, h = _parse_size(size)
    # flux-1-schnell 只接受一组固定尺寸，512 与 1024 两个方向
    w = 512 if w <= 768 else 1024
    h = 512 if h <= 768 else 1024
    url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model}"
    body: dict[str, Any] = {"prompt": prompt, "width": w, "height": h, "steps": 4}
    if seed is not None:
        body["seed"] = int(seed)
    data = _request_json(url, method="POST",
                         headers={"Authorization": f"Bearer {token}"}, body=body)
    if not data.get("success", True):
        raise ProviderError(f"Cloudflare 返回失败：{json.dumps(data)[:300]}")
    result = data.get("result") or {}
    b64 = result.get("image")
    if not b64:
        raise ProviderError("Cloudflare 未返回图片数据")
    _write(out_path, base64.b64decode(b64))
    return out_path


def _gen_agnes(prompt: str, size, seed, ref_image, out_path, **kw) -> str:
    """
    Agnes AI。默认模型 agnes-image-2.5-flash。

    两个坑（实测踩过）：
    1. 必须显式传 response_format=b64_json。默认返回的 CDN 链接是私有桶，
       直接下载会 403 Forbidden。
    2. 服务偶发 503「image queue is full」，需要重试。
    """
    key = cfg("AGNES_API_KEY")
    base = cfg("AGNES_BASE_URL")
    if not key:
        found = _from_workbuddy_models("agnes-ai.com")
        if found:
            key, base = found["api_key"], base or found["base_url"]
    if not key:
        raise ProviderError("缺少 AGNES_API_KEY（也可在 WorkBuddy 模型设置里配置 Agnes）")
    base = (base or "https://apihub.agnes-ai.com/v1").rstrip("/")
    model = kw.get("model") or cfg("AGNES_MODEL", "agnes-image-2.5-flash")

    w, h = _parse_size(size)
    body: dict[str, Any] = {
        "model": model, "prompt": prompt, "n": 1,
        "size": "1K" if max(w, h) <= 1024 else ("2K" if max(w, h) <= 2048 else "4K"),
        "ratio": _closest_ratio(w, h),
        "response_format": "b64_json",
    }
    if seed is not None:
        body["seed"] = int(seed)
    if ref_image:
        with open(ref_image, "rb") as f:
            body["image"] = "data:image/png;base64," + base64.b64encode(f.read()).decode()

    last = None
    for attempt in range(8):
        try:
            data = _request_json(base + "/images/generations", method="POST",
                                 headers={"Authorization": f"Bearer {key}"}, body=body)
        except ProviderError as e:
            last = e
            if "503" in str(e) or "queue is full" in str(e):
                # 高峰期排队会连续 503，退避久一点比换平台划算
                time.sleep(min(30, 6 * (attempt + 1)))
                continue
            raise
        item = (data.get("data") or [{}])[0]
        b64 = item.get("b64_json")
        if b64:
            _write(out_path, base64.b64decode(b64))
            return out_path
        if item.get("url"):
            # 兜底：部分模型不认 response_format
            _download(item["url"], out_path)
            return out_path
        last = ProviderError(f"Agnes 返回结构无法识别：{json.dumps(data)[:300]}")
    raise last or ProviderError("Agnes 生成失败")


def _gen_zhipu(prompt: str, size, seed, ref_image, out_path, **kw) -> str:
    key = cfg("ZHIPU_API_KEY")
    if not key:
        raise ProviderError("缺少 ZHIPU_API_KEY")
    model = kw.get("model") or cfg("ZHIPU_MODEL", "cogview-3-flash")
    w, h = _parse_size(size)
    body = {"model": model, "prompt": prompt,
            "size": f"{min(w,1440)}x{min(h,1440)}"}
    data = _request_json("https://open.bigmodel.cn/api/paas/v4/images/generations",
                         method="POST",
                         headers={"Authorization": f"Bearer {key}"}, body=body)
    item = (data.get("data") or [{}])[0]
    if item.get("url"):
        _download(item["url"], out_path)
        return out_path
    raise ProviderError(f"智谱返回结构无法识别：{json.dumps(data)[:300]}")


def _gen_local(prompt: str, size, seed, ref_image, out_path, **kw) -> str:
    """
    离线占位：不联网，画一张测试用线稿。
    用途是让你在没有密钥时也能把后续的动画管线跑通。
    """
    from PIL import Image, ImageDraw
    w, h = _parse_size(size)
    im = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.rectangle([w * .10, h * .10, w * .90, h * .90], outline=(40, 40, 40), width=3)
    d.ellipse([w * .34, h * .18, w * .66, h * .50], outline=(40, 40, 40), width=3)
    d.line([w * .50, h * .50, w * .50, h * .74], fill=(40, 40, 40), width=3)
    d.line([w * .34, h * .60, w * .66, h * .60], fill=(40, 40, 40), width=3)
    d.line([w * .50, h * .74, w * .36, h * .90], fill=(40, 40, 40), width=3)
    d.line([w * .50, h * .74, w * .64, h * .90], fill=(40, 40, 40), width=3)
    im.save(out_path)
    print(f"[local] 离线占位图（未调用任何生图模型）：{out_path}")
    return out_path


def _closest_ratio(w: int, h: int) -> str:
    target = w / max(h, 1)
    best, bd = "1:1", 1e9
    for name, (a, b) in {"1:1": (1, 1), "4:3": (4, 3), "3:4": (3, 4),
                         "16:9": (16, 9), "9:16": (9, 16), "3:2": (3, 2),
                         "2:3": (2, 3)}.items():
        d = abs(a / b - target)
        if d < bd:
            best, bd = name, d
    return best


def _write(path: str, data: bytes):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


REGISTRY = {
    "modelscope": (_gen_modelscope, ["MODELSCOPE_API_TOKEN"],
                   "魔搭，2000 次/天，国内直连，支持图生图"),
    "agnes": (_gen_agnes, ["AGNES_API_KEY"],
              "Agnes AI，可复用 WorkBuddy 已配的 Key，支持图生图（较慢）"),
    "cloudflare": (_gen_cloudflare, ["CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"],
                   "Workers AI，10000 Neurons/天，UTC 0 点重置"),
    "zhipu": (_gen_zhipu, ["ZHIPU_API_KEY"],
              "CogView-3-Flash，免费但仅文生图，不支持锚点图"),
    "local": (_gen_local, [], "离线占位图，不联网，仅用于跑通管线"),
}

# 能把锚点参考图一起传进去的平台。图生图会让模型大量照搬参考图内容，
# 所以配方里「只继承风格、不复制内容」那句必须写清楚，锚点图也尽量选内容中性的。
SUPPORTS_REFERENCE = {"modelscope", "agnes"}

DEFAULT_ORDER = ["modelscope", "agnes", "cloudflare", "zhipu", "local"]


@dataclass
class ProviderStatus:
    name: str
    ready: bool
    missing: list[str]
    note: str


def _missing_keys(name: str, keys: list[str]) -> list[str]:
    missing = [k for k in keys if not cfg(k)]
    if name == "agnes" and missing and _from_workbuddy_models("agnes-ai.com"):
        missing = []          # 复用 WorkBuddy 模型设置里的 Key
    return missing


def status() -> list[ProviderStatus]:
    out = []
    for name, (_fn, keys, note) in REGISTRY.items():
        missing = _missing_keys(name, keys)
        out.append(ProviderStatus(name, not missing, missing, note))
    return out


def generate(prompt: str, out_path: str, provider: str = "auto",
             size: str | tuple[int, int] = "1024x1024", seed: int | None = None,
             ref_image: str | None = None, model: str | None = None) -> str:
    """按 provider 生成一张图。provider='auto' 时按 DEFAULT_ORDER 挑第一个就绪的。"""
    if ref_image and not os.path.isfile(ref_image):
        raise ProviderError(f"锚点图不存在：{ref_image}")

    order = DEFAULT_ORDER if provider == "auto" else [provider]
    if provider != "auto" and provider not in REGISTRY:
        raise ProviderError(f"未知平台：{provider}（可用：{', '.join(REGISTRY)}）")

    last_err = None
    for name in order:
        fn, keys, _note = REGISTRY[name]
        if name == "local" and provider == "auto":
            continue
        missing = _missing_keys(name, keys)
        if missing:
            last_err = ProviderError(f"{name} 缺少 {'、'.join(missing)}")
            continue
        if ref_image and name not in SUPPORTS_REFERENCE and provider == "auto":
            continue
        try:
            return fn(prompt, size, seed, ref_image, out_path, model=model)
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"[warn] {name} 失败：{e}", file=sys.stderr)
    raise ProviderError(
        f"没有可用的生图平台。最后一个错误：{last_err}\n"
        f"提示：先设置密钥，或临时用 --provider local 跑通管线。"
    )


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="免费生图平台适配器")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("status", help="查看各平台密钥是否就绪")

    g = sub.add_parser("gen", help="生成一张图")
    g.add_argument("--provider", default="auto")
    g.add_argument("--prompt", required=True)
    g.add_argument("--size", default="1024x1024")
    g.add_argument("--seed", type=int)
    g.add_argument("--ref", help="锚点参考图")
    g.add_argument("--model")
    g.add_argument("-o", "--out", default="out.png")

    a = ap.parse_args()
    if a.cmd == "status" or a.cmd is None:
        print(f"配置文件：{CONFIG_PATH}{'（存在）' if os.path.isfile(CONFIG_PATH) else '（不存在）'}\n")
        for s in status():
            mark = "就绪" if s.ready else "缺 " + "、".join(s.missing)
            print(f"  {s.name:<12} {mark:<34} {s.note}")
    elif a.cmd == "gen":
        p = generate(a.prompt, a.out, provider=a.provider, size=a.size,
                     seed=a.seed, ref_image=a.ref, model=a.model)
        print(p)
