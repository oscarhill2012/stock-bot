"""Cloud Scheduler shim — thin wrapper over gcloud CLI for monkey-patching."""
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
