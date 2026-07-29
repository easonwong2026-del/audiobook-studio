"""Keyring failure messages remain actionable and secret-free."""
from __future__ import annotations

import sys
import types

import pytest


def test_keyring_backend_failure_does_not_expose_raw_exception(monkeypatch):
    from services import ai_settings

    fake_keyring = types.SimpleNamespace(
        set_password=lambda *args: (_ for _ in ()).throw(RuntimeError("backend internals")),
        errors=types.SimpleNamespace(PasswordDeleteError=RuntimeError),
    )
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)

    with pytest.raises(ai_settings.SecretStoreUnavailableError) as exc:
        ai_settings._set_secret("AudiobookStudio", "deepseek_api_key", "sk-secret")
    assert "backend internals" not in str(exc.value)
    assert "系统密钥环不可用" in str(exc.value)
    assert "sk-secret" not in str(exc.value)
