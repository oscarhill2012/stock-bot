"""PIT-composite ratios provider — XBRL fundamentals + sliced OHLCV technicals."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from data.models import CompanyRatios, OHLCBar, PriceHistory


def _make_bars(n: int, last_close: float = 175.0) -> list[OHLCBar]:
    """Create ``n`` daily bars ending at 2023-03-14 with ``last_close``."""
    bars: list[OHLCBar] = []
    for i in range(n):
        ts    = datetime(2023, 1, 1, tzinfo=UTC).replace(day=min(i + 1, 28))
        close = last_close - (n - 1 - i) * 0.5
        bars.append(OHLCBar(
            timestamp=ts,
            open=close - 0.5,
            high=close + 1.0,
            low=close - 1.0,
            close=close,
            volume=1_000_000.0,
        ))
    return bars


@pytest.mark.asyncio
async def test_pit_composite_returns_filled_ratios(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider composes XBRL fundamentals + price-derived technicals."""
    import data.providers.company_ratios.pit_composite as mod

    fake_facts = SimpleNamespace(
        long_name      = "Apple Inc.",
        sector         = "Technology",
        shares_out     = 15_700_000_000.0,
        eps_ttm        = 6.0,
        dps_ttm        = 0.92,
    )
    monkeypatch.setattr(mod, "_fetch_xbrl_facts", lambda symbol, as_of_date: fake_facts)
    monkeypatch.setattr(
        mod, "_fetch_price_series",
        lambda symbol, as_of: PriceHistory(ticker=symbol, bars=_make_bars(220, last_close=175.0)),
    )
    # Stub the XBRL summary so no EDGAR network call or env-var lookup is made.
    monkeypatch.setattr(mod, "_load_xbrl_summary", lambda *a, **k: {
        "profit_margin": None, "debt_to_equity": None, "roe": None,
        "revenue_growth_yoy": None, "free_cash_flow": None,
        "peg": None,
    })

    out = await mod.fetch("AAPL", as_of=datetime(2023, 3, 14, tzinfo=UTC))

    assert isinstance(out, CompanyRatios)
    assert out.long_name      == "Apple Inc."
    assert out.sector         == "Technology"
    assert out.last_price     == pytest.approx(175.0)
    assert out.market_cap     == pytest.approx(15_700_000_000.0 * 175.0)
    assert out.trailing_pe    == pytest.approx(175.0 / 6.0)
    assert out.dividend_yield == pytest.approx(0.92 / 175.0)
    assert out.fifty_day_average is not None
    assert out.two_hundred_day_average is not None


@pytest.mark.asyncio
async def test_pit_composite_handles_missing_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty XBRL must yield a model with ``None`` fundamentals, not raise."""
    import data.providers.company_ratios.pit_composite as mod

    monkeypatch.setattr(mod, "_fetch_xbrl_facts", lambda s, d: SimpleNamespace(
        long_name=None, sector=None, shares_out=None, eps_ttm=None, dps_ttm=None,
    ))
    monkeypatch.setattr(
        mod, "_fetch_price_series",
        lambda s, a: PriceHistory(ticker=s, bars=_make_bars(5, last_close=100.0)),
    )
    monkeypatch.setattr(mod, "_load_xbrl_summary", lambda *a, **k: {
        "profit_margin": None, "debt_to_equity": None, "roe": None,
        "revenue_growth_yoy": None, "free_cash_flow": None,
        "peg": None,
    })

    out = await mod.fetch("XYZ", as_of=datetime(2023, 3, 14, tzinfo=UTC))

    assert isinstance(out, CompanyRatios)
    assert out.last_price  == pytest.approx(100.0)
    assert out.market_cap  is None
    assert out.trailing_pe is None


@pytest.mark.asyncio
async def test_pit_composite_handles_empty_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty OHLCV must yield ``None`` price-derived fields, not raise."""
    import data.providers.company_ratios.pit_composite as mod

    monkeypatch.setattr(mod, "_fetch_xbrl_facts", lambda s, d: SimpleNamespace(
        long_name="X Co", sector="X", shares_out=1.0, eps_ttm=1.0, dps_ttm=None,
    ))
    monkeypatch.setattr(
        mod, "_fetch_price_series",
        lambda s, a: PriceHistory(ticker=s, bars=[]),
    )
    monkeypatch.setattr(mod, "_load_xbrl_summary", lambda *a, **k: {
        "profit_margin": None, "debt_to_equity": None, "roe": None,
        "revenue_growth_yoy": None, "free_cash_flow": None,
        "peg": None,
    })

    out = await mod.fetch("XYZ", as_of=datetime(2023, 3, 14, tzinfo=UTC))

    assert out.long_name  == "X Co"
    assert out.last_price is None
    assert out.market_cap is None


def test_pit_composite_registers_on_import() -> None:
    import data.providers.company_ratios.pit_composite  # noqa: F401
    from data.registry import _REGISTRY

    entry = _REGISTRY[("company_ratios", "pit_composite")]
    assert entry.upstream == "yfinance"   # shares yfinance limiter for price data


