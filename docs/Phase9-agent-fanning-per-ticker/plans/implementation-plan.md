# Phase 9 — Per-Ticker Analyst Fan-Out — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/Phase9-agent-fanning-per-ticker/spec.md`

**Goal:** Replace the batched one-LLM-call-per-tick News and Fundamental analyst design with a SequentialAgent of per-ticker LlmAgents joined back into the canonical `news_verdicts` / `fundamental_verdicts` contract keys, with per-branch failure containment.

**Architecture:** Each LLM analyst branch is `SequentialAgent[FetchAgent, *PerTickerBranches, JoinerAgent]`, rebuilt every tick from the current watchlist. Per-ticker branches are `IsolatedFailureWrapper(RetryingAgentWrapper(LlmAgent))` so a single ticker's failure synthesises a no-data verdict at the joiner without aborting the tick. `build_pipeline()` gains an explicit `tickers` parameter; the live `tick.py` and the backtest `driver.py` both call it per tick.

**Tech Stack:** Google ADK 1.34 (`BaseAgent`, `SequentialAgent`, `LlmAgent`, `EventActions`), Pydantic 2, Tenacity, `pytest` (sync + `pytest-asyncio`).

---

## File Structure

**New files:**

| Path | Responsibility |
|---|---|
| `src/agents/isolated_failure.py` | `IsolatedFailureWrapper(BaseAgent)` — catches and logs exceptions from a child branch without propagating them. |
| `src/agents/analysts/news/fetch_agent.py` | `NewsFetchAgent(BaseAgent)` — fetches headlines for all tickers; yields `state_delta` with `temp:news_data` + per-ticker `temp:news_context_<TICKER>`. |
| `src/agents/analysts/fundamental/fetch_agent.py` | `FundamentalFetchAgent(BaseAgent)` — mirror for fundamentals. |
| `src/agents/analysts/news/joiner.py` | `NewsJoinerAgent(BaseAgent)` — reads N `temp:news_verdict_<TICKER>` keys, builds canonical `news_verdicts` + `news_evidence`, synthesises no-data verdicts for missing keys. |
| `src/agents/analysts/fundamental/joiner.py` | `FundamentalJoinerAgent(BaseAgent)` — mirror for fundamentals. |
| `src/agents/analysts/news/per_ticker.py` | `build_news_branch_for_ticker(ticker, vocab)` — constructs one per-ticker News LlmAgent with cache + retry + isolation wrappers. |
| `src/agents/analysts/fundamental/per_ticker.py` | `build_fundamental_branch_for_ticker(ticker, vocab)` — mirror. |
| `tests/agents/test_isolated_failure.py` | Unit tests for `IsolatedFailureWrapper`. |
| `tests/analysts/news/test_fetch_agent.py` | Unit tests for `NewsFetchAgent`. |
| `tests/analysts/fundamental/test_fetch_agent.py` | Unit tests for `FundamentalFetchAgent`. |
| `tests/analysts/news/test_joiner.py` | Unit tests for `NewsJoinerAgent`. |
| `tests/analysts/fundamental/test_joiner.py` | Unit tests for `FundamentalJoinerAgent`. |
| `tests/analysts/test_per_ticker_branch.py` | Unit tests for the per-ticker branch factories (both analysts). |

**Rewritten files:**

| Path | Change |
|---|---|
| `src/agents/analysts/news/agent.py` | `build_news_analyst(vocab)` → `build_news_branch(vocab, tickers)` returns a `SequentialAgent[NewsFetchAgent, *per-ticker branches, NewsJoinerAgent]`. |
| `src/agents/analysts/fundamental/agent.py` | Mirror. |
| `src/agents/analysts/news/prompts.py` | `_TEMPLATE` rewritten for single-ticker; `build_news_instruction(vocab)` unchanged signature, output single-ticker template with `{ticker}` + `{news_context}` placeholders. |
| `src/agents/analysts/fundamental/prompts.py` | Mirror. |
| `src/agents/analysts/cache_callbacks.py` | `make_report_cache_callbacks(...)` gains `ticker: str` and `output_schema: type[BaseModel]` parameters; iterates **one** ticker, returns single-`TickerVerdict` synthetic `LlmResponse` on hit. |
| `src/orchestrator/pipeline.py` | `build_pipeline(broker, db_session, tickers)` — new required `tickers` arg; `_build_analyst_pool(tickers)` composes per-ticker branches. |
| `src/orchestrator/tick.py` | Passes `tickers=state["tickers"]` to `build_pipeline`. |
| `src/backtest/driver.py` | Moves `build_pipeline(...)` from `__init__` to `_run_one_tick(state)` so the pipeline is rebuilt per tick from `state["tickers"]`. |
| `docs/contract-invariants.md` | §A `news_verdicts` / `fundamental_verdicts` rows — Owner column updated to `NewsJoinerAgent` / `FundamentalJoinerAgent`. |

**Retired (delete after confirming no other consumers):**

| Path | Reason |
|---|---|
| `src/agents/analysts/_base_yield.py::YieldingAnalystWrapper` | News/Fundamental no longer have an `after_agent_callback` to republish. Technical, Social, SmartMoney never used it. After Task 11 rewrites the News/Fundamental factories, the wrapper has zero remaining consumers (verified via grep — only docstring mentions in `src/agents/llm_retry.py` remain, plus dedicated unit tests). Delete the module **and** the two dedicated unit-test files (`tests/unit/agents/analysts/test_news_yield.py`, `tests/unit/agents/analysts/test_fundamental_yield.py`). |
| `src/agents/analysts/_common.py::make_evidence_callback` **— call sites in News/Fundamental only** | News/Fundamental shed the `after_agent_callback`; the body moves into the joiners. **Do NOT delete the factory itself** — Technical, Social, and SmartMoney still register it as their `after_agent_callback` (see `src/agents/analysts/technical/agent.py:73`, `src/agents/analysts/social/agent.py:74`, `src/agents/analysts/smart_money/agent.py:80`). `tests/agents/analysts/test_evidence_callback.py` also stays. |

---

## Task 1: `IsolatedFailureWrapper` infrastructure

**Files:**
- Create: `src/agents/isolated_failure.py`
- Test: `tests/agents/test_isolated_failure.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/test_isolated_failure.py
"""Unit tests for IsolatedFailureWrapper — confirms failure-containment semantics."""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

import pytest
from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from agents.isolated_failure import IsolatedFailureWrapper


class _OkAgent(BaseAgent):
    """Inner agent that yields one event and returns normally."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            actions=EventActions(state_delta={"ok_key": "ok_value"}),
        )


class _BoomAgent(BaseAgent):
    """Inner agent that raises mid-run."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        raise RuntimeError("simulated branch failure")
        yield  # pragma: no cover — unreachable, kept for generator typing


@pytest.mark.asyncio
async def test_ok_path_forwards_inner_events(_invocation_context):
    """When the inner runs cleanly, every inner event is forwarded unchanged."""

    wrapper = IsolatedFailureWrapper(
        name="TestWrapper",
        inner=_OkAgent(name="Inner"),
        analyst="news",
        ticker="AAPL",
    )

    events = [ev async for ev in wrapper.run_async(_invocation_context)]

    assert len(events) == 1
    assert events[0].actions.state_delta == {"ok_key": "ok_value"}


@pytest.mark.asyncio
async def test_failure_is_swallowed_and_logged(_invocation_context, caplog):
    """When the inner raises, the wrapper yields zero events and logs the failure."""

    wrapper = IsolatedFailureWrapper(
        name="TestWrapper",
        inner=_BoomAgent(name="Inner"),
        analyst="news",
        ticker="AAPL",
    )

    with caplog.at_level(logging.WARNING, logger="agents.isolated_failure"):
        events = [ev async for ev in wrapper.run_async(_invocation_context)]

    assert events == []
    # Structured failure log was emitted.
    failure_records = [r for r in caplog.records if getattr(r, "kind", None) == "branch_failed"]
    assert len(failure_records) == 1
    rec = failure_records[0]
    assert rec.analyst == "news"
    assert rec.ticker == "AAPL"
    assert rec.exc_type == "RuntimeError"
    assert "simulated branch failure" in rec.exc_message


@pytest.fixture
def _invocation_context():
    """Minimal InvocationContext stub for BaseAgent.run_async."""

    from google.adk.sessions import InMemorySessionService
    import asyncio

    svc = InMemorySessionService()
    session = asyncio.get_event_loop().run_until_complete(
        svc.create_session(
            app_name="test", user_id="test", state={}, session_id="t1",
        )
    )
    return InvocationContext(
        session_service=svc,
        session=session,
        invocation_id="inv-1",
        agent=None,
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/agents/test_isolated_failure.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agents.isolated_failure'`.

- [ ] **Step 3: Implement `IsolatedFailureWrapper`**

```python
# src/agents/isolated_failure.py
"""IsolatedFailureWrapper — catches and logs exceptions from a child branch
without propagating them to the parent SequentialAgent.

Used by the per-ticker analyst fan-out (Phase 9): a single ticker's
persistent failure must not abort the tick.  When the inner raises, the
wrapper yields zero events; the downstream joiner then synthesises a
no-data verdict for the absent state key.

Wrapping order for per-ticker LLM branches:
    IsolatedFailureWrapper(RetryingAgentWrapper(LlmAgent))
— ``RetryingAgentWrapper`` exhausts its retries first; only then does its
exception bubble into ``IsolatedFailureWrapper``'s ``except``.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

_LOGGER = logging.getLogger(__name__)


class IsolatedFailureWrapper(BaseAgent):
    """Proxy an inner agent and suppress any exception it raises.

    The wrapper forwards every event the inner yields up to the point of
    failure.  If the inner raises, the exception is logged with structured
    fields (``analyst``, ``ticker``, ``kind="branch_failed"``, ``exc_type``,
    ``exc_message``) and *no further events* are yielded.  The wrapper
    returns normally so the parent ``SequentialAgent`` continues running.

    Attributes
    ----------
    inner:
        The wrapped agent (typically a ``RetryingAgentWrapper`` around an
        ``LlmAgent``).
    analyst:
        Short identifier of the analyst this branch belongs to (e.g.
        ``"news"`` or ``"fundamental"``).  Surfaced in the failure log.
    ticker:
        Ticker symbol this branch is bound to.  Surfaced in the failure log.
    """

    inner: Any
    analyst: str
    ticker: str

    model_config = {"arbitrary_types_allowed": True}

    def __init__(
        self,
        *,
        name: str,
        inner: Any,
        analyst: str,
        ticker: str,
    ) -> None:
        """Initialise the wrapper.

        Args:
            name: ADK agent name (e.g. ``"NewsAnalyst_AAPL_isolated"``).
            inner: The inner agent instance to delegate to.
            analyst: Short analyst identifier ("news" / "fundamental").
            ticker: Ticker symbol bound to this branch.
        """
        super().__init__(
            name=name,
            inner=inner,
            analyst=analyst,
            ticker=ticker,
        )

    async def _run_async_impl(
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Delegate to ``inner``; suppress and log any exception it raises.

        Args:
            ctx: ADK invocation context.

        Yields:
            Every event yielded by the inner agent up to the point of
            failure.  Yields nothing on or after an exception.
        """
        try:
            async for inner_event in self.inner.run_async(ctx):
                yield inner_event
        except Exception as exc:  # noqa: BLE001 — deliberate broad catch at the isolation boundary
            # Structured failure log — picked up by the per-tick obs/logs
            # aggregator so failed branches are visible without crashing the tick.
            _LOGGER.warning(
                "branch_failed",
                extra={
                    "kind":        "branch_failed",
                    "analyst":     self.analyst,
                    "ticker":      self.ticker,
                    "exc_type":    type(exc).__name__,
                    "exc_message": str(exc),
                },
            )
            # Deliberately return: no further events.  The downstream joiner
            # sees the missing ``temp:<analyst>_verdict_<TICKER>`` key and
            # synthesises a no-data verdict.
            return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/agents/test_isolated_failure.py -v`
Expected: PASS — both tests green.

- [ ] **Step 5: Commit**

```bash
git add src/agents/isolated_failure.py tests/agents/test_isolated_failure.py
git commit -m "feat(agents): IsolatedFailureWrapper — catch and log branch failures without propagating"
```

---

## Task 2: Per-ticker prompt template (News)

**Files:**
- Modify: `src/agents/analysts/news/prompts.py`
- Test: `tests/analysts/news/test_prompts.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/analysts/news/test_prompts.py
"""Tests for the single-ticker News prompt template."""
from __future__ import annotations

from agents.analysts.heuristics import NewsVocabulary
from agents.analysts.news.prompts import build_news_instruction


def _vocab() -> NewsVocabulary:
    """Build a small valid NewsVocabulary for prompt-rendering tests."""

    return NewsVocabulary(
        catalysts=["earnings", "guidance", "macro"],
        novelty=["new", "ongoing", "stale"],
        direction=["positive", "negative", "mixed", "none"],
    )


def test_instruction_addresses_single_ticker():
    """The rendered instruction must address ONE ticker, not 'each ticker'."""

    instruction = build_news_instruction(_vocab())

    # Single-ticker phrasing — must NOT mention "each ticker" or "batch".
    assert "each ticker" not in instruction.lower()
    assert "the batch" not in instruction.lower()
    assert "MUST cover ALL tickers" not in instruction

    # Must keep the runtime placeholders that ADK fills per branch.
    assert "{ticker}" in instruction
    assert "{news_context}" in instruction


def test_instruction_contains_closed_vocabulary():
    """Closed-vocab tokens must still substitute into the prompt."""

    instruction = build_news_instruction(_vocab())

    assert "earnings | guidance | macro" in instruction
    assert "new | ongoing | stale" in instruction
    assert "positive | negative | mixed | none" in instruction


def test_instruction_describes_single_verdict_output():
    """Output spec must describe ONE verdict per call, not a list."""

    instruction = build_news_instruction(_vocab())

    # Output schema directive — single TickerVerdict, not a batch.
    assert "Output ONE JSON object" in instruction or \
           "Emit one verdict" in instruction or \
           "single verdict" in instruction.lower()


def test_instruction_honours_output_caps_from_config():
    """`config/analysts.json::output_caps.verdict_rationale_max_chars`
    must still be substituted into the rendered instruction — the per-
    ticker rewrite must NOT bypass the config-driven character cap that
    bounds each analyst's free-text output.
    """

    from config.analysts import get_analysts_config

    instruction = build_news_instruction(_vocab())
    cap = get_analysts_config().output_caps.verdict_rationale_max_chars

    # The literal cap value should appear in the prompt (the template
    # writes "≤{rationale_max} chars" — `str.format` substitutes the int).
    assert f"≤{cap} chars" in instruction or f"{cap} chars" in instruction, (
        f"rendered prompt does not contain configured rationale cap {cap}; "
        "the per-ticker rewrite must preserve the config/analysts.json "
        "output_caps substitution path (see Phase 9 spec — config control "
        "of analyst output budgets is an invariant)."
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/analysts/news/test_prompts.py -v`
Expected: FAIL — old prompt template addresses "each ticker in the batch".

