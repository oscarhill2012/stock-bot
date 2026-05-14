"""Unit tests for ``agents.analysts.cache_callbacks.make_report_cache_callbacks``.

Covers the five scenarios mandated by B22:

1. ``test_after_reads_from_llm_response_not_state`` — regression pin for the
   lifecycle bug.  ``_after`` must write to disk by reading ``llm_response``,
   not ``state[verdicts_state_key]``, because ADK's
   ``__maybe_save_output_to_state`` runs *after* the after-model-callback
   chain.  If anyone reverts to reading state, this test fails.

2. ``test_after_handles_missing_content_gracefully`` — ``_after`` must return
   ``None`` (not raise) when ``llm_response`` has no usable content.

3. ``test_before_full_hit_short_circuits`` — when the cache is pre-populated
   for every ticker, ``_before`` must return a non-None ``Content`` and write
   verdicts into state.

4. ``test_before_any_miss_returns_none`` — a partial cache (one ticker hit,
   one miss) must cause ``_before`` to return ``None`` (full LLM call).

5. ``test_after_skips_write_when_cache_disabled`` — with ``cache.enabled=False``
   both hooks must be no-ops.
"""
from __future__ import annotations

import json
import types

import pytest

from agents.analysts.cache_callbacks import make_report_cache_callbacks
from agents.analysts.report_cache import write_cache

# ---------------------------------------------------------------------------
# Helpers and fixtures
# ---------------------------------------------------------------------------

# A fixed hash so test assertions don't depend on real hash logic.  Actual
# hash correctness is covered by test_report_cache_hash.py separately.
_FIXED_HASH  = "blake2b:aabbccdd00112233"
_PROMPT_VER  = "test-v1"
_ANALYST     = "stub"
_DATA_KEY    = "stub_data"
_VERDICTS_KEY = "stub_verdicts"


def _hash_fn(d: dict) -> str:
    """Trivial hash function — always returns a fixed digest for test isolation."""
    return _FIXED_HASH


def _fake_llm_response(verdicts: list[dict]) -> types.SimpleNamespace:
    """Build a minimal fake LLM response object.

    The ``_after`` hook reads ``llm_response.content.parts[0].text``.  This
    helper constructs the minimal stub without importing google.genai.

    Parameters
    ----------
    verdicts:
        List of verdict dicts to embed in the JSON payload.

    Returns
    -------
    types.SimpleNamespace
        Stub whose ``.content.parts[0].text`` is the serialised payload.
    """
    text    = json.dumps({"verdicts": verdicts})
    part    = types.SimpleNamespace(text=text)
    content = types.SimpleNamespace(parts=[part])
    return types.SimpleNamespace(content=content)


class _Ctx:
    """Minimal callback-context stub that exposes a mutable ``state`` dict."""

    def __init__(self, state: dict):
        """Initialise with an arbitrary state dict."""
        self.state = state


@pytest.fixture()
def stub_config(tmp_path, monkeypatch):
    """Redirect ``get_analysts_config`` to use a tmp_path cache directory.

    Monkeypatches ``agents.analysts.cache_callbacks.get_analysts_config`` so
    the factory reads from the temp dir rather than the real project cache.

    Parameters
    ----------
    tmp_path:
        pytest-provided temporary directory.
    monkeypatch:
        pytest monkeypatch fixture.

    Yields
    ------
    Path
        The cache root directory (``tmp_path / "cache"``).
    """
    cache_root = tmp_path / "cache"

    # Build a minimal config stub whose .cache attribute matches the shape
    # the factory expects.
    cache_cfg = types.SimpleNamespace(enabled=True, directory=str(cache_root))
    config    = types.SimpleNamespace(cache=cache_cfg)

    monkeypatch.setattr(
        "agents.analysts.cache_callbacks.get_analysts_config",
        lambda: config,
    )

    yield cache_root


