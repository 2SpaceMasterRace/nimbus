"""Runtime-owned Nimbus capability registry.

The registry is the product-facing tool catalog shared by Slack, CLI, and
model-facing tool bindings. It intentionally describes both live and roadmap
capabilities so adapters can explain what Nimbus can do without inventing
their own tool lists.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from nimbus_runtime.domain import OperationMode, PlanRiskLevel


class CapabilityStatus(StrEnum):
    """Implementation state for a Nimbus capability."""

    CURRENT = "current"
    PARTIAL = "partial"
    ROADMAP = "roadmap"


class CapabilitySurface(StrEnum):
    """Client or runtime surface where a capability is visible."""

    RUNTIME = "runtime"
    MODEL_TOOL = "model_tool"
    CLI = "cli"
    SLACK = "slack"
    WORKER = "worker"


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    """Stable public description of one Nimbus tool/capability."""

    name: str
    title: str
    description: str
    status: CapabilityStatus
    modes: tuple[OperationMode, ...]
    risk: PlanRiskLevel
    surfaces: tuple[CapabilitySurface, ...]
    claude_analogues: tuple[str, ...] = ()
    ai_tool_name: str | None = None
    roadmap_feature: str | None = None
    requires_approval: bool = False

    @property
    def is_live(self) -> bool:
        """Return whether the capability is available beyond documentation."""
        return self.status is not CapabilityStatus.ROADMAP


_CAPABILITIES: tuple[CapabilitySpec, ...] = (
    CapabilitySpec(
        name="list_files",
        title="List stored objects",
        description=(
            "List objects by prefix with bounded pagination and compact object "
            "metadata."
        ),
        status=CapabilityStatus.CURRENT,
        modes=(OperationMode.READ_ONLY, OperationMode.PLAN, OperationMode.REVIEW),
        risk=PlanRiskLevel.READ_ONLY,
        surfaces=(CapabilitySurface.RUNTIME, CapabilitySurface.MODEL_TOOL),
        claude_analogues=("Glob",),
        ai_tool_name="list_files",
    ),
    CapabilitySpec(
        name="get_file_info",
        title="Inspect object metadata",
        description=(
            "Fetch size, last-modified, version, and provider metadata for one "
            "stored object."
        ),
        status=CapabilityStatus.CURRENT,
        modes=(OperationMode.READ_ONLY, OperationMode.PLAN, OperationMode.REVIEW),
        risk=PlanRiskLevel.READ_ONLY,
        surfaces=(CapabilitySurface.RUNTIME, CapabilitySurface.MODEL_TOOL),
        claude_analogues=("Read",),
        ai_tool_name="get_file_info",
    ),
    CapabilitySpec(
        name="read_file",
        title="Read bounded object contents",
        description=(
            "Read a capped byte range from an object, returning UTF-8 text or "
            "base64 when bytes are not text-safe."
        ),
        status=CapabilityStatus.CURRENT,
        modes=(OperationMode.READ_ONLY, OperationMode.PLAN, OperationMode.REVIEW),
        risk=PlanRiskLevel.READ_ONLY,
        surfaces=(CapabilitySurface.RUNTIME, CapabilitySurface.MODEL_TOOL),
        claude_analogues=("Read",),
        ai_tool_name="read_file",
    ),
    CapabilitySpec(
        name="search_index",
        title="Search indexed documents",
        description=(
            "Search the rebuildable document index with tenant and ACL filtering "
            "before results are ranked or answered."
        ),
        status=CapabilityStatus.PARTIAL,
        modes=(OperationMode.READ_ONLY, OperationMode.PLAN, OperationMode.REVIEW),
        risk=PlanRiskLevel.READ_ONLY,
        surfaces=(
            CapabilitySurface.RUNTIME,
            CapabilitySurface.CLI,
            CapabilitySurface.SLACK,
        ),
        claude_analogues=("Grep",),
    ),
    CapabilitySpec(
        name="write_file",
        title="Create or overwrite an object",
        description=(
            "Create a new object or overwrite an existing one through the "
            "runtime mutation path."
        ),
        status=CapabilityStatus.PARTIAL,
        modes=(OperationMode.PLAN, OperationMode.APPLY, OperationMode.POLICY_ADMIN),
        risk=PlanRiskLevel.LARGE_WRITE,
        surfaces=(CapabilitySurface.RUNTIME, CapabilitySurface.MODEL_TOOL),
        claude_analogues=("Write",),
        ai_tool_name="write_file",
        requires_approval=True,
    ),
    CapabilitySpec(
        name="copy_file",
        title="Copy an object",
        description=(
            "Copy one stored object to another key while refusing accidental "
            "overwrites unless policy allows them."
        ),
        status=CapabilityStatus.PARTIAL,
        modes=(OperationMode.PLAN, OperationMode.APPLY, OperationMode.POLICY_ADMIN),
        risk=PlanRiskLevel.SMALL_WRITE,
        surfaces=(CapabilitySurface.RUNTIME, CapabilitySurface.MODEL_TOOL),
        claude_analogues=("Write",),
        ai_tool_name="copy_file",
        requires_approval=True,
    ),
    CapabilitySpec(
        name="move_file",
        title="Move or rename an object",
        description=(
            "Copy an object to a new key and delete the source only after an "
            "explicit approval path."
        ),
        status=CapabilityStatus.PARTIAL,
        modes=(OperationMode.PLAN, OperationMode.APPLY, OperationMode.POLICY_ADMIN),
        risk=PlanRiskLevel.DESTRUCTIVE,
        surfaces=(CapabilitySurface.RUNTIME, CapabilitySurface.MODEL_TOOL),
        claude_analogues=("Edit",),
        ai_tool_name="move_file",
        requires_approval=True,
    ),
    CapabilitySpec(
        name="delete_file",
        title="Delete an object",
        description=(
            "Delete one exact object only after a plan, policy decision, "
            "approval, and restore-aware evidence path."
        ),
        status=CapabilityStatus.PARTIAL,
        modes=(OperationMode.PLAN, OperationMode.APPLY, OperationMode.POLICY_ADMIN),
        risk=PlanRiskLevel.DESTRUCTIVE,
        surfaces=(CapabilitySurface.RUNTIME, CapabilitySurface.MODEL_TOOL),
        claude_analogues=("Edit",),
        ai_tool_name="delete_file",
        requires_approval=True,
    ),
    CapabilitySpec(
        name="channel_file_inventory",
        title="Inventory Slack channel files",
        description=(
            "List Slack channel files with sizes and content types before "
            "deciding whether to save, diff, or dedupe them."
        ),
        status=CapabilityStatus.CURRENT,
        modes=(OperationMode.READ_ONLY, OperationMode.PLAN),
        risk=PlanRiskLevel.READ_ONLY,
        surfaces=(CapabilitySurface.SLACK,),
        claude_analogues=("Glob",),
    ),
    CapabilitySpec(
        name="channel_backup",
        title="Back up Slack channel files",
        description=(
            "Save Slack channel files to S3, verify byte-level evidence, and "
            "write manifest artifacts."
        ),
        status=CapabilityStatus.CURRENT,
        modes=(OperationMode.PLAN, OperationMode.APPLY),
        risk=PlanRiskLevel.LARGE_WRITE,
        surfaces=(CapabilitySurface.SLACK, CapabilitySurface.WORKER),
        claude_analogues=("TaskCreate", "Monitor"),
        requires_approval=False,
    ),
    CapabilitySpec(
        name="diff_manifest",
        title="Diff Slack files against S3 manifest",
        description=(
            "Compare visible Slack files with saved manifest entries and report "
            "what is missing or stale."
        ),
        status=CapabilityStatus.CURRENT,
        modes=(OperationMode.READ_ONLY, OperationMode.PLAN),
        risk=PlanRiskLevel.READ_ONLY,
        surfaces=(CapabilitySurface.RUNTIME, CapabilitySurface.SLACK),
        claude_analogues=("Grep", "TaskGet"),
    ),
    CapabilitySpec(
        name="dedupe_manifest",
        title="Find duplicate or stale saved files",
        description=(
            "Detect duplicate content-hash groups and saved S3 entries that are "
            "no longer visible in Slack."
        ),
        status=CapabilityStatus.CURRENT,
        modes=(OperationMode.READ_ONLY, OperationMode.PLAN),
        risk=PlanRiskLevel.READ_ONLY,
        surfaces=(CapabilitySurface.RUNTIME, CapabilitySurface.SLACK),
        claude_analogues=("Grep",),
    ),
    CapabilitySpec(
        name="protected_generations",
        title="Snapshot protected storage roots",
        description=(
            "Protect an S3 bucket/prefix, create immutable generation manifests, "
            "diff generations, verify drift, and trace object provenance."
        ),
        status=CapabilityStatus.CURRENT,
        modes=(OperationMode.READ_ONLY, OperationMode.PLAN, OperationMode.REVIEW),
        risk=PlanRiskLevel.READ_ONLY,
        surfaces=(CapabilitySurface.RUNTIME, CapabilitySurface.CLI),
        claude_analogues=("TaskGet", "Grep"),
    ),
    CapabilitySpec(
        name="migration_decision_packets",
        title="Evaluate S3 replica or region moves",
        description=(
            "Create S3-only decision packets with measured source facts, cost "
            "assumptions, safety checks, rollback shape, and an approval-gated "
            "route-switch plan."
        ),
        status=CapabilityStatus.PARTIAL,
        modes=(OperationMode.PLAN, OperationMode.REVIEW),
        risk=PlanRiskLevel.LARGE_WRITE,
        surfaces=(CapabilitySurface.RUNTIME, CapabilitySurface.CLI),
        claude_analogues=("Plan",),
        requires_approval=True,
    ),
    CapabilitySpec(
        name="task_ledger",
        title="Inspect and control background tasks",
        description=(
            "List, inspect, watch, cancel, approve, retry, and inspect events "
            "and artifacts for durable Nimbus tasks."
        ),
        status=CapabilityStatus.CURRENT,
        modes=(OperationMode.WATCH, OperationMode.REVIEW, OperationMode.POLICY_ADMIN),
        risk=PlanRiskLevel.READ_ONLY,
        surfaces=(
            CapabilitySurface.RUNTIME,
            CapabilitySurface.CLI,
            CapabilitySurface.SLACK,
        ),
        claude_analogues=("TaskList", "TaskGet", "TaskUpdate", "Monitor"),
    ),
    CapabilitySpec(
        name="automation_templates",
        title="Create recurring storage workflows",
        description=(
            "Create common recurring workflows such as weekly channel backup, "
            "duplicate checks, large-file alerts, and approval-gated cleanup."
        ),
        status=CapabilityStatus.ROADMAP,
        modes=(OperationMode.PLAN, OperationMode.APPLY, OperationMode.REVIEW),
        risk=PlanRiskLevel.LARGE_WRITE,
        surfaces=(
            CapabilitySurface.RUNTIME,
            CapabilitySurface.CLI,
            CapabilitySurface.SLACK,
        ),
        claude_analogues=("CronCreate", "CronList", "CronDelete"),
        roadmap_feature="Feature 13",
        requires_approval=True,
    ),
    CapabilitySpec(
        name="ask_user_choice",
        title="Ask richer user choice questions",
        description=(
            "Ask a bounded multiple-choice question when Nimbus needs a user to "
            "select a strategy, scope, or ambiguity resolution."
        ),
        status=CapabilityStatus.ROADMAP,
        modes=(OperationMode.PLAN, OperationMode.REVIEW),
        risk=PlanRiskLevel.READ_ONLY,
        surfaces=(
            CapabilitySurface.RUNTIME,
            CapabilitySurface.CLI,
            CapabilitySurface.SLACK,
        ),
        claude_analogues=("AskUserQuestion",),
        roadmap_feature="Feature 18",
    ),
    CapabilitySpec(
        name="candidate_plans",
        title="Generate speculative candidate plans",
        description=(
            "Generate multiple read-only candidate plans for risky work, render "
            "them side by side, and atomically approve only the selected plan."
        ),
        status=CapabilityStatus.ROADMAP,
        modes=(OperationMode.PLAN, OperationMode.REVIEW),
        risk=PlanRiskLevel.DESTRUCTIVE,
        surfaces=(
            CapabilitySurface.RUNTIME,
            CapabilitySurface.CLI,
            CapabilitySurface.SLACK,
        ),
        claude_analogues=("EnterPlanMode", "ExitPlanMode"),
        roadmap_feature="Feature 18",
        requires_approval=True,
    ),
    CapabilitySpec(
        name="parallel_candidate_agents",
        title="Compare read-only candidate agents",
        description=(
            "Run multiple read-only planners over the same hydrated context and "
            "compare cost, latency, confidence, risk, and evidence coverage."
        ),
        status=CapabilityStatus.ROADMAP,
        modes=(OperationMode.PLAN, OperationMode.REVIEW),
        risk=PlanRiskLevel.READ_ONLY,
        surfaces=(CapabilitySurface.RUNTIME, CapabilitySurface.CLI),
        claude_analogues=("Agent",),
        roadmap_feature="Feature 14",
    ),
    CapabilitySpec(
        name="task_event_monitor",
        title="Stream task events",
        description=(
            "Follow task status and event changes as they happen, first through "
            "CLI watch and Slack thread updates, later through true log streams."
        ),
        status=CapabilityStatus.PARTIAL,
        modes=(OperationMode.WATCH,),
        risk=PlanRiskLevel.READ_ONLY,
        surfaces=(
            CapabilitySurface.RUNTIME,
            CapabilitySurface.CLI,
            CapabilitySurface.SLACK,
        ),
        claude_analogues=("Monitor", "PushNotification"),
    ),
    CapabilitySpec(
        name="tool_search_mcp",
        title="Discover external tool resources",
        description=(
            "Expose connector/MCP resource discovery as a Nimbus product "
            "capability once there is a production-stable provider path."
        ),
        status=CapabilityStatus.ROADMAP,
        modes=(OperationMode.READ_ONLY, OperationMode.PLAN),
        risk=PlanRiskLevel.READ_ONLY,
        surfaces=(CapabilitySurface.RUNTIME,),
        claude_analogues=("ToolSearch", "ListMcpResourcesTool"),
        roadmap_feature="deferred",
    ),
)

_BY_NAME = {capability.name: capability for capability in _CAPABILITIES}
if len(_BY_NAME) != len(_CAPABILITIES):  # pragma: no cover - import-time guard
    msg = "Nimbus capability names must be unique"
    raise RuntimeError(msg)


def all_capabilities(
    *,
    status: CapabilityStatus | None = None,
    surface: CapabilitySurface | None = None,
    include_roadmap: bool = True,
) -> tuple[CapabilitySpec, ...]:
    """Return the shared Nimbus capability catalog with optional filters."""
    capabilities = _CAPABILITIES
    if not include_roadmap:
        capabilities = tuple(
            capability
            for capability in capabilities
            if capability.status is not CapabilityStatus.ROADMAP
        )
    if status is not None:
        capabilities = tuple(
            capability for capability in capabilities if capability.status is status
        )
    if surface is not None:
        capabilities = tuple(
            capability for capability in capabilities if surface in capability.surfaces
        )
    return capabilities


def get_capability(name: str) -> CapabilitySpec:
    """Return one capability by name, raising ``KeyError`` if unknown."""
    return _BY_NAME[name]


def capability_for_ai_tool(tool_name: str) -> CapabilitySpec | None:
    """Return the capability backing a model-facing tool name, if any."""
    for capability in _CAPABILITIES:
        if capability.ai_tool_name == tool_name:
            return capability
    return None


def capability_names() -> tuple[str, ...]:
    """Return all capability names in registry order."""
    return tuple(capability.name for capability in _CAPABILITIES)
