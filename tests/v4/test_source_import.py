from __future__ import annotations

import zipfile

import pytest

from services.source_import_service import SourceImportService


@pytest.mark.parametrize(
    ("payload", "expected_encoding"),
    [
        ("正文".encode(), "utf-8"),
        (b"\xef\xbb\xbf" + "正文".encode(), "utf-8-sig"),
        ("正文".encode("gb18030"), "gb18030"),
    ],
)
def test_txt_encodings_and_hash(tmp_path, payload, expected_encoding):
    source = tmp_path / "book.txt"
    source.write_bytes(payload)
    imported = SourceImportService().import_file(source)
    assert imported.text == "正文"
    assert imported.metadata.encoding == expected_encoding
    imported.metadata.validate(imported.text)


def test_crlf_and_multiple_blank_lines_are_normalized(tmp_path):
    source = tmp_path / "book.txt"
    source.write_bytes(b"one\r\n\r\n\r\n\r\ntwo\rthree")
    imported = SourceImportService().import_file(source)
    assert imported.text == "one\n\ntwo\nthree"


def test_docx_import(tmp_path):
    source = tmp_path / "book.docx"
    document = (
        '<w:document xmlns:w="urn:w"><w:body><w:p><w:r><w:t>第一段</w:t>'
        "</w:r></w:p><w:p><w:r><w:t>第二段</w:t></w:r></w:p></w:body></w:document>"
    )
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("word/document.xml", document)
    assert SourceImportService().import_file(source).text == "第一段\n\n第二段"


def test_epub_import(tmp_path):
    source = tmp_path / "book.epub"
    container = (
        '<container xmlns="urn:o"><rootfiles><rootfile full-path="OPS/book.opf"/>'
        "</rootfiles></container>"
    )
    opf = (
        '<package xmlns="urn:o"><manifest><item id="c1" href="c1.xhtml" '
        'media-type="application/xhtml+xml"/></manifest><spine><itemref idref="c1"/>'
        "</spine></package>"
    )
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OPS/book.opf", opf)
        archive.writestr("OPS/c1.xhtml", "<html><body><p>章节正文</p></body></html>")
    assert SourceImportService().import_file(source).text == "章节正文"


@pytest.mark.parametrize("suffix", [".txt", ".docx", ".epub"])
def test_empty_or_corrupt_sources_fail(tmp_path, suffix):
    source = tmp_path / f"bad{suffix}"
    source.write_bytes(b"")
    with pytest.raises(ValueError):
        SourceImportService().import_file(source)
