"""
Graph Spec Schema for Policy-Constrained Dynamic Agent Graph Compiler.

This module defines the data models (NOT the architect) for a dynamic agent graph
compiler that supports policy-constrained execution. It uses Pydantic for
validation and provides JSON Schema export capabilities.

Data Models:
    - GraphSpec: Top-level graph specification
    - GraphNode: Individual node in the graph
    - GraphEdge: Connection between nodes
    - WorkerPacket: Payload written to packets/<worker_id>.json
    - WorkerResult: Result returned by a completed worker
    - RunManifest: Metadata for an entire run

Enums:
    - NodeType: Roles a node can take
    - SideEffectType: Categories of side effects
    - DifficultyLevel: Graph complexity levels
    - GraphShape: Structural patterns
    - DelegationPermission: Delegation policy levels
    - WorkerStatus: Worker execution outcomes

Example:
    >>> spec = GraphSpec.from_dict({"run_id": "abc", "nodes": [], "edges": []})
    >>> json_schema = GraphSpec.export_json_schema()
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar

from pydantic import (
    BaseModel,
    Field,
    computed_field,
    field_validator,
    model_validator,
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class NodeType(str, Enum):
    """
    Roles a node can assume within the graph.

    Each role carries distinct responsibilities and tool access patterns:
    - planner: Decomposes objectives into sub-tasks
    - retriever: Fetches relevant context or knowledge
    - scorer: Evaluates and ranks options
    - router: Directs flow based on conditions
    - critic: Reviews output for quality/policy compliance
    - worker: Executes delegated tasks
    - integrator: Combines results from multiple workers
    - reviewer: Validates overall execution
    - approver: Authorizes proceeding to next phase
    - executor: Performs concrete actions (file I/O, shell, etc.)
    """

    PLANNER = "planner"
    RETRIEVER = "retriever"
    SCORER = "scorer"
    ROUTER = "router"
    CRITIC = "critic"
    WORKER = "worker"
    INTEGRATOR = "integrator"
    REVIEWER = "reviewer"
    APPROVER = "approver"
    EXECUTOR = "executor"


class SideEffectType(str, Enum):
    """
    Categories of side effects a node may produce.

    Used for policy enforcement and audit trail tracking:
    - file_write: Creating or modifying files
    - shell_command: Running system commands
    - browser_action: UI automation
    - login_flow: Authentication actions
    - send_message: Outbound messaging
    - submit_application: Form submissions
    - network_call: HTTP requests
    - install: Installing packages/software
    - deploy: Deployment operations
    - payment: Financial transactions
    - delete_file: Destructive file operations
    - expose_secret: Accessing sensitive data
    """

    FILE_WRITE = "file_write"
    SHELL_COMMAND = "shell_command"
    BROWSER_ACTION = "browser_action"
    LOGIN_FLOW = "login_flow"
    SEND_MESSAGE = "send_message"
    SUBMIT_APPLICATION = "submit_application"
    NETWORK_CALL = "network_call"
    INSTALL = "install"
    DEPLOY = "deploy"
    PAYMENT = "payment"
    DELETE_FILE = "delete_file"
    EXPOSE_SECRET = "expose_secret"


class DifficultyLevel(str, Enum):
    """
    Graph complexity / difficulty level.

    Guides resource allocation and timeout expectations:
    - trivial: Minimal graph, few nodes
    - simple: Linear flow, low branching
    - moderate: Some branching, moderate depth
    - complex: Deep nesting, multiple teams
    - extreme: Recursive delegation, high concurrency
    """

    TRIVIAL = "trivial"
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"
    EXTREME = "extreme"


class GraphShape(str, Enum):
    """
    Structural patterns the graph can follow.

    Defines high-level topology:
    - single_worker: One worker, linear execution
    - planner_worker_critic: Plan -> Work -> Review cycle
    - multi_team: Multiple parallel teams
    - recursive_delegation: Tasks delegated down a hierarchy
    """

    SINGLE_WORKER = "single_worker"
    PLANNER_WORKER_CRITIC = "planner_worker_critic"
    MULTI_TEAM = "multi_team"
    RECURSIVE_DELEGATION = "recursive_delegation"


class DelegationPermission(str, Enum):
    """
    Policy level for whether a node may delegate its task to another worker.

    - none: No delegation permitted
    - request: May request delegation but must await approval
    - approve: Full authority to delegate without approval
    """

    NONE = "none"
    REQUEST = "request"
    APPROVE = "approve"


class WorkerStatus(str, Enum):
    """
    Terminal status of a completed worker execution.

    - success: Completed normally with output
    - failure: Completed with an error
    - timeout: Exceeded its allocated time
    - rejected: Delegated task was rejected by parent
    - pending: Not yet started (for display only)
    """

    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    REJECTED = "rejected"
    PENDING = "pending"


class RunStatus(str, Enum):
    """
    Status of an entire graph run.

    - running: Actively executing
    - paused: Suspended (e.g. awaiting approval)
    - completed: All nodes finished successfully
    - failed: One or more nodes failed
    - cancelled: Manually cancelled
    """

    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Shared Fields
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return current UTC time in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Core Models
# ---------------------------------------------------------------------------


class GraphNode(BaseModel):
    """
    An individual node in the graph representing an agent role and its constraints.

    Attributes:
        node_id: Unique identifier for this node within the graph.
        role: The agent role this node fulfills (e.g. planner, worker, critic).
        task_prompt: Instructional prompt defining what this node must accomplish.
        allowed_tools: Tool names this node is permitted to call.
        forbidden_tools: Tool names this node is prohibited from calling.
        owned_paths: File system paths this node may access.
        forbidden_paths: File system paths this node may not access.
        timeout: Maximum execution time in seconds.
        budget: Cost ceiling for this node's operations.
        delegation_permission: Whether this node may delegate tasks further.
        max_child_depth: Deepest sub-delegation depth allowed (0 = no children).
        result_contract: Schema or description the node's output must satisfy.
        no_revert_rule: If true, this node's actions must not be undoable.
        secrets_rule: Description of how secrets must be handled (e.g. never log).
        verification_expectations: Criteria for verifying this node's output.
        depends_on: Node IDs that must complete before this node starts.
        retry_on_error: If true, the node retries on failure (up to budget limits).
        checkpoint: Dot-notation path where state is checkpointed for recovery.
    """

    node_id: str = Field(..., description="Unique identifier for this node")
    role: NodeType = Field(..., description="The agent role this node assumes")
    task_prompt: str = Field(..., description="Instruction prompt defining the node's objective")
    allowed_tools: list[str] = Field(default_factory=list, description="Tool names permitted")
    forbidden_tools: list[str] = Field(default_factory=list, description="Tool names prohibited")
    owned_paths: list[str] = Field(default_factory=list, description="File paths this node may access")
    forbidden_paths: list[str] = Field(default_factory=list, description="File paths this node may not access")
    timeout: int | None = Field(default=None, description="Max execution time in seconds")
    budget: float | None = Field(default=None, ge=0, description="Cost ceiling for this node")
    delegation_permission: DelegationPermission = Field(
        default=DelegationPermission.NONE,
        description="Whether this node may delegate tasks",
    )
    max_child_depth: int = Field(default=0, ge=0, description="Max delegation depth (0 = no children)")
    result_contract: str | None = Field(default=None, description="Expected output schema/description")
    no_revert_rule: bool = Field(default=False, description="Actions must not be undoable")
    secrets_rule: str | None = Field(default=None, description="Secret handling policy")
    verification_expectations: str | None = Field(
        default=None, description="Criteria for verifying output correctness"
    )
    depends_on: list[str] = Field(default_factory=list, description="Node IDs that must complete first")
    retry_on_error: bool = Field(default=False, description="Retry on failure if budget allows")
    checkpoint: str | None = Field(default=None, description="Dot-notation checkpoint path for recovery")

    @field_validator("node_id", "task_prompt", mode="before")
    @classmethod
    def _strip_whitespace(cls, v: str) -> str:
        if isinstance(v, str):
            return v.strip()
        return v

    @model_validator(mode="after")
    def _check_depends_on_references_valid_nodes(self) -> GraphNode:
        """Ensure depends_on references are not self-referential."""
        if self.node_id in self.depends_on:
            raise ValueError(f"node_id '{self.node_id}' cannot depend on itself")
        return self


class GraphEdge(BaseModel):
    """
    A directed edge connecting two nodes in the graph.

    Attributes:
        from_node: Source node ID.
        to_node: Target node ID.
        condition: Optional routing condition (e.g. "output.score > 0.8").
        label: Human-readable label describing the relationship.
    """

    from_node: str = Field(..., description="Source node ID")
    to_node: str = Field(..., description="Target node ID")
    condition: str | None = Field(default=None, description="Routing condition expression")
    label: str | None = Field(default=None, description="Human-readable edge label")

    @model_validator(mode="after")
    def _check_nodes_differ(self) -> GraphEdge:
        """Source and target nodes must be different."""
        if self.from_node == self.to_node:
            raise ValueError("from_node and to_node must be different")
        return self


class GraphSpec(BaseModel):
    """
    Top-level specification for a dynamic agent graph.

    This is the data model only — it describes what a graph looks like but does
    not construct or execute it. Produced by the architect and consumed by the
    graph compiler and runtime scheduler.

    Attributes:
        run_id: Unique identifier for this graph run.
        seed: Deterministic seed for reproducibility.
        timestamp: ISO 8601 timestamp when the spec was created.
        version: Schema version string (e.g. "1.0.0").
        difficulty: Complexity level of the graph.
        shape_rationale: Human-readable explanation of why this shape was chosen.
        nodes: Ordered list of GraphNode definitions.
        edges: List of GraphEdge connections.
        total_timeout: Global timeout for the entire graph execution.
        max_depth: Maximum delegation depth across the graph.
        metadata: Arbitrary key-value metadata dictionary.
    """

    run_id: str = Field(..., description="Unique run identifier")
    seed: int = Field(default_factory=lambda: uuid.uuid4().int, description="Deterministic seed for reproducibility")
    timestamp: str = Field(default_factory=_now_iso, description="ISO 8601 creation timestamp")
    version: str = Field(default="1.0.0", description="Schema version")
    difficulty: DifficultyLevel = Field(..., description="Graph complexity level")
    shape_rationale: str = Field(..., description="Why this graph shape was chosen")
    nodes: list[GraphNode] = Field(default_factory=list, description="Node definitions")
    edges: list[GraphEdge] = Field(default_factory=list, description="Edge definitions")
    total_timeout: int | None = Field(default=None, gt=0, description="Global execution timeout (seconds)")
    max_depth: int = Field(default=10, ge=1, description="Maximum delegation depth")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary metadata")

    # Class-level JSON Schema cache
    _json_schema_cache: ClassVar[dict[str, Any] | None] = None

    # -----------------------------------------------------------------------
    # Validators
    # -----------------------------------------------------------------------

    @field_validator("run_id", mode="before")
    @classmethod
    def _run_id_not_empty(cls, v: str) -> str:
        if isinstance(v, str) and not v.strip():
            raise ValueError("run_id must not be empty")
        return v.strip()

    @model_validator(mode="after")
    def _validate_edges_reference_valid_nodes(self) -> GraphSpec:
        """All edge from_node/to_node references must exist in nodes."""
        node_ids = {n.node_id for n in self.nodes}
        for edge in self.edges:
            if edge.from_node not in node_ids:
                raise ValueError(f"Edge references unknown from_node '{edge.from_node}'")
            if edge.to_node not in node_ids:
                raise ValueError(f"Edge references unknown to_node '{edge.to_node}'")
        return self

    @model_validator(mode="after")
    def _validate_no_circular_depends(self) -> GraphSpec:
        """Detect dependency cycles using a simple DFS."""
        # Build adjacency list
        deps: dict[str, list[str]] = {n.node_id: list(n.depends_on) for n in self.nodes}
        visited: set[str] = set()
        stack: set[str] = set()

        def has_cycle(node_id: str) -> bool:
            if node_id in stack:
                return True
            if node_id in visited:
                return False
            stack.add(node_id)
            for dep in deps.get(node_id, []):
                if has_cycle(dep):
                    return True
            stack.remove(node_id)
            visited.add(node_id)
            return False

        for node_id in deps:
            if has_cycle(node_id):
                raise ValueError(f"Circular dependency detected involving node '{node_id}'")
        return self

    # -----------------------------------------------------------------------
    # Class Methods
    # -----------------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GraphSpec:
        """
        Construct a GraphSpec from a dictionary, validating all fields.

        Args:
            data: Raw dictionary conforming to the GraphSpec schema.

        Returns:
            A validated GraphSpec instance.

        Raises:
            pydantic.ValidationError: If validation fails.
        """
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize this GraphSpec to a dictionary.

        Returns:
            Dictionary representation of the spec.
        """
        return self.model_dump(mode="json")

    @classmethod
    def validate_schema(cls, data: dict[str, Any]) -> list[str]:
        """
        Validate raw data against the GraphSpec schema and return error messages.

        Args:
            data: Raw data to validate.

        Returns:
            List of error strings (empty if valid).
        """
        try:
            cls(**data)
            return []
        except Exception as e:
            errors: list[str] = []
            # flatten pydantic errors into human-readable strings
            if hasattr(e, "errors"):
                for err in e.errors():
                    loc = ".".join(str(l) for l in err["loc"])
                    errors.append(f"{loc}: {err['msg']}")
            else:
                errors.append(str(e))
            return errors

    @classmethod
    def export_json_schema(cls) -> dict[str, Any]:
        """
        Export the complete JSON Schema for the graph spec format.

        Returns:
            JSON Schema document as a dictionary.
        """
        # Use Pydantic's built-in JSON Schema generation
        schema = cls.model_json_schema()

        # Inject definitions for enums and sub-models that Pydantic hoists
        definitions: dict[str, Any] = {}

        for enum_cls in (
            NodeType,
            SideEffectType,
            DifficultyLevel,
            GraphShape,
            DelegationPermission,
            WorkerStatus,
            RunStatus,
        ):
            name = enum_cls.__name__
            definitions[name] = {
                "type": "string",
                "enum": [e.value for e in enum_cls],
                "description": enum_cls.__doc__.strip() if enum_cls.__doc__ else None,
            }

        for model_cls in (GraphNode, GraphEdge, WorkerPacket, WorkerResult, RunManifest):
            model_schema = model_cls.model_json_schema()
            definitions[model_cls.__name__] = model_schema

        schema["definitions"] = definitions
        return schema