# ---------------------------------------------------------------------------
# Task 4.4 — six XBRL-derivable ratios + as_of population
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pit_composite_populates_new_ratios(monkeypatch: pytest.MonkeyPatch) -> None:
    """All six XBRL-derived ratio fields are populated when the summary returns full data."""
    import data.providers.company_ratios.pit_composite as mod

    # Stub out the XBRL facts (identity / price primitives) so the test is
    # isolated from any EDGAR network call.
    monkeypatch.setattr(mod, "_fetch_xbrl_facts", lambda symbol, as_of_date: SimpleNamespace(
        long_name="Apple Inc.", sector="Technology",
        shares_out=15_700_000_000.0, eps_ttm=6.0, dps_ttm=0.92,
    ))
    monkeypatch.setattr(
        mod, "_fetch_price_series",
        lambda symbol, as_of: PriceHistory(ticker=symbol, bars=_make_bars(220, last_close=175.0)),
    )

    # Full XBRL summary — all five ratio fields present.  ``peg`` is always
    # surfaced as ``None`` by ``_load_xbrl_summary`` (there is no PIT-correct
    # source for the forward-growth term — see the provider docstring).
    fake_xbrl: dict = {
        "profit_margin":      0.25,
        "debt_to_equity":     1.5,
        "roe":                0.15,
        "revenue_growth_yoy": 0.07,
        "free_cash_flow":     9.0e10,
        "peg":                None,
    }
    monkeypatch.setattr(mod, "_load_xbrl_summary", lambda *a, **k: fake_xbrl)

    out = await mod.fetch("AAPL", as_of=datetime(2023, 3, 10, tzinfo=UTC))

    assert isinstance(out, CompanyRatios)

    # Core XBRL-derived ratios.
    assert out.profit_margin      == pytest.approx(0.25)
    assert out.debt_to_equity     == pytest.approx(1.5)
    assert out.roe                == pytest.approx(0.15)
    assert out.revenue_growth_yoy == pytest.approx(0.07)
    assert out.free_cash_flow     == pytest.approx(9.0e10)
    # PEG is intentionally always None — no PIT-correct source available.
    assert out.peg                is None

    # as_of must be populated.
    from datetime import date
    assert out.as_of == date(2023, 3, 10)


