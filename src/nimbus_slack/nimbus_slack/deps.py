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
from nimbus_slack.store import (
    POSTGRES_BACKEND,
    PostgresSlackStore,
    SlackStore,
    SlackStoreBackend,
    SlackStoreError,
    default_store_path,
    slack_database_url_from_env,
    slack_store_backend_from_env,
)

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

    def update_message(
        self,
        channel_id: str,
        ts: str,
        text: str,
    ) -> object:
        """Edit the body of a previously-posted message."""

    def send_blocks(
        self,
        channel_id: str,
        blocks: list[dict[str, object]],
        fallback_text: str,
        *,
        thread_ts: str | None = None,
    ) -> object:
        """Post a Block Kit message, optionally as a thread reply."""

    def update_blocks(
        self,
        channel_id: str,
        ts: str,
        blocks: list[dict[str, object]],
        fallback_text: str,
    ) -> object:
        """Edit a previously-posted message with new Block Kit blocks."""


class SlackHomePublisher(Protocol):
    """Minimal capability for publishing Slack App Home tab views."""

    def publish_home_tab(
        self,
        user_id: str,
        blocks: list[dict[str, object]],
    ) -> object:
        """Push a new Home-tab view to the given user."""


@dataclass(frozen=True, slots=True)
class SlackSdkHomePublisher:
    """Slack SDK-backed publisher for the App Home tab surface."""

    client: WebClient

    def publish_home_tab(
        self,
        user_id: str,
        blocks: list[dict[str, object]],
    ) -> object:
        """Publish a home-type view for ``user_id`` via ``views.publish``."""
        return self.client.views_publish(
            user_id=user_id,
            view={"type": "home", "blocks": blocks},
        )


@dataclass(frozen=True, slots=True)
class SlackSdkPoster:
    """Slack SDK-backed poster that supports threaded replies and edits."""

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

    def update_message(
        self,
        channel_id: str,
        ts: str,
        text: str,
    ) -> object:
        """Edit a previously-posted message via ``chat.update``."""
        return self.client.chat_update(channel=channel_id, ts=ts, text=text)

    def send_blocks(
        self,
        channel_id: str,
        blocks: list[dict[str, object]],
        fallback_text: str,
        *,
        thread_ts: str | None = None,
    ) -> object:
        """Post a Block Kit message using ``chat.postMessage``."""
        return self.client.chat_postMessage(
            channel=channel_id,
            text=fallback_text,
            blocks=blocks,
            thread_ts=thread_ts,
        )

    def update_blocks(
        self,
        channel_id: str,
        ts: str,
        blocks: list[dict[str, object]],
        fallback_text: str,
    ) -> object:
        """Edit a previously-posted message with new Block Kit blocks."""
        return self.client.chat_update(
            channel=channel_id,
            ts=ts,
            text=fallback_text,
            blocks=blocks,
        )


def get_slack_store() -> SlackStoreBackend:
    """Return the configured Slack control-plane store."""
    codec = SecretCodec.from_env()
    if slack_store_backend_from_env() == POSTGRES_BACKEND:
        return PostgresSlackStore(
            database_url=slack_database_url_from_env(),
            codec=codec,
        )
    return SlackStore(db_path=default_store_path(), codec=codec)


def check_slack_store_ready() -> None:
    """Verify the configured Slack control-plane store is ready."""
    store = get_slack_store()
    if isinstance(store, PostgresSlackStore):
        store.check_ready()


def get_slack_poster(
    *,
    team_id: str | None = None,
    store: SlackStoreBackend | None = None,
) -> SlackPoster:
    """Return the Slack poster for a workspace or local fallback token."""
    token = _workspace_token(team_id=team_id, store=store)
    if not token:
        token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    if not token:
        msg = "Slack bot token is not configured for this workspace."
        raise ValueError(msg)
    return SlackSdkPoster(WebClient(token=token))


def get_slack_home_publisher(
    *,
    team_id: str | None = None,
    store: SlackStoreBackend | None = None,
) -> SlackHomePublisher:
    """Return a home-tab publisher for a workspace or local fallback token."""
    token = _workspace_token(team_id=team_id, store=store)
    if not token:
        token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    if not token:
        msg = "Slack bot token is not configured for this workspace."
        raise ValueError(msg)
    return SlackSdkHomePublisher(WebClient(token=token))


def get_file_sync_service(
    *,
    team_id: str,
    store: SlackStoreBackend | None = None,
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
    store: SlackStoreBackend | None,
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
