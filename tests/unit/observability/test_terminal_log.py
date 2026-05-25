"""Unit tests for ``observability.terminal_log``.

Covers:
- ``format_tokens`` — compact k-suffix formatting.
- ``format_latency`` — fixed-width seconds formatting.
- ``make_observability_callbacks`` factory — accumulator behaviour and
  correct DEBUG emission without INFO rows on ``stockbot.tick``.
- ``emit_analyst_summary`` — singleton vs multi-ticker shapes, failure
  counting, and missing-token-field defensiveness.
- Cache + observability callback composition — verifies that ``_chain_before``
  short-circuits correctly and ``_chain_after`` runs all hooks unconditionally.
"""
from __future__ import annotations

import logging
import time
import types

import pytest

from observability.terminal_log import (
    emit_analyst_summary,
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
# Helpers shared across callback tests
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


# ---------------------------------------------------------------------------
# make_observability_callbacks — accumulator behaviour
# ---------------------------------------------------------------------------

class TestMakeObservabilityCallbacks:
    """Tests for the ``make_observability_callbacks`` factory.

    The key behavioural change (post-parallelism refactor):
    - ``after_cb`` must NOT emit an INFO row on ``stockbot.tick``.
    - ``after_cb`` must write a single structured record to a disjoint
      per-ticker key at ``state["temp:_obs_<analyst>_call_<TICKER>"]``.
      The per-ticker key replaces the previous shared list, which raced
      under ADK's ParallelAgent fan-out (last-writer-wins on the merged
      state_delta either lost records → false "N failed", or stacked
      retry residue → false over-count like 22/20).
    - ``after_cb`` MUST emit at DEBUG level on ``stockbot.tick.calls`` so the
      buffered obs/ capture still gets fine-grained per-call detail.
    """

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

        # The start timestamp must be stamped in state under the temp: key.
        assert "temp:_llm_start_news_AAPL" in ctx.state
        assert isinstance(ctx.state["temp:_llm_start_news_AAPL"], float)

    def test_after_cb_does_not_emit_info_on_tick_logger(self, caplog):
        """``after_cb`` must NOT emit any INFO record on ``stockbot.tick``."""
        before_cb, after_cb = make_observability_callbacks(
            analyst="news",
            ticker="AAPL",
            ticker_index=2,
            ticker_count=20,
            model_name="gemini-test",
        )
        ctx = _make_fake_context()
        before_cb(callback_context=ctx, llm_request=None)
        resp = _make_fake_llm_response(prompt_tokens=8500, candidate_tokens=1100)

        with caplog.at_level(logging.INFO, logger="stockbot.tick"):
            result = after_cb(callback_context=ctx, llm_response=resp)

        assert result is None

        # No INFO records on stockbot.tick — the per-call row is gone.
        tick_info_records = [
            r for r in caplog.records
            if r.name == "stockbot.tick" and r.levelno >= logging.INFO
        ]
        assert len(tick_info_records) == 0

    def test_after_cb_emits_debug_on_calls_logger(self, caplog):
        """``after_cb`` must emit exactly one DEBUG record on ``stockbot.tick.calls``."""
        before_cb, after_cb = make_observability_callbacks(
            analyst="news",
            ticker="AAPL",
            ticker_index=2,
            ticker_count=20,
            model_name="gemini-test",
        )
        ctx = _make_fake_context()
        before_cb(callback_context=ctx, llm_request=None)
        resp = _make_fake_llm_response(prompt_tokens=8500, candidate_tokens=1100)

        with caplog.at_level(logging.DEBUG, logger="stockbot.tick.calls"):
            after_cb(callback_context=ctx, llm_response=resp)

        calls_records = [r for r in caplog.records if r.name == "stockbot.tick.calls"]
        assert len(calls_records) == 1
        msg = calls_records[0].getMessage()
        # Per-call detail should include analyst name and ticker.
        assert "news" in msg
        assert "AAPL" in msg

    def test_after_cb_writes_per_ticker_record(self):
        """``after_cb`` must write a record to ``state["temp:_obs_<analyst>_call_<TICKER>"]``."""
        before_cb, after_cb = make_observability_callbacks(
            analyst="news",
            ticker="AAPL",
            ticker_index=1,
            ticker_count=5,
            model_name="gemini-test",
        )
        ctx = _make_fake_context()
        before_cb(callback_context=ctx, llm_request=None)
        resp = _make_fake_llm_response(prompt_tokens=8500, candidate_tokens=1100)

        after_cb(callback_context=ctx, llm_response=resp)

        record = ctx.state.get("temp:_obs_news_call_AAPL")
        assert isinstance(record, dict)
        assert record["ticker"] == "AAPL"
        assert record["ok"] is True
        # Elapsed should be a small positive float (we just ran before_cb).
        assert isinstance(record["elapsed"], float)
        assert record["elapsed"] >= 0.0
        assert record["prompt_tokens"] == 8500
        assert record["candidate_tokens"] == 1100

    def test_after_cb_writes_disjoint_keys_per_ticker(self):
        """Multiple after_cb calls for different tickers must use disjoint keys.

        This is the key invariant that eliminates the parallel-fan-out race:
        each branch owns its own key, so there is no shared mutable state for
        sibling ParallelAgent children to clobber.
        """
        _, after_cb_aapl = make_observability_callbacks(
            analyst="news", ticker="AAPL",
            ticker_index=1, ticker_count=3, model_name="gemini-test",
        )
        _, after_cb_msft = make_observability_callbacks(
            analyst="news", ticker="MSFT",
            ticker_index=2, ticker_count=3, model_name="gemini-test",
        )

        # Share a single context (simulates shared session state).
        ctx = _make_fake_context()

        after_cb_aapl(callback_context=ctx, llm_response=_make_fake_llm_response(1000, 100))
        after_cb_msft(callback_context=ctx, llm_response=_make_fake_llm_response(2000, 200))

        # Two disjoint keys, one record each.
        rec_aapl = ctx.state.get("temp:_obs_news_call_AAPL")
        rec_msft = ctx.state.get("temp:_obs_news_call_MSFT")
        assert rec_aapl is not None and rec_aapl["ticker"] == "AAPL"
        assert rec_msft is not None and rec_msft["ticker"] == "MSFT"

        # No shared "calls" list should exist any more.
        assert ctx.state.get("temp:_obs_news_calls") is None

    def test_after_cb_handles_missing_usage_metadata(self):
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

        resp = types.SimpleNamespace(usage_metadata=None)
        result = after_cb(callback_context=ctx, llm_response=resp)

        assert result is None
        # Record still written — token fields will be None.
        record = ctx.state.get("temp:_obs_fundamental_call_MSFT")
        assert record is not None
        assert record["prompt_tokens"] is None
        assert record["candidate_tokens"] is None

    def test_after_cb_handles_missing_start_stamp(self):
        """``after_cb`` must not crash when the start stamp is absent."""
        _, after_cb = make_observability_callbacks(
            analyst="news",
            ticker="TSLA",
            ticker_index=3,
            ticker_count=5,
            model_name="gemini-test",
        )
        ctx = _make_fake_context()  # no start timestamp stamped
        resp = _make_fake_llm_response()

        result = after_cb(callback_context=ctx, llm_response=resp)

        assert result is None
        # Record written with elapsed=None.
        record = ctx.state.get("temp:_obs_news_call_TSLA")
        assert record is not None
        assert record["elapsed"] is None

    def test_after_cb_handles_none_token_fields(self):
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

        after_cb(callback_context=ctx, llm_response=resp)

        record = ctx.state.get("temp:_obs_news_call_GOOG")
        assert record is not None
        assert record["prompt_tokens"] is None
        assert record["candidate_tokens"] is None

    def test_call_record_key_uses_temp_prefix(self):
        """The per-ticker call record key must start with ``temp:`` so ADK strips it."""
        before_cb, after_cb = make_observability_callbacks(
            analyst="fundamental",
            ticker="NVDA",
            ticker_index=1,
            ticker_count=1,
            model_name="gemini-test",
        )
        ctx = _make_fake_context()
        before_cb(callback_context=ctx, llm_request=None)
        after_cb(callback_context=ctx, llm_response=_make_fake_llm_response())

        # Exactly one observability key, on the per-ticker shape.
        obs_keys = [k for k in ctx.state if k.startswith("temp:_obs_")]
        assert len(obs_keys) == 1
        assert obs_keys[0] == "temp:_obs_fundamental_call_NVDA"


# ---------------------------------------------------------------------------
# emit_analyst_summary
# ---------------------------------------------------------------------------

def _make_calls(
    n: int,
    *,
    elapsed: float = 1.0,
    prompt_tokens: int | None = 5000,
    candidate_tokens: int | None = 500,
) -> list[dict]:
    """Build a synthetic list of n successful call records.

    Parameters
    ----------
    n:
        Number of records to generate.
    elapsed:
        Latency in seconds for each record (all identical for test simplicity).
    prompt_tokens:
        Prompt token count per record, or None to simulate missing metadata.
    candidate_tokens:
        Candidate token count per record, or None to simulate missing metadata.

    Returns
    -------
    list[dict]
        List of per-call record dicts matching the accumulator format.
    """
    return [
        {
            "ticker":           f"TICK{i}",
            "elapsed":          elapsed,
            "prompt_tokens":    prompt_tokens,
            "candidate_tokens": candidate_tokens,
            "ok":               True,
        }
        for i in range(n)
    ]


class TestEmitAnalystSummary:
    """Tests for the ``emit_analyst_summary`` function."""

    def test_multi_ticker_emits_one_info_row(self, caplog):
        """Multi-ticker path must emit exactly one INFO record on ``stockbot.tick``."""
        calls = _make_calls(18, elapsed=1.4, prompt_tokens=20000, candidate_tokens=3600)

        with caplog.at_level(logging.INFO, logger="stockbot.tick"):
            emit_analyst_summary("news", calls=calls, ticker_count=20)

        tick_records = [r for r in caplog.records if r.name == "stockbot.tick"]
        assert len(tick_records) == 1

    def test_multi_ticker_row_contains_label(self, caplog):
        """The emitted row must contain the analyst label."""
        calls = _make_calls(5, elapsed=1.0)

        with caplog.at_level(logging.INFO, logger="stockbot.tick"):
            emit_analyst_summary("fundamental", calls=calls, ticker_count=5)

        msg = caplog.records[-1].getMessage()
        assert "fundamental" in msg

    def test_multi_ticker_row_shows_succeeded_of_total(self, caplog):
        """Row must show ``succeeded/total`` count."""
        calls = _make_calls(18, elapsed=1.0)

        with caplog.at_level(logging.INFO, logger="stockbot.tick"):
            emit_analyst_summary("news", calls=calls, ticker_count=20)

        msg = caplog.records[-1].getMessage()
        assert "18/20" in msg

    def test_multi_ticker_row_shows_failed_count(self, caplog):
        """Row must show failure count when some branches failed."""
        calls = _make_calls(18, elapsed=1.0)

        with caplog.at_level(logging.INFO, logger="stockbot.tick"):
            emit_analyst_summary("news", calls=calls, ticker_count=20)

        msg = caplog.records[-1].getMessage()
        assert "2 failed" in msg

    def test_multi_ticker_no_failed_annotation_when_all_succeeded(self, caplog):
        """Row must NOT contain 'failed' when all tickers succeeded."""
        calls = _make_calls(5, elapsed=1.0)

        with caplog.at_level(logging.INFO, logger="stockbot.tick"):
            emit_analyst_summary("news", calls=calls, ticker_count=5)

        msg = caplog.records[-1].getMessage()
        assert "failed" not in msg

    def test_multi_ticker_row_contains_median_and_max_latency(self, caplog):
        """Multi-ticker row must contain median and max latency markers."""
        calls = _make_calls(3, elapsed=1.5)

        with caplog.at_level(logging.INFO, logger="stockbot.tick"):
            emit_analyst_summary("news", calls=calls, ticker_count=3)

        msg = caplog.records[-1].getMessage()
        assert "median" in msg
        assert "max" in msg

    def test_multi_ticker_row_contains_tok_total(self, caplog):
        """Multi-ticker row must include total token count."""
        # 3 calls × (5000 prompt + 500 candidate) = 16.5k total
        calls = _make_calls(3, elapsed=1.0, prompt_tokens=5000, candidate_tokens=500)

        with caplog.at_level(logging.INFO, logger="stockbot.tick"):
            emit_analyst_summary("news", calls=calls, ticker_count=3)

        msg = caplog.records[-1].getMessage()
        assert "tok" in msg

    def test_singleton_emits_one_info_row(self, caplog):
        """Singleton path must emit exactly one INFO record on ``stockbot.tick``."""
        calls = _make_calls(1, elapsed=2.1, prompt_tokens=8000, candidate_tokens=400)

        with caplog.at_level(logging.INFO, logger="stockbot.tick"):
            emit_analyst_summary("strategist", calls=calls, ticker_count=1)

        tick_records = [r for r in caplog.records if r.name == "stockbot.tick"]
        assert len(tick_records) == 1

    def test_singleton_row_contains_label(self, caplog):
        """Singleton row must contain the analyst label."""
        calls = _make_calls(1, elapsed=2.1)

        with caplog.at_level(logging.INFO, logger="stockbot.tick"):
            emit_analyst_summary("strategist", calls=calls, ticker_count=1)

        msg = caplog.records[-1].getMessage()
        assert "strategist" in msg

    def test_singleton_row_shows_one_of_one(self, caplog):
        """Singleton row must show ``1/1`` count."""
        calls = _make_calls(1, elapsed=2.1)

        with caplog.at_level(logging.INFO, logger="stockbot.tick"):
            emit_analyst_summary("strategist", calls=calls, ticker_count=1)

        msg = caplog.records[-1].getMessage()
        assert "1/1" in msg

    def test_singleton_row_no_median_no_max(self, caplog):
        """Singleton row must NOT contain 'median' or 'max' latency labels."""
        calls = _make_calls(1, elapsed=2.1)

        with caplog.at_level(logging.INFO, logger="stockbot.tick"):
            emit_analyst_summary("strategist", calls=calls, ticker_count=1)

        msg = caplog.records[-1].getMessage()
        assert "median" not in msg
        assert "max" not in msg

    def test_empty_calls_all_failed(self, caplog):
        """When the accumulator is empty, all tickers count as failed."""
        with caplog.at_level(logging.INFO, logger="stockbot.tick"):
            emit_analyst_summary("news", calls=[], ticker_count=20)

        tick_records = [r for r in caplog.records if r.name == "stockbot.tick"]
        assert len(tick_records) == 1
        msg = tick_records[0].getMessage()
        assert "0/20" in msg

    def test_missing_token_fields_do_not_crash(self, caplog):
        """Calls with ``None`` token fields must not cause an exception."""
        calls = _make_calls(3, elapsed=1.0, prompt_tokens=None, candidate_tokens=None)

        # Should not raise.
        with caplog.at_level(logging.INFO, logger="stockbot.tick"):
            emit_analyst_summary("news", calls=calls, ticker_count=3)

        tick_records = [r for r in caplog.records if r.name == "stockbot.tick"]
        assert len(tick_records) == 1

    def test_missing_elapsed_do_not_crash(self, caplog):
        """Calls with ``elapsed=None`` must not cause a statistics error."""
        calls = [
            {"ticker": "AAPL", "elapsed": None, "prompt_tokens": 100, "candidate_tokens": 10, "ok": True},
            {"ticker": "MSFT", "elapsed": 1.2,  "prompt_tokens": 200, "candidate_tokens": 20, "ok": True},
        ]

        with caplog.at_level(logging.INFO, logger="stockbot.tick"):
            emit_analyst_summary("news", calls=calls, ticker_count=2)

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
       ``obs_before`` (stamps start time) and ``obs_after`` (appends to
       accumulator) fire in the correct order.
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

    def test_cache_miss_stamps_start_time_and_appends_to_accumulator(self, caplog):
        """On a cache miss, obs callbacks must fire and append a record to the accumulator."""
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
        chained_after(callback_context=ctx, llm_response=resp)

        # Per-ticker call record must have been written for AAPL.
        record = ctx.state.get("temp:_obs_news_call_AAPL")
        assert isinstance(record, dict)
        assert record["ticker"] == "AAPL"

        # No INFO records on stockbot.tick — per-call row has been removed.
        tick_info = [
            r for r in caplog.records
            if r.name == "stockbot.tick" and r.levelno >= logging.INFO
        ]
        assert len(tick_info) == 0


# ---------------------------------------------------------------------------
# emit_analyst_summary — retries kwarg rendering
# ---------------------------------------------------------------------------

def test_emit_analyst_summary_no_retries_renders_clean(caplog) -> None:
    """No retry suffix is rendered when retries is None or empty."""

    import logging

    caplog.set_level(logging.INFO, logger="stockbot.tick")

    emit_analyst_summary(
        "news",
        calls         = [{"ticker": "AAPL", "elapsed": 1.0, "prompt_tokens": 1000, "candidate_tokens": 500, "ok": True}],
        ticker_count  = 1,
    )

    rows = [r.message for r in caplog.records if "news" in r.message]
    assert rows, "expected at least one stockbot.tick row mentioning 'news'"
    assert "retries" not in rows[-1]


def test_emit_analyst_summary_renders_retries_suffix(caplog) -> None:
    """A non-empty retries dict renders a ` · retries <class>×<n>` suffix."""

    import logging

    caplog.set_level(logging.INFO, logger="stockbot.tick")

    emit_analyst_summary(
        "fundamental",
        calls         = [{"ticker": "AAPL", "elapsed": 1.0, "prompt_tokens": 1000, "candidate_tokens": 500, "ok": True}],
        ticker_count  = 1,
        retries       = {"rate_limit": 2},
    )

    rows = [r.message for r in caplog.records if "fundamental" in r.message]
    assert any("retries rate_limit×2" in r for r in rows)


def test_emit_analyst_summary_renders_multiple_retry_classes(caplog) -> None:
    """Multiple non-zero classes all appear in the suffix in fixed order
    (rate_limit, timeout, schema)."""

    import logging

    caplog.set_level(logging.INFO, logger="stockbot.tick")

    emit_analyst_summary(
        "strategist",
        calls         = [{"ticker": "decision", "elapsed": 2.0, "prompt_tokens": 5000, "candidate_tokens": 3000, "ok": True}],
        ticker_count  = 1,
        retries       = {"schema": 2, "timeout": 1},      # given out-of-order
    )

    rows = [r.message for r in caplog.records if "strategist" in r.message]
    last = rows[-1]
    # Fixed order: rate_limit then timeout then schema.  rate_limit is zero so it's omitted.
    assert "retries timeout×1 schema×2" in last


def test_emit_analyst_summary_skips_zero_classes(caplog) -> None:
    """Zero-count classes are omitted from the suffix."""

    import logging

    caplog.set_level(logging.INFO, logger="stockbot.tick")

    emit_analyst_summary(
        "news",
        calls         = [{"ticker": "AAPL", "elapsed": 1.0, "prompt_tokens": 1000, "candidate_tokens": 500, "ok": True}],
        ticker_count  = 1,
        retries       = {"rate_limit": 0, "timeout": 1, "schema": 0},
    )

    rows = [r.message for r in caplog.records if "news" in r.message]
    last = rows[-1]
    assert "retries timeout×1" in last
    assert "rate_limit" not in last
    assert "schema"     not in last