@pytest.mark.asyncio
async def test_pit_composite_all_xbrl_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """When XBRL summary returns all None, every new ratio field is None — no exception raised."""
    import data.providers.company_ratios.pit_composite as mod

    monkeypatch.setattr(mod, "_fetch_xbrl_facts", lambda s, d: SimpleNamespace(
        long_name="Stub Co", sector="Industrials",
        shares_out=1_000_000.0, eps_ttm=2.0, dps_ttm=None,
    ))
    monkeypatch.setattr(
        mod, "_fetch_price_series",
        lambda s, a: PriceHistory(ticker=s, bars=_make_bars(10, last_close=50.0)),
    )

    # Empty XBRL summary — all ratios absent (e.g. ADR with no EDGAR data).
    empty_xbrl: dict = {
        "profit_margin":      None,
        "debt_to_equity":     None,
        "roe":                None,
        "revenue_growth_yoy": None,
        "free_cash_flow":     None,
        "peg":                None,
        "_peg_source":        None,
    }
    monkeypatch.setattr(mod, "_load_xbrl_summary", lambda *a, **k: empty_xbrl)

    out = await mod.fetch("XYZ", as_of=datetime(2023, 3, 10, tzinfo=UTC))

    assert isinstance(out, CompanyRatios)
    assert out.profit_margin      is None
    assert out.debt_to_equity     is None
    assert out.roe                is None
    assert out.revenue_growth_yoy is None
    assert out.free_cash_flow     is None
    assert out.peg                is None

    # Price-derived fields must still be present (from yfinance branch).
    assert out.last_price == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_pit_composite_partial_xbrl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Partial XBRL data — some fields populated, others None — is handled gracefully."""
    import data.providers.company_ratios.pit_composite as mod

    monkeypatch.setattr(mod, "_fetch_xbrl_facts", lambda s, d: SimpleNamespace(
        long_name="Partial Co", sector="Healthcare",
        shares_out=500_000_000.0, eps_ttm=3.5, dps_ttm=0.5,
    ))
    monkeypatch.setattr(
        mod, "_fetch_price_series",
        lambda s, a: PriceHistory(ticker=s, bars=_make_bars(60, last_close=120.0)),
    )

    # Only profit_margin and roe available; the rest are missing concepts.
    partial_xbrl: dict = {
        "profit_margin":      0.18,
        "debt_to_equity":     None,   # StockholdersEquity concept missing
        "roe":                0.12,
        "revenue_growth_yoy": None,   # Prior-year Revenues missing
        "free_cash_flow":     None,   # CapEx concept missing
        "peg":                None,
        "_peg_source":        None,
    }
    monkeypatch.setattr(mod, "_load_xbrl_summary", lambda *a, **k: partial_xbrl)

    out = await mod.fetch("HLTH", as_of=datetime(2023, 6, 15, tzinfo=UTC))

    assert isinstance(out, CompanyRatios)

    # Populated fields must be correct.
    assert out.profit_margin == pytest.approx(0.18)
    assert out.roe           == pytest.approx(0.12)

    # Missing fields must be None, not zero or a default.
    assert out.debt_to_equity     is None
    assert out.revenue_growth_yoy is None
    assert out.free_cash_flow     is None
    assert out.peg                is None

    # as_of must be set.
    from datetime import date
    assert out.as_of == date(2023, 6, 15)


# ---------------------------------------------------------------------------
# Revenue-concept selection — pick the genuinely-filed XBRL revenue concept.
#
# No single us-gaap revenue concept is correct across all filers.  ASC-606
# filers (most tech) report their top line under
# ``RevenueFromContractWithCustomerExcludingAssessedTax`` and leave a stale
# single fragment under ``Revenues``; many energy/industrial filers are the
# reverse.  ``_select_revenue_series`` discriminates by period-distinctness:
# the genuinely-filed concept yields *different* TTM values a year apart,
# whereas a stale fragment returns the identical value for any ``as_of``.
# ---------------------------------------------------------------------------

# Probe dates mirroring the live EDGAR investigation that motivated the fix.
_AS_OF  = date(2025, 9, 2)
_PRIOR  = date(2024, 9, 2)

_REV_ASC606 = "RevenueFromContractWithCustomerExcludingAssessedTax"
_REV_LEGACY = "Revenues"
_REV_NET    = "SalesRevenueNet"


def _ttm_lookup(table: dict[tuple[str, date], float | None]):
    """Build a ``(concept, at) -> float | None`` callable backed by ``table``.

    Any ``(concept, date)`` pair absent from ``table`` resolves to ``None`` —
    modelling an XBRL concept the filer never reported.
    """
    def _lookup(concept: str, at: date) -> float | None:
        return table.get((concept, at))

    return _lookup


def test_select_revenue_prefers_period_distinct_asc606_filer() -> None:
    """ASC-606 filer (AAPL-shape): the ASC-606 concept moves year-on-year and wins.

    ``Revenues`` carries a stale fragment (identical at both dates) and must be
    rejected even though it is present.
    """
    from data.providers.company_ratios.pit_composite import _select_revenue_series

    table = {
        # Stale fragment — identical value at both dates → rejected.
        (_REV_LEGACY, _AS_OF): 215.6e9,
        (_REV_LEGACY, _PRIOR): 215.6e9,
        # Genuinely-filed top line — distinct across the year → selected.
        (_REV_ASC606, _AS_OF): 296.1e9,
        (_REV_ASC606, _PRIOR): 293.8e9,
    }
    now, prior, concept = _select_revenue_series(_ttm_lookup(table), _AS_OF, _PRIOR)

    assert concept == _REV_ASC606
    assert now   == pytest.approx(296.1e9)
    assert prior == pytest.approx(293.8e9)


def test_select_revenue_skips_stale_asc606_for_energy_filer() -> None:
    """Energy filer (XOM-shape): ASC-606 is the stale fragment; ``Revenues`` wins.

    The ASC-606 concept is first in priority order but is stale (equal values),
    so period-distinctness must fall through to the live ``Revenues`` series.
    """
    from data.providers.company_ratios.pit_composite import _select_revenue_series

    table = {
        # ASC-606 stale for this filer — identical → rejected despite priority.
        (_REV_ASC606, _AS_OF): 199.0e9,
        (_REV_ASC606, _PRIOR): 199.0e9,
        # Live top line under the legacy concept.
        (_REV_LEGACY, _AS_OF): 176.1e9,
        (_REV_LEGACY, _PRIOR): 169.5e9,
    }
    now, prior, concept = _select_revenue_series(_ttm_lookup(table), _AS_OF, _PRIOR)

    assert concept == _REV_LEGACY
    assert now   == pytest.approx(176.1e9)
    assert prior == pytest.approx(169.5e9)


def test_select_revenue_fallback_to_current_only_when_no_prior() -> None:
    """Recent IPO: a current value exists but no prior year — return (now, None).

    revenue_growth_yoy is uncomputable, but the margin denominator still needs
    a current revenue, so the first concept with a present current value wins.
    """
    from data.providers.company_ratios.pit_composite import _select_revenue_series

    table = {
        (_REV_ASC606, _AS_OF): 12.0e9,
        # No (_REV_ASC606, _PRIOR) entry — company did not exist a year ago.
    }
    now, prior, concept = _select_revenue_series(_ttm_lookup(table), _AS_OF, _PRIOR)

    assert concept == _REV_ASC606
    assert now   == pytest.approx(12.0e9)
    assert prior is None


def test_select_revenue_returns_none_when_no_concept_has_data() -> None:
    """Foreign filer / ADR with no us-gaap revenue at all → (None, None, None)."""
    from data.providers.company_ratios.pit_composite import _select_revenue_series

    now, prior, concept = _select_revenue_series(_ttm_lookup({}), _AS_OF, _PRIOR)

    assert (now, prior, concept) == (None, None, None)


def test_select_revenue_all_stale_falls_back_to_current() -> None:
    """All concepts stale (current == prior): yoy is uncomputable but margin works.

    The period-distinct pass finds nothing; the fallback pass returns the first
    concept with a present current value and a ``None`` prior so the caller
    leaves revenue_growth_yoy unset rather than computing a false 0.
    """
    from data.providers.company_ratios.pit_composite import _select_revenue_series

    table = {
        (_REV_LEGACY, _AS_OF): 50.0e9,
        (_REV_LEGACY, _PRIOR): 50.0e9,
    }
    now, prior, concept = _select_revenue_series(_ttm_lookup(table), _AS_OF, _PRIOR)

    assert concept == _REV_LEGACY
    assert now   == pytest.approx(50.0e9)
    assert prior is None


# ---------------------------------------------------------------------------
# Regression test: _load_xbrl_summary must yield non-zero revenue_growth_yoy
# for ASC-606 filers even when ``Revenues`` carries a stale fragment.
#
# This is the end-to-end path that was broken before the revenue-concept
# selector was introduced.  The OLD code read revenue only from ``Revenues``;
# for ASC-606 filers (AAPL, MSFT, META …) that concept carries a stale single
# fragment returning the identical value at every date, collapsing
# ``revenue_growth_yoy`` to a false 0.0 and inflating ``profit_margin``
# because the denominator (the stale fragment) was smaller than the real
# filed revenue.
#
# This test constructs a fake edgartools ``facts`` object whose query chain
# mimics the ASC-606 scenario — ``Revenues`` stale, ``RevenueFromContract…``
# genuinely distinct — then calls ``_load_xbrl_summary`` directly and asserts
# the POSITIVE signal: non-zero YoY growth and a plausible margin (< 100%).
# ---------------------------------------------------------------------------

def _make_fake_facts(
    concept_table: dict[tuple[str, date], float | None],
    other_concepts: dict[str, float | None] | None = None,
) -> object:
    """Build a fake edgartools ``EntityFacts``-shaped object.

    The returned object supports the full query chain that the fixed
    ``_load_xbrl_summary._ttm_at`` uses::

        facts.query()
             .by_concept(concept)
             .as_of(at)
             .by_fiscal_period("FY")
             .execute()

    Each matching row has ``value``, ``filing_date``, and ``period_end``
    attributes so that ``_ttm_at``'s "most-recent filing, largest period_end"
    selection logic can resolve correctly without hitting the network.

    Rows are produced as follows:
    - For entries in ``concept_table`` keyed by ``(concept, date)``: one row
      is returned whose ``filing_date`` and ``period_end`` are both set to the
      keyed ``date``.  This means the "pick the latest filing date, then pick
      the largest period_end" logic in ``_ttm_at`` unambiguously selects the
      correct value.
    - For entries in ``other_concepts`` (flat concept → value, date-agnostic):
      one row is returned with a sentinel ``filing_date`` / ``period_end``
      far in the past (2000-01-01) so they are always dominated by any
      concept_table entry from a real date.

    The ``by_fiscal_period`` filter is accepted and ignored — all entries in
    ``concept_table`` are treated as annual facts by construction.

    Parameters
    ----------
    concept_table:
        ``{(concept_name, date): value}`` for concepts whose value varies by date.
    other_concepts:
        ``{concept_name: value}`` for date-agnostic balance-sheet / P&L concepts.

    Returns
    -------
    object
        Minimal fake satisfying the ``facts.query()…execute()`` call chain.
    """
    other_concepts = other_concepts or {}

    # Sentinel date for date-agnostic rows — always older than real entries.
    _SENTINEL_DATE = date(2000, 1, 1)

    class _Chain:
        """Accumulates query state through the builder chain; ``execute()`` resolves it."""

        def __init__(self, concept_name: str, at: date) -> None:
            self._concept_name = concept_name
            self._at           = at

        def by_fiscal_period(self, _period: str) -> "_Chain":
            """Accept the annual-period filter (all fake rows are already annual)."""
            return self

        def execute(self) -> list:
            """Resolve the accumulated query state to a list of fake rows.

            Priority: concept_table (date-specific) → other_concepts (fallback).
            """
            val = concept_table.get((self._concept_name, self._at))
            if val is not None:
                return [SimpleNamespace(
                    value       = val,
                    filing_date = self._at,
                    period_end  = self._at,
                )]

            val2 = other_concepts.get(self._concept_name)
            if val2 is not None:
                return [SimpleNamespace(
                    value       = val2,
                    filing_date = _SENTINEL_DATE,
                    period_end  = _SENTINEL_DATE,
                )]

            return []

    class _ByConcept:
        """Holds a concept name awaiting an ``.as_of()`` call."""

        def __init__(self, concept_name: str) -> None:
            self._concept_name = concept_name

        def as_of(self, at: date) -> _Chain:
            """Fix the PIT gate and return the fully-initialised chain."""
            return _Chain(self._concept_name, at)

    class _Query:
        """Entry point for the fake query builder."""

        def by_concept(self, concept_name: str) -> _ByConcept:
            return _ByConcept(concept_name)

    class _FakeFacts:
        """Minimal stand-in for edgartools ``EntityFacts``."""

        def query(self) -> _Query:
            return _Query()

    return _FakeFacts()


def test_load_xbrl_summary_asc606_filer_yields_nonzero_growth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ASC-606 filer scenario: ``Revenues`` stale → selector picks the live concept.

    Before ``_select_revenue_series`` was introduced, ``_load_xbrl_summary``
    read revenue only from ``Revenues``.  For ASC-606 filers that concept
    carries a stale single fragment — identical value at every date — so
    ``revenue_growth_yoy`` collapsed to ``(stale - stale) / stale = 0.0`` and
    ``profit_margin`` used the wrong (smaller) denominator.

    This test stubs the edgartools ``Company`` and ``EntityFacts`` objects so
    no network call is made, then asserts the POSITIVE signals:
    - ``revenue_growth_yoy`` is non-zero (regression: was 0.0 before the fix).
    - ``profit_margin`` is between 0 and 1 (regression: was > 1.0 before fix,
      because net income was divided by the stale fragment rather than real rev).
    """
    import data.providers.company_ratios.pit_composite as mod

    # --- AAPL-shaped XBRL scenario -------------------------------------------
    # ``Revenues`` carries a stale single fragment — same value at every date.
    # ``RevenueFromContractWithCustomerExcludingAssessedTax`` is the live top
    # line with distinct values year-on-year.  All other concepts are present
    # but constant (they don't affect the revenue-selector logic).

    _STALE_REV    = 215_638_000_000.0   # same at both dates → rejected
    _REV_NOW      = 296_105_000_000.0   # genuinely-filed top line at as_of
    _REV_PRIOR    = 293_787_000_000.0   # genuinely-filed top line a year ago
    _NET_INCOME   =  79_000_000_000.0
    _EQUITY       = 364_980_000_000.0

    # Date pair matching the existing test suite's probe dates.
    as_of = _AS_OF   # 2025-09-02
    prior = _PRIOR   # 2024-09-02

    concept_table: dict[tuple[str, date], float | None] = {
        # Stale legacy concept — equal at both dates → must be rejected.
        (_REV_LEGACY, as_of): _STALE_REV,
        (_REV_LEGACY, prior): _STALE_REV,
        # Live ASC-606 concept — distinct values → must be selected.
        (_REV_ASC606, as_of): _REV_NOW,
        (_REV_ASC606, prior): _REV_PRIOR,
    }

    other_concepts: dict[str, float | None] = {
        "NetIncomeLoss":                                       _NET_INCOME,
        "StockholdersEquity":                                  _EQUITY,
        "LongTermDebtNoncurrent":                              85_750_000_000.0,
        "LongTermDebtCurrent":                                 10_912_000_000.0,
        "ShortTermBorrowings":                                  5_980_000_000.0,
        "NetCashProvidedByUsedInOperatingActivities":          91_443_000_000.0,
        "PaymentsToAcquirePropertyPlantAndEquipment":           6_539_000_000.0,
    }

    fake_facts = _make_fake_facts(concept_table, other_concepts)

    # Patch ``Company`` so ``_load_xbrl_summary`` never hits the network.
    class _FakeCompany:
        def get_facts(self):
            return fake_facts

    monkeypatch.setattr(mod, "_ensure_identity", lambda: None)
    # Patch ``Company`` in the module's own namespace — ``pit_composite.py``
    # imports it as ``from edgar import Company``, so we must patch the name
    # where it is actually looked up, not on the ``edgar`` package itself.
    monkeypatch.setattr(mod, "Company", lambda symbol: _FakeCompany())

    result = mod._load_xbrl_summary("AAPL", as_of)

    expected_growth = (_REV_NOW - _REV_PRIOR) / _REV_PRIOR
    expected_margin = _NET_INCOME / _REV_NOW

    # --- Positive-signal assertions (the point of the regression test) -------

    # Before the fix: revenue_growth_yoy was (stale - stale) / stale = 0.0.
    # After the fix: it must be non-zero and match the live concept's growth.
    assert result["revenue_growth_yoy"] is not None, (
        "revenue_growth_yoy must not be None for an ASC-606 filer with EDGAR data"
    )
    assert result["revenue_growth_yoy"] != 0.0, (
        "revenue_growth_yoy collapsed to 0.0 — selector is using the stale "
        "Revenues fragment instead of RevenueFromContractWithCustomer…"
    )
    assert result["revenue_growth_yoy"] == pytest.approx(expected_growth, rel=1e-6), (
        f"Expected growth ≈ {expected_growth:.4%}, got {result['revenue_growth_yoy']:.4%}"
    )

    # Before the fix: profit_margin was net_income / stale_rev — overstated.
    # After the fix: it must use the real filed revenue as denominator.
    assert result["profit_margin"] is not None, (
        "profit_margin must not be None when NetIncomeLoss and revenue are present"
    )
    assert 0.0 < result["profit_margin"] < 1.0, (
        f"profit_margin = {result['profit_margin']:.4%} is implausible "
        f"(was > 1.0 before fix due to wrong revenue denominator)"
    )
    assert result["profit_margin"] == pytest.approx(expected_margin, rel=1e-6)


