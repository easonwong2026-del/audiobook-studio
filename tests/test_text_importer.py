"""TXT / DOCX / EPUB 小说导入测试。"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from lib.text_importer import load_text
from services.script_director import ScriptDirectorService


def _write_docx(path: Path, paragraphs: list[str]) -> None:
    body = "".join(
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
        for text in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)


def _write_epub(path: Path) -> None:
    container = """\
<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf"/>
  </rootfiles>
</container>
"""
    opf = """\
<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf">
  <manifest>
    <item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="c2"/>
    <itemref idref="c1"/>
  </spine>
</package>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr(
            "OEBPS/chapter1.xhtml",
            "<html><body><h1>第一章</h1><p>甲说道：“开始。”</p></body></html>",
        )
        archive.writestr(
            "OEBPS/chapter2.xhtml",
            "<html><body><h1>第二章</h1><p>乙回答：“继续。”</p></body></html>",
        )


def test_txt_supports_gb18030(tmp_path):
    path = tmp_path / "novel.txt"
    path.write_bytes("第一章\n\n中文内容".encode("gb18030"))
    assert "中文内容" in load_text(str(path))


def test_docx_extracts_paragraphs_and_can_be_analyzed(tmp_path):
    path = tmp_path / "novel.docx"
    _write_docx(path, ["第一章", "张三说道：“开始吧。”"])
    text = load_text(str(path))
    assert text == "第一章\n\n张三说道：“开始吧。”"

    script = ScriptDirectorService().analyze_file(str(path))
    assert script["meta"]["title"] == "novel"
    assert script["chapters"][0]["segments"][0]["speaker"] == "张三"


def test_epub_uses_spine_order_and_can_be_analyzed(tmp_path):
    path = tmp_path / "novel.epub"
    _write_epub(path)
    text = load_text(str(path))
    assert text.index("第二章") < text.index("第一章")

    script = ScriptDirectorService().analyze_file(str(path))
    assert len(script["chapters"]) == 2


def test_archive_path_traversal_is_rejected(tmp_path):
    path = tmp_path / "unsafe.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("../escape.txt", "bad")
        archive.writestr("word/document.xml", "<document/>")
    with pytest.raises(ValueError, match="不安全路径"):
        load_text(str(path))


def test_unsupported_extension_is_rejected(tmp_path):
    path = tmp_path / "novel.pdf"
    path.write_text("text", encoding="utf-8")
    with pytest.raises(ValueError, match="不支持的输入格式"):
        load_text(str(path))