# ---------------------------------------------------------------------------
# Worker Packet & Result Models
# ---------------------------------------------------------------------------


class WorkerPacket(BaseModel):
    """
    The payload written to packets/<worker_id>.json when a task is dispatched.

    This model represents the runtime contract between the orchestrator and
    an individual worker process. It captures everything the worker needs to
    execute its assigned task.

    Attributes:
        run_id: The parent run this worker belongs to.
        worker_id: Unique identifier for this worker instance.
        parent_worker_id: ID of the delegating worker (None for root workers).
        depth: Delegation depth (0 = root).
        role: The node role this worker fulfills.
        objective: High-level goal description.
        task_prompt: Detailed instructional prompt.
        allowed_tools: Tool names permitted for this task.
        forbidden_tools: Tool names prohibited for this task.
        owned_paths: File paths this worker may access.
        forbidden_paths: File paths this worker may not access.
        timeout: Maximum execution time in seconds.
        budget: Cost ceiling for this worker.
        delegation_permission: Whether this worker may further delegate.
        max_child_depth: Maximum depth of further delegation.
        result_contract: Expected output schema or description.
        no_revert_rule: If true, actions must not be undoable.
        secrets_rule: Secret handling policy description.
        verification_expectations: Criteria for verifying output.
        created_at: ISO 8601 dispatch timestamp.
    """

    run_id: str = Field(..., description="Parent run identifier")
    worker_id: str = Field(..., description="Unique worker instance identifier")
    parent_worker_id: str | None = Field(default=None, description="Delegating worker ID (None for root)")
    depth: int = Field(..., ge=0, description="Delegation depth (0 = root)")
    role: NodeType = Field(..., description="Node role this worker fulfills")
    objective: str = Field(..., description="High-level goal description")
    task_prompt: str = Field(..., description="Detailed task prompt")
    allowed_tools: list[str] = Field(default_factory=list, description="Permitted tool names")
    forbidden_tools: list[str] = Field(default_factory=list, description="Prohibited tool names")
    owned_paths: list[str] = Field(default_factory=list, description="Accessible file paths")
    forbidden_paths: list[str] = Field(default_factory=list, description="Inaccessible file paths")
    timeout: int | None = Field(default=None, description="Max execution time in seconds")
    budget: float | None = Field(default=None, ge=0, description="Cost ceiling")
    delegation_permission: DelegationPermission = Field(
        default=DelegationPermission.NONE,
        description="Delegation authority level",
    )
    max_child_depth: int = Field(default=0, ge=0, description="Max further delegation depth")
    result_contract: str | None = Field(default=None, description="Expected output schema")
    no_revert_rule: bool = Field(default=False, description="Actions must not be undoable")
    secrets_rule: str | None = Field(default=None, description="Secret handling policy")
    verification_expectations: str | None = Field(
        default=None, description="Output verification criteria"
    )
    created_at: str = Field(default_factory=_now_iso, description="ISO 8601 dispatch timestamp")


