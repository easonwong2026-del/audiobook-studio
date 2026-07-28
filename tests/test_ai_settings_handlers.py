"""AI 设置页真实回调测试。

直接调用 handler 和 service 方法，不依赖 Gradio UI，不依赖真实 Keyring 或网络。
"""
from __future__ import annotations

import os
import tempfile

import pytest


# ── 辅助工具 ─────────────────────────────────────────────────────────────

def _patch_secrets(monkeypatch, keyring_keys: dict = None, env_key: str = None):
    """Mock keyring ops with in-memory dict and optional env var.

    Args:
        keyring_keys: {provider: api_key} for stored secrets.
        env_key: "OPENAI_API_KEY=sk-env-test" style env var.
    """
    store = {}
    if keyring_keys:
        key_map = {"openai": "openai_api_key", "deepseek": "deepseek_api_key"}
        for prov, key in keyring_keys.items():
            store[key_map.get(prov, prov)] = key

    def _get(service, username):
        return store.get(username)
    def _set(service, username, password):
        store[username] = password
    def _delete(service, username):
        store.pop(username, None)

    import services.ai_settings as svc
    monkeypatch.setattr(svc, "_get_secret", _get)
    monkeypatch.setattr(svc, "_set_secret", _set)
    monkeypatch.setattr(svc, "_delete_secret", _delete)

    # Isolate config file
    cfg_path = os.path.join(tempfile.mkdtemp(), "config.json")
    monkeypatch.setattr(svc, "_CONFIG_PATH", cfg_path)

    # Clear interfering env vars
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    if env_key:
        if env_key.startswith("OPENAI_API_KEY"):
            monkeypatch.setenv("OPENAI_API_KEY", env_key.split("=", 1)[1])
        elif env_key.startswith("DEEPSEEK_API_KEY"):
            monkeypatch.setenv("DEEPSEEK_API_KEY", env_key.split("=", 1)[1])


def _save_provider_config(monkeypatch, **overrides):
    """写入隔离配置。"""
    from services.ai_settings import AiSettingsService
    cfg = AiSettingsService.get_provider_config()
    cfg.update(overrides)
    AiSettingsService.save_provider_config(cfg)


# ═══════════════════════════════════════════════════════════════
# 1. Provider 切换测试
# ═══════════════════════════════════════════════════════════════

class TestProviderSwitch:
    def test_switch_openai_returns_correct_values(self, monkeypatch):
        """OpenAI 切换返回 5 值、模型正确、Base URL 正确、Key 为空。"""
        _patch_secrets(monkeypatch)
        _save_provider_config(monkeypatch,
            default_provider="openai",
            openai_model="gpt-test",
            openai_base_url="https://openai.test/v1",
            deepseek_model="deepseek-test",
            timeout=120)

        from ui.director_handlers import update_provider_config_fields
        result = update_provider_config_fields("openai")
        assert len(result) == 5, f"应返回 5 个值，得到 {len(result)}"
        status, model_upd, key_upd, url_upd, btn_upd = result
        assert model_upd["value"] == "gpt-test"
        assert "openai.test" in url_upd["value"]
        assert key_upd["value"] == "", "API Key 输入框必须为空"
        assert key_upd["visible"] is True

    def test_switch_deepseek_returns_correct_values(self, monkeypatch):
        """DeepSeek 切换不继承 OpenAI 模型。"""
        _patch_secrets(monkeypatch)
        _save_provider_config(monkeypatch,
            default_provider="openai",
            openai_model="gpt-only",
            deepseek_model="deepseek-test",
            deepseek_base_url="https://deepseek.test",
            timeout=120)

        from ui.director_handlers import update_provider_config_fields
        result = update_provider_config_fields("deepseek")
        assert len(result) == 5
        _, model_upd, _, url_upd, _ = result
        assert model_upd["value"] == "deepseek-test", "应加载 DeepSeek 模型"
        assert model_upd["value"] != "gpt-only", "不应继承 OpenAI 模型"
        assert "deepseek.test" in url_upd["value"]

    def test_switch_local_hides_all_fields(self, monkeypatch):
        """Local 切换时隐藏 API Key、Base URL、清除按钮。"""
        _patch_secrets(monkeypatch)
        from ui.director_handlers import update_provider_config_fields
        status, model_upd, key_upd, url_upd, btn_upd = update_provider_config_fields("local")
        assert key_upd["visible"] is False
        assert url_upd["visible"] is False
        assert btn_upd["visible"] is False
        assert "无需配置" in status

    def test_no_key_leak_in_result(self, monkeypatch):
        """切换返回值中不包含完整 API Key。"""
        _patch_secrets(monkeypatch, {"openai": "sk-super-secret-test-value"})
        _save_provider_config(monkeypatch, default_provider="openai")
        from ui.director_handlers import update_provider_config_fields
        result_tuple = update_provider_config_fields("openai")
        serialized = str(result_tuple)
        assert "sk-super-secret-test-value" not in serialized


