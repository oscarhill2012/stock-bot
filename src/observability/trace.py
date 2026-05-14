"""Append-only JSON snapshot collector for one tick.

Production runs do not instantiate this; the ``trace_tick.py`` entrypoint
sets ``state["_trace"]`` to a TraceWriter, and every callback opportunistically
routes through ``_trace_maybe(state, ...)``.  Production tick state has no
``"_trace"`` key, so the helper is a single dict lookup no-op.
"""
from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any


class TraceWriter:
    """Collect labelled JSON sections for one tick; flush to disk on demand.

    Each section is stored under an ordered label key.  Sections are written
    in insertion order (Python 3.7+ dicts preserve insertion order).

    Usage::

        tw = TraceWriter()
        tw.snapshot("01_fetch_news", {"AAPL": {"headlines": []}})
        tw.finalise(Path("docs/surface-traces/trace-20260513.json"))
    """

    def __init__(self) -> None:
        """Initialise an empty section store."""
        # Python 3.7+ dicts are insertion-ordered; no need for OrderedDict.
        self._sections: dict[str, Any] = {}

    # ── deepcopy / copy passthrough ──────────────────────────────────────────
    # ADK's InMemorySessionService deep-copies session state on every
    # ``create_session`` and ``get_session``.  If we let the writer be cloned,
    # each agent would mutate a different copy and the harness would flush an
    # empty one at the end.  Returning ``self`` from ``__deepcopy__`` /
    # ``__copy__`` makes the writer a shared singleton across all copies of
    # the session state for one tick, which is exactly what we want.
    def __deepcopy__(self, memo: dict) -> TraceWriter:
        """Identity passthrough so all session-state copies share one writer."""
        return self

    def __copy__(self) -> TraceWriter:
        """Identity passthrough — see ``__deepcopy__``."""
        return self

    def snapshot(
        self,
        label: str,
        payload: Any,
        *,
        state_keys: list[str] | None = None,
    ) -> None:
        """Append one labelled JSON section to the trace.

        Parameters
        ----------
        label:
            Short identifier for this boundary (e.g. ``"01_fetch_news"``).
        payload:
            Arbitrary JSON-serialisable data produced at this boundary.
        state_keys:
            Optional list of session-state keys included in this snapshot,
            recorded alongside the payload for debugging purposes.
        """
        record: dict[str, Any] = {"data": payload}

        if state_keys is not None:
            # Record which state keys were sampled so the reader can cross-
            # reference the raw session state if needed.
            record["state_keys"] = state_keys

        self._sections[label] = record

    def llm_pair(
        self,
        label_base: str,
        prompt: str,
        response: str,
        *,
        model: str,
    ) -> None:
        """Append a paired LLM in/out section under ``{label_base}_in`` and ``{label_base}_out``.

        Parameters
        ----------
        label_base:
            Base label for the pair (e.g. ``"03_fundamental_llm"``).
        prompt:
            The exact text sent to the model this tick.
        response:
            The raw model response text.
        model:
            Model identifier string (e.g. ``"gemini-2.5-flash-lite"``).
        """
        self._sections[f"{label_base}_in"]  = {"model": model, "prompt": prompt}
        self._sections[f"{label_base}_out"] = {"model": model, "response": response}

    def finalise(self, out_path: Path) -> None:
        """Flush the trace to disk as a single JSON document.

        Creates parent directories as needed.  The output is one JSON object
        keyed by label; section order matches insertion order.

        Parameters
        ----------
        out_path:
            Destination file path.  Will be created (or overwritten) atomically
            via a direct ``write_text`` call.
        """
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(self._sections, indent=2, default=str))


def _trace_maybe(
    state: Any,
    label: str,
    payload: Any,
    *,
    state_keys: list[str] | None = None,
) -> None:
    """No-op trace hook — calls ``TraceWriter.snapshot`` iff ``state["_trace"]`` is set.

    Designed to be sprinkled at every pipeline boundary with zero overhead on
    production paths.  When ``state`` is a plain dict with no ``"_trace"`` key
    the function performs a single dict lookup and returns immediately — no
    allocations, no exceptions.

    When ``state["_trace"]`` is a ``TraceWriter`` instance, the payload is
    appended as a new section under ``label``.

    An ``isinstance(state, dict)`` guard prevents ``AttributeError`` if a non-
    dict state subclass (e.g. an ADK ``Session.state`` proxy) is passed; in
    that case the hook silently does nothing.

    Parameters
    ----------
    state:
        The pipeline session state dict (or dict-like object).
    label:
        Section label to pass to ``TraceWriter.snapshot``.
    payload:
        Data to record.
    state_keys:
        Optional list of state keys to record alongside the payload.
    """
    # Duck-typed lookup: ADK's ``Session.state`` is a ``State`` object that
    # is NOT a ``dict`` subclass but does expose a dict-like ``.get`` API.
    # A previous isinstance(state, dict) guard silently no-op'd every hook
    # because ``State`` fails that check.
    try:
        tw = state.get("_trace")
    except (AttributeError, TypeError):
        return
    if tw is None:
        return

    # Route to the writer; any serialisation errors are silently swallowed so
    # the no-op *production* path is never affected by trace-side failures.
    with contextlib.suppress(Exception):
        tw.snapshot(label, payload, state_keys=state_keys)


