"""Content guards for the strategist instruction template."""
from agents.strategist.prompts import STRATEGIST_INSTRUCTION


def test_prompt_explains_the_three_technical_reads_and_horizons():
    """The strategist prompt teaches the three technical reads and horizon precursors."""
    instr = STRATEGIST_INSTRUCTION

    assert "Reading the technical reads and analyst horizons" in instr
    # The three reads named.
    assert "reversal" in instr.lower()
    assert "Volatility regime" in instr
    assert "200d MA" in instr
    # Horizons framed as information, not a hold instruction.
    assert "horizon" in instr.lower()


def test_prompt_has_no_duplicate_holding_discipline_header():
    """Prompt hygiene: the holding-discipline guidance appears exactly once."""
    assert STRATEGIST_INSTRUCTION.count("### Holding discipline") == 1


# ---------------------------------------------------------------------------
# C3 — horizon prose must match the rendered/config horizons.
#
# The hardcoded prose in the "Reading the technical reads and analyst
# horizons" section previously disagreed with the horizons the model
# actually sees on each analyst's `horizon:` line: technical is ~5
# calendar days, fundamental filing_delta_horizon_days=90 (~3 months),
# news drift_horizon_days=20 (~3 weeks). The stale "~1-week news" number
# in particular fought the real 20-day news horizon.
# ---------------------------------------------------------------------------

def test_horizon_prose_matches_config_horizons():
    """The rendered prompt must cite the current horizon numbers, not stale ones."""
    instr = STRATEGIST_INSTRUCTION

    assert "~5-day" in instr, (
        "technical CONTRARIAN mean-reversion call must cite ~5-day, matching "
        "the ~5-calendar-day technical horizon"
    )
    assert "~3-month" in instr, (
        "fundamental lean must cite ~3-month (filing_delta_horizon_days=90)"
    )
    assert "~3-week" in instr, (
        "news lean must cite ~3-week (drift_horizon_days=20)"
    )


def test_horizon_prose_no_longer_has_stale_numbers():
    """The stale horizon numbers must not survive — they actively mislead the model."""
    instr = STRATEGIST_INSTRUCTION

    assert "5-10 day" not in instr, (
        "stale '5-10 day' mean-reversion window must be gone"
    )
    assert "~3-6 month" not in instr, (
        "stale '~3-6 month' fundamental horizon must be gone"
    )
    assert "~1-week" not in instr, (
        "stale '~1-week' news horizon must be gone — it directly fought the "
        "real 20-day (~3-week) news horizon"
    )


def test_assembled_prompt_has_thesis_book_header_exactly_once():
    """C2: '## Thesis Book' must appear exactly once in the assembled prompt.

    The template prints the header once above ``{temp:held_positions_view}``.
    ``_render_positions_shim`` (context_shim.py) must not ALSO emit the
    header in its rendered value, or the assembled prompt shows it twice
    back-to-back.
    """
    from agents.strategist.context_shim import StrategistContextShim
    from broker.portfolio import Portfolio

    state = {
        "user:positions": {
            "AAPL": {
                "rationale":    "iPhone launch",
                "opened_price": 210.0,
                "opened_at":    "2026-01-15T13:30:00+00:00",
            }
        },
        "portfolio": Portfolio(cash=0.0).model_dump(mode="json"),
    }
    held_view = StrategistContextShim().render(state)["temp:held_positions_view"]

    assembled = STRATEGIST_INSTRUCTION.replace(
        "{temp:held_positions_view}", held_view,
    )

    assert assembled.count("## Thesis Book") == 1, (
        "'## Thesis Book' must appear exactly once in the assembled prompt — "
        f"got {assembled.count('## Thesis Book')}"
    )