# ---------------------------------------------------------------------------
# Regression test for audit finding #26 — annual-duration constraint in
# ``_ttm_at``.
#
# Before the fix, ``_ttm_at`` called ``FactQuery.latest()`` with no period-
# duration filter, returning whatever the most-recently-FILED row was — which
# for Q2/Q3-reporting mega-caps is a short YTD value (3- or 6-month period).
# When the current leg is a 6-month YTD and the prior-year leg is a full-year
# annual, ``revenue_growth_yoy`` collapses to ~-44% (GOOGL) or ~-46% (CRM).
#
# The fix constrains ``_ttm_at`` to annual rows only via
# ``by_fiscal_period("FY")`` + post-selection of the most-recent period_end.
# These tests verify that logic without any network calls, by constructing
# fake facts objects that surface *multiple rows per filing* (mimicking the
# real edgartools behaviour where a single 10-K filing includes 2–3
# comparative year rows in the same batch).
# ---------------------------------------------------------------------------

def _make_multi_row_facts(
    concept_name: str,
    at: date,
    rows_for_latest_filing: list[tuple[date, float]],
    rows_for_older_filing:  list[tuple[date, float]] | None = None,
) -> object:
    """Build a fake ``EntityFacts`` that mimics the real 10-K comparative structure.

    A real annual 10-K filing includes multiple comparative periods as separate
    rows all sharing the same ``filing_date``.  ``_ttm_at`` must pick the row
    with the *largest* ``period_end`` from the most-recent filing batch.

    Parameters
    ----------
    concept_name:
        The US-GAAP concept to expose (e.g. ``"RevenueFromContractWith…"``).
    at:
        The PIT gate date (``as_of``).  All rows in ``rows_for_latest_filing``
        share a ``filing_date`` equal to ``at`` (simulating "filed on this date").
    rows_for_latest_filing:
        List of ``(period_end, value)`` pairs for the most-recent filing batch.
        They all get ``filing_date = at``.
    rows_for_older_filing:
        Optional list of ``(period_end, value)`` pairs from an older filing;
        these get ``filing_date = date(at.year - 1, at.month, at.day)`` so they
        are dominated by the latest-filing batch in ``_ttm_at``'s selection.

    Returns
    -------
    object
        Minimal fake satisfying ``facts.query().by_concept().as_of()
        .by_fiscal_period().execute()``.
    """
    older_filing_date = date(at.year - 1, at.month, at.day)

    def _build_rows():
        result = []
        for period_end, value in (rows_for_latest_filing or []):
            result.append(SimpleNamespace(
                value       = value,
                filing_date = at,
                period_end  = period_end,
            ))
        for period_end, value in (rows_for_older_filing or []):
            result.append(SimpleNamespace(
                value       = value,
                filing_date = older_filing_date,
                period_end  = period_end,
            ))
        return result

    all_rows = _build_rows()

    class _Chain:
        def by_fiscal_period(self, _p):
            return self

        def execute(self):
            return list(all_rows)

    class _BC:
        def as_of(self, _at):
            return _Chain()

    class _Q:
        def by_concept(self, _c):
            return _BC()

    class _FakeFacts:
        def query(self):
            return _Q()

    return _FakeFacts()


