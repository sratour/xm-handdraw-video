# 角色库

固定角色的姿态库。一个角色一个目录，目录里存切好的单张立绘 + 一份档案。
出片时用 `--character <角色id>` 直接调，不用每次翻文件夹找图。

```
assets/char/
├── README.md              ← 你在这里
├── laoqin/                ← 老勤 · 沙丁国渔夫
│   ├── card.md
│   ├── manifest.json
│   └── poses/01-stand.png
├── office-lady/           ← 职场秘书 · 棕色马尾
│   ├── card.md            # 角色档案（人看）
│   ├── manifest.json      # 索引（脚本读）
│   ├── _contact.png       # 六姿态联系表
│   ├── source-01.jpg      # 原始设定图
│   ├── source-02.jpg
│   └── poses/
│       ├── 01-front-hold-papers.png
│       └── ...
├── ref_crop.png           # 早期测试图，不属于角色库
├── ref_single.png
└── ref_small.jpg
```

## 现有角色

| id | 名称 | 画风 | 姿态数 | 档案 |
|---|---|---|---|---|
| `office-lady` | 职场秘书 · 棕色马尾 | `anime-jp-flat` | 6 | [card.md](office-lady/card.md) |
| `laoqin` | 老勤 · 沙丁国渔夫 | `anime-jp-flat` | 1 | [card.md](laoqin/card.md) |

```bash
python scripts/char_lib.py list          # 列全部角色和姿态
python scripts/char_lib.py list --json   # 给脚本读
```

## 怎么调

```bash
# 角色默认姿态
--character office-lady

# 指定姿态：序号或文件名前缀
--character office-lady:04
--character office-lady:04-threequarter

# 也可以直接给路径
--character assets/char/office-lady/poses/02-front-arms-crossed.png
```

`--character` 只认**一张**图。所以一个角色目录里同时承担两件事：
锁人物（长相、发型、着装）和锁画风（线条、上色）。换画风就得换角色图，
不能用 `--anchor` 另挂一张——参考图只有一个名额，`--character` 会顶掉 anchor。

## 怎么加角色

1. 准备好横排的设定图（同一角色的多个姿态排在一行，姿态之间留白）
2. 切图入库：

```bash
python scripts/char_lib.py extract 设定图.png --id my-char \
  --names 01-front,02-side,03-back
```

3. **逐张打开看**。切图的坑都在边缘：相邻姿态离得近时，一角会被带进来。
   脚本会自动清理贴边碎块，但还是要人眼过一遍。
4. 手写 `manifest.json`（照 `office-lady/manifest.json` 抄结构），
   补齐 `traits`、每个姿态的 `prompt`，定一个 `default_pose`
5. 写 `card.md`，在上面「现有角色」表里加一行

### 切图的三个参数

| 参数 | 默认 | 什么时候改 |
|---|---|---|
| `--gap` | 6 | 两个姿态被并成一张 → 调小；一个姿态被切成两半 → 调大 |
| `--threshold` | 28 | 纸底不是浅色（深色背景的设定图）→ 调大；浅色衣服被当成背景切掉 → 调小 |
| `--pad` | 24 | 立绘四周留白太窄 → 调大 |

`--alpha` 输出透明背景 PNG（从四边泛洪，不会挖穿白衬衫）。

### 文件命名

- 姿态文件名用 `两位序号-英文短名.png`，序号决定 `--character id:02` 里的数字
- 全小写、连字符，不用中文和空格——命令行里省事
- 角色 id 同样用 `小写-连字符`，和目录名一致

## 注意

- 角色图不能是受著作权保护的现成角色。可以模仿画风，不能复制角色
- 用 `--character` 时 subject 里写死「画面里只有主角一个人」，否则一张图里会出现好几个主角
- 空镜（画面里没人）不要带 `--character`
