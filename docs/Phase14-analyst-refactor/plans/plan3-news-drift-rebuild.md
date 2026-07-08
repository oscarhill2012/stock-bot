# Plan 3 — News Subsystem Rebuild (Ticker-Level Drift)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking. Follow each task in order. Every task is a
> self-contained TDD cycle: write the failing test, watch it fail, implement, watch it
> pass, commit.
> Do not skip the failing-test step. Do not batch commits across tasks. If a step's
> observed behaviour diverges from what the plan predicts, STOP and re-read the
> source file before improvising.

**Spec:** `docs/Phase14-analyst-refactor/specs/analyst-drift-refactor-design.md` §5 (news-rebuild design), plus D1/D2/D4 and §8 (testing).

**Goal:** Rebuild the internals of `src/agents/analysts/news/` so the news analyst
positions for post-news drift instead of reacting to sentiment. Three pillars:

1. A **deterministic embedding staleness pre-filter** (Tetlock textual-similarity
   measure) backed by a new per-run `NewsHistoryStore`, replacing the heuristic
   specificity reranking in `fetch.py`. Only novel articles reach the per-ticker
   LLM in full; previously-seen articles render as headline-only drift context.
2. A **rewritten per-ticker prompt**: surprise classification + drift-window
   positioning (genuine surprise? which direction? how far into the drift window?
   fresh or stale?), emitting a new `horizon_days` field.
3. **Backtest PIT-correctness:** the history store is per-run state, rebuilt from
   the golden cache's news timeline during replay, never persisted across windows.

**What does NOT change:** the branch shape
(`SequentialAgent[NewsFetchAgent, ParallelAgent[per-ticker], NewsJoinerAgent]`),
the `IsolatedFailureWrapper` / `RetryingAgentWrapper` wrappers, the joiner's
ownership of durable `news_verdicts` / `news_evidence` keys, the closed-vocab
`key_factors` mandate, and the news provider (Finnhub `/company-news` only — D1).

---

## Global constraints (apply to every task)

- **British English** everywhere — identifiers, comments, docs, prose.
- **Every function gets a docstring** describing purpose, parameters, and return
  value. Comment non-trivial logic inline. Use blank lines for legibility.
- **Config convention:** new settings go in `config/analysts.json`, are modelled in
  `src/config/analysts.py`, and get a row in `config/README.md` — all in the same
  task. Never hardcode config values in source.
- **Loud failures:** raise rather than degrade to null/empty/neutral. Tests assert
  positive signals (the filtered article IS in the stale bucket), not just absence
  of error. The one sanctioned degradation is the existing per-ticker provider
  fetch failure → empty feed (branch isolation).
- **ADK rules:** every read of `state["as_of"]` goes through `resolve_as_of`;
  datetimes are ISO-stringified before being written to state; never mutate
  `adk_session.state["temp:_*"]` after `create_session`. Per-ticker branches write
  only their own `temp:news_*_<TICKER>` keys; the joiner owns the durable keys.
- **No `max_length` on LLM free-text schema fields** (Vertex pad-toward-cap
  pathology) — prose caps are stated in the prompt only.
- **Test invocation:** `.venv/bin/python -m pytest <path> -v` from the project
  root. Do NOT prepend `cd ... &&` to Bash commands. Lint with
  `.venv/bin/python -m ruff check src/ tests/`.
- **Commits:** one per task, message given in the task. End every commit message
  with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

### Cross-plan interfaces (co-planned — trust, do not shim)

This plan is one of five co-planned Phase 14 plans. Sibling plans land alongside
this one; do **not** write defensive fallbacks for their deliverables.

**This plan CONSUMES (Plan 2 — specificity router):**

```python
# src/agents/analysts/news/router.py  (Plan 2's deliverable — do not create/modify)
def route_articles(articles: list, watchlist: list[str]) -> RoutedArticles: ...

class RoutedArticles:
    company: dict[str, list]   # ticker → company-specific articles (this plan's input)
    macro:   list              # roundup/macro articles (Plan 4's input — NOT consumed here)
```

Until Plan 2 merges, `from agents.analysts.news.router import route_articles` will
fail at import time. That is expected and correct — **do not** add a try/except or
a local stub in `src/`. Unit tests patch `route_articles` at the point of use, so
the test suite for this plan passes independently of Plan 2's merge order. Task 6
(full-suite verification) is the only task that requires Plan 2 to be present.

**This plan PROVIDES (consumed by Plan 5 — the macro/linkage analyst):**

- `src/agents/analysts/news/history.py` —
  `NewsHistoryStore.staleness(namespace, text) -> float` and
  `NewsHistoryStore.record(namespace, article_key, text, published_at)`.
  Namespaces are strings: this plan uses ticker symbols; Plan 5's macro analyst uses `"macro"`.
- The `horizon_days` field on `AnalystVerdict` / `LlmTickerVerdict` in
  `src/contract/evidence.py` is **added by Plan 1** (Task 5) — this plan does not
  define it; its rewritten prompt (Task 4) only **emits** it.