@pytest.fixture()
def disabled_config(tmp_path, monkeypatch):
    """Same as ``stub_config`` but with ``cache.enabled=False``."""
    cache_root = tmp_path / "cache"
    cache_cfg  = types.SimpleNamespace(enabled=False, directory=str(cache_root))
    config     = types.SimpleNamespace(cache=cache_cfg)

    monkeypatch.setattr(
        "agents.analysts.cache_callbacks.get_analysts_config",
        lambda: config,
    )

    yield cache_root


# ---------------------------------------------------------------------------
# Shared factory helper
# ---------------------------------------------------------------------------

def _make_callbacks():
    """Build the shared-factory callbacks with stub parameters."""
    return make_report_cache_callbacks(
        analyst_name       = _ANALYST,
        prompt_version     = _PROMPT_VER,
        data_state_key     = _DATA_KEY,
        verdicts_state_key = _VERDICTS_KEY,
        hash_inputs        = _hash_fn,
        trace_label        = None,
    )


# ---------------------------------------------------------------------------
# Test 1 — regression pin: _after reads llm_response, NOT state
# ---------------------------------------------------------------------------

def test_after_reads_from_llm_response_not_state(stub_config):
    """_after must write the cache file by parsing llm_response, not state.

    This is the regression pin for the B22 lifecycle bug.  We present ``_after``
    with:
    - ``state[verdicts_state_key]`` empty  (the ADK condition that was failing)
    - ``llm_response`` containing the verdict JSON (as ADK receives it)

    Assert that the cache file was written to disk.  If anyone reverts
    ``_after`` to read state instead, this test fails because state is empty.
    """
    _, after = _make_callbacks()

    verdict_dict = {
        "ticker":       "AAPL",
        "lean":         "bullish",
        "magnitude":    0.5,
        "confidence":   0.8,
        "rationale":    "test",
        "key_factors":  [],
        "is_no_data":   False,
    }

    ctx = _Ctx({
        # Populate the data key so the hash function has something to work with.
        _DATA_KEY:     {"AAPL": {}},
        # Verdicts state key is intentionally EMPTY — this is the ADK condition
        # that caused the original lifecycle bug.
        _VERDICTS_KEY: {},
    })

    fake_response = _fake_llm_response([verdict_dict])
    result = after(ctx, llm_response=fake_response)

    # The hook must return None (no short-circuit).
    assert result is None

    # The cache file must have been written to disk — if _after reads state
    # instead of llm_response, the loop sees zero verdicts and writes nothing.
    cache_file = stub_config / _ANALYST / "AAPL.json"
    assert cache_file.exists(), (
        f"Cache file {cache_file} was not created by _after. "
        "This means _after is not reading llm_response directly — "
        "the B22 lifecycle bug has been re-introduced."
    )

    record = json.loads(cache_file.read_text())
    assert record["verdict"]["ticker"] == "AAPL"
    assert record["prompt_version"]    == _PROMPT_VER


# ---------------------------------------------------------------------------
# Test 2 — graceful degradation on bad llm_response
# ---------------------------------------------------------------------------

def test_after_handles_missing_content_gracefully(stub_config):
    """_after must return None without raising on malformed llm_response.

    Exercises four bad-response shapes:
    - ``None`` (ADK passes None in some edge paths)
    - Object with no ``.content`` attribute
    - Object with ``content.parts == []``
    - Object with ``content.parts[0].text`` that is invalid JSON
    """
    _, after = _make_callbacks()

    ctx = _Ctx({_DATA_KEY: {}, _VERDICTS_KEY: {}})

    bad_responses = [
        # Shape 1: None
        None,
        # Shape 2: no content attribute
        types.SimpleNamespace(),
        # Shape 3: empty parts list
        types.SimpleNamespace(
            content=types.SimpleNamespace(parts=[])
        ),
        # Shape 4: invalid JSON text
        types.SimpleNamespace(
            content=types.SimpleNamespace(
                parts=[types.SimpleNamespace(text="not-json-{")]
            )
        ),
    ]

    for bad in bad_responses:
        result = after(ctx, llm_response=bad)
        assert result is None, (
            f"_after raised or returned non-None for bad response {bad!r}"
        )