class WorkerResult(BaseModel):
    """
    Result returned by a completed worker, written to results/<worker_id>.json.

    Attributes:
        run_id: Parent run identifier.
        worker_id: This worker's unique identifier.
        parent_worker_id: ID of the delegating worker.
        status: Terminal execution status.
        output: The worker's output (string or JSON-serializable dict).
        error: Error message if status is failure/timeout.
        duration_seconds: Actual execution time.
        cost_estimate: Actual or estimated cost incurred.
        children: List of child worker IDs spawned by this worker.
        artifacts: File paths to any output artifacts produced.
        critic_findings: Review notes from a critic node (if applicable).
        verification_status: Whether output was verified (pass/fail/pending).
        completed_at: ISO 8601 completion timestamp.
    """

    run_id: str = Field(..., description="Parent run identifier")
    worker_id: str = Field(..., description="Unique worker identifier")
    parent_worker_id: str | None = Field(default=None, description="Delegating worker ID")
    status: WorkerStatus = Field(..., description="Terminal execution status")
    output: Any = Field(default=None, description="Worker output (JSON-serializable)")
    error: str | None = Field(default=None, description="Error message on failure/timeout")
    duration_seconds: float | None = Field(default=None, ge=0, description="Execution duration")
    cost_estimate: float | None = Field(default=None, ge=0, description="Cost incurred")
    children: list[str] = Field(default_factory=list, description="Child worker IDs")
    artifacts: list[str] = Field(default_factory=list, description="Output artifact file paths")
    critic_findings: str | None = Field(default=None, description="Critic node review notes")
    verification_status: str | None = Field(
        default="pending", description="Verification outcome (pass/fail/pending)"
    )
    completed_at: str = Field(default_factory=_now_iso, description="ISO 8601 completion timestamp")