# ═══════════════════════════════════════════════════════════════
# 2. 设置加载测试
# ═══════════════════════════════════════════════════════════════

class TestLoadSettings:
    def test_load_returns_7_values(self, monkeypatch):
        """load_ai_settings 严格返回 7 个值。"""
        _patch_secrets(monkeypatch)
        from ui.director_handlers import load_ai_settings
        result = load_ai_settings()
        assert isinstance(result, tuple)
        assert len(result) == 7, f"应返回 7 个值，得到 {len(result)}"

    def test_load_openai_config(self, monkeypatch):
        """加载已保存的 OpenAI 配置。"""
        _patch_secrets(monkeypatch)
        _save_provider_config(monkeypatch,
            default_provider="openai",
            openai_model="gpt-4-turbo",
            openai_base_url="https://custom.openai.com",
            timeout=90)

        from ui.director_handlers import load_ai_settings
        provider, model, url_upd, timeout, status, key_upd, btn_upd = load_ai_settings()
        assert provider == "openai"
        assert model == "gpt-4-turbo"
        assert "custom.openai.com" in str(url_upd)
        assert timeout == 90
        assert key_upd["value"] == "", "API Key 输入框必须为空"

    def test_load_no_key_leak(self, monkeypatch):
        """加载返回内容中不包含完整密钥。"""
        _patch_secrets(monkeypatch, {"openai": "sk-hidden-key-value"})
        _save_provider_config(monkeypatch, default_provider="openai")
        from ui.director_handlers import load_ai_settings
        serialized = str(load_ai_settings())
        assert "sk-hidden-key-value" not in serialized

    def test_load_clear_button_keyring(self, monkeypatch):
        """Keyring 有密钥时显示清除按钮。"""
        _patch_secrets(monkeypatch, {"openai": "sk-test"})
        _save_provider_config(monkeypatch, default_provider="openai")
        from ui.director_handlers import load_ai_settings
        *_, btn_upd = load_ai_settings()
        assert btn_upd["visible"] is True

    def test_load_clear_button_no_keyring(self, monkeypatch):
        """Keyring 无密钥时不显示清除按钮。"""
        _patch_secrets(monkeypatch)  # no keys
        _save_provider_config(monkeypatch, default_provider="openai")
        from ui.director_handlers import load_ai_settings
        *_, btn_upd = load_ai_settings()
        assert btn_upd["visible"] is False


# ═══════════════════════════════════════════════════════════════
# 3. 保存设置测试
# ═══════════════════════════════════════════════════════════════

class TestSaveSettings:
    def test_save_returns_4_values(self, monkeypatch):
        """save_ai_settings 严格返回 4 个值。"""
        _patch_secrets(monkeypatch)
        from ui.director_handlers import save_ai_settings
        result = save_ai_settings("openai", "gpt-4", "sk-test", "", 120)
        assert len(result) == 4, f"应返回 4 个值，得到 {len(result)}"

    def test_save_key_clears_input_and_shows_button(self, monkeypatch):
        """新密钥保存后 API Key 输入框被清空，清除按钮可见。"""
        _patch_secrets(monkeypatch)
        from ui.director_handlers import save_ai_settings
        msg, status_html, key_upd, btn_upd = save_ai_settings(
            "openai", "gpt-4", "sk-new-test", "https://api.openai.com/v1", 120)
        assert "✅" in msg or "已保存" in msg
        assert key_upd["value"] == ""
        assert btn_upd["visible"] is True

    def test_save_empty_key_no_overwrite(self, monkeypatch):
        """空 Key 保存不覆盖已有密钥。"""
        _patch_secrets(monkeypatch, {"openai": "sk-existing"})
        from services.ai_settings import AiSettingsService
        from ui.director_handlers import save_ai_settings

        # Save non-empty key first
        AiSettingsService.set_api_key("openai", "sk-existing-key")
        assert AiSettingsService.get_api_key("openai") == "sk-existing-key"

        # Now save with empty key
        save_ai_settings("openai", "gpt-4", "", "https://api.openai.com/v1", 120)
        assert AiSettingsService.get_api_key("openai") == "sk-existing-key"

    def test_save_no_key_leak(self, monkeypatch):
        """保存返回内容中不包含完整密钥。"""
        _patch_secrets(monkeypatch)
        from ui.director_handlers import save_ai_settings
        result = save_ai_settings("openai", "gpt-4", "sk-confidential-99999", "", 120)
        serialized = str(result)
        assert "sk-confidential-99999" not in serialized


