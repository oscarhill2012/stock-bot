"""Unit tests for ``observability.terminal_log``.

Covers:
- ``format_tokens`` — compact k-suffix formatting.
- ``format_latency`` — fixed-width seconds formatting.
- ``make_observability_callbacks`` factory — correct log emission and state
  mutation with a synthetic ``LlmResponse``.
- Cache + observability callback composition — verifies that ``_chain_before``
  short-circuits correctly and ``_chain_after`` runs all hooks unconditionally,
  and that the ``stockbot.tick`` logger receives the expected row.
"""
from __future__ import annotations

import logging
import time
import types

import pytest

from observability.terminal_log import (
    format_latency,
    format_tokens,
    make_observability_callbacks,
)


# ---------------------------------------------------------------------------
# format_tokens
# ---------------------------------------------------------------------------

class TestFormatTokens:
    """Tests for the ``format_tokens`` helper."""

    def test_zero_returns_right_justified_zero(self):
        """Zero token count should produce a right-justified zero string."""
        result = format_tokens(0)
        assert result == f"{'0':>6}"
        assert len(result) == 6

    def test_none_treated_as_zero(self):
        """``None`` should be treated the same as zero."""
        assert format_tokens(None) == f"{'0':>6}"

    def test_sub_thousand_no_suffix(self):
        """Values below 1000 are formatted without a k suffix."""
        result = format_tokens(500)
        assert "k" not in result
        assert result.strip() == "500"
        assert len(result) == 6

    def test_exact_thousand_uses_integer_k(self):
        """Exact multiples of 1000 should not show a decimal point."""
        result = format_tokens(8000)
        assert result.strip() == "8k"
        assert len(result) == 6

    def test_8500_shows_one_decimal(self):
        """8500 tokens should render as ``8.5k``."""
        result = format_tokens(8500)
        assert result.strip() == "8.5k"
        assert len(result) == 6

    def test_168000_shows_integer_k(self):
        """168000 tokens should render as ``168k`` (no unnecessary decimal)."""
        result = format_tokens(168_000)
        assert result.strip() == "168k"
        assert len(result) == 6

    def test_output_always_six_chars(self):
        """All outputs must be exactly 6 characters wide for column alignment."""
        for n in (0, 1, 999, 1000, 1500, 10_000, 168_000):
            assert len(format_tokens(n)) == 6, f"failed for n={n}"


# ---------------------------------------------------------------------------
# format_latency
# ---------------------------------------------------------------------------

class TestFormatLatency:
    """Tests for the ``format_latency`` helper."""

    def test_none_returns_spaces(self):
        """``None`` duration should return a space-only string of width 6."""
        result = format_latency(None)
        assert result.strip() == ""
        assert len(result) == 6

    def test_small_latency_padded(self):
        """A value like 4.12 s should be right-padded to 6 characters."""
        result = format_latency(4.12)
        assert result.strip() == "4.12s"
        assert len(result) == 6

    def test_double_digit_latency(self):
        """12.35 s should also be 6 characters, with no leading space."""
        result = format_latency(12.345)
        assert "12.35s" in result
        assert len(result) == 6

    def test_output_always_six_chars(self):
        """All outputs must be exactly 6 characters wide."""
        for s in (0.5, 1.0, 9.99, 10.0, 99.99):
            assert len(format_latency(s)) == 6, f"failed for s={s}"


# ---------------------------------------------------------------------------
# make_observability_callbacks — basic contract
# ---------------------------------------------------------------------------

def _make_fake_context(state: dict | None = None):
    """Build a minimal fake callback_context with a mutable state dict.

    Parameters
    ----------
    state:
        Optional initial state dict.  Defaults to empty dict.

    Returns
    -------
    types.SimpleNamespace
        Object exposing a ``.state`` attribute that behaves like a plain dict.
    """
    ctx = types.SimpleNamespace()
    ctx.state = dict(state or {})
    return ctx


