"""Transport-neutral runtime request and response shapes.

The canonical definitions live in :mod:`nimbus_protocol`. This module keeps the
existing import path stable while the runtime, server, CLI, and chat adapters
move to the shared protocol package.
"""

from __future__ import annotations

from nimbus_protocol import (
    ActionSummary,
    ArtifactSummary,
    ChatTurnInput,
    ChatTurnResult,
    ConfirmationDetails,
    TurnAttachment,
    TurnOutcome,
)

__all__ = [
    "ActionSummary",
    "ArtifactSummary",
    "ChatTurnInput",
    "ChatTurnResult",
    "ConfirmationDetails",
    "TurnAttachment",
    "TurnOutcome",
]
