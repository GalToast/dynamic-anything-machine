"""
Policy-constrained validation for graph specs and worker requests.

Uses Pydantic for policy config models and jsonschema for JSON schema validation.
Imports dynamic_schema models for type checking.
"""

from __future__ import annotations

import json
import os
from enum import Enum
from pathlib import Path
from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# dynamic_schema imports - types only, checked at runtime
# ---------------------------------------------------------------------------

try:
    from dynamic_schema import (  # type: ignore[import-not-found, assignment]
        GraphSpec,
        NodeType,
        SideEffectType,
        WorkerPacket,
    )
except ImportError:

    class NodeType(str, Enum):
        """Placeholder NodeType - replace with actual dynamic_schema.NodeType."""

        AGENT = "agent"
        CRITIC = "critic"
        REVIEWER = "reviewer"
        OUTPUT = "output"
        INTEGRATOR = "integrator"
        CONDITIONAL = "conditional"
        PARALLEL = "parallel"
        SEQUENTIAL = "sequential"
        TRANSFORM = "transform"
        FILTER = "filter"
        INPUT = "input"
        MERGE = "merge"
        SPLIT = "split"
        CUSTOM = "custom"

    class SideEffectType(str, Enum):
        """Placeholder SideEffectType - replace with actual dynamic_schema.SideEffectType."""

        FILE_WRITE = "file_write"
        FILE_READ = "file_read"
        SHELL_EXEC = "shell_exec"
        NETWORK_CALL = "network_call"
        ENV_MODIFY = "env_modify"
        SECRET_ACCESS = "secret_access"
        PROCESS_SPAWN = "process_spawn"
        FILESYSTEM_MODIFY = "filesystem_modify"

    class GraphSpec(BaseModel):
        """Placeholder GraphSpec - replace with actual dynamic_schema.GraphSpec."""

        nodes: List[Any] = Field(default_factory=list)
        edges: List[Any] = Field(default_factory=list)
        metadata: dict[str, Any] = Field(default_factory=dict)
        max_depth: Optional[int] = None
        max_nodes: Optional[int] = None

        model_config = {"arbitrary_types_allowed": True}

    class WorkerPacket(BaseModel):
        """Placeholder WorkerPacket - replace with actual dynamic_schema.WorkerPacket."""

        allowed_tools: List[str] = Field(default_factory=list)
        forbidden_tools: List[str] = Field(default_factory=list)
        owned_paths: List[str] = Field(default_factory=list)
        max_child_depth: int = 3
        side_effects: List[Any] = Field(default_factory=list)
        node_type: Optional[NodeType] = None
        depth: int = 0
        metadata: dict[str, Any] = Field(default_factory=dict)

        model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Profile Policy Models
# ---------------------------------------------------------------------------


class ProfilePolicy(BaseModel):
    """Policy configuration for a profile.

    Defines constraints and permissions for graph execution under this profile.
    """

    profile_name: str = Field(..., description="Unique name of the profile")

    # Capacity limits
    max_nodes: int = Field(default=50, ge=1, description="Maximum number of nodes allowed")
    max_depth: int = Field(default=10, ge=1, description="Maximum graph depth")
    max_parallelism: int = Field(default=5, ge=1, description="Maximum parallel edges")
    max_children_per_node: int = Field(default=3, ge=1, description="Maximum children per node")

    # Runtime limits
    max_runtime_seconds: int = Field(default=3600, ge=1, description="Maximum execution time")

    # Node type constraints
    allowed_node_types: List[NodeType] = Field(
        default_factory=list, description="Node types this profile permits"
    )
    required_node_types: List[NodeType] = Field(
        default_factory=list, description="Node types that must be present (e.g., critic before output)"
    )

    # Tool constraints
    allowed_tools: List[str] = Field(
        default_factory=list, description="Static allowlist of tools workers can be granted"
    )
    forbidden_tools: List[str] = Field(
        default_factory=list, description="Tools that are never granted"
    )

    # Side effect approvals
    side_effect_requires_approval: List[SideEffectType] = Field(
        default_factory=list, description="Side effects that require human approval"
    )

    # Delegation constraints
    max_delegation_depth: int = Field(default=3, ge=0, description="Maximum delegation depth")

    # Structural requirements
    require_critic_before_output: bool = Field(
        default=False, description="Must have critic node before any output/integrator node"
    )
    require_reviewer: bool = Field(default=False, description="Must have reviewer node")

    # Security constraints
    allow_arbitrary_shell: bool = Field(
        default=False, description="Cannot grant unrestricted shell access"
    )
    allow_arbitrary_file_write: bool = Field(default=False, description="Allow arbitrary file writes")
    allow_network_calls: bool = Field(default=True, description="Allow network calls")
    secret_files: List[str] = Field(
        default_factory=list, description="Paths that contain secrets, blocked by default"
    )
    safe_mode: bool = Field(default=True, description="Readonly by default")

    @field_validator("forbidden_tools", mode="after")
    @classmethod
    def ensure_shell_forbidden(cls, v: List[str], info) -> List[str]:
        """Always include shell in forbidden tools unless explicitly allowed."""
        if "shell" not in v:
            return v + ["shell"]
        return v


