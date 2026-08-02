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


DEFAULT_V4_ANALYSIS_CONFIG = V4AnalysisConfig()
