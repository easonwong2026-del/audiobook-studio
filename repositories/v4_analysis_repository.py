"""Small atomic repository for the user-visible V4 analysis state.

schema v1 → v2（DESIGN §3.4）：
- ``ANALYSIS_SCHEMA`` 升级为 ``v4-analysis-state-v2``；
- ``load()`` 兼容 v1（``SUPPORTED_SCHEMAS`` 双版本），缺失字段补默认值；
- ``start()`` 增 ``model`` / ``analysis_mode`` 参数；
- 新增 stats / validity / attempts / pipeline_version / input_fingerprint /
  stages 时间戳等字段（均带默认值，不删既有字段）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repositories.v4_atomic import atomic_write_json

ANALYSIS_SCHEMA = "v4-analysis-state-v2"
SUPPORTED_SCHEMAS = {"v4-analysis-state-v1", "v4-analysis-state-v2"}

_DEFAULT_STATS = {
    "ai_requests": 0,
    "chapters_total": 0,
    "chapters_completed": 0,
    "chapters_failed": 0,
    "shards_total": 0,
    "retries": 0,
    "failures": 0,
    "started_at": "",
    "finished_at": "",
}

_DEFAULT_VALIDITY = {
    "checked": False,
    "is_suspicious": False,
    "reason_codes": [],
    "source_dialogue_signals": {},
}


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    """把 v1/v2 状态统一补齐到 v2 全字段（不修改磁盘，仅返回副本）。"""
    value = dict(data)
    value.setdefault("pipeline_version", "")
    value.setdefault("input_fingerprint", "")
    value.setdefault("model", "")
    value.setdefault("analysis_mode", "")
    value.setdefault("attempts", [])

    stats = value.get("stats")
    if isinstance(stats, dict):
        merged_stats = dict(_DEFAULT_STATS)
        merged_stats.update(stats)
        value["stats"] = merged_stats
    else:
        value["stats"] = dict(_DEFAULT_STATS)

    validity = value.get("validity")
    if isinstance(validity, dict):
        merged_validity = dict(_DEFAULT_VALIDITY)
        merged_validity.update(validity)
        value["validity"] = merged_validity
    else:
        value["validity"] = dict(_DEFAULT_VALIDITY)

    attempts = value.get("attempts")
    if not isinstance(attempts, list):
        value["attempts"] = []

    stages = value.get("stages")
    if isinstance(stages, dict):
        normalized_stages: dict[str, Any] = {}
        for name, stage in stages.items():
            if not isinstance(stage, dict):
                normalized_stages[name] = {"status": "unknown"}
                continue
            item = dict(stage)
            item.setdefault("started_at", "")
            item.setdefault("finished_at", "")
            item.setdefault("duration_ms", 0)
            normalized_stages[name] = item
        value["stages"] = normalized_stages
    else:
        value["stages"] = {}

    value["schema_version"] = ANALYSIS_SCHEMA
    return value


class V4AnalysisRepository:
    relative_path = Path("runtime/analysis.json")

    def __init__(self, project_path: str | Path):
        self.project_path = Path(project_path)
        self.path = self.project_path / self.relative_path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self, source_sha256: str | None = None) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or data.get("schema_version") not in SUPPORTED_SCHEMAS:
            return None
        if source_sha256 is not None and data.get("source_sha256") != source_sha256:
            return None
        return _normalize(data)

    def save(self, state: dict[str, Any]) -> dict[str, Any]:
        value = _normalize(dict(state))
        value["schema_version"] = ANALYSIS_SCHEMA
        value["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(self.path, value)
        return value

    def start(
        self,
        source_sha256: str,
        *,
        provider: str = "",
        model: str = "",
        analysis_mode: str = "",
    ) -> dict[str, Any]:
        return self.save(
            {
                "schema_version": ANALYSIS_SCHEMA,
                "source_sha256": source_sha256,
                "pipeline_version": "",
                "input_fingerprint": "",
                "status": "running",
                "current_stage": "import",
                "analysis_mode": analysis_mode,
                "provider": provider,
                "model": model,
                "stages": {},
                "stats": dict(_DEFAULT_STATS),
                "validity": dict(_DEFAULT_VALIDITY),
                "attempts": [],
                "summary": {},
                "errors": [],
            }
        )
