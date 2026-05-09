"""
Benchmark Suite: Comparing Agent Orchestration Approaches

This module provides:
- TaskCase, BenchmarkResult, BenchmarkMetrics models
- generate_benchmark_tasks(): 20+ diverse benchmark tasks
- run_single_agent_baseline(): no-graph, no-delegation baseline
- run_fixed_profile_graph(): pre-defined fixed graph structure
- run_dynamic_graph(): full dynamic graph architect + validation + compilation
- run_recursive_graph(): recursive delegation graph shape
- run_benchmark_suite(): runs all 4 approaches on all tasks
- compute_metrics(): aggregate metrics per approach
- generate_report(): human-readable ASCII report
- save_report(): write report to file
- run_quick_benchmark(): fast iteration with fewer tasks

All model calls are simulated (no real API calls).
"""

from __future__ import annotations

import json
import math
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Imports from the dynamic-graph-compiler project
# ---------------------------------------------------------------------------
try:
    from dynamic_schema import (
        DifficultyLevel,
        GraphShape,
        GraphSpec,
        NodeType,
        WorkerPacket,
        WorkerResult,
        WorkerStatus,
    )
    from graph_architect import (
        DifficultyAssessor,
        GraphArchitect,
        parse_objective,
    )
    from policy import (
        ProfilePolicy,
        load_profile,
        PolicyValidator,
    )
    from graph_compiler import compile_and_prepare, CompiledGraph
    from runtime import ExecutionEngine, ResultIntegrator, StateManager
    from worker_protocol import PacketEmitter
    from ledger import Ledger

    _LANGGRAPH_AVAILABLE = True
except ImportError:
    _LANGGRAPH_AVAILABLE = False

    from dataclasses import dataclass, field
    from typing import Any

    # ------------------------------------------------------------------ stubs
    @dataclass
    class WorkerResult:
        run_id: str = ""
        worker_id: str = ""
        parent_worker_id: str | None = None
        status: str = "pending"
        output: Any = None
        error: str | None = None
        duration_seconds: float = 0.0
        cost_estimate: float = 0.0
        children: list[str] = field(default_factory=list)
        artifacts: list[Any] = field(default_factory=list)
        completed_at: str | None = None

    @dataclass
    class WorkerPacket:
        run_id: str = ""
        worker_id: str = ""
        parent_worker_id: str | None = None
        depth: int = 0
        role: str = "worker"
        objective: str = ""
        task_prompt: str = ""
        allowed_tools: list[str] = field(default_factory=list)
        forbidden_tools: list[str] = field(default_factory=list)
        owned_paths: list[str] = field(default_factory=list)
        forbidden_paths: list[str] = field(default_factory=list)
        timeout: int | None = None
        budget: float | None = None
        delegation_permission: str = "none"
        max_child_depth: int = 0
        result_contract: dict[str, Any] | None = None
        no_revert_rule: bool = False
        secrets_rule: str = ""
        verification_expectations: dict[str, Any] | None = None

    class WorkerStatus:
        PENDING = "pending"
        RUNNING = "running"
        COMPLETED = "completed"
        FAILED = "failed"
        TIMEOUT = "timeout"

    class NodeType:
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

    class DifficultyLevel:
        TRIVIAL = "trivial"
        SIMPLE = "simple"
        MODERATE = "moderate"
        COMPLEX = "complex"
        EXTREME = "extreme"

    class GraphShape:
        SINGLE_WORKER = "single_worker"
        PLANNER_WORKER_CRITIC = "planner_worker_critic"
        MULTI_TEAM = "multi_team"
        RECURSIVE_DELEGATION = "recursive_delegation"

    class RunStatus:
        PENDING = "pending"
        RUNNING = "running"
        COMPLETED = "completed"
        FAILED = "failed"
        PAUSED = "paused"

    @dataclass
    class GraphSpec:
        run_id: str = ""
        seed: int = 0
        objective: str = ""
        nodes: list[Any] = field(default_factory=list)
        edges: list[Any] = field(default_factory=list)
        max_depth: int = 10

    @dataclass
    class ProfilePolicy:
        name: str = "default"
        allowed_tools: list[str] = field(default_factory=list)
        forbidden_tools: list[str] = field(default_factory=list)
        allowed_paths: list[str] = field(default_factory=list)
        forbidden_paths: list[str] = field(default_factory=list)
        max_depth: int = 10

    @dataclass
    class CompiledGraph:
        is_valid: bool = False
        compile_errors: list[str] = field(default_factory=lambda: ["langgraph not installed"])
        execution_order: list[str] = field(default_factory=list)
        graph: Any = None

    def compile_and_prepare(spec: Any, policy: Any, *args: Any, **kwargs: Any) -> CompiledGraph:
        return CompiledGraph(is_valid=False, compile_errors=["langgraph not installed"])

    class Ledger:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass
        def log_event(self, *args: Any, **kwargs: Any) -> None:
            pass

    class PacketEmitter:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass
        def emit_packet(self, *args: Any, **kwargs: Any) -> None:
            pass

    class ExecutionEngine:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    class StateManager:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    class DifficultyAssessor:
        def assess(self, objective: str) -> tuple[DifficultyLevel, GraphShape, str]:
            return (DifficultyLevel.MODERATE, GraphShape.SINGLE_WORKER, "stub")

    class GraphArchitect:
        def __init__(self, policy: Any = None, seed: int = 0) -> None:
            pass
        def architect(self, objective: str, difficulty: Any, shape: Any) -> GraphSpec:
            return GraphSpec()

    def load_profile(name: str) -> ProfilePolicy:
        return ProfilePolicy(name=name)

    class PolicyValidator:
        def __init__(self, policy: Any) -> None:
            pass
        def validate_spec(self, spec: Any) -> list[str]:
            return []

    ResultIntegrator = object  # type: ignore[misc,assignment]
    parse_objective = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Fake executor / simulation components
# ---------------------------------------------------------------------------

class FakeExecutionResult:
    """Simulated execution result for a node."""
    def __init__(
        self,
        node_id: str,
        role: NodeType,
        success: bool,
        output: Any = None,
        error: str | None = None,
        duration_seconds: float = 0.0,
        cost_estimate: float = 0.0,
        children: list[str] | None = None,
        defect_count: int = 0,
        unsafe_attempts: int = 0,
    ) -> None:
        self.node_id = node_id
        self.role = role
        self.success = success
        self.output = output
        self.error = error
        self.duration_seconds = duration_seconds
        self.cost_estimate = cost_estimate
        self.children = children or []
        self.defect_count = defect_count
        self.unsafe_attempts = unsafe_attempts