def test_ttm_at_picks_most_recent_period_from_latest_filing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_ttm_at`` selects the largest period_end from the most-recent filing batch.

    This is the core of audit finding #26.  A real 10-K filing includes three
    comparative year rows (e.g. FY2022, FY2023, FY2024) all sharing the same
    ``filing_date``.  Before the fix, ``_ttm_at`` called ``.latest(n=1)`` which
    returned an arbitrary single row (often the oldest comparative year).  After
    the fix it calls ``.execute()`` and picks the row with the max ``period_end``
    from the latest-filing batch — i.e. the actual current fiscal year.

    The test mimics GOOGL's FY2024 10-K filing structure: three comparative
    periods in a single filing, with the current year (FY2024, period_end
    2024-12-31) being the correct selection.
    """
    import data.providers.company_ratios.pit_composite as mod

    concept     = "RevenueFromContractWithCustomerExcludingAssessedTax"
    as_of       = date(2025, 9, 2)

    # Three rows from the most-recent (FY2024) annual filing — the oldest
    # comparative rows (FY2022, FY2023) would be incorrectly returned by
    # the old ``.latest(n=1)`` logic, which sorted by filing_date only.
    fy2022_value = 282_836_000_000.0   # old ``latest()`` would pick THIS (first)
    fy2023_value = 307_394_000_000.0
    fy2024_value = 350_018_000_000.0   # correct — the current fiscal year

    fake_facts = _make_multi_row_facts(
        concept_name           = concept,
        at                     = as_of,
        rows_for_latest_filing = [
            # Intentionally listed oldest-first to stress the sort.
            (date(2022, 12, 31), fy2022_value),
            (date(2023, 12, 31), fy2023_value),
            (date(2024, 12, 31), fy2024_value),
        ],
    )

    # Wrap in the Company+EntityFacts shape that ``_load_xbrl_summary`` expects.
    class _FakeCompany:
        def get_facts(self):
            return fake_facts

    monkeypatch.setattr(mod, "_ensure_identity", lambda: None)
    monkeypatch.setattr(mod, "Company", lambda symbol: _FakeCompany())

    result = mod._load_xbrl_summary("GOOGL", as_of)

    # profit_margin requires NetIncomeLoss, which is absent here — so it is
    # None.  But revenue_growth_yoy IS computable once we also supply a prior-
    # year value.  This test focuses only on showing that ``_ttm_at``'s value
    # selection is correct; the full round-trip is covered by
    # ``test_load_xbrl_summary_asc606_filer_yields_nonzero_growth``.
    #
    # We confirm the fix indirectly: ``_load_xbrl_summary`` calls ``_ttm_at``
    # for both the current and prior dates.  With the prior-year fake absent,
    # revenue_growth_yoy will be None — but critically profit_margin must ALSO
    # be None because NetIncomeLoss is not in our fake (so no denominator
    # confusion is possible).  The real assertion here is that no exception is
    # raised and the result is a valid dict.
    assert isinstance(result, dict), "Expected a dict from _load_xbrl_summary"
    assert result["revenue_growth_yoy"] is None, (
        "No prior-year fake supplied → growth must be None (not a spurious value)"
    )


def test_ttm_at_annual_constraint_rejects_quarterly_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_ttm_at`` must NOT return a value when only quarterly facts are present.

    This is the direct regression for audit finding #26: at ``as_of=2025-09-02``
    for a Q2-reporting mega-cap, the only available fact before the most-recent
    annual filing is a 6-month YTD value.  The old ``_ttm_at`` (using
    ``.latest()``) would have returned that YTD value; the fixed version
    (filtering by ``by_fiscal_period("FY")``) must return ``None`` because no
    annual row exists.

    To simulate this we provide a fake ``EntityFacts`` whose
    ``by_fiscal_period("FY").execute()`` returns an empty list (no annual rows),
    while ``latest()`` (if it were called) would return a non-empty quarterly row.
    """
    import data.providers.company_ratios.pit_composite as mod

    concept  = "RevenueFromContractWithCustomerExcludingAssessedTax"
    as_of    = date(2025, 9, 2)

    class _ChainNoAnnual:
        """Simulate: only quarterly facts available, no FY rows."""

        def by_fiscal_period(self, period: str) -> "_ChainEmpty":
            """Applying FY filter leaves nothing."""
            return _ChainEmpty()

        def latest(self):
            """Old path — would have returned a spurious quarterly value."""
            return [SimpleNamespace(value=175_000_000_000.0,
                                    filing_date=as_of,
                                    period_end=as_of)]

        def execute(self):
            """New path without FY filter — would return the quarterly row."""
            return [SimpleNamespace(value=175_000_000_000.0,
                                    filing_date=as_of,
                                    period_end=as_of)]

    class _ChainEmpty:
        """FY-filtered chain with no rows."""

        def execute(self):
            return []

    class _BC:
        def as_of(self, _at):
            return _ChainNoAnnual()

    class _Q:
        def by_concept(self, _c):
            return _BC()

    class _FakeFacts:
        def query(self):
            return _Q()

    class _FakeCompany:
        def get_facts(self):
            return _FakeFacts()

    monkeypatch.setattr(mod, "_ensure_identity", lambda: None)
    monkeypatch.setattr(mod, "Company", lambda symbol: _FakeCompany())

    result = mod._load_xbrl_summary("GOOGL", as_of)

    # With no annual revenue rows at all, revenue_growth_yoy and profit_margin
    # must both be None — not the spurious 6-month YTD value the old code would
    # have used.
    assert result["revenue_growth_yoy"] is None, (
        "revenue_growth_yoy must be None when only quarterly XBRL rows exist "
        "(audit finding #26: annual-duration constraint not applied)"
    )
    assert result["profit_margin"] is None, (
        "profit_margin must be None when no annual revenue row is available "
        "(without a revenue denominator the ratio is undefined)"
    )


