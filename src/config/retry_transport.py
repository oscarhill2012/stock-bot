"""Loader for ``config/retry_transport.json`` — the network-transport retry
policy applied to every LLM agent call in the pipeline.

Why this exists
---------------
Reaching Vertex AI's Gemini models means a chain of network hops: a token
refresh against ``oauth2.googleapis.com`` followed by the model call itself.
Any one of those can fail transiently when the *local* machine briefly loses
connectivity — a Wi-Fi reconnect, a VPN drop, or a momentary DNS-resolver
hiccup surfaces as ``socket.gaierror`` ("Temporary failure in name
resolution"), a builtin ``ConnectionError``, or an HTTP-client transport
error.  These are not server-side capacity problems (those are HTTP 429s,
handled separately by :mod:`src.config.retry_429`) — they are blips that
clear on their own within a few seconds.

Without a retry, a single blip mid-run aborts the whole tick: the backtest
driver treats a failed pipeline as fatal (see ``backtest.driver``).  The
retry layer lives in :mod:`src.agents.llm_retry` and reads its policy from
this file so the knobs stay in JSON, per the project's "no hardcoded config
in source" convention — see ``config/README.md``.

Per-agent timeout and schema-validation retry counts live in
``config/analysts.json`` and ``config/strategist.json`` respectively.  Both
the 429 and the transport back-off policies are project-wide and stored in
their own JSON files.

Hot-reload semantics
--------------------
The ``@lru_cache(maxsize=1)`` decorator on :func:`get_retry_transport_policy`
means the JSON file is read exactly once per process.  Editing the file at
runtime has no effect — a process restart is required.  This mirrors
:mod:`src.config.retry_429`.  ``load_retry_transport_policy(path=...)`` is the
test hook for feeding a custom JSON path without touching the source tree.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

# Project-root-relative default path.  The package is imported via
# PYTHONPATH=src so we resolve relative to the working directory rather
# than relative to this file — matches ``src/config/retry_429.py``.
_DEFAULT_PATH = Path("config/retry_transport.json")


class RetryTransportPolicy(BaseModel):
    """Top-level shape of ``config/retry_transport.json``.

    All three fields are bounds on the same exponential-with-jitter
    backoff schedule applied by :class:`agents.llm_retry.RetryingAgentWrapper`
    when a network transport / DNS error is raised on the path to Vertex AI.

    Attributes
    ----------
    max_attempts:
        Total number of attempts (not retries after the first failure).
        A value of ``1`` disables retries entirely.  Must be ``>= 1``.
    base_delay_seconds:
        Initial wait before the first retry, in seconds.  Subsequent
        retries multiply this by an exponential factor with random
        jitter, capped at ``max_delay_seconds``.  Must be ``> 0``.
    max_delay_seconds:
        Upper bound on any single inter-retry wait, in seconds.  Must be
        ``>= base_delay_seconds``.  The backoff saturates at this value
        once the exponential growth exceeds it.
    """

    max_attempts:       int   = Field(default=4, ge=1)
    base_delay_seconds: float = Field(default=2.0, gt=0.0)
    max_delay_seconds:  float = Field(default=30.0, gt=0.0)


def load_retry_transport_policy(*, path: Path | None = None) -> RetryTransportPolicy:
    """Read and validate ``config/retry_transport.json``.

    Parameters
    ----------
    path:
        Override the default path.  Useful for tests that want to feed a
        temporary JSON file without touching the source tree.

    Returns
    -------
    RetryTransportPolicy
        Validated configuration object.

    Raises
    ------
    FileNotFoundError
        If the JSON file does not exist at the resolved path.
    json.JSONDecodeError
        If the file content is not valid JSON.
    pydantic.ValidationError
        If the parsed payload fails schema validation (e.g. a negative
        delay or ``max_attempts < 1``).
    """

    p = path or _DEFAULT_PATH

    # Strip a leading ``_comment`` field if present — JSON has no native
    # comment syntax and operators sometimes want a header note inside
    # the file itself.  Same convention as ``config/retry_429.json``.
    payload = json.loads(p.read_text(encoding="utf-8"))
    payload.pop("_comment", None)

    cfg = RetryTransportPolicy.model_validate(payload)

    # Cross-field invariant — Pydantic ``Field`` constraints can only
    # validate single fields.  An explicit check here surfaces a
    # mis-configured pair (e.g. base_delay=10, max_delay=5) at load time
    # rather than at first retry attempt.
    if cfg.max_delay_seconds < cfg.base_delay_seconds:
        raise ValueError(
            f"max_delay_seconds ({cfg.max_delay_seconds}) must be >= "
            f"base_delay_seconds ({cfg.base_delay_seconds})"
        )

    return cfg


@lru_cache(maxsize=1)
def get_retry_transport_policy() -> RetryTransportPolicy:
    """Production entry point — cached load of the default config path.

    The result is memoised via ``lru_cache`` so the JSON file is read
    exactly once per process.  A process restart is required after
    editing ``config/retry_transport.json`` — there is no hot-reload path.
    Mirrors the semantics of :func:`src.config.retry_429.get_retry_429_policy`.

    Returns
    -------
    RetryTransportPolicy
        Validated configuration singleton.
    """

    return load_retry_transport_policy()


def _reset_cache() -> None:
    """Clear the ``lru_cache``.  Test hook only — never call from production.

    Use this in test fixtures that mutate ``config/retry_transport.json``
    (e.g. via ``monkeypatch``) so subsequent reads pick up the new
    content.  Mirrors the equivalent helper in
    ``src/config/retry_429.py``.
    """

    get_retry_transport_policy.cache_clear()
