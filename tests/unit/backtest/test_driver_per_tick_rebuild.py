"""The backtest driver rebuilds the pipeline per tick from state['tickers'].

Phase 9 contract: ``Driver._run_one_tick`` must call ``build_pipeline``
with the current ``state["tickers"]`` on every invocation, rather than
reusing a single pipeline built once at ``__init__`` time.  This allows
the News and Fundamental analyst branches to fan out across the watchlist
as it exists at each tick boundary.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_pipeline_built_per_tick_with_current_watchlist(tmp_path: Path) -> None:
    """Each ``_run_one_tick`` call invokes ``build_pipeline`` with
    the watchlist taken from ``state['tickers']`` at that tick.

    Two ticks with different watchlists confirm that the pipeline rebuild
    reads the *current* state rather than an initialisation-time snapshot.
    Also confirms that ``build_pipeline`` is NOT called at construction time.
    """
    from backtest.driver import Driver

    broker = MagicMock()
    broker.get_portfolio = MagicMock(return_value=MagicMock(model_dump=lambda mode: {}))

    # Create the run_dir artefact structure so Driver.__init__ does not fail
    # before we can assert on build_pipeline call count.
    (tmp_path / "manifest.json").write_text("{}")

    # ``build_runner`` is patched alongside ``build_pipeline`` because ADK
    # 1.34's ``App`` validates ``root_agent`` as a real ``BaseAgent`` — a
    # MagicMock pipeline would raise ``ValidationError`` inside the real
    # ``build_runner`` before we reach the assertions.  Patching it here
    # keeps the test focused on the per-tick rebuild contract without
    # touching real ADK runner infrastructure.
    with patch("backtest.driver.build_pipeline") as mock_build, \
         patch("backtest.driver.install_observability"), \
         patch("orchestrator.lifecycle_runner.build_runner") as mock_build_runner:

        mock_build.return_value = MagicMock()

        # Construction must NOT call build_pipeline — that is the key change
        # introduced by Phase 9 Task 14.
        driver = Driver(
            broker=broker,
            db_session=None,
            run_dir=tmp_path,
            window_key="test-window",
            enforce_pipeline_completion=False,
        )
        mock_build.assert_not_called()

        # Stub the ADK runner so _run_one_tick exercises the build path
        # without touching real ADK infrastructure.  The stub is attached to
        # the *runner* the driver actually iterates (``build_runner``'s
        # return value), not the pipeline.
        async def _stub_runner_run(*args, **kwargs):
            """No-op ADK runner — yields nothing."""
            if False:  # pragma: no cover — generator stub
                yield

        mock_build_runner.return_value.run_async = _stub_runner_run

        from observability.trace import TraceWriter

        # First tick — two tickers.
        await driver._run_one_tick({"tickers": ["AAPL", "MSFT"], "tick_id": "t1"}, TraceWriter())

        # Second tick — watchlist shrinks to one ticker to confirm the
        # rebuild reads state on every call, not a cached value.
        await driver._run_one_tick({"tickers": ["AAPL"], "tick_id": "t2"}, TraceWriter())

        # Exactly two pipeline builds — one per tick.
        assert mock_build.call_count == 2

        first_kwargs  = mock_build.call_args_list[0].kwargs
        second_kwargs = mock_build.call_args_list[1].kwargs

        assert first_kwargs["tickers"]  == ["AAPL", "MSFT"]
        assert second_kwargs["tickers"] == ["AAPL"]