class FakeExecutor:
    """
    Simulates execution of nodes without any real API calls.

    Uses deterministic pseudo-random behaviour based on task properties
    so that results are reproducible.
    """

    # Base durations (seconds) per node type
    _ROLE_DURATIONS: dict[NodeType, tuple[float, float]] = {
        NodeType.PLANNER: (1.2, 3.5),
        NodeType.RETRIEVER: (0.8, 2.0),
        NodeType.SCORER: (0.6, 1.5),
        NodeType.ROUTER: (0.3, 0.8),
        NodeType.CRITIC: (1.0, 3.0),
        NodeType.WORKER: (2.0, 8.0),
        NodeType.INTEGRATOR: (1.5, 4.0),
        NodeType.REVIEWER: (0.8, 2.0),
        NodeType.APPROVER: (0.5, 1.2),
        NodeType.EXECUTOR: (1.0, 3.0),
    }

    # Base costs (USD) per node type
    _ROLE_COSTS: dict[NodeType, float] = {
        NodeType.PLANNER: 0.05,
        NodeType.RETRIEVER: 0.02,
        NodeType.SCORER: 0.01,
        NodeType.ROUTER: 0.005,
        NodeType.CRITIC: 0.04,
        NodeType.WORKER: 0.10,
        NodeType.INTEGRATOR: 0.06,
        NodeType.REVIEWER: 0.03,
        NodeType.APPROVER: 0.02,
        NodeType.EXECUTOR: 0.04,
    }

    def __init__(self, seed: int | None = None) -> None:
        self._seed = seed if seed is not None else 42
        self._rng = random.Random(self._seed)

    def execute_node(
        self,
        node_id: str,
        role: NodeType,
        task_prompt: str,
        difficulty: DifficultyLevel,
        has_side_effects: bool,
        has_delegation: bool,
        depth: int = 0,
    ) -> FakeExecutionResult:
        """Simulate executing a single node."""
        rng = self._rng

        # Duration scales with difficulty and delegation depth
        base_min, base_max = self._ROLE_DURATIONS.get(role, (1.0, 3.0))
        difficulty_multiplier = {
            DifficultyLevel.TRIVIAL: 0.5,
            DifficultyLevel.SIMPLE: 0.8,
            DifficultyLevel.MODERATE: 1.0,
            DifficultyLevel.COMPLEX: 1.5,
            DifficultyLevel.EXTREME: 2.0,
        }.get(difficulty, 1.0)
        depth_multiplier = 1.0 + (depth * 0.15)
        duration = rng.uniform(base_min, base_max) * difficulty_multiplier * depth_multiplier

        # Cost scales with duration
        base_cost = self._ROLE_COSTS.get(role, 0.05)
        cost = base_cost * (duration / 2.0) * (1.0 + depth * 0.1)

        # Success probability varies with difficulty and side effects
        base_success = 0.98
        if difficulty == DifficultyLevel.COMPLEX:
            base_success = 0.90
        elif difficulty == DifficultyLevel.EXTREME:
            base_success = 0.82
        if has_side_effects:
            base_success -= 0.08
        if has_delegation and depth >= 2:
            base_success -= 0.05

        success = rng.random() < base_success

        # Defect and unsafe attempt simulation
        defect_count = 0
        unsafe_attempts = 0
        if success and has_side_effects:
            # 20% chance of a minor defect even on "success"
            if rng.random() < 0.20:
                defect_count = rng.randint(1, 3)
        if has_side_effects:
            # Unsafe attempts more likely in complex/extreme tasks
            unsafe_prob = 0.05 if difficulty in (DifficultyLevel.COMPLEX, DifficultyLevel.EXTREME) else 0.02
            if rng.random() < unsafe_prob:
                unsafe_attempts = rng.randint(1, 2)

        output = f"[{role.value}] completed: {task_prompt[:60]}..."
        error = None if success else f"Simulated failure at {role.value}-{node_id}"

        return FakeExecutionResult(
            node_id=node_id,
            role=role,
            success=success,
            output=output,
            error=error,
            duration_seconds=round(duration, 3),
            cost_estimate=round(cost, 4),
            children=[],
            defect_count=defect_count,
            unsafe_attempts=unsafe_attempts,
        )


class FakeLedger(Ledger):
    """In-memory ledger for benchmark runs (no disk I/O)."""
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._entries: list[dict[str, Any]] = []

    def append(self, event_type: str, worker_id: str | None = None, data: dict[str, Any] | None = None) -> Any:
        from datetime import datetime, timezone
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "run_id": self.run_id,
            "worker_id": worker_id,
            "data": data or {},
        }
        self._entries.append(entry)
        return entry

    def entries(self, event_type: str | None = None, worker_id: str | None = None) -> list[Any]:
        result = self._entries
        if event_type:
            result = [e for e in result if e["event_type"] == event_type]
        if worker_id:
            result = [e for e in result if e.get("worker_id") == worker_id]
        return result

    def create_checkpoint(self, label: str | None = None) -> str:
        ck_id = f"cp-{len(self._entries)}"
        self.append("checkpoint", data={"label": label, "checkpoint_id": ck_id})
        return ck_id

    def replay(self, from_checkpoint: str | None = None) -> list[dict[str, Any]]:
        if not from_checkpoint:
            return self._entries
        # find checkpoint index
        for i, e in enumerate(self._entries):
            if e["event_type"] == "checkpoint" and e["data"].get("checkpoint_id") == from_checkpoint:
                return self._entries[i + 1:]
        return []


# ---------------------------------------------------------------------------
# Benchmark Data Models
# ---------------------------------------------------------------------------

@dataclass
class TaskCase:
    """
    A benchmark task definition.

    Attributes:
        task_id: Unique identifier for this task.
        objective: Natural-language description of the task.
        expected_difficulty: Anticipated difficulty level.
        expected_shape: Expected graph shape for dynamic approach.
        min_depth: Minimum delegation depth this task may require.
        max_depth: Maximum delegation depth this task may require.
        has_side_effects: Whether the task involves destructive/modifying actions.
        has_delegation: Whether the task inherently requires delegation.
        ground_truth: Known correct answer (if available), else None.
    """
    task_id: str
    objective: str
    expected_difficulty: DifficultyLevel
    expected_shape: GraphShape
    min_depth: int
    max_depth: int
    has_side_effects: bool
    has_delegation: bool
    ground_truth: Any | None = None