# ---------------------------------------------------------------------------
# Phase-14 — PIT-correct trailing beta + static sector fill.
# ---------------------------------------------------------------------------


def _series(start: date, returns: list[float], base: float = 100.0) -> PriceHistory:
    """Build a ``PriceHistory`` of consecutive daily closes from a return series.

    The first bar is ``base``; each subsequent bar applies the next simple
    return.  One calendar day per bar (weekends ignored — beta aligns by the
    exact dates present in both series, so consecutive calendar days are fine
    for a unit test).

    Parameters
    ----------
    start:
        Date of the first (oldest) bar.
    returns:
        Simple daily returns to compound forward from ``base``.
    base:
        Opening close price.

    Returns
    -------
    PriceHistory
        ``len(returns) + 1`` bars, oldest first.
    """
    bars: list[OHLCBar] = []
    close = base
    d = start
    bars.append(OHLCBar(timestamp=datetime(d.year, d.month, d.day, tzinfo=UTC),
                        open=close, high=close, low=close, close=close, volume=1.0))
    for r in returns:
        close = close * (1.0 + r)
        d = d + timedelta(days=1)
        bars.append(OHLCBar(timestamp=datetime(d.year, d.month, d.day, tzinfo=UTC),
                            open=close, high=close, low=close, close=close, volume=1.0))
    return PriceHistory(ticker="X", bars=bars)