class PolicyValidationResult(BaseModel):
    """Result of a policy validation check."""

    valid: bool = Field(default=False, description="Whether validation passed")
    errors: List[str] = Field(default_factory=list, description="Validation errors")
    warnings: List[str] = Field(default_factory=list, description="Non-fatal warnings")
    required_approvals: List[str] = Field(
        default_factory=list, description="Approval gates that must be cleared"
    )
    approved_tools: List[str] = Field(default_factory=list, description="Tools granted by policy")
    denied_tools: List[str] = Field(default_factory=list, description="Tools explicitly denied")

    def merge(self, other: PolicyValidationResult) -> PolicyValidationResult:
        """Merge another result into this one."""
        return PolicyValidationResult(
            valid=self.valid and other.valid,
            errors=self.errors + other.errors,
            warnings=self.warnings + other.warnings,
            required_approvals=self.required_approvals + other.required_approvals,
            approved_tools=self.approved_tools + other.approved_tools,
            denied_tools=self.denied_tools + other.denied_tools,
        )


# ---------------------------------------------------------------------------
# Policy Validator
# ---------------------------------------------------------------------------


class PolicyValidator:
    """Validates graph specs and worker requests against a ProfilePolicy."""

    def __init__(self, profiles_dir: Optional[Path] = None):
        """Initialize validator with optional profiles directory.

        Args:
            profiles_dir: Directory containing profile JSON files. Defaults to ./profiles
                        relative to this module.
        """
        if profiles_dir is None:
            profiles_dir = Path(__file__).parent / "profiles"
        self.profiles_dir = Path(profiles_dir)
        self.profiles_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Graph Spec Validation
    # ------------------------------------------------------------------

    def validate_graph_spec(
        self, graph_spec: GraphSpec, profile: ProfilePolicy
    ) -> PolicyValidationResult:
        """Validate a complete graph spec against a profile policy.

        Args:
            graph_spec: The graph specification to validate
            profile: The profile policy to validate against

        Returns:
            PolicyValidationResult with validation status and any errors/warnings
        """
        result = PolicyValidationResult(valid=True)

        # Collect all node types in the graph
        node_types: dict[str, NodeType] = {}
        node_ids: set[str] = set()
        depth_cache: dict[str, int] = {}

        for node in graph_spec.nodes:
            node_id = getattr(node, "id", None) or getattr(node, "name", None)
            if node_id:
                node_ids.add(node_id)
                node_types[node_id] = getattr(node, "node_type", NodeType.CUSTOM)

        # 1. Node count check
        if len(node_ids) > profile.max_nodes:
            result.valid = False
            result.errors.append(
                f"Node count {len(node_ids)} exceeds max_nodes {profile.max_nodes}"
            )

        # 2. Max depth check
        computed_depth = self._compute_max_depth(graph_spec)
        if computed_depth > profile.max_depth:
            result.valid = False
            result.errors.append(
                f"Graph depth {computed_depth} exceeds max_depth {profile.max_depth}"
            )

        # 3. Parallelism check (edges from same parent to multiple children)
        parallelism = self._compute_max_parallelism(graph_spec)
        if parallelism > profile.max_parallelism:
            result.valid = False
            result.errors.append(
                f"Max parallelism {parallelism} exceeds max_parallelism {profile.max_parallelism}"
            )

        # 4. Allowed node types check
        for node_id, node_type in node_types.items():
            if profile.allowed_node_types and node_type not in profile.allowed_node_types:
                result.valid = False
                result.errors.append(
                    f"Node '{node_id}' has disallowed type '{node_type}'"
                )

        # 5. Required node types check
        present_required = set(profile.required_node_types) & set(node_types.values())
        missing_required = set(profile.required_node_types) - present_required
        if missing_required:
            result.valid = False
            result.errors.append(
                f"Missing required node types: {', '.join(str(t) for t in missing_required)}"
            )

        # 6. Critic before output check
        if profile.require_critic_before_output:
            critic_ok = self._check_critic_before_output(graph_spec, node_types)
            if not critic_ok:
                result.errors.append(
                    "Profile requires critic node before any output/integrator node"
                )
                result.valid = False

        # 7. Reviewer check
        if profile.require_reviewer:
            has_reviewer = NodeType.REVIEWER in node_types.values()
            if not has_reviewer:
                result.errors.append("Profile requires a reviewer node")
                result.valid = False

        # 8. Tool allowlist check (if tools are specified in graph metadata)
        graph_tools = getattr(graph_spec, "metadata", {}).get("allowed_tools", [])
        disallowed_tools = [t for t in graph_tools if t not in profile.allowed_tools]
        if disallowed_tools:
            result.valid = False
            result.errors.append(
                f"Graph requests disallowed tools: {', '.join(disallowed_tools)}"
            )

        # 9. Forbidden tools check
        forbidden_in_graph = [t for t in graph_tools if t in profile.forbidden_tools]
        if forbidden_in_graph:
            result.valid = False
            result.errors.append(
                f"Graph uses forbidden tools: {', '.join(forbidden_in_graph)}"
            )

        # 10. Side effects approval check
        side_effects = getattr(graph_spec, "metadata", {}).get("side_effects", [])
        for effect in side_effects:
            effect_type = getattr(effect, "type", None) or effect.get("type") if isinstance(effect, dict) else None
            if effect_type in profile.side_effect_requires_approval:
                result.warnings.append(
                    f"Side effect '{effect_type}' requires approval before execution"
                )
                result.required_approvals.append(
                    f"Approve side effect: {effect_type}"
                )

        return result

    def _compute_max_depth(self, graph_spec: GraphSpec) -> int:
        """Compute the maximum depth of the graph."""
        edges = getattr(graph_spec, "edges", [])
        children_map: dict[str, list[str]] = {}
        roots: set[str] = set()

        for edge in edges:
            if isinstance(edge, dict):
                source = edge.get("source") or edge.get("from")
                target = edge.get("target") or edge.get("to")
            else:
                source = getattr(edge, "source", None) or getattr(edge, "from", None)
                target = getattr(edge, "target", None) or getattr(edge, "to", None)

            if source and target:
                children_map.setdefault(source, []).append(target)
                roots.discard(target)

        node_ids = {getattr(n, "id", None) or getattr(n, "name", None) for n in graph_spec.nodes}
        roots.update(node_ids - set(children_map.keys()))

        depths: dict[str, int] = {}

        def compute_depth(node_id: str) -> int:
            if node_id in depths:
                return depths[node_id]
            children = children_map.get(node_id, [])
            if not children:
                depths[node_id] = 1
                return 1
            max_child_depth = max(compute_depth(c) for c in children)
            depths[node_id] = max_child_depth + 1
            return depths[node_id]

        if not roots:
            return 1

        return max(compute_depth(r) for r in roots)

    def _compute_max_parallelism(self, graph_spec: GraphSpec) -> int:
        """Compute maximum number of parallel edges from a single parent."""
        edges = getattr(graph_spec, "edges", [])
        source_count: dict[str, int] = {}

        for edge in edges:
            if isinstance(edge, dict):
                source = edge.get("source") or edge.get("from")
            else:
                source = getattr(edge, "source", None) or getattr(edge, "from", None)

            if source:
                source_count[source] = source_count.get(source, 0) + 1

        return max(source_count.values()) if source_count else 1

    def _check_critic_before_output(
        self, graph_spec: GraphSpec, node_types: dict[str, NodeType]
    ) -> bool:
        """Check that any output/integrator node has a critic ancestor."""
        edges = getattr(graph_spec, "edges", [])
        children_map: dict[str, list[str]] = {}

        for edge in edges:
            if isinstance(edge, dict):
                source = edge.get("source") or edge.get("from")
                target = edge.get("target") or edge.get("to")
            else:
                source = getattr(edge, "source", None) or getattr(edge, "from", None)
                target = getattr(edge, "target", None) or getattr(edge, "to", None)

            if source and target:
                children_map.setdefault(source, []).append(target)

        output_nodes = {
            nid for nid, ntype in node_types.items()
            if ntype in (NodeType.OUTPUT, NodeType.INTEGRATOR)
        }

        if not output_nodes:
            return True

        ancestors_cache: dict[str, set[str]] = {}

        def get_ancestors(node_id: str) -> set[str]:
            if node_id in ancestors_cache:
                return ancestors_cache[node_id]
            ancestors: set[str] = {node_id}
            for edge in edges:
                if isinstance(edge, dict):
                    source = edge.get("source") or edge.get("from")
                    target = edge.get("target") or edge.get("to")
                else:
                    source = getattr(edge, "source", None) or getattr(edge, "from", None)
                    target = getattr(edge, "target", None) or getattr(edge, "to", None)

                if target == node_id and source:
                    ancestors.add(source)
                    ancestors.update(get_ancestors(source))

            ancestors_cache[node_id] = ancestors
            return ancestors

        for out_node in output_nodes:
            ancestors = get_ancestors(out_node)
            has_critic = any(ntype == NodeType.CRITIC for nid in ancestors for nid, ntype in node_types.items() if nid in ancestors)
            if not has_critic:
                return False

        return True

    # ------------------------------------------------------------------
    # Worker Request Validation
    # ------------------------------------------------------------------

    def validate_worker_request(
        self,
        packet: WorkerPacket,
        parent_packet: Optional[WorkerPacket],
        profile: ProfilePolicy,
    ) -> PolicyValidationResult:
        """Validate a child worker request against policy.

        Child inherits narrower permissions from parent.

        Args:
            packet: The child worker packet to validate
            parent_packet: The parent worker packet (for inheritance checks)
            profile: The profile policy

        Returns:
            PolicyValidationResult with validation status
        """
        result = PolicyValidationResult(valid=True)

        # Depth check
        if parent_packet is not None:
            max_allowed_depth = min(
                profile.max_delegation_depth,
                getattr(parent_packet, "max_child_depth", profile.max_delegation_depth),
            )
            if getattr(packet, "depth", 0) > max_allowed_depth:
                result.valid = False
                result.errors.append(
                    f"Worker depth {getattr(packet, 'depth', 0)} exceeds max allowed {max_allowed_depth}"
                )

            # Tool inheritance check - child tools must be subset of parent tools
            parent_tools = set(getattr(parent_packet, "allowed_tools", []))
            child_tools = set(getattr(packet, "allowed_tools", []))
            disallowed_inheritance = child_tools - parent_tools
            if disallowed_inheritance:
                result.valid = False
                result.errors.append(
                    f"Worker requests tools not in parent: {', '.join(disallowed_inheritance)}"
                )

            # Owned paths check - child paths must be subset of parent paths
            parent_paths = set(getattr(parent_packet, "owned_paths", []))
            child_paths = set(getattr(packet, "owned_paths", []))
            unauthorized_paths = child_paths - parent_paths
            if unauthorized_paths:
                result.valid = False
                result.errors.append(
                    f"Worker claims paths not owned by parent: {', '.join(unauthorized_paths)}"
                )

        # Forbidden tools union check
        profile_forbidden = set(profile.forbidden_tools)
        packet_forbidden = set(getattr(packet, "forbidden_tools", []))
        union_forbidden = profile_forbidden | packet_forbidden

        packet_tools = set(getattr(packet, "allowed_tools", []))
        violated_forbidden = packet_tools & union_forbidden
        if violated_forbidden:
            result.valid = False
            result.errors.append(
                f"Worker uses forbidden tools: {', '.join(violated_forbidden)}"
            )
            result.denied_tools = list(violated_forbidden)

        # Safe mode restrictions
        if profile.safe_mode:
            if profile.allow_arbitrary_file_write is False:
                for path in getattr(packet, "owned_paths", []):
                    if not self._is_safe_path(path, profile.secret_files):
                        result.warnings.append(f"Path '{path}' may contain secrets")

        return result

    def _is_safe_path(self, path: str, secret_files: List[str]) -> bool:
        """Check if a path is safe (not in secret file list)."""
        path_lower = path.lower()
        for secret in secret_files:
            if secret.lower() in path_lower:
                return False
        return True

    # ------------------------------------------------------------------
    # Tool Validation
    # ------------------------------------------------------------------

    def validate_tool_grant(
        self, tool_name: str, profile: ProfilePolicy, node_type: NodeType
    ) -> bool:
        """Validate that a tool can be granted.

        Static allowlist check - a model-generated spec CANNOT grant arbitrary tools.
        Tool must be in profile's allowed_tools list.

        Args:
            tool_name: Name of the tool to validate
            profile: The profile policy
            node_type: The node type requesting the tool

        Returns:
            True if tool is allowed by policy
        """
        # Check if tool is in allowlist
        if profile.allowed_tools:
            if tool_name not in profile.allowed_tools:
                return False

        # Check if tool is forbidden
        if tool_name in profile.forbidden_tools:
            return False

        # Security checks based on node type
        if node_type == NodeType.AGENT:
            # Agents have some latitude but still restricted
            pass

        # Shell tool special case
        if tool_name in ("shell", "bash", "exec", "run_shell"):
            if not profile.allow_arbitrary_shell:
                return False

        return True

    def validate_side_effect(
        self, action_type: SideEffectType, target: str, profile: ProfilePolicy
    ) -> bool:
        """Validate a side effect action.

        Args:
            action_type: Type of side effect
            target: Target of the side effect (e.g., file path)
            profile: The profile policy

        Returns:
            True if action is allowed, False if it needs approval or is denied
        """
        # Check if action requires approval
        if action_type in profile.side_effect_requires_approval:
            return False

        # File write restrictions
        if action_type == SideEffectType.FILE_WRITE:
            if not profile.allow_arbitrary_file_write:
                # Check if target is a safe path
                if not self._is_safe_path(target, profile.secret_files):
                    return False

        # Network call restrictions
        if action_type == SideEffectType.NETWORK_CALL:
            if not profile.allow_network_calls:
                return False

        # Secret access is always restricted
        if action_type == SideEffectType.SECRET_ACCESS:
            if not self._is_safe_path(target, profile.secret_files):
                return False

        # Shell execution requires explicit allow
        if action_type == SideEffectType.SHELL_EXEC:
            if not profile.allow_arbitrary_shell:
                return False

        return True

    def get_approval_required(
        self, packet: WorkerPacket, profile: ProfilePolicy
    ) -> List[str]:
        """Return list of approval gate descriptions that must be cleared.

        Args:
            packet: The worker packet to check
            profile: The profile policy

        Returns:
            List of approval gate descriptions
        """
        approvals: list[str] = []

        # Check side effects requiring approval
        side_effects = getattr(packet, "side_effects", [])
        for effect in side_effects:
            effect_type = getattr(effect, "type", None) or (
                effect.get("type") if isinstance(effect, dict) else None
            )
            target = getattr(effect, "target", None) or (
                effect.get("target") if isinstance(effect, dict) else ""
            )

            if effect_type and effect_type in profile.side_effect_requires_approval:
                approvals.append(
                    f"Approve {effect_type} on '{target}'"
                )

            # Also check via validate_side_effect
            if effect_type:
                try:
                    effect_enum = SideEffectType(effect_type)
                except ValueError:
                    continue

                if not self.validate_side_effect(effect_enum, target or "", profile):
                    approvals.append(
                        f"Approve {effect_type} on '{target}'"
                    )

        # Tool-related approvals
        tools = getattr(packet, "allowed_tools", [])
        for tool in tools:
            if tool in profile.forbidden_tools:
                approvals.append(f"Approve denied tool: {tool}")

        return list(set(approvals))  # Deduplicate

    # ------------------------------------------------------------------
    # Profile Loading / Saving
    # ------------------------------------------------------------------

    def load_profile(self, profile_name: str) -> ProfilePolicy:
        """Load a profile policy from the profiles directory.

        Args:
            profile_name: Name of the profile to load

        Returns:
            Loaded ProfilePolicy

        Raises:
            FileNotFoundError: If profile file does not exist
            ValueError: If profile data is invalid
        """
        profile_path = self.profiles_dir / f"{profile_name}.json"

        if not profile_path.exists():
            raise FileNotFoundError(f"Profile '{profile_name}' not found at {profile_path}")

        with open(profile_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return ProfilePolicy(**data)

    def save_profile(self, profile: ProfilePolicy, profile_name: str) -> None:
        """Save a profile policy to the profiles directory.

        Args:
            profile: The profile policy to save
            profile_name: Name for the profile file (without .json extension)
        """
        profile_path = self.profiles_dir / f"{profile_name}.json"
        profile.profile_name = profile_name

        with open(profile_path, "w", encoding="utf-8") as f:
            json.dump(profile.model_dump(mode="json"), f, indent=2)


# ---------------------------------------------------------------------------
# Standard Policy Profiles
# ---------------------------------------------------------------------------

#: Default safe readonly profile - minimal capabilities
DEFAULT_POLICY = ProfilePolicy(
    profile_name="default",
    max_nodes=50,
    max_depth=10,
    max_parallelism=5,
    max_children_per_node=3,
    max_runtime_seconds=3600,
    allowed_node_types=[
        NodeType.PLANNER,
        NodeType.WORKER,
        NodeType.REVIEWER,
        NodeType.CRITIC,
        NodeType.ROUTER,
        NodeType.INTEGRATOR,
    ],
    required_node_types=[NodeType.CRITIC],
    allowed_tools=[
        "read_file",
        "list_dir",
        "search",
        "glob",
        "read",
        "grep",
        "transform",
        "filter",
    ],
    forbidden_tools=[
        "shell",
        "bash",
        "exec",
        "run_shell",
        "write_file",
        "delete",
        "mkdir",
        "rmdir",
        "download",
        "upload",
        "http_request",
        "curl",
        "wget",
    ],
    side_effect_requires_approval=[
        SideEffectType.FILE_WRITE,
        SideEffectType.SHELL_COMMAND,
        SideEffectType.NETWORK_CALL,
        SideEffectType.EXPOSE_SECRET,
    ],
    max_delegation_depth=2,
    require_critic_before_output=True,
    require_reviewer=False,
    allow_arbitrary_shell=False,
    allow_arbitrary_file_write=False,
    allow_network_calls=False,
    secret_files=[
        ".env",
        "credentials",
        "secrets",
        "keys",
        "token",
        "password",
        ".gitcredentials",
        "id_rsa",
        "id_ed25519",
        ".netrc",
    ],
    safe_mode=True,
)

#: Relaxed profile for code tasks
CODE_POLICY = ProfilePolicy(
    profile_name="code",
    max_nodes=100,
    max_depth=15,
    max_parallelism=8,
    max_children_per_node=5,
    max_runtime_seconds=7200,
    allowed_node_types=[
        NodeType.PLANNER,
        NodeType.WORKER,
        NodeType.REVIEWER,
        NodeType.CRITIC,
        NodeType.ROUTER,
        NodeType.INTEGRATOR,
        NodeType.EXECUTOR,
    ],
    required_node_types=[NodeType.CRITIC],
    allowed_tools=[
        "read_file",
        "write_file",
        "list_dir",
        "search",
        "glob",
        "read",
        "grep",
        "transform",
        "filter",
        "edit_file",
        "create_file",
        "delete",
        "mkdir",
        "run_tests",
        "lint",
        "format",
        "compile",
        "execute",
    ],
    forbidden_tools=[
        "shell",
        "bash",
        "exec",
        "run_shell",
        "sudo",
        "chmod",
        "chown",
    ],
    side_effect_requires_approval=[
        SideEffectType.SHELL_COMMAND,
        SideEffectType.EXPOSE_SECRET,
        SideEffectType.DEPLOY,
    ],
    max_delegation_depth=3,
    require_critic_before_output=True,
    require_reviewer=True,
    allow_arbitrary_shell=False,
    allow_arbitrary_file_write=True,
    allow_network_calls=True,
    secret_files=[
        ".env",
        "credentials",
        "secrets",
        "keys",
        "token",
        "password",
        ".gitcredentials",
        "id_rsa",
        "id_ed25519",
        ".netrc",
    ],
    safe_mode=False,
)

#: Profile for research tasks
RESEARCH_POLICY = ProfilePolicy(
    profile_name="research",
    max_nodes=75,
    max_depth=12,
    max_parallelism=6,
    max_children_per_node=4,
    max_runtime_seconds=5400,
    allowed_node_types=[
        NodeType.PLANNER,
        NodeType.WORKER,
        NodeType.REVIEWER,
        NodeType.CRITIC,
        NodeType.ROUTER,
        NodeType.INTEGRATOR,
    ],
    required_node_types=[NodeType.CRITIC, NodeType.REVIEWER],
    allowed_tools=[
        "read_file",
        "list_dir",
        "search",
        "glob",
        "read",
        "grep",
        "transform",
        "filter",
        "web_search",
        "fetch_url",
        "download",
        "cache",
        "index",
    ],
    forbidden_tools=[
        "shell",
        "bash",
        "exec",
        "run_shell",
        "write_file",
        "delete",
        "mkdir",
        "rmdir",
    ],
    side_effect_requires_approval=[
        SideEffectType.FILE_WRITE,
        SideEffectType.SHELL_COMMAND,
        SideEffectType.EXPOSE_SECRET,
        SideEffectType.DEPLOY,
    ],
    max_delegation_depth=4,
    require_critic_before_output=True,
    require_reviewer=True,
    allow_arbitrary_shell=False,
    allow_arbitrary_file_write=False,
    allow_network_calls=True,
    secret_files=[
        ".env",
        "credentials",
        "secrets",
        "keys",
        "token",
        "password",
    ],
    safe_mode=True,
)

#: Strictest settings for high-risk operations
HIGH_RISK_POLICY = ProfilePolicy(
    profile_name="high_risk",
    max_nodes=20,
    max_depth=5,
    max_parallelism=2,
    max_children_per_node=2,
    max_runtime_seconds=1800,
    allowed_node_types=[
        NodeType.PLANNER,
        NodeType.WORKER,
        NodeType.REVIEWER,
    ],
    required_node_types=[NodeType.CRITIC, NodeType.REVIEWER],
    allowed_tools=[
        "read_file",
        "read",
        "grep",
        "transform",
        "filter",
        "list_dir",
    ],
    forbidden_tools=[
        "shell",
        "bash",
        "exec",
        "run_shell",
        "write_file",
        "delete",
        "mkdir",
        "rmdir",
        "download",
        "upload",
        "http_request",
        "curl",
        "wget",
        "sudo",
        "chmod",
        "chown",
        "execute",
        "run_tests",
        "create_file",
        "edit_file",
        "web_search",
        "fetch_url",
    ],
    side_effect_requires_approval=[
        SideEffectType.FILE_WRITE,
        SideEffectType.SHELL_COMMAND,
        SideEffectType.NETWORK_CALL,
        SideEffectType.EXPOSE_SECRET,
        SideEffectType.DEPLOY,
        SideEffectType.PAYMENT,
        SideEffectType.DELETE_FILE,
        SideEffectType.SUBMIT_APPLICATION,
    ],
    max_delegation_depth=1,
    require_critic_before_output=True,
    require_reviewer=True,
    allow_arbitrary_shell=False,
    allow_arbitrary_file_write=False,
    allow_network_calls=False,
    secret_files=[
        ".env",
        "credentials",
        "secrets",
        "keys",
        "token",
        "password",
        ".gitcredentials",
        "id_rsa",
        "id_ed25519",
        ".netrc",
    ],
    safe_mode=True,
)


# ---------------------------------------------------------------------------
# Convenience Functions
# ---------------------------------------------------------------------------

def get_profile(profile_name: str) -> ProfilePolicy:
    """Get a policy profile by name.

    Args:
        profile_name: Name of the profile ('default', 'code', 'research', 'high_risk')

    Returns:
        The ProfilePolicy for the given name

    Raises:
        ValueError: If profile_name is unknown
    """
    profiles = {
        "default": DEFAULT_POLICY,
        "code": CODE_POLICY,
        "research": RESEARCH_POLICY,
        "high_risk": HIGH_RISK_POLICY,
    }

    if profile_name in profiles:
        return profiles[profile_name]

    # Try to load from disk
    validator = PolicyValidator()
    try:
        return validator.load_profile(profile_name)
    except FileNotFoundError:
        raise ValueError(f"Unknown profile: {profile_name}. Valid built-ins: {list(profiles.keys())}")


def load_profile(profile_name: str) -> ProfilePolicy:
    """Load a profile from the profiles directory.

    Args:
        profile_name: Name of the profile to load

    Returns:
        Loaded ProfilePolicy
    """
    validator = PolicyValidator()
    return validator.load_profile(profile_name)


def save_profile(profile: ProfilePolicy, profile_name: str) -> None:
    """Save a profile to the profiles directory.

    Args:
        profile: The profile policy to save
        profile_name: Name for the profile file
    """
    validator = PolicyValidator()
    validator.save_profile(profile, profile_name)