# 生成与迁移批注

已有同书的精读索引时复用审阅过的批注。需要生成新批注时，先拿 EPUB 当前正文作为引文唯一来源。

```bash
python3 scripts/epub_reader.py extract "书.epub"
python3 scripts/epub_reader.py extract "书.epub" --chapter "c-从输出复制的完整 ID"
```

以逻辑章节为单位阅读，短小拆分文件可以合在一起。先做一章验证。每章通常 5–12 条，短章可少于 5 条。选择核心观点、概念解释、案例背景、适用边界、跨章节关联和思考题。针对投资书，写清作者在什么前提下得出判断，术语如何理解，以及读者可用什么问题检验。区分作者原意与解读。未经核实的书外事实省略或明确写为“待核实”。根据用户要求处理范围，批量完成后回报每本的实际批注数量。

使用如下 JSON。逐字复制 `quote.exact`，保留标点和内部空白，选择一个段落内的确切引文。若同段重复，补充前后文，或提供 JavaScript UTF-16 偏移。`id` 应稳定，用于重新转换时覆盖对应的预置 AI 批注；避免复用编号去代表一条无关的批注。浏览器内对预置 AI 批注的编辑、删除和个人笔记是独立改动，在同一本书重建时会覆盖预置记录。

```json
{
  "bookId": "extract 返回的 bookId",
  "annotations": [
    {
      "id": "ai-ch01-01",
      "chapter": "extract 返回的章节 ID",
      "paragraphId": "extract 返回的段落 ID",
      "quote": {"exact": "逐字原文", "prefix": "", "suffix": ""},
      "author": "ai",
      "type": "note",
      "tag": "核心观点",
      "color": "yellow",
      "note": "基于这段原文的解读"
    }
  ]
}
```

批注可用颜色为 `yellow`、`green`、`blue`、`pink`、`purple`。构建时会逐条核对并补齐上下文及位置。引文缺失、模糊或匹配同样可能的位置会拒收。浏览器备份导入会保留因正文改版而无法定位的笔记，在“待定位”下显示。个人编辑和删除应通过浏览器的 JSON 备份迁移，因为备份同时携带删除记录。作为构建输入的 JSON 只读取其中的 `annotations`。
