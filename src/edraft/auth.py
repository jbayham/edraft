from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import msal


# MSAL reserves OIDC scopes such as offline_access and adds them as needed.
# Request only the Graph scopes the app actually needs here.
GRAPH_SCOPES = ["User.Read", "Mail.ReadWrite"]


class AuthConfigurationError(RuntimeError):
    """Raised when Microsoft auth environment variables are missing."""


class GraphAuthenticationError(RuntimeError):
    """Raised when Graph authentication fails."""


@dataclass(slots=True)
class AuthSettings:
    tenant_id: str
    client_id: str
    token_cache_path: Path
    redirect_port: int = 8765

    @property
    def authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}"


def load_auth_settings() -> AuthSettings:
    tenant_id = os.getenv("MICROSOFT_TENANT_ID", "").strip()
    client_id = os.getenv("MICROSOFT_CLIENT_ID", "").strip()
    token_cache_path = Path(
        os.getenv("EDRAFT_TOKEN_CACHE_PATH", "./data/msal_token_cache.bin")
    ).expanduser()
    redirect_port = int(os.getenv("EDRAFT_REDIRECT_PORT", "8765"))
    if not tenant_id or not client_id:
        raise AuthConfigurationError(
            "MICROSOFT_TENANT_ID and MICROSOFT_CLIENT_ID must be set in your environment "
            "or .env file."
        )
    return AuthSettings(
        tenant_id=tenant_id,
        client_id=client_id,
        token_cache_path=token_cache_path,
        redirect_port=redirect_port,
    )


class GraphAuthenticator:
    def __init__(self, settings: AuthSettings):
        self.settings = settings
        self._cache = msal.SerializableTokenCache()
        self._token: str | None = None
        if self.settings.token_cache_path.exists():
            self._cache.deserialize(self.settings.token_cache_path.read_text())
        self._app = msal.PublicClientApplication(
            client_id=self.settings.client_id,
            authority=self.settings.authority,
            token_cache=self._cache,
        )

    def get_access_token(self, interactive: bool = True) -> str:
        if self._token:
            return self._token
        result = None
        accounts = self._app.get_accounts()
        if accounts:
            result = self._app.acquire_token_silent(GRAPH_SCOPES, account=accounts[0])
        if not result and interactive:
            try:
                result = self._app.acquire_token_interactive(
                    scopes=GRAPH_SCOPES,
                    port=self.settings.redirect_port,
                    prompt="select_account",
                )
            except Exception:
                flow = self._app.initiate_device_flow(scopes=GRAPH_SCOPES)
                if "user_code" not in flow:
                    raise
                result = self._app.acquire_token_by_device_flow(flow)
        if not result or "access_token" not in result:
            description = ""
            if result:
                description = result.get("error_description") or result.get("error") or ""
            raise GraphAuthenticationError(
                f"Unable to acquire Microsoft Graph token. {description}".strip()
            )
        self._token = result["access_token"]
        self._persist_cache()
        return self._token

    def clear_cached_token(self) -> None:
        self._token = None

    def _persist_cache(self) -> None:
        if not self._cache.has_state_changed:
            return
        self.settings.token_cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings.token_cache_path.write_text(self._cache.serialize())
