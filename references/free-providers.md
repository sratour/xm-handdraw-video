# 免费生图平台

密钥读取顺序：**环境变量 → `~/.xm-handdraw/config.json` → WorkBuddy 的 `~/.workbuddy-ai/models.json`**。

最后一级是为了复用你已经配好的服务——比如 Agnes 的 Key 如果已经在
WorkBuddy 的「模型设置」里填过，这里会自动读出来，不用再填一次。

单独配置的写法：

```json
{
  "MODELSCOPE_API_TOKEN": "ms-xxxxxxxx",
  "CLOUDFLARE_API_TOKEN": "xxxxxxxx",
  "CLOUDFLARE_ACCOUNT_ID": "xxxxxxxx",
  "AGNES_API_KEY": "sk-xxxxxxxx",
  "ZHIPU_API_KEY": "xxxxxxxx"
}
```

查当前状态：

```bash
python scripts/xm_handdraw.py providers
```

---

## 各平台对比

| 平台 | 免费额度 | 生图模型 | 锚点图 | 实测速度 |
|---|---|---|---|---|
| **agnes** | 官方称「无限期免费」 | agnes-image-2.5 / 2.1 / 2.0-flash | 支持 | 文生图 ~11s；图生图 2 分钟以上 |
| **modelscope** | 2000 次/天，单模型另有日额度 | Z-Image-Turbo、Qwen-Image-Edit 等 | 支持 | 异步任务，约 14s |
| **cloudflare** | 10,000 Neurons/天，UTC 0 点重置 | flux-1-schnell 等 | 不支持 | 较快 |
| **zhipu** | Flash 系列免费，限并发 | cogview-3-flash | 不支持 | 快 |
| **local** | 无限，但不联网 | 离线占位线稿 | — | 瞬时 |

---

## agnes

同一套接口下还挂着 `agnes-video-2.5`、`agnes-video-2.5-flash` 两个视频模型，
以及 `agnes-2.5-flash` / `agnes-3.0-flash` 等对话模型。**本项目只用生图模型。**

### 两个实测踩到的坑

**1. 必须显式传 `response_format: "b64_json"`。**

不传的话，接口返回的是一个 CDN 链接（`platform-outputs.agnes-ai.space/...`），
但这个桶是私有的——直接下载返回 **403 Forbidden**，带不带 Authorization 都一样。
而且此时 `b64_json` 字段是个空字符串，很容易误判成「模型没返回图」。

```json
{
  "model": "agnes-image-2.5-flash",
  "prompt": "...",
  "n": 1,
  "size": "1K",
  "ratio": "1:1",
  "response_format": "b64_json"
}
```

**2. 图生图经常 503。**

返回 `{"error":{"message":"image queue is full, please retry later"}}`。
实测连续 3 次失败、第 4 次成功，成功那次耗时 134 秒。脚本已内置 4 次退避重试。

### 尺寸参数

用 `size`（`1K` / `2K` / `4K`）+ `ratio`（`1:1` / `4:3` / `16:9` …）两档，
不是像素尺寸。脚本会把 `--size 1024x1024` 自动换算成 `1K` + `1:1`。

### 图生图的注意事项

`image` 字段接受 `data:image/png;base64,...`。但实测发现
**模型会大量照搬参考图里的具体内容**——拿一张「女孩放风筝」当锚点，
新画面里就真的多出一个风筝。

所以两件事必须做：

1. 锚点图尽量选**内容中性**的（单人、素背景的角色设定图），不要选带独特道具的场景图
2. 配方正文里「只继承风格，不复制参考图的人物身份、服装、站位或情节」那句一定要写

```bash
python scripts/xm_handdraw.py draw --style <id> --subject "..." --provider agnes -o shot.png
```

换模型：

```bash
... --provider agnes --model agnes-image-2.0-flash
```

---

## modelscope

异步任务模式：先提交拿 `task_id`，再轮询 `/v1/tasks/{task_id}`，状态 `SUCCEED`
后从 `output_images[0]` 下载。

```bash
export MODELSCOPE_API_TOKEN=ms-xxxx
python scripts/xm_handdraw.py draw --style retro-mystery-manga \
       --subject "一个少年侦探站在走廊上" --provider modelscope -o shot.png
```

## cloudflare

模型名带 `@cf/` 前缀。flux-1-schnell 只接受 512 / 1024 两个方向，
脚本会把尺寸自动吸附到最近的合法值。英文提示词效果更好。

```bash
export CLOUDFLARE_API_TOKEN=xxxx
export CLOUDFLARE_ACCOUNT_ID=xxxx
```

## zhipu

CogView-3-Flash 免费但**只支持文生图**，且尺寸上限较低。
适合快速抽卡，不适合需要锚点图定风格的场景。

---

## 已排除的选项

**Pollinations.AI** —— 网上大量教程仍推荐它「无需注册、无需 Key」，
但 2026-10 实测已不可用：`image.pollinations.ai` 返回 402 Payment Required，
`gen.pollinations.ai` 对新提示词返回 401 Unauthorized，
只有此前被缓存过的 URL 还能返回 200（是旧缓存图，不是新生成）。不要再依赖它。

**本地扩散模型** —— 本机无独立显卡，纯 CPU 跑 SD1.5 约 1~3 分钟/张（512px），
内存也只有 7.8 GB，跑 FLUX 级别无望。不作为方案。

---

## 免费额度用完了怎么办

按顺序试：

1. 换一个平台（`--provider` 指定，或让 `auto` 自动挑下一个就绪的）
2. 降低抽卡频率：先用 `local` 或低分辨率确认构图，定型后再用免费额度出正式图
3. 用 WorkBuddy 内置生图顶一下（按积分计费，不是免费，但零配置）
4. 如实告诉用户额度用尽，不要静默降级或假装成功

额度规则会变，用之前最好先跑一次 `providers` 命令确认。
