"""Curated catalogue of OpenRouter models surfaced in `nimbus model`.

This is intentionally small — we hand-pick a few free and paid models that we
know work well with Nimbus's tool-calling and storage agent prompts. Users
can still set any model ID via ``nimbus auth local --model …`` or the
``Custom`` entry in the picker.

Cost estimates are approximate and represent OpenRouter's published prices at
the time of writing. They exist so the picker can show ``~$0.005 / 1k tok``
hints; they are not used for billing or budgeting.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelChoice:
    """One entry in the model catalogue."""

    id: str
    """The OpenRouter model identifier, e.g. ``openai/gpt-4o-mini``."""

    label: str
    """Short human-readable name shown in the picker."""

    description: str
    """One-line caption: cost tier, context size, special features."""

    group: str
    """Group heading (``Free``, ``Paid``, ``Custom``) for visual sectioning."""


# Curated picks. Order within each group matters — first item is the default
# recommendation. Bring this list under workspace-level control later.
MODEL_CATALOG: tuple[ModelChoice, ...] = (
    # ── Free tier ──────────────────────────────────────────────────────
    ModelChoice(
        id="openai/gpt-oss-120b:free",
        label="openai/gpt-oss-120b",
        description="default — free, capable, good at tools",
        group="Free",
    ),
    ModelChoice(
        id="google/gemini-2.0-flash-exp:free",
        label="google/gemini-2.0-flash",
        description="free, fast, 1M context window",
        group="Free",
    ),
    ModelChoice(
        id="deepseek/deepseek-r1:free",
        label="deepseek/deepseek-r1",
        description="free, reasoning model",
        group="Free",
    ),
    # ── Paid (BYOK OpenRouter credits) ────────────────────────────────
    ModelChoice(
        id="openai/gpt-4o-mini",
        label="openai/gpt-4o-mini",
        description="~$0.00015 / 1k input tok, very fast",
        group="Paid",
    ),
    ModelChoice(
        id="openai/gpt-4o",
        label="openai/gpt-4o",
        description="~$0.0025 / 1k input tok",
        group="Paid",
    ),
    ModelChoice(
        id="anthropic/claude-3-5-sonnet",
        label="anthropic/claude-3-5-sonnet",
        description="~$0.003 / 1k input tok, strong reasoning",
        group="Paid",
    ),
    ModelChoice(
        id="anthropic/claude-opus-4",
        label="anthropic/claude-opus-4",
        description="~$0.015 / 1k input tok, top tier",
        group="Paid",
    ),
)


CUSTOM_VALUE = "__custom__"
"""Sentinel value the picker returns when the user picks ``Enter model ID…``.

Callers should detect this value and prompt for a free-form string.
"""
