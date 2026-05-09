"""Tests for Slack BYOK setup validation."""

from __future__ import annotations

from uuid import uuid4

import pytest
from nimbus_slack.setup import (
    TenantSetupError,
    TenantSetupInput,
    render_install_success,
    render_setup_error,
    render_setup_form,
    render_setup_success,
)

pytestmark = pytest.mark.unit


def test_tenant_setup_accepts_valid_payload() -> None:
    """Valid setup data should be normalized for persistence."""
    setup_input = TenantSetupInput.from_mapping(
        {
            "openrouter_api_key": "sk-or-secret",
            "aws_access_key_id": "AKIA_TEST_SECRET",
            "aws_secret_access_key": "aws-secret",
            "aws_region": "us-east-1",
            "s3_bucket": "nimbus-test-bucket",
            "s3_prefix": "/team-one/slack/",
        }
    )

    assert setup_input.s3_prefix == "team-one/slack"
    assert setup_input.s3_bucket == "nimbus-test-bucket"


def test_tenant_setup_rejects_malformed_bucket() -> None:
    """Malformed bucket names should fail before entering storage state."""
    with pytest.raises(TenantSetupError):
        TenantSetupInput.from_mapping(
            {
                "openrouter_api_key": "sk-or-secret",
                "aws_access_key_id": "AKIA_TEST_SECRET",
                "aws_secret_access_key": "aws-secret",
                "aws_region": "us-east-1",
                "s3_bucket": "Bad_Bucket",
                "s3_prefix": "",
            }
        )


def test_tenant_setup_rejects_control_characters_in_secrets() -> None:
    """Slack setup should not persist multiline pasted secrets."""
    with pytest.raises(TenantSetupError):
        TenantSetupInput.from_mapping(
            {
                "openrouter_api_key": "sk-or-secret\nnext",
                "aws_access_key_id": "AKIA_TEST_SECRET",
                "aws_secret_access_key": "aws-secret",
                "aws_region": "us-east-1",
                "s3_bucket": "nimbus-test-bucket",
                "s3_prefix": "",
            }
        )


def test_render_setup_form_contains_product_shell() -> None:
    """Setup form should render the polished product shell."""
    setup_session = uuid4().hex
    html = render_setup_form(team_id="T123", token=setup_session)

    assert "Connect Nimbus to your tools" in html
    assert 'class="panel"' in html
    assert "OpenRouter API key" in html
    assert "Secrets are submitted over HTTPS" in html
    assert 'autocomplete="on"' in html
    assert 'autocomplete="section-openrouter current-password"' in html
    assert 'autocomplete="section-aws username"' in html
    assert 'data-1p-label="AWS secret access key"' in html


def test_render_setup_form_shows_inline_error_without_echoing_secrets() -> None:
    """Validation failures should return a useful page, not browser JSON."""
    html = render_setup_form(
        team_id="T123",
        token="setup-token",  # noqa: S106
        error="openrouter_api_key must be a non-empty string.",
        values={
            "openrouter_api_key": "sk-secret",
            "aws_secret_access_key": "aws-secret",
            "aws_region": "us-west-2",
            "s3_bucket": "nimbus-prod",
            "s3_prefix": "team",
        },
    )

    assert "Configuration was not saved" in html
    assert "openrouter_api_key must be a non-empty string." in html
    assert 'value="us-west-2"' in html
    assert 'value="nimbus-prod"' in html
    assert 'value="team"' in html
    assert "sk-secret" not in html
    assert "aws-secret" not in html


def test_render_setup_error_is_human_readable() -> None:
    html = render_setup_error(title="Setup link unavailable", message="Expired.")

    assert "Setup link unavailable" in html
    assert "Expired." in html
    assert "application/json" not in html


def test_render_install_success_links_to_setup() -> None:
    """OAuth completion should render the same product shell as setup."""
    html = render_install_success(team_id="T123", setup_path="/slack/setup/t")

    assert "Finish connecting Nimbus" in html
    assert 'class="button-link"' in html
    assert 'href="/slack/setup/t"' in html


def test_render_setup_success_suggests_first_slack_prompt() -> None:
    """Completion page should give users a concrete Slack prompt to try."""
    html = render_setup_success(team_id="T123")

    assert "Nimbus is ready in Slack" in html
    assert "@Nimbus what files in this channel" in html


def test_render_setup_success_links_back_to_slack_workspace() -> None:
    """Completion page should offer a Slack deep link back to the workspace."""
    html = render_setup_success(team_id="T123")

    assert "Return to Slack" in html
    assert "https://app.slack.com/client/T123" in html