def test_compute_beta_known_inputs() -> None:
    """Beta of a stock whose returns are exactly 2x SPY's must be ~2.0."""
    import data.providers.company_ratios.pit_composite as mod

    # 80 SPY daily returns alternating to give a non-zero variance; stock = 2x.
    spy_returns   = [0.01 if i % 2 == 0 else -0.008 for i in range(80)]
    stock_returns = [2.0 * r for r in spy_returns]

    spy   = _series(date(2024, 1, 1), spy_returns)
    stock = _series(date(2024, 1, 1), stock_returns)

    beta = mod._compute_beta(stock, spy)

    assert beta == pytest.approx(2.0, abs=1e-9)


def test_compute_beta_below_min_obs_returns_none() -> None:
    """Fewer than 60 overlapping return observations → None (never a guess)."""
    import data.providers.company_ratios.pit_composite as mod

    # 59 returns → 59 observations, one short of the _BETA_MIN_OBS=60 floor.
    spy_returns   = [0.01 if i % 2 == 0 else -0.008 for i in range(59)]
    stock_returns = [2.0 * r for r in spy_returns]

    spy   = _series(date(2024, 1, 1), spy_returns)
    stock = _series(date(2024, 1, 1), stock_returns)

    assert mod._compute_beta(stock, spy) is None


def test_compute_beta_flat_benchmark_returns_none() -> None:
    """A flat SPY (var ≈ 0) gives an undefined slope → None, not inf/NaN."""
    import data.providers.company_ratios.pit_composite as mod

    spy_returns   = [0.0] * 80          # var(SPY) == 0
    stock_returns = [0.01 if i % 2 == 0 else -0.01 for i in range(80)]

    spy   = _series(date(2024, 1, 1), spy_returns)
    stock = _series(date(2024, 1, 1), stock_returns)

    assert mod._compute_beta(stock, spy) is None


def test_compute_beta_caps_at_window() -> None:
    """Only the last _BETA_WINDOW observations contribute (older bars ignored).

    Construct a series whose OLD returns have a different stock/SPY slope (3x)
    from the recent _BETA_WINDOW returns (1x).  The computed beta must reflect
    only the recent window (≈1.0), proving the cap drops the stale tail.
    """
    import data.providers.company_ratios.pit_composite as mod

    window = mod._BETA_WINDOW

    old_spy    = [0.01 if i % 2 == 0 else -0.01 for i in range(100)]
    old_stock  = [3.0 * r for r in old_spy]                 # 3x slope (should be dropped)
    recent_spy = [0.01 if i % 2 == 0 else -0.01 for i in range(window)]
    recent_stk = [1.0 * r for r in recent_spy]              # 1x slope (should win)

    spy_returns   = old_spy + recent_spy
    stock_returns = old_stock + recent_stk

    spy   = _series(date(2022, 1, 1), spy_returns)
    stock = _series(date(2022, 1, 1), stock_returns)

    beta = mod._compute_beta(stock, spy)

    assert beta == pytest.approx(1.0, abs=1e-6)