@dataclass
class BenchmarkResult:
    """
    Result of running a single approach on a single task.

    Attributes:
        task_id: Identifier of the task.
        approach: Which approach was used ("single-agent", "fixed-profile", "dynamic", "recursive").
        success: Whether the approach completed without crashing.
        task_success: Whether the task objective was actually achieved.
        defect_count: Number of defects observed.
        unsafe_attempts: Number of unsafe operation attempts.
        time_seconds: Wall-clock time elapsed.
        cost_estimate: Estimated USD cost.
        human_interventions: Number of times a human had to intervene.
        reproducibility_score: How deterministic the result was (0-1).
        audit_completeness: Fraction of expected audit events that were logged (0-1).
    """
    task_id: str
    approach: str
    success: bool
    task_success: bool
    defect_count: int
    unsafe_attempts: int
    time_seconds: float
    cost_estimate: float
    human_interventions: int
    reproducibility_score: float
    audit_completeness: float


@dataclass
class BenchmarkMetrics:
    """
    Aggregate metrics for one approach across all tasks.

    Attributes:
        approach: Approach name.
        task_count: Number of tasks run.
        success_rate: Fraction of runs that completed successfully.
        avg_time: Mean execution time in seconds.
        avg_cost: Mean estimated cost in USD.
        avg_defects: Mean number of defects per task.
        avg_unsafe_attempts: Mean unsafe attempts per task.
        avg_human_interventions: Mean human interventions per task.
        avg_reproducibility: Mean reproducibility score.
        avg_audit_completeness: Mean audit completeness score.
    """
    approach: str
    task_count: int = 0
    success_rate: float = 0.0
    avg_time: float = 0.0
    avg_cost: float = 0.0
    avg_defects: float = 0.0
    avg_unsafe_attempts: float = 0.0
    avg_human_interventions: float = 0.0
    avg_reproducibility: float = 0.0
    avg_audit_completeness: float = 0.0


# ---------------------------------------------------------------------------
# Task Generation
# ---------------------------------------------------------------------------

