"""
WorkPacket data models for the recursive work-packet runtime.

This module defines the core data structures used to represent work packets,
their states, artifacts, and resumption snapshots throughout the runtime lifecycle.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _now_iso() -> str:
    """Return current UTC time as an ISO 8601 formatted string."""
    return datetime.now(timezone.utc).isoformat()


class PacketStatus(str, Enum):
    """Lifecycle status of a work packet."""

    CREATED = "created"  # initialized, not yet emitted
    EMITTED = "emitted"  # written to disk, available for claiming
    CLAIMED = "claimed"  # an agent has picked it up
    IN_PROGRESS = "in_progress"  # actively working
    PAUSED = "paused"  # waiting at a gate or checkpoint
    COMPLETED = "completed"  # terminal: success
    FAILED = "failed"  # terminal: failure
    RESUMED = "resumed"  # picked up by a different agent


class DelegationPermission(str, Enum):
    """Controls whether and how a packet may delegate work to child packets."""

    NONE = "none"  # cannot delegate
    REQUEST = "request"  # can request delegation, needs approval
    APPROVE = "approve"  # can delegate without approval


class ShapeType(str, Enum):
    """Classification of reusable shape artifacts produced during execution."""

    PROMPT_TEMPLATE = "prompt_template"
    SKILL_DEFINITION = "skill_definition"
    PACKET_TEMPLATE = "packet_template"
    ORCHESTRATION_SHAPE = "orchestration_shape"
    EVALUATION_CHECK = "evaluation_check"
    DELEGATION_PATTERN = "delegation_pattern"


class HarnessType(str, Enum):
    """Identifies the execution harness (agent runtime) a packet is associated with."""

    UNKNOWN = "unknown"
    FAKE = "fake"
    CLAUDE_CODE = "claude_code"
    HTTP_API = "http_api"
    CODEX = "codex"
    GEMINI_CLI = "gemini_cli"
    QWEN_CLI = "qwen_cli"
    OPENCODE = "opencode"
    OPENCLAW = "openclaw"
    MINIMAX = "minimax"
    QWEN = "qwen"
    GEMINI = "gemini"
    OPENROUTER = "openrouter"
    HUMAN = "human"
    TEST = "test"


class ShapeRef(BaseModel):
    """
    Reference to a shape artifact that was saved to durable storage.

    Shapes are reusable, named units of structured knowledge (prompts, skills,
    delegation patterns, etc.) that can be discovered during packet execution and
    reused by subsequent packets in the same run.
    """

    shape_id: str
    shape_type: ShapeType
    name: str
    tags: list[str] = Field(default_factory=list)
    created_from_packet_id: str
    created_at: str = Field(default_factory=_now_iso)


class ResumptionSnapshot(BaseModel):
    """
    Frozen checkpoint of a packet's execution state used to resume interrupted work.

    Snapshots are created during execution to capture work-in-progress so that
    if an agent crashes or the runtime restarts, another agent can pick up the
    packet from a known-good checkpoint rather than starting over.
    """

    packet_id: str
    run_id: str
    snapshot_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    label: str
    state: dict[str, Any]
    artifacts: list[str] = Field(default_factory=list)
    shapes: list[ShapeRef] = Field(default_factory=list)
    event_id: str
    created_at: str = Field(default_factory=_now_iso)


class WorkResult(BaseModel):
    """
    Output produced when a work packet reaches a terminal state.

    This model captures the outcome of a packet's execution, including any
    artifacts produced, shapes discovered, child packets spawned, and metadata
    about duration and cost.
    """

    packet_id: str
    run_id: str
    status: PacketStatus
    output: Any = None
    error: str | None = None
    shapes_discovered: list[ShapeRef] = Field(default_factory=list)
    artifacts_produced: list[str] = Field(default_factory=list)
    duration_seconds: float | None = None
    cost_estimate: float | None = None
    children: list[str] = Field(default_factory=list)  # child packet_ids
    critic_findings: str | None = None
    completed_at: str = Field(default_factory=_now_iso)


class WorkPacket(BaseModel):
    """
    Self-contained unit of delegatable work in the recursive runtime.

    A packet encapsulates everything an agent needs to know to perform a unit of work:
    identity and lineage, the objective and success criteria, constraints on how to
    execute (tools, paths, budget), permissions for further delegation, and audit
    metadata for traceability.

    Packets form a tree rooted at a top-level goal, with child packets spawned
    by delegating agents. Any packet can be paused at a checkpoint and later
    resumed by a different agent.
    """

    # ── Identity ─────────────────────────────────────────────────────────────

    packet_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str = Field(..., description="Parent run this packet belongs to")
    parent_packet_id: str | None = Field(
        default=None, description="Packet that delegated this one, if any"
    )
    depth: int = Field(
        default=0, ge=0, description="Delegation depth (0 = root goal)"
    )

    # ── Seam — what this packet owns ─────────────────────────────────────────

    seam: str = Field(
        ...,
        description="Work seam this packet claims (e.g. 'auth-flow', 'file-parser')",
    )

    # ── Objective ─────────────────────────────────────────────────────────────

    objective: str = Field(..., description="What needs to be accomplished")
    success_criteria: str = Field(
        ..., description="How we know this is done"
    )

    # ── State ─────────────────────────────────────────────────────────────────

    current_state: dict[str, Any] = Field(
        default_factory=dict, description="Work-in-progress snapshot"
    )
    known_context: dict[str, Any] = Field(
        default_factory=dict,
        description="What previous packets in the lineage established",
    )
    open_questions: list[str] = Field(
        default_factory=list,
        description="Things needed but not yet known to proceed",
    )

    # ── Constraints ──────────────────────────────────────────────────────────

    allowed_tools: list[str] = Field(
        default_factory=list, description="Permitted tool names"
    )
    forbidden_tools: list[str] = Field(
        default_factory=list, description="Prohibited tool names"
    )
    owned_paths: list[str] = Field(
        default_factory=list, description="Accessible file paths"
    )
    forbidden_paths: list[str] = Field(
        default_factory=list, description="Inaccessible paths"
    )
    timeout_seconds: int | None = Field(
        default=None, description="Max execution time in seconds"
    )
    budget: float | None = Field(default=None, ge=0, description="Cost ceiling")

    # ── Permissions ───────────────────────────────────────────────────────────

    delegation_permission: DelegationPermission = Field(
        default=DelegationPermission.NONE
    )
    max_child_depth: int = Field(
        default=0, ge=0, description="Max delegation depth allowed for children"
    )
    side_effects_requiring_approval: list[str] = Field(
        default_factory=list,
        description="Side-effect categories that need explicit approval before execution",
    )

    # ── Artifacts ─────────────────────────────────────────────────────────────

    available_artifacts: list[str] = Field(
        default_factory=list, description="Paths to reusable resources"
    )
    created_shapes: list[str] = Field(
        default_factory=list,
        description="Shape IDs created during execution",
    )
    artifacts_produced: list[str] = Field(
        default_factory=list, description="Output artifact paths produced"
    )

    # ── Resumption ────────────────────────────────────────────────────────────

    resumption_contract: str | None = Field(
        default=None,
        description="What a resuming agent needs to know to take over",
    )
    checkpoint_hint: str | None = Field(
        default=None,
        description="Dot-notation path to a field that can be checkpointed",
    )

    # ── Audit ────────────────────────────────────────────────────────────────

    audit_trail: list[str] = Field(
        default_factory=list,
        description="Event IDs recorded in the ledger for this packet",
    )
    secrets_rule: str | None = Field(
        default=None,
        description="Policy string describing how secrets must be handled",
    )
    no_revert_rule: bool = Field(
        default=False,
        description="When True, actions taken must not be undoable",
    )

    # ── Timestamps (ISO 8601 UTC) ─────────────────────────────────────────────

    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
    claimed_at: str | None = Field(default=None)
    completed_at: str | None = Field(default=None)

    # ── Ownership ────────────────────────────────────────────────────────────

    claimed_by: str | None = Field(
        default=None,
        description="Agent or harness instance that claimed this packet",
    )
    claimed_by_harness: HarnessType = Field(default=HarnessType.UNKNOWN)

    # ── Methods ───────────────────────────────────────────────────────────────

    def claim(self, agent_id: str, harness: HarnessType) -> None:
        """
        Mark this packet as claimed by the given agent.

        Sets the claimed_by and claimed_by_harness fields, updates claimed_at
        to the current time, and advances status to CLAIMED.

        Parameters
        ----------
        agent_id:
            Identifier of the agent or agent instance claiming this packet.
        harness:
            Type of harness the agent is running under.
        """
        self.claimed_by = agent_id
        self.claimed_by_harness = harness
        self.claimed_at = _now_iso()
        self.updated_at = _now_iso()

    def checkpoint(self, state: dict[str, Any], label: str) -> ResumptionSnapshot:
        """
        Freeze the current execution state into a named resumption snapshot.

        Parameters
        ----------
        state:
            The work-in-progress state to freeze.
        label:
            Human-readable name for this checkpoint (e.g. "mid-file-write").

        Returns
        -------
        ResumptionSnapshot
            A new snapshot object that can be used to resume from this point.
        """
        self.current_state = state
        self.updated_at = _now_iso()
        return ResumptionSnapshot(
            packet_id=self.packet_id,
            run_id=self.run_id,
            label=label,
            state=state,
            artifacts=list(self.artifacts_produced),
            shapes=[
                ShapeRef.model_validate(s) if isinstance(s, dict) else s
                for s in self.created_shapes
            ],
            event_id="",  # caller populates once logged
        )

    def progress(self, state_update: dict[str, Any]) -> None:
        """
        Merge a state update into the current_state snapshot.

        Parameters
        ----------
        state_update:
            A dictionary of keys and values to merge into current_state.
            Existing keys are overwritten; new keys are added.
        """
        self.current_state.update(state_update)
        self.updated_at = _now_iso()

    def add_audit_event(self, event_id: str) -> None:
        """
        Append an event ID to the packet's audit trail.

        Parameters
        ----------
        event_id:
            The event ID to record, typically generated by the ledger.
        """
        self.audit_trail.append(event_id)
        self.updated_at = _now_iso()

    def discover_shape(self, shape_ref: ShapeRef) -> None:
        """
        Record a shape discovered or created during this packet's execution.

        Parameters
        ----------
        shape_ref:
            The ShapeRef describing the discovered shape.
        """
        if shape_ref.shape_id not in self.created_shapes:
            self.created_shapes.append(shape_ref.shape_id)
        self.updated_at = _now_iso()

    def to_markdown(self) -> str:
        """
        Render a human-readable summary of this packet as a markdown string.

        Returns
        -------
        str
            A formatted markdown representation of the packet.
        """
        lines = [
            f"# WorkPacket `{self.packet_id}`",
            "",
            f"**Run:** `{self.run_id}`  |  **Depth:** {self.depth}  |  **Seam:** `{self.seam}`",
            "",
            f"## Objective\n{self.objective}",
            "",
            f"## Success Criteria\n{self.success_criteria}",
            "",
            "## State",
            f"- Known context keys: {list(self.known_context.keys())}",
            f"- Open questions: {len(self.open_questions)}",
            "",
            "## Constraints",
            f"- Allowed tools: {self.allowed_tools or '(none)'}",
            f"- Forbidden tools: {self.forbidden_tools or '(none)'}",
            f"- Owned paths: {self.owned_paths or '(none)'}",
            f"- Forbidden paths: {self.forbidden_paths or '(none)'}",
            f"- Timeout: {self.timeout_seconds}s  |  Budget: {self.budget}",
            "",
            "## Permissions",
            f"- Delegation: `{self.delegation_permission.value}`",
            f"- Max child depth: {self.max_child_depth}",
            "",
            "## Artifacts",
            f"- Available: {self.available_artifacts or '(none)'}",
            f"- Created shapes: {self.created_shapes or '(none)'}",
            f"- Produced: {self.artifacts_produced or '(none)'}",
            "",
            "## Resumption",
            f"- Contract: {self.resumption_contract or '(none)'}",
            f"- Checkpoint hint: {self.checkpoint_hint or '(none)'}",
            "",
            "## Audit",
            f"- Events: {len(self.audit_trail)}",
            f"- Secrets rule: {self.secrets_rule or '(none)'}",
            f"- No-revert: {self.no_revert_rule}",
            "",
            "## Timestamps",
            f"- Created: {self.created_at}",
            f"- Updated: {self.updated_at}",
            f"- Claimed: {self.claimed_at or '(unclaimed)'}",
            f"- Completed: {self.completed_at or '(incomplete)'}",
            "",
            f"**Claimed by:** {self.claimed_by or '(unclaimed)'} ({self.claimed_by_harness.value})",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize this packet to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            A dictionary representation suitable for JSON serialization.
        """
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkPacket:
        """
        Deserialize a packet from a plain dictionary.

        Parameters
        ----------
        data:
            A dictionary previously produced by ``to_dict()`` or equivalent.

        Returns
        -------
        WorkPacket
            The reconstructed packet instance.
        """
        return cls.model_validate(data)
