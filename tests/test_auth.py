import pytest

from edraft.auth import DEFAULT_PUBLIC_CLIENT_ID, AuthConfigurationError, load_auth_settings


def test_load_auth_settings_uses_microsoft365r_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MICROSOFT_TENANT_ID", "tenant-id")
    monkeypatch.delenv("MICROSOFT_CLIENT_ID", raising=False)
    monkeypatch.delenv("EDRAFT_AUTH_MODE", raising=False)
    monkeypatch.setenv("EDRAFT_TOKEN_CACHE_PATH", "./data/cache.bin")

    settings = load_auth_settings()

    assert settings.tenant_id == "tenant-id"
    assert settings.client_id == DEFAULT_PUBLIC_CLIENT_ID
    assert settings.fallback_client_id == DEFAULT_PUBLIC_CLIENT_ID
    assert settings.auth_mode == "auto"
    assert str(settings.token_cache_path) == "data/cache.bin"


def test_load_auth_settings_accepts_explicit_fallback_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MICROSOFT_TENANT_ID", "tenant-id")
    monkeypatch.setenv("EDRAFT_AUTH_MODE", "fallback")

    settings = load_auth_settings()

    assert settings.auth_mode == "fallback"


def test_load_auth_settings_requires_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MICROSOFT_TENANT_ID", raising=False)
    monkeypatch.delenv("MICROSOFT_CLIENT_ID", raising=False)
    monkeypatch.delenv("EDRAFT_AUTH_MODE", raising=False)

    with pytest.raises(AuthConfigurationError):
        load_auth_settings()