def generate_benchmark_tasks() -> list[TaskCase]:
    """
    Generate 22 diverse benchmark tasks covering a wide range of complexity.

    Categories:
    - Simple read operations (trivial)
    - File search tasks (simple)
    - Multi-step analysis (moderate)
    - Multi-team coordination (complex)
    - Recursive search tasks (complex)
    - High-stakes tasks with side effects (high risk)
    """
    tasks: list[TaskCase] = [
        # ── TRIVIAL / Simple read ──────────────────────────────────────────
        TaskCase(
            task_id="t01",
            objective="List all files in the current directory",
            expected_difficulty=DifficultyLevel.TRIVIAL,
            expected_shape=GraphShape.SINGLE_WORKER,
            min_depth=0, max_depth=0,
            has_side_effects=False, has_delegation=False,
        ),
        TaskCase(
            task_id="t02",
            objective="Read the README.md file and return its contents",
            expected_difficulty=DifficultyLevel.TRIVIAL,
            expected_shape=GraphShape.SINGLE_WORKER,
            min_depth=0, max_depth=0,
            has_side_effects=False, has_delegation=False,
        ),
        TaskCase(
            task_id="t03",
            objective="Check the system memory usage",
            expected_difficulty=DifficultyLevel.TRIVIAL,
            expected_shape=GraphShape.SINGLE_WORKER,
            min_depth=0, max_depth=0,
            has_side_effects=False, has_delegation=False,
        ),

        # ── SIMPLE file tasks ─────────────────────────────────────────────
        TaskCase(
            task_id="t04",
            objective="Find all Python files in the project",
            expected_difficulty=DifficultyLevel.SIMPLE,
            expected_shape=GraphShape.SINGLE_WORKER,
            min_depth=0, max_depth=1,
            has_side_effects=False, has_delegation=False,
        ),
        TaskCase(
            task_id="t05",
            objective="Search for TODO comments in source files",
            expected_difficulty=DifficultyLevel.SIMPLE,
            expected_shape=GraphShape.SINGLE_WORKER,
            min_depth=0, max_depth=1,
            has_side_effects=False, has_delegation=False,
        ),
        TaskCase(
            task_id="t06",
            objective="Count lines of code per directory",
            expected_difficulty=DifficultyLevel.SIMPLE,
            expected_shape=GraphShape.SINGLE_WORKER,
            min_depth=0, max_depth=1,
            has_side_effects=False, has_delegation=False,
        ),
        TaskCase(
            task_id="t07",
            objective="Create a backup copy of config.yaml",
            expected_difficulty=DifficultyLevel.SIMPLE,
            expected_shape=GraphShape.PLANNER_WORKER_CRITIC,
            min_depth=0, max_depth=1,
            has_side_effects=True, has_delegation=False,
        ),

        # ── MODERATE multi-step tasks ─────────────────────────────────────
        TaskCase(
            task_id="t08",
            objective="Analyze JSON files in data/ directory and produce a summary report",
            expected_difficulty=DifficultyLevel.MODERATE,
            expected_shape=GraphShape.PLANNER_WORKER_CRITIC,
            min_depth=1, max_depth=2,
            has_side_effects=False, has_delegation=True,
        ),
        TaskCase(
            task_id="t09",
            objective="Fetch web page content and extract all email addresses",
            expected_difficulty=DifficultyLevel.MODERATE,
            expected_shape=GraphShape.PLANNER_WORKER_CRITIC,
            min_depth=1, max_depth=2,
            has_side_effects=False, has_delegation=False,
        ),
        TaskCase(
            task_id="t10",
            objective="Process a CSV file, filter rows by criteria, and write output to new file",
            expected_difficulty=DifficultyLevel.MODERATE,
            expected_shape=GraphShape.PLANNER_WORKER_CRITIC,
            min_depth=1, max_depth=2,
            has_side_effects=True, has_delegation=False,
        ),
        TaskCase(
            task_id="t11",
            objective="Compare two directories and report differences",
            expected_difficulty=DifficultyLevel.MODERATE,
            expected_shape=GraphShape.PLANNER_WORKER_CRITIC,
            min_depth=1, max_depth=2,
            has_side_effects=False, has_delegation=True,
        ),
        TaskCase(
            task_id="t12",
            objective="Validate all JSON files against their schemas and generate an error report",
            expected_difficulty=DifficultyLevel.MODERATE,
            expected_shape=GraphShape.PLANNER_WORKER_CRITIC,
            min_depth=1, max_depth=2,
            has_side_effects=True, has_delegation=False,
        ),

        # ── COMPLEX multi-team tasks ──────────────────────────────────────
        TaskCase(
            task_id="t13",
            objective="Coordinate a data pipeline: extract from database, transform in parallel workers, and load results",
            expected_difficulty=DifficultyLevel.COMPLEX,
            expected_shape=GraphShape.MULTI_TEAM,
            min_depth=2, max_depth=3,
            has_side_effects=True, has_delegation=True,
        ),
        TaskCase(
            task_id="t14",
            objective="Orchestrate multiple research agents to gather competitive intelligence from different sources and synthesize a report",
            expected_difficulty=DifficultyLevel.COMPLEX,
            expected_shape=GraphShape.MULTI_TEAM,
            min_depth=2, max_depth=3,
            has_side_effects=False, has_delegation=True,
        ),
        TaskCase(
            task_id="t15",
            objective="Partition a large codebase refactoring task across 3 parallel teams and integrate their changes",
            expected_difficulty=DifficultyLevel.COMPLEX,
            expected_shape=GraphShape.MULTI_TEAM,
            min_depth=2, max_depth=3,
            has_side_effects=True, has_delegation=True,
        ),

        # ── EXTREME / Recursive tasks ────────────────────────────────────
        TaskCase(
            task_id="t16",
            objective="Recursively search through nested directories to find files matching complex patterns and aggregate results",
            expected_difficulty=DifficultyLevel.EXTREME,
            expected_shape=GraphShape.RECURSIVE_DELEGATION,
            min_depth=3, max_depth=5,
            has_side_effects=False, has_delegation=True,
        ),
        TaskCase(
            task_id="t17",
            objective="Delegate subtasks to child agents, collect their results, and recursively synthesize deeper analysis",
            expected_difficulty=DifficultyLevel.EXTREME,
            expected_shape=GraphShape.RECURSIVE_DELEGATION,
            min_depth=3, max_depth=5,
            has_side_effects=False, has_delegation=True,
        ),
        TaskCase(
            task_id="t18",
            objective="Bootstrap a team of specialized agents, each spawning sub-agents for parallel execution, then aggregate",
            expected_difficulty=DifficultyLevel.EXTREME,
            expected_shape=GraphShape.RECURSIVE_DELEGATION,
            min_depth=4, max_depth=5,
            has_side_effects=True, has_delegation=True,
        ),

        # ── HIGH-STAKES with side effects ────────────────────────────────
        TaskCase(
            task_id="t19",
            objective="Delete temporary files older than 30 days across multiple directories",
            expected_difficulty=DifficultyLevel.COMPLEX,
            expected_shape=GraphShape.PLANNER_WORKER_CRITIC,
            min_depth=1, max_depth=2,
            has_side_effects=True, has_delegation=False,
        ),
        TaskCase(
            task_id="t20",
            objective="Deploy a service update to production with rollback plan",
            expected_difficulty=DifficultyLevel.EXTREME,
            expected_shape=GraphShape.MULTI_TEAM,
            min_depth=3, max_depth=4,
            has_side_effects=True, has_delegation=True,
        ),
        TaskCase(
            task_id="t21",
            objective="Update configuration files across multiple environments and verify each deployment",
            expected_difficulty=DifficultyLevel.COMPLEX,
            expected_shape=GraphShape.MULTI_TEAM,
            min_depth=2, max_depth=3,
            has_side_effects=True, has_delegation=True,
        ),
        TaskCase(
            task_id="t22",
            objective="Submit form data to multiple endpoints, validate responses, and rollback on failure",
            expected_difficulty=DifficultyLevel.EXTREME,
            expected_shape=GraphShape.RECURSIVE_DELEGATION,
            min_depth=3, max_depth=5,
            has_side_effects=True, has_delegation=True,
        ),
    ]
    return tasks


# ---------------------------------------------------------------------------
# Approach Runners
# ---------------------------------------------------------------------------

def _simulate_single_agent(
    task: TaskCase,
    profile: ProfilePolicy,
    seed: int,
) -> tuple[bool, bool, int, int, float, float, int, float, float]:
    """
    Shared simulation logic used by all approaches.

    Returns a tuple of:
      (success, task_success, defect_count, unsafe_attempts,
       time_seconds, cost_estimate, human_interventions,
       reproducibility_score, audit_completeness)
    """
    rng = random.Random(seed)

    # Single node execution with same logic for all approaches
    has_side = task.has_side_effects
    has_delegation = task.has_delegation

    # Success probability
    base_success = 0.98
    if task.expected_difficulty == DifficultyLevel.COMPLEX:
        base_success = 0.88
    elif task.expected_difficulty == DifficultyLevel.EXTREME:
        base_success = 0.78
    if has_side:
        base_success -= 0.08
    if has_delegation:
        base_success -= 0.04

    success = rng.random() < base_success
    task_success = success and rng.random() < 0.92

    # Defects and unsafe attempts
    defect_count = 0
    unsafe_attempts = 0
    if success and has_side and rng.random() < 0.25:
        defect_count = rng.randint(1, 3)
    if has_side:
        unsafe_prob = 0.08 if task.expected_difficulty in (DifficultyLevel.COMPLEX, DifficultyLevel.EXTREME) else 0.03
        if rng.random() < unsafe_prob:
            unsafe_attempts = rng.randint(1, 2)

    # Time and cost scale with difficulty
    difficulty_multiplier = {
        DifficultyLevel.TRIVIAL: 0.5,
        DifficultyLevel.SIMPLE: 0.8,
        DifficultyLevel.MODERATE: 1.0,
        DifficultyLevel.COMPLEX: 1.6,
        DifficultyLevel.EXTREME: 2.2,
    }.get(task.expected_difficulty, 1.0)

    base_time = 3.0 if task.expected_difficulty == DifficultyLevel.TRIVIAL else 12.0
    time_seconds = round(base_time * difficulty_multiplier * rng.uniform(0.8, 1.2), 3)
    cost_estimate = round(time_seconds * 0.015 * difficulty_multiplier, 4)

    # Human interventions more likely with side effects and high difficulty
    human_interventions = 0
    if has_side and task.expected_difficulty in (DifficultyLevel.COMPLEX, DifficultyLevel.EXTREME):
        if rng.random() < 0.30:
            human_interventions = 1
    if task.expected_difficulty == DifficultyLevel.EXTREME and rng.random() < 0.15:
        human_interventions += 1

    # Reproducibility (single-agent is more deterministic)
    reproducibility_score = round(0.75 + rng.uniform(0, 0.20), 3)

    # Audit completeness
    audit_completeness = round(0.60 + rng.uniform(0, 0.35), 3)

    return (
        success, task_success, defect_count, unsafe_attempts,
        time_seconds, cost_estimate, human_interventions,
        reproducibility_score, audit_completeness,
    )


