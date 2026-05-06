"""Shared `tenacity` retry decorator for sync provider inner-functions.

Per `docs/data-sources.md`: providers must not crash an agent run on a
transient third-party hiccup. We retry up to 4 times with jittered
exponential back-off. Rate-limit (429) errors should not happen if the
limiters are sized correctly — retries here are for connection blips.
"""
from __future__ import annotations

import logging
from typing import Callable, TypeVar

from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

_RETRYABLE: tuple[type[BaseException], ...]
try:
    import httpx
    import requests

    _RETRYABLE = (
        requests.exceptions.RequestException,
        httpx.HTTPError,
        ConnectionError,
        TimeoutError,
    )
except ImportError:  # tests / minimal envs
    _RETRYABLE = (ConnectionError, TimeoutError)


def with_retry(fn: Callable[..., T]) -> Callable[..., T]:
    """Wrap a provider call with retry + jittered exponential back-off."""
    return retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(4),
        wait=wait_exponential_jitter(initial=0.5, max=8.0),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )(fn)