def test_render_setup_success_includes_invite_step_and_prompt_ideas() -> None:
    """Onboarding tips should walk users from invite to a concrete prompt."""
    html = render_setup_success(team_id="T123")

    assert "/invite @Nimbus" in html
    assert "More ideas" in html
    assert "Save all files in this channel to S3." in html


# ── P6: Onboarding page overhaul ────────────────────────────────────────────


def test_render_setup_success_has_four_numbered_steps() -> None:
    """Post-setup page should guide the user through four onboarding steps."""
    html = render_setup_success(team_id="T123")

    # All four step-num badges should appear in the page.
    assert html.count('class="step-num"') >= 4


def test_render_setup_success_includes_app_home_tab_guidance() -> None:
    """Onboarding page should mention the App Home tab (added in P2)."""
    html = render_setup_success(team_id="T123")

    assert "Home" in html and ("tab" in html or "Tab" in html)


def test_render_setup_success_includes_cli_installation_hint() -> None:
    """Onboarding page should show a pip install command for the CLI."""
    html = render_setup_success(team_id="T123")

    assert "pip install nimbus-cli" in html


def test_render_setup_success_capabilities_section_present() -> None:
    """Onboarding page should have a 'What Nimbus does' section."""
    html = render_setup_success(team_id="T123")

    assert "What Nimbus does" in html


def test_render_setup_success_prompt_gallery_has_workspace_health_prompts() -> None:
    """Expanded prompt gallery should include workspace-health related prompts."""
    html = render_setup_success(team_id="T123")

    # One of the new health/status prompts should appear in the More ideas list.
    assert (
        "workspace health" in html.lower()
        or "pending approvals" in html.lower()
        or "tasks are currently running" in html.lower()
    )


def test_render_setup_success_prompt_gallery_covers_all_categories() -> None:
    """All prompt categories should be represented in the page HTML."""
    html = render_setup_success(team_id="T123")

    # File backup prompts
    assert "missing from S3" in html
    # Discovery prompts
    assert "PDF" in html or "duplicate" in html
    # CLI hint
    assert "nimbus-cli" in html


def test_render_setup_success_escapes_team_id() -> None:
    """Workspace ID with special characters must be safely HTML-escaped."""
    html = render_setup_success(team_id="T<script>")

    assert "<script>" not in html
    assert "T&lt;script&gt;" in html or "T" in html


def test_tenant_setup_input_rejects_invalid_region() -> None:
    """An AWS region that does not match the pattern should fail validation."""
    with pytest.raises(TenantSetupError, match="aws_region"):
        TenantSetupInput.from_mapping(
            {
                "openrouter_api_key": "sk-or",
                "aws_access_key_id": "AKIA",
                "aws_secret_access_key": "secret",
                "aws_region": "Us-East-1",
                "s3_bucket": "nimbus-test-bucket",
                "s3_prefix": "",
            }
        )


def test_tenant_setup_input_rejects_invalid_bucket_name() -> None:
    """An S3 bucket name with disallowed characters should fail validation."""
    with pytest.raises(TenantSetupError, match="s3_bucket"):
        TenantSetupInput.from_mapping(
            {
                "openrouter_api_key": "sk-or",
                "aws_access_key_id": "AKIA",
                "aws_secret_access_key": "secret",
                "aws_region": "us-east-1",
                "s3_bucket": "Bad..Name",
                "s3_prefix": "",
            }
        )


def test_tenant_setup_input_rejects_missing_required_field() -> None:
    """An empty required field should fail validation."""
    with pytest.raises(TenantSetupError, match="non-empty string"):
        TenantSetupInput.from_mapping(
            {
                "openrouter_api_key": "",
                "aws_access_key_id": "AKIA",
                "aws_secret_access_key": "secret",
                "aws_region": "us-east-1",
                "s3_bucket": "nimbus-test-bucket",
                "s3_prefix": "",
            }
        )


def test_tenant_setup_input_rejects_non_string_optional_prefix() -> None:
    """A non-string optional field should fail validation explicitly."""
    with pytest.raises(TenantSetupError, match="must be a string"):
        TenantSetupInput.from_mapping(
            {
                "openrouter_api_key": "sk-or",
                "aws_access_key_id": "AKIA",
                "aws_secret_access_key": "secret",
                "aws_region": "us-east-1",
                "s3_bucket": "nimbus-test-bucket",
                "s3_prefix": 42,
            }
        )


def test_render_setup_form_supports_credential_reveal_toggles() -> None:
    """Credential inputs should expose accessible show/hide controls."""
    html = render_setup_form(team_id="T123", token="t")  # noqa: S106

    assert 'data-reveal-target="openrouter_api_key"' in html
    assert 'data-reveal-target="aws_access_key_id"' in html
    assert 'data-reveal-target="aws_secret_access_key"' in html
    assert 'aria-pressed="false"' in html
