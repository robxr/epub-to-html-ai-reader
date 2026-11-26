# EPUB 批注阅读器

把一本 EPUB 变成一个能离线打开的 HTML。读书时在段落旁写想法，复习时点开「卡片」，一眼浏览全书笔记。遇到记不清的上下文，点一下卡片就能跳回当时划线的那句话。

**体验示例**　下载《窗边摄影手记》的 HTML，用浏览器打开，点顶部「卡片」。六张示例卡片涵盖观察笔记、书签和练习题，正文与批注均为本项目原创。[下载示例 HTML](examples/reader-demo.html?raw=1)

## 从阅读到复习

| 想做什么 | 在阅读器里怎么做 |
| --- | --- |
| 读一章并留下想法 | 选中同一段内的文字，选择高亮色或点「写笔记」。双栏模式将批注放在对应段落旁。 |
| 快速找回某处 | 点右侧色点或笔记列表跳回原文。给当前段落加书签后，段首会出现横线和书签图标。 |
| 集中复习 | 点「卡片」查看引文、笔记、章节和来源。卡片按需加载，随屏宽排成一至三列，短卡片下方可以接着排下一张。 |
| 分清自己的想法和 AI 解读 | 在批注面板按来源筛选，或在外观设置中隐藏 AI 批注，先独立阅读。 |
| 带走笔记 | 导出 Markdown 放进 Obsidian，或导出 JSON，在另一台设备打开同一版阅读器时导入备份。 |

## 作为 Skill 使用

下载或克隆本仓库，进入仓库根目录。在使用用户级通用技能目录的环境中，可以这样安装或更新：

```sh
mkdir -p "$HOME/.agents/skills/epub-annotation-reader"
cp -R epub-annotation-reader/. "$HOME/.agents/skills/epub-annotation-reader/"
```

在支持该目录的 Agent 中，附上一本书或指定本机路径，然后输入：

> 使用 $epub-annotation-reader 把这本 EPUB 转为离线 HTML。每章选出值得复习的观点，为每条批注保留准确的原文引文。

Agent 负责阅读与撰写解读。转换器逐条比对引文，只嵌入可以定位的批注，并报告找不到或无法唯一定位的引文。

## 直接用命令行转换

转换器只使用 Python 标准库。在仓库根目录运行以下命令，将示例路径换成自己的文件或目录。

```sh
python3 epub-annotation-reader/scripts/epub_reader.py convert "我的书.epub" --out-dir "阅读版"
python3 epub-annotation-reader/scripts/epub_reader.py convert "EPUB目录" --out-dir "阅读版"
```

对已有批注的转换，可在 EPUB 旁放置同名的批注文件，例如 `我的书.annotations.json`，或通过 `--annotations` 指定文件。详情见[批注格式和引文校验规则](epub-annotation-reader/references/annotations.md)。

## 文件与保存

支持无 DRM、可重排的 EPUB 2/3。输出将正文、目录、图片和阅读器打包在同一个 HTML 中。浏览器会在本地暂存个人改动，迁移文件或清除浏览器数据前，请用「批注 → 导出 JSON」保存笔记与阅读进度。

[技能工作流程](epub-annotation-reader/SKILL.md) · [转换器源码](epub-annotation-reader/scripts/epub_reader.py)
