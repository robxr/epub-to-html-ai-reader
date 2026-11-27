#!/usr/bin/env python3
"""Convert reflowable EPUB 2/3 books and validated annotations to offline HTML."""
from __future__ import annotations

import argparse
import base64
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from hashlib import sha256
from html import escape
from html.parser import HTMLParser
import json
import mimetypes
from pathlib import Path
import posixpath
import re
import tempfile
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

ASSETS = Path(__file__).resolve().parents[1] / "assets"
FORMAT = "epub-annotation-reader/1"
CONTENT = {"application/xhtml+xml", "text/html"}
BLOCKS = set("p h1 h2 h3 h4 h5 h6 li dt dd pre caption figcaption td th blockquote div section aside address".split())
TAGS = BLOCKS | set("a abbr article b bdi bdo br cite code del dfn dl em figcaption figure footer header hr i img ins kbd main mark nav ol q rp rt ruby s samp small span strong sub sup table tbody tfoot thead tr u ul var wbr col colgroup".split())
VOID = set("img br hr wbr col area base embed input link meta param source track".split())
DROP = set("script style template iframe object embed form button input textarea select noscript canvas audio video source track".split())
SVG = set("svg g path rect circle ellipse line polyline polygon text tspan title desc defs symbol use image clipPath mask linearGradient radialGradient stop pattern".lower().split())
SVG_NAMES = {name.lower(): name for name in ("clipPath", "linearGradient", "radialGradient", "viewBox", "preserveAspectRatio", "gradientUnits", "gradientTransform", "patternUnits", "patternContentUnits", "patternTransform")}
SVG_ATTRS = set("id class role aria-label x y dx dy x1 x2 y1 y2 cx cy r rx ry width height viewbox preserveaspectratio d points transform gradientunits gradienttransform patternunits patterncontentunits patterntransform fx fy offset fill fill-opacity fill-rule stroke stroke-width stroke-opacity stroke-dasharray stroke-linejoin stroke-linecap opacity stop-color stop-opacity clip-path mask text-anchor font-size font-weight dominant-baseline".split())
ATTRS = set("class title lang dir alt width height colspan rowspan scope headers start reversed value abbr span role aria-label epub:type".split())
IMAGES = {".svg", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp", ".apng", ".ico"}
COLORS = {"yellow", "green", "blue", "pink", "purple"}
OBFUSCATION = {"http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC"}
STYLE_PROPS = set("text-align vertical-align font-style font-weight text-decoration text-indent white-space list-style-type border-collapse writing-mode ruby-position".split())
STYLE_VALUE = re.compile(r"^[\w .,%+()/-]{1,80}$", re.ASCII)


def digest(text, length=16):
    return sha256(text.encode("utf-8")).hexdigest()[:length]


def json_text(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def norm(text):
    return " ".join(text.split())


def utf16(text):
    return len(text.encode("utf-16-le")) // 2


def local_name(tag):
    return tag.rsplit("}", 1)[-1]


def xml(data):
    if re.search(rb"<!ENTITY", data, re.I):
        raise ValueError("EPUB XML 包含不支持的实体声明")
    return ET.fromstring(data)


def archive_link(base, href):
    try:
        url = urlsplit(href)
    except ValueError:
        return None
    if url.scheme or url.netloc:
        return None
    path = unquote(url.path)
    if path:
        path = posixpath.normpath(
            path.lstrip("/") if path.startswith("/") else posixpath.join(posixpath.dirname(base), path)
        )
    else:
        path = base
    if path == ".." or path.startswith("../") or "\\" in path or "\x00" in path:
        return None
    return path, unquote(url.fragment)


@dataclass(eq=False)
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)
    parent: Node | None = None
    pid: str = ""


class Markup(HTMLParser):
    def __init__(self, data):
        super().__init__(convert_charrefs=True)
        self.root = Node("#")
        self.stack = [self.root]
        self.feed(data)
        self.close()

    def handle_starttag(self, tag, attrs):
        for auto in ({"li"}, {"dt", "dd"}, {"td", "th"}, {"tr"}, {"option"}):
            if tag in auto:
                self.close_before(auto, {"ul", "ol", "dl", "table", "tr", "select"} - auto)
        if tag in BLOCKS or tag in {"table", "ol", "ul", "hr", "figure"}:
            self.close_before({"p"}, {"td", "th", "li", "table", "div", "section"})
        parent = self.stack[-1]
        node = Node(tag, {k: v or "" for k, v in attrs}, parent=parent)
        parent.children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def close_before(self, targets, boundaries):
        for index in range(len(self.stack) - 1, 0, -1):
            tag = self.stack[index].tag
            if tag in targets:
                del self.stack[index:]
                return
            if tag in boundaries:
                return

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        self.close_before({tag}, set())

    def handle_data(self, data):
        if data:
            self.stack[-1].children.append(data)


def walk(node):
    yield node
    if node.tag in DROP:
        return
    for child in node.children:
        if isinstance(child, Node) and child.tag not in DROP:
            yield from walk(child)


def raw_text(node):
    if node.tag in DROP:
        return ""
    return "".join(child if isinstance(child, str) else raw_text(child) for child in node.children)


def offset_in(parent, target):
    offset = 0
    ancestors = set(target_parents(target))
    for child in parent.children:
        if child is target:
            return offset
        if isinstance(child, str):
            offset += len(child)
        elif child in ancestors:
            return offset + offset_in(child, target)
        else:
            offset += len(raw_text(child))
    raise ValueError("批注锚点不在所属段落")


def target_parents(node):
    while node.parent:
        node = node.parent
        yield node


def read_text(data):
    enc = re.search(rb'encoding\s*=\s*["\']([\w-]+)', data[:180], re.I)
    codec = enc.group(1).decode("ascii") if enc else "utf-8-sig"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        codec = "utf-16"
    return data.decode(codec, errors="replace").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "\ufffd")