# ═══════════════════════════════════════════════════════════════
# 4. 密钥来源测试
# ═══════════════════════════════════════════════════════════════

class TestKeySource:
    def test_source_keyring(self, monkeypatch):
        """Keyring 存在时来源为 keyring。"""
        _patch_secrets(monkeypatch, {"openai": "sk-keyring"})
        from services.ai_settings import AiSettingsService
        assert AiSettingsService.get_api_key_source("openai") == "keyring"
        assert AiSettingsService.has_stored_api_key("openai") is True

    def test_source_environment(self, monkeypatch):
        """环境变量存在时来源为 environment。"""
        _patch_secrets(monkeypatch)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env-value")
        from services.ai_settings import AiSettingsService
        assert AiSettingsService.get_api_key_source("openai") == "environment"
        assert AiSettingsService.has_stored_api_key("openai") is False

    def test_source_none(self, monkeypatch):
        """无密钥时来源为 none。"""
        _patch_secrets(monkeypatch)
        from services.ai_settings import AiSettingsService
        assert AiSettingsService.get_api_key_source("openai") == "none"
        assert AiSettingsService.has_api_key("openai") is False

    def test_environment_status_no_clear_button(self, monkeypatch):
        """环境变量来源时不显示清除按钮。"""
        _patch_secrets(monkeypatch)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env-value")
        from ui.director_handlers import update_provider_config_fields
        _, _, _, _, btn_upd = update_provider_config_fields("openai")
        assert btn_upd["visible"] is False

    def test_keyring_status_shows_clear_button(self, monkeypatch):
        """Keyring 来源时显示清除按钮。"""
        _patch_secrets(monkeypatch, {"openai": "sk-keyring"})
        from ui.director_handlers import update_provider_config_fields
        _, _, _, _, btn_upd = update_provider_config_fields("openai")
        assert btn_upd["visible"] is True


# ═══════════════════════════════════════════════════════════════
# 5. 清除密钥回调测试
# ═══════════════════════════════════════════════════════════════

class TestClearKey:
    def test_clear_returns_4_values(self, monkeypatch):
        """clear_ai_api_key 严格返回 4 个值。"""
        _patch_secrets(monkeypatch, {"openai": "sk-test"})
        from ui.director_handlers import clear_ai_api_key
        result = clear_ai_api_key("openai")
        assert len(result) == 4, f"应返回 4 个值，得到 {len(result)}"

    def test_clear_deletes_keyring(self, monkeypatch):
        """清除后 Keyring 中密钥不存在。"""
        _patch_secrets(monkeypatch, {"openai": "sk-test"})
        from services.ai_settings import AiSettingsService
        from ui.director_handlers import clear_ai_api_key
        assert AiSettingsService.has_stored_api_key("openai") is True
        clear_ai_api_key("openai")
        assert AiSettingsService.has_stored_api_key("openai") is False

    def test_clear_clears_input_and_hides_button(self, monkeypatch):
        """清除后输入框清空、按钮隐藏、成功提示。"""
        _patch_secrets(monkeypatch, {"openai": "sk-test"})
        from ui.director_handlers import clear_ai_api_key
        status, key_upd, btn_upd, msg = clear_ai_api_key("openai")
        assert key_upd["value"] == ""
        assert btn_upd["visible"] is False
        assert "✅" in msg

    def test_clear_failure_shows_error(self, monkeypatch):
        """清除失败时返回错误提示。"""
        _patch_secrets(monkeypatch, {"openai": "sk-test"})
        import services.ai_settings as svc

        def broken_delete(service, username):
            raise RuntimeError("test failure")
        monkeypatch.setattr(svc, "_delete_secret", broken_delete)

        from ui.director_handlers import clear_ai_api_key
        _, _, _, msg = clear_ai_api_key("openai")
        assert "❌" in msg
        assert "失败" in msg
        assert "test failure" not in msg  # 检查安全转义

    def test_clear_no_key_leak(self, monkeypatch):
        """清除返回值中不包含完整密钥。"""
        _patch_secrets(monkeypatch, {"openai": "sk-super-secret-12345"})
        from ui.director_handlers import clear_ai_api_key
        serialized = str(clear_ai_api_key("openai"))
        assert "sk-super-secret-12345" not in serialized


