"""统一结果类型 OperationResult[T] 与错误码常量。

用于 Repository / Service 层方法的标准返回类型，取代裸露的 True/False/None
或未标记异常，使调用方能通过类型系统获知操作结果。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, Optional, TypeVar

T = TypeVar("T")

# ── 标准错误码 ──
OK = "OK"
PROJECT_NOT_OPEN = "PROJECT_NOT_OPEN"
PROJECT_NOT_FOUND = "PROJECT_NOT_FOUND"
SCRIPT_INVALID = "SCRIPT_INVALID"
VOICE_NOT_BOUND = "VOICE_NOT_BOUND"
AUDIO_FILE_MISSING = "AUDIO_FILE_MISSING"
MODEL_NOT_CONFIGURED = "MODEL_NOT_CONFIGURED"
MODEL_LOAD_FAILED = "MODEL_LOAD_FAILED"
SYNTHESIS_BUSY = "SYNTHESIS_BUSY"
SYNTHESIS_CANCELLED = "SYNTHESIS_CANCELLED"
FFMPEG_NOT_FOUND = "FFMPEG_NOT_FOUND"
AUDIO_FORMAT_UNSUPPORTED = "AUDIO_FORMAT_UNSUPPORTED"
EXPORT_FAILED = "EXPORT_FAILED"


@dataclass
class OperationResult(Generic[T]):
    """统一操作结果类型。

    Attributes:
        ok: 操作是否成功。
        data: 成功时的返回数据（可选）。
        user_message: 给用户看的错误消息（成功时可留空）。
        error_code: 错误码（成功时为 ``OK``）。
        technical_message: 技术详情（traceback 等，仅失败时填写）。
    """
    ok: bool = True
    data: Optional[T] = None
    user_message: str = ""
    error_code: Optional[str] = None
    technical_message: Optional[str] = None

    @classmethod
    def success(cls, data: T = None, user_message: str = "") -> "OperationResult[T]":
        """构造成功结果。"""
        return cls(ok=True, data=data, user_message=user_message, error_code=OK)

    @classmethod
    def failure(
        cls,
        error_code: str,
        user_message: str = "",
        technical_message: str = None,
        data: T = None,
    ) -> "OperationResult[T]":
        """构造失败结果。"""
        return cls(
            ok=False,
            data=data,
            user_message=user_message,
            error_code=error_code,
            technical_message=technical_message,
        )