@dataclass
class Chapter:
    path: str
    key: str
    title: str
    body: Node
    blocks: dict
    source_ids: dict
    styles: list


class Book:
    def __init__(self, archive, fallback_title):
        self.archive = archive
        infos = archive.infolist()
        if len(infos) > 25000 or sum(item.file_size for item in infos) > 1024**3:
            raise ValueError("EPUB 解压体积超出 1 GiB 或文件数过多")
        if any(item.flag_bits & 1 for item in infos):
            raise ValueError("不支持加密 EPUB")
        self.files = set(archive.namelist())
        encryption = self.find_file("META-INF/encryption.xml")
        if encryption:
            records = [item for item in xml(archive.read(encryption)).iter() if local_name(item.tag) == "EncryptedData"]
            for item in records:
                methods = [sub.get("Algorithm") for sub in item.iter() if local_name(sub.tag) == "EncryptionMethod"]
                if len(methods) != 1 or methods[0] not in OBFUSCATION:
                    raise ValueError("不支持 DRM 或加密正文")
        container = self.find_file("META-INF/container.xml")
        if not container:
            raise ValueError("缺少 EPUB container.xml")
        rootfiles = [x.get("full-path") for x in xml(archive.read(container)).iter() if local_name(x.tag) == "rootfile"]
        if not rootfiles or not rootfiles[0]:
            raise ValueError("EPUB 缺少 OPF 路径")
        opf = self.find_file(rootfiles[0]) or self.find_file(unquote(rootfiles[0]))
        if not opf:
            raise ValueError("找不到 EPUB OPF")
        package = xml(archive.read(opf))
        metadata = next(x for x in package if local_name(x.tag) == "metadata")
        values = defaultdict(list)
        for child in metadata:
            values[local_name(child.tag)].append(norm("".join(child.itertext())))
        self.title = next(filter(None, values["title"]), fallback_title)
        self.lang = next(filter(None, values["language"]), "zh-CN")
        self.creator = "、".join(filter(None, values["creator"]))
        manifest = next(x for x in package if local_name(x.tag) == "manifest")
        entries = {}
        for item in manifest:
            resolved = archive_link(opf, item.get("href", ""))
            if resolved and item.get("id"):
                path = self.find_file(resolved[0]) or resolved[0]
                entries[item.get("id")] = (path, item.get("media-type", ""), item.get("properties", "").split())
        spine = next(x for x in package if local_name(x.tag) == "spine")
        ordered = []
        for item in spine:
            identifier = item.get("idref")
            if identifier not in entries:
                raise ValueError(f"书脊引用缺失：{identifier}")
            path, mime, _ = entries[identifier]
            if mime not in CONTENT and Path(path).suffix.lower() not in {".html", ".xhtml", ".htm"}:
                raise ValueError(f"不支持的书脊格式：{mime or path}")
            if path not in self.files:
                raise ValueError(f"书脊文件缺失：{path}")
            if path not in ordered:
                ordered.append(path)
        if not ordered:
            raise ValueError("EPUB 没有可转换的正文")
        paths = ordered + [path for path, mime, props in entries.values() if mime in CONTENT and "nav" not in props and path in self.files and path not in ordered]
        self.spine = set(ordered)
        toc = self.read_toc(entries, spine.get("toc"))
        self.book_id = "epub-" + digest(norm(self.title) + "\n" + norm(self.creator) + "\n" + "\n".join(p for p in ordered if Path(p).stem != "robin-reading-index"), 24)
        chapter_titles = {}
        for target, fragment, label, _ in toc:
            if label:
                chapter_titles.setdefault(target, label)
        self.chapters = {}
        self.assets = {}
        self.missing = set()
        for path in dict.fromkeys(paths):
            self.chapters[path] = self.read_chapter(path, chapter_titles.get(path))
        self.chapter_keys = {chapter.key: chapter for chapter in self.chapters.values()}
        self.toc = self.finish_toc(toc)
        self.annotations = self.read_existing_annotations()
        self.study = None

    def find_file(self, path):
        if path in self.files:
            return path
        if path.startswith("META-INF/"):
            return next((name for name in self.files if name.casefold() == path.casefold()), None)
        return None

    def read_toc(self, entries, ncx_id):
        nav = next((path for path, _, props in entries.values() if "nav" in props and path in self.files), None)
        rows = []

        def add(path, href, label, depth):
            resolved = archive_link(path, href)
            if resolved and label:
                rows.append((*resolved, label, depth))

        def nav_list(path, node, depth):
            for item in node.children:
                if not isinstance(item, Node) or item.tag != "li":
                    continue
                link = next((child for child in walk(item) if child.tag == "a" and "href" in child.attrs), None)
                if link:
                    add(path, link.attrs["href"], norm(raw_text(link)), depth)
                for child in item.children:
                    if isinstance(child, Node) and child.tag in {"ol", "ul"}:
                        nav_list(path, child, depth + 1)

        if nav:
            tree = Markup(read_text(self.archive.read(nav))).root
            toc = next((n for n in walk(tree) if n.tag == "nav" and ("toc" in n.attrs.get("epub:type", "").split() or n.attrs.get("role") == "doc-toc")), None)
            if toc:
                for node in toc.children:
                    if isinstance(node, Node) and node.tag in {"ol", "ul"}:
                        nav_list(nav, node, 0)
        if rows:
            return rows
        ncx = entries.get(ncx_id) or next((item for item in entries.values() if item[1] == "application/x-dtbncx+xml"), None)
        if ncx and ncx[0] in self.files:
            try:
                tree = xml(self.archive.read(ncx[0]))
            except ET.ParseError:
                return rows

            def visit(node, depth):
                for item in node:
                    if local_name(item.tag) != "navPoint":
                        continue
                    label = next((norm("".join(x.itertext())) for x in item if local_name(x.tag) == "navLabel"), "")
                    href = next((x.get("src", "") for x in item if local_name(x.tag) == "content"), "")
                    if href:
                        add(ncx[0], href, label, depth)
                    visit(item, depth + 1)

            for item in tree.iter():
                if local_name(item.tag) == "navMap":
                    visit(item, 0)
        return rows

    def read_chapter(self, path, toc_title):
        tree = Markup(read_text(self.archive.read(path))).root
        body = next((n for n in walk(tree) if n.tag == "body"), tree)
        head_title = next((norm(raw_text(n)) for n in walk(tree) if n.tag == "title" and norm(raw_text(n))), "")
        heading = next((norm(raw_text(n)) for n in walk(body) if n.tag in {"h1", "h2", "h3", "h4", "h5", "h6"} and norm(raw_text(n))), "")
        cid = "c-" + digest(path, 14)
        source_ids = {}
        blocks = {}
        sequence = Counter()
        nodes = list(walk(body))
        descendant_blocks = set()
        for node in reversed(nodes):
            value = norm(raw_text(node)) if node.tag in BLOCKS else ""
            if value:
                descendant_blocks.update(target_parents(node))
        for node in nodes:
            for original in (node.attrs.get("id"), node.attrs.get("xml:id"), node.attrs.get("name") if node.tag == "a" else None):
                if original:
                    source_ids.setdefault(original, node)
            if node.tag in BLOCKS and node not in descendant_blocks:
                text = norm(raw_text(node))
                if text:
                    key = digest(text, 18)
                    sequence[key] += 1
                    node.pid = f"{cid}-p-{key}-{sequence[key]}"
                    blocks[node.pid] = node
        if not blocks and norm(raw_text(body)):
            body.pid = cid + "-body"
            blocks[body.pid] = body
        styles = []
        for node in walk(tree):
            if node.tag == "link" and "stylesheet" in node.attrs.get("rel", "").lower().split():
                target = archive_link(path, node.attrs.get("href", ""))
                if target and target[0] in self.files and target[0].lower().endswith(".css"):
                    styles.append(target[0])
        return Chapter(path, cid, toc_title or heading or head_title or Path(path).stem, body, blocks, source_ids, styles)

    def dom_target(self, path, fragment):
        chapter = self.chapters.get(path)
        if not chapter:
            return ""
        if not fragment or fragment not in chapter.source_ids:
            return chapter.key
        node = chapter.source_ids[fragment]
        if node is chapter.body:
            return chapter.key
        return chapter.key + "-a-" + digest(self.source_id(node), 14)

    @staticmethod
    def source_id(node):
        return node.attrs.get("id") or node.attrs.get("xml:id") or (node.attrs.get("name", "") if node.tag == "a" else "")

    def finish_toc(self, rows):
        toc = []
        seen = set()
        for path, fragment, title, depth in rows:
            if path in self.chapters:
                target = self.dom_target(path, fragment)
                if target not in seen:
                    seen.add(target)
                    toc.append({"target": target, "title": title, "depth": depth})
        if not toc:
            return [{"target": c.key, "title": c.title, "depth": 0} for c in self.chapters.values() if c.path in self.spine]
        return toc

    def image(self, base, src):
        if not src:
            return ""
        if re.fullmatch(r"data:image/(?:png|jpe?g|gif|webp|avif|bmp|apng);base64,[a-zA-Z0-9+/=]+", src, re.I):
            key = "asset-" + digest(src, 24)
            self.assets.setdefault(key, (None, src))
            return key
        link = archive_link(base, src)
        if link and link[0] in self.files and Path(link[0]).suffix.lower() in IMAGES:
            key = "asset-" + digest(link[0], 24)
            self.assets.setdefault(key, (link[0], None))
            return key
        if link and not src.startswith("#"):
            self.missing.add(link[0])
        return ""

    def image_dimensions(self, asset):
        path, embedded = self.assets[asset]
        if embedded or Path(path).suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif"}:
            return None
        with self.archive.open(path) as stream:
            data = stream.read(65536)
        if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
            return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        if data.startswith((b"GIF87a", b"GIF89a")) and len(data) >= 10:
            return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
        if not data.startswith(b"\xff\xd8"):
            return None
        index = 2
        while index + 8 < len(data) and data[index] == 255:
            marker = data[index + 1]
            if marker == 255:
                index += 1
                continue
            if marker in {192, 193, 194, 195, 197, 198, 199, 201, 202, 203, 205, 206, 207}:
                return int.from_bytes(data[index + 7:index + 9], "big"), int.from_bytes(data[index + 5:index + 7], "big")
            length = int.from_bytes(data[index + 2:index + 4], "big")
            if length < 2:
                break
            index += 2 + length
        return None

    def read_existing_annotations(self):
        labels = {}
        for chapter in self.chapters.values():
            if Path(chapter.path).stem != "robin-reading-index":
                continue
            for node in walk(chapter.body):
                if node.tag == "a" and "href" in node.attrs:
                    target = archive_link(chapter.path, node.attrs["href"])
                    if target:
                        labels[target] = norm(raw_text(node))
        notes = []
        for chapter in self.chapters.values():
            for node in walk(chapter.body):
                source_id = self.source_id(node)
                if "robin-reading-highlight" not in node.attrs.get("class", "").split():
                    continue
                owner = next((parent for parent in (node, *target_parents(node)) if parent.pid), None)
                text = raw_text(node)
                if not owner or not norm(text):
                    continue
                detail = labels.get((chapter.path, source_id), "")
                start = offset_in(owner, node) if owner is not node else 0
                notes.append(self.make_note(chapter, owner.pid, start, text, {
                    "id": "legacy-" + digest(chapter.path + "#" + (source_id or f"{owner.pid}:{start}")),
                    "note": detail, "tag": "精读索引", "author": "ai",
                    "type": "note" if detail else "highlight", "color": "yellow",
                }))
        return notes

    def make_note(self, chapter, pid, start, exact, item):
        text = raw_text(chapter.blocks[pid])
        end = start + len(exact)
        if text[start:end] != exact:
            raise ValueError("原文引文未通过逐字校验")
        return {
            "id": str(item.get("id") or "ai-" + digest(f"{chapter.key}:{pid}:{start}:{exact}")),
            "bookId": self.book_id, "chapter": chapter.key, "paragraphId": pid,
            "quote": {"exact": exact, "prefix": text[max(0, start - 32):start], "suffix": text[end:end + 32]},
            "position": {"start": utf16(text[:start]), "end": utf16(text[:end])},
            "author": "me" if item.get("author") == "me" else "ai",
            "type": item.get("type") if item.get("type") in {"highlight", "note", "bookmark"} else "note",
            "tag": str(item.get("tag") or ""), "color": item.get("color") if item.get("color") in COLORS else "yellow",
            "note": str(item.get("note") or ""), "createdAt": str(item.get("createdAt") or ""),
            "updatedAt": str(item.get("updatedAt") or ""),
            **{field: item[field].strip() for field in ("claim", "question") if item.get(field)},
            **({"editedByMe": True} if item.get("editedByMe") is True else {}),
        }

    def import_notes(self, path):
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, dict) and payload.get("bookId") not in (None, "", self.book_id):
            raise ValueError("批注文件属于另一本书")
        records = payload.get("annotations") if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            raise ValueError("批注 JSON 必须是数组或包含 annotations 数组的对象")
        accepted = []
        rejected = []
        ids = set()
        for index, item in enumerate(records, 1):
            try:
                note = self.validate_note(item)
                if note["id"] in ids:
                    raise ValueError("同一批注文件中出现重复编号")
                ids.add(note["id"])
                accepted.append(note)
            except (ValueError, TypeError) as error:
                rejected.append({"index": index, "id": item.get("id") if isinstance(item, dict) else None, "reason": str(error)})
        if isinstance(payload, dict) and "study" in payload:
            known_ids = {note["id"] for note in self.annotations + accepted}
            self.study = validate_study(payload["study"], known_ids)
        return accepted, rejected

    def validate_note(self, item):
        if not isinstance(item, dict) or item.get("bookId") not in (None, "", self.book_id):
            raise ValueError("批注对象或 bookId 无效")
        for field in ("claim", "question"):
            if field in item and not isinstance(item[field], str):
                raise ValueError(f"{field} 必须是文本")
        quote = item.get("quote")
        quote = quote if isinstance(quote, dict) else {"exact": quote}
        exact = quote.get("exact")
        if not isinstance(exact, str) or (not exact.strip() and item.get("type") != "bookmark"):
            raise ValueError("缺少逐字原文引文")
        requested = item.get("chapter")
        chapter = self.chapter_keys.get(str(requested)) or self.chapters.get(str(requested))
        pid = item.get("paragraphId")
        if not chapter and isinstance(pid, str):
            chapter = next((c for c in self.chapters.values() if pid in c.blocks), None)
        if requested and not chapter and not any(c.title == requested for c in self.chapters.values()):
            raise ValueError("指定章节不存在")
        candidates = [chapter] if chapter else [c for c in self.chapters.values() if not requested or c.title == requested]
        matches = []
        for c in candidates:
            for key, node in c.blocks.items():
                text = raw_text(node)
                offsets = [m.start() for m in re.finditer(f"(?={re.escape(exact)})", text)] if exact else ([0] if key == pid else [])
                for start in offsets:
                    score = 10 if key == pid else 0
                    pos = item.get("position")
                    if key == pid and isinstance(pos, dict) and pos.get("start") == utf16(text[:start]):
                        score += 1
                    prefix, suffix = quote.get("prefix"), quote.get("suffix")
                    if isinstance(prefix, str) and prefix and text[:start].endswith(prefix):
                        score += 5
                    if isinstance(suffix, str) and suffix and text[start + len(exact):].startswith(suffix):
                        score += 5
                    matches.append((score, c, key, start))
        matches.sort(key=lambda match: match[0], reverse=True)
        if not matches:
            raise ValueError("逐字原文未找到")
        if len(matches) > 1 and matches[0][0] == matches[1][0]:
            raise ValueError("引文有多个同等匹配，需补充段落编号或前后文")
        _, c, key, start = matches[0]
        return self.make_note(c, key, start, exact, item)

    def render_node(self, chapter, node, inside_svg=False):
        if isinstance(node, str):
            return escape(node, quote=False)
        tag = node.tag
        if tag in DROP or tag in {"head", "title"} and not inside_svg:
            return ""
        in_svg = inside_svg or tag == "svg"
        if tag not in (SVG if in_svg else TAGS):
            return "".join(self.render_node(chapter, child, in_svg) for child in node.children)

        attrs = {SVG_NAMES.get(k, k) if in_svg else k: v for k, v in node.attrs.items() if k in (SVG_ATTRS if in_svg else ATTRS)}
        attrs.pop("id", None)
        original = self.source_id(node)
        if original and chapter.source_ids.get(original) is node:
            attrs["id"] = self.dom_target(chapter.path, original)
        if node.pid:
            attrs["data-pid"] = node.pid
        legacy = "robin-reading-highlight" in node.attrs.get("class", "").split()
        if legacy:
            attrs["class"] = " ".join(token for token in attrs.get("class", "").split() if token != "robin-reading-highlight")
        elif not in_svg:
            style = safe_declarations(node.attrs.get("style", ""))
            if style:
                attrs["style"] = style
        if tag == "a":
            href = node.attrs.get("href", node.attrs.get("xlink:href", ""))
            target = archive_link(chapter.path, href) if href else None
            if target and (jump := self.dom_target(*target)):
                attrs["href"] = "#" + jump
            elif re.match(r"^https?://[^\s]+$", href, re.I):
                attrs["href"], attrs["target"], attrs["rel"] = href, "_blank", "noopener noreferrer"
        elif tag in {"img", "image"}:
            src = next((node.attrs[k] for k in ("src", "href", "xlink:href") if node.attrs.get(k)), "")
            asset = self.image(chapter.path, src)
            if asset:
                attrs["data-asset"] = asset
            elif src:
                label = attrs.get("alt") or "图片不可用"
                return f'<span class="rd-image-missing">[{escape(label)}]</span>'
            if tag == "img":
                attrs.setdefault("alt", "")
                attrs["decoding"] = "async"
                if asset and not ("width" in attrs or "height" in attrs):
                    dimensions = self.image_dimensions(asset)
                    if dimensions and all(0 < dimension < 100000 for dimension in dimensions):
                        attrs["width"], attrs["height"] = map(str, dimensions)
        if in_svg:
            if tag == "use":
                href = node.attrs.get("href", node.attrs.get("xlink:href", ""))
                if href.startswith("#") and unquote(href[1:]) in chapter.source_ids:
                    attrs["href"] = "#" + self.dom_target(chapter.path, unquote(href[1:]))
            for attr in ("fill", "stroke", "clip-path", "mask"):
                if attr in attrs and "url" in attrs[attr].lower():
                    match = re.fullmatch(r"url\(\s*#([^\s)]+)\s*\)", attrs[attr], re.I)
                    if match and match[1] in chapter.source_ids:
                        attrs[attr] = f"url(#{self.dom_target(chapter.path, match[1])})"
                    else:
                        del attrs[attr]
        serialized = "".join(f' {key}="{escape(value, quote=True)}"' for key, value in attrs.items())
        html_tag = SVG_NAMES.get(tag, tag)
        if tag in VOID and not in_svg:
            return f"<{html_tag}{serialized}>"
        children = "".join(self.render_node(chapter, child, in_svg) for child in node.children)
        return f"<{html_tag}{serialized}>{children}</{html_tag}>"

    def chapter_markup(self, chapter):
        children = "".join(self.render_node(chapter, child) for child in chapter.body.children if not isinstance(child, Node) or child.tag != "head")
        classes = "rd-chapter " + chapter.body.attrs.get("class", "")
        scope = " ".join("s-" + digest(style) for style in chapter.styles)
        attributes = f' id="{chapter.key}" class="{escape(classes)}" data-epub-style="{scope}"'
        if chapter.body.pid:
            attributes += f' data-pid="{chapter.body.pid}"'
        return f"<section{attributes}>{children}</section>"

    def book_css(self):
        sheets = dict.fromkeys(path for c in self.chapters.values() for path in c.styles)
        for path in sheets:
            root = '#rd-book .rd-chapter[data-epub-style~="s-' + digest(path) + '"]'
            raw = re.sub(r"/\*.*?\*/", "", read_text(self.archive.read(path)), flags=re.S)
            raw = re.sub(r"@[^{};]*;", "", raw)
            for selectors, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", raw):
                declarations = safe_declarations(declarations)
                if not declarations:
                    continue
                for selector in selectors.split(","):
                    selector = selector.strip()
                    if not re.fullmatch(r"[\w .>#:+~*-]+", selector, flags=re.ASCII):
                        continue
                    if ":" in selector:
                        continue
                    if re.match(r"^body(?:$|[. #>])", selector, re.I):
                        yield root + selector[4:] + "{" + declarations + "}\n"
                    else:
                        yield root + " " + selector + "{" + declarations + "}\n"

    def diagnostics(self):
        return {
            "bookId": self.book_id, "title": self.title, "chapters": len(self.chapters),
            "paragraphs": sum(len(chapter.blocks) for chapter in self.chapters.values()),
            "tocEntries": len(self.toc), "images": len(self.assets), "missingImages": sorted(self.missing),
        }


