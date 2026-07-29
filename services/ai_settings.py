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
    except Exception:
        raise SecretStoreUnavailableError(
            "系统密钥环不可用，无法安全保存 API Key。"
            "请在 Windows 上配置可用的 Keyring 后端，或使用 OPENAI_API_KEY / "
            "DEEPSEEK_API_KEY 环境变量。"
        ) from None


def _delete_secret(service: str, username: str) -> None:
    try:
        import keyring
        try:
            keyring.delete_password(service, username)
        except keyring.errors.PasswordDeleteError:
            pass
        except Exception:
            raise SecretStoreUnavailableError(
                "系统密钥环不可用，无法删除 API Key。"
                "请检查 Keyring 后端权限，或使用环境变量管理密钥。"
            ) from None
    except ImportError:
        raise SecretStoreUnavailableError("Keyring 库未安装，无法删除系统密钥。") from None


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
    def has_api_key(provider: str) -> bool:
        """仅返回是否已配置 Key，不泄露密钥内容。"""
        return AiSettingsService.get_api_key(provider) is not None

    @staticmethod
    def has_stored_api_key(provider: str) -> bool:
        username = {"openai": KEYRING_OPENAI_KEY, "deepseek": KEYRING_DEEPSEEK_KEY}.get(provider)
        return bool(username and _get_secret(KEYRING_SERVICE, username))

    @staticmethod
    def get_api_key_source(provider: str) -> str:
        if AiSettingsService.has_stored_api_key(provider):
            return "keyring"
        env = {"openai": "OPENAI_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}.get(provider)
        return "environment" if env and os.getenv(env) else "none"

    @staticmethod
    def api_key_status(provider: str) -> str:
        """返回用户可读的密钥状态 HTML。"""
        for_source = f"环境变量 `{provider.upper()}_API_KEY`"
        if provider == "openai":
            for_source = "环境变量 `OPENAI_API_KEY`"
        elif provider == "deepseek":
            for_source = "环境变量 `DEEPSEEK_API_KEY`"
        has = AiSettingsService.has_api_key(provider)
        if has:
            source = AiSettingsService.get_api_key_source(provider)
            if source == "environment":
                return "<p style='color:#16a34a'>✅ API Key 已通过环境变量配置</p>"
            return (
                f"<p style='color:#16a34a'>✅ API Key 已配置</p>"
                f"<p style='color:#666;font-size:0.85em'>来源：Keyring 或 {for_source}</p>"
            )
        return (
            f"<p style='color:#d97706'>⚠ API Key 尚未配置</p>"
            f"<p style='color:#666;font-size:0.85em'>输入新密钥并保存，"
            f"或设置 {for_source}</p>"
        )

    @staticmethod
    def set_api_key(provider: str, api_key: str) -> None:
        if provider == "openai":
            _set_secret(KEYRING_SERVICE, KEYRING_OPENAI_KEY, api_key)
        elif provider == "deepseek":
            _set_secret(KEYRING_SERVICE, KEYRING_DEEPSEEK_KEY, api_key)

    @staticmethod
    def delete_api_key(provider: str) -> None:
        if provider == "openai":
            _delete_secret(KEYRING_SERVICE, KEYRING_OPENAI_KEY)
        elif provider == "deepseek":
            _delete_secret(KEYRING_SERVICE, KEYRING_DEEPSEEK_KEY)

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
    def check_connection(
        provider: str,
        api_key: str = "",
        base_url: str = "",
        timeout: float = 30.0,
    ) -> str:
        """测试 Provider 连接，使用提供的参数（优先）而非已保存配置。

        Args:
            provider: Provider 名称。
            api_key: 当前表单输入的 Key；空则回退已保存 Key。
            base_url: 当前表单输入的 URL；空则使用默认地址。
            timeout: 超时秒数。

        Returns:
            用户可读的测试结果 Markdown。
        """
        if provider == "local":
            return "✅ 本地离线基线无需网络连接。"

        effective_key = api_key.strip() if api_key and api_key.strip() else (
            AiSettingsService.get_api_key(provider) or ""
        )
        if not effective_key:
            return (
                f"⚠ **{provider.title()} API Key 尚未配置**。\n\n"
                f"请输入密钥并保存，或设置环境变量。"
            )

        effective_url = base_url.strip() if base_url and base_url.strip() else (
            "https://api.openai.com/v1"
            if provider == "openai"
            else "https://api.deepseek.com"
        )
        effective_timeout = min(max(float(timeout), 5), 60)

        try:
            import urllib.request
            import urllib.error
            endpoint = f"{effective_url.rstrip('/')}/models"
            headers = {
                "Authorization": f"Bearer {effective_key}",
                "Content-Type": "application/json",
            }
            req = urllib.request.Request(endpoint, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=effective_timeout) as resp:
                resp.read()
            return f"✅ **{provider.title()}** 连接成功。Endpoint：`{effective_url}`"
        except Exception as exc:
            msg = str(exc)[:200]
            # 不要在错误信息中泄露 API Key
            safe_msg = msg.replace(effective_key, "***") if effective_key in msg else msg
            return f"❌ 连接失败：{safe_msg}"