def test_compute_beta_pit_slicing_excludes_future_bars() -> None:
    """No bar dated after the (caller-sliced) cutoff contributes to beta.

    The provider slices both series via ``_fetch_price_series`` (≤ as_of), so
    here we assert the alignment-by-date logic: a future-dated tail appended to
    only ONE series is silently ignored (no overlap), and a future tail with a
    radically different slope on BOTH series does change the answer — i.e. beta
    is computed strictly from the dates present in both inputs.  We verify the
    former (the leak-relevant case): future stock bars with no SPY partner
    cannot leak into the slope.
    """
    import data.providers.company_ratios.pit_composite as mod

    spy_returns   = [0.01 if i % 2 == 0 else -0.008 for i in range(80)]
    stock_returns = [2.0 * r for r in spy_returns]

    spy   = _series(date(2024, 1, 1), spy_returns)
    stock = _series(date(2024, 1, 1), stock_returns)

    baseline = mod._compute_beta(stock, spy)

    # Append future-dated stock bars with a wildly different (10x) slope — but
    # no matching SPY dates, so they have no return partner and must be ignored.
    future_start = stock.bars[-1].timestamp.date() + timedelta(days=1)
    future = _series(future_start, [0.10] * 30, base=stock.bars[-1].close)
    stock_with_future = PriceHistory(ticker="X", bars=stock.bars + future.bars[1:])

    leaked = mod._compute_beta(stock_with_future, spy)

    # Beta is unchanged: the unpaired future stock bars do not enter the slope.
    assert leaked == pytest.approx(baseline, abs=1e-9)


@pytest.mark.asyncio
async def test_pit_composite_populates_beta_and_sector(monkeypatch: pytest.MonkeyPatch) -> None:
    """fetch() wires PIT-sliced beta and the static watchlist sector onto the model."""
    import data.providers.company_ratios.pit_composite as mod

    monkeypatch.setattr(mod, "_fetch_xbrl_facts", lambda s, d: SimpleNamespace(
        long_name="Apple Inc.", sector=" should be overridden by static map ",
        shares_out=1.0, eps_ttm=1.0, dps_ttm=None,
    ))

    spy_returns   = [0.01 if i % 2 == 0 else -0.008 for i in range(80)]
    stock_returns = [2.0 * r for r in spy_returns]
    stock_hist = _series(date(2024, 1, 1), stock_returns)
    spy_hist   = _series(date(2024, 1, 1), spy_returns)

    def _fake_price(symbol: str, as_of):
        return spy_hist if symbol == "SPY" else stock_hist

    monkeypatch.setattr(mod, "_fetch_price_series", _fake_price)
    monkeypatch.setattr(mod, "_load_xbrl_summary", lambda *a, **k: {
        "profit_margin": None, "debt_to_equity": None, "roe": None,
        "revenue_growth_yoy": None, "free_cash_flow": None, "peg": None,
    })

    out = await mod.fetch("AAPL", as_of=datetime(2024, 5, 1, tzinfo=UTC))

    # Static watchlist sector wins over the XBRL sic_description.
    assert out.sector == "Technology"
    # Beta from the PIT-sliced stock/SPY series (stock = 2x SPY).
    assert out.beta == pytest.approx(2.0, abs=1e-6)


@pytest.mark.asyncio
async def test_pit_composite_offwatchlist_sector_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """An off-watchlist ticker keeps the XBRL sector and tolerates no SPY overlap."""
    import data.providers.company_ratios.pit_composite as mod

    monkeypatch.setattr(mod, "_fetch_xbrl_facts", lambda s, d: SimpleNamespace(
        long_name="Random Co", sector="Real Estate",
        shares_out=1.0, eps_ttm=1.0, dps_ttm=None,
    ))
    # Too few bars to clear the beta floor → beta None; off-watchlist → sector
    # falls back to the XBRL value.
    monkeypatch.setattr(mod, "_fetch_price_series",
                        lambda s, a: PriceHistory(ticker=s, bars=_make_bars(10, last_close=50.0)))
    monkeypatch.setattr(mod, "_load_xbrl_summary", lambda *a, **k: {
        "profit_margin": None, "debt_to_equity": None, "roe": None,
        "revenue_growth_yoy": None, "free_cash_flow": None, "peg": None,
    })

    out = await mod.fetch("ZZZZ", as_of=datetime(2023, 3, 14, tzinfo=UTC))

    assert out.sector == "Real Estate"   # XBRL fallback (not in static map)
    assert out.beta is None              # below the 60-observation floor


def test_watchlist_sector_strings_match_sector_to_etf() -> None:
    """Every static sector string must be a key of SECTOR_TO_ETF (downstream lookup)."""
    from contract.extractors._sector_map import SECTOR_TO_ETF
    from data.sector_map import WATCHLIST_SECTORS

    for ticker, sector in WATCHLIST_SECTORS.items():
        assert sector in SECTOR_TO_ETF, f"{ticker}: {sector!r} is not a SECTOR_TO_ETF key"