def safe_declarations(style):
    declarations = []
    for item in style.split(";"):
        key, sep, value = item.partition(":")
        key, value = key.strip().lower(), value.strip()
        if sep and key in STYLE_PROPS and STYLE_VALUE.fullmatch(value) and "url" not in value.lower():
            declarations.append(f"{key}:{value}")
    return ";".join(declarations)


def validate_study(study, known_ids):
    def text_field(obj, name):
        value = obj.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"精读路线缺少有效的 {name}")
        return value.strip()

    def minutes(value):
        if type(value) is not int or value < 1:
            raise ValueError("精读路线时长必须是正整数分钟")
        return value

    if not isinstance(study, dict):
        raise ValueError("study 必须是精读路线对象")
    stage = study.get("stage", "full")
    if stage not in ("preview", "full"):
        raise ValueError("精读路线 stage 应为 preview 或 full")
    mode = study.get("mode", "目标精读")
    preferences = study.get("preferences", "")
    if not isinstance(mode, str) or not mode.strip() or not isinstance(preferences, str):
        raise ValueError("精读路线 mode 和 preferences 应为文本")
    gaps = study.get("gaps", [])
    if not isinstance(gaps, list) or any(not isinstance(gap, str) or not gap.strip() for gap in gaps):
        raise ValueError("gaps 应为尚未回答的问题列表")
    sessions = study.get("sessions", [])
    if not isinstance(sessions, list) or not (sessions or gaps):
        raise ValueError("精读路线至少需要一个阅读单元或一个未回答的问题")
    result = []
    for number, session in enumerate(sessions, 1):
        if not isinstance(session, dict):
            raise ValueError(f"精读单元 {number} 格式无效")
        ids = session.get("noteIds")
        if not isinstance(ids, list) or not ids or not all(isinstance(ref, str) for ref in ids):
            raise ValueError(f"精读单元 {number} 缺少批注编号")
        missing = [ref for ref in ids if ref not in known_ids]
        if missing or len(set(ids)) != len(ids):
            raise ValueError(f"精读单元 {number} 引用了缺失或重复的批注：{', '.join(missing or ids)}")
        result.append({"title": text_field(session, "title"), "reason": text_field(session, "reason"),
                       "minutes": minutes(session.get("minutes")), "noteIds": ids})
    return {"goal": text_field(study, "goal"), "mode": mode.strip(), "stage": stage,
            "sessionMinutes": minutes(study.get("sessionMinutes", 20)), "sessions": result,
            "gaps": [gap.strip() for gap in gaps], "preferences": preferences.strip()}