# ---------------------------------------------------------------------------
# Test 3 — before returns Content on full cache hit
# ---------------------------------------------------------------------------

def test_before_full_hit_short_circuits(stub_config):
    """_before must return a Content object and populate state on a full cache hit.

    Pre-populates the cache for both tickers in the state, then calls _before
    and asserts:
    - Return value is not None (short-circuit triggered).
    - ``state[verdicts_state_key]`` was populated with the cached verdicts.
    """
    before, _ = _make_callbacks()

    # Pre-populate the cache for AAPL and MSFT.
    for ticker in ("AAPL", "MSFT"):
        write_cache(
            stub_config, _ANALYST, ticker,
            input_hash=_FIXED_HASH,
            prompt_version=_PROMPT_VER,
            verdict={
                "ticker":      ticker,
                "lean":        "neutral",
                "magnitude":   0.3,
                "confidence":  0.6,
                "rationale":   "cached",
                "key_factors": [],
                "is_no_data":  False,
            },
            report=None,
        )

    ctx = _Ctx({
        "tickers":  ["AAPL", "MSFT"],
        _DATA_KEY:  {"AAPL": {}, "MSFT": {}},
    })

    result = before(ctx, llm_request=None)

    # Must return a non-None value to short-circuit ADK's model call.
    assert result is not None, (
        "_before returned None despite full cache hit for both tickers."
    )

    # Regression pin — ADK's downstream post-processors (``_nl_planning`` and
    # friends) access ``llm_response.content`` on whatever the
    # before_model_callback returns.  Returning a bare ``genai_types.Content``
    # crashes with ``AttributeError: 'Content' object has no attribute
    # 'content'`` the moment a real cache hit occurs.  The hook MUST therefore
    # return an ``LlmResponse`` that wraps the Content.  This assertion fails
    # loudly if anyone "simplifies" back to returning the raw Content.
    from google.adk.models import LlmResponse

    assert isinstance(result, LlmResponse), (
        f"_before returned {type(result).__name__}, expected LlmResponse — "
        "ADK's _nl_planning post-processor reads .content on this object."
    )
    assert result.content is not None, (
        "_before returned LlmResponse without populated .content — "
        "downstream post-processors will crash on the missing payload."
    )

    # State must have been populated with the cached verdicts batch.
    assert _VERDICTS_KEY in ctx.state, (
        "_before did not write to state[verdicts_state_key] on cache hit."
    )
    tickers_returned = {
        v["ticker"]
        for v in ctx.state[_VERDICTS_KEY].get("verdicts", [])
    }
    assert tickers_returned == {"AAPL", "MSFT"}, (
        f"Cached verdicts tickers {tickers_returned} do not match expected {{AAPL, MSFT}}."
    )


