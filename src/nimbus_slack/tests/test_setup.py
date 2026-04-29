"""Tests for Slack BYOK setup validation."""

from __future__ import annotations

import pytest
from nimbus_slack.setup import TenantSetupError, TenantSetupInput

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
