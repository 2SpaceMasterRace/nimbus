"""Per-request profile timing trace for the Nimbus Slack adapter.

Mirrors the Nimbus CLI's ``--profile-timing`` flag: when a Slack user includes
the flag in their ``@Nimbus`` message, the adapter records timing spans for
each major step of the turn and posts a follow-up Block Kit card after the
main reply showing where time was spent.

The opt-in is text-based because Slack does not provide CLI-style flags. The
token ``--profile-timing`` (case-insensitive, whole tokens only) is stripped
from the message text before command parsing and the model call, so the rest
of the adapter sees the user's original intent unchanged.

When tracing is disabled, ``ProfileTrace.span`` is a zero-cost no-op so the
hot path pays nothing for unprofiled turns.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, cast

from nimbus_slack import design

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

ProfileMode = Literal["half", "full", "hud", "waterfall"]

PROFILE_TIMING_FLAG = "--profile-timing"
"""Slack-side opt-in token. Whole-word match, case-insensitive."""
PROFILE_TIMINGS_FLAG = "--profile-timings"
"""Slack-side opt-in token for an explicit profiler rendering mode."""


@dataclass(frozen=True, slots=True)
class ProfileSpan:
    """One measured Slack-side operation.

    ``detail`` carries small structured metadata (kind, model, outcome, etc.)
    that is rendered alongside the timing in the follow-up card.
    """

    name: str
    start_ns: int
    end_ns: int
    detail: Mapping[str, object]

    @property
    def duration_ms(self) -> float:
        """Return the span's wall-clock duration in milliseconds."""
        return (self.end_ns - self.start_ns) / 1_000_000