# ═══════════════════════════════════════════════════════════════
# 6. 当前配置连接测试
# ═══════════════════════════════════════════════════════════════

class TestConnectionForm:
    def _mock_urlopen(self, monkeypatch, requests_log=None):
        """Mock urllib.request.urlopen."""
        import urllib.request
        class FakeResponse:
            def read(self): return b"{}"
            def __enter__(self): return self
            def __exit__(self, *a): pass

        def fake_urlopen(req, timeout=30):
            if requests_log is not None:
                requests_log.append({
                    "url": req.full_url,
                    "method": req.get_method(),
                    "auth": req.headers.get("Authorization", ""),
                })
            return FakeResponse()

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    def test_uses_current_form_key(self, monkeypatch):
        """连接测试优先使用当前表单输入的 API Key。"""
        _patch_secrets(monkeypatch, {"openai": "sk-keyring-backup"})
        log = []
        self._mock_urlopen(monkeypatch, log)

        from services.ai_settings import AiSettingsService
        result = AiSettingsService.check_connection(
            "openai", api_key="sk-form-test", base_url="https://form.test.com", timeout=30)
        assert "✅" in result
        assert "sk-form-test" in log[0]["auth"]
        assert "sk-keyring-backup" not in log[0]["auth"]

    def test_empty_key_falls_back(self, monkeypatch):
        """Key 为空时回退已保存密钥。"""
        _patch_secrets(monkeypatch, {"openai": "sk-keyring-fallback"})
        log = []
        self._mock_urlopen(monkeypatch, log)

        from services.ai_settings import AiSettingsService
        result = AiSettingsService.check_connection(
            "openai", api_key="", base_url="https://api.openai.com/v1", timeout=30)
        assert "✅" in result
        assert "sk-keyring-fallback" in log[0]["auth"]

    def test_uses_current_base_url(self, monkeypatch):
        """连接测试使用当前表单输入的 Base URL。"""
        _patch_secrets(monkeypatch, {"openai": "sk-test"})
        log = []
        self._mock_urlopen(monkeypatch, log)

        from services.ai_settings import AiSettingsService
        AiSettingsService.check_connection(
            "openai", api_key="sk-test", base_url="https://my-custom-proxy.com/v1", timeout=30)
        assert log[0]["url"] == "https://my-custom-proxy.com/v1/models"

    def test_uses_get_method(self, monkeypatch):
        """连接测试使用 GET 方法。"""
        _patch_secrets(monkeypatch, {"openai": "sk-test"})
        log = []
        self._mock_urlopen(monkeypatch, log)

        from services.ai_settings import AiSettingsService
        AiSettingsService.check_connection(
            "openai", api_key="sk-test", base_url="https://api.openai.com/v1", timeout=30)
        assert log[0]["method"] == "GET"

    def test_does_not_write_config(self, monkeypatch):
        """连接测试不修改配置文件。"""
        _patch_secrets(monkeypatch, {"openai": "sk-test"})
        self._mock_urlopen(monkeypatch)

        from services.ai_settings import AiSettingsService
        cfg_before = AiSettingsService.get_provider_config().copy()
        AiSettingsService.check_connection(
            "openai", api_key="sk-test", base_url="https://api.openai.com/v1", timeout=30)
        assert AiSettingsService.get_provider_config() == cfg_before

    def test_no_key_leak_in_result(self, monkeypatch):
        """测试结果中不包含完整 API Key。"""
        _patch_secrets(monkeypatch, {"openai": "sk-super-secret-value"})
        self._mock_urlopen(monkeypatch)

        from services.ai_settings import AiSettingsService
        result = AiSettingsService.check_connection(
            "openai", api_key="sk-super-secret-value",
            base_url="https://api.openai.com/v1", timeout=30)
        assert "sk-super-secret-value" not in result
