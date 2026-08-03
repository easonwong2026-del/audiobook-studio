"""PR #22 域一：AI 分析结果有效性检测器 + 本地对白信号启发式 + schema v2 兼容。

覆盖：
- ReasonCode 枚举稳定唯一；
- V4AnalysisConfig 有效性阈值默认值 / 非法值校验；
- analysis.json schema v1 → v2 读取兼容（_normalize 补默认字段）；
- compute_input_fingerprint 稳定性 / 敏感性；
- 多对白书稿 + AI 空结果 → 判可疑；
- 纯旁白 / 说明文 + 0 角色 → 不判可疑（P0-2 规则 2）；
- 引号对 / 提示词 / 发言结构三类信号计数；
- check_cached_state 对可疑缓存判 CACHE_INVALIDATED；
- CountingAdapterProxy 只统计 AI 请求方法。
"""
from __future__ import annotations

import json

import pytest

from domain.v4 import CharacterCandidatesDocument, ScriptDocument, Speaker, SpeakersDocument
from domain.v4.models import source_sha256
from repositories.v4_analysis_repository import (
    ANALYSIS_SCHEMA,
    SUPPORTED_SCHEMAS,
    V4AnalysisRepository,
)
from services.v4_analysis_config import V4AnalysisConfig
from services.v4_analysis_validity import (
    AnalysisRunStats,
    AnalysisValidityChecker,
    CountingAdapterProxy,
    DIALOGUE_COVERAGE_UNKNOWN_LABEL,
    PIPELINE_VERSION,
    ReasonCode,
    SourceDialogueSignals,
    compute_input_fingerprint,
)


def _script_doc(source: str) -> ScriptDocument:
    return ScriptDocument(source_sha256=source_sha256(source), chapters=[])


def _narrator_only(source: str) -> SpeakersDocument:
    return SpeakersDocument(
        speakers=[
            Speaker(
                speaker_id="narrator",
                display_name="旁白",
                status="confirmed",
                speaker_type="narrator",
                locked=True,
            )
        ]
    )


def _empty_candidates(source: str) -> CharacterCandidatesDocument:
    return CharacterCandidatesDocument(source_sha256=source_sha256(source), candidates=[])


def test_reason_code_values_are_stable_and_unique():
    values = [item.value for item in ReasonCode]
    assert len(values) == len(set(values)), "reason_code 枚举值必须唯一"
    assert values == [
        "ok",
        "empty_result_suspected",
        "dialogue_signal_no_dialogue",
        "dialogue_signal_no_characters",
        "stage_failures",
        "ai_requests_abnormal",
        "cache_invalidated",
        "coverage_undefined",
    ]


def test_reason_code_from_value_roundtrip():
    assert ReasonCode.from_value("cache_invalidated") == ReasonCode.CACHE_INVALIDATED
    assert ReasonCode.from_value("not-a-code") is None
    assert ReasonCode.from_value(None) is None


def test_config_defaults_and_validation():
    config = V4AnalysisConfig()
    assert config.validity_min_quote_pairs == 2
    assert config.validity_min_dialogue_keywords == 3
    assert config.validity_min_speaker_patterns == 1
    assert config.validity_min_bible_chars == 1
    assert config.validity_max_unresolved_ratio == 0.90
    assert config.validity_min_ai_request_ratio == 0.30
    assert config.validity_retry_enabled is True
    assert config.validity_retry_max == 1
    with pytest.raises(ValueError):
        V4AnalysisConfig(validity_min_ai_request_ratio=1.5)
    with pytest.raises(ValueError):
        V4AnalysisConfig(validity_max_unresolved_ratio=-0.1)
    with pytest.raises(ValueError):
        V4AnalysisConfig(validity_retry_max=-1)
    with pytest.raises(ValueError):
        V4AnalysisConfig(validity_min_quote_pairs=-1)


def test_unknown_label_constant_is_shared():
    assert DIALOGUE_COVERAGE_UNKNOWN_LABEL == "未识别到对白（暂无可计算对白）"
    assert PIPELINE_VERSION == "v4-pipeline-v2"