@dataclass(slots=True)
class ProfileTrace:
    """Per-request timing trace.

    Construct one trace per Slack event. When ``enabled`` is ``False``, every
    ``span()`` call is an empty context manager — no allocations, no time
    sampling, no list growth — so unprofiled turns pay nothing.

    The trace is intentionally not thread-safe: one Slack event is handled by
    one synchronous handler call, so spans are appended sequentially.
    """

    enabled: bool
    mode: ProfileMode = "half"
    started_ns: int = 0
    ended_ns: int = 0
    spans: list[ProfileSpan] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Seed start/end timestamps so ``total_ms`` is defined on an empty trace."""
        now = time.perf_counter_ns()
        self.started_ns = now
        self.ended_ns = now

    @contextmanager
    def span(self, name: str, **detail: object) -> Iterator[None]:
        """Record one span's duration; safe to use even when disabled.

        The span is recorded even if the wrapped block raises, so a partial
        timing breakdown is still visible when an exception cuts the turn
        short. The exception propagates unchanged.
        """
        if not self.enabled:
            yield
            return
        start_ns = time.perf_counter_ns()
        try:
            yield
        finally:
            end_ns = time.perf_counter_ns()
            self.spans.append(
                ProfileSpan(
                    name=name,
                    start_ns=start_ns,
                    end_ns=end_ns,
                    detail=dict(detail),
                )
            )
            self.ended_ns = end_ns

    @property
    def total_ms(self) -> float:
        """Return the total elapsed time across all recorded spans."""
        end_ns = self.ended_ns or time.perf_counter_ns()
        return (end_ns - self.started_ns) / 1_000_000


def extract_profile_timing(text: str) -> tuple[str, bool]:
    """Strip ``--profile-timing`` tokens from ``text``.

    Matches whole tokens only (case-insensitive) so substrings like
    ``foo--profile-timingbar`` are left untouched. Multiple occurrences are
    all stripped and the flag is reported as enabled.

    Args:
        text: The user-authored Slack message body, with the leading
            ``@Nimbus`` mention already stripped.

    Returns:
        ``(cleaned_text, enabled)`` — ``cleaned_text`` is the original text
        with every ``--profile-timing`` token removed and remaining whitespace
        collapsed; ``enabled`` is ``True`` when at least one flag token was
        present.

    """
    cleaned, mode = extract_profile_timing_mode(text)
    return cleaned, mode is not None


def extract_profile_timing_mode(text: str) -> tuple[str, ProfileMode | None]:
    """Strip profile timing flags and return the requested render mode."""
    found_mode: ProfileMode | None = None
    cleaned_parts: list[str] = []
    tokens = text.split()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        lowered = token.lower()
        if lowered == PROFILE_TIMING_FLAG:
            found_mode = "half"
            index += 1
            continue
        if lowered.startswith(f"{PROFILE_TIMING_FLAG}="):
            found_mode = _profile_mode_or_half(lowered.split("=", 1)[1])
            index += 1
            continue
        if lowered.startswith(f"{PROFILE_TIMINGS_FLAG}="):
            found_mode = _profile_mode_or_half(lowered.split("=", 1)[1])
            index += 1
            continue
        if lowered == PROFILE_TIMINGS_FLAG:
            found_mode = _profile_mode_or_half(
                tokens[index + 1] if index + 1 < len(tokens) else ""
            )
            index += 2 if index + 1 < len(tokens) else 1
            continue
        cleaned_parts.append(token)
        index += 1
    return " ".join(cleaned_parts), found_mode


def profile_trace_card(trace: ProfileTrace) -> list[dict[str, Any]]:
    """Render a Block Kit card showing the per-step timing breakdown.

    Layout mirrors the CLI's Rich table: a header with total elapsed time, a
    monospace code block with one row per span, and a footer naming the flag
    that produced the card.
    """
    header_block = design.branded_header(
        f"Profile timing {trace.mode.upper()}  •  {trace.total_ms:.1f} ms total",
        status="info",
    )
    footer_block = design.context(
        f"Triggered by `{PROFILE_TIMING_FLAG}` or `{PROFILE_TIMINGS_FLAG}=MODE`. "
        "Omit the flag to suppress this card."
    )
    if not trace.spans:
        return [
            header_block,
            design.section("_No spans were recorded for this request._"),
            footer_block,
        ]
    if trace.mode == "hud":
        body = _profile_hud(trace)
    elif trace.mode == "waterfall":
        body = _profile_waterfall(trace)
    elif trace.mode == "full":
        body = _profile_full(trace)
    else:
        body = _profile_half(trace)
    return [
        header_block,
        design.section(body),
        footer_block,
    ]


def _profile_half(trace: ProfileTrace) -> str:
    rows: list[str] = ["*critical path*"]
    for span in trace.spans:
        detail_str = "  ".join(f"{k}={v}" for k, v in span.detail.items())
        detail_suffix = f" ({detail_str})" if detail_str else ""
        rows.append(
            f"• `{span.name}` {span.duration_ms:.1f} ms measured{detail_suffix}"
        )
    return "\n".join(rows)


def _profile_full(trace: ProfileTrace) -> str:
    rows = ["```span                                      ms   kind      detail"]
    for span in trace.spans:
        detail_str = " ".join(f"{k}={v}" for k, v in span.detail.items())
        rows.append(
            f"{span.name[:38]:<38} {span.duration_ms:>6.1f} "
            f"{_span_kind(span):<9} {detail_str[:60]}"
        )
    rows.append("```")
    rows.append("_opaque means provider/network internals are not visible to Python._")
    return "\n".join(rows)


def _profile_hud(trace: ProfileTrace) -> str:
    total = max(trace.total_ms, 1.0)
    parts = [
        f"`{span.name}` {_bar(span.duration_ms / total)} {span.duration_ms:.0f} ms"
        for span in trace.spans[:6]
    ]
    bottleneck = max(trace.spans, key=lambda span: span.duration_ms)
    parts.append(
        f"*bottleneck:* `{bottleneck.name}` at {bottleneck.duration_ms:.1f} ms"
    )
    return "\n".join(parts)


def _profile_waterfall(trace: ProfileTrace) -> str:
    rows = ["```offset   duration  span"]
    for span in trace.spans:
        offset_ms = (span.start_ns - trace.started_ns) / 1_000_000
        rows.append(f"{offset_ms:>6.1f}ms {span.duration_ms:>7.1f}ms  {span.name}")
    rows.append("```")
    return "\n".join(rows)


def _span_kind(span: ProfileSpan) -> str:
    if "remote" in span.name or "model" in span.name or "post_result" in span.name:
        return "opaque"
    return "measured"


def _bar(ratio: float) -> str:
    width = 12
    filled = max(1, min(width, round(ratio * width)))
    return "[" + ("#" * filled) + ("." * (width - filled)) + "]"


def _profile_mode_or_half(value: str) -> ProfileMode:
    normalized = value.strip().lower()
    if normalized in {"full", "hud", "waterfall", "half"}:
        return cast("ProfileMode", normalized)
    return "half"
