# 字体许可声明

本目录的 `DouyinSans.otf` 为 **抖音美好体**，不是本项目原创，按原许可随附分发。

| 项 | 内容 |
|---|---|
| 字体名 | 抖音美好体 / Douyin Sans（Bold） |
| 版权方 | 北京字跳网络技术有限公司、北京北大方正电子有限公司 |
| 许可协议 | SIL Open Font License 1.1（OFL-1.1） |
| 许可全文 | 同目录 `OFL.txt` |
| 官方说明 | https://openfontlicense.org |

## OFL 允许什么

- 免费商用、免费分发、嵌入到软件或文档中
- 修改字体（但**修改版不得继续使用保留字体名** "Douyin Sans"）

## 使用本项目时注意

本项目只把字体当作**字幕渲染资源**调用，未做任何字形修改。
如果你的用途对字体有特殊要求，请自行核对 OFL 原文，或换用其他字体：

1. 把新字体放进 `assets/fonts/`
2. 改 `scripts/compose.py` 的 `FONT_NAME` 为该字体的 family 名

目录为空时会自动回退系统黑体（Microsoft YaHei），不影响功能。
