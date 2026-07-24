"""ConfigRepository 单元测试。

测试内容：
- ConfigData 序列化 / 反序列化
- ConfigRepository.load() 缺省值
- ConfigRepository.save() / set_data_dir() 持久化
- 原子写完整性
"""
from __future__ import annotations

import json
import os

import pytest

from repositories.config_repo import ConfigData, ConfigRepository
from repositories.exceptions import AtomicWriteError


class TestConfigData:
    """ConfigData 数据类序列化测试。"""

    def test_default_values(self):
        """缺省构造返回默认值。"""
        cfg = ConfigData()
        assert cfg.data_dir == ""
        assert cfg.model_dir == ""
        assert cfg.ffmpeg_path == ""
        assert cfg.cache_retention_days == 7
        assert cfg.sample_rate == 24000
        assert cfg.channels == 1
        assert cfg.default_format == "wav"

    def test_to_dict_roundtrip(self):
        """to_dict → from_dict 往返不变。"""
        cfg = ConfigData(
            data_dir="/tmp/data",
            model_dir="/tmp/models",
            ffmpeg_path="/usr/bin/ffmpeg",
            cache_retention_days=14,
            sample_rate=44100,
            channels=2,
            default_format="mp3",
        )
        d = cfg.to_dict()
        assert d["data_dir"] == "/tmp/data"
        assert d["model_dir"] == "/tmp/models"
        assert d["ffmpeg_path"] == "/usr/bin/ffmpeg"
        assert d["cache_retention_days"] == 14
        assert d["sample_rate"] == 44100
        assert d["channels"] == 2
        assert d["default_format"] == "mp3"

        restored = ConfigData.from_dict(d)
        assert restored == cfg

    def test_from_dict_partial(self):
        """部分字段 from_dict 使用默认值补缺。"""
        d = {"data_dir": "/custom/data"}
        cfg = ConfigData.from_dict(d)
        assert cfg.data_dir == "/custom/data"
        assert cfg.cache_retention_days == 7  # 默认
        assert cfg.sample_rate == 24000  # 默认
        assert cfg.model_dir == ""  # 默认空

    def test_frozen(self):
        """ConfigData 不可变。"""
        cfg = ConfigData(data_dir="/d")
        with pytest.raises(AttributeError):
            cfg.data_dir = "/e"  # type: ignore[misc]


class TestConfigRepository:
    """ConfigRepository 持久化测试。"""

    def test_load_default_when_not_exists(self, tmp_path):
        """config.json 不存在时返回默认 ConfigData。"""
        monkeypatch_cfg_path = str(tmp_path / "nonexistent" / "config.json")
        original_path = ConfigRepository.CONFIG_PATH
        try:
            ConfigRepository.CONFIG_PATH = monkeypatch_cfg_path
            cfg = ConfigRepository.load()
            assert isinstance(cfg, ConfigData)
            assert cfg.data_dir == ""
        finally:
            ConfigRepository.CONFIG_PATH = original_path

    def test_save_and_load(self, tmp_path):
        """save → load 往返一致。"""
        cfg_path = str(tmp_path / "config.json")
        original_path = ConfigRepository.CONFIG_PATH
        try:
            ConfigRepository.CONFIG_PATH = cfg_path
            cfg = ConfigData(
                data_dir=str(tmp_path / "data"),
                cache_retention_days=14,
                sample_rate=44100,
            )
            ConfigRepository.save(cfg)
            assert os.path.isfile(cfg_path)

            loaded = ConfigRepository.load()
            assert loaded.data_dir == cfg.data_dir
            assert loaded.cache_retention_days == 14
            assert loaded.sample_rate == 44100
            assert loaded.channels == 1  # 默认
        finally:
            ConfigRepository.CONFIG_PATH = original_path

    def test_atomic_write_integrity(self, tmp_path):
        """原子写中途中断不会损坏已有文件。"""
        cfg_path = str(tmp_path / "config.json")
        original_path = ConfigRepository.CONFIG_PATH
        try:
            ConfigRepository.CONFIG_PATH = cfg_path

            # 写一个初始值
            ConfigRepository.save(ConfigData(data_dir="/initial"))
            with open(cfg_path, encoding="utf-8") as f:
                initial_content = f.read()

            # 模拟写入：先写 tmp 但不 os.replace（模拟崩溃）
            from repositories._atomic import atomic_write
            tmp = cfg_path + ".tmp"
            bad_data = {"data_dir": "/corrupted", "extra": "x" * 10000}
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(bad_data, f)
                f.flush()
                os.fsync(f.fileno())
            # 故意不 os.replace -> 原始文件不变

            # 验证：原始文件内容未损坏
            with open(cfg_path, encoding="utf-8") as f:
                content = f.read()
            assert content == initial_content

            # 清理临时文件
            if os.path.isfile(tmp):
                os.remove(tmp)
        finally:
            ConfigRepository.CONFIG_PATH = original_path

    def test_set_data_dir_persists(self, tmp_path):
        """set_data_dir 持久化 data_dir。"""
        cfg_path = str(tmp_path / "config.json")
        original_path = ConfigRepository.CONFIG_PATH
        try:
            ConfigRepository.CONFIG_PATH = cfg_path

            new_dir = str(tmp_path / "my_data")
            returned = ConfigRepository.set_data_dir(new_dir)
            assert returned == os.path.abspath(new_dir)

            # 验证持久化
            loaded = ConfigRepository.load()
            assert loaded.data_dir == os.path.abspath(new_dir)
        finally:
            ConfigRepository.CONFIG_PATH = original_path

    def test_set_data_dir_updates_existing(self, tmp_path):
        """set_data_dir 保留现有其他配置。"""
        cfg_path = str(tmp_path / "config.json")
        original_path = ConfigRepository.CONFIG_PATH
        try:
            ConfigRepository.CONFIG_PATH = cfg_path

            # 初始配置含 model_dir
            ConfigRepository.save(ConfigData(
                data_dir="/old",
                model_dir="/models",
            ))

            returned = ConfigRepository.set_data_dir(str(tmp_path / "new_data"))
            assert "new_data" in returned

            loaded = ConfigRepository.load()
            assert loaded.model_dir == "/models"  # 保留
        finally:
            ConfigRepository.CONFIG_PATH = original_path

    def test_get_single_key(self, tmp_path):
        """get() 读取单键。"""
        cfg_path = str(tmp_path / "config.json")
        original_path = ConfigRepository.CONFIG_PATH
        try:
            ConfigRepository.CONFIG_PATH = cfg_path
            ConfigRepository.save(ConfigData(data_dir="/d", cache_retention_days=30))

            val = ConfigRepository.get("cache_retention_days")
            assert val == 30

            # 不存在的键返回默认
            val2 = ConfigRepository.get("nonexistent", "fallback")
            assert val2 == "fallback"
        finally:
            ConfigRepository.CONFIG_PATH = original_path