- [ ] **Step 3: Rewrite `_TEMPLATE` for single-ticker**

Edit `src/agents/analysts/news/prompts.py` — replace the `_TEMPLATE` constant and the `build_news_instruction` returns block:

```python
_TEMPLATE = """You are the News analyst.

You are focused on a SINGLE ticker for this call: {ticker}

Read the supplied headlines and article summaries for that ticker.
Output ONE JSON object — a single verdict — using ONLY the closed
vocabulary below.

Closed vocabulary (use these tags ONLY in key_factors):

  catalyst:<type>     ∈ {catalyst_options}
  novelty:<level>     ∈ {novelty_options}
  direction:<value>   ∈ {direction_options}
  material:<bool>     when material to a long-only fund

Output ONE JSON object with fields:
  ticker       string — MUST be exactly "{ticker}"
  lean         ∈ {{bullish, bearish, neutral}}
  magnitude    ∈ [0, 1]
  confidence   ∈ [0, 1]
  rationale    string ≤{rationale_max} chars naming the dominant catalyst
  key_factors  list of closed-vocabulary tags (≤8)
  is_no_data   true if no headlines in the window
  report       object — see schema below; omit only when is_no_data=true.

Report schema:
  summary  3-5 sentences of connective tissue covering the gestalt this
           tick — not a bullet list. Argue your lean.
  drivers  2-4 entries. Each driver:
    name       short label (4-6 words)
    direction  ∈ {{bull, bear, neutral}}
    weight     ∈ [0, 1] — relative importance vs other drivers; should sum
               roughly to 1.0 but is not strictly normalised
    body       2-3 sentences explaining the driver. Do NOT cite source URLs;
               synthesise.

The report is your reasoning; the verdict is your conclusion. They must be
consistent — the lean and direction-weighted driver mix should agree.

Decision rule:
- Lean ← direction: positive → bullish; negative → bearish; mixed/none → neutral.
- Magnitude ← novelty × material weight: high novelty + material → higher magnitude.
- Confidence scales with headline count; fewer than 3 articles caps confidence low.
- Conflicting direction signals across articles → mixed → neutral with low confidence.

--- HEADLINES & SUMMARIES FOR {ticker} ---
{news_context}
"""


def build_news_instruction(vocab: NewsVocabulary) -> str:
    """Render the News LLM instruction with the closed vocabulary baked in.

    Substitutes the three vocab placeholder tokens (``{catalyst_options}``,
    ``{novelty_options}``, ``{direction_options}``) using ``str.format``.
    The two runtime state tokens — ``{news_context}`` and ``{ticker}`` —
    are left intact in the returned string; the per-ticker branch factory
    substitutes ``{ticker}`` at build time, and ADK's
    ``inject_session_state`` substitutes ``{news_context}`` from
    ``state["news_context"]`` at run time (the per-ticker fetch agent
    writes a single-ticker block into that key — see Phase 9 spec §1).

    Parameters
    ----------
    vocab:
        Validated ``NewsVocabulary`` instance holding the three closed-
        vocabulary lists.

    Returns
    -------
    str
        The rendered instruction string.  Contains exactly two remaining
        single-brace tokens: ``{news_context}`` and ``{ticker}``.
    """
    out_caps = get_analysts_config().output_caps

    return _TEMPLATE.format(
        catalyst_options ="{" + " | ".join(vocab.catalysts) + "}",
        novelty_options  ="{" + " | ".join(vocab.novelty)   + "}",
        direction_options="{" + " | ".join(vocab.direction)  + "}",
        rationale_max    = out_caps.verdict_rationale_max_chars,
        # Protect the two runtime placeholders from str.format substitution
        # by passing them back as themselves.
        news_context="{news_context}",
        ticker      ="{ticker}",
    )
```

Also update the module docstring (lines 1-18) to reflect that the prompt now addresses one ticker per call and that `{ticker}` replaces `{tickers}`. Concretely: change "delivers a formatted multi-ticker block" → "delivers a single-ticker block" and "and tickers ... watchlist" → "and ticker ... the single ticker bound to this branch".

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/analysts/news/test_prompts.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/analysts/news/prompts.py tests/analysts/news/test_prompts.py
git commit -m "refactor(news/prompt): single-ticker instruction template"
```

---

## Task 3: Per-ticker prompt template (Fundamental)

**Files:**
- Modify: `src/agents/analysts/fundamental/prompts.py`
- Test: `tests/analysts/fundamental/test_prompts.py`

- [ ] **Step 1: Read the existing fundamental template**

Run: `PYTHONPATH=src .venv/bin/python -c "from agents.analysts.fundamental.prompts import _TEMPLATE; print(_TEMPLATE)"`
Note the exact placeholder names (e.g. `{fundamental_context}` vs `{news_context}`) and the "MUST cover ALL tickers" / "for each ticker" phrasing the rewrite needs to remove.

- [ ] **Step 2: Write the failing test**

```python
# tests/analysts/fundamental/test_prompts.py
"""Tests for the single-ticker Fundamental prompt template."""
from __future__ import annotations

from agents.analysts.fundamental.prompts import build_fundamental_instruction
from agents.analysts.heuristics import FundamentalVocabulary


def _vocab() -> FundamentalVocabulary:
    """Build a small valid FundamentalVocabulary for prompt-rendering tests."""

    # Inspect FundamentalVocabulary at implementation time and populate the
    # required closed-vocab lists with realistic sample tags.  The factory
    # rejects missing fields, so do not stub with empty lists.
    raise NotImplementedError(
        "Inspect FundamentalVocabulary at implementation time and populate."
    )


def test_instruction_addresses_single_ticker():
    """The rendered instruction must address ONE ticker, not 'each ticker'."""

    instruction = build_fundamental_instruction(_vocab())

    assert "each ticker" not in instruction.lower()
    assert "the batch" not in instruction.lower()
    assert "MUST cover ALL tickers" not in instruction

    assert "{ticker}" in instruction
    assert "{fundamental_context}" in instruction


def test_instruction_describes_single_verdict_output():
    """Output spec must describe ONE verdict per call."""

    instruction = build_fundamental_instruction(_vocab())

    assert "Output ONE JSON object" in instruction or \
           "single verdict" in instruction.lower()


def test_instruction_honours_output_caps_from_config():
    """`config/analysts.json::output_caps.verdict_rationale_max_chars`
    must still be substituted into the rendered instruction — the per-
    ticker rewrite must NOT bypass the config-driven character cap that
    bounds each analyst's free-text output.
    """

    from config.analysts import get_analysts_config

    instruction = build_fundamental_instruction(_vocab())
    cap = get_analysts_config().output_caps.verdict_rationale_max_chars

    assert f"≤{cap} chars" in instruction or f"{cap} chars" in instruction, (
        f"rendered prompt does not contain configured rationale cap {cap}; "
        "the per-ticker rewrite must preserve the config/analysts.json "
        "output_caps substitution path (see Phase 9 spec — config control "
        "of analyst output budgets is an invariant)."
    )
```

> **Note:** the `_vocab()` stub raises `NotImplementedError`. Read `src/agents/analysts/heuristics.py` for the `FundamentalVocabulary` definition, populate the lists, then run the tests.

- [ ] **Step 3: Run tests to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/analysts/fundamental/test_prompts.py -v`
Expected: FAIL (`NotImplementedError` from the vocab fixture, then assertion failures on phrasing).

- [ ] **Step 4: Implement — rewrite `_TEMPLATE` and populate `_vocab()`**

Apply the same rewrite pattern as Task 2 to `src/agents/analysts/fundamental/prompts.py`:
- Replace "For each ticker in the batch" → "for a SINGLE ticker for this call: {ticker}".
- Replace "Output a JSON object per ticker" → "Output ONE JSON object — a single verdict".
- Replace any "ticker must be one of the watchlist" → 'ticker MUST be exactly "{ticker}"'.
- Delete the "MUST cover ALL tickers: {tickers}" line.
- Replace `{tickers}` with `{ticker}` everywhere in the template.
- Update `build_fundamental_instruction`'s `str.format` call to use `ticker="{ticker}"` instead of `tickers="{tickers}"`.

Populate `_vocab()` in the test file using the real `FundamentalVocabulary` field names (replace the `raise NotImplementedError` placeholder).

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/analysts/fundamental/test_prompts.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agents/analysts/fundamental/prompts.py tests/analysts/fundamental/test_prompts.py
git commit -m "refactor(fundamental/prompt): single-ticker instruction template"
```

---

## Task 4: `NewsFetchAgent` (BaseAgent)

**Files:**
- Create: `src/agents/analysts/news/fetch_agent.py`
- Test: `tests/analysts/news/test_fetch_agent.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/analysts/news/test_fetch_agent.py
"""Unit tests for NewsFetchAgent.

The fetch agent runs ONCE per tick, calls the news provider for every
watchlist ticker, and yields exactly one state_delta event containing:
  - temp:news_data — dict keyed by ticker (machine-readable)
  - temp:news_context_<TICKER> — per-ticker formatted text block (one key
    per ticker; consumed by that ticker's LlmAgent via {news_context})
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions import InMemorySessionService

from agents.analysts.news.fetch_agent import NewsFetchAgent


@pytest.mark.asyncio
async def test_fetch_writes_per_ticker_context_keys():
    """One temp:news_context_<TICKER> key is written per watchlist ticker."""

    tickers = ["AAPL", "MSFT"]

    fake_news = {
        "AAPL": [{"title": "AAPL beats", "summary": "Strong quarter.", "published_at": "2026-05-21"}],
        "MSFT": [{"title": "MSFT guides up", "summary": "Cloud strong.", "published_at": "2026-05-21"}],
    }

    async def _mock_get_stock_news(ticker, as_of=None):
        return fake_news.get(ticker, [])

    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test",
        state={
            "tickers": tickers,
            "as_of":   datetime(2026, 5, 21, 14, 0),
        },
        session_id="t1",
    )

    agent = NewsFetchAgent(name="NewsFetch")
    ctx = InvocationContext(
        session_service=svc,
        session=session,
        invocation_id="inv-1",
        agent=agent,
    )

    with patch("agents.analysts.news.fetch_agent.get_stock_news", _mock_get_stock_news):
        events = [ev async for ev in agent.run_async(ctx)]

    assert len(events) == 1
    state_delta = events[0].actions.state_delta

    # temp:news_data carries the machine-readable per-ticker dict.
    assert "temp:news_data" in state_delta
    nd = state_delta["temp:news_data"]
    assert "AAPL" in nd and "MSFT" in nd
    assert nd["AAPL"]["news"][0]["title"] == "AAPL beats"

    # One temp:news_context_<TICKER> per ticker, each containing only that ticker's block.
    assert "temp:news_context_AAPL" in state_delta
    assert "temp:news_context_MSFT" in state_delta
    assert "AAPL beats" in state_delta["temp:news_context_AAPL"]
    assert "MSFT guides up" not in state_delta["temp:news_context_AAPL"]
    assert "MSFT guides up" in state_delta["temp:news_context_MSFT"]


@pytest.mark.asyncio
async def test_fetch_degrades_on_provider_error():
    """A provider exception for one ticker yields an empty context block for it."""

    async def _flaky_get_stock_news(ticker, as_of=None):
        if ticker == "MSFT":
            raise RuntimeError("provider down")
        return [{"title": "AAPL beats", "summary": "ok.", "published_at": "2026-05-21"}]

    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test",
        state={"tickers": ["AAPL", "MSFT"], "as_of": datetime(2026, 5, 21)},
        session_id="t1",
    )

    agent = NewsFetchAgent(name="NewsFetch")
    ctx = InvocationContext(
        session_service=svc, session=session,
        invocation_id="inv-1", agent=agent,
    )

    with patch("agents.analysts.news.fetch_agent.get_stock_news", _flaky_get_stock_news):
        events = [ev async for ev in agent.run_async(ctx)]

    sd = events[0].actions.state_delta
    # MSFT entry exists but is empty.
    assert sd["temp:news_data"]["MSFT"]["news"] == []
    # Per-ticker context for MSFT still exists, just empty/placeholder.
    assert "temp:news_context_MSFT" in sd
    assert "(no news available)" in sd["temp:news_context_MSFT"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/analysts/news/test_fetch_agent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agents.analysts.news.fetch_agent'`.

- [ ] **Step 3: Implement `NewsFetchAgent`**

```python
# src/agents/analysts/news/fetch_agent.py
"""NewsFetchAgent — BaseAgent that fetches news for every watchlist ticker.

