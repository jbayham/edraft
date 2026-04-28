from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import msal


# MSAL reserves OIDC scopes such as offline_access and adds them as needed.
# edraft can run in two modes:
# - primary: use the approved edraft app with calendar access.
# - fallback: use the Microsoft365R public client with mail-only access.
PRIMARY_SCOPES = ["User.Read", "Mail.ReadWrite", "Calendars.Read"]
FALLBACK_SCOPES = ["User.Read", "Mail.ReadWrite"]
# Microsoft365R's published default public-client app registration.
# edraft still requests only FALLBACK_SCOPES in fallback mode, so Mail.Send is not requested.
DEFAULT_PUBLIC_CLIENT_ID = "d44a05d5-c6a5-4bbb-82d2-443123722380"


class AuthConfigurationError(RuntimeError):
    """Raised when Microsoft auth environment variables are missing."""


class GraphAuthenticationError(RuntimeError):
    """Raised when Graph authentication fails."""


@dataclass(slots=True)
class AuthSettings:
    tenant_id: str
    client_id: str
    fallback_client_id: str
    auth_mode: str
    token_cache_path: Path
    redirect_port: int = 8765

    @property
    def authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}"


def load_auth_settings() -> AuthSettings:
    tenant_id = os.getenv("MICROSOFT_TENANT_ID", "").strip()
    client_id = os.getenv("MICROSOFT_CLIENT_ID", "").strip() or DEFAULT_PUBLIC_CLIENT_ID
    fallback_client_id = os.getenv("EDRAFT_FALLBACK_CLIENT_ID", "").strip() or DEFAULT_PUBLIC_CLIENT_ID
    auth_mode = os.getenv("EDRAFT_AUTH_MODE", "auto").strip().lower()
    token_cache_path = Path(
        os.getenv("EDRAFT_TOKEN_CACHE_PATH", "./data/msal_token_cache.bin")
    ).expanduser()
    redirect_port = int(os.getenv("EDRAFT_REDIRECT_PORT", "8765"))
    if not tenant_id:
        raise AuthConfigurationError(
            "MICROSOFT_TENANT_ID must be set in your environment or .env file."
        )
    if auth_mode not in {"auto", "primary", "fallback"}:
        raise AuthConfigurationError("EDRAFT_AUTH_MODE must be auto, primary, or fallback")
    return AuthSettings(
        tenant_id=tenant_id,
        client_id=client_id,
        fallback_client_id=fallback_client_id,
        auth_mode=auth_mode,
        token_cache_path=token_cache_path,
        redirect_port=redirect_port,
    )


class GraphAuthenticator:
    def __init__(self, settings: AuthSettings):
        self.settings = settings
        self._cache = msal.SerializableTokenCache()
        self._token: str | None = None
        self._selected_profile: str | None = None
        if self.settings.token_cache_path.exists():
            self._cache.deserialize(self.settings.token_cache_path.read_text())

    def get_access_token(self, interactive: bool = True) -> str:
        if self._token:
            return self._token
        if self.settings.auth_mode == "primary":
            result = self._acquire_token(self.settings.client_id, PRIMARY_SCOPES, interactive=interactive)
            self._selected_profile = "primary"
        elif self.settings.auth_mode == "fallback":
            result = self._acquire_token(
                self.settings.fallback_client_id,
                FALLBACK_SCOPES,
                interactive=interactive,
            )
            self._selected_profile = "fallback"
        else:
            result = self._acquire_safest_token(interactive=interactive)
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
        self._selected_profile = None

    @property
    def selected_profile(self) -> str | None:
        return self._selected_profile

    @property
    def supports_calendar(self) -> bool:
        return self._selected_profile == "primary" or (
            self.settings.auth_mode == "primary" and self._selected_profile is None
        )

    def _acquire_safest_token(self, *, interactive: bool) -> dict[str, str] | None:
        primary_result = self._acquire_token_silently(self.settings.client_id, PRIMARY_SCOPES)
        if primary_result and "access_token" in primary_result:
            self._selected_profile = "primary"
            return primary_result

        fallback_result = self._acquire_token_silently(self.settings.fallback_client_id, FALLBACK_SCOPES)
        if fallback_result and "access_token" in fallback_result:
            self._selected_profile = "fallback"
            return fallback_result

        if not interactive:
            return primary_result or fallback_result

        try:
            fallback_result = self._acquire_token(
                self.settings.fallback_client_id,
                FALLBACK_SCOPES,
                interactive=True,
            )
            self._selected_profile = "fallback"
            return fallback_result
        except Exception as fallback_exc:
            primary_error = None
            try:
                primary_result = self._acquire_token(
                    self.settings.client_id,
                    PRIMARY_SCOPES,
                    interactive=True,
                )
                self._selected_profile = "primary"
                return primary_result
            except Exception as primary_exc:
                primary_error = primary_exc
                raise fallback_exc from primary_error

    def _acquire_token_silently(
        self,
        client_id: str,
        scopes: list[str],
    ) -> dict[str, str] | None:
        app = self._build_app(client_id)
        accounts = app.get_accounts()
        if not accounts:
            return None
        return app.acquire_token_silent(scopes, account=accounts[0])

    def _acquire_token(
        self,
        client_id: str,
        scopes: list[str],
        *,
        interactive: bool,
    ) -> dict[str, str] | None:
        app = self._build_app(client_id)
        if not interactive:
            return self._acquire_token_silently(client_id, scopes)
        try:
            return app.acquire_token_interactive(
                scopes=scopes,
                port=self.settings.redirect_port,
                prompt="select_account",
            )
        except Exception:
            flow = app.initiate_device_flow(scopes=scopes)
            if "user_code" not in flow:
                raise
            return app.acquire_token_by_device_flow(flow)

    def _build_app(self, client_id: str) -> msal.PublicClientApplication:
        return msal.PublicClientApplication(
            client_id=client_id,
            authority=self.settings.authority,
            token_cache=self._cache,
        )

    def _persist_cache(self) -> None:
        if not self._cache.has_state_changed:
            return
        self.settings.token_cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings.token_cache_path.write_text(self._cache.serialize())
