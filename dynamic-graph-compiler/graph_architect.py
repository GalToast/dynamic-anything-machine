"""
Graph Architect: synthesises a task-specific execution graph from a natural-language objective.

This module provides a deterministic, rule-based graph synthesis system (Milestone 1).
It does NOT call any LLM — it uses keyword heuristics to assess difficulty and shape,
then assembles a GraphSpec from profile-constrained nodes.

Imports:
    dynamic_schema: GraphSpec, GraphNode, GraphEdge, NodeType, GraphShape,
                    DifficultyLevel, WorkerPacket, WorkerResult
    policy: ProfilePolicy, PolicyValidator, load_profile
"""

from __future__ import annotations

import hashlib
import random
import re
from typing import Any, Dict, List, Tuple

from dynamic_schema import (
    DifficultyLevel,
    GraphEdge,
    GraphNode,
    GraphShape,
    GraphSpec,
    NodeType,
)

from policy import ProfilePolicy


# ---------------------------------------------------------------------------
# DifficultyAssessor
# ---------------------------------------------------------------------------


class DifficultyAssessor:
    """
    Heuristic-based difficulty and shape assessor.

    Uses simple keyword and pattern matching on the objective string to produce
    a DifficultyLevel, GraphShape, and rationale — without LLM involvement.
    """

    # Keywords that push toward higher difficulty
    _TRIVIAL_KEYWORDS = [
        "list", "show", "get", "read", "find", "check", "view", "display", "print",
    ]
    _SIMPLE_KEYWORDS = [
        "write", "create", "update", "edit", "generate", "build", "make", "do",
    ]
    _MODERATE_KEYWORDS = [
        "analyze", "compare", "review", "search", "fetch", "process", "validate",
        "transform", "convert", "parse", "extract",
    ]
    _COMPLEX_KEYWORDS = [
        "coordinate", "orchestrate", "parallel", "concurrent", "distribute",
        "multi", "team", "synthesize", "aggregate",
    ]
    _EXTREME_KEYWORDS = [
        "recursive", "delegate", "spawn", "escalate", "negotiate", "compromise",
        "bootstrap", "self-improve",
    ]

    # Keywords that imply side effects
    _RISK_KEYWORDS = [
        "delete", "remove", "destroy", "drop", "deploy", "submit", "pay",
        "send", "post", "upload", "exec", "run", "install", "update",
        "modify", "overwrite", "replace", "cancel",
    ]

    # Keywords that imply delegation / multi-node coordination
    _DELEGATION_KEYWORDS = [
        "team", "delegate", "spawn", "child", "parent", "worker", "coordinator",
        "parallel", "concurrent", "split", "partition", "distribute",
    ]

    def assess(self, objective: str) -> Tuple[DifficultyLevel, GraphShape, str]:
        """
        Assess the difficulty level, graph shape, and rationale for an objective.

        Args:
            objective: The natural-language objective string.

        Returns:
            A tuple of (difficulty_level, graph_shape, rationale).
        """
        obj_lower = objective.lower()

        # Count keyword matches per category
        trivial_score = sum(1 for kw in self._TRIVIAL_KEYWORDS if kw in obj_lower)
        simple_score = sum(1 for kw in self._SIMPLE_KEYWORDS if kw in obj_lower)
        moderate_score = sum(1 for kw in self._MODERATE_KEYWORDS if kw in obj_lower)
        complex_score = sum(1 for kw in self._COMPLEX_KEYWORDS if kw in obj_lower)
        extreme_score = sum(1 for kw in self._EXTREME_KEYWORDS if kw in obj_lower)

        risk_score = sum(1 for kw in self._RISK_KEYWORDS if kw in obj_lower)
        delegation_score = sum(1 for kw in self._DELEGATION_KEYWORDS if kw in obj_lower)

        # Determine difficulty level
        if extreme_score > 0 or delegation_score >= 3:
            difficulty = DifficultyLevel.EXTREME
        elif complex_score > 0 or delegation_score >= 2:
            difficulty = DifficultyLevel.COMPLEX
        elif moderate_score > 0 or delegation_score >= 1:
            difficulty = DifficultyLevel.MODERATE
        elif simple_score > 0 and risk_score > 0:
            difficulty = DifficultyLevel.MODERATE
        elif simple_score > 0 or moderate_score > 0:
            difficulty = DifficultyLevel.SIMPLE
        else:
            difficulty = DifficultyLevel.TRIVIAL

        # Determine graph shape
        shape = self._determine_shape(difficulty, delegation_score, obj_lower)

        rationale = (
            f"difficulty={difficulty.value}, shape={shape.value}, "
            f"risk_signals={risk_score}, delegation_signals={delegation_score}"
        )

        return difficulty, shape, rationale

    def _determine_shape(
        self, difficulty: DifficultyLevel, delegation_score: int, obj_lower: str
    ) -> GraphShape:
        """Determine the graph shape based on difficulty and delegation signals."""
        if difficulty == DifficultyLevel.EXTREME:
            return GraphShape.RECURSIVE_DELEGATION
        if difficulty == DifficultyLevel.COMPLEX or delegation_score >= 2:
            return GraphShape.MULTI_TEAM
        if difficulty == DifficultyLevel.MODERATE:
            return GraphShape.PLANNER_WORKER_CRITIC
        # trivial or simple
        if delegation_score >= 1 or "planner" in obj_lower or "plan" in obj_lower:
            return GraphShape.PLANNER_WORKER_CRITIC
        return GraphShape.SINGLE_WORKER


