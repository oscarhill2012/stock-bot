"""Unit tests for ``src/agents/model_resolver.py::resolve_model``.

Covers the native-Gemini-vs-Claude routing decision: a bare Gemini ID passes
through unchanged, while an ``anthropic_vertex/<region>/<model>`` ID is turned
into a native ADK ``Claude`` instance carrying the assembled Vertex resource
path.  Also covers the loud-failure paths (malformed ID, missing project).
"""
from __future__ import annotations

import pytest
from google.adk.models.anthropic_llm import Claude

from agents.model_resolver import resolve_model


def test_native_gemini_id_passes_through_unchanged() -> None:
    """A model ID with no provider prefix is returned verbatim as a string.

    ADK consumes the bare string via its native google-genai path, so the
    resolver must not wrap it.
    """

    result = resolve_model("gemini-2.5-flash")

    assert result == "gemini-2.5-flash"
    assert isinstance(result, str)


def test_claude_vertex_id_becomes_configured_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    """An ``anthropic_vertex/<region>/<model>`` ID becomes a ``Claude``.

    The resolver assembles a full Vertex resource path from the env project,
    the ID's region segment, and the ID's bare-model segment — that path is
    what ADK's Claude client parses for project/region selection.
    """

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project-123")

    result = resolve_model("anthropic_vertex/global/claude-haiku-4-5@20251001")

    assert isinstance(result, Claude)
    assert result.model == (
        "projects/test-project-123/locations/global"
        "/publishers/anthropic/models/claude-haiku-4-5@20251001"
    )


def test_region_segment_is_threaded_into_the_resource_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The middle segment of the ID selects the Vertex region verbatim.

    This is what keeps Claude off the strategist's ``us-central1`` without
    touching ``GOOGLE_CLOUD_LOCATION``.
    """

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project-123")

    result = resolve_model("anthropic_vertex/europe-west1/claude-haiku-4-5@20251001")

    assert isinstance(result, Claude)
    assert "/locations/europe-west1/" in result.model


def test_malformed_claude_id_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider-prefixed ID missing the region segment fails loudly.

    Silently defaulting the region would mis-route or mis-bill the call, so
    the resolver raises rather than guessing.
    """

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project-123")

    with pytest.raises(ValueError, match="must have the form"):
        resolve_model("anthropic_vertex/claude-haiku-4-5@20251001")


def test_missing_project_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Claude ID with no ``GOOGLE_CLOUD_PROJECT`` set fails loudly.

    The project is a deployment secret read from the environment; its absence
    is an operator error, not something to paper over.
    """

    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

    with pytest.raises(ValueError, match="GOOGLE_CLOUD_PROJECT"):
        resolve_model("anthropic_vertex/global/claude-haiku-4-5@20251001")
