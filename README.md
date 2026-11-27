# EPUB 转 HTML · AI 批注阅读器

带着一个问题读一本书。把原生 EPUB 交给 AI 助手，说明你想读懂什么、每次有多少时间。它从原书选段，标出作者的观点、论据和适用条件，生成一个双击就能打开的轻量 HTML 阅读器。正文、批注、路线和练习题都能离线使用。

**试试看**　下载原创样书《窗边摄影手记》。打开「路线」，从“在同一地点找出画面的变化”跳进原文，再到「卡片 → 考考我」闭卷回忆。样书包含两个阅读单元、六条批注和四道自测题。[下载示例 HTML](examples/reader-demo.html?raw=1)

## 把一本书读成可复用的笔记

| 你想做的事 | 怎么使用 |
| --- | --- |
| 用手头的十几分钟解决一个问题 | 说出阅读目标，选择快速了解、系统学习或找素材。每个阅读单元都有选读理由、估计用时和可跳转的原文。路线会列出尚待解答的问题。 |
| 先判断 AI 选得好不好 | 先做一份试读。在「路线 → 校准阅读偏好」写下“少讲定义，多选反例”，导出偏好交给助手。也可以直接在对话中反馈，再让助手完成全书。偏好文件可以带到下一本书。 |
| 看懂证据，保留自己的判断 | 引文、作者观点与 AI 解读分别展示。单击回到原句，上下文仍在；也可暂时隐藏 AI 批注，自己划线、写笔记、加段落书签。 |
| 检验自己是否记住 | 「卡片」按瀑布流浏览。「考考我」先出题，展开后看原文与解读，再选“已掌握”或“再看一次”。下次优先复习未掌握的题，也可以重温全部。个人笔记可自行添加题目。 |
| 跨几本书核对同一个概念 | 离线书架可按关键词、书名和来源筛选。选择两条笔记并排比较，查看作者、章节和引文，再回到各自原书。新版支持定位原句，旧阅读版可打开对应章节。 |

## 作为 Skill 使用

下载或克隆本仓库，从仓库根目录运行：

```sh
mkdir -p "$HOME/.agents/skills/epub-annotation-reader"
cp -R epub-annotation-reader/. "$HOME/.agents/skills/epub-annotation-reader/"
```

在支持该目录的 AI 助手中附上 EPUB，用自己的问题开始。例如：

> 使用 $epub-annotation-reader 精读这两本书。我想弄清两位作者怎样判断一个案例能否推广，每次读 15 分钟。先给我一份试读，重点保留论据和例外。确认方向后做完整阅读版，再生成可以跨书对照的笔记书架。

AI 助手负责审阅和撰写；转换器核验引文与位置，报告无法定位的记录。可以直接要求一次完成，也可以只转换格式，自己做笔记。修改反馈后，请把反馈交给助手重新生成阅读版；离线网页本身无需连接模型。

## 直接转换与更新书架

转换器使用 Python 3 标准库。在仓库根目录运行：

```sh
python3 epub-annotation-reader/scripts/epub_reader.py convert "我的书.epub" --out-dir "阅读版"
python3 epub-annotation-reader/scripts/epub_reader.py convert "EPUB目录" --annotations "批注目录" --out-dir "阅读版" --shelf
```

第一条命令可直接转换原书。已有批注时，可在 EPUB 旁放置 `原书名.annotations.json`。第二条命令读取每本书的同名批注，并生成 `笔记书架.html`。在输入 JSON 中加入 `study` 可以指定路线与试读阶段，省略它时保留普通阅读体验。[批注与路线格式](epub-annotation-reader/references/annotations.md)

**把后来写的笔记放进书架。** 分别从各本阅读器导出 JSON，再重新生成书架。书架汇总生成时的笔记，包括备份里的新增、编辑和删除记录。移动到其他设备时，请一起复制书架与原书 HTML，保留它们的相对位置。

```sh
python3 epub-annotation-reader/scripts/bookshelf.py "阅读版" --output "阅读版/笔记书架.html" --backups "甲书备份.json" "乙书备份.json"
```

支持无 DRM 的可重排 EPUB 2/3。浏览器在本地暂存个人笔记、阅读位置和复习状态。换浏览器、清理数据或重建阅读版前，请在「批注」中导出 JSON；导入同一本书的新版即可恢复。另可导出 Markdown，在 Obsidian 等工具中整理。

[Skill 工作流程](epub-annotation-reader/SKILL.md) · [转换器源码](epub-annotation-reader/scripts/epub_reader.py)