def _make_fake_llm_response(
    prompt_tokens: int | None = 8500,
    candidate_tokens: int | None = 1100,
):
    """Build a fake ``LlmResponse``-like object with usage_metadata.

    Parameters
    ----------
    prompt_tokens:
        Value for ``usage_metadata.prompt_token_count``.
    candidate_tokens:
        Value for ``usage_metadata.candidates_token_count``.

    Returns
    -------
    types.SimpleNamespace
        Mimics the ADK ``LlmResponse`` shape used by the after-callback.
    """
    meta = types.SimpleNamespace(
        prompt_token_count=prompt_tokens,
        candidates_token_count=candidate_tokens,
    )
    resp = types.SimpleNamespace(usage_metadata=meta)
    return resp


class TestMakeObservabilityCallbacks:
    """Tests for the ``make_observability_callbacks`` factory."""

    def test_returns_two_callables(self):
        """Factory must return exactly two callables."""
        before_cb, after_cb = make_observability_callbacks(
            analyst="news",
            ticker="AAPL",
            ticker_index=1,
            ticker_count=5,
            model_name="gemini-test",
        )
        assert callable(before_cb)
        assert callable(after_cb)

    def test_before_cb_stamps_state_key(self):
        """``before_cb`` must write the start timestamp to session state."""
        before_cb, _ = make_observability_callbacks(
            analyst="news",
            ticker="AAPL",
            ticker_index=1,
            ticker_count=5,
            model_name="gemini-test",
        )
        ctx = _make_fake_context()
        result = before_cb(callback_context=ctx, llm_request=None)

        # The before-callback must not short-circuit (must return None).
        assert result is None

        # The start timestamp must be stamped in state.
        assert "temp:_llm_start_news_AAPL" in ctx.state
        assert isinstance(ctx.state["temp:_llm_start_news_AAPL"], float)

    def test_after_cb_emits_log_line(self, caplog):
        """``after_cb`` must emit exactly one INFO record on ``stockbot.tick``."""
        before_cb, after_cb = make_observability_callbacks(
            analyst="news",
            ticker="AAPL",
            ticker_index=2,
            ticker_count=20,
            model_name="gemini-test",
        )
        ctx = _make_fake_context()

        # Stamp start time so elapsed is computable.
        before_cb(callback_context=ctx, llm_request=None)

        resp = _make_fake_llm_response(prompt_tokens=8500, candidate_tokens=1100)

        with caplog.at_level(logging.INFO, logger="stockbot.tick"):
            result = after_cb(callback_context=ctx, llm_response=resp)

        # after-callback must not short-circuit.
        assert result is None

        # Exactly one log record on the tick logger.
        tick_records = [r for r in caplog.records if r.name == "stockbot.tick"]
        assert len(tick_records) == 1

        msg = tick_records[0].getMessage()
        # Progress counter.
        assert "2/20" in msg
        # Ticker symbol.
        assert "AAPL" in msg
        # Status symbol.
        assert "✓" in msg

    def test_after_cb_handles_missing_usage_metadata(self, caplog):
        """``after_cb`` must not crash when ``usage_metadata`` is ``None``."""
        before_cb, after_cb = make_observability_callbacks(
            analyst="fundamental",
            ticker="MSFT",
            ticker_index=1,
            ticker_count=5,
            model_name="gemini-test",
        )
        ctx = _make_fake_context()
        before_cb(callback_context=ctx, llm_request=None)

        # LlmResponse with no usage_metadata.
        resp = types.SimpleNamespace(usage_metadata=None)

        with caplog.at_level(logging.INFO, logger="stockbot.tick"):
            result = after_cb(callback_context=ctx, llm_response=resp)

        assert result is None
        tick_records = [r for r in caplog.records if r.name == "stockbot.tick"]
        assert len(tick_records) == 1

    def test_after_cb_handles_missing_start_stamp(self, caplog):
        """``after_cb`` must not crash when the start stamp is absent."""
        _, after_cb = make_observability_callbacks(
            analyst="news",
            ticker="TSLA",
            ticker_index=3,
            ticker_count=5,
            model_name="gemini-test",
        )
        # Context with NO start timestamp (simulates test setups that skip before_cb).
        ctx = _make_fake_context()
        resp = _make_fake_llm_response()

        with caplog.at_level(logging.INFO, logger="stockbot.tick"):
            result = after_cb(callback_context=ctx, llm_response=resp)

        assert result is None
        # Should still emit a row.
        tick_records = [r for r in caplog.records if r.name == "stockbot.tick"]
        assert len(tick_records) == 1

    def test_after_cb_handles_none_token_fields(self, caplog):
        """``after_cb`` must not crash when token count fields are ``None``."""
        before_cb, after_cb = make_observability_callbacks(
            analyst="news",
            ticker="GOOG",
            ticker_index=4,
            ticker_count=5,
            model_name="gemini-test",
        )
        ctx = _make_fake_context()
        before_cb(callback_context=ctx, llm_request=None)
        resp = _make_fake_llm_response(prompt_tokens=None, candidate_tokens=None)

        with caplog.at_level(logging.INFO, logger="stockbot.tick"):
            after_cb(callback_context=ctx, llm_response=resp)

        tick_records = [r for r in caplog.records if r.name == "stockbot.tick"]
        assert len(tick_records) == 1