def test_analysis_repository_loads_v1_and_normalizes(tmp_path):
    repo = V4AnalysisRepository(tmp_path)
    repo.path.write_text(
        json.dumps(
            {
                "schema_version": "v4-analysis-state-v1",
                "source_sha256": "abc123",
                "status": "completed",
                "current_stage": "completed",
                "provider": "deepseek",
                "stages": {"book_understanding": {"status": "completed"}},
                "summary": {"identified_characters": 2},
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    data = repo.load("abc123")
    assert data is not None
    assert data["schema_version"] == ANALYSIS_SCHEMA
    assert data["schema_version"] == "v4-analysis-state-v2"
    assert data["stats"]["ai_requests"] == 0
    assert data["stats"]["shards_total"] == 0
    assert data["validity"]["checked"] is False
    assert data["validity"]["reason_codes"] == []
    assert data["attempts"] == []
    assert data["model"] == ""
    assert data["analysis_mode"] == ""
    assert data["pipeline_version"] == ""
    assert data["input_fingerprint"] == ""
    stage = data["stages"]["book_understanding"]
    assert stage["started_at"] == ""
    assert stage["finished_at"] == ""
    assert stage["duration_ms"] == 0
    assert "v4-analysis-state-v1" in SUPPORTED_SCHEMAS
    assert "v4-analysis-state-v2" in SUPPORTED_SCHEMAS


def test_analysis_repository_save_is_v2_and_load_roundtrips(tmp_path):
    repo = V4AnalysisRepository(tmp_path)
    repo.save(
        {
            "schema_version": "v4-analysis-state-v1",
            "source_sha256": "xyz",
            "status": "running",
            "current_stage": "book_understanding",
            "stats": {"ai_requests": 3},
        }
    )
    raw = json.loads(repo.path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == "v4-analysis-state-v2"
    assert raw["updated_at"]
    loaded = repo.load("xyz")
    assert loaded["stats"]["ai_requests"] == 3
    assert loaded["stats"]["retries"] == 0


def test_compute_input_fingerprint_is_stable_and_sensitive():
    config = V4AnalysisConfig()
    base = compute_input_fingerprint(
        "sha", provider="deepseek", model="deepseek-chat", config=config
    )
    assert (
        compute_input_fingerprint(
            "sha", provider="deepseek", model="deepseek-chat", config=config
        )
        == base
    )
    assert (
        compute_input_fingerprint(
            "sha2", provider="deepseek", model="deepseek-chat", config=config
        )
        != base
    )
    assert (
        compute_input_fingerprint(
            "sha", provider="openai", model="deepseek-chat", config=config
        )
        != base
    )
    assert (
        compute_input_fingerprint(
            "sha", provider="deepseek", model="gpt-4o", config=config
        )
        != base
    )
    assert (
        compute_input_fingerprint(
            "sha",
            provider="deepseek",
            model="deepseek-chat",
            config=V4AnalysisConfig(validity_min_quote_pairs=5),
        )
        != base
    )
    assert (
        compute_input_fingerprint(
            "sha",
            provider="deepseek",
            model="deepseek-chat",
            config=config,
            pipeline_version="v4-pipeline-v9",
        )
        != base
    )


def test_multi_dialogue_book_with_empty_ai_result_is_suspicious():
    source = "第一章\n林晚说道：“我们走吧。”\n顾川问道：“去哪？”\n林晚回答：“回家。”"
    checker = AnalysisValidityChecker()
    report = checker.check(
        source_text=source,
        script=_script_doc(source),
        speakers=_narrator_only(source),
        candidates=_empty_candidates(source),
        bible_count=0,
        summary={
            "identified_characters": 0,
            "dialogue_total": 0,
            "dialogue_unresolved": 0,
        },
        stats=AnalysisRunStats(ai_requests=0, shards_total=3),
        errors=[],
    )
    assert report.is_suspicious
    codes = {item.code for item in report.issues}
    assert ReasonCode.DIALOGUE_SIGNAL_NO_CHARACTERS in codes
    assert ReasonCode.DIALOGUE_SIGNAL_NO_DIALOGUE in codes
    assert ReasonCode.EMPTY_RESULT_SUSPECTED in codes


def test_pure_narration_with_zero_characters_is_not_suspicious():
    source = "第一章\n清晨的阳光洒满大地，他缓缓走在田间小路上。"
    checker = AnalysisValidityChecker()
    report = checker.check(
        source_text=source,
        script=_script_doc(source),
        speakers=_narrator_only(source),
        candidates=_empty_candidates(source),
        bible_count=0,
        summary={
            "identified_characters": 0,
            "dialogue_total": 0,
            "dialogue_unresolved": 0,
        },
        stats=AnalysisRunStats(ai_requests=1, shards_total=1),
        errors=[],
    )
    assert not report.is_suspicious
    assert ReasonCode.COVERAGE_UNDEFINED in {item.code for item in report.issues}


def test_stage_failures_are_suspicious_with_dialogue_signal():
    source = "第一章\n林晚说道：“我们走吧。”"
    checker = AnalysisValidityChecker()
    report = checker.check(
        source_text=source,
        script=_script_doc(source),
        speakers=_narrator_only(source),
        candidates=_empty_candidates(source),
        bible_count=0,
        summary={"identified_characters": 0, "dialogue_total": 1, "dialogue_unresolved": 1},
        stats=AnalysisRunStats(ai_requests=1, shards_total=1),
        errors=["全书人物理解失败：boom"],
    )
    assert report.is_suspicious
    assert ReasonCode.STAGE_FAILURES in {item.code for item in report.issues}


def test_detect_source_signals_counts_three_signal_types():
    checker = AnalysisValidityChecker()
    source = (
        "林晚说道：“我们走吧。”\n顾川问道：“去哪？”\n"
        "林晚回答：“回家。”\n“等等！”她喊道。"
    )
    signals = checker.detect_source_signals(source)
    assert isinstance(signals, SourceDialogueSignals)
    assert signals.quote_pair_count >= 3
    assert signals.dialogue_keyword_hits >= 3
    assert signals.speaker_pattern_hits >= 3
    assert signals.has_dialogue_signal is True
    assert signals.signal_strength > 0
    payload = signals.to_dict()
    assert payload["quote_pair_count"] == signals.quote_pair_count
    assert payload["has_dialogue_signal"] is True


def test_detect_source_signals_returns_false_for_narration():
    checker = AnalysisValidityChecker()
    signals = checker.detect_source_signals("第一章\n晨雾散去，他推开门。")
    assert signals.has_dialogue_signal is False
    assert signals.quote_pair_count == 0
    assert signals.dialogue_keyword_hits == 0
    assert signals.speaker_pattern_hits == 0


def test_check_cached_state_flags_suspicious_completed_cache():
    checker = AnalysisValidityChecker()
    source = "第一章\n林晚说道：“我们走吧。”\n顾川问道：“去哪？”"
    state = {
        "schema_version": "v4-analysis-state-v1",
        "source_sha256": source_sha256(source),
        "status": "completed",
        "summary": {"identified_characters": 0, "dialogue_total": 0},
        "errors": [],
    }
    report = checker.check_cached_state(state, source)
    assert report.is_suspicious
    assert ReasonCode.CACHE_INVALIDATED in {item.code for item in report.issues}
    assert ReasonCode.DIALOGUE_SIGNAL_NO_CHARACTERS in {item.code for item in report.issues}


def test_check_cached_state_reuses_valid_cache():
    checker = AnalysisValidityChecker()
    source = "第一章\n林晚说道：“我们走吧。”\n顾川问道：“去哪？”"
    state = {
        "status": "completed",
        "summary": {"identified_characters": 2, "dialogue_total": 2},
    }
    report = checker.check_cached_state(state, source)
    assert not report.is_suspicious


def test_check_cached_state_reuses_verified_v2_cache():
    checker = AnalysisValidityChecker()
    source = "第一章\n林晚说道：“我们走吧。”"
    state = {
        "status": "completed",
        "validity": {"checked": True, "is_suspicious": False, "reason_codes": []},
        "summary": {"identified_characters": 1, "dialogue_total": 1},
    }
    report = checker.check_cached_state(state, source)
    assert not report.is_suspicious


def test_counting_adapter_proxy_counts_only_ai_methods():
    class Adapter:
        name = "fake"
        model = "fake-reasoner"

        def __init__(self):
            self.calls = 0

        def read_chapter(self, **_kwargs):
            self.calls += 1
            return {}

        def finalize(self, **_kwargs):
            return {}

    inner = Adapter()
    proxy = CountingAdapterProxy(inner)
    assert proxy.name == "fake"
    assert proxy.model == "fake-reasoner"
    proxy.read_chapter(a=1)
    proxy.read_chapter(a=2)
    proxy.finalize()
    assert proxy.calls == 3
    assert inner.calls == 2
