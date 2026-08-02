#!/usr/bin/env python3
"""Evaluate a V4 project against a local JSON ground-truth fixture.

Usage: python tools/evaluate_v4_accuracy.py PROJECT_DIR GROUND_TRUTH.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from domain.v4 import ScriptDocument, SpeakersDocument
from services.v4_accuracy_evaluation import evaluate_v4_accuracy


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    project = Path(argv[1])
    source = (project / "source/source.txt").read_text(encoding="utf-8")
    script = ScriptDocument.from_dict(
        json.loads((project / "script/script.json").read_text(encoding="utf-8")),
        source,
    )
    speakers = SpeakersDocument.from_dict(
        json.loads((project / "script/speakers.json").read_text(encoding="utf-8"))
    )
    truth = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
    print(json.dumps(evaluate_v4_accuracy(speakers, script, truth).to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
