"""ConfigRepository：配置原子读写 + ConfigData 数据类。

ConfigData 为 frozen dataclass，提供 to_dict() / from_dict() 序列化。
ConfigRepository 全部为 @staticmethod，无实例状态。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, ClassVar

from ._atomic import atomic_write as _atomic_write

logger = logging.getLogger(__name__)

# PROGRAM_DIR 与 lib/config.py 保持一致
_PROGRAM_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass(frozen=True)
class ConfigData:
    """配置数据 dataclass（frozen，不可变）。

    Attributes:
        data_dir: 数据根目录。
        model_dir: 模型目录。
        ffmpeg_path: ffmpeg 可执行文件路径。
        cache_retention_days: 缓存保留天数（默认 7）。
        sample_rate: 采样率（默认 24000）。
        channels: 声道数（默认 1）。
        default_format: 默认输出格式（默认 "wav"）。
    """
    data_dir: str = ""
    model_dir: str = ""
    ffmpeg_path: str = ""
    cache_retention_days: int = 7
    sample_rate: int = 24000
    channels: int = 1
    default_format: str = "wav"
    engine_backend: str = "indextts"
    engine_version: str = ""
    model_dir_v2: str = ""
    model_dir_v25: str = ""
    tts_precision: str = ""

    def to_dict(self) -> dict:
        """序列化为 dict（略去空值字段以保持向后兼容）。"""
        d = {
            "data_dir": self.data_dir,
            "cache_retention_days": self.cache_retention_days,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "default_format": self.default_format,
        }
        if self.model_dir:
            d["model_dir"] = self.model_dir
        if self.ffmpeg_path:
            d["ffmpeg_path"] = self.ffmpeg_path
        if self.engine_backend:
            d["engine_backend"] = self.engine_backend
        if self.engine_version:
            d["engine_version"] = self.engine_version
        if self.model_dir_v2:
            d["model_dir_v2"] = self.model_dir_v2
        if self.model_dir_v25:
            d["model_dir_v25"] = self.model_dir_v25
        if self.tts_precision:
            d["tts_precision"] = self.tts_precision
        return d

    @staticmethod
    def from_dict(data: dict) -> "ConfigData":
        """从 dict 反序列化，缺省字段使用默认值。"""
        return ConfigData(
            data_dir=data.get("data_dir", ""),
            model_dir=data.get("model_dir", ""),
            ffmpeg_path=data.get("ffmpeg_path", ""),
            cache_retention_days=data.get("cache_retention_days", 7),
            sample_rate=data.get("sample_rate", 24000),
            channels=data.get("channels", 1),
            default_format=data.get("default_format", "wav"),
            engine_backend=data.get("engine_backend", data.get("engine", "indextts")),
            engine_version=data.get("engine_version", data.get("tts_version", "")),
            model_dir_v2=data.get("model_dir_v2", ""),
            model_dir_v25=data.get("model_dir_v25", data.get("indextts25_model_dir", "")),
            tts_precision=data.get("tts_precision", data.get("precision", "")),
        )


class ConfigRepository:
    """配置仓库：读取 / 写入 config.json，全部静态方法。

    默认 CONFIG_PATH 与 ``lib/config.py`` 一致（PROGRAM_DIR/config.json）。
    测试通过 ``monkeypatch.setattr(ConfigRepository, "CONFIG_PATH", ...)`` 隔离。
    """
    CONFIG_PATH: ClassVar[str] = os.path.join(_PROGRAM_DIR, "config.json")

    @staticmethod
    def load() -> ConfigData:
        """读 config.json，文件不存在或解析失败时返回默认 ConfigData。

        Returns:
            ConfigData 实例。
        """
        path = ConfigRepository.CONFIG_PATH
        if not os.path.isfile(path):
            return ConfigData()
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return ConfigData.from_dict(data)
            return ConfigData()
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("config.json 解析失败，回退默认配置: %s", exc)
            return ConfigData()

    @staticmethod
    def save(config: ConfigData) -> None:
        """原子写 config.json。

        Args:
            config: ConfigData 实例。

        Raises:
            AtomicWriteError: 写入失败时抛出。
        """
        data: dict[str, Any] = {}
        if os.path.isfile(ConfigRepository.CONFIG_PATH):
            try:
                with open(ConfigRepository.CONFIG_PATH, encoding="utf-8") as f:
                    existing = json.load(f)
                if isinstance(existing, dict):
                    data.update(existing)
            except (json.JSONDecodeError, OSError):
                pass
        data.update(config.to_dict())
        _atomic_write(ConfigRepository.CONFIG_PATH, data)

    @staticmethod
    def set_data_dir(path: str) -> str:
        """设置并持久化 data_dir。

        Args:
            path: 新数据目录路径。

        Returns:
            规范化后的绝对路径。
        """
        abs_path = os.path.abspath(path)
        os.makedirs(abs_path, exist_ok=True)
        cfg = ConfigRepository.load()
        new_cfg = ConfigData(
            data_dir=abs_path,
            model_dir=cfg.model_dir,
            ffmpeg_path=cfg.ffmpeg_path,
            cache_retention_days=cfg.cache_retention_days,
            sample_rate=cfg.sample_rate,
            channels=cfg.channels,
            default_format=cfg.default_format,
            engine_backend=cfg.engine_backend,
            engine_version=cfg.engine_version,
            model_dir_v2=cfg.model_dir_v2,
            model_dir_v25=cfg.model_dir_v25,
            tts_precision=cfg.tts_precision,
        )
        ConfigRepository.save(new_cfg)
        return abs_path

    @staticmethod
    def set_model_dir(path: str) -> str:
        """设置并持久化 model_dir。

        Args:
            path: 新模型目录路径。

        Returns:
            规范化后的绝对路径。
        """
        abs_path = os.path.abspath(path)
        cfg = ConfigRepository.load()
        new_cfg = ConfigData(
            data_dir=cfg.data_dir,
            model_dir=abs_path,
            ffmpeg_path=cfg.ffmpeg_path,
            cache_retention_days=cfg.cache_retention_days,
            sample_rate=cfg.sample_rate,
            channels=cfg.channels,
            default_format=cfg.default_format,
            engine_backend=cfg.engine_backend,
            engine_version=cfg.engine_version,
            model_dir_v2=abs_path,
            model_dir_v25=cfg.model_dir_v25,
            tts_precision=cfg.tts_precision,
        )
        ConfigRepository.save(new_cfg)
        return abs_path

    @staticmethod
    def get(key: str, default: Any = None) -> Any:
        """读取 raw config.json 的单个键（兼容 lib/config.py 既有调用方）。

        Args:
            key: 配置键名。
            default: 缺省值。

        Returns:
            配置值，键不存在或 JSON 解析失败时返回 default。
        """
        path = ConfigRepository.CONFIG_PATH
        if not os.path.isfile(path):
            return default
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data.get(key, default) if isinstance(data, dict) else default
        except (json.JSONDecodeError, OSError):
            return default