Replaces the legacy ``news_fetch_callback`` (an ``before_agent_callback``
on the batched ``NewsAnalyst`` LlmAgent).  The per-ticker fan-out design
(Phase 9) splits the prompt so each ``NewsAnalyst_<TICKER>`` reads only
its own ticker's context — this agent writes one
``temp:news_context_<TICKER>`` key per ticker so ADK's
``inject_session_state`` fills each branch's ``{news_context}``
placeholder with single-ticker text.

Yielded keys (one state_delta event):
  - ``temp:news_data``  — dict[ticker, {"news": [serialised NewsArticle, ...]}]
  - ``temp:news_context_<TICKER>`` — formatted text block for one ticker
"""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from datetime import datetime

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from agents.analysts.news.fetch import _build_ticker_news_context  # reuse existing formatter
from data import get_stock_news
from data.timeguard import resolve_as_of
from observability.trace import _trace_maybe

_LOGGER = logging.getLogger(__name__)


class NewsFetchAgent(BaseAgent):
    """Fetch news for every watchlist ticker; yield per-ticker context keys.

    Reads ``state["tickers"]`` and ``state["as_of"]``; writes
    ``temp:news_data`` and one ``temp:news_context_<TICKER>`` per ticker
    via a single ``state_delta`` event.

    The agent is idempotent for a given ``(tickers, as_of)`` input — re-
    running yields identical keys (subject to provider determinism).
    """

    async def _run_async_impl(
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Fetch and yield in one event.

        Args:
            ctx: ADK invocation context.

        Yields:
            One Event whose ``state_delta`` carries the machine-readable
            news dict and one per-ticker formatted context block.
        """
        state = ctx.session.state
        tickers: list[str] = state.get("tickers", []) or []

        # Historical clock (backtest) or wall-clock (live).
        as_of: datetime = resolve_as_of(
            state.get("as_of"), allow_wallclock=True, site="news/fetch_agent",
        )

        news_data: dict[str, dict] = {}
        per_ticker_blocks: dict[str, str] = {}

        for ticker in tickers:
            try:
                articles = await get_stock_news(ticker, as_of=as_of)
            except Exception as exc:  # noqa: BLE001 — degrade gracefully per ticker
                _LOGGER.warning("news fetch failed for %s: %s", ticker, exc)
                articles = []

            # Serialise so downstream consumers always see plain dicts.
            serialised = [
                a.model_dump() if hasattr(a, "model_dump") else a for a in articles
            ]
            news_data[ticker] = {"news": serialised}

            # Per-ticker formatted block — ADK's instruction template fills
            # this into the per-ticker LlmAgent's {news_context} placeholder.
            per_ticker_blocks[ticker] = _build_ticker_news_context(ticker, serialised)

        # Build the state_delta payload.  All keys are temp:-prefixed
        # (Rule 2) so ADK strips them at the invocation boundary.
        delta: dict[str, object] = {"temp:news_data": news_data}
        for ticker, block in per_ticker_blocks.items():
            delta[f"temp:news_context_{ticker}"] = block

        # Retain the plain ``news_context`` key — multi-ticker joined
        # block — for trace/debug surfaces (per Phase 9 spec §1).  Each
        # per-ticker LlmAgent reads its OWN ``temp:news_context_<TICKER>``;
        # this aggregate is only for human-readable traces.
        delta["temp:news_context"] = "\n\n".join(
            f"=== {t} ===\n{per_ticker_blocks[t]}" for t in tickers
        )

        # Surface trace — no-op unless state["_trace"] is set.
        _trace_maybe(state, "01_fetch_news", news_data)

        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            actions=EventActions(state_delta=delta),
        )
```

Also add a test assertion confirming the aggregate key is written:

```python
@pytest.mark.asyncio
async def test_fetch_writes_aggregate_news_context_for_trace():
    """The aggregate ``temp:news_context`` key (multi-ticker joined block) is
    retained for trace/debug surfaces — see Phase 9 spec §1.
    """

    async def _mock(ticker, as_of=None):
        return [{"title": f"{ticker} hed", "summary": "body", "published_at": "2026-05-21"}]

    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test",
        state={"tickers": ["AAPL", "MSFT"], "as_of": datetime(2026, 5, 21)},
        session_id="t1",
    )
    agent = NewsFetchAgent(name="NewsFetch")
    ctx = InvocationContext(session_service=svc, session=session,
                            invocation_id="inv-1", agent=agent)

    with patch("agents.analysts.news.fetch_agent.get_stock_news", _mock):
        events = [ev async for ev in agent.run_async(ctx)]

    sd = events[0].actions.state_delta
    assert "temp:news_context" in sd
    # Both ticker headers appear in the joined block.
    assert "=== AAPL ===" in sd["temp:news_context"]
    assert "=== MSFT ===" in sd["temp:news_context"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/analysts/news/test_fetch_agent.py -v`
Expected: PASS — both tests green.

- [ ] **Step 5: Commit**

```bash
git add src/agents/analysts/news/fetch_agent.py tests/analysts/news/test_fetch_agent.py
git commit -m "feat(news): NewsFetchAgent — per-ticker context BaseAgent (Phase 9)"
```

---

## Task 5: `FundamentalFetchAgent` (BaseAgent)

**Files:**
- Create: `src/agents/analysts/fundamental/fetch_agent.py`
- Test: `tests/analysts/fundamental/test_fetch_agent.py`

- [ ] **Step 1: Read the existing fundamental fetch callback**

Run: `PYTHONPATH=src .venv/bin/python -c "import inspect, agents.analysts.fundamental.fetch as m; print(inspect.getsource(m))"`

Note the providers it calls (ratios, filings, insider trades, etc.), the state keys it writes (`temp:fundamental_data`, `fundamental_context`), and any helpers (e.g. a per-ticker formatter analogous to `_build_ticker_news_context`).

- [ ] **Step 2: Write the failing tests**

Mirror `tests/analysts/news/test_fetch_agent.py` but for the fundamental data shape. Concretely:

```python
# tests/analysts/fundamental/test_fetch_agent.py
"""Unit tests for FundamentalFetchAgent — one temp:fundamental_context_<TICKER>
key per watchlist ticker; degrades gracefully on provider errors."""

# Pattern matches tests/analysts/news/test_fetch_agent.py exactly, with:
# - patch targets pointing at `agents.analysts.fundamental.fetch_agent.<provider>`
# - state-delta keys checked for "temp:fundamental_data" and
#   "temp:fundamental_context_<TICKER>"
# - sample provider responses shaped to match the existing
#   `agents.analysts.fundamental.fetch` callbacks (ratios + filings + insider)
```

> **Note:** at implementation time, fill in the exact patch targets and the per-ticker block format by reading the existing `agents/analysts/fundamental/fetch.py` and following its provider-call structure exactly.

- [ ] **Step 3: Implement `FundamentalFetchAgent`**

Mirror the `NewsFetchAgent` pattern. Skeleton:

```python
# src/agents/analysts/fundamental/fetch_agent.py
"""FundamentalFetchAgent — BaseAgent variant of fundamental_fetch_callback.

See agents/analysts/news/fetch_agent.py for the design rationale (Phase 9).
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from agents.analysts.fundamental.fetch import (
    _build_ticker_fundamental_context,  # if present in existing module — else lift the formatter here
    # ... provider imports
)
from data.timeguard import resolve_as_of
from observability.trace import _trace_maybe


class FundamentalFetchAgent(BaseAgent):
    """Fetch ratios + filings + insider trades for every watchlist ticker."""

    async def _run_async_impl(
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Fetch and yield in one event — see module docstring."""

        state = ctx.session.state
        tickers: list[str] = state.get("tickers", []) or []
        as_of: datetime = resolve_as_of(
            state.get("as_of"), allow_wallclock=True, site="fundamental/fetch_agent",
        )

        fundamental_data: dict[str, dict] = {}
        per_ticker_blocks: dict[str, str] = {}

        for ticker in tickers:
            # Build the per-ticker dict by calling each provider with
            # the same defensive try/except the legacy callback uses.
            # (Copy the per-ticker fetch loop from the old callback verbatim.)
            ...  # implementation lifted from fundamental/fetch.py

            per_ticker_blocks[ticker] = _build_ticker_fundamental_context(
                ticker, fundamental_data[ticker],
            )

        delta: dict[str, object] = {"temp:fundamental_data": fundamental_data}
        for ticker, block in per_ticker_blocks.items():
            delta[f"temp:fundamental_context_{ticker}"] = block

        # Retain the aggregate ``temp:fundamental_context`` key — multi-
        # ticker joined block — for trace/debug surfaces (per Phase 9 spec
        # §1).  The per-ticker LlmAgents read their OWN
        # ``temp:fundamental_context_<TICKER>`` keys; this aggregate is
        # only for human-readable traces.
        delta["temp:fundamental_context"] = "\n\n".join(
            f"=== {t} ===\n{per_ticker_blocks[t]}" for t in tickers
        )

        _trace_maybe(state, "01_fetch_fundamental", fundamental_data)

        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            actions=EventActions(state_delta=delta),
        )
```

If `_build_ticker_fundamental_context` does not already exist in `agents/analysts/fundamental/fetch.py`, extract it from the existing context-build loop (look for the block that today produces `fundamental_context` as a multi-ticker concatenation) — refactor it into a per-ticker helper first, then call it once per ticker from the new agent.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/analysts/fundamental/test_fetch_agent.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/analysts/fundamental/fetch_agent.py src/agents/analysts/fundamental/fetch.py tests/analysts/fundamental/test_fetch_agent.py
git commit -m "feat(fundamental): FundamentalFetchAgent — per-ticker context BaseAgent (Phase 9)"
```

---

## Task 6: Per-ticker cache callbacks

**Files:**
- Modify: `src/agents/analysts/cache_callbacks.py`
- Test: `tests/analysts/test_cache_callbacks_per_ticker.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/analysts/test_cache_callbacks_per_ticker.py
"""Per-ticker cache callback contract — one ticker per LlmAgent, one verdict per response."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from agents.analysts.cache_callbacks import make_report_cache_callbacks
from contract.evidence import TickerVerdict


def test_before_single_ticker_hit_returns_single_verdict_llm_response(tmp_path, monkeypatch):
    """A cache hit on the single bound ticker returns an LlmResponse whose
    text is a valid TickerVerdict JSON (NOT a VerdictBatch wrapper)."""

    # Arrange: pre-populate the cache for ticker AAPL.
    from agents.analysts.report_cache import write_cache

    write_cache(
        tmp_path, "news", "AAPL",
        input_hash="hash-1",
        prompt_version="2026-05-21-a",
        verdict={
            "ticker":      "AAPL",
            "lean":        "bullish",
            "magnitude":   0.7,
            "confidence":  0.8,
            "rationale":   "Strong quarter",
            "key_factors": ["catalyst:earnings", "direction:positive"],
            "is_no_data":  False,
        },
        report=None,
        originating_as_of="2026-05-21T14:00",
    )

    # Build the per-ticker callbacks with cache directory pointed at tmp_path.
    monkeypatch.setattr(
        "agents.analysts.cache_callbacks.get_analysts_config",
        lambda: MagicMock(cache=MagicMock(enabled=True, directory=str(tmp_path))),
    )

    before_cb, _after_cb = make_report_cache_callbacks(
        analyst_name       = "news",
        prompt_version     = "2026-05-21-a",
        data_state_key     = "temp:news_data",
        verdicts_state_key = "temp:news_verdict_AAPL",
        ticker             = "AAPL",
        output_schema      = TickerVerdict,
        hash_inputs        = lambda d: "hash-1",
        trace_label        = None,
    )

    state = {
        "tickers":       ["AAPL"],
        "temp:news_data": {"AAPL": {"news": []}},
    }
    callback_context = MagicMock(state=state)

    response = before_cb(callback_context, MagicMock())

    # Result must be a valid LlmResponse whose text parses as a TickerVerdict.
    assert response is not None
    text    = response.content.parts[0].text
    parsed  = json.loads(text)
    verdict = TickerVerdict.model_validate(parsed)
    assert verdict.ticker == "AAPL"
    assert verdict.lean   == "bullish"


def test_before_single_ticker_miss_returns_none(tmp_path, monkeypatch):
    """A cache miss returns None — the LLM runs."""

    monkeypatch.setattr(
        "agents.analysts.cache_callbacks.get_analysts_config",
        lambda: MagicMock(cache=MagicMock(enabled=True, directory=str(tmp_path))),
    )

    before_cb, _ = make_report_cache_callbacks(
        analyst_name       = "news",
        prompt_version     = "2026-05-21-a",
        data_state_key     = "temp:news_data",
        verdicts_state_key = "temp:news_verdict_AAPL",
        ticker             = "AAPL",
        output_schema      = TickerVerdict,
        hash_inputs        = lambda d: "hash-novel",
        trace_label        = None,
    )

    state = {"temp:news_data": {"AAPL": {"news": []}}}
    callback_context = MagicMock(state=state)

    assert before_cb(callback_context, MagicMock()) is None


def test_after_writes_single_verdict_to_cache(tmp_path, monkeypatch):
    """The after-callback parses a single-verdict LLM response and writes one
    cache entry — NOT a {verdicts: [...]} wrapper."""

    monkeypatch.setattr(
        "agents.analysts.cache_callbacks.get_analysts_config",
        lambda: MagicMock(cache=MagicMock(enabled=True, directory=str(tmp_path))),
    )

    _before, after_cb = make_report_cache_callbacks(
        analyst_name       = "news",
        prompt_version     = "2026-05-21-a",
        data_state_key     = "temp:news_data",
        verdicts_state_key = "temp:news_verdict_AAPL",
        ticker             = "AAPL",
        output_schema      = TickerVerdict,
        hash_inputs        = lambda d: "hash-fresh",
        trace_label        = None,
    )

    # Synthetic LLM response — a single TickerVerdict JSON, not a batch.
    fake_text = json.dumps({
        "ticker": "AAPL", "lean": "bearish", "magnitude": 0.4,
        "confidence": 0.6, "rationale": "Macro headwinds",
        "key_factors": ["catalyst:macro"], "is_no_data": False,
    })
    llm_response = MagicMock()
    llm_response.content.parts = [MagicMock(text=fake_text)]

    state = {"temp:news_data": {"AAPL": {"news": []}}, "as_of": "2026-05-21T14:00"}
    callback_context = MagicMock(state=state)

    result = after_cb(callback_context, llm_response)

    # Returns None (no short-circuit), but a cache entry now exists for AAPL.
    assert result is None

    from agents.analysts.report_cache import read_cache

    hit = read_cache(tmp_path, "news", "AAPL",
                     input_hash="hash-fresh", prompt_version="2026-05-21-a")
    assert hit is not None
    assert hit["verdict"]["lean"] == "bearish"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/analysts/test_cache_callbacks_per_ticker.py -v`