# ---------------------------------------------------------------------------
# GraphArchitect
# ---------------------------------------------------------------------------


class GraphArchitect:
    """
    Synthesises a task-specific GraphSpec from an objective and profile policy.

    This is a deterministic rule-based system (Milestone 1) — same inputs
    always produce the same output given the same seed.

    The architect does NOT call any LLM. It uses DifficultyAssessor to determine
    difficulty/shape, then assembles nodes using static profile allowlists.
    """

    # Timeout baselines per difficulty level (seconds)
    _BASE_TIMEOUTS: Dict[DifficultyLevel, int] = {
        DifficultyLevel.TRIVIAL: 60,
        DifficultyLevel.SIMPLE: 120,
        DifficultyLevel.MODERATE: 300,
        DifficultyLevel.COMPLEX: 600,
        DifficultyLevel.EXTREME: 900,
    }

    # Multiplier per shape (approximate node count / complexity)
    _SHAPE_TIMEOUT_MULTIPLIERS: Dict[GraphShape, float] = {
        GraphShape.SINGLE_WORKER: 1.0,
        GraphShape.PLANNER_WORKER_CRITIC: 1.5,
        GraphShape.MULTI_TEAM: 2.5,
        GraphShape.RECURSIVE_DELEGATION: 3.0,
    }

    def __init__(self, policy: ProfilePolicy, seed: int | None = None) -> None:
        """
        Initialise the GraphArchitect.

        Args:
            policy: The ProfilePolicy providing tool/path constraints.
            seed: Optional random seed for deterministic node ID generation.
                  If None, a fixed seed is used.
        """
        self.policy = policy
        self._seed = seed if seed is not None else 42
        self._assessor = DifficultyAssessor()

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def architect(
        self, objective: str, difficulty: DifficultyLevel, shape: GraphShape
    ) -> GraphSpec:
        """
        Synthesise a complete GraphSpec for the given objective.

        This method is DETERMINISTIC — same inputs produce same output
        given the same seed.

        Args:
            objective: The natural-language objective string.
            difficulty: The assessed difficulty level.
            shape: The structural graph shape.

        Returns:
            A complete, validated GraphSpec ready for execution.
        """
        # Use deterministic seed derived from objective + policy name
        hash_input = int(self.hash_seed(objective, self.policy.profile_name, self._seed), 16)
        rng = random.Random(hash_input)

        nodes = self._build_nodes(objective, difficulty, shape, rng)
        edges = self._build_edges(nodes, shape)

        timeout = self._estimate_timeout(difficulty, shape)

        run_id = f"run-{abs(hash_input) % 1_000_000:06d}"

        spec = GraphSpec(
            run_id=run_id,
            seed=self._seed,
            difficulty=difficulty,
            shape_rationale=f"shape={shape.value}, difficulty={difficulty.value}",
            nodes=nodes,
            edges=edges,
            total_timeout=timeout,
            max_depth=self._max_depth_for_shape(shape),
            metadata={
                "objective": objective,
                "policy_name": self.policy.profile_name,
                "architect_version": "1.0.0",
            },
        )

        return spec

    @staticmethod
    def hash_seed(objective: str, policy_name: str, seed: int) -> str:
        """
        Create a deterministic seed hash from inputs.

        Args:
            objective: The natural-language objective.
            policy_name: Name of the profile policy.
            seed: Base seed integer.

        Returns:
            A hex string derived from the inputs.
        """
        raw = f"{objective}|{policy_name}|{seed}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    # -----------------------------------------------------------------------
    # Node building
    # -----------------------------------------------------------------------

    def _build_nodes(
        self, objective: str, difficulty: DifficultyLevel, shape: GraphShape, rng: random.Random
    ) -> List[GraphNode]:
        """Build the node list for the given shape and difficulty."""
        if shape == GraphShape.SINGLE_WORKER:
            return self._build_single_worker_nodes(objective, difficulty, rng)
        if shape == GraphShape.PLANNER_WORKER_CRITIC:
            return self._build_planner_worker_critic_nodes(objective, difficulty, rng)
        if shape == GraphShape.MULTI_TEAM:
            return self._build_multi_team_nodes(objective, difficulty, rng)
        if shape == GraphShape.RECURSIVE_DELEGATION:
            return self._build_recursive_delegation_nodes(objective, difficulty, rng)
        # Fallback to single worker
        return self._build_single_worker_nodes(objective, difficulty, rng)

    def _build_single_worker_nodes(
        self, objective: str, difficulty: DifficultyLevel, rng: random.Random
    ) -> List[GraphNode]:
        """Build a single worker node."""
        profile = self.policy
        worker_tools = self._determine_tools(NodeType.WORKER, profile)
        worker = GraphNode(
            node_id=self._generate_node_id(NodeType.WORKER, 0),
            role=NodeType.WORKER,
            task_prompt=objective,
            allowed_tools=worker_tools,
            forbidden_tools=list(profile.forbidden_tools),
            owned_paths=list(profile.allowed_paths),
            forbidden_paths=list(profile.forbidden_paths),
            timeout=self._estimate_timeout(difficulty, GraphShape.SINGLE_WORKER),
            delegation_permission=DelegationPermission.NONE,
            result_contract=self._result_contract_for_role(NodeType.WORKER),
            verification_expectations=self._verification_for_role(NodeType.WORKER),
        )
        return [worker]

    def _build_planner_worker_critic_nodes(
        self, objective: str, difficulty: DifficultyLevel, rng: random.Random
    ) -> List[GraphNode]:
        """Build planner -> worker -> critic -> reviewer chain."""
        nodes: List[GraphNode] = []
        timeout = self._estimate_timeout(difficulty, GraphShape.PLANNER_WORKER_CRITIC)

        # Planner
        planner = GraphNode(
            node_id=self._generate_node_id(NodeType.PLANNER, 0),
            role=NodeType.PLANNER,
            task_prompt=f"Decompose the following objective into precise sub-tasks:\n{objective}",
            allowed_tools=self._determine_tools(NodeType.PLANNER, self.policy),
            forbidden_tools=list(self.policy.forbidden_tools),
            owned_paths=list(self.policy.allowed_paths),
            forbidden_paths=list(self.policy.forbidden_paths),
            timeout=int(timeout * 0.15),
            delegation_permission=DelegationPermission.NONE,
            result_contract="A structured list of sub-tasks with clear success criteria.",
            verification_expectations="All sub-tasks are specific, achievable, and directly support the objective.",
        )
        nodes.append(planner)

        # Worker
        worker = GraphNode(
            node_id=self._generate_node_id(NodeType.WORKER, 0),
            role=NodeType.WORKER,
            task_prompt=f"Execute the following task:\n{objective}",
            allowed_tools=self._determine_tools(NodeType.WORKER, self.policy),
            forbidden_tools=list(self.policy.forbidden_tools),
            owned_paths=list(self.policy.allowed_paths),
            forbidden_paths=list(self.policy.forbidden_paths),
            timeout=int(timeout * 0.50),
            delegation_permission=DelegationPermission.APPROVE,
            max_child_depth=1,
            result_contract=self._result_contract_for_role(NodeType.WORKER),
            verification_expectations=self._verification_for_role(NodeType.WORKER),
            depends_on=[planner.node_id],
        )
        nodes.append(worker)

        # Critic
        critic = GraphNode(
            node_id=self._generate_node_id(NodeType.CRITIC, 0),
            role=NodeType.CRITIC,
            task_prompt=f"Review and critique the output for:\n{objective}",
            allowed_tools=self._determine_tools(NodeType.CRITIC, self.policy),
            forbidden_tools=list(self.policy.forbidden_tools),
            owned_paths=list(self.policy.allowed_paths),
            forbidden_paths=list(self.policy.forbidden_paths),
            timeout=int(timeout * 0.20),
            delegation_permission=DelegationPermission.NONE,
            result_contract="A critique with identified issues and suggested corrections.",
            verification_expectations="Critique is specific, actionable, and policy-compliant.",
            depends_on=[worker.node_id],
        )
        nodes.append(critic)

        # Reviewer
        reviewer = GraphNode(
            node_id=self._generate_node_id(NodeType.REVIEWER, 0),
            role=NodeType.REVIEWER,
            task_prompt=f"Final review and validation for:\n{objective}",
            allowed_tools=self._determine_tools(NodeType.REVIEWER, self.policy),
            forbidden_tools=list(self.policy.forbidden_tools),
            owned_paths=list(self.policy.allowed_paths),
            forbidden_paths=list(self.policy.forbidden_paths),
            timeout=int(timeout * 0.15),
            delegation_permission=DelegationPermission.NONE,
            result_contract="Final validated output with sign-off.",
            verification_expectations="Output meets all success criteria and policy constraints.",
            depends_on=[critic.node_id],
        )
        nodes.append(reviewer)

        return nodes

    def _build_multi_team_nodes(
        self, objective: str, difficulty: DifficultyLevel, rng: random.Random
    ) -> List[GraphNode]:
        """Build coordinator -> multiple workers -> integrator -> reviewer."""
        nodes: List[GraphNode] = []
        timeout = self._estimate_timeout(difficulty, GraphShape.MULTI_TEAM)
        num_workers = min(4, max(2, int(difficulty.value[0]) if difficulty.value[0].isdigit() else 2))

        # Coordinator
        coordinator = GraphNode(
            node_id=self._generate_node_id(NodeType.PLANNER, 0),
            role=NodeType.PLANNER,
            task_prompt=f"Coordinate and partition the following objective into parallel sub-tasks:\n{objective}",
            allowed_tools=self._determine_tools(NodeType.PLANNER, self.policy),
            forbidden_tools=list(self.policy.forbidden_tools),
            owned_paths=list(self.policy.allowed_paths),
            forbidden_paths=list(self.policy.forbidden_paths),
            timeout=int(timeout * 0.10),
            delegation_permission=DelegationPermission.APPROVE,
            max_child_depth=1,
            result_contract="A partition plan assigning distinct sub-tasks to worker roles.",
            verification_expectations="Partition is balanced, independent, and policy-compliant.",
        )
        nodes.append(coordinator)

        # Workers
        worker_ids: List[str] = []
        for i in range(num_workers):
            worker = GraphNode(
                node_id=self._generate_node_id(NodeType.WORKER, i),
                role=NodeType.WORKER,
                task_prompt=f"Execute your assigned sub-task ({i + 1}/{num_workers}) of:\n{objective}",
                allowed_tools=self._determine_tools(NodeType.WORKER, self.policy),
                forbidden_tools=list(self.policy.forbidden_tools),
                owned_paths=list(self.policy.allowed_paths),
                forbidden_paths=list(self.policy.forbidden_paths),
                timeout=int(timeout * 0.45),
                delegation_permission=DelegationPermission.NONE,
                result_contract=self._result_contract_for_role(NodeType.WORKER),
                verification_expectations=self._verification_for_role(NodeType.WORKER),
                depends_on=[coordinator.node_id],
            )
            nodes.append(worker)
            worker_ids.append(worker.node_id)

        # Integrator
        integrator = GraphNode(
            node_id=self._generate_node_id(NodeType.INTEGRATOR, 0),
            role=NodeType.INTEGRATOR,
            task_prompt=f"Integrate results from {num_workers} workers for:\n{objective}",
            allowed_tools=self._determine_tools(NodeType.INTEGRATOR, self.policy),
            forbidden_tools=list(self.policy.forbidden_tools),
            owned_paths=list(self.policy.allowed_paths),
            forbidden_paths=list(self.policy.forbidden_paths),
            timeout=int(timeout * 0.25),
            delegation_permission=DelegationPermission.NONE,
            result_contract="A unified, coherent result combining all worker outputs.",
            verification_expectations="Integration is complete, consistent, and free of conflicts.",
            depends_on=worker_ids,
        )
        nodes.append(integrator)

        # Reviewer
        reviewer = GraphNode(
            node_id=self._generate_node_id(NodeType.REVIEWER, 0),
            role=NodeType.REVIEWER,
            task_prompt=f"Final validation of integrated result for:\n{objective}",
            allowed_tools=self._determine_tools(NodeType.REVIEWER, self.policy),
            forbidden_tools=list(self.policy.forbidden_tools),
            owned_paths=list(self.policy.allowed_paths),
            forbidden_paths=list(self.policy.forbidden_paths),
            timeout=int(timeout * 0.20),
            delegation_permission=DelegationPermission.NONE,
            result_contract="Final signed-off output.",
            verification_expectations="Output meets all criteria, policy, and quality gates.",
            depends_on=[integrator.node_id],
        )
        nodes.append(reviewer)

        return nodes

    def _build_recursive_delegation_nodes(
        self, objective: str, difficulty: DifficultyLevel, rng: random.Random
    ) -> List[GraphNode]:
        """Build a parent worker with delegation edges to child workers."""
        nodes: List[GraphNode] = []
        timeout = self._estimate_timeout(difficulty, GraphShape.RECURSIVE_DELEGATION)
        max_depth = self._max_depth_for_shape(GraphShape.RECURSIVE_DELEGATION)

        # Root parent worker
        parent_worker = GraphNode(
            node_id=self._generate_node_id(NodeType.WORKER, 0),
            role=NodeType.WORKER,
            task_prompt=(
                f"Execute the following high-stakes objective and delegate sub-tasks as needed:\n{objective}"
            ),
            allowed_tools=self._determine_tools(NodeType.WORKER, self.policy),
            forbidden_tools=list(self.policy.forbidden_tools),
            owned_paths=list(self.policy.allowed_paths),
            forbidden_paths=list(self.policy.forbidden_paths),
            timeout=int(timeout * 0.40),
            delegation_permission=DelegationPermission.APPROVE,
            max_child_depth=max_depth,
            result_contract=self._result_contract_for_role(NodeType.WORKER),
            verification_expectations=self._verification_for_role(NodeType.WORKER),
        )
        nodes.append(parent_worker)

        # First level child workers (2 children for parallel execution)
        for i in range(2):
            child = GraphNode(
                node_id=self._generate_node_id(NodeType.WORKER, i + 1),
                role=NodeType.WORKER,
                task_prompt=f"Child worker {i + 1} executing delegated task from parent:\n{objective}",
                allowed_tools=self._determine_tools(NodeType.WORKER, self.policy),
                forbidden_tools=list(self.policy.forbidden_tools),
                owned_paths=list(self.policy.allowed_paths),
                forbidden_paths=list(self.policy.forbidden_paths),
                timeout=int(timeout * 0.30),
                delegation_permission=DelegationPermission.APPROVE,
                max_child_depth=max_depth - 1,
                result_contract=self._result_contract_for_role(NodeType.WORKER),
                verification_expectations=self._verification_for_role(NodeType.WORKER),
                depends_on=[parent_worker.node_id],
            )
            nodes.append(child)

        return nodes

    # -----------------------------------------------------------------------
    # Edge building
    # -----------------------------------------------------------------------

    def _build_edges(self, nodes: List[GraphNode], shape: GraphShape) -> List[GraphEdge]:
        """Build edges connecting nodes according to the shape pattern."""
        edges: List[GraphEdge] = []

        if shape == GraphShape.SINGLE_WORKER:
            # No edges needed for single worker
            pass

        elif shape == GraphShape.PLANNER_WORKER_CRITIC:
            if len(nodes) >= 4:
                edges.append(GraphEdge(
                    from_node=nodes[0].node_id, to_node=nodes[1].node_id,
                    label="sub-tasks",
                ))
                edges.append(GraphEdge(
                    from_node=nodes[1].node_id, to_node=nodes[2].node_id,
                    label="output",
                ))
                edges.append(GraphEdge(
                    from_node=nodes[2].node_id, to_node=nodes[3].node_id,
                    label="critique",
                ))

        elif shape == GraphShape.MULTI_TEAM:
            if len(nodes) >= 4:
                coordinator_id = nodes[0].node_id
                # Coordinator -> each worker
                worker_ids = [n.node_id for n in nodes[1:len(nodes)-2]]
                for wid in worker_ids:
                    edges.append(GraphEdge(
                        from_node=coordinator_id, to_node=wid,
                        label="assignment",
                    ))
                # Workers -> integrator
                integrator_id = nodes[len(nodes)-2].node_id
                for wid in worker_ids:
                    edges.append(GraphEdge(
                        from_node=wid, to_node=integrator_id,
                        label="result",
                    ))
                # Integrator -> reviewer
                edges.append(GraphEdge(
                    from_node=integrator_id, to_node=nodes[-1].node_id,
                    label="integrated_output",
                ))

        elif shape == GraphShape.RECURSIVE_DELEGATION:
            if len(nodes) >= 2:
                parent_id = nodes[0].node_id
                for child_node in nodes[1:]:
                    edges.append(GraphEdge(
                        from_node=parent_id, to_node=child_node.node_id,
                        label="delegated_task",
                    ))

        return edges

    # -----------------------------------------------------------------------
    # Helper methods
    # -----------------------------------------------------------------------

    def _generate_node_id(self, role: NodeType, index: int) -> str:
        """Generate a deterministic node ID from role and index."""
        return f"{role.value}-{index:02d}"

    def _estimate_timeout(self, difficulty: DifficultyLevel, shape: GraphShape) -> int:
        """Estimate a reasonable timeout in seconds for the graph."""
        base = self._BASE_TIMEOUTS.get(difficulty, 120)
        multiplier = self._SHAPE_TIMEOUT_MULTIPLIERS.get(shape, 1.0)
        return int(base * multiplier)

    def _max_depth_for_shape(self, shape: GraphShape) -> int:
        """Return the max delegation depth for a given shape."""
        return {
            GraphShape.SINGLE_WORKER: 1,
            GraphShape.PLANNER_WORKER_CRITIC: 2,
            GraphShape.MULTI_TEAM: 3,
            GraphShape.RECURSIVE_DELEGATION: 5,
        }.get(shape, 2)

    def _determine_tools(self, node_role: NodeType, profile: ProfilePolicy) -> List[str]:
        """
        Determine the allowed tools for a node role using static profile allowlist.

        This method NEVER consults an LLM. It uses only the static profile allowlist.
        If the profile allowlist is empty, it returns a sensible default set based
        on the node role.
        """
        # Use profile allowlist if non-empty
        if profile.allowed_tools:
            # Filter to only tools that are allowed and not forbidden
            return [
                t for t in profile.allowed_tools
                if t not in profile.forbidden_tools
            ]

        # Default tool sets per role (when profile has no allowlist)
        defaults: Dict[NodeType, List[str]] = {
            NodeType.PLANNER: [
                "Read", "Write", "Grep", "Glob", "Bash",
            ],
            NodeType.RETRIEVER: [
                "Read", "Grep", "Glob", "WebFetch",
            ],
            NodeType.SCORER: [
                "Read", "Write",
            ],
            NodeType.ROUTER: [
                "Read",
            ],
            NodeType.CRITIC: [
                "Read", "Write", "Bash",
            ],
            NodeType.WORKER: [
                "Read", "Write", "Bash", "Glob", "Grep",
            ],
            NodeType.INTEGRATOR: [
                "Read", "Write", "Bash",
            ],
            NodeType.REVIEWER: [
                "Read", "Write", "Bash",
            ],
            NodeType.APPROVER: [
                "Read",
            ],
            NodeType.EXECUTOR: [
                "Read", "Write", "Bash",
            ],
        }
        return defaults.get(node_role, ["Read", "Write"])

    def _delegation_for_role(self, role: NodeType, depth: int) -> "DelegationPermission":
        """Determine delegation permission based on role and current depth."""
        if depth >= 3:
            return DelegationPermission.NONE
        if role in (NodeType.WORKER, NodeType.PLANNER):
            return DelegationPermission.APPROVE
        return DelegationPermission.NONE

    def _result_contract_for_role(self, role: NodeType) -> str:
        """Return a standard result contract description for a role."""
        contracts: Dict[NodeType, str] = {
            NodeType.PLANNER: "A structured decomposition of the objective into sub-tasks.",
            NodeType.RETRIEVER: "Retrieved context, facts, or documents relevant to the objective.",
            NodeType.SCORER: "A scored ranking of options with rationale.",
            NodeType.ROUTER: "A routing decision indicating the chosen path.",
            NodeType.CRITIC: "A critique identifying issues with suggested corrections.",
            NodeType.WORKER: "Completed output satisfying the task objective.",
            NodeType.INTEGRATOR: "A unified result combining all inputs coherently.",
            NodeType.REVIEWER: "A final validated output with sign-off confirmation.",
            NodeType.APPROVER: "An approval decision with any conditions noted.",
            NodeType.EXECUTOR: "Execution confirmation and any output artifacts.",
        }
        return contracts.get(role, "Task completed successfully.")

    def _verification_for_role(self, role: NodeType) -> str:
        """Return standard verification expectations for a role."""
        verifications: Dict[NodeType, str] = {
            NodeType.PLANNER: "Sub-tasks are specific, achievable, and cover the objective.",
            NodeType.RETRIEVER: "Retrieved information is relevant, accurate, and cited.",
            NodeType.SCORER: "Scores are consistent with stated criteria and rationale.",
            NodeType.ROUTER: "Routing decision is well-justified and policy-compliant.",
            NodeType.CRITIC: "Critique is specific, actionable, and identifies key issues.",
            NodeType.WORKER: "Output is correct, complete, and meets the objective.",
            NodeType.INTEGRATOR: "Integration is coherent, complete, and free of conflicts.",
            NodeType.REVIEWER: "Final output passes all quality gates and policy checks.",
            NodeType.APPROVER: "Approval is justified and all conditions are met.",
            NodeType.EXECUTOR: "Execution completed without errors and produced expected output.",
        }
        return verifications.get(role, "Output meets quality and policy standards.")