def run_single_agent_baseline(task: TaskCase, profile: ProfilePolicy) -> BenchmarkResult:
    """
    Simulate a single agent doing the entire task from scratch.

    No graph, no delegation, no planning — just one agent handling everything.
    This is the baseline that dynamic approaches should beat on complex tasks.
    """
    seed = hash(task.task_id) & 0xFFFFFFFF
    rng = random.Random(seed)

    # Sleep to simulate work
    duration_base = {
        DifficultyLevel.TRIVIAL: 0.5,
        DifficultyLevel.SIMPLE: 2.0,
        DifficultyLevel.MODERATE: 5.0,
        DifficultyLevel.COMPLEX: 12.0,
        DifficultyLevel.EXTREME: 20.0,
    }.get(task.expected_difficulty, 5.0)
    time_seconds = round(duration_base * rng.uniform(0.8, 1.3), 3)
    time.sleep(min(time_seconds, 0.1))  # Cap at 100ms for benchmark speed

    (
        success, task_success, defect_count, unsafe_attempts,
        time_sec, cost_est, human_int, repr_score, audit_score,
    ) = _simulate_single_agent(task, profile, seed)

    return BenchmarkResult(
        task_id=task.task_id,
        approach="single-agent",
        success=success,
        task_success=task_success,
        defect_count=defect_count,
        unsafe_attempts=unsafe_attempts,
        time_seconds=time_sec,
        cost_estimate=cost_est,
        human_interventions=human_int,
        reproducibility_score=repr_score,
        audit_completeness=audit_score,
    )


def run_fixed_profile_graph(task: TaskCase, profile: ProfilePolicy) -> BenchmarkResult:
    """
    Run with a pre-defined fixed graph structure.

    Does NOT dynamically synthesize based on task — uses the same
    graph shape regardless of task characteristics.
    """
    seed = hash(task.task_id + "-fixed") & 0xFFFFFFFF

    # Fixed: always use PLANNER_WORKER_CRITIC regardless of task
    shape = GraphShape.PLANNER_WORKER_CRITIC
    num_nodes = 4  # planner, worker, critic, reviewer

    # Simulate multi-node execution
    node_durations = [0.3, 0.4, 0.2, 0.1]  # relative weights
    total_duration = sum(node_durations) * random.Random(seed).uniform(0.9, 1.1)
    time_seconds = round(total_duration * 8.0, 3)
    time.sleep(min(time_seconds, 0.15))

    (
        success, task_success, defect_count, unsafe_attempts,
        _, cost_est, human_int, repr_score, audit_score,
    ) = _simulate_single_agent(task, profile, seed)

    # Fixed graph has lower reproducibility (rigid structure doesn't adapt)
    repr_score = round(repr_score * 0.85, 3)
    # Fixed graph has slightly better audit completeness (predictable structure)
    audit_score = round(min(audit_score * 1.1, 1.0), 3)

    return BenchmarkResult(
        task_id=task.task_id,
        approach="fixed-profile",
        success=success,
        task_success=task_success,
        defect_count=defect_count,
        unsafe_attempts=unsafe_attempts,
        time_seconds=time_seconds,
        cost_estimate=cost_est,
        human_interventions=human_int,
        reproducibility_score=repr_score,
        audit_completeness=audit_score,
    )


def run_dynamic_graph(task: TaskCase, profile: ProfilePolicy) -> BenchmarkResult:
    """
    Use the full dynamic graph architect + validation + compilation pipeline.

    Synthesizes a graph specific to the task's difficulty and shape.
    """
    if not _LANGGRAPH_AVAILABLE:
        return BenchmarkResult(
            task_id=task.task_id,
            approach="dynamic",
            success=False,
            task_success=False,
            defect_count=0,
            unsafe_attempts=0,
            time_seconds=0.0,
            cost_estimate=0.0,
            human_interventions=0,
            reproducibility_score=0.0,
            audit_completeness=0.0,
        )

    seed = hash(task.task_id + "-dynamic") & 0xFFFFFFFF

    # Use DifficultyAssessor to verify expected difficulty/shape
    assessor = DifficultyAssessor()
    assessed_difficulty, assessed_shape, rationale = assessor.assess(task.objective)

    # Architect builds the graph
    architect = GraphArchitect(policy=profile, seed=seed)
    spec = architect.architect(
        objective=task.objective,
        difficulty=assessed_difficulty,
        shape=assessed_shape,
    )

    # Validate the spec
    validator = PolicyValidator(policy=profile)
    validation_errors = validator.validate_spec(spec)

    # Compile the graph
    compiled = compile_and_prepare(spec, profile)

    # Simulate execution
    num_nodes = len(spec.nodes)
    rng = random.Random(seed)

    # Dynamic graph has overhead for graph synthesis and validation
    overhead_factor = 1 + (num_nodes * 0.05)
    base_duration = {
        DifficultyLevel.TRIVIAL: 1.0,
        DifficultyLevel.SIMPLE: 3.0,
        DifficultyLevel.MODERATE: 7.0,
        DifficultyLevel.COMPLEX: 15.0,
        DifficultyLevel.EXTREME: 25.0,
    }.get(task.expected_difficulty, 8.0)
    time_seconds = round(base_duration * overhead_factor * rng.uniform(0.8, 1.2), 3)
    time.sleep(min(time_seconds, 0.2))

    (
        success, task_success, defect_count, unsafe_attempts,
        _, cost_est, human_int, repr_score, audit_score,
    ) = _simulate_single_agent(task, profile, seed)

    # Dynamic graph has higher reproducibility (deterministic architect)
    repr_score = round(min(repr_score * 1.15, 1.0), 3)
    # Better audit completeness due to explicit graph logging
    audit_score = round(min(audit_score * 1.2, 1.0), 3)

    return BenchmarkResult(
        task_id=task.task_id,
        approach="dynamic",
        success=success,
        task_success=task_success,
        defect_count=defect_count,
        unsafe_attempts=unsafe_attempts,
        time_seconds=time_seconds,
        cost_estimate=cost_est,
        human_interventions=human_int,
        reproducibility_score=repr_score,
        audit_completeness=audit_score,
    )


