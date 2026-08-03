"""AI 分析结果有效性检查层（异常检测器）+ 本地对白信号启发式。

职责边界（与 DESIGN §2.0 一致）：
- 只做"判可疑"，不创建角色、不归属说话人、不改文档；
- 角色创建/归属仍由 AI 三阶段与 ``v4_reanalysis_service`` 的
  ``reconcile_speakers`` / ``protect_manual_assignments`` / ``migrate_voice_bindings``
  负责。

隐私红线：本模块不记录 API Key、不记录完整请求正文、不记录完整模型原始响应；
``attempts[].summary`` 只保存计数摘要。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from services.v4_analysis_config import (
    DEFAULT_V4_ANALYSIS_CONFIG,
    V4AnalysisConfig,
)

PIPELINE_VERSION = "v4-pipeline-v2"

DIALOGUE_COVERAGE_UNKNOWN_LABEL = "未识别到对白（暂无可计算对白）"


class ReasonCode(str, Enum):
    """稳定 reason_code 枚举（唯一定义处，勿改名；见 DESIGN §6.1）。"""

    OK = "ok"
    EMPTY_RESULT_SUSPECTED = "empty_result_suspected"
    DIALOGUE_SIGNAL_NO_DIALOGUE = "dialogue_signal_no_dialogue"
    DIALOGUE_SIGNAL_NO_CHARACTERS = "dialogue_signal_no_characters"
    STAGE_FAILURES = "stage_failures"
    AI_REQUESTS_ABNORMAL = "ai_requests_abnormal"
    CACHE_INVALIDATED = "cache_invalidated"
    COVERAGE_UNDEFINED = "coverage_undefined"

    @classmethod
    def from_value(cls, value: Any) -> "ReasonCode | None":
        """把持久化字符串/枚举安全转换回 ReasonCode；未知值返回 None。"""
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value))
        except (TypeError, ValueError):
            return None


REASON_MESSAGES: dict[ReasonCode, str] = {
    ReasonCode.OK: "",
    ReasonCode.EMPTY_RESULT_SUSPECTED: (
        "AI 返回了可疑的空分析结果：原文包含明显对白信号，但未识别出人物或对白。"
        "本次结果未被标记为完成，请重试或检查 Provider、模型及 API 配置。"
    ),
    ReasonCode.DIALOGUE_SIGNAL_NO_DIALOGUE: "原文存在明显对白信号，但 AI 输出 0 条对白。",
    ReasonCode.DIALOGUE_SIGNAL_NO_CHARACTERS: "原文存在明显对白信号，但 AI 输出 0 个正式角色。",
    ReasonCode.STAGE_FAILURES: "存在阶段失败，可继续分析重试。",
    ReasonCode.AI_REQUESTS_ABNORMAL: "AI 请求次数远低于分片数，疑似未真正分析完整本书。",
    ReasonCode.CACHE_INVALIDATED: (
        "检测到历史可疑空完成缓存，已自动失效并转入重新分析（人工锁定/指派/声音绑定已保留）。"
    ),
    ReasonCode.COVERAGE_UNDEFINED: f"{DIALOGUE_COVERAGE_UNKNOWN_LABEL}。",
}

# 对白提示词（说道/问道/回答/喊道/答道/嚷道/解释/反驳/告诉/轻声说/笑着说…）
_DIALOGUE_KEYWORDS = (
    "说道", "问道", "答道", "回答", "喊道", "嚷道", "解释",
    "反驳", "告诉", "轻声说", "笑着说", "补充道", "接口道", "回应道",
)

# 成对引号（中英弯引号 / 直角引号）
_QUOTE_PAIRS = {"“": "”", "「": "」", "『": "』", "‘": "’", '"': '"'}

# XXX说："…" 发言结构（说话人 + 提示词 + 引号）
_SPEAKER_PATTERN_RE = re.compile(
    r"([\w\u3400-\u9fff·]{1,8}?)"
    r"(?:说道|问道|答道|回答|说|问|答|喊|叫|道)"
    r"\s*[：:，,\s]*\s*[“「『\"‘]"
)


def _count_quote_pairs(text: str) -> int:
    """统计成对引号数量（确定性、只读原文）。"""
    total = 0
    for opener, closer in _QUOTE_PAIRS.items():
        if opener == closer:
            total += text.count(opener) // 2
        else:
            total += min(text.count(opener), text.count(closer))
    return total


def _count_keywords(text: str) -> int:
    return sum(text.count(keyword) for keyword in _DIALOGUE_KEYWORDS)


def _count_speaker_patterns(text: str) -> int:
    return len(_SPEAKER_PATTERN_RE.findall(text))


def _signal_strength(
    signals: "SourceDialogueSignals", config: V4AnalysisConfig
) -> float:
    """把三类信号归一化到 0..1 并取均值（阈值作为饱和点）。"""
    quote = min(1.0, signals.quote_pair_count / max(1, config.validity_min_quote_pairs))
    keywords = min(
        1.0,
        signals.dialogue_keyword_hits / max(1, config.validity_min_dialogue_keywords),
    )
    speaker = min(
        1.0,
        signals.speaker_pattern_hits / max(1, config.validity_min_speaker_patterns),
    )
    return round((quote + keywords + speaker) / 3.0, 4)


@dataclass(frozen=True)
class SourceDialogueSignals:
    """原文本地对白信号（只读原文，不调用模型）。"""

    quote_pair_count: int = 0
    dialogue_keyword_hits: int = 0
    speaker_pattern_hits: int = 0
    has_dialogue_signal: bool = False
    signal_strength: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "quote_pair_count": self.quote_pair_count,
            "dialogue_keyword_hits": self.dialogue_keyword_hits,
            "speaker_pattern_hits": self.speaker_pattern_hits,
            "has_dialogue_signal": self.has_dialogue_signal,
            "signal_strength": self.signal_strength,
        }


@dataclass(frozen=True)
class ValidityIssue:
    code: ReasonCode
    severity: str  # "suspicious" | "warning" | "info"
    message: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidityReport:
    is_suspicious: bool
    issues: tuple[ValidityIssue, ...] = ()
    source_signals: SourceDialogueSignals | None = None


@dataclass(frozen=True)
class AnalysisRunStats:
    """AI-first 分析可观测性统计（仅计数，不含原文/响应）。"""

    ai_requests: int = 0
    chapters_total: int = 0
    chapters_completed: int = 0
    chapters_failed: int = 0
    shards_total: int = 0
    retries: int = 0
    failures: int = 0
    started_at: str = ""
    finished_at: str = ""


# AI 请求计数口径（DESIGN §6.6）：经代理包装后计入的 adapter 方法。
_COUNTED_METHODS = frozenset({
    "read_chapter", "understand_chapter", "finalize", "finalize_book",
    "analyze_batch", "direct_batch", "review_chapter", "review",
})


class CountingAdapterProxy:
    """包装 adapter 并统计 AI 请求次数（含自动重试），其余属性透传。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls = 0

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if name in _COUNTED_METHODS and callable(attr):
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                self.calls += 1
                return attr(*args, **kwargs)

            return wrapper
        return attr