Expected: FAIL — `make_report_cache_callbacks` does not accept `ticker` or `output_schema` kwargs.

- [ ] **Step 3: Rewrite `make_report_cache_callbacks` for per-ticker shape**

Edit `src/agents/analysts/cache_callbacks.py`. Replace the function signature and the `_before` / `_after` bodies (keep the docstring up-to-date):

```python
def make_report_cache_callbacks(
    *,
    analyst_name: str,
    prompt_version: str,
    data_state_key: str,
    verdicts_state_key: str,
    ticker: str,
    output_schema: type,
    hash_inputs,
    trace_label: str | None = None,
):
    """Build ``(before_model_callback, after_model_callback)`` for a single-ticker cache-aware LlmAgent.

    Changed in Phase 9: each LlmAgent is bound to ONE ticker.  Both hooks
    therefore look up a single per-ticker cache entry rather than iterating
    every watchlist ticker.  ``output_schema`` is the Pydantic model used
    to validate the cached payload and to shape the synthetic LlmResponse
    text — typically ``TickerVerdict`` for the per-ticker News and
    Fundamental analysts.

    See module docstring for the broader cache lifecycle.

    Parameters
    ----------
    ticker:
        The single ticker this callback pair is bound to.  Set at LlmAgent
        construction time by the per-ticker factory.
    output_schema:
        Pydantic model class that the synthetic ``LlmResponse`` text must
        validate against — must match the agent's ``output_schema`` so
        ADK's ``__maybe_save_output_to_state`` parses cleanly.
    (other parameters unchanged)
    """
    cfg  = get_analysts_config().cache
    root = Path(cfg.directory)

    def _before(callback_context, llm_request):
        """Short-circuit if this single ticker's cache hits."""

        if not cfg.enabled:
            return None

        state = callback_context.state
        data: dict = state.get(data_state_key, {}) or {}
        per_ticker = data.get(ticker, {}) or {}
        input_hash = hash_inputs(per_ticker)

        hit = read_cache(
            root, analyst_name, ticker,
            input_hash=input_hash,
            prompt_version=prompt_version,
        )

        if hit is None:
            _log.info(
                "report_cache_miss",
                extra={
                    "analyst":        analyst_name,
                    "ticker":         ticker,
                    "input_hash":     input_hash,
                    "prompt_version": prompt_version,
                    "kind":           "report_cache_miss",
                },
            )
            return None

        # Audit telemetry — records which tick the cached verdict originated from.
        log_cache_hit_to_state(
            state,
            analyst=analyst_name,
            ticker=ticker,
            input_hash=input_hash,
            originating_as_of=hit.get("originating_as_of"),
        )

        _log.info(
            "report_cache_hit",
            extra={
                "analyst":           analyst_name,
                "ticker":            ticker,
                "input_hash":        input_hash,
                "originating_as_of": hit.get("originating_as_of"),
                "prompt_version":    prompt_version,
                "kind":              "report_cache_hit",
            },
        )

        # Merge report blob into verdict if one was stored.
        v = hit["verdict"]
        if hit["report"] is not None:
            v = {**v, "report": hit["report"]}
        v = {**v, "ticker": ticker}

        # Validate against the per-ticker schema — same shape ADK's
        # __maybe_save_output_to_state will expect from the response text.
        validated = output_schema.model_validate(v)
        verdict_json = validated.model_dump_json()

        # Write to state so any downstream consumer that reads
        # state[verdicts_state_key] sees the populated value.  Note: ADK's
        # __maybe_save_output_to_state will also write the same JSON to the
        # agent's output_key — kept as defence-in-depth.
        state[verdicts_state_key] = validated.model_dump()

        if trace_label is not None:
            try:
                tw = state.get("_trace")
            except (AttributeError, TypeError):
                tw = None
            if isinstance(tw, TraceWriter):
                tw.llm_pair(
                    trace_label,
                    prompt=f"(cache hit — {ticker}, prompt_version={prompt_version})",
                    response=f"(loaded from cache/reports/{analyst_name}/{ticker}.json)",
                    model="cache",
                )

        return LlmResponse(
            content=genai_types.Content(
                parts=[genai_types.Part.from_text(text=verdict_json)]
            )
        )

    def _after(callback_context, llm_response):
        """Persist the fresh single-ticker verdict to the cache."""

        if not cfg.enabled:
            return None

        state = callback_context.state
        data: dict = state.get(data_state_key, {}) or {}

        try:
            text    = llm_response.content.parts[0].text
            payload = json.loads(text)
        except (AttributeError, IndexError, TypeError, json.JSONDecodeError):
            _log.warning(
                "%s cache: could not parse LLM response — cache write skipped for %s.",
                analyst_name, ticker,
            )
            return None

        # Payload is a single TickerVerdict (NOT {verdicts: [...]}).
        v_dict = payload if isinstance(payload, dict) else {}

        per_ticker = data.get(ticker, {}) or {}
        input_hash = hash_inputs(per_ticker)

        verdict_payload = {k: val for k, val in v_dict.items() if k != "report"}
        report_payload  = v_dict.get("report")

        try:
            write_cache(
                root, analyst_name, ticker,
                input_hash=input_hash,
                prompt_version=prompt_version,
                verdict=verdict_payload,
                report=report_payload,
                originating_as_of=state.get("as_of"),
            )
        except OSError:
            _log.warning(
                "%s cache write failed for ticker %s — disk error.",
                analyst_name, ticker, exc_info=True,
            )

        return None

    return _before, _after
```

