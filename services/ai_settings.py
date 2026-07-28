"""AI Provider 配置服务：管理非敏感配置与密钥。

敏感配置（API Key）通过 Keyring 管理，不会明文进入 config.json。
非敏感配置（默认 Provider、模型、Base URL、超时）存入 config.json。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

_CONFIG_SECTION = "ai_provider"
_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config.json",
)
KEYRING_SERVICE = "AudiobookStudio"
KEYRING_OPENAI_KEY = "openai_api_key"
KEYRING_DEEPSEEK_KEY = "deepseek_api_key"


class SecretStoreUnavailableError(RuntimeError):
    """Keyring 不可用时的错误。"""


def _read_full_config() -> dict[str, Any]:
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_full_config(cfg: dict[str, Any]) -> None:
    import tempfile
    os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=os.path.dirname(_CONFIG_PATH),
            prefix=".config.", suffix=".tmp", delete=False,
        ) as f:
            tmp = f.name
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, _CONFIG_PATH)
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def _get_secret(service: str, username: str) -> Optional[str]:
    try:
        import keyring
        try:
            secret = keyring.get_password(service, username)
            if secret:
                return secret
        except Exception:
            pass
    except ImportError:
        pass
    return None


def _set_secret(service: str, username: str, password: str) -> None:
    try:
        import keyring
        keyring.set_password(service, username, password)
    except ImportError:
        raise SecretStoreUnavailableError(
            "Keyring 库未安装。请使用环境变量设置 API Key，"
            "或 `pip install keyring` 后重试。"
        ) from None
    except Exception as exc:
        raise SecretStoreUnavailableError(
            f"Keyring 写入失败：{exc}\n请使用环境变量设置 API Key。"
        ) from None


class AiSettingsService:
    """AI Provider 配置的读写和有效配置计算。"""

    @staticmethod
    def get_provider_config() -> dict[str, Any]:
        cfg = _read_full_config()
        return cfg.get(_CONFIG_SECTION, {})

    @staticmethod
    def save_provider_config(config: dict[str, Any]) -> None:
        cfg = _read_full_config()
        cfg[_CONFIG_SECTION] = config
        _write_full_config(cfg)

    @staticmethod
    def get_api_key(provider: str) -> Optional[str]:
        if provider == "openai":
            secret = _get_secret(KEYRING_SERVICE, KEYRING_OPENAI_KEY)
            return secret or os.getenv("OPENAI_API_KEY")
        elif provider == "deepseek":
            secret = _get_secret(KEYRING_SERVICE, KEYRING_DEEPSEEK_KEY)
            return secret or os.getenv("DEEPSEEK_API_KEY")
        return None

    @staticmethod
    def set_api_key(provider: str, api_key: str) -> None:
        if provider == "openai":
            _set_secret(KEYRING_SERVICE, KEYRING_OPENAI_KEY, api_key)
        elif provider == "deepseek":
            _set_secret(KEYRING_SERVICE, KEYRING_DEEPSEEK_KEY, api_key)

    @staticmethod
    def get_effective_provider_config(provider: Optional[str] = None) -> dict[str, Any]:
        saved = AiSettingsService.get_provider_config()
        name = provider or saved.get("default_provider", "local")
        return {
            "provider": name,
            "model": saved.get(f"{name}_model") or "",
            "base_url": saved.get(f"{name}_base_url") or "",
            "timeout": saved.get("timeout", 180),
            "api_key": AiSettingsService.get_api_key(name) or "",
        }

    @staticmethod
    def check_connection(provider: str) -> str:
        if provider == "local":
            return "✅ 本地离线基线无需网络连接。"

        api_key = AiSettingsService.get_api_key(provider)
        if not api_key:
            env_var = "OPENAI_API_KEY" if provider == "openai" else "DEEPSEEK_API_KEY"
            return (
                f"⚠ **{provider.title()} API Key 尚未配置**。\n\n"
                f"请前往「设置 → AI 模型」保存密钥，或设置环境变量 `{env_var}`。"
            )

        config = AiSettingsService.get_provider_config()
        base_url = config.get(f"{provider}_base_url", "")
        timeout = min(config.get("timeout", 180), 30)

        try:
            import urllib.request
            import urllib.error
            base_url = base_url or (
                "https://api.openai.com/v1"
                if provider == "openai"
                else "https://api.deepseek.com"
            )
            endpoint = f"{base_url.rstrip('/')}/models"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            req = urllib.request.Request(endpoint, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp.read()  # 仅确认不解析响应体
            return f"✅ **{provider.title()}** 连接成功。"
        except Exception as exc:
            msg = str(exc)[:200]
            return f"❌ 连接失败：{msg}"
