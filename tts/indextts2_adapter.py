"""Resident IndexTTS2 adapter; never modifies the upstream checkout."""
from __future__ import annotations

import importlib
import inspect
import wave
from collections.abc import Callable
from pathlib import Path
from threading import RLock
from typing import Any

from domain.v4.production import TtsProfile
from tts.base_adapter import (
    EmptyAudioError,
    SynthesisOutput,
    TtsLengthLimitError,
    TtsOutOfMemoryError,
)

VoicePromptResolver = Callable[[str], str | Path]


class IndexTTS2Adapter:
    def __init__(
        self,
        model_dir: str | Path,
        voice_prompt_resolver: VoicePromptResolver,
        *,
        engine_class: type[Any] | None = None,
    ):
        self.model_dir = Path(model_dir)
        self.voice_prompt_resolver = voice_prompt_resolver
        self._engine_class = engine_class
        self._engine: Any | None = None
        self._lock = RLock()

    def synthesize(
        self,
        task: dict[str, Any],
        profile: TtsProfile,
        output_path: Path,
    ) -> SynthesisOutput:
        with self._lock:
            engine = self._get_engine(profile)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            signature = inspect.signature(engine.infer)
            parameters = signature.parameters
            has_kwargs = any(
                item.kind == inspect.Parameter.VAR_KEYWORD
                for item in parameters.values()
            )
            text = str(task["actual_text"])
            kwargs: dict[str, Any] = {
                "spk_audio_prompt": str(
                    self.voice_prompt_resolver(str(task["voice_id"]))
                ),
                "text": text,
                "output_path": str(output_path),
                "use_emo_text": bool(profile.emotion.get("use_emo_text", True)),
                "emo_text": text,
                "emo_alpha": float(profile.emotion.get("emo_alpha", 0.55)),
                "max_text_tokens_per_segment": profile.limits.maximum,
            }
            options = profile.options
            if "num_beams" in parameters or has_kwargs:
                kwargs["num_beams"] = int(options.get("num_beams", 2))
            kwargs = {
                key: value
                for key, value in kwargs.items()
                if key in parameters or (has_kwargs and key == "num_beams")
            }
            try:
                engine.infer(**kwargs)
            except Exception as exc:  # noqa: BLE001 - classify upstream engine errors
                self._raise_classified(exc)
            self._validate_wav(output_path)
            return SynthesisOutput(output_path)

    def close(self) -> None:
        with self._lock:
            self._engine = None

    def _get_engine(self, profile: TtsProfile) -> Any:
        if self._engine is not None:
            return self._engine
        engine_class = self._engine_class
        if engine_class is None:
            module = importlib.import_module("indextts.infer_v2")
            engine_class = module.IndexTTS2
        cfg_path = self.model_dir / "config.yaml"
        if not self.model_dir.is_dir() or not cfg_path.is_file():
            raise FileNotFoundError(
                "IndexTTS2 model directory is missing config.yaml"
            )
        candidates = {
            "cfg_path": str(cfg_path),
            "model_dir": str(self.model_dir),
            "use_fp16": bool(profile.options.get("fp16", True)),
            "use_deepspeed": bool(profile.options.get("deepspeed", False)),
            "use_accel": bool(profile.options.get("accel", False)),
            "use_cuda_kernel": bool(profile.options.get("cuda_kernel", False)),
        }
        signature = inspect.signature(engine_class)
        kwargs = {
            key: value for key, value in candidates.items() if key in signature.parameters
        }
        self._engine = engine_class(**kwargs)
        return self._engine

    @staticmethod
    def _raise_classified(exc: Exception) -> None:
        label = f"{type(exc).__name__}: {exc}".lower()
        if "outofmemory" in label or "out of memory" in label or "cuda oom" in label:
            raise TtsOutOfMemoryError("IndexTTS2 CUDA out of memory") from exc
        if "length" in label or "token" in label or "too long" in label:
            raise TtsLengthLimitError("IndexTTS2 text length limit") from exc
        raise exc

    @staticmethod
    def _validate_wav(path: Path) -> None:
        try:
            with wave.open(str(path), "rb") as handle:
                if handle.getnframes() <= 0 or handle.getframerate() <= 0:
                    raise EmptyAudioError("IndexTTS2 produced empty audio")
        except (wave.Error, OSError) as exc:
            raise EmptyAudioError("IndexTTS2 produced invalid audio") from exc