def write_html(book, output, extra):
    notes = {note["id"]: note for note in book.annotations}
    notes.update((note["id"], note) for note in extra)
    metadata = {
        "format": FORMAT, "bookId": book.book_id, "title": book.title, "creator": book.creator, "noteLinks": 1,
        "chapters": [{ "id": c.key, "title": c.title } for c in book.chapters.values()],
        "toc": book.toc, "annotations": list(notes.values()),
    }
    if book.study:
        metadata["study"] = book.study
    before, after = (ASSETS / "reader.html").read_text(encoding="utf-8").split("<!-- BOOK BODY -->")
    before = before.replace("@@LANG@@", escape(book.lang)).replace("@@TITLE@@", escape(book.title))
    before = before.replace("/* READER CSS */", (ASSETS / "reader.css").read_text(encoding="utf-8"))
    after = after.replace("/* READER JS */", (ASSETS / "reader.js").read_text(encoding="utf-8"))
    after = after.replace("/* STUDY JS */", (ASSETS / "study.js").read_text(encoding="utf-8"))
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent, delete=False) as handle:
            temp = Path(handle.name)
            handle.write(before.replace("/* EPUB CSS */", "".join(book.book_css())))
            for chapter in book.chapters.values():
                handle.write(book.chapter_markup(chapter))
            after_chapters, reader = after.split("<!-- BOOK DATA -->")
            reader_script, after_assets = reader.split("<!-- BOOK ASSETS -->")
            handle.write(after_chapters)
            handle.write(f'<script type="application/json" id="rd-data">{json_text(metadata)}</script>\n')
            handle.write(reader_script)
            for key, (path, src) in book.assets.items():
                if path:
                    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
                    src = f"data:{mime};base64," + base64.b64encode(book.archive.read(path)).decode("ascii")
                handle.write(f'<script type="application/octet-stream" id="{key}">{src}</script>\n')
            handle.write(after_assets)
        temp.replace(output)
    finally:
        if temp and temp.exists():
            temp.unlink()
    return len(notes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    convert = actions.add_parser("convert", help="convert one or more EPUBs or folders")
    convert.add_argument("inputs", type=Path, nargs="+")
    convert.add_argument("--out-dir", type=Path, required=True)
    convert.add_argument("--annotations", type=Path, help="JSON for one EPUB, or folder of <source stem>.annotations.json")
    convert.add_argument("--overwrite", action="store_true")
    convert.add_argument("--shelf", action="store_true", help="also create an offline searchable cross-book notebook")
    extract = actions.add_parser("extract", help="list chapters or extract exact paragraph text for annotation")
    extract.add_argument("input", type=Path)
    extract.add_argument("--chapter", help="exact chapter ID or EPUB internal path")
    args = parser.parse_args()
    if args.action == "extract":
        try:
            with ZipFile(args.input) as archive:
                book = Book(archive, args.input.stem)
                chapter = book.chapter_keys.get(args.chapter) or book.chapters.get(args.chapter)
                if args.chapter and not chapter:
                    raise ValueError("章节不存在，先运行 extract 查看章节 ID")
                rows = [{"paragraphId": key, "text": raw_text(node)} for key, node in chapter.blocks.items()] if chapter else [
                    {"chapter": c.key, "path": c.path, "title": c.title, "paragraphs": len(c.blocks)} for c in book.chapters.values()
                ]
                print(json.dumps({"bookId": book.book_id, "title": book.title, "rows": rows}, ensure_ascii=False, indent=2))
                return 0
        except (OSError, ValueError, KeyError, StopIteration, ET.ParseError, BadZipFile, RuntimeError, LookupError) as error:
            parser.exit(1, f"{args.input}: {error}\n")

    paths = list(dict.fromkeys(candidate.resolve() for path in args.inputs for candidate in (
        sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() == ".epub") if path.is_dir() else [path]
    )))
    if not paths:
        parser.error("没有找到 EPUB")
    if args.annotations and not args.annotations.exists():
        parser.error("找不到批注 JSON 或目录")
    if args.annotations and not args.annotations.is_dir() and len(paths) != 1:
        parser.error("批量转换时，--annotations 应为目录")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    shelf = args.out_dir / "笔记书架.html"
    if args.shelf and shelf.exists() and not args.overwrite:
        parser.error("笔记书架已存在，需要重建时使用 --overwrite")
    counts = Counter(p.stem.casefold() for p in paths)
    failed = False
    outputs = []
    for path in paths:
        suffix = "-" + digest(str(path), 8) if counts[path.stem.casefold()] > 1 else ""
        output = args.out_dir / (path.stem + suffix + "｜批注阅读器.html")
        try:
            if path.suffix.lower() != ".epub":
                raise ValueError("输入应为 .epub 文件")
            if output.exists() and not args.overwrite:
                raise ValueError("输出已存在，需要重建时使用 --overwrite")
            sidecar = args.annotations or path.with_name(path.stem + ".annotations.json")
            if sidecar.is_dir():
                sidecar = sidecar / (path.stem + ".annotations.json")
            with ZipFile(path) as archive:
                book = Book(archive, path.stem)
                notes, rejected = book.import_notes(sidecar) if sidecar.is_file() else ([], [])
                count = write_html(book, output, notes)
                result = {"input": str(path), "output": str(output.resolve()), **book.diagnostics(),
                          "annotations": count, "importedAnnotations": len(notes), "rejectedAnnotations": rejected}
                if book.study:
                    result.update(studyStage=book.study["stage"], sessions=len(book.study["sessions"]),
                                  unanswered=len(book.study["gaps"]))
            failed |= bool(rejected)
            if not rejected:
                outputs.append(output)
        except (OSError, ValueError, KeyError, StopIteration, ET.ParseError, BadZipFile, RuntimeError, LookupError) as error:
            failed = True
            result = {"input": str(path), "error": str(error) or "EPUB 结构缺失或无效"}
        print(json.dumps(result, ensure_ascii=False), flush=True)
    if args.shelf and not failed:
        from bookshelf import build_shelf
        try:
            build_shelf(outputs, shelf, backups=())
            print(json.dumps({"shelf": str(shelf.resolve()), "books": len(outputs)}, ensure_ascii=False))
        except (OSError, ValueError) as error:
            print(json.dumps({"shelfError": str(error)}, ensure_ascii=False))
            failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
