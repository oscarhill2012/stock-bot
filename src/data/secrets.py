"""Secret reader. Loads `.env` once, then `require_key()` reads via `os.getenv`.

Providers call `require_key("FINNHUB_API_KEY")` at fetch time (not at import
time) so a partially-configured `.env` can still import the data package.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv


class SecretMissingError(RuntimeError):
    """Raised when a provider asks for an env var that is unset."""


_loaded = False


def _ensure_loaded() -> None:
    global _loaded
    if not _loaded:
        load_dotenv()
        _loaded = True


def require_key(env_var: str) -> str:
    """Return the env var or raise `SecretMissingError`."""
    _ensure_loaded()
    val = os.getenv(env_var)
    if not val:
        raise SecretMissingError(
            f"{env_var} is unset. Add it to .env."
        )
    return val
