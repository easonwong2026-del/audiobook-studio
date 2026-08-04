"""Local-only source import for v4 projects."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from domain.v4 import SourceMetadata
from domain.v4.models import source_sha256
from lib.text_importer import load_text

NORMALIZATION_VERSION = "audiobook-normalization-v1"


@dataclass(frozen=True)
class ImportedSource:
    text: str
    metadata: SourceMetadata


class SourceImportService:
    """Import supported documents without AI or network access."""

    def import_text(
        self,
        text: str,
        *,
        original_filename: str = "pasted-chapter.txt",
        source_format: str = "txt",
        encoding: str = "utf-8",
        source_origin: str = "pasted-chapter",
    ) -> ImportedSource:
        """Create the same immutable source record for pasted chapter text."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("source text cannot be empty")
        metadata = SourceMetadata(
            original_filename=original_filename or "pasted-chapter.txt",
            source_format=source_format or "txt",
            encoding=encoding or "utf-8",
            normalization=NORMALIZATION_VERSION,
            char_count=len(text),
            sha256=source_sha256(text),
            imported_at=datetime.now(timezone.utc).isoformat(),
            source_origin=source_origin,
            source_fidelity="normalized-source",
        )
        metadata.validate(text)
        return ImportedSource(text=text, metadata=metadata)

    def import_file(self, path: str | Path) -> ImportedSource:
        source = Path(path)
        text = load_text(str(source))
        encoding = self._detect_encoding(source)
        metadata = SourceMetadata(
            original_filename=source.name,
            source_format=source.suffix.lower().lstrip("."),
            encoding=encoding,
            normalization=NORMALIZATION_VERSION,
            char_count=len(text),
            sha256=source_sha256(text),
            imported_at=datetime.now(timezone.utc).isoformat(),
        )
        metadata.validate(text)
        return ImportedSource(text=text, metadata=metadata)

    @staticmethod
    def _detect_encoding(path: Path) -> str:
        if path.suffix.lower() != ".txt":
            return "container-defined"
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            return "utf-8-sig"
        for encoding in ("utf-8", "gb18030"):
            try:
                raw.decode(encoding)
                return encoding
            except UnicodeDecodeError:
                continue
        return "unknown"
