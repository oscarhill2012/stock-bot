"""Resolve a config model ID into the argument ADK's ``LlmAgent`` expects.

Why this exists
---------------
``config/models.json`` is the single source of truth for every model ID (see
``src/config/models.py``).  Historically every value there was a native
Gemini ID (e.g. ``"gemini-2.5-pro"``), which ADK accepts as a bare string and
dispatches through its built-in ``google-genai`` client — billed to Vertex
when ``GOOGLE_GENAI_USE_VERTEXAI=true``.

To bill a Claude model through the *same* Google Cloud project we route it via
ADK's native Anthropic-on-Vertex integration
(``google.adk.models.anthropic_llm.Claude``), which wraps the official
``anthropic`` SDK's ``AsyncAnthropicVertex`` client.  ADK does not accept a
bare Claude string for this path — the model must be handed over as a
``Claude`` instance.  This helper is the single place that decides which form
a given config ID needs, so every agent construction site can keep reading one
plain string from ``config/models.json`` and hand it straight here.

The native-vs-Claude rule
-------------------------
A native Gemini ID never contains a ``"/"`` (e.g. ``"gemini-2.5-flash"``,
``"text-embedding-005"``).  A Claude-on-Vertex ID is provider-prefixed and
*also carries its serving region* in the form::

    anthropic_vertex/<region>/<model>
    e.g. anthropic_vertex/global/claude-haiku-4-5@20251001

The presence of a ``"/"`` is therefore the unambiguous signal that an ID must
be dispatched through the native Claude path rather than ADK's native Gemini
path.

(Detecting the slash rather than a ``"gemini-"`` prefix is also deliberate:
it keeps this module free of the model-name literal that the
``tests/contract/test_no_hardcoded_models.py`` contract forbids in ``src/``.)

Why the region lives in the ID
------------------------------
Claude is **not** served from every Vertex region — in particular not from
``us-central1``, which the native-Gemini strategist uses via
``GOOGLE_CLOUD_LOCATION``.  Rather than overload that env var (which would drag
Gemini onto a Claude region too), the Claude region travels *inside* the model
ID.  ADK's ``Claude`` client parses the project and region out of a full
``projects/.../locations/<region>/...`` resource path and uses them verbatim,
overriding ``GOOGLE_CLOUD_LOCATION``.  So this helper assembles that resource
path from three pieces:

* the **GCP project** — read from ``GOOGLE_CLOUD_PROJECT`` (a deployment
  secret, kept out of version-controlled config);
* the **region** — taken from the model ID's middle segment;
* the **bare model name** — the final segment.

The upshot: swapping the analysts between Gemini and Claude is a one-line edit
in ``config/models.json`` and touches no source and no ``.env``.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Imported only for type-checkers; the runtime import is lazy (see below)
    # so a pure-Gemini deployment never needs the optional ``anthropic`` SDK
    # imported on its hot path.
    from google.adk.models.anthropic_llm import Claude


def resolve_model(model_id: str) -> str | Claude:
    """Return the ``model=`` argument ADK's ``LlmAgent`` should receive.

    Parameters
    ----------
    model_id:
        A model identifier as stored in ``config/models.json`` — either a
        native Gemini ID (no ``"/"``, e.g. ``"gemini-2.5-flash"``) or a
        Claude-on-Vertex ID of the form ``anthropic_vertex/<region>/<model>``
        (e.g. ``"anthropic_vertex/global/claude-haiku-4-5@20251001"``).

    Returns
    -------
    str | Claude
        The original string unchanged for a native Gemini ID, or a configured
        ``google.adk.models.anthropic_llm.Claude`` instance for a
        Claude-on-Vertex ID.

    Raises
    ------
    ValueError
        If a Claude-on-Vertex ID is malformed (missing the ``<region>`` or
        ``<model>`` segment) or if ``GOOGLE_CLOUD_PROJECT`` is not set in the
        environment.  These are raised loudly rather than silently falling
        back to a default, which would mis-bill or mis-route the call.
    ImportError
        If a Claude-on-Vertex ID is supplied but the optional ``anthropic``
        SDK is not installed (``pip install anthropic``).  Allowed to surface
        rather than degrading to a native string, which would route the call
        to the wrong provider.
    """

    # No provider prefix → native Gemini.  ADK consumes the bare string and
    # routes it through google-genai (Vertex when GOOGLE_GENAI_USE_VERTEXAI is
    # set).  Nothing to wrap.
    if "/" not in model_id:
        return model_id

    # Provider-prefixed → Claude on Vertex.  The ID encodes its own serving
    # region: ``<provider>/<region>/<model>``.  We split into exactly three
    # parts; anything else is a malformed ID and must fail loudly.
    parts = model_id.split("/")

    if len(parts) != 3 or not parts[1] or not parts[2]:
        raise ValueError(
            "Claude-on-Vertex model IDs must have the form "
            "'<provider>/<region>/<model>' (e.g. "
            "'anthropic_vertex/global/claude-haiku-4-5@20251001'); got "
            f"{model_id!r}."
        )

    _provider, region, bare_model = parts

    # The GCP project is a deployment secret, so it is read from the
    # environment rather than committed to config/models.json.
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")

    if not project:
        raise ValueError(
            "GOOGLE_CLOUD_PROJECT must be set in the environment to route a "
            f"Claude-on-Vertex model ({model_id!r}); it is unset or empty."
        )

    # Assemble the full Vertex resource path.  ADK's Claude client parses the
    # project and region out of this and uses them to construct the
    # AsyncAnthropicVertex client — overriding GOOGLE_CLOUD_LOCATION, so the
    # native-Gemini strategist on us-central1 is left untouched.
    resource_path = (
        f"projects/{project}/locations/{region}"
        f"/publishers/anthropic/models/{bare_model}"
    )

    # Import lazily so the optional ``anthropic`` SDK is only required when a
    # Claude model is actually configured; a pure-Gemini deployment never pays
    # the import cost.
    from google.adk.models.anthropic_llm import Claude

    return Claude(model=resource_path)