# ---------------------------------------------------------------------------
# Run Manifest
# ---------------------------------------------------------------------------


class RunManifest(BaseModel):
    """
    Metadata and status tracker for an entire graph run.

    Written to manifests/<run_id>.json and updated by the runtime scheduler
    as the run progresses.

    Attributes:
        run_id: Unique run identifier.
        graph_spec_path: Path to the graph spec JSON file.
        status: Current run status.
        created_at: ISO 8601 run creation timestamp.
        updated_at: ISO 8601 timestamp of last update.
        checkpoint_path: Path to the latest checkpoint file.
        final_output_path: Path to the final output artifact.
        total_cost_estimate: Aggregated cost estimate across all workers.
        total_duration_seconds: Total wall-clock time elapsed.
        event_count: Total number of events recorded in the run log.
    """

    run_id: str = Field(..., description="Unique run identifier")
    graph_spec_path: str | None = Field(default=None, description="Path to the graph spec")
    status: RunStatus = Field(default=RunStatus.RUNNING, description="Current run status")
    created_at: str = Field(default_factory=_now_iso, description="ISO 8601 creation timestamp")
    updated_at: str = Field(default_factory=_now_iso, description="ISO 8601 last update timestamp")
    checkpoint_path: str | None = Field(default=None, description="Latest checkpoint file path")
    final_output_path: str | None = Field(default=None, description="Final output artifact path")
    total_cost_estimate: float = Field(default=0.0, ge=0, description="Aggregated cost estimate")
    total_duration_seconds: float | None = Field(default=None, ge=0, description="Total wall-clock time")
    event_count: int = Field(default=0, ge=0, description="Total events in the run log")


# ---------------------------------------------------------------------------
# JSON Schema Export
# ---------------------------------------------------------------------------


GRAPH_SPEC_JSON_SCHEMA: dict[str, Any] = GraphSpec.export_json_schema()
"""Pre-computed JSON Schema for the entire graph spec format."""


def export_schema_as_json(path: str | Path) -> None:
    """
    Write the graph spec JSON Schema to a file.

    Args:
        path: Destination file path.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(GRAPH_SPEC_JSON_SCHEMA, indent=2), encoding="utf-8")