- `config/analysts.json::staleness_similarity_threshold` (top-level — shared by
  Plan 5's macro staleness pass).

---

## Task 1 — `NewsHistoryStore` + `staleness_similarity_threshold` config

**Files:**
- Create: `src/agents/analysts/news/history.py`
- Create: `tests/unit/agents/analysts/news/test_history.py`
- Modify: `src/config/analysts.py`
- Modify: `config/analysts.json`
- Modify: `config/README.md`

**Interface being created (pinned across plans — signatures are fixed):**

```python
class NewsHistoryStore:
    async def staleness(self, namespace: str, text: str) -> float: ...
    async def record(self, namespace: str, article_key: str, text: str,
                     published_at: datetime) -> None: ...
    def has(self, namespace: str, article_key: str) -> bool: ...

def get_news_history_store() -> NewsHistoryStore: ...
def reset_news_history_store() -> None: ...
```

**Steps:**

**2.1 — Write the failing store tests.** Create
`tests/unit/agents/analysts/news/test_history.py`:

```python
"""Unit tests for the per-run NewsHistoryStore (Phase 14 Plan 3).

The store backs the deterministic embedding staleness pre-filter: it holds
one embedding vector per previously-seen article, per namespace, and
answers "how similar is this new text to anything already recorded?".
All embedding calls are stubbed — no network access in unit tests.
"""
from __future__ import annotations

from datetime import datetime

import pytest

import agents.analysts.news.history as history
from agents.analysts.news.history import (
    NewsHistoryStore,
    get_news_history_store,
    reset_news_history_store,
)

_PUBLISHED = datetime(2026, 7, 1, 12, 0)


def _stub_embed_factory(calls: list[str]):
    """Build a deterministic embed stub that logs every text it embeds.

    Vectors are chosen so that the two 'earnings' phrasings are near-parallel
    (cosine ≈ 0.97 — a syndicated rehash) while 'layoffs' is orthogonal to
    both (a genuinely novel story).

    Parameters:
        calls: list mutated in place with each embedded text (call log).

    Returns:
        An async ``embed_fn(text) -> list[float]`` suitable for the store.
    """
    async def _stub(text: str) -> list[float]:
        calls.append(text)
        if "beats on earnings" in text:
            return [1.0, 0.0, 0.0]
        if "tops earnings estimates" in text:
            return [0.97, 0.24, 0.0]
        if "layoffs" in text:
            return [0.0, 1.0, 0.0]
        raise AssertionError(f"unexpected embed text: {text!r}")

    return _stub


@pytest.mark.asyncio
async def test_staleness_is_zero_for_an_empty_namespace():
    """With nothing recorded, everything is maximally novel — and no
    embedding call is spent finding that out."""
    calls: list[str] = []
    store = NewsHistoryStore(embed_fn=_stub_embed_factory(calls))

    similarity = await store.staleness("AAPL", "AAPL beats on earnings")

    assert similarity == 0.0
    assert calls == []          # short-circuit: no embed for an empty namespace


@pytest.mark.asyncio
async def test_staleness_is_high_for_a_recorded_rehash():
    """A paraphrased rehash of a recorded story scores near 1.0."""
    store = NewsHistoryStore(embed_fn=_stub_embed_factory([]))
    await store.record("AAPL", "url-1", "AAPL beats on earnings",
                       published_at=_PUBLISHED)

    similarity = await store.staleness("AAPL", "Apple tops earnings estimates")

    assert similarity > 0.9     # POSITIVE assertion — the rehash IS caught


@pytest.mark.asyncio
async def test_staleness_is_low_for_a_novel_story():
    """An unrelated story scores near 0 against the recorded history."""
    store = NewsHistoryStore(embed_fn=_stub_embed_factory([]))
    await store.record("AAPL", "url-1", "AAPL beats on earnings",
                       published_at=_PUBLISHED)

    similarity = await store.staleness("AAPL", "AAPL layoffs announced")

    assert similarity < 0.1


@pytest.mark.asyncio
async def test_namespaces_are_isolated():
    """AAPL's history must never make an MSFT article look stale."""
    store = NewsHistoryStore(embed_fn=_stub_embed_factory([]))
    await store.record("AAPL", "url-1", "AAPL beats on earnings",
                       published_at=_PUBLISHED)

    similarity = await store.staleness("MSFT", "AAPL beats on earnings")

    assert similarity == 0.0    # MSFT namespace is empty


@pytest.mark.asyncio
async def test_has_tracks_recorded_article_keys_per_namespace():
    """has() gives an exact-identity short-circuit, scoped to the namespace."""
    store = NewsHistoryStore(embed_fn=_stub_embed_factory([]))
    await store.record("AAPL", "url-1", "AAPL beats on earnings",
                       published_at=_PUBLISHED)

    assert store.has("AAPL", "url-1") is True
    assert store.has("AAPL", "url-2") is False
    assert store.has("MSFT", "url-1") is False


@pytest.mark.asyncio
async def test_recording_the_same_key_twice_does_not_reembed():
    """record() is idempotent per (namespace, key) — one embed per article."""
    calls: list[str] = []
    store = NewsHistoryStore(embed_fn=_stub_embed_factory(calls))

    await store.record("AAPL", "url-1", "AAPL beats on earnings",
                       published_at=_PUBLISHED)
    await store.record("AAPL", "url-1", "AAPL beats on earnings",
                       published_at=_PUBLISHED)

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_embedding_failure_raises_loudly():
    """An embedding outage must fail the run, never silently mark articles
    fresh (or stale) — silent degradation is this project's banned bug class."""
    async def _broken(text: str) -> list[float]:
        raise RuntimeError("embedding endpoint down")

    store = NewsHistoryStore(embed_fn=_broken)

    with pytest.raises(RuntimeError, match="embedding endpoint down"):
        await store.record("AAPL", "url-1", "AAPL beats on earnings",
                           published_at=_PUBLISHED)


def test_module_singleton_reset_swaps_the_instance():
    """reset_news_history_store() must hand back a brand-new empty store —
    the backtest driver relies on this for per-run PIT isolation."""
    first = get_news_history_store()
    reset_news_history_store()
    second = get_news_history_store()

    assert second is not first
    # Reset again so this test leaves no shared state behind.
    reset_news_history_store()
```

Run and watch it fail (`ModuleNotFoundError`):

```bash
.venv/bin/python -m pytest tests/unit/agents/analysts/news/test_history.py -v
```

**2.2 — Create `src/agents/analysts/news/history.py`:**

```python
"""Per-run news-history store backing the staleness pre-filter (Phase 14).

Implements the Tetlock textual-similarity measure deterministically: each
article seen by the news subsystem is embedded once and recorded under a
namespace; new candidate text is scored by its maximum cosine similarity
against everything already recorded in that namespace.  Scores at or above
``staleness_similarity_threshold`` (config/analysts.json) mark an article
as a stale rehash rather than a fresh surprise.

Namespaces are plain strings.  This plan uses ticker symbols (company
news); Plan 5's macro analyst uses the reserved namespace ``"macro"``.

Lifecycle — PIT correctness (spec D2):
    The store is strictly PER-RUN state.  Live trading accumulates it
    within a single process run; the backtest driver calls
    ``reset_news_history_store()`` at the start of every window replay so
    the store is rebuilt tick-by-tick from the golden cache's news
    timeline.  It is NEVER persisted to disk and never survives across
    windows — an article from window B must not look "previously seen"
    because window A mentioned it.

Failure policy:
    Embedding failures RAISE.  A store that silently treats articles as
    fresh (or stale) when the embedding backend is down is exactly the
    silent-degradation bug class this project bans.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from agents.memory.embeddings import cosine_similarity, embed

# Type alias for the injectable embedding function (tests supply stubs).
EmbedFn = Callable[[str], Awaitable[list[float]]]


@dataclass
class _HistoryEntry:
    """One recorded article: its identity key, embedding, and timestamp.

    Attributes:
        article_key:  stable identity (URL or content digest) — dedup handle.
        vector:       embedding of the article's headline + capped summary.
        published_at: publication time, kept for future window-decay logic.
    """

    article_key: str
    vector: list[float]
    published_at: datetime


class NewsHistoryStore:
    """In-memory, per-run store of article embeddings, keyed by namespace.

    Parameters:
        embed_fn: async ``text -> vector`` function.  Defaults to the shared
                  memory-subsystem embedder; tests inject deterministic stubs.
    """

    def __init__(self, embed_fn: EmbedFn = embed) -> None:
        """Initialise an empty store with the given embedding function."""
        self._embed_fn: EmbedFn = embed_fn

        # namespace → ordered list of recorded entries.
        self._entries: dict[str, list[_HistoryEntry]] = {}

        # namespace → set of recorded article keys (O(1) identity checks).
        self._keys: dict[str, set[str]] = {}

    def has(self, namespace: str, article_key: str) -> bool:
        """Return True if ``article_key`` was already recorded in ``namespace``.

        This is the exact-identity short-circuit: a re-fetched article whose
        key is known needs no embedding call — it is stale by definition.

        Parameters:
            namespace:   history partition (ticker symbol, or "macro").
            article_key: stable identity key from ``fetch.article_key``.

        Returns:
            Whether the key is present in the namespace.
        """
        return article_key in self._keys.get(namespace, set())

    async def staleness(self, namespace: str, text: str) -> float:
        """Score how textually stale ``text`` is within ``namespace``.

        Embeds the candidate text and returns its maximum cosine similarity
        against every entry recorded in the namespace — the deterministic
        Tetlock stale-news measure.  An empty namespace short-circuits to
        0.0 without spending an embedding call.

        Parameters:
            namespace: history partition (ticker symbol, or "macro").
            text:      candidate article text (headline + capped summary).

        Returns:
            Max cosine similarity in [0.0, 1.0]; 0.0 when nothing is recorded.

        Raises:
            Whatever the embedding function raises — failures are loud.
        """
        entries = self._entries.get(namespace)
        if not entries:
            return 0.0

        candidate = await self._embed_fn(text)

        return max(
            cosine_similarity(candidate, entry.vector) for entry in entries
        )

    async def record(
        self,
        namespace: str,
        article_key: str,
        text: str,
        published_at: datetime,
    ) -> None:
        """Embed ``text`` and record it under ``namespace``.

        Idempotent per ``(namespace, article_key)`` — recording a key that
        is already present is a no-op, so each article costs at most one
        embedding call for the lifetime of the run.

        Parameters:
            namespace:    history partition (ticker symbol, or "macro").
            article_key:  stable identity key from ``fetch.article_key``.
            text:         article text to embed (headline + capped summary).
            published_at: the article's publication time.

        Returns:
            None.

        Raises:
            Whatever the embedding function raises — failures are loud.
        """
        if self.has(namespace, article_key):
            return

        vector = await self._embed_fn(text)

        self._entries.setdefault(namespace, []).append(
            _HistoryEntry(
                article_key=article_key,
                vector=vector,
                published_at=published_at,
            )
        )
        self._keys.setdefault(namespace, set()).add(article_key)


# ── Module-level per-run singleton ────────────────────────────────────────
#
# The fetch agent (and Plan 5's macro analyst) share one store per process
# run.  The backtest driver resets it at the start of every window replay.

_STORE: NewsHistoryStore | None = None


def get_news_history_store() -> NewsHistoryStore:
    """Return the process-wide per-run store, creating it on first use.

    Returns:
        The shared ``NewsHistoryStore`` instance for the current run.
    """
    global _STORE
    if _STORE is None:
        _STORE = NewsHistoryStore()
    return _STORE


def reset_news_history_store() -> None:
    """Discard the current store so the next access builds a fresh one.

    Called by the backtest driver before each window replay — history must
    be rebuilt from that window's golden-cache news timeline, never carried
    over from a previous window or run (PIT correctness, spec D2).

    Returns:
        None.
    """
    global _STORE
    _STORE = None
```

**2.3 — Add the config threshold.** In `src/config/analysts.py`, add a top-level
field to `AnalystsConfig` (top-level, not under `NewsCaps`, because Plan 5's
`"macro"` namespace shares the same threshold). Place it alongside the existing
top-level fields (e.g. after `slack_percent`):

```python
    # Phase 14: minimum embedding cosine similarity at which an article is
    # classed as a stale rehash of previously-seen news (Tetlock measure).
    # Shared by the per-ticker news filter and the macro analyst's filter.
    staleness_similarity_threshold: float = Field(ge=0.0, le=1.0, default=0.85)
```

In `config/analysts.json`, add the matching key at the top level of the document
(next to `slack_percent`):

```json
  "staleness_similarity_threshold": 0.85,
```

In `config/README.md`, find the `analysts.json` section's top-level settings
table (`grep -n "slack_percent" config/README.md`) and add a row:

```markdown
| `staleness_similarity_threshold` | float, `0.0`–`1.0` | Minimum embedding cosine similarity at which a news article counts as a stale rehash of previously-seen news (Tetlock textual-similarity measure). Applies to both per-ticker company news and the macro stream. Default `0.85`. |
```

The default of `0.85` deliberately matches the memory subsystem's
`COSINE_THRESHOLD` for repeat detection — the same embedding model, the same
"same story, different words" judgement.

**2.4 — Config round-trip test.** Append to
`tests/unit/config/test_analysts_config.py` (reusing its existing imports):

```python
def test_staleness_similarity_threshold_loads_from_config():
    """The committed config file must carry the Phase 14 staleness threshold."""
    cfg = load_analysts_config()

    assert 0.0 <= cfg.staleness_similarity_threshold <= 1.0
    assert cfg.staleness_similarity_threshold == 0.85
```

(If that file's loader helper takes an explicit path argument in its other tests,
follow the same pattern here.)

**2.5 — Verify and commit.**

```bash
.venv/bin/python -m pytest tests/unit/agents/analysts/news/test_history.py tests/unit/config/ -v
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m ruff check src/ tests/
git add -A
git commit -m "feat(news): add per-run NewsHistoryStore and staleness threshold config

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 2 — Staleness partition helpers in `fetch.py`

**Files:**
- Modify: `src/agents/analysts/news/fetch.py` (additions only in this task)
- Create: `tests/unit/agents/analysts/news/test_staleness_filter.py`

**Interface being added:**

```python
def article_key(article: object) -> str: ...

async def partition_articles_by_staleness(
    ticker: str, articles: list, *,
    store: NewsHistoryStore, threshold: float,
) -> tuple[list, list]:   # (fresh, stale) — both oldest-first
```

**Steps:**

**3.1 — Write the failing tests.** Create
`tests/unit/agents/analysts/news/test_staleness_filter.py`:

```python
"""Tests for the deterministic embedding staleness pre-filter (Phase 14).

Covers ``article_key`` (stable identity) and
``partition_articles_by_staleness`` (fresh/stale split), which together
replace the deleted heuristic specificity re-ranker.  Embeddings are
stubbed with deterministic vectors — no network access.
"""
from __future__ import annotations

import pytest

from agents.analysts.news.fetch import article_key, partition_articles_by_staleness
from agents.analysts.news.history import NewsHistoryStore


def _stub_embed_factory(calls: list[str]):
    """Deterministic embed stub — see test_history.py for the vector design.

    Parameters:
        calls: list mutated in place with each embedded text.

    Returns:
        An async ``embed_fn(text) -> list[float]``.
    """
    async def _stub(text: str) -> list[float]:
        calls.append(text)
        if "beats on earnings" in text:
            return [1.0, 0.0, 0.0]
        if "tops earnings estimates" in text:
            return [0.97, 0.24, 0.0]
        if "layoffs" in text:
            return [0.0, 1.0, 0.0]
        raise AssertionError(f"unexpected embed text: {text!r}")

    return _stub


def _article(title: str, url: str, published: str) -> dict:
    """Build a serialised article dict in the provider shape."""
    return {"title": title, "summary": "", "url": url, "published_at": published}


@pytest.mark.asyncio
async def test_known_stale_rehash_is_filtered_within_one_tick():
    """A same-tick paraphrase of an earlier article lands in the stale
    bucket; the original survives as fresh.  POSITIVE assertions both ways."""
    store = NewsHistoryStore(embed_fn=_stub_embed_factory([]))
    original = _article("AAPL beats on earnings",
                        "https://news/a1", "2026-07-01T12:00:00")
    rehash = _article("Apple tops earnings estimates",
                      "https://news/b2", "2026-07-01T13:00:00")

    # Deliberately pass the rehash FIRST — oldest-first processing must
    # still judge the original before the rehash.
    fresh, stale = await partition_articles_by_staleness(
        "AAPL", [rehash, original], store=store, threshold=0.85,
    )

    assert fresh == [original]
    assert stale == [rehash]


@pytest.mark.asyncio
async def test_article_is_stale_on_the_next_tick_without_reembedding():
    """A re-fetched article is stale by identity — no second embed call."""
    calls: list[str] = []
    store = NewsHistoryStore(embed_fn=_stub_embed_factory(calls))
    art = _article("AAPL beats on earnings",
                   "https://news/a1", "2026-07-01T12:00:00")

    fresh_1, stale_1 = await partition_articles_by_staleness(
        "AAPL", [art], store=store, threshold=0.85,
    )
    assert fresh_1 == [art] and stale_1 == []

    embeds_after_first_tick = len(calls)

    fresh_2, stale_2 = await partition_articles_by_staleness(
        "AAPL", [art], store=store, threshold=0.85,
    )

    assert stale_2 == [art] and fresh_2 == []
    assert len(calls) == embeds_after_first_tick    # has() short-circuit held


@pytest.mark.asyncio
async def test_genuinely_novel_stories_stay_fresh():
    """Dissimilar stories all pass, returned oldest-first."""
    store = NewsHistoryStore(embed_fn=_stub_embed_factory([]))
    earnings = _article("AAPL beats on earnings",
                        "https://news/a1", "2026-07-01T12:00:00")
    layoffs = _article("AAPL layoffs announced",
                       "https://news/a2", "2026-07-02T09:00:00")

    fresh, stale = await partition_articles_by_staleness(
        "AAPL", [layoffs, earnings], store=store, threshold=0.85,
    )

    assert fresh == [earnings, layoffs]
    assert stale == []


@pytest.mark.asyncio
async def test_partition_namespaces_do_not_cross_contaminate():
    """AAPL history must not make the same story stale for MSFT."""
    store = NewsHistoryStore(embed_fn=_stub_embed_factory([]))
    art = _article("AAPL beats on earnings",
                   "https://news/a1", "2026-07-01T12:00:00")

    await partition_articles_by_staleness("AAPL", [art], store=store, threshold=0.85)

    # Different URL so the identity short-circuit cannot fire either.
    msft_copy = _article("AAPL beats on earnings",
                         "https://news/z9", "2026-07-01T12:00:00")
    fresh, stale = await partition_articles_by_staleness(
        "MSFT", [msft_copy], store=store, threshold=0.85,
    )

    assert fresh == [msft_copy] and stale == []


@pytest.mark.asyncio
async def test_embedding_failure_propagates():
    """Embedding outages fail the partition loudly — no silent 'fresh'."""
    async def _broken(text: str) -> list[float]:
        raise RuntimeError("embedding endpoint down")

    store = NewsHistoryStore(embed_fn=_broken)
    art = _article("AAPL beats on earnings",
                   "https://news/a1", "2026-07-01T12:00:00")

    with pytest.raises(RuntimeError, match="embedding endpoint down"):
        await partition_articles_by_staleness(
            "AAPL", [art], store=store, threshold=0.85,
        )


def test_article_key_prefers_url_and_falls_back_to_a_digest():
    """URL is the identity when present; otherwise headline+timestamp digest
    (so two same-headline stories on different days do not collide)."""
    with_url = {"title": "T", "url": "https://news/x1", "published_at": "2026-07-01"}
    assert article_key(with_url) == "https://news/x1"

    no_url_day1 = {"title": "Same headline", "published_at": "2026-07-01"}
    no_url_day2 = {"title": "Same headline", "published_at": "2026-07-02"}

    assert article_key(no_url_day1).startswith("hash:")
    assert article_key(no_url_day1) != article_key(no_url_day2)
    assert article_key(no_url_day1) == article_key(dict(no_url_day1))   # stable
```

Run and watch it fail (`ImportError` on the two new names):

```bash
.venv/bin/python -m pytest tests/unit/agents/analysts/news/test_staleness_filter.py -v
```

**3.2 — Add the helpers to `src/agents/analysts/news/fetch.py`.** Add
`from hashlib import blake2b` and
`from agents.analysts.news.history import NewsHistoryStore` to the imports
(no cycle: `history` imports only from `agents.memory`). Then add:

```python
# Sort anchor for articles whose publication time cannot be parsed — epoch
# zero sorts them first so they can never displace a datable original.
_EPOCH_ZERO = datetime(1970, 1, 1)


def _article_fields(article: object) -> tuple[str, str, object]:
    """Extract ``(headline, summary, raw_published)`` from an article.

    Centralises the dual dict/model access pattern used throughout this
    module so every consumer reads fields identically.  ``raw_published``
    is returned unparsed (str, datetime, or None) — callers hand it to
    ``_parse_published`` when they need a datetime.

    Parameters:
        article: serialised dict or ``NewsArticle``-shaped object.

    Returns:
        ``(headline, stripped_summary, raw_published)``.
    """
    if isinstance(article, dict):
        headline = article.get("title") or article.get("headline") or ""
        summary = (article.get("summary") or "").strip()
        published = article.get("published_at") or article.get("date")
    else:
        headline = (
            getattr(article, "title", None)
            or getattr(article, "headline", None)
            or ""
        )
        summary = (getattr(article, "summary", None) or "").strip()
        published = (
            getattr(article, "published_at", None)
            or getattr(article, "date", None)
        )

    return str(headline), str(summary), published


def article_key(article: object) -> str:
    """Stable identity key for one article across ticks and re-fetches.

    Prefers the provider URL (unique per story, stable across fetches).
    URL-less articles fall back to a digest of headline + raw timestamp so
    two same-headline stories on different days do not collide.

    Parameters:
        article: serialised dict or ``NewsArticle``-shaped object.

    Returns:
        The URL, or ``"hash:<blake2b-digest>"`` when no URL is present.
    """
    headline, _summary, raw_published = _article_fields(article)

    if isinstance(article, dict):
        url = article.get("url") or ""
    else:
        url = getattr(article, "url", None) or ""

    if url:
        return str(url)

    digest = blake2b(
        f"{headline}|{raw_published}".encode(), digest_size=12,
    ).hexdigest()
    return f"hash:{digest}"


async def partition_articles_by_staleness(
    ticker: str,
    articles: list,
    *,
    store: NewsHistoryStore,
    threshold: float,
) -> tuple[list, list]:
    """Split ``articles`` into (fresh, stale) via the history store.

    This is the deterministic staleness pre-filter that replaced the
    heuristic specificity re-ranker (Phase 14): an article is STALE when it
    was seen on an earlier tick (identity match) or when its text scores at
    or above ``threshold`` cosine similarity against anything previously
    recorded for the ticker (Tetlock rehash measure).  Everything else is
    FRESH — a genuine-surprise candidate for the LLM.

    Articles are processed oldest-first so that, within a single tick, the
    first copy of a syndicated story is judged (and recorded) before its
    rehashes — later copies then measure similar and land in the stale
    bucket.  Every judged article is recorded, including stale ones, so
    the next tick's re-fetch short-circuits on identity without a fresh
    embedding call.

    Parameters:
        ticker:    namespace for the history store (the ticker symbol).
        articles:  serialised article dicts (or model objects) for one ticker.
        store:     the per-run ``NewsHistoryStore``.
        threshold: cosine-similarity cut-off from
                   ``config/analysts.json::staleness_similarity_threshold``.

    Returns:
        ``(fresh, stale)`` — two lists in oldest-first order.

    Raises:
        Whatever the store's embedding function raises — failures are loud.
    """
    caps = _caps()

    def _published_or_epoch(article: object) -> datetime:
        """Sort key: parsed publication time, or epoch zero when unknown."""
        _headline, _summary, raw_published = _article_fields(article)
        return _parse_published(raw_published) or _EPOCH_ZERO

    ordered = sorted(articles, key=_published_or_epoch)

    fresh: list = []
    stale: list = []

    for article in ordered:
        headline, summary, raw_published = _article_fields(article)
        key = article_key(article)

        # Exact-identity short-circuit: seen on an earlier tick means stale
        # by definition — no embedding spend.
        if store.has(ticker, key):
            stale.append(article)
            continue

        # Tetlock measure: embed headline + capped summary, compare against
        # everything previously recorded for this ticker.
        text = f"{headline}. {summary[: caps.max_summary_chars]}".strip()
        similarity = await store.staleness(ticker, text)

        # Record BEFORE classifying so same-tick rehashes compare against
        # this article too.  Stale articles are recorded as well — their
        # key then short-circuits the next tick's re-fetch.
        await store.record(
            ticker,
            key,
            text,
            published_at=_parse_published(raw_published) or _EPOCH_ZERO,
        )

        if similarity >= threshold:
            stale.append(article)
        else:
            fresh.append(article)

    return fresh, stale
```

Note: `_parse_published` in `fetch.py` accepts the raw value shapes the providers
emit; if its current signature is annotated str-only, widen the annotation to
`object` (its body already handles datetime pass-through and unparseable input —
verify, and extend only if a test fails).

**3.3 — Verify and commit.**

```bash
.venv/bin/python -m pytest tests/unit/agents/analysts/news/ -v
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m ruff check src/ tests/
git add -A
git commit -m "feat(news): add article_key and embedding staleness partition to fetch helpers

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 3 — Rebuild the fetch flow (router + staleness filter + two-section context)

This is the core rewiring task: `NewsFetchAgent` now routes the fetched union
through Plan 2's `route_articles`, staleness-partitions each ticker's articles,
and renders a two-section context (fresh in full, previously-seen as headline
one-liners). The heuristic reranker is deleted outright.

**Files:**
- Modify: `src/agents/analysts/news/fetch.py` (delete reranker, rewrite renderer)
- Modify: `src/agents/analysts/news/fetch_agent.py` (full rewrite)
- Modify: `src/agents/analysts/news/per_ticker.py` (cache hash inputs)
- Modify: `src/config/analysts.py`, `config/analysts.json`, `config/README.md`
- Rewrite: `tests/unit/agents/analysts/news/test_fetch.py`
- Rewrite: `tests/unit/agents/analysts/news/test_fetch_agent.py`
- Modify (fixture sweep): tests constructing `NewsCaps`

**Deleted symbols (and their tests):** `_score_article_specificity`,
`_rerank_articles`, `_build_company_terms`, `_count_roundup_companies`,
`_watchlist_universe` (delete this one only if, after the rewrite, `grep -rn
"_watchlist_universe" src/ tests/` shows no remaining callers — the router now
receives the watchlist from state). `roundup_company_threshold` stays in config —
Plan 2's router consumes it.

**Kept symbols:** `_dedup_and_sort_articles`, `_normalise_title`,
`_title_similarity`, `_parse_published`, `_caps()`, `_MISSING_SENTINEL` — the
title-level dedup is a cheap exact/near-exact hygiene pass that runs BEFORE
embeddings are spent; the staleness filter then catches the paraphrased rehashes
the title pass cannot.

**Steps:**

**4.1 — Config: replace the generic-articles cap with a stale-headlines cap.**
With routing owned by Plan 2, the old "generic articles" concept (roundups
stapled onto a ticker's feed) no longer reaches the per-ticker context, so
`max_generic_articles_per_ticker` is dead. The new knob caps the
headline-only PREVIOUSLY SEEN section.

In `src/config/analysts.py` (`NewsCaps`), replace the
`max_generic_articles_per_ticker` field with:

```python
    # Cap on the headline-only PREVIOUSLY SEEN section of the per-ticker
    # context — stale drift-context lines are cheap but not free.
    max_stale_headlines_per_ticker: int = Field(ge=0, le=100, default=10)
```

(update the class docstring's field list to match, and remove any mention of
generic articles). In `config/analysts.json`, inside the `news` block, replace

```json
    "max_generic_articles_per_ticker": 10,
```

with

```json
    "max_stale_headlines_per_ticker": 10,
```

In `config/README.md`, in the `news` caps table, replace the
`max_generic_articles_per_ticker` row with:

```markdown
| `news.max_stale_headlines_per_ticker` | int, `0`–`100` | Maximum headline-only lines rendered in the per-ticker context's PREVIOUSLY SEEN section (stale drift context). Default `10`. |
```

and update the `news.max_articles_per_ticker` row's description to read
"Maximum FRESH (novel) articles rendered in full per ticker." Sweep test
fixtures now: `grep -rn "max_generic_articles_per_ticker" src/ tests/ config/`
must come back empty by the end of this step — in any test building a `NewsCaps`
or a news-config dict, swap the key for `max_stale_headlines_per_ticker`.

**4.2 — Write the failing renderer tests.** Replace the entire contents of
`tests/unit/agents/analysts/news/test_fetch.py` with:

```python
"""Unit tests for the news fetch helpers — Phase 14 two-section renderer.

The heuristic specificity re-ranker (``_score_article_specificity`` /
``_rerank_articles``) was replaced by the embedding staleness pre-filter
(Plan 3) and the specificity router (Plan 2); its tests were deleted with
it.  Title-dedup and recency-sort tests live in ``test_dedup_recency.py``;
staleness-partition tests live in ``test_staleness_filter.py``.  This file
covers the two-section context renderer.
"""
from __future__ import annotations

from datetime import datetime

from agents.analysts.news.fetch import _build_ticker_news_context
from config.analysts import get_analysts_config

_AS_OF = datetime(2026, 7, 6, 14, 0)


def _article(title: str, summary: str, published: str,
             url: str = "https://news/x1") -> dict:
    """Build a serialised article dict in the provider shape."""
    return {"title": title, "summary": summary,
            "published_at": published, "url": url}


def test_fresh_articles_render_with_headline_summary_and_age():
    """Fresh (novel) articles are the surprise candidates — full render."""
    block = _build_ticker_news_context(
        "AAPL",
        [_article("AAPL beats on earnings",
                  "Strong quarter across all segments.",
                  "2026-07-05T12:00:00")],
        [],
        as_of=_AS_OF,
    )

    assert "FRESH ARTICLES" in block
    assert "AAPL beats on earnings" in block
    assert "Strong quarter across all segments." in block
    assert "1d ago" in block


def test_stale_articles_render_headline_only():
    """Previously-seen articles are drift context — headline + age, NO summary."""
    block = _build_ticker_news_context(
        "AAPL",
        [],
        [_article("AAPL beats on earnings",
                  "This summary must NOT render.",
                  "2026-07-02T12:00:00")],
        as_of=_AS_OF,
    )

    assert "PREVIOUSLY SEEN" in block
    assert "AAPL beats on earnings" in block
    assert "This summary must NOT render." not in block
    assert "4d ago" in block


def test_empty_sections_render_explicit_placeholders():
    """Both sections empty → the no-news placeholder; one section empty →
    an explicit (none) marker so the LLM never guesses."""
    empty = _build_ticker_news_context("AAPL", [], [], as_of=_AS_OF)
    assert "(no news available)" in empty
    assert "FRESH ARTICLES" not in empty

    fresh_only = _build_ticker_news_context(
        "AAPL",
        [_article("Hed", "Sum.", "2026-07-05T12:00:00")],
        [],
        as_of=_AS_OF,
    )
    assert "(none)" in fresh_only              # stale section's placeholder


def test_summaries_are_truncated_to_the_configured_cap():
    """Per-article summary text is capped at news.max_summary_chars."""
    cap = get_analysts_config().news.max_summary_chars
    block = _build_ticker_news_context(
        "AAPL",
        [_article("Hed", "x" * (cap + 500), "2026-07-05T12:00:00")],
        [],
        as_of=_AS_OF,
    )

    assert "x" * cap in block
    assert "x" * (cap + 1) not in block


def test_as_of_anchor_is_rendered():
    """The block carries the tick date so ages are self-explanatory."""
    block = _build_ticker_news_context("AAPL", [], [], as_of=_AS_OF)
    assert "As of: 2026-07-06" in block


def test_heuristic_reranker_is_fully_deleted():
    """The old scoring path must be gone, not left dormant."""
    import agents.analysts.news.fetch as fetch_mod

    for name in ("_score_article_specificity", "_rerank_articles",
                 "_build_company_terms", "_count_roundup_companies"):
        assert not hasattr(fetch_mod, name), f"{name} should have been deleted"
```

Run and watch it fail (old renderer signature / reranker still present):

```bash
.venv/bin/python -m pytest tests/unit/agents/analysts/news/test_fetch.py -v
```

**4.3 — Rewrite the renderer and delete the reranker in `fetch.py`.**

Delete `_score_article_specificity`, `_rerank_articles`, `_build_company_terms`,
`_count_roundup_companies` (and `_watchlist_universe` per the caller check in the
task header), together with any module constants used only by them. Add the two
small helpers, then replace `_build_ticker_news_context` with the two-section
version:

```python
def _freshest_first(articles: list) -> list:
    """Sort articles freshest-first by parsed publication time.

    Undated articles (epoch-zero sort anchor) sink to the end — they must
    never displace a datable article under the render caps.

    Parameters:
        articles: serialised article dicts (or model objects).

    Returns:
        A new list, newest publication time first.
    """
    def _key(article: object) -> datetime:
        _headline, _summary, raw_published = _article_fields(article)
        return _parse_published(raw_published) or _EPOCH_ZERO

    return sorted(articles, key=_key, reverse=True)


def _age_label(raw_published: object, as_of_naive: datetime) -> str:
    """Render the ``[YYYY-MM-DD, Nd ago]`` age suffix for one article.

    Ages anchor the LLM's drift-window arithmetic ("how many days into the
    drift window are we?"), so unparseable timestamps are labelled
    explicitly rather than hidden.

    Parameters:
        raw_published: the article's raw publication value (str/datetime/None).
        as_of_naive:   the tick's as_of, UTC-naive.

    Returns:
        A bracketed suffix string, e.g. `` [2026-07-05, 1d ago]``.
    """
    published_dt = _parse_published(raw_published)

    if published_dt is not None:
        age_days = max(0, (as_of_naive - published_dt).days)
        return f" [{str(raw_published)[:10]}, {age_days}d ago]"

    if raw_published:
        return f" [{raw_published}, age unknown]"

    return " [age unknown]"


def _build_ticker_news_context(
    ticker: str,
    fresh: list,
    stale: list,
    *,
    as_of: datetime,
) -> str:
    """Build the two-section LLM context block for one ticker.

    The staleness pre-filter has already partitioned the articles:

    - ``fresh`` articles (novel — surprise candidates) render IN FULL:
      headline, age, and capped summary.
    - ``stale`` articles (previously seen) render as headline-only
      one-liners — drift-window context, deliberately cheap (spec D4).

    The caller applies the COUNT caps before calling this function, so the
    rendered lists match the report-cache key inputs exactly; only the
    per-article summary character cap is applied here.

    Parameters:
        ticker: the ticker symbol this block describes.
        fresh:  capped list of novel articles (any order; re-sorted here).
        stale:  capped list of previously-seen articles (any order).
        as_of:  the tick's as_of datetime (aware or naive).

    Returns:
        The formatted context block for ``temp:news_context_<TICKER>``.
    """
    # Normalise to UTC-naive so age arithmetic matches _parse_published output.
    as_of_naive = (
        as_of.astimezone(UTC).replace(tzinfo=None)
        if as_of.tzinfo is not None
        else as_of
    )

    lines: list[str] = [
        f"=== {ticker} ===",
        f"  As of: {as_of_naive.date().isoformat()}",
    ]

    if not fresh and not stale:
        lines.append("  (no news available)")
        return "\n".join(lines)

    caps = _caps()

    lines.append("")
    lines.append("  FRESH ARTICLES (not previously seen — surprise candidates):")
    if not fresh:
        lines.append("    (none — no novel articles this tick)")
    for index, article in enumerate(_freshest_first(fresh), start=1):
        headline, summary, raw_published = _article_fields(article)
        lines.append(
            f"  [{index}]{_age_label(raw_published, as_of_naive)}"
            f" {headline or '(no title)'}"
        )
        if summary:
            lines.append(f"       {summary[: caps.max_summary_chars]}")

    lines.append("")
    lines.append(
        "  PREVIOUSLY SEEN (already assessed on earlier ticks —"
        " drift context, headlines only):"
    )
    if not stale:
        lines.append("    (none)")
    for index, article in enumerate(_freshest_first(stale), start=1):
        headline, _summary, raw_published = _article_fields(article)
        lines.append(
            f"  [S{index}]{_age_label(raw_published, as_of_naive)}"
            f" {headline or '(no title)'}"
        )

    return "\n".join(lines)
```

(Ensure `UTC` is imported from `datetime`; keep/adjust existing imports as the
deletions free them up. If the old renderer's callers passed different
arguments, they are rewritten in step 4.5.)

Run the renderer tests green:

```bash
.venv/bin/python -m pytest tests/unit/agents/analysts/news/test_fetch.py tests/unit/agents/analysts/news/test_dedup_recency.py -v
```

**4.4 — Write the failing fetch-agent tests.** Replace the entire contents of
`tests/unit/agents/analysts/news/test_fetch_agent.py` with:

```python
"""Unit tests for NewsFetchAgent — Phase 14 routed + staleness-filtered flow.

Plan 2's ``route_articles`` and the embedding backend are both stubbed:
these tests pin THIS agent's contract (fetch → union-dedup → route →
partition → render → single state_delta event), not the router's or the
embedder's.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions import InMemorySessionService

from agents.analysts.news.fetch_agent import NewsFetchAgent
from agents.analysts.news.history import NewsHistoryStore

_AS_OF = "2026-07-06T14:00:00"


def _article(title: str, summary: str, published: str, url: str) -> dict:
    """Build a serialised article dict in the provider shape."""
    return {"title": title, "summary": summary,
            "published_at": published, "url": url}


async def _stub_embed(text: str) -> list[float]:
    """Deterministic vectors: 'beats' stories are parallel to each other and
    orthogonal to everything else."""
    if "beats" in text:
        return [1.0, 0.0, 0.0]
    return [0.0, 1.0, 0.0]


def _fake_router(company: dict, macro: list | None = None):
    """Build a route_articles stand-in returning a fixed RoutedArticles shape.

    Parameters:
        company: ticker → article list mapping to return.
        macro:   macro-stream articles (default empty).

    Returns:
        A callable matching ``route_articles(articles, watchlist)``.
    """
    def _route(articles: list, watchlist: list[str]) -> SimpleNamespace:
        return SimpleNamespace(company=company, macro=macro or [])

    return _route


async def _run_agent(state: dict) -> tuple[list, dict]:
    """Create a session with ``state``, run NewsFetchAgent, return (events, delta).

    Parameters:
        state: initial session state (tickers, as_of, ...).

    Returns:
        The event list and the final event's state_delta dict.
    """
    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test", state=state, session_id="t-1",
    )
    agent = NewsFetchAgent(name="NewsFetch")
    ctx = InvocationContext(
        session_service=svc, session=session, invocation_id="inv-1", agent=agent,
    )
    events = [ev async for ev in agent.run_async(ctx)]
    return events, events[-1].actions.state_delta


@pytest.mark.asyncio
async def test_fresh_articles_land_in_per_ticker_context_and_data():
    """Happy path: routed novel articles render in FRESH and populate
    temp:news_data with news/fresh/stale slices."""
    art = _article("AAPL beats on earnings", "Big beat.",
                   "2026-07-05T12:00:00", "https://news/a1")
    store = NewsHistoryStore(embed_fn=_stub_embed)

    with (
        patch("agents.analysts.news.fetch_agent.get_stock_news",
              new=AsyncMock(return_value=[art])),
        patch("agents.analysts.news.fetch_agent.route_articles",
              new=_fake_router({"AAPL": [art]})),
        patch("agents.analysts.news.fetch_agent.get_news_history_store",
              return_value=store),
    ):
        _events, delta = await _run_agent({"tickers": ["AAPL"], "as_of": _AS_OF})

    data = delta["temp:news_data"]["AAPL"]
    assert data["fresh"] == [art]           # POSITIVE: the article IS fresh
    assert data["stale"] == []
    assert data["news"] == [art]

    context = delta["temp:news_context_AAPL"]
    assert "FRESH ARTICLES" in context
    assert "AAPL beats on earnings" in context
    assert "Big beat." in context


@pytest.mark.asyncio
async def test_previously_seen_article_moves_to_stale_on_the_next_tick():
    """The same store across two agent runs: tick 2 renders the article
    headline-only under PREVIOUSLY SEEN.  This is the agent-level staleness
    guarantee the spec's D4 token reduction rests on."""
    art = _article("AAPL beats on earnings", "Big beat.",
                   "2026-07-05T12:00:00", "https://news/a1")
    store = NewsHistoryStore(embed_fn=_stub_embed)

    with (
        patch("agents.analysts.news.fetch_agent.get_stock_news",
              new=AsyncMock(return_value=[art])),
        patch("agents.analysts.news.fetch_agent.route_articles",
              new=_fake_router({"AAPL": [art]})),
        patch("agents.analysts.news.fetch_agent.get_news_history_store",
              return_value=store),
    ):
        await _run_agent({"tickers": ["AAPL"], "as_of": _AS_OF})
        _events, delta = await _run_agent(
            {"tickers": ["AAPL"], "as_of": "2026-07-07T14:00:00"},
        )

    data = delta["temp:news_data"]["AAPL"]
    assert data["stale"] == [art]           # POSITIVE: it IS filtered
    assert data["fresh"] == []

    context = delta["temp:news_context_AAPL"]
    assert "PREVIOUSLY SEEN" in context
    assert "AAPL beats on earnings" in context
    assert "Big beat." not in context       # stale renders headline-only


@pytest.mark.asyncio
async def test_macro_stream_is_not_consumed_here():
    """Roundup/macro articles routed to .macro belong to Plan 5's analyst —
    the per-ticker context must not contain them."""
    roundup = _article("Markets roundup: five movers", "Blah.",
                       "2026-07-05T12:00:00", "https://news/r1")
    store = NewsHistoryStore(embed_fn=_stub_embed)

    with (
        patch("agents.analysts.news.fetch_agent.get_stock_news",
              new=AsyncMock(return_value=[roundup])),
        patch("agents.analysts.news.fetch_agent.route_articles",
              new=_fake_router({"AAPL": []}, macro=[roundup])),
        patch("agents.analysts.news.fetch_agent.get_news_history_store",
              return_value=store),
    ):
        _events, delta = await _run_agent({"tickers": ["AAPL"], "as_of": _AS_OF})

    assert "(no news available)" in delta["temp:news_context_AAPL"]
    assert "Markets roundup" not in delta["temp:news_context_AAPL"]


@pytest.mark.asyncio
async def test_provider_failure_degrades_that_ticker_to_empty():
    """A per-ticker provider error must not kill the branch (isolation) —
    that ticker just renders the no-news placeholder."""
    store = NewsHistoryStore(embed_fn=_stub_embed)

    with (
        patch("agents.analysts.news.fetch_agent.get_stock_news",
              new=AsyncMock(side_effect=RuntimeError("provider down"))),
        patch("agents.analysts.news.fetch_agent.route_articles",
              new=_fake_router({"AAPL": []})),
        patch("agents.analysts.news.fetch_agent.get_news_history_store",
              return_value=store),
    ):
        _events, delta = await _run_agent({"tickers": ["AAPL"], "as_of": _AS_OF})

    assert "(no news available)" in delta["temp:news_context_AAPL"]


@pytest.mark.asyncio
async def test_embedding_failure_fails_the_agent_loudly():
    """Unlike provider failures, an embedding outage is NOT isolated — the
    staleness verdicts would be garbage, so the agent must raise."""
    art = _article("AAPL beats on earnings", "Big beat.",
                   "2026-07-05T12:00:00", "https://news/a1")

    async def _broken(text: str) -> list[float]:
        raise RuntimeError("embedding endpoint down")

    store = NewsHistoryStore(embed_fn=_broken)

    with (
        patch("agents.analysts.news.fetch_agent.get_stock_news",
              new=AsyncMock(return_value=[art])),
        patch("agents.analysts.news.fetch_agent.route_articles",
              new=_fake_router({"AAPL": [art]})),
        patch("agents.analysts.news.fetch_agent.get_news_history_store",
              return_value=store),
    ):
        with pytest.raises(RuntimeError, match="embedding endpoint down"):
            await _run_agent({"tickers": ["AAPL"], "as_of": _AS_OF})
```

Run and watch it fail. (If the old `test_fetch_agent.py` had a different
session/context harness, mirror it in `_run_agent` — the assertions are the
load-bearing part.)

**4.5 — Rewrite `src/agents/analysts/news/fetch_agent.py`.** Preserve the
class name and any constructor conventions `agent.py` relies on; the module
becomes:

```python
"""NewsFetchAgent — fetch, route, staleness-filter, and render per ticker.

Phase 14 (Plan 3) rebuild.  Per tick this agent:

1. Fetches ``/company-news`` for every watchlist ticker (existing provider
   path — cache-backed during backtests; D1: no new providers).
2. Unions the feeds and de-duplicates exact re-fetches by ``article_key``
   (the same story is stapled onto several tickers' feeds — judge it once).
3. Routes the union through the specificity router (Plan 2):
   company-specific articles come back keyed by ticker in
   ``RoutedArticles.company``; roundup/macro articles land in ``.macro``,
   which this agent does NOT consume — Plan 5's macro analyst owns that
   stream.
4. Per ticker: title-level dedup (cheap exact/near-exact hygiene), then the
   deterministic embedding staleness pre-filter against the per-run
   ``NewsHistoryStore``.  Only novel articles render in full; previously
   seen articles render as headline-only drift context (D4).

Yielded state keys (one state_delta event):
    - ``temp:news_data`` — dict[ticker, {"news", "fresh", "stale"}] where
      ``news`` is the full routed set (deterministic-extractor input) and
      ``fresh``/``stale`` are the capped LLM-visible slices (report-cache
      key inputs).
    - ``temp:news_context_<TICKER>`` — the two-section context block.
    - ``temp:news_context`` — aggregate block (trace/debug only).

Failure policy: a per-ticker provider error degrades that ticker to an
empty feed (branch isolation), but an embedding failure RAISES — silently
mis-classifying staleness is the banned silent-degradation bug class.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from datetime import datetime

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from agents.analysts.news.fetch import (
    _build_ticker_news_context,
    _dedup_and_sort_articles,
    _freshest_first,
    article_key,
    partition_articles_by_staleness,
)
from agents.analysts.news.history import get_news_history_store
from agents.analysts.news.router import route_articles
from config.analysts import get_analysts_config
from data import get_stock_news
from data.timeguard import resolve_as_of

_LOGGER = logging.getLogger(__name__)


class NewsFetchAgent(BaseAgent):
    """Deterministic pre-LLM stage of the news branch (see module docstring)."""

    async def _run_async_impl(
        self,
        ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Fetch, route, partition, and render news for every ticker.

        Parameters:
            ctx: the ADK invocation context carrying session state.

        Yields:
            One Event whose state_delta holds ``temp:news_data``, the
            per-ticker ``temp:news_context_<TICKER>`` blocks, and the
            aggregate ``temp:news_context``.
        """
        state = ctx.session.state
        tickers: list[str] = state.get("tickers", []) or []

        as_of: datetime = resolve_as_of(
            state.get("as_of"), allow_wallclock=True, site="news/fetch_agent",
        )

        # ── 1. Fetch every ticker's feed ─────────────────────────────────
        all_articles: list = []
        for ticker in tickers:
            try:
                articles = await get_stock_news(ticker, as_of=as_of)
            except Exception as exc:  # noqa: BLE001 — per-ticker isolation
                _LOGGER.warning("news fetch failed for %s: %s", ticker, exc)
                articles = []
            all_articles.extend(articles)

        # ── 2. Union-level identity dedup ────────────────────────────────
        # The same story appears on several tickers' /company-news feeds;
        # route each story once, not once per (story, feed) pair.
        unique: dict[str, object] = {}
        for article in all_articles:
            unique.setdefault(article_key(article), article)

        # ── 3. Specificity routing (Plan 2) ──────────────────────────────
        # ``routed.macro`` is deliberately ignored here — Plan 4 consumes it.
        routed = route_articles(list(unique.values()), tickers)

        # ── 4. Per-ticker partition + render ─────────────────────────────
        store = get_news_history_store()
        cfg = get_analysts_config()
        threshold = cfg.staleness_similarity_threshold

        news_data: dict[str, dict] = {}
        context_blocks: dict[str, str] = {}

        for ticker in tickers:
            routed_articles = routed.company.get(ticker, []) or []

            # Serialise model objects so state stays JSON-safe end to end.
            serialised = [
                a.model_dump(mode="json") if hasattr(a, "model_dump") else a
                for a in routed_articles
            ]

            # Cheap title-level dedup first — collapse exact syndication
            # copies before any embedding is spent; the staleness filter
            # then catches the paraphrased rehashes this pass misses.
            deduped = _dedup_and_sort_articles(serialised)

            fresh, stale = await partition_articles_by_staleness(
                ticker, deduped, store=store, threshold=threshold,
            )

            # Apply the count caps HERE (freshest survive) so the rendered
            # block and the report-cache key hash byte-identical lists.
            fresh_capped = _freshest_first(fresh)[
                : cfg.news.max_articles_per_ticker
            ]
            stale_capped = _freshest_first(stale)[
                : cfg.news.max_stale_headlines_per_ticker
            ]

            news_data[ticker] = {
                "news": serialised,
                "fresh": fresh_capped,
                "stale": stale_capped,
            }
            context_blocks[ticker] = _build_ticker_news_context(
                ticker, fresh_capped, stale_capped, as_of=as_of,
            )

        # ── 5. Emit one state_delta event ────────────────────────────────
        delta: dict[str, object] = {"temp:news_data": news_data}
        for ticker, block in context_blocks.items():
            delta[f"temp:news_context_{ticker}"] = block
        delta["temp:news_context"] = "\n\n".join(
            context_blocks[ticker] for ticker in tickers
        )

        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            actions=EventActions(state_delta=delta),
        )
```

Carry over verbatim any existing pieces of the current `fetch_agent.py` this
skeleton does not mention but the branch relies on (e.g. a `trace_maybe`/trace
call, pydantic model config on the agent class, or an explicit `__init__`) —
diff against the old file before deleting it wholesale. If
`_dedup_and_sort_articles`'s current signature takes extra arguments (e.g. a
caps object), call it the way its existing tests in `test_dedup_recency.py` do.

**4.6 — Point the report-cache hash at the LLM-visible slices.** In
`src/agents/analysts/news/per_ticker.py`, replace the `hash_inputs` line:

```python
        hash_inputs        = lambda d: news_hash_inputs((d or {}).get("news") or []),
```

with:

```python
        # Cache key covers exactly what the LLM sees: the capped fresh list
        # (rendered in full) plus the capped stale list (headline-only).
        # The full routed set under "news" feeds only the deterministic
        # feature extractor and must not bust the report cache.
        hash_inputs        = lambda d: news_hash_inputs(
            ((d or {}).get("fresh") or []) + ((d or {}).get("stale") or [])
        ),
```

(match the file's existing alignment style). This matters because the context
now depends on the store's fresh/stale judgement: an article moving from fresh
to stale changes the rendered prompt, so it must change the cache key too.

**4.7 — Verify and commit.** The staleness-filter tests, renderer tests,
fetch-agent tests, and dedup tests must all pass; then the full suite:

```bash
.venv/bin/python -m pytest tests/unit/agents/analysts/news/ -v
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m ruff check src/ tests/
grep -rn "max_generic_articles_per_ticker\|_rerank_articles\|_score_article_specificity" src/ tests/ config/
```

The grep must return nothing. Commit:

```bash
git add -A
git commit -m "feat(news): route + staleness-filter fetch flow with two-section context

Replaces the heuristic specificity reranker with Plan 2 routing and the
deterministic embedding staleness pre-filter; per-ticker contexts now
render fresh articles in full and previously-seen headlines only.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 4 — Rewrite the per-ticker prompt: surprise classification + drift positioning

**Files:**
- Modify: `src/agents/analysts/news/prompts.py` (replace `_TEMPLATE`)
- Modify: `tests/unit/agents/analysts/news/test_prompts.py`

`NEWS_PROMPT_VERSION` is derived by hashing the rendered prompt
(`report_cache.py::_derive_prompt_version`), so this rewrite auto-invalidates
the on-disk report cache — no manual version bump exists or is needed.

**Steps:**

**5.1 — Write the failing prompt tests.** Append to
`tests/unit/agents/analysts/news/test_prompts.py` (reuse its existing `_vocab()`
/ vocabulary fixture helper — whatever name that file already uses):

```python
def test_instruction_demands_horizon_days():
    """The drift prompt must require an explicit holding horizon."""
    instruction = build_news_instruction(_vocab())

    assert "horizon_days" in instruction
    assert "trading days" in instruction


def test_decision_rule_is_surprise_plus_drift_not_sentiment_reaction():
    """The Phase 14 decision rule: classify the surprise, position for the
    drift window — the old react-to-sentiment framing must be gone."""
    instruction = build_news_instruction(_vocab())

    assert "SURPRISE CLASSIFICATION" in instruction
    assert "DRIFT POSITIONING" in instruction
    assert "PREVIOUSLY SEEN" in instruction        # explains the stale section


def test_instruction_explains_the_two_context_sections():
    """The prompt must tell the model what FRESH vs PREVIOUSLY SEEN mean —
    the sections only exist because the pre-filter builds them."""
    instruction = build_news_instruction(_vocab())

    assert "FRESH ARTICLES" in instruction
    assert "headline" in instruction.lower()
```

Watch them fail, and note which EXISTING prompt tests fail alongside (single-
ticker phrasing, OUTPUT CONTRACT presence, closed-vocab lines, `{ticker}` /
`{news_context}` placeholder checks, prose-cap substitution — those must all
still pass after the rewrite; also `tests/unit/test_news_prompt_report_required.py`
if present pins the `is_no_data`/`report` REQUIRED wording):

```bash
.venv/bin/python -m pytest tests/unit/agents/analysts/news/test_prompts.py -v
```

**5.2 — Replace `_TEMPLATE` in `src/agents/analysts/news/prompts.py`.** Keep
`build_news_instruction(vocab)`'s signature, its vocabulary/caps substitution
mechanics, and the runtime `{ticker}` / `{news_context}` placeholders exactly as
they are (per_ticker.py string-replaces them). Keep the OUTPUT CONTRACT and
SHAPE EXAMPLE block structure (mirroring the fundamental prompt's pattern), the
closed-vocab `key_factors` mandate, and prose-stated caps (never schema
`max_length`). The new template:

```python
_TEMPLATE = """\
You are the news analyst for {ticker}. You do NOT react to sentiment — you
position for POST-NEWS DRIFT: the well-documented tendency of prices to
continue moving in the direction of a genuine surprise for days to weeks
after the news lands (post-earnings-announcement drift and its analogues).

Today's date and the news for {ticker} are below. Articles arrive in two
sections, pre-filtered deterministically before you see them:

- FRESH ARTICLES — not previously seen by this desk. These are your only
  candidates for a NEW surprise. Full text is provided.
- PREVIOUSLY SEEN — already assessed on earlier ticks (headlines and ages
  only). These are NOT new information. Use them solely to work out where
  you are inside an existing drift window.

{news_context}

DECISION PROCEDURE — work through these steps in order:

STEP 1 — SURPRISE CLASSIFICATION (fresh articles only).
For each fresh article, decide: is this a GENUINE SURPRISE — new, material,
company-specific information that plausibly moved (or will move)
expectations — or is it noise (rehash phrased past the filter, commentary,
minor housekeeping, already-implied follow-up)?
- A genuine surprise names specifics: numbers vs expectations, a decision,
  a contract, a regulatory outcome, guidance change.
- Noise recycles what the PREVIOUSLY SEEN section already covers, or is
  too vague to shift expectations. Classifying everything as noise is a
  perfectly good outcome — most ticks have no genuine surprise.

STEP 2 — DIRECTION. For each genuine surprise: positive or negative for
{ticker}'s equity over the coming days? Judge the SURPRISE direction (vs
expectations), not the headline's emotional tone.

STEP 3 — DRIFT POSITIONING (use the PREVIOUSLY SEEN ages).
- Fresh genuine surprise (0–1 days old): the drift window is just opening.
  Lean WITH the surprise direction. Set horizon_days to roughly 5.
- Existing drift, early/middle of the window (surprise 2–10 trading days
  ago per the PREVIOUSLY SEEN ages, no fresh contradiction): continuation
  lean is justified at REDUCED magnitude and confidence; set horizon_days
  to the remaining window (e.g. 3–15).
- Late or exhausted window (several weeks old, nothing fresh): the edge is
  gone — and stale news re-circulating without new facts mildly predicts
  REVERSAL, not continuation. Go neutral rather than chase.
- Fresh surprise CONTRADICTING an existing drift: the fresh information
  wins; re-anchor on it.

STEP 4 — NO SURPRISE AT ALL. Nothing fresh is genuine and no live drift
window exists → lean neutral with low magnitude. Do NOT manufacture a lean
from noise volume.

horizon_days is REQUIRED: the number of TRADING DAYS you expect your lean
to remain valid. ~5 for a fresh surprise; longer (up to ~20) only for
mid-window drift continuation; 1 for a neutral no-surprise verdict.

OUTPUT CONTRACT — respond ONLY with a JSON object matching the schema.
Field meanings:
- lean: "bullish" | "bearish" | "neutral".
- magnitude: 0.0–1.0 — size of the expected drift move, discounted by how
  far into the window you already are.
- confidence: 0.0–1.0 — how sure you are a genuine surprise (or live
  drift) exists. Noise-only ticks are LOW confidence neutrals.
- is_no_data: true ONLY when the context shows "(no news available)" for
  {ticker}; report must then be null and key_factors empty.
- horizon_days: integer >= 1 — trading days the lean should hold (see
  STEP 3).
- key_factors: 0–8 tags, EXCLUSIVELY from this closed vocabulary —
  catalyst:<one of {catalysts}>, novelty:<one of {novelty}>,
  direction:<one of {direction}>, plus the bare tag "material" when the
  surprise is company-moving. Never invent tags outside this list.
- report: REQUIRED whenever is_no_data is false.
  - report.summary: <= {report_summary_max} characters — state the
    surprise (or its absence), the drift-window position, and the horizon.
  - report.drivers: 1–4 entries; name <= {driver_name_max} characters;
    body <= {driver_body_max} characters explaining how that article (or
    the window position) feeds the lean.

SHAPE EXAMPLE (illustrative values only — never copy them):
{{
  "ticker": "{ticker}",
  "lean": "bullish",
  "magnitude": 0.45,
  "confidence": 0.7,
  "is_no_data": false,
  "horizon_days": 5,
  "key_factors": ["catalyst:earnings", "novelty:high",
                  "direction:positive", "material"],
  "report": {{
    "summary": "Fresh EPS beat with raised guidance is a genuine positive \
surprise; drift window opening today, positioning long for ~5 sessions.",
    "drivers": [
      {{"name": "eps_beat", "direction": "bull", "weight": 0.6,
        "body": "Reported EPS well above consensus per the fresh article."}},
      {{"name": "guidance_raise", "direction": "bull", "weight": 0.4,
        "body": "Full-year guidance lifted, extending the surprise."}}
    ]
  }}
}}
"""
```

Adapt the substitution tokens (`{catalysts}`, `{novelty}`, `{direction}`,
`{report_summary_max}`, `{driver_name_max}`, `{driver_body_max}`) to the EXACT
names and formatting `build_news_instruction` currently substitutes — read the
existing function and keep its rendering code unchanged (only `_TEMPLATE`
changes; if the current template writes vocab lists differently, e.g. one line
per option, follow the existing convention and the existing tests' assertions).
Escape literal JSON braces as `{{`/`}}` exactly as the current template does
for its shape example. Remove the Task-1.4 interim `horizon_days` line — the
contract above supersedes it.

**5.3 — Reconcile the existing prompt tests.** Run the whole prompt test file;
where an existing test pins removed wording (e.g. the old reactive decision
rule or the recency-discount phrasing), update the assertion to the new
equivalent — but keep the intent of every test (single-ticker phrasing, closed
vocab, OUTPUT CONTRACT, no-data protocol, placeholder integrity, caps
substitution). Also run the report-cache tests — `NEWS_PROMPT_VERSION` changes
value but nothing should pin the literal hash:

```bash
.venv/bin/python -m pytest tests/unit/agents/analysts/news/test_prompts.py tests/unit/test_news_prompt_report_required.py tests/unit/agents/analysts/test_report_cache.py -v
```

(Adjust paths to whichever of those files exist — locate with
`grep -rln "NEWS_PROMPT_VERSION\|build_news_instruction" tests/`.)

**5.4 — Verify and commit.**

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m ruff check src/ tests/
git add -A
git commit -m "feat(news): rewrite per-ticker prompt to surprise classification + drift positioning

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 5 — Backtest lifecycle: per-run history-store reset

**Files:**
- Modify: `src/backtest/driver.py`
- Create: `tests/unit/backtest/test_driver_news_history_reset.py`

PIT-correctness (spec D2): the history store must be rebuilt from each window's
golden-cache news timeline, tick by tick, exactly as the live pipeline would
build it — and must never leak across windows or across repeated runs of the
same window inside one process. The natural rebuild already happens for free
(NewsFetchAgent records every article it sees, and during a backtest the
articles come from the cache provider in tick order); the only thing to add is
the reset at the start of each replay. `Driver.run` is the right seam — it is
the per-window entrypoint the runner calls, and it is directly testable.
The live path (`orchestrator` tick entrypoint) needs no change: a live process
accumulating history across its own ticks is the desired behaviour.

**Steps:**

**6.1 — Write the failing test.** Create
`tests/unit/backtest/test_driver_news_history_reset.py`, mirroring the driver
construction used in `tests/unit/backtest/test_driver_consumes_tickers.py`
(same `FakeBroker` arguments, same keyword defaults — copy that file's fixture
verbatim if it differs from the sketch below; the load-bearing assertion is the
store-identity change):

```python
"""Contract test: Driver.run resets the per-run news-history store.

PIT-correctness (Phase 14 Plan 3): each window replay must rebuild the
staleness history from that window's own news timeline — nothing may leak
in from a previous window (or a previous run) executed in this process.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import agents.analysts.news.history as history
from backtest.driver import Driver
from broker.fake import FakeBroker


@pytest.mark.asyncio
async def test_driver_run_resets_the_news_history_store(tmp_path: Path) -> None:
    """A store instance that existed before Driver.run must be discarded."""
    # Simulate leakage: a store left over from a previous window replay.
    stale_store = history.get_news_history_store()

    driver = Driver(
        broker=FakeBroker(starting_cash=10_000.0, prices={}),
        run_dir=tmp_path,
        window_key="test-window",
        failure_abort_ratio=0.99,
        enforce_pipeline_completion=False,
        require_store=False,
    )

    # An empty schedule executes no ticks — we are testing only the
    # pre-flight reset, not the pipeline.
    await driver.run({"tickers": []}, [])

    assert history.get_news_history_store() is not stale_store

    # Leave no shared state behind for other tests.
    history.reset_news_history_store()
```

Run and watch it fail (the identity assertion):

```bash
.venv/bin/python -m pytest tests/unit/backtest/test_driver_news_history_reset.py -v
```

If `Driver`'s constructor or `run()` signature differs from the sketch (extra
required kwargs such as `run_id`), copy the exact construction from
`test_driver_consumes_tickers.py`; if `run()` cannot tolerate an empty schedule,
reuse that file's minimal one-tick harness instead.

**6.2 — Add the reset to `src/backtest/driver.py`.** Import at the top of the
file (with the other `agents.` imports if any exist, otherwise in the import
block):

```python
from agents.analysts.news.history import reset_news_history_store
```

Then, in `Driver.run`, immediately before the tick loop begins (before the
`for tick in schedule:` line and any `total_ticks = len(schedule)` bookkeeping):

```python
        # Phase 14 (Plan 3): the news staleness pre-filter's embedding store
        # is strictly per-run state.  Reset it before the first tick so this
        # replay rebuilds it from the window's golden-cache news timeline in
        # tick order — nothing may leak in from a previous window (or a
        # previous run of the same window) executed in this process.
        reset_news_history_store()
```

**6.3 — Verify and commit.**

```bash
.venv/bin/python -m pytest tests/unit/backtest/ -v
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m ruff check src/ tests/
git add -A
git commit -m "feat(backtest): reset per-run news-history store at the start of each window replay

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

Note: files under `tests/unit/backtest/` may be masked by the repo's
`.git/info/exclude` data patterns — if `git status` does not show the new test
file, `git add -f tests/unit/backtest/test_driver_news_history_reset.py`.

---

## Task 6 — Full-suite verification and cross-plan integration check

**Files:** none created — verification only.

**Steps:**

**7.1 — Full suite and lint:**

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m ruff check src/ tests/
```

If `from agents.analysts.news.router import route_articles` fails at import
time because Plan 2 has not merged yet, every OTHER test must still pass
(the fetch-agent tests patch the symbol, but the import itself needs the
module). In that situation report the suite status against this plan's own
test files and flag the pending Plan 2 dependency in the completion report —
do NOT stub `router.py` yourself; Plan 2 owns that file.

**7.2 — Dead-symbol and convention sweep:**

```bash
grep -rn "_rerank_articles\|_score_article_specificity\|_build_company_terms\|_count_roundup_companies\|max_generic_articles_per_ticker" src/ tests/ config/ docs/Phase14-analyst-refactor/plans/plan3-news-drift-rebuild.md --include="*.py" --include="*.json"
grep -rn "max_length" src/contract/evidence.py
```

The first grep must return nothing from `src/`, `tests/`, or `config/`. The
second must show `max_length` only on `key_factors` (list bound — allowed);
no free-text field may have gained one.

**7.3 — Config/README parity check:** confirm `config/README.md` documents both
`staleness_similarity_threshold` and `news.max_stale_headlines_per_ticker`, and
no longer documents `max_generic_articles_per_ticker`.

**7.4 — Final commit if the sweeps required fixes**, message:

```
chore(news): post-rebuild sweep fixes

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
```

---

## Self-review

Performed against the writing-plans checklist before hand-off:

- **Spec coverage (§5 news-rebuild design):** staleness pre-filter replacing heuristic rerank
  (Tasks 2–3), NewsHistoryStore with pinned signatures (Task 1), prompt rewrite
  to surprise + drift with `horizon_days` (Task 4; the field itself is added by Plan 1), branch shape and
  wrappers untouched (Task 3 rewrites only `fetch_agent.py` internals; `agent.py`
  and `per_ticker.py` wrappers unchanged apart from the hash-inputs lambda),
  backtest PIT lifecycle (Task 5), D1 (no new providers — fetch path unchanged),
  D4 (stale renders headline-only; identity short-circuit avoids re-embedding).
- **§8 testing requirements:** horizon propagation (Plan 1, Task 5), staleness threshold
  behaviour with positive assertions (Task 2 and the agent-level test in 4.4),
  loud-failure tests for embedding outages (2.1, 3.1, 4.4).
- **Placeholder scan:** no TODOs/ellipses in code blocks; the only intentional
  "adapt to existing" instructions are where the plan cannot see private test
  harness details (prompt substitution tokens in 5.2, driver construction in 6.1)
  and each names the exact reference file to copy.
- **Type consistency:** `horizon_days` int/ge=1 on both classes; store
  signatures match the cross-plan pin exactly (`staleness(namespace, text)`,
  `record(namespace, article_key, text, published_at)`); `route_articles`
  consumed with the pinned `RoutedArticles.company` shape and never stubbed in
  `src/`.
- **Known judgement calls** (recorded for the reviewer): `horizon_days` made
  REQUIRED on `LlmTickerVerdict` (schema doctrine) by Plan 1's Task 5, which also
  owns the interim prompt compat patch (this plan's Task 4 emits the field); `staleness_similarity_threshold`
  placed at the top level of `analysts.json` because Plan 5's macro namespace
  shares it; the title-dedup pass retained as a cheap pre-embedding hygiene
  step; `max_generic_articles_per_ticker` retired in favour of
  `max_stale_headlines_per_ticker`; report-cache key redefined to the
  fresh+stale (LLM-visible) slices so store-state changes bust the cache
  correctly; the per-run reset seated in `Driver.run` rather than the runner's
  window loop because it is the per-window entrypoint and directly unit-testable.
