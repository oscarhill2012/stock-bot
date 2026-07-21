import asyncio

from agents.analysts.news.embeddings import _default_embed, cosine_similarity
from config.models import _reset_cache


class _FakeEmbedding:
    """Stand-in for a single ``google.genai`` embedding result entry."""

    def __init__(self, values: list[float]) -> None:
        self.values = values


class _FakeEmbedContentResult:
    """Stand-in for the ``embed_content`` response shape consumed by
    ``_default_embed`` (only the ``.embeddings[0].values`` path is used)."""

    def __init__(self, values: list[float]) -> None:
        self.embeddings = [_FakeEmbedding(values)]


class _FakeModels:
    """Captures the kwargs passed to ``embed_content`` and returns a stub
    embedding so no network call is made."""

    def embed_content(self, *, model: str, contents: str):
        return _FakeEmbedContentResult([0.1, 0.2, 0.3])


class _FakeGenaiClient:
    """Stand-in for ``google.genai.Client`` that records the constructor
    kwargs it was called with, so the test can assert ``location`` flows
    through from config without making any real Vertex AI call."""

    captured_kwargs: dict | None = None

    def __init__(self, **kwargs) -> None:
        # Record the kwargs on the class so the test can inspect them after
        # ``_default_embed`` has constructed (and discarded) the instance.
        _FakeGenaiClient.captured_kwargs = kwargs
        self.models = _FakeModels()


def test_default_embed_pins_client_to_configured_location(monkeypatch, tmp_path) -> None:
    """``_default_embed`` must construct ``genai.Client`` with ``location``
    equal to ``memory_embedding_location`` from ``config/models.json``.

    The ``global`` Vertex endpoint serves no embedding models at all, so the
    embedding client is deliberately pinned to a region (e.g. ``europe-west2``)
    independently of the ambient ``GOOGLE_CLOUD_LOCATION=global`` that the rest
    of the pipeline's generative agents inherit. This test proves that pin is
    wired through to the actual client construction call, not just present in
    config.
    """

    import json

    from google import genai

    # Point the models config loader at a temporary file carrying a distinct
    # embedding location, so a match against the real config value would not
    # accidentally pass this test for the wrong reason.
    cfg_file = tmp_path / "models.json"
    cfg_file.write_text(json.dumps({
        "strategist":                "gemini-2.5-pro",
        "news_analyst":               "gemini-2.5-flash-lite",
        "fundamental_analyst":        "gemini-2.5-flash-lite",
        "memory_embedding":           "text-embedding-005",
        "memory_embedding_location":  "europe-west2",
    }))

    # Redirect the default config path the production loader reads, and
    # clear the lru_cache so the new path is actually picked up.
    import config.models as models_module
    monkeypatch.setattr(models_module, "_DEFAULT_PATH", cfg_file)
    _reset_cache()

    # Swap out the real Vertex client for the recording fake.
    monkeypatch.setattr(genai, "Client", _FakeGenaiClient)

    result = asyncio.run(_default_embed("some memory text"))

    assert result == [0.1, 0.2, 0.3]
    assert _FakeGenaiClient.captured_kwargs is not None
    assert _FakeGenaiClient.captured_kwargs.get("location") == "europe-west2"

    # Clean up the cache so later tests in the suite re-read the real config.
    _reset_cache()


def test_cosine_self_similarity():
    assert cosine_similarity([1, 0, 0], [1, 0, 0]) == 1.0


def test_cosine_orthogonal():
    assert cosine_similarity([1, 0, 0], [0, 1, 0]) == 0.0


def test_cosine_symmetric():
    a, b = [1, 2, 3], [4, 5, 6]
    assert cosine_similarity(a, b) == cosine_similarity(b, a)


def test_cosine_range():
    import random
    for _ in range(10):
        a = [random.random() for _ in range(16)]
        b = [random.random() for _ in range(16)]
        v = cosine_similarity(a, b)
        assert -1.0 <= v <= 1.0
