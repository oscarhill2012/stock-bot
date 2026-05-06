from orchestrator.state import TickState


def test_tick_state_defaults():
    ts = TickState()
    assert ts.tick_id == ""
    assert ts.tickers == []
    assert ts.memory_buffer == []
    assert ts.last_executed_tick_id is None


def test_tick_state_serializes():
    ts = TickState(tick_id="tick-001", tickers=["AAPL", "MSFT"])
    data = ts.model_dump()
    assert data["tick_id"] == "tick-001"
    assert "AAPL" in data["tickers"]
