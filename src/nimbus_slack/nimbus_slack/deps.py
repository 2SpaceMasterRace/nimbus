"""FastAPI-oriented dependency providers for the Slack bridge."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

import slack_client_impl  # noqa: F401  # Register Slack ChatClient with chat_client_api
from chat_client_api import ChatClient, get_client
from slack_sdk import WebClient

from nimbus_slack.crypto import SecretCodec, SecretCodecError
from nimbus_slack.file_sync import (
    DEFAULT_FILE_SCAN_MAX_PAGES,
    DEFAULT_FILE_SCAN_PAGE_SIZE,
    DEFAULT_MAX_FILE_BYTES,
    S3TenantObjectSink,
    SlackFileSyncService,
    SlackWebFileSource,
)
from nimbus_slack.store import SlackStore, SlackStoreError, default_store_path

NIMBUS_SLACK_FILE_SCAN_PAGE_SIZE = "NIMBUS_SLACK_FILE_SCAN_PAGE_SIZE"
NIMBUS_SLACK_FILE_SCAN_MAX_PAGES = "NIMBUS_SLACK_FILE_SCAN_MAX_PAGES"
NIMBUS_SLACK_MAX_FILE_BYTES = "NIMBUS_SLACK_MAX_FILE_BYTES"


def get_chat_client() -> ChatClient:
    """Return the configured :class:`ChatClient` for this process.

    Importing :mod:`slack_client_impl` registers a factory with
    :mod:`chat_client_api`. Callers that inject this dependency depend only on
    :class:`ChatClient`, not on Slack.
    """
    return get_client()


class SlackPoster(Protocol):
    """Minimal Slack posting capability Nimbus needs."""

    def send_message(
        self,
        channel_id: str,
        text: str,
        *,
        thread_ts: str | None = None,
    ) -> object:
        """Post a message, optionally as a thread reply."""


@dataclass(frozen=True, slots=True)
class SlackSdkPoster:
    """Slack SDK-backed poster that supports threaded replies."""

    client: WebClient

    def send_message(
        self,
        channel_id: str,
        text: str,
        *,
        thread_ts: str | None = None,
    ) -> object:
        """Post a Slack message using ``chat.postMessage``."""
        return self.client.chat_postMessage(
            channel=channel_id,
            text=text,
            thread_ts=thread_ts,
        )


def get_slack_store() -> SlackStore:
    """Return the configured Slack control-plane store."""
    return SlackStore(db_path=default_store_path(), codec=SecretCodec.from_env())


def get_slack_poster(
    *,
    team_id: str | None = None,
    store: SlackStore | None = None,
) -> SlackPoster:
    """Return the Slack poster for a workspace or local fallback token."""
    token = _workspace_token(team_id=team_id, store=store)
    if not token:
        token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    if not token:
        msg = "Slack bot token is not configured for this workspace."
        raise ValueError(msg)
    return SlackSdkPoster(WebClient(token=token))


def get_file_sync_service(
    *,
    team_id: str,
    store: SlackStore | None = None,
) -> SlackFileSyncService:
    """Return the file sync service for a Slack workspace."""
    resolved_store = store or get_slack_store()
    installation = resolved_store.get_installation(team_id)
    if installation is None:
        msg = "Nimbus Slack is not installed for this workspace."
        raise ValueError(msg)
    return SlackFileSyncService(
        store=resolved_store,
        source=SlackWebFileSource(
            client=WebClient(token=installation.bot_token),
            bot_token=installation.bot_token,
        ),
        sink=S3TenantObjectSink(),
        page_size=_positive_int_env(
            NIMBUS_SLACK_FILE_SCAN_PAGE_SIZE,
            default=DEFAULT_FILE_SCAN_PAGE_SIZE,
        ),
        max_pages=_positive_int_env(
            NIMBUS_SLACK_FILE_SCAN_MAX_PAGES,
            default=DEFAULT_FILE_SCAN_MAX_PAGES,
        ),
        max_file_bytes=_positive_int_env(
            NIMBUS_SLACK_MAX_FILE_BYTES,
            default=DEFAULT_MAX_FILE_BYTES,
        ),
    )


def _workspace_token(
    *,
    team_id: str | None,
    store: SlackStore | None,
) -> str | None:
    """Resolve the encrypted per-workspace bot token when available."""
    if team_id is None:
        return None
    try:
        resolved_store = store or get_slack_store()
    except (SecretCodecError, SlackStoreError):
        return None
    installation = resolved_store.get_installation(team_id)
    if installation is None:
        return None
    return installation.bot_token


def _positive_int_env(name: str, *, default: int) -> int:
    """Return a positive integer environment override."""
    raw_value = os.environ.get(name, "").strip()
    if not raw_value:
        return default
    try:
        parsed = int(raw_value)
    except ValueError as exc:
        msg = f"{name} must be a positive integer."
        raise ValueError(msg) from exc
    if parsed <= 0:
        msg = f"{name} must be a positive integer."
        raise ValueError(msg)
    return parsed
