"""Central policy knobs for the V4 character-analysis pipeline."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class V4AnalysisConfig:
    """Thresholds are kept in one place so accuracy/coverage can be tuned."""

    auto_confirm_threshold: float = 0.90
    routing_min_confidence: float = 0.75
    routing_batch_size: int = 24
    routing_context_radius: int = 720
    routing_scene_gap: int = 1600
    routing_previous_speaker_limit: int = 6
    consistency_unresolved_spike_ratio: float = 0.50
    ai_max_input_chars: int = 12000

    # ── AI 分析结果有效性检查层（DESIGN §3.3）──
    # 本地对白信号启发式阈值：任一信号达到阈值即认为原文"有明显对白信号"。
    validity_min_quote_pairs: int = 2
    validity_min_dialogue_keywords: int = 3
    validity_min_speaker_patterns: int = 1
    # 有对白信号时期望的最少角色数（0 → 可疑）。
    validity_min_bible_chars: int = 1
    # unresolved 片段占比超过该值 → 提示（warning，不单独触发可疑）。
    validity_max_unresolved_ratio: float = 0.90
    # ai_requests / shards_total 低于该值 → AI_REQUESTS_ABNORMAL。
    validity_min_ai_request_ratio: float = 0.30
    # 可疑结果自动重试开关与上限（PRD：恰好 1 次）。
    validity_retry_enabled: bool = True
    validity_retry_max: int = 1

    def __post_init__(self) -> None:
        if not 0.0 <= self.auto_confirm_threshold <= 1.0:
            raise ValueError("auto_confirm_threshold must be between 0 and 1")
        if not 0.0 <= self.routing_min_confidence <= 1.0:
            raise ValueError("routing_min_confidence must be between 0 and 1")
        if self.routing_batch_size < 1:
            raise ValueError("routing_batch_size must be positive")
        if self.routing_context_radius < 0:
            raise ValueError("routing_context_radius cannot be negative")
        if self.routing_scene_gap < 0:
            raise ValueError("routing_scene_gap cannot be negative")
        if self.routing_previous_speaker_limit < 0:
            raise ValueError("routing_previous_speaker_limit cannot be negative")
        if not 0.0 <= self.consistency_unresolved_spike_ratio <= 1.0:
            raise ValueError("consistency_unresolved_spike_ratio must be between 0 and 1")
        if self.ai_max_input_chars < 200:
            raise ValueError("ai_max_input_chars must be at least 200")
        if self.validity_min_quote_pairs < 0:
            raise ValueError("validity_min_quote_pairs must be non-negative")
        if self.validity_min_dialogue_keywords < 0:
            raise ValueError("validity_min_dialogue_keywords must be non-negative")
        if self.validity_min_speaker_patterns < 0:
            raise ValueError("validity_min_speaker_patterns must be non-negative")
        if self.validity_min_bible_chars < 0:
            raise ValueError("validity_min_bible_chars must be non-negative")
        if not 0.0 <= self.validity_max_unresolved_ratio <= 1.0:
            raise ValueError("validity_max_unresolved_ratio must be between 0 and 1")
        if not 0.0 <= self.validity_min_ai_request_ratio <= 1.0:
            raise ValueError("validity_min_ai_request_ratio must be between 0 and 1")
        if self.validity_retry_max < 0:
            raise ValueError("validity_retry_max must be non-negative")


DEFAULT_V4_ANALYSIS_CONFIG = V4AnalysisConfig()
