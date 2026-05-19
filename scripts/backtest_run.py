"""CLI: drive one full backtest run for a configured window.

Usage::

    PYTHONPATH=src python -m scripts.backtest_run --window svb-stress-2023-03

The script loads ``config/backtest_settings.json`` and
``config/backtest_windows.json`` via the ``Runner`` class, materialises a
run directory under ``<runs_root>/<run-id>/``, executes every scheduled tick,
and prints the run summary.  Exit code is non-zero if the run is aborted (i.e.
the failure-tick ratio was exceeded).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

from backtest.runner import Runner


def main() -> None:
    """CLI entrypoint for a full backtest run.

    Parses ``--window`` (required) from ``sys.argv``, delegates to
    ``Runner().run()``, and exits with code 1 if the run was aborted.
    """
    # Load ``.env`` *before* anything reads ``os.environ``.  In a backtest
    # replay every data provider reads from the SQLite cache rather than a
    # live API, so the lazy ``data.secrets._ensure_loaded()`` path never
    # fires — without this call, ADK's LLM client can't see Vertex AI vars
    # (``GOOGLE_GENAI_USE_VERTEXAI`` / ``GOOGLE_CLOUD_PROJECT`` etc.) and
    # falls back to API-key mode with no key.  ``load_dotenv`` is idempotent
    # and respects pre-existing env vars (override=False by default).
    load_dotenv()

    # Strict-as_of mode is mandatory for backtests — a missing as_of at any
    # provider or writer site must abort the run rather than fabricate a
    # wall-clock substitute.  See src/data/timeguard.py.
    os.environ["STOCKBOT_STRICT_AS_OF"] = "1"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Run one full backtest window.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  PYTHONPATH=src python -m scripts.backtest_run "
            "--window svb-stress-2023-03"
        ),
    )
    parser.add_argument(
        "--window",
        required=True,
        metavar="KEY",
        help="window key in config/backtest_windows.json",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help=(
            "optional cap on the number of ticks to execute (e.g. --limit 1 "
            "for a single-tick sanity run).  Default: run every scheduled tick."
        ),
    )
    parser.add_argument(
        "--run-id",
        dest="run_id",
        default=None,
        metavar="NAME",
        help=(
            "optional override for the run-id (and therefore the artefact "
            "directory name).  Default: <window>-<git-sha7>."
        ),
    )
    args = parser.parse_args()

    result = Runner().run(
        args.window,
        tick_limit       = args.limit,
        run_id_override  = args.run_id,
    )

    print(f"run_id:  {result.run_id}")
    print(f"run_dir: {result.run_dir}")
    print(f"status:  {result.status}")

    if result.status == "aborted":
        sys.exit(1)


if __name__ == "__main__":
    main()
