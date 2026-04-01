import pytest

from edraft.auth import DEFAULT_PUBLIC_CLIENT_ID, AuthConfigurationError, load_auth_settings


def test_load_auth_settings_uses_microsoft365r_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MICROSOFT_TENANT_ID", "tenant-id")
    monkeypatch.delenv("MICROSOFT_CLIENT_ID", raising=False)
    monkeypatch.setenv("EDRAFT_TOKEN_CACHE_PATH", "./data/cache.bin")

    settings = load_auth_settings()

    assert settings.tenant_id == "tenant-id"
    assert settings.client_id == DEFAULT_PUBLIC_CLIENT_ID
    assert str(settings.token_cache_path) == "data/cache.bin"


def test_load_auth_settings_requires_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MICROSOFT_TENANT_ID", raising=False)
    monkeypatch.delenv("MICROSOFT_CLIENT_ID", raising=False)

    with pytest.raises(AuthConfigurationError):
        load_auth_settings()