def compute_input_fingerprint(
    source_sha256: str,
    *,
    provider: str,
    model: str,
    config: V4AnalysisConfig = DEFAULT_V4_ANALYSIS_CONFIG,
    pipeline_version: str = PIPELINE_VERSION,
) -> str:
    """计算 analysis.json 缓存身份（原文 + 配置 + Provider/模型 + 版本）。

    只取影响输出的稳定要素；不含时间戳/随机数/API Key。
    """
    knobs = (
        config.ai_max_input_chars,
        config.validity_min_quote_pairs,
        config.validity_min_dialogue_keywords,
        config.validity_min_speaker_patterns,
        config.validity_min_bible_chars,
        config.validity_max_unresolved_ratio,
        config.validity_min_ai_request_ratio,
    )
    payload = f"{pipeline_version}:{source_sha256}:{provider}:{model}:{knobs}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AnalysisValidityChecker:
    """AI 三阶段产物 + 统计 的可疑判定器（只判可疑，不代做）。"""

    def __init__(self, config: V4AnalysisConfig = DEFAULT_V4_ANALYSIS_CONFIG) -> None:
        self.config = config

    def reason_message(self, code: ReasonCode) -> str:
        return REASON_MESSAGES.get(code, "")

    def detect_source_signals(self, source_text: str) -> SourceDialogueSignals:
        """本地对白信号启发式：引号对 / 提示词 / 发言结构。"""
        text = str(source_text or "")
        quote_pairs = _count_quote_pairs(text)
        keywords = _count_keywords(text)
        speaker_patterns = _count_speaker_patterns(text)
        base = SourceDialogueSignals(
            quote_pair_count=quote_pairs,
            dialogue_keyword_hits=keywords,
            speaker_pattern_hits=speaker_patterns,
        )
        has_signal = (
            quote_pairs >= self.config.validity_min_quote_pairs
            or keywords >= self.config.validity_min_dialogue_keywords
            or speaker_patterns >= self.config.validity_min_speaker_patterns
        )
        return SourceDialogueSignals(
            quote_pair_count=quote_pairs,
            dialogue_keyword_hits=keywords,
            speaker_pattern_hits=speaker_patterns,
            has_dialogue_signal=has_signal,
            signal_strength=_signal_strength(base, self.config),
        )

    def _issue(
        self, code: ReasonCode, severity: str, detail: dict[str, Any]
    ) -> ValidityIssue:
        return ValidityIssue(code, severity, self.reason_message(code), dict(detail))

    def check(
        self,
        *,
        source_text: str,
        script: Any,
        speakers: Any,
        candidates: Any,
        bible_count: int,
        summary: dict[str, Any],
        stats: AnalysisRunStats | None = None,
        errors: list[str] | None = None,
    ) -> ValidityReport:
        """对一次 AI 三阶段产物做可疑判定（DESIGN §3.6）。"""
        signals = self.detect_source_signals(source_text)
        errors = list(errors or [])
        dialogue_total = int(summary.get("dialogue_total", 0) or 0)
        identified_characters = int(summary.get("identified_characters", 0) or 0)
        unresolved = int(summary.get("dialogue_unresolved", 0) or 0)
        issues: list[ValidityIssue] = []

        if not signals.has_dialogue_signal:
            # 纯旁白/说明文：0 角色不判可疑（P0-2 规则 2）
            if dialogue_total == 0:
                issues.append(
                    self._issue(
                        ReasonCode.COVERAGE_UNDEFINED,
                        "info",
                        {"dialogue_total": 0},
                    )
                )
            if errors:
                issues.append(
                    self._issue(
                        ReasonCode.STAGE_FAILURES,
                        "warning",
                        {"error_count": len(errors)},
                    )
                )
            return ValidityReport(False, tuple(issues), signals)

        if identified_characters == 0:
            issues.append(
                self._issue(
                    ReasonCode.DIALOGUE_SIGNAL_NO_CHARACTERS,
                    "suspicious",
                    {
                        "identified_characters": 0,
                        "bible_count": int(bible_count or 0),
                        "source_quote_pairs": signals.quote_pair_count,
                        "source_dialogue_keywords": signals.dialogue_keyword_hits,
                        "source_speaker_patterns": signals.speaker_pattern_hits,
                    },
                )
            )
        if dialogue_total == 0:
            issues.append(
                self._issue(
                    ReasonCode.DIALOGUE_SIGNAL_NO_DIALOGUE,
                    "suspicious",
                    {
                        "dialogue_total": 0,
                        "source_quote_pairs": signals.quote_pair_count,
                        "source_dialogue_keywords": signals.dialogue_keyword_hits,
                    },
                )
            )
        if identified_characters == 0 and dialogue_total == 0:
            issues.append(
                self._issue(
                    ReasonCode.EMPTY_RESULT_SUSPECTED,
                    "suspicious",
                    {
                        "identified_characters": 0,
                        "dialogue_total": 0,
                        "source_quote_pairs": signals.quote_pair_count,
                        "source_dialogue_keywords": signals.dialogue_keyword_hits,
                    },
                )
            )
        if errors:
            issues.append(
                self._issue(
                    ReasonCode.STAGE_FAILURES,
                    "suspicious",
                    {"error_count": len(errors)},
                )
            )
        if (
            stats is not None
            and stats.shards_total > 0
            and stats.ai_requests / stats.shards_total
            < self.config.validity_min_ai_request_ratio
        ):
            issues.append(
                self._issue(
                    ReasonCode.AI_REQUESTS_ABNORMAL,
                    "suspicious",
                    {
                        "ai_requests": stats.ai_requests,
                        "shards_total": stats.shards_total,
                    },
                )
            )
        if dialogue_total > 0 and unresolved / dialogue_total > self.config.validity_max_unresolved_ratio:
            issues.append(
                ValidityIssue(
                    ReasonCode.OK,
                    "warning",
                    "对白归属 unresolved 占比过高，建议人工确认后继续。",
                    {
                        "unresolved": unresolved,
                        "dialogue_total": dialogue_total,
                        "unresolved_ratio": round(unresolved / dialogue_total, 4),
                    },
                )
            )
        return ValidityReport(
            is_suspicious=any(item.severity == "suspicious" for item in issues),
            issues=tuple(issues),
            source_signals=signals,
        )

    def check_cached_state(
        self, state: dict[str, Any], source_text: str
    ) -> ValidityReport:
        """completed 缓存复用前校验（不重新调用模型，只读 summary + 原文信号）。"""
        summary = state.get("summary") or {}
        signals = self.detect_source_signals(source_text)
        validity = state.get("validity") or {}
        issues: list[ValidityIssue] = []
        if (
            validity.get("checked")
            and not validity.get("is_suspicious")
            and state.get("status") == "completed"
        ):
            return ValidityReport(False, (), signals)
        identified = int(summary.get("identified_characters", 0) or 0)
        dialogue_total = int(summary.get("dialogue_total", 0) or 0)
        if signals.has_dialogue_signal and (identified == 0 or dialogue_total == 0):
            if identified == 0:
                issues.append(
                    self._issue(
                        ReasonCode.DIALOGUE_SIGNAL_NO_CHARACTERS,
                        "suspicious",
                        {
                            "identified_characters": 0,
                            "source_quote_pairs": signals.quote_pair_count,
                        },
                    )
                )
            if dialogue_total == 0:
                issues.append(
                    self._issue(
                        ReasonCode.DIALOGUE_SIGNAL_NO_DIALOGUE,
                        "suspicious",
                        {
                            "dialogue_total": 0,
                            "source_quote_pairs": signals.quote_pair_count,
                        },
                    )
                )
            issues.append(
                self._issue(
                    ReasonCode.CACHE_INVALIDATED,
                    "suspicious",
                    {
                        "source_sha256": state.get("source_sha256"),
                        "cached_status": state.get("status"),
                    },
                )
            )
            return ValidityReport(True, tuple(issues), signals)
        if not signals.has_dialogue_signal and dialogue_total == 0:
            issues.append(
                self._issue(
                    ReasonCode.COVERAGE_UNDEFINED,
                    "info",
                    {"dialogue_total": 0},
                )
            )
        return ValidityReport(False, tuple(issues), signals)
