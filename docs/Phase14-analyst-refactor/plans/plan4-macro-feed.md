# Plan 4 — Macro Stream Feed (Emission + Cache Refetch) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Post-eval gate.** This plan and Plan 5 (linkage) are **gated on the evaluation** that follows Plans 1–3 (spec §3, §6). Do not begin this plan until that eval has run and the decision to build the linkage stream has been taken. The macro stream's sole downstream consumer is Plan 5's linkage analyst; if the gate does not open, this plan is not built.

**Goal:** Emit the routed macro/roundup stream into ADK session state as `state["macro_articles"]` from the (Plan 3-rebuilt) `NewsFetchAgent`, and refetch the golden-cache news windows so the roundups the macro stream depends on are present in the caches the linkage analyst replays over.

**Architecture:** `NewsFetchAgent` — after Plan 3 has rebuilt it — pools each tick's per-ticker fetches, calls Plan 2's `route_articles`, and writes the serialised `.macro` stream to the (non-`temp:`) `macro_articles` state key. No LLM calls. Then the target backtest windows are refetched (`scripts/backtest_fetch.py --refetch-domain news`, the caps-aware version from Plan 2) so cached responses include roundups previously discarded at old cap margins, and a positive-signal smoke check asserts the macro stream is non-empty over a refetched cache.

**Tech Stack:** Python 3.12, Pydantic v2, Google ADK (`BaseAgent` state_delta events), SQLite golden cache (SQLAlchemy), Finnhub `/company-news`, pytest + pytest-asyncio.

## Global Constraints

Every task's requirements implicitly include this section. Copied from the approved spec (`docs/Phase14-analyst-refactor/specs/analyst-drift-refactor-design.md`) and the cross-plan pins:

