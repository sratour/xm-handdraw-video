# 锚点图目录

每个画风一个子目录，里面放一张 `anchor.png`。

```
assets/
├── retro-mystery-manga/
│   └── anchor.png
└── fonts/
    └── DouyinSans.otf
```

## 锚点图是什么

一张最能代表该画风的图。**每次生图时随请求一起传给生图平台**，
让新画面向它收敛。这是风格稳定性的命门——纯文字提示词做不到稳定。

## 怎么生成

用配方正文跑 30~50 张（换不同 seed 和主体），逐张看，
挑**线条、五官、比例、上色**四项都最对味的一张存下来。

判断标准：把它和任意一张候选图放一起看，像不像同一个画师画的。

## 注意

- 锚点一旦定下就**不要换**。换锚点等于换画风，之前所有画面都要重做
- 只有 `modelscope` 平台支持把锚点图一起传进去；其他平台会降级为纯提示词
- 锚点图只提供**风格**，不要让新画面复制它里面的人物身份、服装、站位、情节
- 生成锚点图时不要用受著作权保护的现成角色形象

---

# 字体目录 fonts/

字幕用的字体放这里。**不需要装进系统**——`compose.py` 会把整个目录通过
libass 的 `fontsdir` 参数传给 ffmpeg，直接加载。

当前内置：`DouyinSans.otf`（抖音美好体，family 名 `Douyin Sans`）。

## 换字体

1. 把 `.otf` / `.ttf` 丢进 `fonts/`
2. 改 `scripts/compose.py` 里的 `FONT_NAME` 成该字体的 **family 名**

family 名不是文件名，这样查：

```python
from PIL import ImageFont
print(ImageFont.truetype("新字体.otf", 40).getname())   # ('family名', 'Bold')
```

填错不会报错，只会**静默回退系统黑体**。验证要看 libass 的选择日志：

```bash
ffmpeg -loglevel verbose -t 1 -i in.mp4 -vf "subtitles=...:fontsdir=..." -f null - 2>&1 | grep fontselect
```

`fonts/` 为空时自动用 `Microsoft YaHei` 兜底。