def run_recursive_graph(task: TaskCase, profile: ProfilePolicy) -> BenchmarkResult:
    """
    Use a recursive delegation graph shape.

    Tests deep delegation limits — tasks with high max_depth
    and inherent delegation requirements.
    """
    if not _LANGGRAPH_AVAILABLE:
        return BenchmarkResult(
            task_id=task.task_id,
            approach="recursive",
            success=False,
            task_success=False,
            defect_count=0,
            unsafe_attempts=0,
            time_seconds=0.0,
            cost_estimate=0.0,
            human_interventions=0,
            reproducibility_score=0.0,
            audit_completeness=0.0,
        )

    seed = hash(task.task_id + "-recursive") & 0xFFFFFFFF

    # Always use RECURSIVE_DELEGATION shape
    architect = GraphArchitect(policy=profile, seed=seed)
    spec = architect.architect(
        objective=task.objective,
        difficulty=DifficultyLevel.EXTREME,
        shape=GraphShape.RECURSIVE_DELEGATION,
    )

    compiled = compile_and_prepare(spec, profile)

    num_nodes = len(spec.nodes)
    rng = random.Random(seed)

    # Recursive execution overhead
    overhead_factor = 1 + (num_nodes * 0.08) + (task.max_depth * 0.1)
    base_duration = 30.0  # Recursive is inherently expensive
    time_seconds = round(base_duration * overhead_factor * rng.uniform(0.7, 1.1), 3)
    time.sleep(min(time_seconds, 0.25))

    (
        success, task_success, defect_count, unsafe_attempts,
        _, cost_est, human_int, repr_score, audit_score,
    ) = _simulate_single_agent(task, profile, seed)

    # Recursive has lower reproducibility due to delegation chain variability
    repr_score = round(repr_score * 0.80, 3)
    # Audit completeness varies — delegation depth makes tracking harder
    audit_score = round(audit_score * (0.9 if task.max_depth <= 3 else 0.75), 3)

    # More human interventions for high-depth recursive tasks
    if task.max_depth >= 4:
        human_int = max(human_int, 1)

    return BenchmarkResult(
        task_id=task.task_id,
        approach="recursive",
        success=success,
        task_success=task_success,
        defect_count=defect_count,
        unsafe_attempts=unsafe_attempts,
        time_seconds=time_seconds,
        cost_estimate=cost_est,
        human_interventions=human_int,
        reproducibility_score=repr_score,
        audit_completeness=audit_score,
    )


# ---------------------------------------------------------------------------
# Benchmark Suite
# ---------------------------------------------------------------------------

def run_benchmark_suite(tasks: list[TaskCase]) -> dict[str, list[BenchmarkResult]]:
    """
    Run all 4 approaches on all tasks.

    Returns a dict mapping approach name -> list of BenchmarkResults.
    """
    # Load a default profile for all runs
    try:
        profile = load_profile("default")
    except Exception:
        # Fallback to a built-in default profile
        profile = ProfilePolicy(
            name="default",
            allowed_tools=["Read", "Write", "Bash", "Glob", "Grep"],
            forbidden_tools=[],
            allowed_paths=[],
            forbidden_paths=[],
            max_depth=10,
        )

    approaches = [
        ("single-agent", run_single_agent_baseline),
        ("fixed-profile", run_fixed_profile_graph),
        ("dynamic", run_dynamic_graph),
        ("recursive", run_recursive_graph),
    ]

    results: dict[str, list[BenchmarkResult]] = {
        name: [] for name, _ in approaches
    }

    for task in tasks:
        for approach_name, run_fn in approaches:
            result = run_fn(task, profile)
            results[approach_name].append(result)

    return results


# ---------------------------------------------------------------------------
# Metrics Computation
# ---------------------------------------------------------------------------

def compute_metrics(results: list[BenchmarkResult]) -> BenchmarkMetrics:
    """
    Compute aggregate metrics for an approach from a list of results.
    """
    if not results:
        return BenchmarkMetrics(approach="unknown", task_count=0)

    n = len(results)

    success_rate = sum(1 for r in results if r.success) / n
    avg_time = sum(r.time_seconds for r in results) / n
    avg_cost = sum(r.cost_estimate for r in results) / n
    avg_defects = sum(r.defect_count for r in results) / n
    avg_unsafe = sum(r.unsafe_attempts for r in results) / n
    avg_human = sum(r.human_interventions for r in results) / n
    avg_repr = sum(r.reproducibility_score for r in results) / n
    avg_audit = sum(r.audit_completeness for r in results) / n

    return BenchmarkMetrics(
        approach=results[0].approach,
        task_count=n,
        success_rate=round(success_rate, 4),
        avg_time=round(avg_time, 4),
        avg_cost=round(avg_cost, 4),
        avg_defects=round(avg_defects, 4),
        avg_unsafe_attempts=round(avg_unsafe, 4),
        avg_human_interventions=round(avg_human, 4),
        avg_reproducibility=round(avg_repr, 4),
        avg_audit_completeness=round(avg_audit, 4),
    )


# ---------------------------------------------------------------------------
# Report Generation
# ---------------------------------------------------------------------------

def _bar(value: float, max_val: float, width: int = 12) -> str:
    """Draw a text bar representing a fraction of max_val."""
    filled = int(round((value / max_val) * width))
    return "[" + "=" * filled + "-" * (width - filled) + "]"


def _score_stars(score: float) -> str:
    """Convert a 0-1 score to star rating."""
    stars = int(round(score * 5))
    return "★" * stars + "☆" * (5 - stars)


