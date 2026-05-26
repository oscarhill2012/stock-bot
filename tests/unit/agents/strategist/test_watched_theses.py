"""Tests for watched-thesis behaviour: update on non-held tickers.

Covers:
1.  PositionThesis(kind="held", ...) with full entry fields → valid.
2.  PositionThesis(kind="held", opened_at=None, ...) → ValidationError.
3.  PositionThesis(kind="watched", ...) with all entry fields None → valid.
4.  PositionThesis(kind="watched", opened_at=<some date>, ...) → ValidationError.
5.  apply_stance_to_thesis(update, prior_row=None) → watched row seeded.
6.  apply_stance_to_thesis(update, prior_row=<watched>) → rationale mutates.
7.  apply_stance_to_thesis(update, prior_row=<held>) → rationale UNCHANGED (Invariant 3).
8.  apply_stance_to_thesis(buy, prior_row=<watched>) → promotes to held,
    rationale = buy-stance rationale (not the prior watched rationale).
9.  apply_stance_to_thesis(sell, prior_row=<watched>) → raises ValueError.
10. _render_positions_shim with mixed held + watched → both sections rendered.
11. _render_positions_shim with watched only → "Watched theses" section shown,
    "Currently Held" section shows flat sentinel.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agents.executor._verb_dispatch import apply_stance_to_thesis
from agents.strategist.context_shim import _render_positions_shim
from agents.strategist.position_thesis import PositionThesis
from agents.strategist.stance_schema import TickerStance

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

_TS = datetime(2026, 5, 26, tzinfo=UTC)
_TICK_ID = "t-test"


def _make_held_thesis(**overrides) -> PositionThesis:
    """Build a minimal valid held PositionThesis.

    Supplies all four entry fields so the kind='held' validator passes.
    """
    defaults = {
        "ticker":                 "AAPL",
        "kind":                   "held",
        "opened_at":              _TS,
        "opened_tick_id":         "t-open",
        "opened_price":           150.0,
        "weight":                 0.10,
        "rationale":              "Original held rationale — FROZEN",
        "last_reviewed_at":       _TS,
        "last_reviewed_decision": "buy",
        "last_reviewed_reason":   "opened",
    }
    defaults.update(overrides)
    return PositionThesis(**defaults)


def _make_watched_thesis(**overrides) -> PositionThesis:
    """Build a minimal valid watched PositionThesis.

    Entry fields are all None; rationale is mutable.
    """
    defaults = {
        "ticker":                 "MSFT",
        "kind":                   "watched",
        "rationale":              "Initial watched view",
        "last_reviewed_at":       _TS,
        "last_reviewed_decision": "update",
        "last_reviewed_reason":   "seeded",
    }
    defaults.update(overrides)
    return PositionThesis(**defaults)


def _make_stance(**kwargs) -> TickerStance:
    """Build a minimal valid TickerStance; intent is required."""
    defaults = {"ticker": "AAPL"}
    defaults.update(kwargs)
    return TickerStance(**defaults)


# ---------------------------------------------------------------------------
# 1. kind="held" with full entry fields → valid
# ---------------------------------------------------------------------------


def test_position_thesis_held_with_full_fields_is_valid():
    """PositionThesis(kind='held', ...) with all entry fields → no exception."""
    thesis = _make_held_thesis()

    assert thesis.kind           == "held"
    assert thesis.opened_at      == _TS
    assert thesis.opened_price   == 150.0
    assert thesis.weight         == pytest.approx(0.10)


# ---------------------------------------------------------------------------
# 2. kind="held" with a missing entry field → ValidationError
# ---------------------------------------------------------------------------


def test_position_thesis_held_missing_entry_field_raises():
    """PositionThesis(kind='held', opened_at=None, ...) → ValidationError.

    All four entry fields must be non-None for held rows.
    """
    with pytest.raises(ValidationError, match="kind='held'"):
        PositionThesis(
            ticker                    = "AAPL",
            kind                      = "held",
            opened_at                 = None,      # ← missing
            opened_tick_id            = "t-open",
            opened_price              = 150.0,
            weight                    = 0.10,
            rationale                 = "x",
            last_reviewed_at          = _TS,
            last_reviewed_decision    = "buy",
            last_reviewed_reason      = "opened",
        )


# ---------------------------------------------------------------------------
# 3. kind="watched" with all entry fields None → valid
# ---------------------------------------------------------------------------


def test_position_thesis_watched_with_null_entry_fields_is_valid():
    """PositionThesis(kind='watched', ...) with all entry fields None → no exception."""
    thesis = _make_watched_thesis()

    assert thesis.kind           == "watched"
    assert thesis.opened_at      is None
    assert thesis.opened_tick_id is None
    assert thesis.opened_price   is None
    assert thesis.weight         is None


# ---------------------------------------------------------------------------
# 4. kind="watched" with entry fields set → ValidationError
# ---------------------------------------------------------------------------


def test_position_thesis_watched_with_entry_fields_set_raises():
    """PositionThesis(kind='watched', opened_at=<date>, ...) → ValidationError.

    Watched rows must have all entry fields as None — they carry no open record.
    """
    with pytest.raises(ValidationError, match="kind='watched'"):
        PositionThesis(
            ticker                    = "MSFT",
            kind                      = "watched",
            opened_at                 = _TS,       # ← forbidden on watched
            opened_tick_id            = "t-open",
            opened_price              = 100.0,
            weight                    = 0.05,
            rationale                 = "x",
            last_reviewed_at          = _TS,
            last_reviewed_decision    = "update",
            last_reviewed_reason      = "x",
        )


# ---------------------------------------------------------------------------
# 5. update with prior_row=None → seeds a watched row
# ---------------------------------------------------------------------------


def test_apply_stance_update_on_flat_ticker_creates_watched_row():
    """apply_stance_to_thesis(update, prior_row=None) must create a watched thesis.

    The strategist is recording a view on a ticker it doesn't yet hold.
    The resulting row carries kind='watched', weight=None, and rationale
    set to stance.reason.
    """
    stance = _make_stance(
        ticker  = "MSFT",
        intent  = "update",
        reason  = "Watching for breakout above resistance",
    )

    result = apply_stance_to_thesis(
        stance,
        prior_row          = None,
        fill_price         = None,
        tick_id            = _TICK_ID,
        as_of              = _TS,
        current_tick_index = 3,
    )

    assert result is not None
    assert result.kind                      == "watched"
    assert result.rationale                 == "Watching for breakout above resistance"
    assert result.weight                    is None
    assert result.opened_at                 is None
    assert result.opened_tick_id            is None
    assert result.opened_price              is None
    assert result.last_reviewed_decision    == "update"
    assert result.thesis_last_updated_tick  == 3


# ---------------------------------------------------------------------------
# 6. update with prior_row=<watched> → rationale mutates
# ---------------------------------------------------------------------------


def test_apply_stance_update_on_watched_row_mutates_rationale():
    """apply_stance_to_thesis(update, prior_row=<watched>) must mutate rationale.

    Watched rows are explicitly exempt from Invariant 3.  The latest
    update replaces the prior rationale with the new view.
    """
    prior = _make_watched_thesis(
        ticker    = "MSFT",
        rationale = "Old watched view — should be replaced",
    )

    stance = _make_stance(
        ticker = "MSFT",
        intent = "update",
        reason = "New view: macro tailwind shifted",
    )

    result = apply_stance_to_thesis(
        stance,
        prior_row          = prior,
        fill_price         = None,
        tick_id            = _TICK_ID,
        as_of              = _TS,
        current_tick_index = 5,
    )

    assert result is not None
    assert result.kind                      == "watched"
    assert result.rationale                 == "New view: macro tailwind shifted"
    assert result.last_reviewed_decision    == "update"
    assert result.thesis_last_updated_tick  == 5

    # Entry fields remain None — watched row was not promoted.
    assert result.weight         is None
    assert result.opened_at      is None
    assert result.opened_price   is None


# ---------------------------------------------------------------------------
# 7. update with prior_row=<held> → rationale UNCHANGED (Invariant 3)
# ---------------------------------------------------------------------------


def test_apply_stance_update_on_held_row_preserves_rationale():
    """apply_stance_to_thesis(update, prior_row=<held>) must NOT mutate rationale.

    Invariant 3 applies to held rows.  The review trail is refreshed but
    the frozen entry rationale must survive unchanged.
    """
    original_rationale = "Locked-in rationale — must not change"
    prior = _make_held_thesis(rationale=original_rationale)

    stance = _make_stance(
        intent = "update",
        reason = "Revised macro view",
    )

    result = apply_stance_to_thesis(
        stance,
        prior_row          = prior,
        fill_price         = None,
        tick_id            = _TICK_ID,
        as_of              = _TS,
        current_tick_index = 8,
    )

    assert result is not None
    assert result.rationale                 == original_rationale, (
        "update on held must not overwrite rationale — Invariant 3"
    )
    assert result.last_reviewed_decision    == "update"
    assert result.last_reviewed_reason      == "Revised macro view"
    assert result.thesis_last_updated_tick  == 8

    # Entry fields must be unchanged.
    assert result.weight       == prior.weight
    assert result.opened_at    == prior.opened_at
    assert result.opened_price == prior.opened_price


# ---------------------------------------------------------------------------
# 8. buy with prior_row=<watched> → promotes to held
# ---------------------------------------------------------------------------


def test_apply_stance_buy_on_watched_row_promotes_to_held():
    """apply_stance_to_thesis(buy, prior_row=<watched>) must promote to held.

    The resulting row must:
    - have kind='held'
    - carry all entry fields from the buy (not the watched row)
    - have rationale = BUY stance's rationale (NOT the watched view's rationale)
    - Invariant 3 is now in effect: this rationale is frozen going forward
    """
    prior_watched_rationale = "Watched view — should be discarded at promotion"
    buy_rationale           = "iPhone cycle turning; entry on the dip"

    prior = _make_watched_thesis(
        ticker    = "AAPL",
        rationale = prior_watched_rationale,
    )
    stance = _make_stance(
        ticker    = "AAPL",
        intent    = "buy",
        weight    = 0.05,
        rationale = buy_rationale,
        catalyst  = "iPhone 17 demand data",
    )

    result = apply_stance_to_thesis(
        stance,
        prior_row          = prior,
        fill_price         = 210.0,
        tick_id            = _TICK_ID,
        as_of              = _TS,
        current_tick_index = 6,
    )

    assert result is not None
    assert result.kind                      == "held"
    assert result.rationale                 == buy_rationale, (
        "promoted row must carry the BUY stance's rationale, "
        "not the prior watched view's rationale"
    )
    assert result.opened_at                 == _TS
    assert result.opened_tick_id            == _TICK_ID
    assert result.opened_price              == pytest.approx(210.0)
    assert result.weight                    == pytest.approx(0.05)
    assert result.catalyst                  == "iPhone 17 demand data"
    assert result.last_reviewed_decision    == "buy"
    assert result.thesis_last_updated_tick  == 6


# ---------------------------------------------------------------------------
# 9. sell with prior_row=<watched> → raises ValueError
# ---------------------------------------------------------------------------


def test_apply_stance_sell_on_watched_row_raises():
    """apply_stance_to_thesis(sell, prior_row=<watched>) must raise ValueError.

    A sell stance presupposes an active held position.  Watched rows have no
    position to sell.
    """
    prior = _make_watched_thesis(ticker="MSFT")
    stance = _make_stance(
        ticker = "MSFT",
        intent = "sell",
        reason = "Thesis invalidated",
    )

    with pytest.raises(ValueError, match="sell"):
        apply_stance_to_thesis(
            stance,
            prior_row  = prior,
            fill_price = None,
            tick_id    = _TICK_ID,
            as_of      = _TS,
        )


# ---------------------------------------------------------------------------
# 10. _render_positions_shim with mixed held + watched
# ---------------------------------------------------------------------------


def test_render_positions_shim_mixed_shows_both_sections():
    """_render_positions_shim with held + watched → output contains both sections.

    Held section must include "Opened at".
    Watched section must omit "Opened at" (no entry record).
    """
    positions = {
        "AAPL": _make_held_thesis(ticker="AAPL").model_dump(mode="json"),
        "MSFT": _make_watched_thesis(ticker="MSFT").model_dump(mode="json"),
    }

    rendered = _render_positions_shim(positions, current_tick_index=5)

    # Both section headers must be present.
    assert "## Currently Held"                    in rendered
    assert "## Watched theses (not in book)"      in rendered

    # Held block must show the open price and the frozen rationale.
    assert "AAPL"                                 in rendered
    assert "Opened at $"                          in rendered
    assert "Original held rationale"              in rendered

    # Watched block must show the evolving rationale but NOT an "Opened at" line
    # for the watched ticker (MSFT).
    assert "MSFT"                                 in rendered
    assert "Initial watched view"                 in rendered

    # Confirm "Opened at" line does not appear in the MSFT section by checking
    # that every "Opened at" occurrence precedes MSFT in the output.
    msft_idx = rendered.index("MSFT")
    opened_at_positions = [i for i in range(len(rendered)) if rendered[i:].startswith("Opened at")]
    assert all(pos < msft_idx for pos in opened_at_positions), (
        "Watched block (MSFT) must not contain 'Opened at' — no entry record"
    )


# ---------------------------------------------------------------------------
# 11. _render_positions_shim with watched only
# ---------------------------------------------------------------------------


def test_render_positions_shim_watched_only_shows_no_exposure_sentinel_and_watched_section():
    """_render_positions_shim with watched-only → no-exposure sentinel + watched section.

    When there are no held positions but watched theses exist, the
    "Currently Held" section shows the tighter "no exposure" sentinel
    rather than the "portfolio is flat" string (which is reserved for
    the truly-empty positions case — the bot has live views, just no
    open exposure).  The "Watched theses" section is still rendered.
    """
    positions = {
        "MSFT": _make_watched_thesis(ticker="MSFT").model_dump(mode="json"),
    }

    rendered = _render_positions_shim(positions, current_tick_index=2)

    # Currently Held section uses the tighter sentinel.
    assert "## Currently Held"                  in rendered
    assert "(None — no exposure currently.)"    in rendered
    # The "portfolio is flat" claim is reserved for the truly-empty case
    # and must NOT appear when watched theses exist.
    assert "portfolio is flat" not in rendered

    # Watched section must appear.
    assert "## Watched theses (not in book)"    in rendered
    assert "MSFT"                               in rendered
    assert "Initial watched view"               in rendered

    # No "Opened at" anywhere — there are no held positions at all.
    assert "Opened at $" not in rendered