def test_before_full_hit_content_is_valid_verdict_batch_json(stub_config):
    """Regression pin for the ``"(cached)"`` placeholder bug.

    ADK's ``__maybe_save_output_to_state`` (in
    ``google.adk.agents.llm_agent``) runs after a before-model-callback that
    returns a non-None ``LlmResponse`` and validates that response's text
    payload against the agent's declared ``output_schema``.  Our LLM
    analysts declare ``output_schema=VerdictBatch``, so the response text
    MUST be valid JSON that parses cleanly as a ``VerdictBatch``.

    Earlier code returned the literal string ``"(cached)"`` as a placeholder
    — fine in unit tests that only inspect the wrapper type, but the moment
    a real cache hit fired in a live ADK run it raised
    ``pydantic.ValidationError: Invalid JSON: expected value at line 1
    column 1, input_value='(cached)'`` and tanked the tick.  This test
    fails loudly if anyone reintroduces a placeholder string.
    """
    before, _ = _make_callbacks()

    # Pre-populate the cache so the full-hit short-circuit fires.
    for ticker in ("AAPL", "MSFT"):
        write_cache(
            stub_config, _ANALYST, ticker,
            input_hash=_FIXED_HASH,
            prompt_version=_PROMPT_VER,
            verdict={
                "ticker":      ticker,
                "lean":        "neutral",
                "magnitude":   0.3,
                "confidence":  0.6,
                "rationale":   "cached",
                "key_factors": [],
                "is_no_data":  False,
            },
            report=None,
        )

    ctx = _Ctx({
        "tickers":  ["AAPL", "MSFT"],
        _DATA_KEY:  {"AAPL": {}, "MSFT": {}},
    })

    result = before(ctx, llm_request=None)

    # Pull the synthetic response text out of the wrapper.
    text = result.content.parts[0].text

    # It must parse as JSON — a placeholder string like "(cached)" would not.
    payload = json.loads(text)

    # And the parsed shape must round-trip through ``VerdictBatch`` cleanly,
    # because that is exactly what ADK's downstream validator will attempt.
    from contract.evidence import VerdictBatch

    batch = VerdictBatch.model_validate(payload)
    tickers_in_payload = {v.ticker for v in batch.verdicts}

    assert tickers_in_payload == {"AAPL", "MSFT"}, (
        f"VerdictBatch in synthetic response carries tickers {tickers_in_payload}, "
        "expected {AAPL, MSFT}."
    )


# ---------------------------------------------------------------------------
# Test 4 — before returns None on any partial miss
# ---------------------------------------------------------------------------

def test_before_any_miss_returns_none(stub_config):
    """_before must return None if even one ticker is not in the cache.

    Pre-populates the cache for AAPL only.  MSFT is a miss.  The hook must
    force a full LLM call (return None) — no partial loads permitted.
    """
    before, _ = _make_callbacks()

    # Cache only AAPL.
    write_cache(
        stub_config, _ANALYST, "AAPL",
        input_hash=_FIXED_HASH,
        prompt_version=_PROMPT_VER,
        verdict={
            "ticker":      "AAPL",
            "lean":        "bearish",
            "magnitude":   0.4,
            "confidence":  0.7,
            "rationale":   "cached",
            "key_factors": [],
            "is_no_data":  False,
        },
        report=None,
    )

    ctx = _Ctx({
        "tickers":  ["AAPL", "MSFT"],
        _DATA_KEY:  {"AAPL": {}, "MSFT": {}},
    })

    result = before(ctx, llm_request=None)

    assert result is None, (
        "_before returned non-None despite MSFT being a cache miss. "
        "Partial loads are not permitted — the full LLM call must proceed."
    )


# ---------------------------------------------------------------------------
# Test 5 — both hooks are no-ops when cache is disabled
# ---------------------------------------------------------------------------

def test_after_skips_write_when_cache_disabled(disabled_config):
    """With ``cache.enabled=False``, both hooks must return None immediately.

    No cache files should be created even when a valid llm_response is provided.
    """
    before, after = _make_callbacks()

    ctx = _Ctx({
        "tickers":  ["AAPL"],
        _DATA_KEY:  {"AAPL": {}},
        _VERDICTS_KEY: {},
    })

    # _before must return None without consulting the (empty) cache.
    before_result = before(ctx, llm_request=None)
    assert before_result is None

    # _after must return None without writing any file.
    verdict_dict = {
        "ticker":      "AAPL",
        "lean":        "neutral",
        "magnitude":   0.1,
        "confidence":  0.5,
        "rationale":   "test",
        "key_factors": [],
        "is_no_data":  False,
    }
    after_result = after(ctx, llm_response=_fake_llm_response([verdict_dict]))
    assert after_result is None

    # No cache file should have been written.
    cache_file = disabled_config / _ANALYST / "AAPL.json"
    assert not cache_file.exists(), (
        "Cache file was created despite cache.enabled=False. "
        "The disabled guard in _after is broken."
    )