- **D1 — No new news providers.** Finnhub `/company-news` is the sole news source.
- **D2 — Backtest/live parity is non-negotiable.** Live and backtest consume the identical endpoint with identical routing and identical caps. The Finnhub general-news feed (`/news?category=…`) must not appear anywhere.
- **D3 — Golden-cache refetch is permitted** to populate the macro stream for existing windows.
- **This plan is deterministic only — no LLM calls anywhere in this plan.**
- **Ordering dependency (read before Task 1):** Plan 3 rebuilds `src/agents/analysts/news/fetch_agent.py` wholesale; this plan **adds** the macro emission to that rebuilt agent. Apply Task 1's edits against Plan 3's rebuilt `fetch_agent.py`, not the pre-Phase-14 original — the anchor points below describe the emission seam; adapt them if the rebuilt structure has moved.
- **Pinned cross-plan interfaces (names verbatim):** `route_articles(articles, watchlist, *, company_names, roundup_threshold) -> RoutedArticles`, `RoutedArticles.macro: list[MacroArticle]`, `MacroArticle` (from Plan 2's `src/agents/analysts/news/router.py`). Session-state key: `state["macro_articles"]` — a list of serialised `MacroArticle` dicts with datetimes ISO-stringified.
- **PIT rules:** the Finnhub provider's `to_date` clipping and response-side PIT filter must be preserved untouched. Every datetime written to ADK state is ISO-stringified first (`model_dump(mode="json")`) — the backtest `DatabaseSessionService` cannot hold `datetime`.
- **Config convention:** every new tunable goes in `config/*.json` + a `config/README.md` row in the same task. Never hardcode.
- **House style:** British English everywhere; every function gets a docstring; non-trivial logic gets inline comments; blank lines between logical blocks.
- **Loud failures:** raise over silent degradation. Tests assert **positive** signals (a fixture roundup ARRIVES in `state["macro_articles"]` with correct ticker tags), never just absence of errors.
- **Shell conventions:** never prefix commands with `cd`. Tests: `.venv/bin/python -m pytest tests/... -v`. Scripts: `PYTHONPATH=src .venv/bin/python -m scripts.<name>`.
- **Destructive ops:** the Task 2 refetch replaces cached rows — show the commands and wait for the user's explicit go-ahead before running.
- Commit messages end with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

### Cross-plan interfaces (co-planned — trust, do not shim)

This plan is one of five co-planned Phase 14 plans. It runs after the eval gate.

**This plan CONSUMES:**

- **Plan 2 — specificity router:** `route_articles` / `MacroArticle` from `src/agents/analysts/news/router.py`; the config-driven news caps applied by `scripts/backtest_fetch.py` (Plan 2, Task 3).
- **Plan 3 — rebuilt fetch flow:** `NewsFetchAgent` in `src/agents/analysts/news/fetch_agent.py`; this plan adds the macro-emission seam to the rebuilt agent and owns the `.macro` side, while Plan 3 owns the `.company` side.

**This plan PROVIDES (consumed by Plan 5 — the linkage analyst):**

- `state["macro_articles"]` — always written (empty list on a quiet tick), non-`temp:`, ISO-serialised.
- Refetched `backtests/{iran-conflict-2026-02,baseline-2025-09}/store.sqlite` news tables containing the roundup articles the linkage eval windows replay over.

---

### Task 1: NewsFetchAgent emits `state["macro_articles"]`

**Files:**
- Modify: `src/agents/analysts/news/fetch_agent.py`
- Test: `tests/unit/agents/analysts/news/test_fetch_agent.py` (fixture modernisation + two new tests)

**Interfaces:**
- Consumes: `router.route_articles` / `router.MacroArticle` (Plan 2's router); `config.analysts.get_analysts_config().news.roundup_company_threshold` (existing config); `orchestrator.stock_picker.get_watchlist_with_names` (existing).
- Produces (pinned, consumed by Plan 5): `state["macro_articles"]` — a list of `MacroArticle.model_dump(mode="json")` dicts, shape `{"article": {<serialised NewsArticle, published_at as ISO string>}, "mentioned_tickers": ["..."]}`. The key is **always** written (empty list on a quiet tick) so Plan 5 can distinguish "no macro news" from "stage never ran". It is deliberately NOT `temp:`-prefixed — ADK strips `temp:` keys at the invocation boundary and Plan 5's linkage branch reads this key; `mode="json"` ISO-stringification is mandatory because the backtest `DatabaseSessionService` cannot hold `datetime`.

- [ ] **Step 1: Modernise the existing mock fixtures to the provider contract**

The three existing tests mock `get_stock_news` with bare dicts (`{"title": ..., "summary": ..., "published_at": ...}`) that are not valid `NewsArticle` payloads. The real provider contract is `list[NewsArticle]`; the routing step added below validates dicts loudly, so the fixtures must become `NewsArticle`-valid. Keep the extra `"title"` key (Pydantic ignores extras; the existing assertions on `["title"]` and the renderer's dual-key lookup keep passing).

In `tests/unit/agents/analysts/news/test_fetch_agent.py`:

Replace (in `test_fetch_writes_per_ticker_context_keys`):

```python
    fake_news = {
        "AAPL": [{"title": "AAPL beats", "summary": "Strong quarter.", "published_at": "2026-05-21"}],
        "MSFT": [{"title": "MSFT guides up", "summary": "Cloud strong.", "published_at": "2026-05-21"}],
    }
```

with:

```python
    # NewsArticle-valid dicts (ticker/headline/url are required by the model);
    # the redundant "title" key is kept because the renderer and the
    # assertions below use dual-key lookup.
    fake_news = {
        "AAPL": [{
            "ticker": "AAPL", "title": "AAPL beats", "headline": "AAPL beats",
            "summary": "Strong quarter.", "url": "https://example.com/aapl-1",
            "published_at": "2026-05-21",
        }],
        "MSFT": [{
            "ticker": "MSFT", "title": "MSFT guides up", "headline": "MSFT guides up",
            "summary": "Cloud strong.", "url": "https://example.com/msft-1",
            "published_at": "2026-05-21",
        }],
    }
```

Replace (in `test_fetch_degrades_on_provider_error`):

```python
        return [{"title": "AAPL beats", "summary": "ok.", "published_at": "2026-05-21"}]
```

with:

```python
        return [{
            "ticker": "AAPL", "title": "AAPL beats", "headline": "AAPL beats",
            "summary": "ok.", "url": "https://example.com/aapl-1",
            "published_at": "2026-05-21",
        }]
```

Replace (in `test_fetch_writes_aggregate_news_context_for_trace`):

```python
    async def _mock(ticker, as_of=None):
        return [{"title": f"{ticker} hed", "summary": "body", "published_at": "2026-05-21"}]
```

with:

```python
    async def _mock(ticker, as_of=None):
        return [{
            "ticker": ticker, "title": f"{ticker} hed", "headline": f"{ticker} hed",
            "summary": "body", "url": f"https://example.com/{ticker.lower()}-1",
            "published_at": "2026-05-21",
        }]
```

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/news/test_fetch_agent.py -v`
Expected: all 3 existing tests still PASS (the current agent passes dicts straight through, so richer dicts change nothing yet).

- [ ] **Step 2: Write the failing macro-emission tests**

Append to `tests/unit/agents/analysts/news/test_fetch_agent.py`:

```python
# ---------------------------------------------------------------------------
# macro_articles emission (Phase 14 Plan 4)
# ---------------------------------------------------------------------------

from types import SimpleNamespace


def _fixture_universe():
    """Deterministic watchlist universe for macro-routing tests.

    Returns
    -------
    list[dict[str, str]]
        ``{"symbol", "name"}`` entries mirroring get_watchlist_with_names.
    """
    return [
        {"symbol": "AAPL", "name": "Apple"},
        {"symbol": "MSFT", "name": "Microsoft"},
        {"symbol": "NVDA", "name": "Nvidia"},
    ]


def _fixture_analysts_config():
    """Minimal stand-in for get_analysts_config() exposing the roundup knob.

    Returns
    -------
    SimpleNamespace
        Object with ``.news.roundup_company_threshold`` set to 3.
    """
    return SimpleNamespace(news=SimpleNamespace(roundup_company_threshold=3))


@pytest.mark.asyncio
async def test_macro_articles_key_emitted_with_roundup():
    """A roundup ARRIVES in state['macro_articles'], tagged and ISO-serialised."""

    roundup = {
        "ticker": "AAPL",
        "headline": "Apple, Microsoft and Nvidia lead a broad market rally",
        "summary": "Megacaps drove the index higher.",
        "url": "https://example.com/roundup-1",
        "published_at": "2026-05-21T14:00:00",
    }
    specific = {
        "ticker": "AAPL",
        "headline": "Apple ships new iPhone",
        "summary": "Launch day.",
        "url": "https://example.com/aapl-2",
        "published_at": "2026-05-21T15:00:00",
    }

    async def _mock(ticker, as_of=None):
        return [roundup, specific] if ticker == "AAPL" else []

    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test",
        state={"tickers": ["AAPL", "MSFT", "NVDA"], "as_of": datetime(2026, 5, 21, 16, 0)},
        session_id="t1",
    )
    agent = NewsFetchAgent(name="NewsFetch")
    ctx = InvocationContext(session_service=svc, session=session,
                            invocation_id="inv-1", agent=agent)

    with patch("agents.analysts.news.fetch_agent.get_stock_news", _mock), \
         patch("agents.analysts.news.fetch_agent.get_watchlist_with_names",
               _fixture_universe), \
         patch("agents.analysts.news.fetch_agent.get_analysts_config",
               _fixture_analysts_config):
        events = [ev async for ev in agent.run_async(ctx)]

    sd = events[0].actions.state_delta

    # Key is present, NOT temp:-prefixed, and carries exactly the roundup.
    assert "macro_articles" in sd
    macro = sd["macro_articles"]
    assert len(macro) == 1
    assert macro[0]["article"]["headline"] == roundup["headline"]
    assert macro[0]["mentioned_tickers"] == ["AAPL", "MSFT", "NVDA"]

    # ISO-stringified datetimes — the DatabaseSessionService contract.
    assert isinstance(macro[0]["article"]["published_at"], str)

    # The company-specific article did not leak into the macro stream.
    assert all(m["article"]["url"] != specific["url"] for m in macro)


@pytest.mark.asyncio
async def test_macro_articles_key_present_when_empty():
    """A quiet tick still writes macro_articles == [] (key presence contract)."""

    async def _mock(ticker, as_of=None):
        return [{
            "ticker": ticker, "headline": f"{ticker} quarterly note",
            "summary": "", "url": f"https://example.com/{ticker.lower()}-q",
            "published_at": "2026-05-21T14:00:00",
        }] if ticker == "AAPL" else []

    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test",
        state={"tickers": ["AAPL", "MSFT", "NVDA"], "as_of": datetime(2026, 5, 21)},
        session_id="t1",
    )
    agent = NewsFetchAgent(name="NewsFetch")
    ctx = InvocationContext(session_service=svc, session=session,
                            invocation_id="inv-1", agent=agent)

    with patch("agents.analysts.news.fetch_agent.get_stock_news", _mock), \
         patch("agents.analysts.news.fetch_agent.get_watchlist_with_names",
               _fixture_universe), \
         patch("agents.analysts.news.fetch_agent.get_analysts_config",
               _fixture_analysts_config):
        events = [ev async for ev in agent.run_async(ctx)]

    sd = events[0].actions.state_delta

    assert sd["macro_articles"] == []
```

Note: `"AAPL quarterly note"` does not contain "aapl"/"apple"? It contains the ticker text "AAPL" — lower-cased to "aapl", which the symbol term matches, so it routes to `company["AAPL"]`, leaving macro empty. That is the point of the second test.

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/news/test_fetch_agent.py -v`
Expected: the two new tests FAIL with `KeyError: 'macro_articles'` (or an assertion on key presence); the three modernised tests still PASS.

- [ ] **Step 4: Implement the emission in fetch_agent.py**

Three edits to `src/agents/analysts/news/fetch_agent.py`:

(a) Extend the import block. After the existing line `from agents.analysts.news.fetch import _build_ticker_news_context`, add:

```python
from agents.analysts.news.router import route_articles
from config.analysts import get_analysts_config
from data.models import NewsArticle
```

(b) Inside `_run_async_impl`, replace:

```python
        news_data: dict[str, dict] = {}
        per_ticker_blocks: dict[str, str] = {}

        for ticker in tickers:
            try:
                articles = await get_stock_news(ticker, as_of=as_of)
            except Exception as exc:  # noqa: BLE001 — degrade gracefully per ticker
                _LOGGER.warning("news fetch failed for %s: %s", ticker, exc)
                articles = []
```

with:

```python
        news_data: dict[str, dict] = {}
        per_ticker_blocks: dict[str, str] = {}

        # Pooled model-typed articles across every feed — input to the
        # company/macro specificity router (Phase 14 Plan 4).
        pooled: list[NewsArticle] = []

        for ticker in tickers:
            try:
                articles = await get_stock_news(ticker, as_of=as_of)
            except Exception as exc:  # noqa: BLE001 — degrade gracefully per ticker
                _LOGGER.warning("news fetch failed for %s: %s", ticker, exc)
                articles = []

            # Coerce to NewsArticle for the router pool.  The provider
            # contract is list[NewsArticle]; dict payloads (cache layers,
            # test doubles) are validated LOUDLY — a malformed article is a
            # contract violation and must raise, never silently vanish from
            # the macro stream (spec §7).
            for raw in articles:
                pooled.append(
                    raw if isinstance(raw, NewsArticle)
                    else NewsArticle.model_validate(raw)
                )
```

(c) After the aggregate-context block (the statement assigning `delta["temp:news_context"] = ...`) and before the `trace_maybe(...)` call, insert:

```python
        # ── Macro-stream routing (Phase 14 Plan 4) ─────────────────────────
        # Split the pooled feeds into company-specific vs macro (roundup /
        # market-summary) streams.  Only the macro side is emitted here —
        # Plan 5's linkage branch consumes it; Plan 3's rebuilt fetch flow
        # takes over the .company side.  The key is written on every tick
        # (empty list on a quiet tick) so downstream can distinguish "no
        # macro news" from "stage never ran".  It is deliberately NOT
        # temp:-prefixed, and model_dump(mode="json") ISO-stringifies the
        # datetimes — the backtest DatabaseSessionService cannot hold
        # datetime objects.
        if tickers:
            routed = route_articles(
                pooled,
                tickers,
                company_names=watchlist_names,
                roundup_threshold=(
                    get_analysts_config().news.roundup_company_threshold
                ),
            )
            delta["macro_articles"] = [
                ma.model_dump(mode="json") for ma in routed.macro
            ]
        else:
            # Degenerate tick with no tickers — nothing to route, but the
            # key contract still holds.
            delta["macro_articles"] = []
```

(d) Update the module docstring's "Yielded keys" list — add one line after the `temp:news_context` bullet:

```python
  - ``macro_articles`` — serialised MacroArticle dicts (roundups / market
    summaries routed off the per-ticker streams; Phase 14 Plan 4).  NOT
    temp:-prefixed — persisted for the linkage branch (Plan 5).
```

- [ ] **Step 5: Run the full news test package to verify everything passes**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/news/ -v`
Expected: all tests PASS (router, fetch, dedup/recency, fetch_agent including the two new macro tests, joiner, prompts).

- [ ] **Step 6: Commit**

```bash
git add src/agents/analysts/news/fetch_agent.py tests/unit/agents/analysts/news/test_fetch_agent.py
git commit -m "feat(news): NewsFetchAgent emits macro_articles session-state key

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Golden-cache news refetch for the target windows + positive-signal verification

**Files:**
- Create: `docs/Phase14-analyst-refactor/plan4-news-refetch-results.md` (results record — topic-keyed filename, per docs convention)
- No source changes — this task runs `scripts/backtest_fetch.py` (the caps-aware version from Plan 2, Task 3) and verifies the routed macro stream against the refetched caches.

**Interfaces:**
- Consumes: `scripts.backtest_fetch --refetch-domain news` (existing CLI; the fetcher clears each ticker's stale news rows before re-writing, so the refetched state is exactly one fresh pull at `defaults.news_backfill_limit`); `route_articles` (Plan 2).
- Produces: refetched `backtests/iran-conflict-2026-02/store.sqlite` and `backtests/baseline-2025-09/store.sqlite` news tables containing the roundup articles the linkage eval windows need, plus a written before/after record.

**Operator notes (read before Step 2):**
- The refetch **replaces** each ticker's cached news rows (the fetcher deletes stale rows for a refetched domain before re-writing). House rule: destructive operations need the user's explicit go-ahead — show the exact commands from Step 2 and wait for "go" before running them.
- Requires `FINNHUB_API_KEY` in the environment and ~10–20 minutes per window (rate-limited at 50 calls/min shared across chunked pulls).
- Finnhub `/company-news` retains roughly one year. `baseline-2025-09` sits near that margin from a mid-2026 vantage — the spec (§9) accepts possible thinning, but it must be **visible**: Step 1 records before-counts and Step 4 compares.
- `long-baseline-2025` is deliberately excluded here: its cache is pending the separate revenue-concept refetch (see MEMORY), and Plan 1 owns that window's refresh sequencing.

- [ ] **Step 1: Record before-counts for both windows**

```bash
PYTHONPATH=src .venv/bin/python - <<'EOF'
"""Record pre-refetch news-table shape for the Plan 4 target windows."""
import sqlite3

for window in ("iran-conflict-2026-02", "baseline-2025-09"):
    db = sqlite3.connect(f"backtests/{window}/store.sqlite")

    total, tickers = db.execute(
        "SELECT COUNT(*), COUNT(DISTINCT ticker) FROM news_articles"
    ).fetchone()

    # Cross-ticker shared URLs are the deterministic roundup proxy —
    # Finnhub staples one roundup story onto many feeds under one URL.
    shared = db.execute(
        "SELECT COUNT(*) FROM (SELECT url FROM news_articles "
        "GROUP BY url HAVING COUNT(DISTINCT ticker) >= 2)"
    ).fetchone()[0]

    print(f"BEFORE {window}: rows={total} tickers={tickers} cross_ticker_urls={shared}")

    for t, n in db.execute(
        "SELECT ticker, COUNT(*) FROM news_articles GROUP BY ticker ORDER BY ticker"
    ):
        print(f"  {t}: {n}")
EOF
```

Expected: per-window totals print without error. Save the output — Step 5 records it.

- [ ] **Step 2: Refetch the news domain for both windows (after explicit user go-ahead)**

```bash
PYTHONPATH=src .venv/bin/python -m scripts.backtest_fetch --window iran-conflict-2026-02 --refetch-domain news
PYTHONPATH=src .venv/bin/python -m scripts.backtest_fetch --window baseline-2025-09 --refetch-domain news
```

Expected: per-ticker `refetch news/<TICKER> — cleared N stale row(s) before re-write` log lines followed by successful fill lines; the run ends with `Cache fill complete.` and no `status='error'` rows for the news domain. If Finnhub truncation warnings (`>= truncation threshold 240`) appear for specific chunks, note them in the results file — they flag possible partial weeks, not failures.

- [ ] **Step 3: Verify roundups are present (positive signal, not absence of error)**

```bash
PYTHONPATH=src .venv/bin/python - <<'EOF'
"""Post-refetch check: roundup articles must be PRESENT in both caches."""
import sqlite3

for window in ("iran-conflict-2026-02", "baseline-2025-09"):
    db = sqlite3.connect(f"backtests/{window}/store.sqlite")

    total, tickers = db.execute(
        "SELECT COUNT(*), COUNT(DISTINCT ticker) FROM news_articles"
    ).fetchone()

    shared = db.execute(
        "SELECT COUNT(*) FROM (SELECT url FROM news_articles "
        "GROUP BY url HAVING COUNT(DISTINCT ticker) >= 2)"
    ).fetchone()[0]

    print(f"AFTER {window}: rows={total} tickers={tickers} cross_ticker_urls={shared}")

    # Loud assertion: a refetched window with zero cross-ticker URLs means
    # the roundups the macro stream depends on are absent — fail, don't shrug.
    assert shared > 0, f"{window}: no cross-ticker URLs — roundups absent, refetch failed"
EOF
```

Expected: both windows print non-zero `cross_ticker_urls` and the assertions pass. Compare per-window `rows` against Step 1: `iran-conflict-2026-02` should hold steady or grow; if `baseline-2025-09` shrank materially (> 20 % on any ticker), record the thinning per the retention caveat — it bounds Plan 3/5 eval power on that window but does not block this plan.

- [ ] **Step 4: Router smoke over a refetched cache — the macro stream must be non-empty**

```bash
PYTHONPATH=src .venv/bin/python - <<'EOF'
"""Route the entire refetched iran-conflict-2026-02 news table and assert a
non-empty macro stream — the end-to-end positive-signal check for Plan 4."""
import sqlite3
from datetime import datetime

from agents.analysts.news.router import route_articles
from config.analysts import get_analysts_config
from data.models import NewsArticle
from orchestrator.stock_picker import get_watchlist_with_names

universe  = get_watchlist_with_names()
watchlist = [e["symbol"] for e in universe]
names     = {e["symbol"]: e["name"] for e in universe}
threshold = get_analysts_config().news.roundup_company_threshold

db   = sqlite3.connect("backtests/iran-conflict-2026-02/store.sqlite")
rows = db.execute(
    "SELECT ticker, headline, summary, url, source, published_at FROM news_articles"
).fetchall()

# Rebuild NewsArticle models from the raw rows; skip tickers no longer on
# the watchlist (watchlist edits since the fill are legitimate).
pooled = [
    NewsArticle(
        ticker=t, headline=h or "", summary=s or "", url=u,
        source=src or "", published_at=datetime.fromisoformat(p),
    )
    for t, h, s, u, src, p in rows
    if t in set(watchlist)
]

routed    = route_articles(pooled, watchlist, company_names=names, roundup_threshold=threshold)
n_company = sum(len(v) for v in routed.company.values())

print(f"pooled={len(pooled)} company={n_company} macro={len(routed.macro)}")

for m in routed.macro[:5]:
    print(f"  MACRO [{','.join(m.mentioned_tickers) or '-'}] {m.article.headline[:80]}")

assert routed.macro, "no macro articles routed — positive-signal check failed"
assert any(m.mentioned_tickers for m in routed.macro), (
    "no macro article carries ticker tags — roundup classification not firing"
)
EOF
```

Expected: prints pooled/company/macro counts with `macro` well above zero, sample macro headlines with ticker tags, and both assertions pass.

- [ ] **Step 5: Record the results**

Create `docs/Phase14-analyst-refactor/plan4-news-refetch-results.md` containing: the refetch date, the two commands run, the Step 1 before-table and Step 3 after-table (rows / cross-ticker URLs per window), any truncation warnings from Step 2, any `baseline-2025-09` thinning observed (with the retention caveat noted), and the Step 4 smoke output (pooled/company/macro counts plus the sample headlines). Keep it factual — numbers and commands, no narrative.

- [ ] **Step 6: Full-suite regression run**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: entire suite PASSES.

Run: `.venv/bin/python -m ruff check src/ scripts/ tests/`
Expected: no findings.

- [ ] **Step 7: Commit the results record**

```bash
git add docs/Phase14-analyst-refactor/plan4-news-refetch-results.md
git commit -m "docs(backtest): record Plan 4 news refetch results for target windows

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-review (completed at plan-writing time)

- **Spec coverage (§6):** macro stream emitted into session state → Task 1 (`macro_articles`, always-written key contract, ISO serialisation); golden-cache refetch so roundups discarded at the old cap margin are present in the eval windows → Task 2; D2 parity (identical endpoint, identical routing, identical caps via Plan 2's fetcher) → Tasks 1–2; D1 (no new providers, no general-news feed) → nothing in this plan touches providers or adds endpoints; §7 loud failures → loud `NewsArticle` validation in the fetch agent, refetch positive-signal assertions; §8 positive-signal integration guard → Task 1 tests + Task 2 Steps 3–4. No LLM calls anywhere.
- **Placeholder scan:** every code step contains complete, runnable code; no TBD/TODO/"similar to Task N".
- **Type consistency:** `route_articles(articles: list[NewsArticle], watchlist: list[str], *, company_names: dict[str, str] | None, roundup_threshold: int) -> RoutedArticles` (Plan 2) is called identically in Task 1's agent and Task 2's smoke; the `macro_articles` payload shape (`{"article": {...}, "mentioned_tickers": [...]}`) matches between Task 1's serialisation and the Plan 5 linkage consumer contract.
- **Ordering:** Task 1 patches the `fetch_agent.py` that Plan 3 rebuilt; the emission-seam anchors are described against that rebuilt agent and must be adapted if its internal structure has shifted.
