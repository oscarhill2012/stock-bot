"""Typed config loaded from `.env`.

Keys are intentionally Optional so a partial setup (e.g. only `yfinance`
working) still imports cleanly. Providers that need a key raise
`ProviderConfigError` at call time, not import time.
"""
from __future__ import annotations

from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderConfigError(RuntimeError):
    """Raised when a provider is invoked without its required API key."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    finnhub_api_key: Optional[str] = None
    quiver_quant_api_key: Optional[str] = None
    edgar_identity: Optional[str] = None  # SEC mandates a contact in User-Agent: "Name email@x"

    quiver_base_url: str = "https://api.quiverquant.com/beta"
    http_timeout_seconds: float = 15.0


_cached: Optional[Settings] = None


def get_settings() -> Settings:
    global _cached
    if _cached is None:
        _cached = Settings()
    return _cached


def require(key: str, value: Optional[str], provider: str) -> str:
    if not value:
        raise ProviderConfigError(
            f"{provider} requires {key} but it is unset. Add it to .env."
        )
    return value