# ---------------------------------------------------------------------------
# Cache + observability composition
# ---------------------------------------------------------------------------

class TestCacheObservabilityComposition:
    """Verify that observability callbacks compose correctly with the cache chain.

    These tests use ``_chain_before`` / ``_chain_after`` from ``_common`` and a
    fake cache callback to assert:

    1. When the cache hits (``before_cache`` returns non-None), the chain
       short-circuits BEFORE ``obs_before`` fires, so no start timestamp is
       stamped and ADK never invokes the after-model chain.
    2. When the cache misses (``before_cache`` returns ``None``), both
       ``obs_before`` (stamps start time) and ``obs_after`` (emits log row)
       fire in the correct order.
    """

    def test_cache_hit_short_circuits_before_obs_before(self):
        """On a cache hit, obs_before must NOT be called (no start stamp)."""
        from agents.analysts._common import _chain_before

        def cache_hit_before(callback_context, llm_request):
            """Fake cache hit — returns a synthetic response immediately."""
            return "SYNTHETIC_RESPONSE"

        before_cb, _ = make_observability_callbacks(
            analyst="news",
            ticker="AAPL",
            ticker_index=1,
            ticker_count=5,
            model_name="gemini-test",
        )

        chained_before = _chain_before(cache_hit_before, before_cb)
        ctx = _make_fake_context()

        result = chained_before(callback_context=ctx, llm_request=None)

        # Chain must return the synthetic response.
        assert result == "SYNTHETIC_RESPONSE"

        # obs_before must NOT have stamped the state key — it was skipped.
        assert "temp:_llm_start_news_AAPL" not in ctx.state

    def test_cache_miss_allows_obs_before_and_after(self, caplog):
        """On a cache miss, both obs callbacks must fire normally."""
        from agents.analysts._common import _chain_after, _chain_before

        def cache_miss_before(callback_context, llm_request):
            """Fake cache miss — returns None so the LLM call proceeds."""
            return None

        def cache_after(callback_context, llm_response):
            """Fake cache after — no-op (cache write skipped in tests)."""
            return None

        before_cb, after_cb = make_observability_callbacks(
            analyst="news",
            ticker="AAPL",
            ticker_index=1,
            ticker_count=5,
            model_name="gemini-test",
        )

        chained_before = _chain_before(cache_miss_before, before_cb)
        chained_after  = _chain_after(cache_after, after_cb)

        ctx = _make_fake_context()

        # before chain — should return None (cache miss, obs stamps start time).
        result = chained_before(callback_context=ctx, llm_request=None)
        assert result is None

        # Start timestamp must have been stamped.
        assert "temp:_llm_start_news_AAPL" in ctx.state

        resp = _make_fake_llm_response(prompt_tokens=5000, candidate_tokens=800)

        with caplog.at_level(logging.INFO, logger="stockbot.tick"):
            chained_after(callback_context=ctx, llm_response=resp)

        tick_records = [r for r in caplog.records if r.name == "stockbot.tick"]
        assert len(tick_records) == 1
        assert "AAPL" in tick_records[0].getMessage()
