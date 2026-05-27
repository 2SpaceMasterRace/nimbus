"""Tests for the shared Nimbus capability registry."""

from __future__ import annotations

import pytest
from nimbus_runtime.capabilities import (
    CapabilityStatus,
    CapabilitySurface,
    all_capabilities,
    capability_for_ai_tool,
    capability_names,
    get_capability,
)

pytestmark = pytest.mark.unit


def test_capability_names_are_unique() -> None:
    """The registry should expose stable unique capability names."""
    names = capability_names()
    assert len(names) == len(set(names))


def test_current_runtime_tools_are_registered() -> None:
    """Model-facing storage tools should map back to product capabilities."""
    assert capability_for_ai_tool("list_files") is get_capability("list_files")
    assert capability_for_ai_tool("read_file") is get_capability("read_file")
    assert capability_for_ai_tool("delete_file") is get_capability("delete_file")


def test_roadmap_capabilities_include_features_from_features_doc() -> None:
    """The first tool-system slice should name the next unfinished roadmap work."""
    names = {
        capability.name
        for capability in all_capabilities(status=CapabilityStatus.ROADMAP)
    }
    assert "automation_templates" in names
    assert "candidate_plans" in names
    assert "parallel_candidate_agents" in names
    assert "ask_user_choice" in names


def test_surface_filter_returns_slack_visible_tools() -> None:
    """Slack and CLI adapters should read from the same registry."""
    names = {
        capability.name
        for capability in all_capabilities(surface=CapabilitySurface.SLACK)
    }
    assert "channel_backup" in names
    assert "candidate_plans" in names
    assert "task_event_monitor" in names


def test_current_only_omits_roadmap() -> None:
    """Adapters can hide not-yet-built capabilities when needed."""
    capabilities = all_capabilities(include_roadmap=False)
    assert capabilities
    assert all(
        capability.status is not CapabilityStatus.ROADMAP for capability in capabilities
    )