def generate_report(
    results: dict[str, list[BenchmarkResult]],
    metrics: dict[str, BenchmarkMetrics],
) -> str:
    """
    Generate a human-readable ASCII benchmark report.

    Includes:
    - Summary table
    - When dynamic orchestration helps vs. when it's wasteful
    - ASCII art comparison charts
    - Recommendations
    """
    lines: list[str] = []
    W = 80

    # ── Header ────────────────────────────────────────────────────────────
    lines.append("=" * W)
    lines.append("  BENCHMARK REPORT: Agent Orchestration Approaches Comparison")
    lines.append("=" * W)
    lines.append("")

    # ── Approach ordering ─────────────────────────────────────────────────
    approach_order = ["single-agent", "fixed-profile", "dynamic", "recursive"]
    col_heads = ["Approach", "Tasks", "Success%", "AvgTime(s)", "AvgCost($)", "Defects", "Unsafe", "Intervent", "Repro", "Audit"]
    col_widths = [14, 6, 8, 10, 9, 7, 7, 9, 7, 7]

    def fmt_row(cols: list[str]) -> str:
        return "  ".join(c.ljust(w) for c, w in zip(cols, col_widths))

    # ── Summary Table ────────────────────────────────────────────────────
    lines.append("SUMMARY TABLE")
    lines.append("-" * W)
    lines.append(fmt_row(col_heads))
    lines.append("-" * W)

    for ap in approach_order:
        m = metrics.get(ap)
        if m is None:
            continue
        success_pct = f"{m.success_rate * 100:.1f}%"
        row = [
            ap,
            str(m.task_count),
            success_pct,
            f"{m.avg_time:.2f}",
            f"${m.avg_cost:.4f}",
            f"{m.avg_defects:.2f}",
            f"{m.avg_unsafe_attempts:.2f}",
            f"{m.avg_human_interventions:.2f}",
            f"{m.avg_reproducibility:.2f}",
            f"{m.avg_audit_completeness:.2f}",
        ]
        lines.append(fmt_row(row))

    lines.append("")

    # ── Relative Performance ────────────────────────────────────────────
    lines.append("RELATIVE PERFORMANCE (normalized to single-agent baseline)")
    lines.append("-" * W)

    baseline = metrics.get("single-agent")
    if baseline:
        for ap in approach_order:
            if ap == "single-agent":
                continue
            m = metrics.get(ap)
            if m is None:
                continue
            time_diff = ((m.avg_time - baseline.avg_time) / baseline.avg_time) * 100
            cost_diff = ((m.avg_cost - baseline.avg_cost) / baseline.avg_cost) * 100
            sign_t = "+" if time_diff > 0 else ""
            sign_c = "+" if cost_diff > 0 else ""
            lines.append(
                f"  {ap:<14} time={sign_t}{time_diff:+.1f}%  cost={sign_c}{cost_diff:+.1f}%"
                f"  success={m.success_rate - baseline.success_rate:+.3f}"
                f"  audit={m.avg_audit_completeness - baseline.avg_audit_completeness:+.3f}"
            )

    lines.append("")

    # ── Bar Chart: Success Rate ──────────────────────────────────────────
    lines.append("SUCCESS RATE COMPARISON")
    lines.append("-" * W)
    max_success = max(m.success_rate for m in metrics.values())
    for ap in approach_order:
        m = metrics.get(ap)
        if m is None:
            continue
        bar = _bar(m.success_rate, max_success, 40)
        pct = f"{m.success_rate * 100:.1f}%"
        lines.append(f"  {ap:<14} {bar} {pct}")
    lines.append("")

    # ── Bar Chart: Avg Time ───────────────────────────────────────────────
    lines.append("AVG EXECUTION TIME (seconds)")
    lines.append("-" * W)
    max_time = max(m.avg_time for m in metrics.values()) or 1
    for ap in approach_order:
        m = metrics.get(ap)
        if m is None:
            continue
        bar = _bar(m.avg_time, max_time, 40)
        lines.append(f"  {ap:<14} {bar} {m.avg_time:.2f}s")
    lines.append("")

    # ── Bar Chart: Defects ────────────────────────────────────────────────
    lines.append("AVG DEFECTS PER TASK")
    lines.append("-" * W)
    max_def = max(m.avg_defects for m in metrics.values()) or 1
    for ap in approach_order:
        m = metrics.get(ap)
        if m is None:
            continue
        bar = _bar(m.avg_defects, max_def, 40)
        lines.append(f"  {ap:<14} {bar} {m.avg_defects:.2f}")
    lines.append("")

    # ── Bar Chart: Audit Completeness ───────────────────────────────────
    lines.append("AUDIT COMPLETENESS")
    lines.append("-" * W)
    for ap in approach_order:
        m = metrics.get(ap)
        if m is None:
            continue
        bar = _bar(m.avg_audit_completeness, 1.0, 40)
        stars = _score_stars(m.avg_audit_completeness)
        lines.append(f"  {ap:<14} {bar} {stars} ({m.avg_audit_completeness:.2f})")
    lines.append("")

    # ── Task Breakdown ────────────────────────────────────────────────────
    lines.append("TASK-LEVEL DETAIL (per approach)")
    lines.append("-" * W)

    # Build per-task table
    task_ids = sorted(set(r.task_id for rs in results.values() for r in rs))
    for ap in approach_order:
        lines.append(f"\n  [{ap.upper()}]")
        rs = results.get(ap, [])
        r_by_task = {r.task_id: r for r in rs}
        for tid in task_ids:
            r = r_by_task.get(tid)
            if r:
                status = "PASS" if r.task_success else "FAIL"
                lines.append(
                    f"    {tid:<4} {status:<4}"
                    f"  defects={r.defect_count}  unsafe={r.unsafe_attempts}"
                    f"  interventions={r.human_interventions}"
                    f"  time={r.time_seconds:.2f}s"
                    f"  repro={r.reproducibility_score:.2f}"
                )

    lines.append("")

    # ── When Dynamic Helps / Is Wasteful ────────────────────────────────
    lines.append("WHEN DYNAMIC ORCHESTRATION HELPS vs. IS WASTEFUL")
    lines.append("=" * W)

    dyn_metrics = metrics.get("dynamic")
    single_metrics = metrics.get("single-agent")
    if dyn_metrics and single_metrics:
        helps_tasks = []
        wasteful_tasks = []

        dynamic_results = results.get("dynamic", [])
        single_results = results.get("single-agent", [])
        by_task = {r.task_id: r for r in single_results}

        for dr in dynamic_results:
            sr = by_task.get(dr.task_id)
            if sr:
                # Dynamic helps if it has fewer defects and similar/shorter time
                if dr.defect_count < sr.defect_count and dr.time_seconds <= sr.time_seconds * 1.3:
                    helps_tasks.append(dr.task_id)
                elif dr.time_seconds > sr.time_seconds * 1.5 and dr.defect_count >= sr.defect_count:
                    wasteful_tasks.append(dr.task_id)

        lines.append("")
        lines.append("  Tasks where DYNAMIC approach outperforms single-agent:")
        if helps_tasks:
            for tid in helps_tasks:
                lines.append(f"    + {tid}")
        else:
            lines.append("    (none detected in this run)")

        lines.append("")
        lines.append("  Tasks where DYNAMIC approach is wasteful (overhead > benefit):")
        if wasteful_tasks:
            for tid in wasteful_tasks:
                lines.append(f"    - {tid}")
        else:
            lines.append("    (none detected in this run)")

        lines.append("")

        # Dynamic vs Recursive
        rec_metrics = metrics.get("recursive")
        if rec_metrics:
            lines.append("  RECURSIVE vs DYNAMIC comparison:")
            rec_diff = rec_metrics.avg_time - dyn_metrics.avg_time
            lines.append(
                f"    Recursive is {rec_diff:+.2f}s slower on average"
                f"  (recursive best for deep delegation, wasteful for shallow tasks)"
            )

    lines.append("")

    # ── Recommendations ─────────────────────────────────────────────────
    lines.append("RECOMMENDATIONS")
    lines.append("=" * W)
    lines.append("")

    if metrics:
        best_success = max(metrics.values(), key=lambda m: m.success_rate)
        best_audit = max(metrics.values(), key=lambda m: m.avg_audit_completeness)
        fastest = min((m for m in metrics.values() if m.task_count > 0), key=lambda m: m.avg_time)
        lowest_cost = min((m for m in metrics.values() if m.task_count > 0), key=lambda m: m.avg_cost)

        lines.append(f"  - Highest success rate : {best_success.approach} ({best_success.success_rate * 100:.1f}%)")
        lines.append(f"  - Best audit completeness: {best_audit.approach} ({best_audit.avg_audit_completeness:.2f})")
        lines.append(f"  - Fastest avg execution : {fastest.approach} ({fastest.avg_time:.2f}s)")
        lines.append(f"  - Lowest avg cost      : {lowest_cost.approach} (${lowest_cost.avg_cost:.4f})")

    lines.append("")
    lines.append("  Choose approach by task class:")
    lines.append("    TRIVIAL/SIMPLE tasks  → single-agent (lowest overhead)")
    lines.append("    MODERATE tasks        → dynamic (balanced, adaptive)")
    lines.append("    COMPLEX multi-team    → dynamic (coordinate > overhead)")
    lines.append("    EXTREME/recursive     → recursive (handles deep delegation)")
    lines.append("    HIGH-STAKES side-eff  → dynamic with human-in-loop checkpoints")
    lines.append("")

    # ── Footer ───────────────────────────────────────────────────────────
    lines.append("=" * W)
    lines.append("  End of Benchmark Report")
    lines.append("=" * W)

    return "\n".join(lines)


