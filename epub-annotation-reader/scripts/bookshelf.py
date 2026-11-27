#!/usr/bin/env python3
"""Build a portable, offline search and comparison page from annotated HTML readers."""
import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

DATA_START = b'<script type="application/json" id="rd-data">'
DATA_END = b"</script>"
SHELF_MARK = '<meta name="application-name" content="epub-note-shelf">'
TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "shelf.html"


def reader_data(path):
    """Only read up to the metadata, leaving embedded image data on disk."""
    with path.open("rb") as stream:
        tail = b""
        collecting = False
        parts = []
        while chunk := stream.read(128 * 1024):
            chunk = tail + chunk
            if not collecting:
                start = chunk.find(DATA_START)
                if start < 0:
                    tail = chunk[-(len(DATA_START) - 1):]
                    continue
                chunk = chunk[start + len(DATA_START):]
                collecting = True
            end = chunk.find(DATA_END)
            if end >= 0:
                parts.append(chunk[:end])
                result = json.loads(b"".join(parts))
                if not isinstance(result, dict) or not isinstance(result.get("bookId"), str) or not isinstance(result.get("annotations"), list):
                    raise ValueError(f"{path.name} 的阅读器数据不完整")
                return result
            keep = len(DATA_END) - 1
            parts.append(chunk[:-keep])
            tail = chunk[-keep:]
    if collecting:
        raise ValueError(f"{path.name} 的阅读器数据不完整")
    return None


def checked_note(note, book_id):
    if (not isinstance(note, dict) or note.get("bookId") != book_id
            or not isinstance(note.get("id"), str) or not note["id"]
            or not isinstance(note.get("quote"), dict)
            or not isinstance(note["quote"].get("exact"), str)
            or not isinstance(note.get("chapter"), str)
            or not isinstance(note.get("paragraphId"), str)):
        raise ValueError("批注缺少有效的书籍、编号、引文或段落定位")
    return note


def timestamp(value):
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        raise ValueError("备份修改时间无效")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


def merge_notes(book_id, originals, backups):
    notes = {item["id"]: item for item in map(lambda n: checked_note(n, book_id), originals)}
    changes = {}
    added = {}
    for backup in backups:
        records = backup.get("changes", {})
        if not isinstance(records, dict) or not isinstance(backup.get("annotations"), list):
            raise ValueError("请选择从阅读器导出的 JSON 备份")
        for item in backup["annotations"]:
            note = checked_note(item, book_id)
            key = note["id"]
            if key not in notes:
                date = timestamp(note.get("updatedAt") or backup.get("exportedAt"))
                if key not in added or date >= added[key][0]:
                    added[key] = date, note
        for key, record in records.items():
            if not isinstance(record, dict) or "note" not in record or "updatedAt" not in record:
                raise ValueError("个人批注修改记录不完整")
            note = record["note"]
            if note is not None and checked_note(note, book_id)["id"] != key:
                raise ValueError("个人批注的记录编号不一致")
            date = timestamp(record["updatedAt"])
            if key not in changes or date >= changes[key][0]:
                changes[key] = date, note
    notes.update((key, item) for key, (_, item) in added.items())
    for key, (_, item) in changes.items():
        if item is None:
            notes.pop(key, None)
        else:
            notes[key] = item
    return notes.values()


def build_shelf(readers, output, backups=()):
    output = Path(output).resolve()
    if output.exists():
        with output.open("rb") as file:
            if SHELF_MARK.encode() not in file.read(2048):
                raise ValueError(f"输出已存在且不是笔记书架，请另选文件名：{output}")
    readers = list(dict.fromkeys(Path(p).resolve() for p in readers))
    grouped = {}
    for path in dict.fromkeys(Path(p).resolve() for p in backups):
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or not isinstance(data.get("bookId"), str):
            raise ValueError(f"{path.name} 缺少 bookId，请使用阅读器导出的 JSON 备份")
        grouped.setdefault(data["bookId"], []).append(data)
    seen = set()
    books = []
    rows = []
    skipped = []
    for path in readers:
        if path == output:
            continue
        data = reader_data(path)
        if data is None:
            skipped.append(path.name)
            continue
        book_id = data["bookId"]
        if book_id in seen:
            raise ValueError(f"发现同一本书的两个阅读版（{path.name}），请只选择需要的版本")
        seen.add(book_id)
        book_index = len(books)
        books.append({
            "title": data["title"], "creator": data.get("creator", ""),
            "href": "./" + quote(os.path.relpath(path, output.parent).replace(os.sep, "/")),
            "noteLinks": data.get("noteLinks") == 1,
            "chapters": {c["id"]: c["title"] for c in data["chapters"]},
        })
        for note in merge_notes(book_id, data["annotations"], grouped.pop(book_id, [])):
            rows.append({
                "book": book_index, "id": note["id"], "chapter": note["chapter"],
                "p": note["paragraphId"], "start": (note.get("position") or {}).get("start"),
                "quote": note["quote"]["exact"], "source": note.get("author", "ai"),
                "edited": note.get("editedByMe") is True, "type": note.get("type", "note"),
                **{k: note.get(k, "") for k in ("claim", "note", "tag", "question")},
            })
    if not books:
        raise ValueError("没有找到可索引的 HTML 阅读版")
    if grouped:
        raise ValueError("有备份缺少配套的 HTML 阅读版，请一并选择：" + "、".join(grouped))
    payload = json.dumps({"books": books, "rows": rows}, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    content = TEMPLATE.read_text(encoding="utf-8").replace("<!--INDEX-->", payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=output.parent, encoding="utf-8", delete=False) as handle:
        temp = Path(handle.name)
        try:
            handle.write(content)
            handle.close()
            temp.replace(output)
        finally:
            temp.unlink(missing_ok=True)
    return {"output": str(output), "books": len(books), "notes": len(rows), "skipped": skipped}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="阅读版 HTML 或其所在目录")
    parser.add_argument("--output", required=True, type=Path, help="书架 HTML 的保存位置")
    parser.add_argument("--backups", type=Path, nargs="+", default=[], help="要合并的个人批注 JSON 备份")
    args = parser.parse_args()
    try:
        paths = []
        for path in args.inputs:
            if path.is_dir():
                paths.extend(sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() == ".html"))
            else:
                paths.append(path)
        print(json.dumps(build_shelf(paths, args.output, args.backups), ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"书架生成失败：{error}\n")


if __name__ == "__main__":
    main()