# ── Shared LLM trace callback factory ────────────────────────────────────────


def _extract_content_text(content: Any) -> str:
    """Concatenate every text part of a single ADK ``Content`` into one string.

    Parameters
    ----------
    content:
        An ADK ``Content`` object — has a ``.parts`` list whose entries may
        carry a ``.text`` attribute. Non-text parts are silently skipped.

    Returns
    -------
    str
        The concatenated text, or an empty string if no text parts exist.
    """
    if content is None:
        return ""

    parts = getattr(content, "parts", None) or []
    chunks: list[str] = []

    for part in parts:
        text = getattr(part, "text", None)
        if text:
            chunks.append(text)

    return "\n".join(chunks)


def make_llm_trace_callbacks(section_name: str, *, model: str) -> tuple[Callable, Callable]:
    """Build paired before/after model callbacks that capture the LLM round-trip.

    Captures BOTH the rendered system instruction (which contains the
    ``{news_context}`` / ``{fundamental_context}`` placeholders after ADK
    substitution) AND the user-side ``contents``. Pre-Phase-5 helpers only
    captured ``contents`` and silently dropped the system instruction, which
    meant every surface trace was missing the actual article / filing text the
    LLM saw.

    The captured prompt is structured as::

        === system ===
        <rendered system instruction>
        === user ===
        <user contents>

    Both callbacks are no-ops when ``state["_trace"]`` is not a
    ``TraceWriter`` — production runs pay a single dict lookup.

    Parameters
    ----------
    section_name:
        Base label for the trace section (e.g. ``"03_news_llm"``). The
        before-callback writes to ``{section_name}_in``; the after-callback
        writes to ``{section_name}_out``.
    model:
        Model identifier string to record alongside the prompt + response
        (e.g. ``"gemini-2.5-flash-lite"``).

    Returns
    -------
    (before, after):
        Two callables matching ADK's ``before_model_callback`` and
        ``after_model_callback`` signatures.
    """

    def _state_writer(ctx: Any) -> TraceWriter | None:
        """Look up the TraceWriter on ``ctx.state``; return None if absent."""
        state = ctx.state
        try:
            tw = state.get("_trace")
        except (AttributeError, TypeError):
            return None
        return tw if isinstance(tw, TraceWriter) else None

    def _before(callback_context: Any, llm_request: Any) -> Any:
        """Capture system + user prompt portions into the trace writer."""
        tw = _state_writer(callback_context)
        if tw is None:
            return None

        # System instruction (where {news_context} / {fundamental_context}
        # / {tickers} are substituted) lives on llm_request.config.system_instruction.
        config = getattr(llm_request, "config", None)
        system_text = _extract_content_text(getattr(config, "system_instruction", None))

        # User contents — the historical capture target.
        user_chunks: list[str] = []
        for content in (getattr(llm_request, "contents", None) or []):
            user_chunks.append(_extract_content_text(content))
        user_text = "\n---\n".join(c for c in user_chunks if c)

        prompt = (
            "=== system ===\n"
            f"{system_text or '(no system instruction)'}\n"
            "=== user ===\n"
            f"{user_text or '(no user content)'}"
        )

        tw.llm_pair(section_name, prompt=prompt, response="(pending)", model=model)
        return None

    def _after(callback_context: Any, llm_response: Any) -> Any:
        """Overwrite the ``(pending)`` placeholder with the model's response text."""
        tw = _state_writer(callback_context)
        if tw is None:
            return None

        response_text = _extract_content_text(getattr(llm_response, "content", None))

        # Intentional direct write — overwrites the ``(pending)`` placeholder set
        # by ``llm_pair`` in ``_before`` so the ``_in`` entry isn't duplicated.
        # ``TraceWriter`` has no public update method for this case yet.
        tw._sections[f"{section_name}_out"] = {
            "model": model,
            "response": response_text or "(no text parts)",
        }
        return None

    return _before, _after
