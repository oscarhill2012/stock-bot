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
