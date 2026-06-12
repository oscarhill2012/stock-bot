"""Cloud Scheduler shells (A-090, audit §8.5 — keep).

These functions are intentional scaffolding for the Cloud Scheduler
deployment path. The 2026-05-26 audit flagged them as P3 dead-code; the
human gate decision (intent.md §8.5) is **keep** because Cloud Scheduler
is the planned deployment topology. Do not delete without revisiting §8.5.

Thin wrapper over the gcloud CLI; the subprocess calls are monkey-patched
to no-ops under tests.
"""
from __future__ import annotations

import subprocess


def pause_job(name: str) -> None:
    """Pause a Cloud Scheduler job. No-op shim under tests."""
    subprocess.run(
        ["gcloud", "scheduler", "jobs", "pause", name],
        check=True,
    )


def resume_job(name: str) -> None:
    """Resume a Cloud Scheduler job. No-op shim under tests."""
    subprocess.run(
        ["gcloud", "scheduler", "jobs", "resume", name],
        check=True,
    )
