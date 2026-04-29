"""FastAPI-oriented dependency providers for the Slack bridge."""

from __future__ import annotations

import slack_client_impl  # noqa: F401  # Register Slack ChatClient with chat_client_api
from chat_client_api import ChatClient, get_client


def get_chat_client() -> ChatClient:
    """Return the configured :class:`ChatClient` for this process.

    Importing :mod:`slack_client_impl` registers a factory with
    :mod:`chat_client_api`. Callers that inject this dependency depend only on
    :class:`ChatClient`, not on Slack.
    """
    return get_client()
