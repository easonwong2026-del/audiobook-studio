"""小说文本导入：TXT / DOCX / EPUB → 纯文本。"""
from __future__ import annotations

import html
import posixpath
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

SUPPORTED_EXTENSIONS = {".txt", ".docx", ".epub"}
_MAX_ARCHIVE_UNCOMPRESSED = 100 * 1024 * 1024
_MAX_ENTRY_SIZE = 20 * 1024 * 1024


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _validate_archive(archive: zipfile.ZipFile) -> None:
    total = sum(info.file_size for info in archive.infolist())
    if total > _MAX_ARCHIVE_UNCOMPRESSED:
        raise ValueError("压缩文档解压后超过 100 MB，拒绝导入")
    for info in archive.infolist():
        path = PurePosixPath(info.filename)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"压缩文档包含不安全路径：{info.filename}")
        if info.file_size > _MAX_ENTRY_SIZE:
            raise ValueError(f"压缩文档内单个文件超过 20 MB：{info.filename}")


def _read_entry(archive: zipfile.ZipFile, name: str) -> bytes:
    normalized = posixpath.normpath(name)
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"文档引用了不安全路径：{name}")
    try:
        return archive.read(normalized)
    except KeyError as exc:
        raise ValueError(f"文档缺少内部文件：{normalized}") from exc


def load_text(path: str) -> str:
    """读取受支持文件并返回适合导演分析的纯文本。"""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"找不到输入文件：{source}")
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        supported = " / ".join(sorted(SUPPORTED_EXTENSIONS))
        raise ValueError(f"不支持的输入格式 {suffix or '（无扩展名）'}；支持 {supported}")
    if suffix == ".txt":
        text = _load_txt(source)
    elif suffix == ".docx":
        text = _load_docx(source)
    else:
        text = _load_epub(source)
    text = _normalize_text(text)
    if not text:
        raise ValueError(f"{suffix[1:].upper()} 文档中没有可分析文本")
    return text


def _load_txt(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("TXT 编码无法识别；请使用 UTF-8 或 GB18030")


def _load_docx(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            _validate_archive(archive)
            xml = _read_entry(archive, "word/document.xml")
    except zipfile.BadZipFile as exc:
        raise ValueError("DOCX 文件损坏或不是有效的 Word 文档") from exc
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise ValueError("DOCX document.xml 无法解析") from exc

    paragraphs = []
    for paragraph in root.iter():
        if _local_name(paragraph.tag) != "p":
            continue
        parts = []
        for node in paragraph.iter():
            name = _local_name(node.tag)
            if name == "t" and node.text:
                parts.append(node.text)
            elif name == "tab":
                parts.append("\t")
            elif name in {"br", "cr"}:
                parts.append("\n")
        text = "".join(parts).strip()
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


class _XhtmlTextParser(HTMLParser):
    _BLOCKS = {
        "article", "blockquote", "br", "div", "h1", "h2", "h3", "h4",
        "h5", "h6", "hr", "li", "p", "section", "tr",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag, attrs):
        name = tag.lower()
        if name in {"script", "style", "svg"}:
            self._ignored_depth += 1
        elif not self._ignored_depth and name in self._BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        name = tag.lower()
        if name in {"script", "style", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif not self._ignored_depth and name in self._BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._ignored_depth and data.strip():
            self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


def _load_epub(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            _validate_archive(archive)
            container = ElementTree.fromstring(
                _read_entry(archive, "META-INF/container.xml")
            )
            rootfile = next(
                (
                    node.attrib.get("full-path")
                    for node in container.iter()
                    if _local_name(node.tag) == "rootfile"
                    and node.attrib.get("full-path")
                ),
                None,
            )
            if not rootfile:
                raise ValueError("EPUB container.xml 未声明 OPF")
            opf_root = ElementTree.fromstring(_read_entry(archive, rootfile))
            opf_dir = posixpath.dirname(rootfile)
            manifest = {
                node.attrib.get("id"): (
                    node.attrib.get("href"),
                    node.attrib.get("media-type", ""),
                )
                for node in opf_root.iter()
                if _local_name(node.tag) == "item"
                and node.attrib.get("id")
                and node.attrib.get("href")
            }
            spine_ids = [
                node.attrib.get("idref")
                for node in opf_root.iter()
                if _local_name(node.tag) == "itemref" and node.attrib.get("idref")
            ]
            ordered_items = [
                manifest[item_id]
                for item_id in spine_ids
                if item_id in manifest
            ]
            if not ordered_items:
                ordered_items = [
                    item
                    for item in manifest.values()
                    if "html" in item[1]
                ]
            chapters = []
            for href, media_type in ordered_items:
                if "html" not in media_type and not href.lower().endswith((".html", ".xhtml", ".htm")):
                    continue
                href_path = unquote(urlsplit(href).path)
                internal_path = posixpath.normpath(posixpath.join(opf_dir, href_path))
                raw = _read_entry(archive, internal_path)
                markup = raw.decode("utf-8", errors="replace")
                parser = _XhtmlTextParser()
                parser.feed(markup)
                chapter = _normalize_text(html.unescape(parser.text()))
                if chapter:
                    chapters.append(chapter)
            return "\n\n".join(chapters)
    except zipfile.BadZipFile as exc:
        raise ValueError("EPUB 文件损坏或不是有效的 EPUB") from exc
    except ElementTree.ParseError as exc:
        raise ValueError("EPUB 元数据 XML 无法解析") from exc


def _normalize_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    normalized = re.sub(r"[ \t]+\n", "\n", normalized)
    normalized = re.sub(r"\n[ \t]+", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()