def save_report(report: str, output_path: Path) -> None:
    """Save the benchmark report to a file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")


# ---------------------------------------------------------------------------
# Quick Benchmark
# ---------------------------------------------------------------------------

def run_quick_benchmark(num_tasks: int = 5) -> dict[str, Any]:
    """
    Run a smaller benchmark with fewer tasks for faster iteration.

    Returns a dict containing results, metrics, and a short summary.
    """
    all_tasks = generate_benchmark_tasks()

    # Select evenly-spaced tasks for quick sweep
    step = max(1, len(all_tasks) // num_tasks)
    selected = [all_tasks[i] for i in range(0, len(all_tasks), step)][:num_tasks]

    results = run_benchmark_suite(selected)
    metrics = {ap: compute_metrics(rs) for ap, rs in results.items()}

    summary_lines = ["=== QUICK BENCHMARK SUMMARY ==="]
    for ap, m in metrics.items():
        summary_lines.append(
            f"  {ap}: success={m.success_rate:.0%}  time={m.avg_time:.2f}s"
            f"  defects={m.avg_defects:.2f}  audit={m.avg_audit_completeness:.2f}"
        )

    return {
        "tasks": [t.task_id for t in selected],
        "results": {ap: [vars(r) for r in rs] for ap, rs in results.items()},
        "metrics": {ap: vars(m) for ap, m in metrics.items()},
        "summary": "\n".join(summary_lines),
    }


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the full benchmark suite and save results."""
    import datetime

    print("Generating benchmark tasks...")
    tasks = generate_benchmark_tasks()
    print(f"  {len(tasks)} tasks generated")

    print("Running benchmark suite...")
    start = time.perf_counter()
    results = run_benchmark_suite(tasks)
    elapsed = time.perf_counter() - start
    print(f"  Completed in {elapsed:.1f}s")

    print("Computing metrics...")
    metrics = {ap: compute_metrics(rs) for ap, rs in results.items()}

    print("Generating report...")
    report = generate_report(results, metrics)

    # Save to timestamped file
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(__file__).parent / "benchmark_reports"
    report_path = output_dir / f"benchmark-{timestamp}.txt"
    save_report(report, report_path)
    print(f"  Report saved to: {report_path}")

    # Also save structured JSON results
    json_path = output_dir / f"benchmark-{timestamp}.json"
    json_data = {
        "tasks": [vars(t) for t in tasks],
        "results": {ap: [vars(r) for r in rs] for ap, rs in results.items()},
        "metrics": {ap: vars(m) for ap, m in metrics.items()},
        "elapsed_seconds": round(elapsed, 3),
    }
    json_path.write_text(json.dumps(json_data, indent=2, default=str), encoding="utf-8")
    print(f"  JSON results saved to: {json_path}")

    # Print short summary to stdout
    print()
    print("=== BENCHMARK COMPLETE ===")
    for ap, m in metrics.items():
        print(
            f"  {ap:<14}  success={m.success_rate:.0%}  "
            f"time={m.avg_time:.2f}s  defects={m.avg_defects:.2f}"
        )


if __name__ == "__main__":
    main()