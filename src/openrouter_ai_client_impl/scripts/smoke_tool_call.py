"""Smoke-test whether the configured model actually emits tool calls.

Run with::

    uv run --package openrouter-ai-client-impl python \
        src/openrouter_ai_client_impl/scripts/smoke_tool_call.py

Environment: loads ``credentials.env`` from the repo root. Uses ``dry_run=True``
so no S3 object is actually created — we only want to know whether the LLM
emits a ``tool_calls`` block given a natural-language upload request.

Exit codes:
    0  model emitted at least one tool_call to ``upload_file``
    1  model did not emit any tool calls (the bug we're hunting)
    2  missing config / connection error
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv
from openrouter_ai_client_impl.cli import _load_dotenv_best_effort
from openrouter_ai_client_impl.cloud_storage_tools import build_cloud_storage_tools
from openrouter_ai_client_impl.config import OpenRouterConfig
from openrouter_ai_client_impl.openrouter_client import OpenRouterClient

from ai_client_api import AIClientError


class _NoopStorage:
    """Dry-run-only storage: the tools never invoke it because dry_run=True."""

    def upload_file(self, *_args: object, **_kwargs: object) -> object:
        msg = "should not be called under dry_run"
        raise AssertionError(msg)

    def __getattr__(self, name: str) -> object:  # pragma: no cover
        def _fail(*_args: object, **_kwargs: object) -> object:
            msg = f"{name} should not be called under dry_run"
            raise AssertionError(msg)

        return _fail


def _main() -> int:
    load_dotenv()  # current-dir .env if present
    _load_dotenv_best_effort()  # walk up for credentials.env

    try:
        config = OpenRouterConfig.from_env()
    except AIClientError as err:
        sys.stderr.write(f"config error: {err}\n")
        return 2

    client = OpenRouterClient(config)
    tools = build_cloud_storage_tools(
        storage=_NoopStorage(),  # type: ignore[arg-type]
        container="smoke-test-bucket",
        safe_root=Path.cwd(),
    )

    prompt = "Please upload hello.txt to the bucket."
    sys.stdout.write(f"model: {config.model}\n")
    sys.stdout.write(f"prompt: {prompt}\n\n")

    # Wire up a listener so we can see the events as they happen.
    def _listen(event: object) -> None:
        kind = getattr(event, "kind", "?")
        payload = getattr(event, "payload", {})
        sys.stdout.write(f"  [{kind}] {payload}\n")

    client.on_event(_listen)

    try:
        response = client.send_message(prompt, tools=tools, max_steps=2, dry_run=True)
    except AIClientError as err:
        sys.stderr.write(f"provider error: {err}\n")
        return 2

    sys.stdout.write(
        f"\nsteps={response.steps} "
        f"tokens={response.tokens.total} model={response.model}\n"
    )
    sys.stdout.write(f"stop_reason={response.stop_reason}\n")
    if response.tool_calls:
        sys.stdout.write(f"tool_calls: {len(response.tool_calls)}\n")
        for rec in response.tool_calls:
            sys.stdout.write(f"  {rec.name}({rec.arguments}) success={rec.success}\n")
        any_upload = any(rec.name == "upload_file" for rec in response.tool_calls)
        if any_upload:
            sys.stdout.write("\nPASS: model called upload_file\n")
            return 0
        sys.stdout.write(
            "\nFAIL: model called tools but none of them was upload_file\n"
        )
        return 1
    sys.stdout.write(f"\nFAIL: no tool calls. final text was:\n{response.text}\n")
    # Show the raw capture so we can see what the model actually emitted.
    sys.stdout.write("\nraw completions captured:\n")
    for entry in client.last_raw_completions():
        sys.stdout.write(f"  {entry}\n")
    return 1


if __name__ == "__main__":
    sys.exit(_main())