# ---------------------------------------------------------------------------
# parse_objective helper
# ---------------------------------------------------------------------------

# Re-export for convenience
from dynamic_schema import DelegationPermission  # noqa: E402, F401


def parse_objective(objective: str) -> Dict[str, Any]:
    """
    Extract structured signals from a natural-language objective.

    Args:
        objective: The raw objective string.

    Returns:
        A dictionary containing:
            - intent: The primary verb/action detected
            - target_domain: The domain area (code, research, data, etc.)
            - side_effect_signals: List of mentioned side effect keywords
            - delegation_signals: List of delegation/hierarchy keywords
            - risk_level: Estimated risk level (none/low/medium/high/extreme)
    """
    obj_lower = objective.lower()

    # Extract intent (first meaningful verb)
    words = objective.split()
    intent = words[0].lower() if words else ""

    # Determine target domain
    domain_patterns = {
        "code": ["code", "function", "class", "api", "script", "program", "software", "implement"],
        "research": ["research", "study", "investigate", "analyze", "survey", "review"],
        "data": ["data", "dataset", "csv", "sql", "database", "table", "record"],
        "research": ["research", "study", "investigate"],
        "file": ["file", "directory", "folder", "path", "read", "write"],
        "web": ["web", "http", "url", "site", "page", "fetch", "scrape"],
        "communication": ["email", "message", "send", "notify", "post"],
        "deployment": ["deploy", "server", "host", "cloud", "release"],
    }
    target_domain = "general"
    for domain, keywords in domain_patterns.items():
        if any(kw in obj_lower for kw in keywords):
            target_domain = domain
            break

    # Side effect signals
    side_effect_keywords = [
        "delete", "remove", "destroy", "drop", "create", "write", "update",
        "modify", "overwrite", "replace", "append", "insert",
        "deploy", "submit", "pay", "send", "post", "upload", "run", "exec",
        "install", "uninstall", "start", "stop", "kill", "cancel",
    ]
    side_effect_signals = [
        kw for kw in side_effect_keywords
        if kw in obj_lower
    ]

    # Delegation signals
    delegation_keywords = [
        "team", "delegate", "spawn", "child", "parent", "worker",
        "coordinator", "parallel", "concurrent", "split", "partition",
        "distribute", "escalate", "handoff", "subordinate",
    ]
    delegation_signals = [
        kw for kw in delegation_keywords
        if kw in obj_lower
    ]

    # Risk level estimation
    risk_keywords_high = ["delete", "deploy", "submit", "pay", "destroy", "drop", "kill"]
    risk_keywords_medium = ["update", "modify", "overwrite", "replace", "install", "run"]

    if any(kw in obj_lower for kw in risk_keywords_high):
        risk_level = "high"
    elif any(kw in obj_lower for kw in risk_keywords_medium):
        risk_level = "medium"
    elif side_effect_signals:
        risk_level = "low"
    else:
        risk_level = "none"

    return {
        "intent": intent,
        "target_domain": target_domain,
        "side_effect_signals": side_effect_signals,
        "delegation_signals": delegation_signals,
        "risk_level": risk_level,
    }