Remove the `from contract.evidence import VerdictBatch` import (no longer used) — replace with no import (the caller passes `output_schema`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/analysts/test_cache_callbacks_per_ticker.py -v`
Expected: PASS — all three tests green.

- [ ] **Step 5: Remove obsolete batched-cache tests**

Run: `grep -rln "make_report_cache_callbacks\|test_before_full_hit" tests/`. For every existing test that asserts the old batched behaviour (iterates all watchlist tickers; expects `VerdictBatch` JSON; expects "any single miss forces full LLM call"), delete it — those invariants no longer hold. Keep tests that exercise the new per-ticker contract.

- [ ] **Step 6: Commit**

```bash
git add src/agents/analysts/cache_callbacks.py tests/
git commit -m "refactor(analysts/cache): per-ticker cache callbacks; TickerVerdict shape"
```

---

## Task 7: Per-ticker News branch factory

**Files:**
- Create: `src/agents/analysts/news/per_ticker.py`
- Test: `tests/analysts/test_per_ticker_branch.py` (News portion)

- [ ] **Step 1: Write the failing test (News only)**

```python
# tests/analysts/test_per_ticker_branch.py
"""Per-ticker branch factory tests — News + Fundamental.

The factory must produce:
  IsolatedFailureWrapper[RetryingAgentWrapper[LlmAgent]]
with:
  - output_schema=TickerVerdict
  - output_key="temp:news_verdict_<TICKER>"
  - instruction containing the ticker substituted in
  - no after_agent_callback (evidence-build moves to the joiner)
"""
from __future__ import annotations

from agents.analysts.heuristics import NewsVocabulary
from agents.analysts.isolated_failure import IsolatedFailureWrapper  # placeholder import path — see below
from agents.analysts.news.per_ticker import build_news_branch_for_ticker
from agents.llm_retry import RetryingAgentWrapper
from contract.evidence import TickerVerdict
from google.adk.agents import LlmAgent


def _news_vocab() -> NewsVocabulary:
    return NewsVocabulary(
        catalysts=["earnings", "guidance"],
        novelty=["new", "ongoing"],
        direction=["positive", "negative", "mixed", "none"],
    )


def test_news_branch_is_isolated_wrapping_retrying_wrapping_llm():
    """The wrapper composition is exact: IsolatedFailureWrapper(Retrying(LlmAgent))."""

    from agents.isolated_failure import IsolatedFailureWrapper  # actual import path

    branch = build_news_branch_for_ticker("AAPL", _news_vocab())

    assert isinstance(branch, IsolatedFailureWrapper)
    assert isinstance(branch.inner, RetryingAgentWrapper)
    assert isinstance(branch.inner.inner, LlmAgent)


def test_news_branch_output_schema_and_key():
    """output_schema is TickerVerdict; output_key is temp:news_verdict_<TICKER>."""

    branch = build_news_branch_for_ticker("AAPL", _news_vocab())
    llm = branch.inner.inner

    assert llm.output_schema is TickerVerdict
    assert llm.output_key   == "temp:news_verdict_AAPL"


def test_news_branch_has_no_after_agent_callback():
    """The per-ticker LlmAgent must not own evidence-build — that moved to the joiner."""

    branch = build_news_branch_for_ticker("AAPL", _news_vocab())
    llm = branch.inner.inner

    assert llm.after_agent_callback is None
    # And no before_agent_callback either — fetch lives in NewsFetchAgent.
    assert llm.before_agent_callback is None


def test_news_branch_instruction_pins_ticker():
    """The rendered instruction must reference the specific ticker, not a placeholder."""

    branch = build_news_branch_for_ticker("AAPL", _news_vocab())
    llm = branch.inner.inner

    # {ticker} must be substituted at construction time; only ADK's
    # {news_context} runtime placeholder remains as a single-brace token.
    assert "{ticker}" not in llm.instruction
    assert "AAPL" in llm.instruction
    assert "{news_context}" in llm.instruction
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/analysts/test_per_ticker_branch.py -v -k news`
Expected: FAIL (`ModuleNotFoundError: agents.analysts.news.per_ticker`).

- [ ] **Step 3: Implement `build_news_branch_for_ticker`**

```python
# src/agents/analysts/news/per_ticker.py
"""Per-ticker News branch factory (Phase 9).

Constructs one IsolatedFailureWrapper(RetryingAgentWrapper(LlmAgent))
bound to a single ticker.  The LlmAgent's instruction has {ticker}
substituted at build time so each branch's prompt mentions only its
own ticker.  The {news_context} placeholder remains for ADK's
inject_session_state to fill from temp:news_context_<TICKER> at run
time — see ``NewsFetchAgent`` for the writer side.
"""
from __future__ import annotations

import os

from google.adk.agents import LlmAgent

from agents.analysts._common import _chain_after, _chain_before
from agents.analysts.cache_callbacks import make_report_cache_callbacks
from agents.analysts.heuristics import NewsVocabulary
from agents.analysts.news.prompts import build_news_instruction
from agents.analysts.report_cache import (
    NEWS_PROMPT_VERSION,
    news_hash_inputs,
)
from agents.isolated_failure import IsolatedFailureWrapper
from agents.llm_retry import RetryingAgentWrapper
from config.models import get_models_config
from contract.evidence import TickerVerdict
from observability.trace import make_llm_trace_callbacks


def build_news_branch_for_ticker(
    ticker: str,
    vocab: NewsVocabulary,
) -> IsolatedFailureWrapper:
    """Build a single-ticker News branch.

    Args:
        ticker: The ticker symbol this branch is bound to (e.g. "AAPL").
        vocab:  Validated NewsVocabulary holding closed-vocab tag lists.

    Returns:
        IsolatedFailureWrapper wrapping a RetryingAgentWrapper wrapping
        an LlmAgent.  The wrappers' names embed ``ticker`` so traces and
        logs identify each branch.

    The returned agent emits exactly one TickerVerdict, written to
    ``state["temp:news_verdict_<TICKER>"]`` via ADK's output_key
    mechanism.  No after_agent_callback — evidence-build is the
    joiner's responsibility (see ``NewsJoinerAgent``).
    """
    # Render the instruction template, then substitute the ticker at build
    # time.  Only {news_context} remains as a single-brace placeholder for
    # ADK to fill from temp:news_context_<TICKER> at run time.
    base_instruction = build_news_instruction(vocab)
    instruction      = base_instruction.replace("{ticker}", ticker)

    model = get_models_config().news_analyst

    # Cache callbacks — per-ticker signature.  The lambda passes the
    # per-ticker data slice straight to the hash function.
    cache_before, cache_after = make_report_cache_callbacks(
        analyst_name       = "news",
        prompt_version     = NEWS_PROMPT_VERSION,
        data_state_key     = "temp:news_data",
        verdicts_state_key = f"temp:news_verdict_{ticker}",
        ticker             = ticker,
        output_schema      = TickerVerdict,
        hash_inputs        = lambda d: news_hash_inputs((d or {}).get("news") or []),
        trace_label        = f"03_news_llm_{ticker}",
    )

    trace_before = None
    trace_after  = None
    if os.environ.get("STOCKBOT_TRACE") == "1":
        trace_before, trace_after = make_llm_trace_callbacks(
            f"03_news_llm_{ticker}", model=model,
        )

    before_cb = _chain_before(cache_before, trace_before)
    after_cb  = _chain_after(cache_after, trace_after)

    llm = LlmAgent(
        name=f"NewsAnalyst_{ticker}",
        model=model,
        instruction=instruction,
        output_schema=TickerVerdict,
        output_key=f"temp:news_verdict_{ticker}",
        # No before_agent_callback — NewsFetchAgent runs once per tick before
        # any per-ticker branch.
        # No after_agent_callback — NewsJoinerAgent builds news_verdicts and
        # news_evidence from the per-ticker keys.
        before_model_callback=before_cb,
        after_model_callback=after_cb,
    )

    retrying = RetryingAgentWrapper(
        name  = f"NewsAnalyst_{ticker}_retrying",
        inner = llm,
    )

    return IsolatedFailureWrapper(
        name    = f"NewsAnalyst_{ticker}_isolated",
        inner   = retrying,
        analyst = "news",
        ticker  = ticker,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/analysts/test_per_ticker_branch.py -v -k news`
Expected: PASS — all four tests green.

- [ ] **Step 5: Commit**

```bash
git add src/agents/analysts/news/per_ticker.py tests/analysts/test_per_ticker_branch.py
git commit -m "feat(news): per-ticker branch factory — IsolatedFailureWrapper(Retrying(LlmAgent))"
```

---

## Task 8: Per-ticker Fundamental branch factory

**Files:**
- Create: `src/agents/analysts/fundamental/per_ticker.py`
- Test: extend `tests/analysts/test_per_ticker_branch.py` with the Fundamental cases

- [ ] **Step 1: Write the failing tests (Fundamental cases)**

Append to `tests/analysts/test_per_ticker_branch.py`:

```python
def _fundamental_vocab():
    """Populate at implementation time from agents/analysts/heuristics.py."""
    raise NotImplementedError


def test_fundamental_branch_composition():
    """Same wrapper composition as News, with fundamental-specific output key."""

    from agents.analysts.fundamental.per_ticker import build_fundamental_branch_for_ticker
    from agents.isolated_failure import IsolatedFailureWrapper
    from agents.llm_retry import RetryingAgentWrapper
    from contract.evidence import TickerVerdict
    from google.adk.agents import LlmAgent

    branch = build_fundamental_branch_for_ticker("AAPL", _fundamental_vocab())

    assert isinstance(branch, IsolatedFailureWrapper)
    assert isinstance(branch.inner, RetryingAgentWrapper)

    llm = branch.inner.inner
    assert isinstance(llm, LlmAgent)
    assert llm.output_schema is TickerVerdict
    assert llm.output_key   == "temp:fundamental_verdict_AAPL"
    assert llm.before_agent_callback is None
    assert llm.after_agent_callback  is None
    assert "{ticker}" not in llm.instruction
    assert "AAPL" in llm.instruction
    assert "{fundamental_context}" in llm.instruction
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/analysts/test_per_ticker_branch.py -v -k fundamental`
Expected: FAIL.

- [ ] **Step 3: Implement `build_fundamental_branch_for_ticker`**

Mirror Task 7's implementation in `src/agents/analysts/fundamental/per_ticker.py`. Swap:
- `news` → `fundamental`
- `NEWS_PROMPT_VERSION` → `FUNDAMENTAL_PROMPT_VERSION`
- `news_hash_inputs` → `_fundamental_hash_inputs_from_dict` (currently a private helper in `fundamental/agent.py` — promote it to `fundamental/report_cache.py` or import it directly)
- `build_news_instruction` → `build_fundamental_instruction`
- output_key pattern → `temp:fundamental_verdict_{ticker}`
- trace section → `04_fundamental_llm_{ticker}`
- `news_analyst` model → `fundamental_analyst` model

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/analysts/test_per_ticker_branch.py -v`
Expected: PASS — both News and Fundamental cases green.

- [ ] **Step 5: Commit**

```bash
git add src/agents/analysts/fundamental/per_ticker.py tests/analysts/test_per_ticker_branch.py
git commit -m "feat(fundamental): per-ticker branch factory — symmetric with News"
```

---

## Task 9: `NewsJoinerAgent`

**Files:**
- Create: `src/agents/analysts/news/joiner.py`
- Test: `tests/analysts/news/test_joiner.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/analysts/news/test_joiner.py
"""NewsJoinerAgent — reads N temp:news_verdict_<TICKER> keys; builds
news_verdicts + news_evidence; synthesises no-data for missing keys."""
from __future__ import annotations

import pytest
from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions import InMemorySessionService

from agents.analysts.news.joiner import NewsJoinerAgent


@pytest.mark.asyncio
async def test_joiner_builds_canonical_keys_from_per_ticker_state():
    """news_verdicts + news_evidence land via one state_delta event."""

    state = {
        "tickers":  ["AAPL", "MSFT"],
        "tick_id":  "t-1",
        "as_of":    "2026-05-21T14:00",
        "temp:news_data": {
            "AAPL": {"news": [{"title": "AAPL beats", "summary": "Q3 strong"}]},
            "MSFT": {"news": [{"title": "MSFT guides up", "summary": "Cloud growth"}]},
        },
        "temp:news_verdict_AAPL": {
            "ticker": "AAPL", "lean": "bullish", "magnitude": 0.7,
            "confidence": 0.8, "rationale": "Earnings beat",
            "key_factors": ["catalyst:earnings"], "is_no_data": False,
        },
        "temp:news_verdict_MSFT": {
            "ticker": "MSFT", "lean": "bullish", "magnitude": 0.5,
            "confidence": 0.6, "rationale": "Guidance up",
            "key_factors": ["catalyst:guidance"], "is_no_data": False,
        },
    }

    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test", state=state, session_id="t1",
    )

    agent = NewsJoinerAgent(name="NewsJoiner")
    ctx = InvocationContext(
        session_service=svc, session=session, invocation_id="inv-1", agent=agent,
    )

    events = [ev async for ev in agent.run_async(ctx)]

    assert len(events) == 1
    delta = events[0].actions.state_delta

    # news_verdicts is a VerdictBatch dict.
    assert "news_verdicts" in delta
    assert "verdicts" in delta["news_verdicts"]
    verdict_tickers = {v["ticker"] for v in delta["news_verdicts"]["verdicts"]}
    assert verdict_tickers == {"AAPL", "MSFT"}

    # news_evidence is a list of AnalystEvidence dumps, one row per ticker.
    assert "news_evidence" in delta
    ev_tickers = {row["ticker"] for row in delta["news_evidence"]}
    assert ev_tickers == {"AAPL", "MSFT"}


@pytest.mark.asyncio
async def test_joiner_synthesises_no_data_for_missing_key():
    """A missing temp:news_verdict_<TICKER> → synthetic no-data verdict in both outputs."""

    state = {
        "tickers":  ["AAPL", "MSFT"],
        "tick_id":  "t-1",
        "as_of":    "2026-05-21T14:00",
        "temp:news_data": {
            "AAPL": {"news": [{"title": "AAPL beats"}]},
            "MSFT": {"news": []},
        },
        # MSFT key absent — simulates a failed branch.
        "temp:news_verdict_AAPL": {
            "ticker": "AAPL", "lean": "bullish", "magnitude": 0.7,
            "confidence": 0.8, "rationale": "ok", "key_factors": [],
            "is_no_data": False,
        },
    }

    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test", state=state, session_id="t1",
    )

    agent = NewsJoinerAgent(name="NewsJoiner")
    ctx = InvocationContext(
        session_service=svc, session=session, invocation_id="inv-1", agent=agent,
    )

    events = [ev async for ev in agent.run_async(ctx)]
    delta = events[0].actions.state_delta

    # MSFT appears as no-data in both outputs.
    msft_verdict = next(
        v for v in delta["news_verdicts"]["verdicts"] if v["ticker"] == "MSFT"
    )
    assert msft_verdict["is_no_data"] is True
    assert msft_verdict["lean"]       == "neutral"

    msft_ev = next(row for row in delta["news_evidence"] if row["ticker"] == "MSFT")
    assert msft_ev["verdict"]["is_no_data"] is True


@pytest.mark.asyncio
async def test_joiner_output_consumable_by_strategist_index_evidence():
    """The joiner's `news_evidence` list must round-trip through
    Strategist's ``_index_evidence`` without shape errors.  This locks in the
    contract verified at plan-time (``context_shim._index_evidence`` accepts
    either a raw ``dict`` or a validated ``AnalystEvidence``).  If a future
    edit changes ``ev.model_dump(mode="json")`` to a non-dict payload, this
    test catches it before the strategist crashes mid-tick.
    """

    from agents.strategist.context_shim import _index_evidence

    state = {
        "tickers": ["AAPL"],
        "tick_id": "t-1",
        "as_of":   "2026-05-21T14:00",
        "temp:news_data": {"AAPL": {"news": [{"title": "AAPL beats"}]}},
        "temp:news_verdict_AAPL": {
            "ticker": "AAPL", "lean": "bullish", "magnitude": 0.7,
            "confidence": 0.8, "rationale": "ok", "key_factors": [],
            "is_no_data": False,
        },
    }

    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test", state=state, session_id="t1",
    )
    agent = NewsJoinerAgent(name="NewsJoiner")
    ctx = InvocationContext(
        session_service=svc, session=session, invocation_id="inv-1", agent=agent,
    )

    delta = (await agent.run_async(ctx).__anext__()).actions.state_delta

    # Simulate Strategist downstream consumption.
    indexed = _index_evidence({"news_evidence": delta["news_evidence"]}, "news_evidence")
    assert "AAPL" in indexed
    assert indexed["AAPL"].ticker == "AAPL"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/analysts/news/test_joiner.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `NewsJoinerAgent`**

```python
# src/agents/analysts/news/joiner.py
"""NewsJoinerAgent — consolidates per-ticker News verdicts into the canonical
contract keys (Phase 9).

Reads (per watchlist ticker):
  - temp:news_verdict_<TICKER>  — TickerVerdict dict, or absent if the branch failed
  - temp:news_data              — raw per-ticker news dict (extractor input)
  - tickers, tick_id, as_of     — pipeline context

Yields one state_delta event carrying:
  - news_verdicts  — VerdictBatch dict (the §A contract key)
  - news_evidence  — list[AnalystEvidence] dumps
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from contract.evidence import AnalystEvidence, AnalystVerdict, TickerVerdict, VerdictBatch
from contract.extractors.news import extract_news_features
from data.timeguard import resolve_as_of
from observability.trace import _trace_maybe


class NewsJoinerAgent(BaseAgent):
    """Build news_verdicts + news_evidence from per-ticker working keys."""

    async def _run_async_impl(
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Read N temp:news_verdict_<TICKER> keys; emit canonical contract keys.

        For each watchlist ticker:
          1. Look up ``temp:news_verdict_<TICKER>``.  If absent (branch
             failed), synthesise a no-data ``AnalystVerdict``.
          2. Run ``extract_news_features`` on the per-ticker raw data slice.
          3. Wrap (verdict, features) in an ``AnalystEvidence`` record.

        Build a ``VerdictBatch`` of all ``TickerVerdict`` rows and yield
        both canonical keys via one ``state_delta`` event.
        """
        state    = ctx.session.state
        tickers: list[str] = state.get("tickers", []) or []
        tick_id: str       = state.get("tick_id", "unknown")
        data:    dict      = state.get("temp:news_data", {}) or {}

        recorded_at: datetime = resolve_as_of(
            state.get("as_of"), allow_wallclock=True, site="news/joiner",
        )

        # Snapshot session state for extractors that read pipeline context.
        _to_dict = getattr(state, "to_dict", None)
        state_snapshot: dict = _to_dict() if callable(_to_dict) else dict(state)

        verdicts_list: list[TickerVerdict] = []
        evidence_list: list[dict]          = []

        for ticker in tickers:
            raw_v = state.get(f"temp:news_verdict_{ticker}")

            if raw_v is None:
                # Branch failed (or LlmAgent omitted output) — synthesise.
                verdict = AnalystVerdict(
                    lean        = "neutral",
                    magnitude   = 0.0,
                    confidence  = 0.0,
                    rationale   = "no verdict from LLM",
                    key_factors = [],
                    is_no_data  = True,
                )
                ticker_verdict = TickerVerdict(ticker=ticker, **verdict.model_dump())
            else:
                # Validate against the strict schema.  ADK's output_schema
                # already validated once on write, but re-validate here so
                # downstream consumers can rely on the shape unconditionally.
                ticker_verdict = TickerVerdict.model_validate({**raw_v, "ticker": ticker})
                verdict = AnalystVerdict.model_validate(
                    {k: v for k, v in ticker_verdict.model_dump().items() if k != "ticker"}
                )

            verdicts_list.append(ticker_verdict)

            # Deterministic feature extractor — operates on the per-ticker slice.
            raw_slice = data.get(ticker, {}) or {}
            features  = extract_news_features(
                raw_slice, ticker,
                as_of=recorded_at,
                state=state_snapshot,
            )

            ev = AnalystEvidence(
                analyst        = "news",
                ticker         = ticker,
                tick_id        = tick_id,
                recorded_at    = recorded_at,
                verdict        = verdict,
                features       = features,
                feature_warnings = [],
            )
            evidence_list.append(ev.model_dump(mode="json"))

        batch = VerdictBatch(verdicts=verdicts_list)

        # Surface trace — mirrors the old YieldingAnalystWrapper.trace_key behaviour.
        _trace_maybe(state, "02_news_verdict", [v.model_dump() for v in verdicts_list])

        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            actions=EventActions(state_delta={
                "news_verdicts": batch.model_dump(),
                "news_evidence": evidence_list,
            }),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/analysts/news/test_joiner.py -v`
Expected: PASS — both tests green.

- [ ] **Step 5: Commit**

```bash
git add src/agents/analysts/news/joiner.py tests/analysts/news/test_joiner.py
git commit -m "feat(news): NewsJoinerAgent — consolidate per-ticker verdicts into canonical keys"
```

---

## Task 10: `FundamentalJoinerAgent`

**Files:**
- Create: `src/agents/analysts/fundamental/joiner.py`
- Test: `tests/analysts/fundamental/test_joiner.py`

- [ ] **Step 1: Write the failing tests**

Mirror `tests/analysts/news/test_joiner.py` with:
- agent class → `FundamentalJoinerAgent`
- state keys: `temp:fundamental_verdict_<TICKER>`, `temp:fundamental_data`, output keys `fundamental_verdicts` + `fundamental_evidence`
- extractor → `extract_fundamental_features`
- raw data shape per ticker uses the same dict shape as the existing fundamental fetch callback writes

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/analysts/fundamental/test_joiner.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `FundamentalJoinerAgent`**

Mirror `NewsJoinerAgent` line-for-line with `news` → `fundamental` everywhere. Import `extract_fundamental_features` from `contract.extractors.fundamental`. Use trace key `02_fundamental_verdict`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/analysts/fundamental/test_joiner.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/analysts/fundamental/joiner.py tests/analysts/fundamental/test_joiner.py
git commit -m "feat(fundamental): FundamentalJoinerAgent — symmetric with NewsJoinerAgent"
```

---

## Task 11: Rewrite analyst-branch factories — `build_news_branch` / `build_fundamental_branch`

**Files:**
- Modify: `src/agents/analysts/news/agent.py`
- Modify: `src/agents/analysts/fundamental/agent.py`
- Test: `tests/analysts/test_branch_composition.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/analysts/test_branch_composition.py
"""The top-level News and Fundamental branches are SequentialAgents of
[FetchAgent, *per-ticker branches, JoinerAgent]."""
from __future__ import annotations

from google.adk.agents import SequentialAgent

from agents.analysts.fundamental.agent import build_fundamental_branch
from agents.analysts.fundamental.fetch_agent import FundamentalFetchAgent
from agents.analysts.fundamental.joiner import FundamentalJoinerAgent
from agents.analysts.heuristics import load_heuristics
from agents.analysts.news.agent import build_news_branch
from agents.analysts.news.fetch_agent import NewsFetchAgent
from agents.analysts.news.joiner import NewsJoinerAgent
from agents.isolated_failure import IsolatedFailureWrapper


def test_news_branch_shape():
    """[NewsFetchAgent, IsolatedFailureWrapper×N, NewsJoinerAgent]."""

    h = load_heuristics()
    branch = build_news_branch(h.news_vocabulary, tickers=["AAPL", "MSFT", "GOOG"])

    assert isinstance(branch, SequentialAgent)
    subs = branch.sub_agents
    assert len(subs) == 5  # fetch + 3 per-ticker + joiner

    assert isinstance(subs[0],  NewsFetchAgent)
    assert isinstance(subs[-1], NewsJoinerAgent)
    for inner in subs[1:-1]:
        assert isinstance(inner, IsolatedFailureWrapper)
        assert inner.analyst == "news"


def test_fundamental_branch_shape():
    """[FundamentalFetchAgent, IsolatedFailureWrapper×N, FundamentalJoinerAgent]."""

    h = load_heuristics()
    branch = build_fundamental_branch(h.fundamental_vocabulary, tickers=["AAPL", "MSFT"])

    assert isinstance(branch, SequentialAgent)
    subs = branch.sub_agents
    assert len(subs) == 4

    assert isinstance(subs[0],  FundamentalFetchAgent)
    assert isinstance(subs[-1], FundamentalJoinerAgent)
    for inner in subs[1:-1]:
        assert isinstance(inner, IsolatedFailureWrapper)
        assert inner.analyst == "fundamental"


def test_empty_watchlist_yields_minimal_branch():
    """Zero tickers → just [FetchAgent, JoinerAgent] (still a valid no-op branch)."""

    h = load_heuristics()
    branch = build_news_branch(h.news_vocabulary, tickers=[])

    subs = branch.sub_agents
    assert len(subs) == 2
    assert isinstance(subs[0],  NewsFetchAgent)
    assert isinstance(subs[-1], NewsJoinerAgent)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/analysts/test_branch_composition.py -v`
Expected: FAIL — `build_news_branch` / `build_fundamental_branch` do not exist (today's API is `build_news_analyst`).

- [ ] **Step 3: Rewrite `news/agent.py`**

Replace the entire `agent.py` body with:

```python
"""News analyst SequentialAgent branch — per-ticker fan-out (Phase 9).

Builds: SequentialAgent[NewsFetchAgent, *per-ticker branches, NewsJoinerAgent]

Where each per-ticker branch is
``IsolatedFailureWrapper(RetryingAgentWrapper(LlmAgent))`` constructed via
``build_news_branch_for_ticker``.  Composition is done here rather than in
``orchestrator.pipeline`` so the per-tick pipeline build (driver.py /
tick.py) calls a single factory.

The legacy ``build_news_analyst`` factory (one LlmAgent over a
``VerdictBatch``) is retired in Phase 9 — every call site (pipeline, tests)
is updated to use ``build_news_branch`` instead.  See
``docs/Phase9-agent-fanning-per-ticker/spec.md``.
"""
from __future__ import annotations

from google.adk.agents import SequentialAgent

from agents.analysts.heuristics import NewsVocabulary
from agents.analysts.news.fetch_agent import NewsFetchAgent
from agents.analysts.news.joiner import NewsJoinerAgent
from agents.analysts.news.per_ticker import build_news_branch_for_ticker


def build_news_branch(
    vocab: NewsVocabulary,
    *,
    tickers: list[str],
) -> SequentialAgent:
    """Construct the per-tick News analyst branch from the current watchlist.

    Args:
        vocab:    Validated NewsVocabulary holding closed-vocab tag lists.
        tickers:  The watchlist as known at pipeline-build time.  An empty
                  list is permitted — the branch becomes a fetch+joiner
                  no-op that still emits canonical (empty) news_verdicts /
                  news_evidence so downstream consumers see consistent
                  shapes.

    Returns:
        SequentialAgent named ``"NewsAnalystBranch"`` composed of
        ``[NewsFetchAgent, *per-ticker branches, NewsJoinerAgent]``.

    The caller (``orchestrator.pipeline._build_analyst_pool``) is
    responsible for invoking this once per tick with the current watchlist
    — see the Phase 9 spec §7 for the per-tick rebuild rationale.
    """
    per_ticker = [
        build_news_branch_for_ticker(ticker, vocab) for ticker in tickers
    ]

    return SequentialAgent(
        name="NewsAnalystBranch",
        sub_agents=[
            NewsFetchAgent(name="NewsFetch"),
            *per_ticker,
            NewsJoinerAgent(name="NewsJoiner"),
        ],
    )
```

- [ ] **Step 4: Rewrite `fundamental/agent.py` symmetrically**

Apply the same shape — `build_fundamental_branch(vocab, *, tickers)` returning a `SequentialAgent` of `[FundamentalFetchAgent, *per-ticker branches, FundamentalJoinerAgent]`. Name: `"FundamentalAnalystBranch"`.

Delete the old `build_fundamental_analyst` function and the `_fundamental_hash_inputs_from_dict` helper (it has moved to `per_ticker.py`'s import via `report_cache.py`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/analysts/test_branch_composition.py -v`
Expected: PASS — all three tests green.

- [ ] **Step 6: Commit**

```bash
git add src/agents/analysts/news/agent.py src/agents/analysts/fundamental/agent.py tests/analysts/test_branch_composition.py
git commit -m "refactor(analysts): build_news_branch / build_fundamental_branch — SequentialAgent fan-out"
```

---

## Task 12: Update `_build_analyst_pool` and `build_pipeline` signatures

**Files:**
- Modify: `src/orchestrator/pipeline.py`
- Test: `tests/orchestrator/test_pipeline_build.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestrator/test_pipeline_build.py
"""build_pipeline accepts the current watchlist explicitly (Phase 9)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orchestrator.pipeline import build_pipeline


def test_build_pipeline_requires_tickers_kwarg():
    """tickers is a required keyword argument."""

    broker = MagicMock()

    with pytest.raises(TypeError):
        build_pipeline(broker, db_session=None)  # missing tickers


def test_build_pipeline_threads_tickers_into_analyst_pool():
    """The composed pipeline contains a NewsAnalystBranch sized to the watchlist."""

    from agents.analysts.news.agent import build_news_branch
    from agents.isolated_failure import IsolatedFailureWrapper

    broker  = MagicMock()
    tickers = ["AAPL", "MSFT", "GOOG"]

    pipeline = build_pipeline(broker, db_session=None, tickers=tickers)

    # Drill into the SequentialAgent tree to find the News branch.
    analyst_pool = pipeline.sub_agents[0]
    news_branch  = next(
        sa for sa in analyst_pool.sub_agents if sa.name == "NewsAnalystBranch"
    )

    # FetchAgent + 3 per-ticker branches + JoinerAgent = 5
    assert len(news_branch.sub_agents) == 5
    per_ticker = news_branch.sub_agents[1:-1]
    assert {p.ticker for p in per_ticker} == set(tickers)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/orchestrator/test_pipeline_build.py -v`
Expected: FAIL — current `build_pipeline` has no `tickers` parameter.

- [ ] **Step 3: Update `pipeline.py`**

Replace `_build_analyst_pool` and `build_pipeline` in `src/orchestrator/pipeline.py`:

```python
def _build_analyst_pool(tickers: list[str]):
    """Build the AnalystPool — Sequential[Parallel[Tech,Social], Fund, News].

    Phase 9 changes:
      - Fundamental and News are now per-ticker fan-out branches
        constructed from the watchlist.  Each is a
        ``SequentialAgent[FetchAgent, *PerTickerBranches, JoinerAgent]``
        built via ``build_fundamental_branch`` / ``build_news_branch``.
      - Both branches own their own failure-containment
        (``IsolatedFailureWrapper`` per per-ticker child) and per-ticker
        retry semantics.  The outer ``RetryingAgentWrapper`` wrap
        previously applied here is dropped — retries now live INSIDE each
        per-ticker child so one ticker's 429 backoff does not block the
        other tickers.

    Technical and Social remain a ParallelAgent of two BaseAgent
    subclasses with distinct output keys (Rule 4 satisfied).
    """
    from google.adk.agents import ParallelAgent, SequentialAgent

    from agents.analysts.fundamental.agent import build_fundamental_branch
    from agents.analysts.heuristics import load_heuristics
    from agents.analysts.news.agent import build_news_branch
    from agents.analysts.social.agent import _build_social_analyst
    from agents.analysts.technical.agent import _build_technical_analyst

    h = load_heuristics()

    parallel_deterministic = ParallelAgent(
        name="DeterministicAnalysts",
        sub_agents=[
            _build_technical_analyst(h.technical),
            _build_social_analyst(h.social),
        ],
    )

    fundamental_branch = build_fundamental_branch(
        h.fundamental_vocabulary, tickers=tickers,
    )
    news_branch = build_news_branch(
        h.news_vocabulary, tickers=tickers,
    )

    return SequentialAgent(
        name="AnalystPool",
        sub_agents=[
            parallel_deterministic,
            fundamental_branch,
            news_branch,
        ],
    )


def build_pipeline(broker, db_session=None, *, tickers: list[str]) -> SequentialAgent:
    """Compose the full hourly tick pipeline.

    Phase 9: ``tickers`` is a required keyword argument.  Both lifecycles
    (live tick.py and backtest driver.py) call ``build_pipeline`` per
    invocation with the current ``state["tickers"]``.

    Args:
        broker:     Broker instance (FakeBroker for backtests, Trading212Broker for live).
        db_session: Optional SQLAlchemy session for persistence writers.
        tickers:    The current watchlist.  Drives per-tick fan-out of the
                    News and Fundamental analyst branches.

    Returns:
        SequentialAgent named "HourlyTick" wiring the full pipeline.
    """
    from agents.contract.evidence_writer import build_evidence_writer
    from agents.executor.agent import build_executor
    from agents.risk_gate.agent import RiskGateAgent
    from agents.snapshot.agent import build_snapshotter
    from agents.strategist.decision_writer import build_strategist_decision_writer
    return SequentialAgent(
        name="HourlyTick",
        sub_agents=[
            _build_analyst_pool(tickers),
            build_evidence_writer(db_session),
            _build_strategist(),
            build_strategist_decision_writer(db_session),
            RiskGateAgent(broker=broker),
            build_executor(broker, db_session),
            _build_memory_writer(),
            build_snapshotter(broker, db_session),
        ],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/orchestrator/test_pipeline_build.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/pipeline.py tests/orchestrator/test_pipeline_build.py
git commit -m "refactor(pipeline): tickers parameter; per-tick analyst fan-out composition"
```

---

## Task 13: Live tick + every other `build_pipeline` caller — pass `tickers=`

**Files:**
- Modify: `src/orchestrator/tick.py`
- Modify: `tests/unit/orchestrator/test_pipeline_wiring_v2.py`
- Modify: `tests/integration/test_pipeline_composition.py`
- Modify: `scripts/trace_tick.py`

> **Why this task touches multiple files:** Task 12 made `tickers=` a required kwarg on `build_pipeline`. Every existing caller across the codebase must be updated in the same commit or `pytest -q` will fail at collection. The enumerated list below was generated by `grep -rn "build_pipeline" src/ tests/ scripts/` on 2026-05-21 — if the grep returns additional sites at implementation time, patch them too.

- [ ] **Step 1: Enumerate every `build_pipeline` call site**

Run: `grep -rn "build_pipeline(" src/ tests/ scripts/`

The known-at-plan-time sites (from the audit grep) are:

| File | Line(s) | Current call | New call |
|---|---|---|---|
| `src/orchestrator/tick.py` | 138 | `build_pipeline(broker, session)` | `build_pipeline(broker, session, tickers=state["tickers"])` |
| `src/backtest/driver.py` | 173 | `build_pipeline(broker, db_session)` | _Handled by Task 14 — moves into per-tick loop._ |
| `tests/unit/orchestrator/test_pipeline_wiring_v2.py` | 17, 35 | `build_pipeline(broker=..., db_session=None)` | add `tickers=["AAPL"]` (or whatever tickers the assertion checks against) |
| `tests/integration/test_pipeline_composition.py` | 10, 16, 23, 39 | `build_pipeline(broker)` | `build_pipeline(broker, tickers=["AAPL"])` |
| `scripts/trace_tick.py` | 115 | `build_pipeline(broker)` | `build_pipeline(broker, tickers=state["tickers"])` (the script already builds `state` before this line) |

- [ ] **Step 2: Patch the live tick**

Change `src/orchestrator/tick.py:138` from `build_pipeline(broker, session)` to:

```python
pipeline = build_pipeline(broker, session, tickers=state["tickers"])
```

`state["tickers"]` is already populated upstream by `_build_initial_state` — no other change in `tick.py` needed.

- [ ] **Step 3: Patch every test/script caller**

For each file listed in Step 1, add `tickers=[...]` (a single-ticker list like `["AAPL"]` is fine for tests that only check pipeline wiring shape; pass `state["tickers"]` for `scripts/trace_tick.py`). Do **not** stub `build_pipeline` itself — update real call sites.

- [ ] **Step 4: Run the full unit + orchestrator suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/ tests/integration/test_pipeline_composition.py tests/orchestrator/ -v`
Expected: PASS (any collection-time `TypeError: missing keyword 'tickers'` means a call site was missed — go back to Step 1 and grep again).

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/tick.py tests/unit/orchestrator/test_pipeline_wiring_v2.py tests/integration/test_pipeline_composition.py scripts/trace_tick.py
git commit -m "refactor(orchestrator): thread watchlist into per-tick build_pipeline calls"
```

---

## Task 14: Backtest driver — per-tick pipeline rebuild

**Files:**
- Modify: `src/backtest/driver.py`
- Test: `tests/backtest/test_driver_per_tick_rebuild.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/backtest/test_driver_per_tick_rebuild.py
"""The backtest driver rebuilds the pipeline per tick from state['tickers']."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_pipeline_built_per_tick_with_current_watchlist():
    """Each _run_one_tick call invokes build_pipeline with state['tickers']."""

    from backtest.driver import BacktestDriver
    from backtest.schedule import Tick

    broker  = MagicMock()
    broker.get_portfolio = MagicMock(return_value=MagicMock(model_dump=lambda mode: {}))

    state = {"tickers": ["AAPL", "MSFT"]}

    with patch("backtest.driver.build_pipeline") as mock_build, \
         patch("backtest.driver.install_observability"):
        mock_build.return_value = MagicMock()

        # The driver should NOT call build_pipeline at construction —
        # only at per-tick run time.
        driver = BacktestDriver(
            broker=broker, db_session=None, run_id="test",
            run_dir=MagicMock(), enforce_completion=False,
        )
        mock_build.assert_not_called()

        # Stub the runner so we exercise _run_one_tick's build path without
        # exercising ADK.
        async def _stub_runner_run(*args, **kwargs):
            if False:  # pragma: no cover
                yield
        mock_build.return_value.run_async = _stub_runner_run

        # Per-tick state mutation — the second tick uses a different watchlist
        # to confirm the rebuild reads from state each time.
        await driver._run_one_tick({"tickers": ["AAPL", "MSFT"], "tick_id": "t1"})
        await driver._run_one_tick({"tickers": ["AAPL"],          "tick_id": "t2"})

        # Two builds, with the right tickers each time.
        assert mock_build.call_count == 2
        first_call_kwargs  = mock_build.call_args_list[0].kwargs
        second_call_kwargs = mock_build.call_args_list[1].kwargs
        assert first_call_kwargs["tickers"]  == ["AAPL", "MSFT"]
        assert second_call_kwargs["tickers"] == ["AAPL"]
```

> Patch targets at implementation time may need tweaking depending on how the driver imports `build_pipeline`. Confirm via `grep -n "build_pipeline" src/backtest/driver.py` before writing the test.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/backtest/test_driver_per_tick_rebuild.py -v`
Expected: FAIL — `build_pipeline` is called once at `__init__`, not per tick.

- [ ] **Step 3: Modify `BacktestDriver`**

In `src/backtest/driver.py`:

1. Delete the line `self._pipeline = build_pipeline(broker, db_session)` at `__init__` (currently `driver.py:173`).
2. Keep `self._broker` and `self._db_session` as attributes (likely already stored — check).
3. In `_run_one_tick`, before constructing the `Runner`, build the pipeline:

```python
async def _run_one_tick(self, state: dict) -> None:
    """... existing docstring ..."""

    # Phase 9: rebuild the pipeline per tick so the News and Fundamental
    # analyst branches fan out across the current state["tickers"].  The
    # watchlist is tick-scoped per §A; building once per run would freeze
    # an outdated tickers list into the SequentialAgent.sub_agents.
    pipeline = build_pipeline(
        self._broker, self._db_session,
        tickers=state.get("tickers", []) or [],
    )

    session_service = InMemorySessionService()
    runner = Runner(
        agent=pipeline,
        app_name="backtest",
        session_service=session_service,
    )
    # ... rest of method unchanged, but replace `self._pipeline` with `pipeline`
```

Search for every other reference to `self._pipeline` in the file and replace with the local `pipeline` (likely none after `__init__` — verify).

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/backtest/test_driver_per_tick_rebuild.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/backtest/driver.py tests/backtest/test_driver_per_tick_rebuild.py
git commit -m "refactor(backtest/driver): rebuild pipeline per tick to honour watchlist contract"
```

---

## Task 15: Update integration tests — direct imports + stub shims

**Files:**
- Modify: `tests/integration/backtest/test_end_to_end_smoke.py`
- Modify: `tests/integration/backtest/test_no_silent_zero_features.py`
- Modify: any conftest stub shims for News/Fundamental LLMs

> **Why this task touches two integration tests:** both files directly import `build_news_analyst` / `build_fundamental_analyst` and construct them with the old no-tickers signature. Task 11 deletes those symbols and replaces them with `build_news_branch(vocab, tickers)` / `build_fundamental_branch(vocab, tickers)`. If we patched only the smoke test, the second file would crash at import. Verified at plan-time via `grep -rn "build_news_analyst\|build_fundamental_analyst" tests/`.

- [ ] **Step 1: Identify per-ticker agent name patterns in stub shims**

Run: `grep -rn "NewsAnalyst\|FundamentalAnalyst" tests/integration/`

Today's shims key off the literal names `"NewsAnalyst"` / `"FundamentalAnalyst"`. Per-ticker agents are named `NewsAnalyst_AAPL`, `NewsAnalyst_MSFT`, etc.

- [ ] **Step 2: Update the stub recognition**

In every test fixture or conftest that short-circuits a News / Fundamental LLM via `before_model_callback`, replace exact-match comparisons with a prefix match:

```python
# Before:
if agent.name in {"NewsAnalyst", "FundamentalAnalyst"}:
    ...

# After:
if agent.name.startswith(("NewsAnalyst_", "FundamentalAnalyst_")):
    ...
```

Also update the synthetic `LlmResponse` payload — it must now be a `TickerVerdict` JSON (one verdict), not a `VerdictBatch` JSON (a list).

```python
synthetic_text = json.dumps({
    "ticker":      ticker_from_agent_name(agent.name),
    "lean":        "neutral",
    "magnitude":   0.0,
    "confidence":  0.5,
    "rationale":   "stub",
    "key_factors": [],
    "is_no_data":  False,
})
```

Where `ticker_from_agent_name(name)` strips the `NewsAnalyst_` / `FundamentalAnalyst_` prefix.

- [ ] **Step 3: Swap the direct factory imports**

Two integration tests import the deleted factories directly:

| File | Lines | Change |
|---|---|---|
| `tests/integration/backtest/test_end_to_end_smoke.py` | 359, 361, 373, 374 | `from agents.analysts.fundamental.agent import build_fundamental_analyst` → `build_fundamental_branch`; `from agents.analysts.news.agent import build_news_analyst` → `build_news_branch`; call sites become `build_fundamental_branch(h.fundamental_vocabulary, tickers=tickers)` / `build_news_branch(h.news_vocabulary, tickers=tickers)`. `tickers` is the watchlist already in scope from the surrounding fixture. |
| `tests/integration/backtest/test_no_silent_zero_features.py` | 270, 272, 285, 286 | Same swap, same kwarg. |

Both files use the constructed branches to assert pipeline composition / observability — the assertion targets may need adjusting since the new branch is a `SequentialAgent[Fetch, *per-ticker branches, Joiner]` rather than the old `YieldingAnalystWrapper(LlmAgent)`. Read the surrounding asserts and update them to walk `sub_agents` instead of `.inner`.

- [ ] **Step 4: Run the smoke test + the no-silent-zero check**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m pytest \
    tests/integration/backtest/test_end_to_end_smoke.py \
    tests/integration/backtest/test_no_silent_zero_features.py \
    -v -m slow
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/backtest/ tests/conftest.py
git commit -m "test(integration): adapt stubs + direct imports for per-ticker fan-out"
```

---

## Task 16: Retire `YieldingAnalystWrapper`; clean up dead exports/comments

> **Scope correction (audit fix):** `make_evidence_callback` is **NOT** deleted in this task — Technical, Social, and SmartMoney still register it as their `after_agent_callback`. This task only removes the News/Fundamental call sites (which Task 11's rewrite has already done in-source) and retires the now-unused `YieldingAnalystWrapper`. The `make_evidence_callback` factory in `src/agents/analysts/_common.py` stays.

**Files:**
- Delete: `src/agents/analysts/_base_yield.py`
- Delete: `tests/unit/agents/analysts/test_news_yield.py`
- Delete: `tests/unit/agents/analysts/test_fundamental_yield.py`
- Modify: `src/agents/analysts/__init__.py` — drop `build_news_analyst` / `build_fundamental_analyst` re-exports; add `build_news_branch` / `build_fundamental_branch`
- Modify: `src/agents/analysts/news/__init__.py` — same swap
- Modify: `src/agents/analysts/fundamental/__init__.py` — same swap
- Modify: `src/agents/llm_retry.py` — purge the five `YieldingAnalystWrapper` docstring/comment references (lines 14, 46, 149, 165, 206 at plan-time)
- Modify: `src/contract/extractors/social.py` — fix the `make_evidence_callback`-related comment at line 12 if it references the retired wrapper (read context to confirm)
- Modify: `tests/unit/orchestrator/test_pipeline_sequential_branches.py` — fix the line 46 comment that mentions `.inner` pointing at a `YieldingAnalystWrapper`
- Modify: `tests/integration/test_analyst_pool.py` — fix the line 41 comment referencing `YieldingAnalystWrapper`
- Modify: `tests/analysts/test_news.py`, `tests/analysts/test_fundamental.py` — these structural tests assert `isinstance(branch, YieldingAnalystWrapper)`. Rewrite to assert the new `SequentialAgent[Fetch, *per-ticker branches, Joiner]` shape (or delete if Task 11/12 already covered the structural assertions in the new branch-factory tests).
- Modify: `src/config/models.py` — fix docstring references at lines 61, 64 from `build_news_analyst` / `build_fundamental_analyst` to `build_news_branch` / `build_fundamental_branch`

- [ ] **Step 1: Confirm `YieldingAnalystWrapper` has no remaining live consumers**

Run:
```bash
grep -rn "YieldingAnalystWrapper" src/ tests/ scripts/
```

After Task 11, every match should be either:
- the definition site (`src/agents/analysts/_base_yield.py`) — about to be deleted
- one of the two dedicated test files — about to be deleted
- a docstring/comment reference — about to be cleaned up in this task

If anything else still imports the symbol, find the missed call site and fix it before continuing — **do not push through**.

- [ ] **Step 2: Delete the module and its dedicated tests**

```bash
git rm src/agents/analysts/_base_yield.py \
       tests/unit/agents/analysts/test_news_yield.py \
       tests/unit/agents/analysts/test_fundamental_yield.py
```

- [ ] **Step 3: Update the analyst-package re-exports**

`src/agents/analysts/__init__.py` — replace:

```python
from .fundamental.agent import build_fundamental_analyst
from .news.agent        import build_news_analyst

__all__ = [
    "build_fundamental_analyst",
    "build_news_analyst",
]
```

with:

```python
from .fundamental.agent import build_fundamental_branch
from .news.agent        import build_news_branch

__all__ = [
    "build_fundamental_branch",
    "build_news_branch",
]
```

Do the same surgery to `src/agents/analysts/news/__init__.py` and `src/agents/analysts/fundamental/__init__.py`. Also update the module docstrings — they currently describe the returned object as a `YieldingAnalystWrapper`; change to "a `SequentialAgent[FetchAgent, per-ticker branches, JoinerAgent]`".

- [ ] **Step 4: Clean up docstring/comment references**

For each file listed above, replace `YieldingAnalystWrapper` mentions with accurate descriptions of the new structure. Concretely:

- `src/agents/llm_retry.py` lines 14, 46, 149, 165, 206 — the docstrings explain that `RetryingAgentWrapper` works on `LlmAgent` or `YieldingAnalystWrapper(LlmAgent)`. The new wrapping pattern is `IsolatedFailureWrapper(RetryingAgentWrapper(LlmAgent))`. Update accordingly.
- `tests/unit/orchestrator/test_pipeline_sequential_branches.py:46` — the comment refers to `.inner` pointing at the wrapper. With per-ticker branches that no-longer-exist, the assertion itself is probably wrong too — read the test, update the assertion to walk the new `SequentialAgent` shape, and rewrite the comment to match.
- `tests/integration/test_analyst_pool.py:41` — same kind of comment fix.
- `src/config/models.py` lines 61, 64 — change the cross-references in the docstring to the new factory names.

- [ ] **Step 5: Rewrite the News/Fundamental structural tests**

`tests/analysts/test_news.py` and `tests/analysts/test_fundamental.py` today assert the factory returns a `YieldingAnalystWrapper` named `"NewsAnalystBranch"` / `"FundamentalAnalystBranch"` and walk `.inner` to check the underlying `LlmAgent`. After Phase 9 the factory returns a `SequentialAgent[FetchAgent, *per-ticker branches, JoinerAgent]`. Rewrite each test to:

1. Call the new factory with a small `tickers=["AAPL", "MSFT"]` list.
2. Assert the returned agent is a `SequentialAgent`.
3. Assert `sub_agents[0]` is the fetch agent and `sub_agents[-1]` is the joiner.
4. Assert the middle slice is exactly `len(tickers)` per-ticker branches, each named `"NewsAnalyst_<TICKER>"` / `"FundamentalAnalyst_<TICKER>"`.

If Task 11's new branch-factory tests already cover all of this, just delete `test_news.py` / `test_fundamental.py` — do not maintain duplicate structural tests.

- [ ] **Step 6: Run the full fast test suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -m "not slow and not integration" -q`
Expected: PASS. Any `ImportError: cannot import name 'build_news_analyst'` means a re-export was missed — go back to Step 3.

- [ ] **Step 7: Commit**

```bash
git add -A src/agents/ src/config/models.py tests/analysts/ tests/unit/agents/ tests/unit/orchestrator/ tests/integration/test_analyst_pool.py
git commit -m "refactor(analysts): retire YieldingAnalystWrapper; clean up dead docstrings/exports"
```

---

## Task 17: Update §A invariant table in `docs/contract-invariants.md`

**Files:**
- Modify: `docs/contract-invariants.md`

- [ ] **Step 1: Locate the §A rows**

Run: `grep -n "news_verdicts\|fundamental_verdicts" docs/contract-invariants.md`

- [ ] **Step 2: Update the Owner column**

For both `news_verdicts` and `fundamental_verdicts` rows, change the Owner column:
- Before: `NewsAnalyst (output_key)` / `FundamentalAnalyst (output_key)`
- After:  `NewsJoinerAgent (state_delta)` / `FundamentalJoinerAgent (state_delta)`

No new rows. The per-ticker `temp:news_verdict_<TICKER>`, `temp:fundamental_verdict_<TICKER>`, and `temp:news_context_<TICKER>` keys are pipeline-internal working state (Rule 2) — not §A-listed.

- [ ] **Step 3: Add a short note above the §A table or in the closest commentary block**

> "Phase 9 (per-ticker analyst fan-out): the canonical `news_verdicts` and `fundamental_verdicts` keys are now written by joiner agents that consolidate per-ticker working keys.  The keys' contract values and lifetimes are unchanged — only ownership shifted from a batched LlmAgent to a downstream BaseAgent.  See `docs/Phase9-agent-fanning-per-ticker/spec.md`."

- [ ] **Step 4: Commit**

```bash
git add docs/contract-invariants.md
git commit -m "docs(contract): §A owners updated for Phase 9 joiner agents"
```

---

## Task 18: Final sweep — end-to-end backtest smoke

- [ ] **Step 1: Run the full fast test suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -m "not slow and not integration" -q`
Expected: PASS.

- [ ] **Step 2: Run lint**

Run: `PYTHONPATH=src .venv/bin/python -m ruff check src/ tests/`
Expected: PASS (no warnings).

- [ ] **Step 3: Run the end-to-end backtest smoke**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_end_to_end_smoke.py -v -m slow`
Expected: PASS.

- [ ] **Step 3b: `config/analysts.json` still controls per-analyst output caps — end-to-end check**

> **Why this step exists:** The audit flagged that `config/analysts.json::output_caps` (rationale chars, summary chars, driver name/body chars) must still bound each per-ticker LlmAgent's output after the fan-out. Output caps flow into the system via two paths — (a) prompt-facing text written by `build_news_instruction` / `build_fundamental_instruction` (`prompts.py`), and (b) Pydantic schema enforcement on `AnalystVerdict` / `DriverReport` / `DriverEvidence` (`src/contract/evidence.py:40-106`). `TickerVerdict` inherits from `AnalystVerdict` so the schema-side cap survives automatically — but only if no task accidentally bypassed `AnalystVerdict` for a new ad-hoc schema. This step locks both paths down.

Add (or extend) `tests/agents/test_output_caps_per_ticker.py`:

```python
"""Per-ticker analysts honour ``config/analysts.json::output_caps``.

Two assertions:
  1. Prompt-facing — the literal rationale-char cap is substituted into
     the rendered single-ticker instruction.
  2. Schema-facing — the per-ticker LlmAgent's ``output_schema`` is
     ``TickerVerdict`` (or a subclass) and inherits the
     ``rationale`` ``max_length`` from ``AnalystVerdict``.
"""
from __future__ import annotations

from agents.analysts.fundamental.per_ticker import build_fundamental_branch_for_ticker
from agents.analysts.heuristics import load_heuristics
from agents.analysts.news.per_ticker import build_news_branch_for_ticker
from config.analysts import get_analysts_config
from contract.evidence import AnalystVerdict, TickerVerdict


def _walk_to_llm_agent(branch):
    """Return the inner ``LlmAgent`` regardless of wrapper nesting."""

    cur = branch
    while not hasattr(cur, "instruction") or not hasattr(cur, "output_schema"):
        cur = getattr(cur, "inner", None)
        assert cur is not None, "could not locate inner LlmAgent"
    return cur


def test_news_per_ticker_prompt_contains_config_rationale_cap():
    """Rendered News instruction substitutes ``verdict_rationale_max_chars``."""

    h = load_heuristics()
    branch = build_news_branch_for_ticker("AAPL", h.news_vocabulary)
    llm = _walk_to_llm_agent(branch)

    cap = get_analysts_config().output_caps.verdict_rationale_max_chars
    assert f"{cap}" in llm.instruction, (
        "news per-ticker instruction does not carry the configured "
        "rationale cap — output_caps config path is broken"
    )


def test_fundamental_per_ticker_prompt_contains_config_rationale_cap():
    """Mirror for fundamental."""

    h = load_heuristics()
    branch = build_fundamental_branch_for_ticker("AAPL", h.fundamental_vocabulary)
    llm = _walk_to_llm_agent(branch)

    cap = get_analysts_config().output_caps.verdict_rationale_max_chars
    assert f"{cap}" in llm.instruction


def test_per_ticker_output_schema_inherits_analyst_verdict_caps():
    """`output_schema` must be (or extend) ``AnalystVerdict`` so Pydantic
    enforces the schema-side ``rationale`` ``max_length``."""

    h = load_heuristics()
    for branch in (
        build_news_branch_for_ticker("AAPL", h.news_vocabulary),
        build_fundamental_branch_for_ticker("AAPL", h.fundamental_vocabulary),
    ):
        llm = _walk_to_llm_agent(branch)
        assert issubclass(llm.output_schema, AnalystVerdict), (
            f"{llm.name} bypassed AnalystVerdict — schema-side output caps "
            f"are no longer enforced.  Got: {llm.output_schema!r}"
        )
        # Sanity — confirm the per-ticker variant in current use.
        assert llm.output_schema is TickerVerdict
```

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/agents/test_output_caps_per_ticker.py -v`
Expected: PASS.

Commit:
```bash
git add tests/agents/test_output_caps_per_ticker.py
git commit -m "test(analysts): lock in config/analysts.json output_caps end-to-end (Phase 9)"
```

- [ ] **Step 4: Run a real backtest on the SVB-stress window**

Run: `PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window svb-stress-2023-03`

Expected:
- No `EOF while parsing a string` JSON-validation errors (the original symptom).
- Per-tick obs/logs show one `invoke_agent` span per ticker per analyst (e.g. `NewsAnalyst_AAPL`, `NewsAnalyst_MSFT`).
- `metrics.md` cache hit rate is meaningful (one row per branch).
- The run completes without aborts.

If the run surfaces a structural issue (e.g. unexpected state-key collision, joiner producing wrong shapes), file the issue and fix before reporting the phase done. Do not paper over with try/except.

- [ ] **Step 5: Append a `graph_delta.md` entry**

Append to `graphify-out/graph_delta.md` (per project CLAUDE.md — local-only, never committed):

```
## 2026-05-21 — Phase 9 per-ticker analyst fan-out

Replaced batched News/Fundamental LlmAgents with SequentialAgent fan-out.

- New nodes: IsolatedFailureWrapper, NewsFetchAgent, FundamentalFetchAgent,
  NewsJoinerAgent, FundamentalJoinerAgent, build_news_branch_for_ticker,
  build_fundamental_branch_for_ticker, build_news_branch,
  build_fundamental_branch.
- New edges: orchestrator.pipeline → build_news_branch/build_fundamental_branch
  (per tick); per_ticker → IsolatedFailureWrapper → RetryingAgentWrapper →
  LlmAgent; JoinerAgent → AnalystEvidence / VerdictBatch.
- Removed nodes: YieldingAnalystWrapper, make_evidence_callback,
  build_news_analyst, build_fundamental_analyst.
```

- [ ] **Step 6: Commit**

```bash
# graphify-out/ is gitignored — no commit needed for the delta.
# Final empty commit to mark the phase done:
git commit --allow-empty -m "feat(analysts): Phase 9 per-ticker fan-out complete"
```

---

## Self-Review

Done after writing the plan; issues fixed inline.

**Spec coverage:**

| Spec section | Plan tasks |
|---|---|
| §1 NewsFetchAgent / FundamentalFetchAgent | Tasks 4, 5 |
| §2 Per-ticker LlmAgents | Tasks 2, 3, 7, 8 |
| §3 Joiner agents | Tasks 9, 10 |
| §4 Cache layer adaptation | Task 6 |
| §5 Failure handling per branch (IsolatedFailureWrapper) | Task 1 |
| §6 Wrappers retiring | Task 16 |
| §7 Backtest pipeline build cadence | Tasks 12, 13, 14 |
| §8 Schema additions (TickerVerdict — already exists) | n/a (no new schema) |
| §9 Observability reshape | Task 18 (verified during backtest run) |
| §10 §A invariant table updates | Task 17 |
| Testing strategy | Tests embedded in every task; integration suite updated in Task 15 |
| **Config control — `config/analysts.json::output_caps` (audit add-on)** | Tasks 2, 3 (prompt-side cap substitution test) + Task 18 Step 3b (end-to-end lock-in test) |
| **Strategist `_index_evidence` shape compat (audit add-on)** | Task 9 (explicit round-trip test in the joiner suite); confirmed at plan-time that `context_shim._index_evidence` accepts both `dict` and `AnalystEvidence` |

**Type / name consistency checks:**
- `temp:news_verdict_<TICKER>` / `temp:fundamental_verdict_<TICKER>` — used consistently across Tasks 6, 7, 8, 9, 10, 11.
- `temp:news_context_<TICKER>` / `temp:fundamental_context_<TICKER>` — Task 4 (writer) ↔ Task 7 (reader via instruction placeholder).
- `temp:news_context` / `temp:fundamental_context` (aggregate) — written by Tasks 4 and 5 for trace/debug, per spec §1.
- `build_news_branch` / `build_fundamental_branch` — Task 11 (definition) ↔ Task 12 (caller) ↔ Task 15 (integration tests) ↔ Task 16 (re-export update).
- `IsolatedFailureWrapper(name=, inner=, analyst=, ticker=)` — kwargs match across Task 1 (definition), Task 7 (News use), Task 8 (Fundamental use).
- `make_report_cache_callbacks(... ticker=, output_schema=, ...)` — Task 6 (signature) ↔ Tasks 7, 8 (callers).

**Placeholder scan:** The `_vocab()` stubs in Tasks 3 and 8 are flagged with `raise NotImplementedError` and an explicit instruction to populate at implementation time — acceptable since the implementer reads the real class to fill them. All other code blocks are complete.

**Audit fix-ups (applied after first audit pass):**

| Audit finding | Where fixed in this plan |
|---|---|
| Task 16 wrongly proposed deleting `make_evidence_callback` (still used by Tech/Social/SmartMoney) | Task 16 rewritten — factory stays; only News/Fundamental call sites are removed (which Task 11 already does in-source). |
| Task 16 did not enumerate `tests/unit/agents/analysts/test_news_yield.py` / `test_fundamental_yield.py` for deletion | Task 16 now lists both for explicit `git rm`. |
| `__init__.py` re-exports of `build_news_analyst` / `build_fundamental_analyst` left dangling | Task 16 now updates `src/agents/analysts/__init__.py`, `news/__init__.py`, `fundamental/__init__.py`. |
| `tests/integration/backtest/test_no_silent_zero_features.py` directly imports the deleted factories | Task 15 now enumerates this file alongside the smoke test. |
| Several other `build_pipeline(broker)` callers (test_pipeline_wiring_v2, test_pipeline_composition, scripts/trace_tick.py) needed `tickers=` kwarg added | Task 13 now enumerates every known caller in a table. |
| `news_context` / `fundamental_context` plain keys (spec §1) no longer written | Tasks 4 and 5 now retain the aggregate `temp:news_context` / `temp:fundamental_context` for trace/debug. |
| `tests/analysts/test_news.py`, `tests/analysts/test_fundamental.py`, `test_pipeline_sequential_branches.py:46`, `test_analyst_pool.py:41`, `src/agents/llm_retry.py` docstrings, `src/config/models.py:61,64` carry stale `YieldingAnalystWrapper` / old-factory references | Task 16 enumerates each file for cleanup/rewrite. |
| Config-control of analyst output budgets (`config/analysts.json::output_caps`) at risk if the per-ticker rewrite bypassed `out_caps` substitution | Tasks 2 + 3 each gain an `output_caps` substitution test; Task 18 adds a Step 3b end-to-end lock-in test asserting both the prompt-side cap and the schema-side cap (via `issubclass(output_schema, AnalystVerdict)`). |
| `_index_evidence` shape compat (does it accept `ev.model_dump(mode="json")`?) | Verified at plan-time: `src/agents/strategist/context_shim.py:74-76` tolerates both `dict` and `AnalystEvidence`. Task 9 gains an explicit round-trip test as a regression guard. |

**Scope check:** One cohesive refactor (News + Fundamental fan-out + supporting infrastructure). Single plan is correct.
