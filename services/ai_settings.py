"""AI Provider 配置服务：管理非敏感配置与密钥。

敏感配置（API Key）通过 Keyring 管理，不会明文进入 config.json。
非敏感配置（默认 Provider、模型、Base URL、超时）存入 config.json。
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Optional

from ai.providers import DeepSeekProvider, OpenAIProvider

logger = logging.getLogger(__name__)

_CONFIG_SECTION = "ai_provider"
_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config.json",
)
KEYRING_SERVICE = "AudiobookStudio"
KEYRING_OPENAI_KEY = "openai_api_key"
KEYRING_DEEPSEEK_KEY = "deepseek_api_key"
PROVIDER_DEFAULT_MODELS = {
    "local": ["本地离线基线"],
    "openai": [OpenAIProvider.default_model],
    "deepseek": [DeepSeekProvider.default_model],
}
PROVIDER_DEFAULT_BASE_URLS = {
    "openai": OpenAIProvider.default_base_url,
    "deepseek": DeepSeekProvider.default_base_url,
}


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

    DEFAULT_MODELS = PROVIDER_DEFAULT_MODELS

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
    def get_default_model(provider: str) -> str:
        normalized = str(provider or "local").strip().lower()
        models = PROVIDER_DEFAULT_MODELS.get(normalized, [])
        if not models or normalized == "local":
            return ""
        return models[0]

    @staticmethod
    def get_default_base_url(provider: str) -> str:
        return PROVIDER_DEFAULT_BASE_URLS.get(str(provider or ""), "")

    @staticmethod
    def list_models(
        provider: str,
        api_key: str = "",
        base_url: str = "",
        timeout: float = 30.0,
    ) -> list[str]:
        """Read model IDs from the current form endpoint without persisting it."""
        provider = str(provider or "local").strip().lower()
        if provider == "local":
            return list(PROVIDER_DEFAULT_MODELS["local"])
        if provider not in PROVIDER_DEFAULT_BASE_URLS:
            raise ValueError(f"不支持的 AI Provider：{provider}")

        effective_key = api_key.strip() if api_key and api_key.strip() else (
            AiSettingsService.get_api_key(provider) or ""
        )
        if not effective_key:
            raise ValueError(f"{provider.title()} API Key 尚未配置")

        saved = AiSettingsService.get_provider_config()
        effective_url = (
            base_url.strip()
            if base_url and base_url.strip()
            else saved.get(f"{provider}_base_url")
            or AiSettingsService.get_default_base_url(provider)
        )
        effective_timeout = min(max(float(timeout), 5), 60)
        endpoint = f"{effective_url.rstrip('/')}/models"
        request = urllib.request.Request(
            endpoint,
            headers={
                "Authorization": f"Bearer {effective_key}",
                "Content-Type": "application/json",
            },
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=effective_timeout) as response:
            body = response.read().decode("utf-8")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("模型列表接口返回了无效 JSON") from exc
        data = payload.get("data", []) if isinstance(payload, dict) else []
        model_ids = {
            str(item.get("id")).strip()
            for item in data
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }
        return sorted(model_ids)

    @staticmethod
    def model_source(provider: str, model: str, api_models: Optional[list[str]] = None) -> str:
        selected = str(model or "").strip()
        default = AiSettingsService.get_default_model(provider)
        saved = AiSettingsService.get_provider_config().get(f"{provider}_model", "")
        if not selected or selected == default:
            return "Provider 默认"
        if selected == saved:
            return "已保存配置"
        if api_models and selected in api_models:
            return "API 模型列表"
        return "自定义输入"

    @staticmethod
    def check_connection(
        provider: str,
        api_key: str = "",
        base_url: str = "",
        timeout: float = 30.0,
        model: str = "",
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

        try:
            models = AiSettingsService.list_models(
                provider,
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
            )
            current_model = (
                str(model or "").strip()
                or AiSettingsService.get_default_model(provider)
            )
            lines = [
                f"✅ **{provider.title()}** Provider 连接成功",
                f"✅ 模型列表读取成功（{len(models)} 个）",
            ]
            if current_model and current_model in models:
                lines.append(f"✅ 当前模型 `{current_model}` 在账户可用列表中")
            elif current_model:
                lines.append(
                    f"⚠ Provider 连接成功，但未在模型列表中找到当前模型 "
                    f"`{current_model}`。如果服务允许隐藏模型列表，可继续保存并尝试调用。"
                )
            return "\n\n".join(lines)
        except Exception as exc:
            effective_key = api_key.strip() if api_key and api_key.strip() else (
                AiSettingsService.get_api_key(provider) or ""
            )
            if not effective_key:
                return (
                    f"⚠ **{provider.title()} API Key 尚未配置**。\n\n"
                    "请输入密钥并保存，或设置环境变量。"
                )
            msg = str(exc)[:200]
            # 不要在错误信息中泄露 API Key
            safe_msg = msg.replace(effective_key, "***") if effective_key in msg else msg
            return f"❌ 连接失败：{safe_msg}"
