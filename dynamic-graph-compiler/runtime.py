"""
Runtime execution engine for the Dynamic Graph Compiler.

This module provides the ExecutionEngine that dispatches workers, handles
delegation, and integrates results, along with ResultIntegrator, StateManager,
and supporting infrastructure.

Imports:
    dynamic_schema: GraphSpec, WorkerPacket, WorkerResult, RunStatus, NodeType
    policy: ProfilePolicy, PolicyValidator
    worker_protocol: PacketEmitter, PacketBuilder
    ledger: Ledger
    graph_compiler: GraphCompiler, RunState
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

# ---------------------------------------------------------------------------
# Optional imports — fall back to stubs if modules are not yet available
# ---------------------------------------------------------------------------

try:
    from dynamic_schema import (
        GraphSpec,
        WorkerPacket,
        WorkerResult,
        RunStatus,
        NodeType,
        DelegationPermission,
        WorkerStatus,
    )
except ImportError:
    # Stub types for bootstrap ordering
    class RunStatus(str, Enum):
        RUNNING = "running"
        PAUSED = "paused"
        COMPLETED = "completed"
        FAILED = "failed"
        CANCELLED = "cancelled"

    class NodeType(str, Enum):
        PLANNER = "planner"
        WORKER = "worker"
        CRITIC = "critic"
        INTEGRATOR = "integrator"
        REVIEWER = "reviewer"
        RETRIEVER = "retriever"
        SCORER = "scorer"
        ROUTER = "router"
        APPROVER = "approver"
        EXECUTOR = "executor"

    class DelegationPermission(str, Enum):
        NONE = "none"
        REQUEST = "request"
        APPROVE = "approve"

    class WorkerStatus(str, Enum):
        SUCCESS = "success"
        FAILURE = "failure"
        TIMEOUT = "timeout"
        REJECTED = "rejected"
        PENDING = "pending"

    class GraphSpec:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    class WorkerPacket:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    class WorkerResult:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

try:
    from policy import ProfilePolicy, PolicyValidator
except ImportError:
    class ProfilePolicy:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    class PolicyValidator:  # type: ignore[no-redef]
        def __init__(self, policy: ProfilePolicy) -> None:
            self.policy = policy

try:
    from worker_protocol import PacketEmitter, PacketBuilder
except ImportError:
    class PacketEmitter:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            pass

        def emit(self, packet: Any) -> str:
            return ""

    class PacketBuilder:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            pass

try:
    from ledger import Ledger
except ImportError:
    class Ledger:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            pass

        def log_event(self, **kwargs: Any) -> None:
            pass

        def log_worker_result(self, **kwargs: Any) -> None:
            pass

try:
    from graph_compiler import GraphCompiler, RunState
except ImportError:
    class RunState:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    class GraphCompiler:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            pass


# ---------------------------------------------------------------------------
# Runtime Status
# ---------------------------------------------------------------------------

class RuntimeStatus(str, Enum):
    """Runtime execution status constants."""
    READY = "ready"
    RUNNING = "running"
    PAUSED_AT_GATE = "paused_at_gate"
    PAUSED_AT_CHECKPOINT = "paused_at_checkpoint"
    COMPLETED = "completed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# ExecutionEngine
# ---------------------------------------------------------------------------

class ExecutionEngine:
    """
    Main execution engine that dispatches workers, handles delegation,
    and integrates results.

    The engine runs the compiled LangGraph until a terminal state or human gate,
    emitting packets for each worker node, validating delegation requests,
    and recording results to the ledger.

    Attributes:
        compiled_graph: The compiled graph ready for execution.
        emitter: PacketEmitter for dispatching worker packets.
        ledger: Ledger for audit logging.
        validator: PolicyValidator for delegation validation.
        profile: ProfilePolicy providing tool/path constraints.
    """

    def __init__(
        self,
        compiled_graph: Any,
        emitter: PacketEmitter,
        ledger: Ledger,
        validator: PolicyValidator,
        profile: ProfilePolicy,
    ) -> None:
        self.compiled_graph = compiled_graph
        self.emitter = emitter
        self.ledger = ledger
        self.validator = validator
        self.profile = profile
        self._status = RuntimeStatus.READY

    # -----------------------------------------------------------------------
    # Main execution
    # -----------------------------------------------------------------------

    def execute(
        self,
        run_id: str,
        initial_state: "RunState | None" = None,
    ) -> "RunState":
        """
        Main execution loop for a graph run.

        Runs the compiled LangGraph until a terminal state or human gate.

        Args:
            run_id: Unique identifier for this run.
            initial_state: Optional starting state. If None, created from
                          the compiled graph.

        Returns:
            The final RunState after execution completes.
        """
        self._status = RuntimeStatus.RUNNING

        # Initialise state from compiled graph if not provided
        if initial_state is None:
            initial_state = self._create_state_from_graph(run_id)

        state = initial_state

        try:
            # Run the compiled graph until terminal or human gate
            while not self._is_terminal_state(state) and not self._has_human_gate(state):
                state = self._execute_next_node(state)

                # Check for human gates after each node
                gates = self.check_human_gates(state)
                if gates:
                    self._status = RuntimeStatus.PAUSED_AT_GATE
                    break

            # If we exited due to terminal state, mark complete
            if self._is_terminal_state(state):
                self._status = RuntimeStatus.COMPLETED
                if hasattr(state, "status"):
                    state.status = RunStatus.COMPLETED

        except Exception as exc:
            self._status = RuntimeStatus.FAILED
            if hasattr(state, "status"):
                state.status = RunStatus.FAILED
            if hasattr(state, "error"):
                state.error = str(exc)

        return state

    def _create_state_from_graph(self, run_id: str) -> "RunState":
        """Create a fresh RunState from the compiled graph."""
        state = RunState(
            run_id=run_id,
            status=RunStatus.RUNNING,
            results={},
            pending_workers=[],
            completed_workers=[],
            blocked_workers=[],
            checkpoints=[],
            human_gates=[],
            metadata={},
        )
        return state

    def _is_terminal_state(self, state: "RunState") -> bool:
        """Return True if the state is terminal (all nodes done or failed)."""
        if hasattr(state, "status") and state.status in (
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        ):
            return True

        # Check if there are pending workers
        if hasattr(state, "pending_workers") and state.pending_workers:
            return False

        # Check if all required nodes have results
        if hasattr(state, "results"):
            required_nodes = self._get_required_nodes()
            completed = set(state.results.keys())
            required = set(required_nodes)
            return required.issubset(completed)

        return False

    def _has_human_gate(self, state: "RunState") -> bool:
        """Return True if the state has pending human approval gates."""
        return bool(state.human_gates) if hasattr(state, "human_gates") else False

    def _get_required_nodes(self) -> List[str]:
        """Return list of node IDs that must complete."""
        if hasattr(self.compiled_graph, "nodes"):
            return [n.node_id for n in self.compiled_graph.nodes]
        return []

    def _execute_next_node(self, state: "RunState") -> "RunState":
        """Execute the next ready node in the graph."""
        ready_nodes = self._get_ready_nodes(state)

        if not ready_nodes:
            return state

        node_id = ready_nodes[0]
        return self.execute_node(node_id, state)

    def _get_ready_nodes(self, state: "RunState") -> List[str]:
        """Return list of node IDs whose dependencies are all satisfied."""
        if not hasattr(self.compiled_graph, "nodes"):
            return []

        completed = set(getattr(state, "results", {}).keys())
        pending = set(getattr(state, "pending_workers", []))
        blocked = set(getattr(state, "blocked_workers", []))

        ready: List[str] = []
        for node in self.compiled_graph.nodes:
            node_id = getattr(node, "node_id", "")
            if node_id in completed or node_id in pending or node_id in blocked:
                continue

            deps = getattr(node, "depends_on", [])
            if all(dep in completed for dep in deps):
                ready.append(node_id)

        return ready

    # -----------------------------------------------------------------------
    # Node execution
    # -----------------------------------------------------------------------

    def execute_node(self, node_id: str, state: "RunState") -> "RunState":
        """
        Execute a single node and return the updated state.

        Args:
            node_id: The ID of the node to execute.
            state: Current RunState.

        Returns:
            Updated RunState after node execution.
        """
        node = self._get_node(node_id)
        if node is None:
            return state

        role = getattr(node, "role", None)
        if role is None:
            return state

        # Route by node type
        if role == NodeType.WORKER:
            state = self._execute_worker_node(node, state)
        elif role == NodeType.CRITIC:
            state = self._execute_critic_node(node, state)
        elif role == NodeType.INTEGRATOR:
            state = self._execute_integrator_node(node, state)
        elif role == NodeType.PLANNER:
            state = self._execute_planner_node(node, state)
        elif role == NodeType.REVIEWER:
            state = self._execute_reviewer_node(node, state)
        else:
            state = self._execute_generic_node(node, state)

        return state

    def _execute_worker_node(self, node: Any, state: "RunState") -> "RunState":
        """Execute a worker node: emit packet, simulate result (M1), record."""
        # Build worker packet
        packet = self._build_worker_packet(node, state)

        # Emit the packet and get worker_id
        worker_id = self.emitter.emit(packet)
        if not worker_id:
            worker_id = getattr(packet, "worker_id", node.node_id)

        # Add to pending workers
        if not hasattr(state, "pending_workers"):
            state.pending_workers = []
        state.pending_workers.append(worker_id)

        self.ledger.log_event(
            event="worker_dispatched",
            run_id=state.run_id,
            worker_id=worker_id,
            node_id=node.node_id,
            role=str(getattr(node, "role", "")),
        )

        # Simulate worker result (M1 — actual execution would happen externally)
        result = self._simulate_worker_result(worker_id, node, state)

        # Handle the result
        state = self.handle_worker_result(result, state)

        return state

    def _execute_critic_node(self, node: Any, state: "RunState") -> "RunState":
        """Execute a critic node: evaluate results, record critic findings."""
        deps = getattr(node, "depends_on", [])
        worker_outputs: Dict[str, WorkerResult] = {}
        if hasattr(state, "results"):
            for dep_id in deps:
                if dep_id in state.results:
                    worker_outputs[dep_id] = state.results[dep_id]

        findings = self._evaluate_critic(node, worker_outputs, state)

        if not hasattr(state, "results"):
            state.results = {}
        state.results[node.node_id] = WorkerResult(
            run_id=state.run_id,
            worker_id=node.node_id,
            status=WorkerStatus.SUCCESS,
            output=findings,
            critic_findings=findings,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

        self.ledger.log_event(
            event="critic_evaluated",
            run_id=state.run_id,
            node_id=node.node_id,
            findings_summary=findings[:200] if isinstance(findings, str) else str(findings),
        )

        return state

    def _execute_integrator_node(self, node: Any, state: "RunState") -> "RunState":
        """Execute an integrator node: combine results into final output."""
        all_results = getattr(state, "results", {})

        integrator = ResultIntegrator()
        integrated = integrator.integrate_results(
            self.compiled_graph if hasattr(self, "compiled_graph") else None,
            all_results,
        )

        if not hasattr(state, "results"):
            state.results = {}
        state.results[node.node_id] = WorkerResult(
            run_id=state.run_id,
            worker_id=node.node_id,
            status=WorkerStatus.SUCCESS,
            output=integrated,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

        self.ledger.log_event(
            event="results_integrated",
            run_id=state.run_id,
            node_id=node.node_id,
            output_type=str(type(integrated).__name__),
        )

        return state

    def _execute_planner_node(self, node: Any, state: "RunState") -> "RunState":
        """Execute a planner node to decompose the objective."""
        task_prompt = getattr(node, "task_prompt", "")
        output = self._simulate_planner_output(task_prompt, state)

        if not hasattr(state, "results"):
            state.results = {}
        state.results[node.node_id] = WorkerResult(
            run_id=state.run_id,
            worker_id=node.node_id,
            status=WorkerStatus.SUCCESS,
            output=output,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

        self.ledger.log_event(
            event="planner_completed",
            run_id=state.run_id,
            node_id=node.node_id,
        )

        return state

    def _execute_reviewer_node(self, node: Any, state: "RunState") -> "RunState":
        """Execute a reviewer node for final validation."""
        deps = getattr(node, "depends_on", [])
        inputs: Dict[str, WorkerResult] = {}
        if hasattr(state, "results"):
            for dep_id in deps:
                if dep_id in state.results:
                    inputs[dep_id] = state.results[dep_id]

        is_valid, issues = self._validate_reviewer_inputs(node, inputs, state)

        output = {
            "approved": is_valid,
            "issues": issues,
            "reviewer_signoff": is_valid,
        }

        if not hasattr(state, "results"):
            state.results = {}
        state.results[node.node_id] = WorkerResult(
            run_id=state.run_id,
            worker_id=node.node_id,
            status=WorkerStatus.SUCCESS,
            output=output,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

        self.ledger.log_event(
            event="reviewer_completed",
            run_id=state.run_id,
            node_id=node.node_id,
            approved=is_valid,
        )

        return state

    def _execute_generic_node(self, node: Any, state: "RunState") -> "RunState":
        """Execute a generic node with no special handling."""
        if not hasattr(state, "results"):
            state.results = {}
        state.results[node.node_id] = WorkerResult(
            run_id=state.run_id,
            worker_id=node.node_id,
            status=WorkerStatus.SUCCESS,
            output=None,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        return state

    # -----------------------------------------------------------------------
    # Worker result handling
    # -----------------------------------------------------------------------

    def handle_worker_result(
        self,
        result: WorkerResult,
        state: "RunState",
    ) -> "RunState":
        """
        Process a completed worker result.

        - Update results dict
        - Move worker from pending to completed
        - Check if any blocked workers are now unblocked
        - Log to ledger

        Args:
            result: The completed WorkerResult.
            state: Current RunState.

        Returns:
            Updated RunState.
        """
        worker_id = getattr(result, "worker_id", "")

        # Update results dict
        if not hasattr(state, "results"):
            state.results = {}
        state.results[worker_id] = result

        # Move from pending to completed
        if hasattr(state, "pending_workers") and worker_id in state.pending_workers:
            state.pending_workers.remove(worker_id)

        if not hasattr(state, "completed_workers"):
            state.completed_workers = []
        if worker_id not in state.completed_workers:
            state.completed_workers.append(worker_id)

        # Check blocked workers
        state = self._unblock_workers_if_ready(state)

        # Log to ledger
        self.ledger.log_worker_result(
            run_id=state.run_id,
            worker_id=worker_id,
            status=str(getattr(result, "status", WorkerStatus.SUCCESS)),
            output=getattr(result, "output", None),
            duration_seconds=getattr(result, "duration_seconds", None),
        )

        # Handle delegation (child workers spawned by this result)
        if hasattr(result, "children") and result.children:
            for child_id in result.children:
                child_result = self._get_child_result(child_id, state)
                if child_result:
                    state = self.handle_worker_result(child_result, state)

        return state

    def _unblock_workers_if_ready(self, state: "RunState") -> "RunState":
        """Check if any blocked workers can now proceed."""
        if not hasattr(state, "blocked_workers") or not state.blocked_workers:
            return state

        completed = set(getattr(state, "completed_workers", []))
        still_blocked: List[str] = []
        newly_ready: List[str] = []

        for blocked_id in state.blocked_workers:
            node = self._get_node(blocked_id)
            if node is None:
                still_blocked.append(blocked_id)
                continue

            deps = getattr(node, "depends_on", [])
            if all(dep in completed for dep in deps):
                newly_ready.append(blocked_id)
            else:
                still_blocked.append(blocked_id)

        state.blocked_workers = still_blocked

        # Execute newly unblocked workers
        for node_id in newly_ready:
            state = self.execute_node(node_id, state)

        return state

    def _get_child_result(self, child_id: str, state: "RunState") -> "WorkerResult | None":
        """Retrieve a child worker result if available."""
        return getattr(state, "results", {}).get(child_id)

    # -----------------------------------------------------------------------
    # Delegation handling
    # -----------------------------------------------------------------------

    def handle_delegation_request(
        self,
        parent_worker_id: str,
        child_packet: WorkerPacket,
        state: "RunState",
    ) -> Tuple[bool, str]:
        """
        Process a delegation request from a worker.

        Validates through PolicyValidator.validate_worker_request() and
        emits the child packet if approved.

        Args:
            parent_worker_id: ID of the delegating worker.
            child_packet: WorkerPacket for the child worker.
            state: Current RunState.

        Returns:
            Tuple of (approved: bool, identifier: str).
            If approved, identifier is the child_worker_id.
            If rejected, identifier is the rejection reason.
        """
        # Validate the delegation request through policy validator
        validation_errors: List[str] = []
        if hasattr(self.validator, "validate_worker_request"):
            validation_errors = self.validator.validate_worker_request(
                parent_worker_id, child_packet, state
            )
        else:
            # Fallback: basic depth check
            current_depth = getattr(child_packet, "depth", 0)
            max_depth = getattr(self.profile, "max_depth", 10)
            if current_depth > max_depth:
                validation_errors.append(
                    f"Delegation depth {current_depth} exceeds max_depth {max_depth}"
                )

        if validation_errors:
            reason = "; ".join(validation_errors)
            self.ledger.log_event(
                event="delegation_rejected",
                run_id=state.run_id,
                parent_worker_id=parent_worker_id,
                child_worker_id=getattr(child_packet, "worker_id", "unknown"),
                reason=reason,
            )
            return (False, reason)

        # Emit child packet
        child_worker_id = self.emitter.emit(child_packet)
        if not child_worker_id:
            child_worker_id = getattr(child_packet, "worker_id", str(uuid.uuid4()))

        # Add to pending workers
        if not hasattr(state, "pending_workers"):
            state.pending_workers = []
        state.pending_workers.append(child_worker_id)

        self.ledger.log_event(
            event="delegation_approved",
            run_id=state.run_id,
            parent_worker_id=parent_worker_id,
            child_worker_id=child_worker_id,
        )

        return (True, child_worker_id)

    # -----------------------------------------------------------------------
    # Human gate handling
    # -----------------------------------------------------------------------

    def check_human_gates(self, state: "RunState") -> List[str]:
        """
        Return list of approval gates that need human input before proceeding.

        Args:
            state: Current RunState.

        Returns:
            List of gate identifiers awaiting approval.
        """
        gates: List[str] = []

        # Check for nodes with approval requirements
        if hasattr(self.compiled_graph, "nodes"):
            for node in self.compiled_graph.nodes:
                if getattr(node, "role", None) == NodeType.APPROVER:
                    node_id = getattr(node, "node_id", "")
                    deps = getattr(node, "depends_on", [])
                    completed = set(getattr(state, "results", {}).keys())
                    if all(dep in completed for dep in deps):
                        approval_key = f"approval_{node_id}"
                        if not hasattr(state, "metadata") or approval_key not in state.metadata:
                            gates.append(node_id)

        # Check explicit human gates in state
        if hasattr(state, "human_gates"):
            for gate in state.human_gates:
                gate_id = gate if isinstance(gate, str) else getattr(gate, "gate_id", str(gate))
                approval_key = f"approval_{gate_id}"
                if not hasattr(state, "metadata") or approval_key not in state.metadata:
                    gates.append(gate_id)

        return gates

    def wait_for_human_approval(
        self,
        gate: str,
        decision: str,
        approver: str,
    ) -> None:
        """
        Record human approval decision in ledger and state.

        Args:
            gate: The gate identifier that was approved/rejected.
            decision: Either "approved" or "rejected".
            approver: Identifier of the approver.
        """
        self.ledger.log_event(
            event="human_approval",
            gate=gate,
            decision=decision,
            approver=approver,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _get_node(self, node_id: str) -> Any:
        """Retrieve a node by ID from the compiled graph."""
        if hasattr(self.compiled_graph, "nodes"):
            for node in self.compiled_graph.nodes:
                if getattr(node, "node_id", "") == node_id:
                    return node
        return None

    def _build_worker_packet(self, node: Any, state: "RunState") -> WorkerPacket:
        """Build a WorkerPacket from a node definition."""
        worker_id = f"worker-{getattr(node, 'node_id', 'unknown')}-{uuid.uuid4().hex[:8]}"

        return WorkerPacket(
            run_id=state.run_id,
            worker_id=worker_id,
            parent_worker_id=None,
            depth=0,
            role=getattr(node, "role", NodeType.WORKER),
            objective=getattr(node, "task_prompt", ""),
            task_prompt=getattr(node, "task_prompt", ""),
            allowed_tools=getattr(node, "allowed_tools", []),
            forbidden_tools=getattr(node, "forbidden_tools", []),
            owned_paths=getattr(node, "owned_paths", []),
            forbidden_paths=getattr(node, "forbidden_paths", []),
            timeout=getattr(node, "timeout", None),
            budget=getattr(node, "budget", None),
            delegation_permission=getattr(node, "delegation_permission", DelegationPermission.NONE),
            max_child_depth=getattr(node, "max_child_depth", 0),
            result_contract=getattr(node, "result_contract", None),
            no_revert_rule=getattr(node, "no_revert_rule", False),
            secrets_rule=getattr(node, "secrets_rule", None),
            verification_expectations=getattr(node, "verification_expectations", None),
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def _simulate_worker_result(
        self,
        worker_id: str,
        node: Any,
        state: "RunState",
    ) -> WorkerResult:
        """
        Simulate a worker result for M1.

        In production this would be replaced by actual worker execution.
        Here we produce a plausible result for testing/development.
        """
        return WorkerResult(
            run_id=state.run_id,
            worker_id=worker_id,
            parent_worker_id=None,
            status=WorkerStatus.SUCCESS,
            output=f"Result from worker {worker_id} for node {getattr(node, 'node_id', 'unknown')}",
            duration_seconds=1.0,
            cost_estimate=0.01,
            children=[],
            artifacts=[],
            critic_findings=None,
            verification_status="pass",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

    def _simulate_planner_output(self, task_prompt: str, state: "RunState") -> str:
        """Simulate planner output by decomposing the task prompt."""
        return f"Decomposed plan for: {task_prompt[:100]}..."

    def _evaluate_critic(
        self,
        node: Any,
        worker_outputs: Dict[str, WorkerResult],
        state: "RunState",
    ) -> str:
        """Evaluate worker outputs and produce critic findings."""
        findings_parts = []
        for worker_id, result in worker_outputs.items():
            output = getattr(result, "output", None)
            findings_parts.append(f"[{worker_id}]: {output}")

        return "Critique: All worker outputs reviewed. " + " | ".join(findings_parts)

    def _validate_reviewer_inputs(
        self,
        node: Any,
        inputs: Dict[str, WorkerResult],
        state: "RunState",
    ) -> Tuple[bool, List[str]]:
        """Validate reviewer inputs and return (is_valid, issues)."""
        issues: List[str] = []

        for worker_id, result in inputs.items():
            status = getattr(result, "status", None)
            if status != WorkerStatus.SUCCESS:
                issues.append(f"{worker_id} completed with status {status}")

        is_valid = len(issues) == 0
        return (is_valid, issues)

    @property
    def status(self) -> RuntimeStatus:
        """Return current runtime status."""
        return self._status


# ---------------------------------------------------------------------------
# ResultIntegrator
# ---------------------------------------------------------------------------

class ResultIntegrator:
    """
    Combines all worker results into a final output based on graph shape.

    Supports:
    - single_worker: return the single result output
    - planner_worker_critic: synthesize worker output with critic review
    - multi_team: merge parallel outputs
    - recursive_delegation: recursively integrate child results
    """

    def integrate_results(
        self,
        graph_spec: "GraphSpec | None",
        results: Dict[str, WorkerResult],
    ) -> Any:
        """
        Combine all worker results into final output.

        Args:
            graph_spec: The GraphSpec describing the graph shape.
            results: Dict mapping worker/node ID to WorkerResult.

        Returns:
            The integrated final output.
        """
        if not results:
            return None

        # Determine shape from graph_spec
        shape = self._get_graph_shape(graph_spec)

        if shape == "single_worker":
            return self._integrate_single_worker(results)
        elif shape == "planner_worker_critic":
            return self._integrate_planner_worker_critic(results)
        elif shape == "multi_team":
            return self._integrate_multi_team(results)
        elif shape == "recursive_delegation":
            return self._integrate_recursive_delegation(results)
        else:
            return self._integrate_default(results)

    def _get_graph_shape(self, graph_spec: "GraphSpec | None") -> str:
        """Determine graph shape from spec or results."""
        if graph_spec and hasattr(graph_spec, "metadata"):
            shape = graph_spec.metadata.get("shape", "")
            if shape:
                return shape

        # Infer from result keys
        keys = list(results.keys())
        if any("integrator" in k.lower() for k in keys):
            return "multi_team"
        if any("critic" in k.lower() for k in keys):
            return "planner_worker_critic"
        return "single_worker"

    def _integrate_single_worker(self, results: Dict[str, WorkerResult]) -> Any:
        """Return the single worker result output."""
        for result in results.values():
            return getattr(result, "output", None)
        return None

    def _integrate_planner_worker_critic(
        self,
        results: Dict[str, WorkerResult],
    ) -> Dict[str, Any]:
        """Synthesize planner -> worker -> critic chain into final output."""
        planner_output = None
        worker_output = None
        critic_findings: List[str] = []

        for worker_id, result in results.items():
            output = getattr(result, "output", None)
            critic = getattr(result, "critic_findings", None)

            if "planner" in worker_id.lower():
                planner_output = output
            elif "worker" in worker_id.lower() and "critic" not in worker_id.lower():
                worker_output = output
            elif critic:
                critic_findings.append(critic)

        return {
            "planner_output": planner_output,
            "worker_output": worker_output,
            "critic_findings": critic_findings,
            "final_output": worker_output,
            "status": "completed",
        }

    def _integrate_multi_team(self, results: Dict[str, WorkerResult]) -> Dict[str, Any]:
        """Merge parallel worker outputs."""
        outputs: Dict[str, Any] = {}

        for worker_id, result in results.items():
            if "integrator" in worker_id.lower() or "reviewer" in worker_id.lower():
                continue
            outputs[worker_id] = getattr(result, "output", None)

        # Check for integrator output
        integrator_output = None
        for worker_id, result in results.items():
            if "integrator" in worker_id.lower():
                integrator_output = getattr(result, "output", None)
                break

        return {
            "worker_outputs": outputs,
            "integrated_output": integrator_output,
            "worker_count": len(outputs),
            "status": "completed",
        }

    def _integrate_recursive_delegation(
        self,
        results: Dict[str, WorkerResult],
    ) -> Dict[str, Any]:
        """Recursively integrate child results."""
        outputs: Dict[str, Any] = {}
        children: Dict[str, List[str]] = {}

        for worker_id, result in results.items():
            children_list = getattr(result, "children", [])
            if children_list:
                children[worker_id] = children_list
            outputs[worker_id] = {
                "output": getattr(result, "output", None),
                "status": str(getattr(result, "status", "")),
                "children": children_list,
            }

        # Recursively integrate children
        for worker_id, child_ids in children.items():
            child_outputs = [outputs[cid] for cid in child_ids if cid in outputs]
            if child_outputs:
                outputs[worker_id]["child_outputs"] = child_outputs

        return {
            "delegation_tree": outputs,
            "status": "completed",
        }

    def _integrate_default(self, results: Dict[str, WorkerResult]) -> Dict[str, Any]:
        """Default integration: collect all results."""
        return {
            "results": {
                k: getattr(v, "output", None) for k, v in results.items()
            },
            "status": "completed",
        }

    def verify_deliverable(
        self,
        output: Any,
        contract: Dict[str, Any],
    ) -> Tuple[bool, List[str]]:
        """
        Check if output meets the result_contract.

        Args:
            output: The integrated output.
            contract: Expected contract dict.

        Returns:
            Tuple of (is_valid: bool, issues: List[str]).
        """
        issues: List[str] = []

        if contract is None:
            return (True, [])

        # Check required keys
        required_keys = contract.get("required_keys", [])
        for key in required_keys:
            if not self._has_nested_key(output, key):
                issues.append(f"Required key '{key}' missing from output")

        # Check output type
        expected_type = contract.get("type", None)
        if expected_type and not self._check_output_type(output, expected_type):
            issues.append(f"Output type mismatch: expected {expected_type}")

        # Check output is not None
        if output is None:
            issues.append("Output is None")

        is_valid = len(issues) == 0
        return (is_valid, issues)

    def _has_nested_key(self, obj: Any, key: str) -> bool:
        """Check if a dot-notation key exists in a nested object."""
        parts = key.split(".")
        current: Any = obj

        for part in parts:
            if isinstance(current, dict):
                if part not in current:
                    return False
                current = current[part]
            elif hasattr(current, part):
                current = getattr(current, part)
            else:
                return False

        return True

    def _check_output_type(self, output: Any, expected_type: str) -> bool:
        """Check if output matches expected type."""
        type_map = {
            "string": (str,),
            "object": (dict,),
            "list": (list, tuple),
            "boolean": (bool,),
            "number": (int, float),
        }
        expected_classes = type_map.get(expected_type.lower(), (object,))
        return isinstance(output, expected_classes)


# ---------------------------------------------------------------------------
# StateManager
# ---------------------------------------------------------------------------

class StateManager:
    """
    Manages RunState checkpoints for recovery and inspection.

    Checkpoints are stored in runs/<run_id>/checkpoints/.
    """

    def __init__(self, runs_dir: Path) -> None:
        self.runs_dir = Path(runs_dir)

    def save_checkpoint(
        self,
        state: "RunState",
        label: str,
    ) -> str:
        """
        Create a checkpoint of the current state.

        Args:
            state: The RunState to checkpoint.
            label: A descriptive label for this checkpoint.

        Returns:
            The checkpoint_id (filename without extension).
        """
        run_id = getattr(state, "run_id", "unknown")
        checkpoint_dir = self.runs_dir / run_id / "checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)

        # Generate checkpoint ID
        timestamp = datetime.now(timezone.utc).isoformat().replace(":", "-").replace(".", "-")
        checkpoint_id = f"{label}_{timestamp}"

        # Serialize results
        serialized_results = self._serialize_results(getattr(state, "results", {}))

        checkpoint_data = {
            "checkpoint_id": checkpoint_id,
            "label": label,
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": str(getattr(state, "status", "running")),
            "results": serialized_results,
            "pending_workers": getattr(state, "pending_workers", []),
            "completed_workers": getattr(state, "completed_workers", []),
            "metadata": getattr(state, "metadata", {}),
        }

        checkpoint_path = checkpoint_dir / f"{checkpoint_id}.json"
        checkpoint_path.write_text(
            json.dumps(checkpoint_data, indent=2),
            encoding="utf-8",
        )

        return checkpoint_id

    def restore_checkpoint(
        self,
        run_id: str,
        checkpoint_id: str,
    ) -> "RunState":
        """
        Restore a RunState from a checkpoint.

        Args:
            run_id: The run identifier.
            checkpoint_id: The checkpoint identifier.

        Returns:
            The restored RunState.
        """
        checkpoint_path = self.runs_dir / run_id / "checkpoints" / f"{checkpoint_id}.json"

        if not checkpoint_path.exists():
            raise FileNotFoundError(f"Checkpoint not found: {checkpoint_path}")

        data = json.loads(checkpoint_path.read_text(encoding="utf-8"))

        # Reconstruct results as WorkerResult objects
        results = {}
        for worker_id, result_data in data.get("results", {}).items():
            results[worker_id] = WorkerResult(**result_data)

        status_val = data.get("status", "RUNNING")
        try:
            status_enum = RunStatus[status_val]
        except KeyError:
            status_enum = RunStatus.RUNNING

        state = RunState(
            run_id=run_id,
            status=status_enum,
            results=results,
            pending_workers=data.get("pending_workers", []),
            completed_workers=data.get("completed_workers", []),
            blocked_workers=[],
            checkpoints=[],
            human_gates=[],
            metadata=data.get("metadata", {}),
        )

        return state

    def list_checkpoints(self, run_id: str) -> List[Dict[str, str]]:
        """
        List all checkpoints for a run.

        Args:
            run_id: The run identifier.

        Returns:
            List of dicts with checkpoint metadata.
        """
        checkpoint_dir = self.runs_dir / run_id / "checkpoints"

        if not checkpoint_dir.exists():
            return []

        checkpoints: List[Dict[str, str]] = []
        for fp in checkpoint_dir.glob("*.json"):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                checkpoints.append({
                    "checkpoint_id": data.get("checkpoint_id", fp.stem),
                    "label": data.get("label", ""),
                    "timestamp": data.get("timestamp", ""),
                    "status": data.get("status", ""),
                })
            except Exception:
                checkpoints.append({
                    "checkpoint_id": fp.stem,
                    "label": "unknown",
                    "timestamp": "",
                    "status": "error",
                })

        return sorted(checkpoints, key=lambda x: x.get("timestamp", ""))

    def _serialize_results(self, results: Dict[str, WorkerResult]) -> Dict[str, Any]:
        """Serialize a results dict to JSON-compatible format."""
        serialized: Dict[str, Any] = {}
        for worker_id, result in results.items():
            if hasattr(result, "model_dump"):
                serialized[worker_id] = result.model_dump(mode="json")
            else:
                serialized[worker_id] = {
                    "run_id": getattr(result, "run_id", ""),
                    "worker_id": getattr(result, "worker_id", ""),
                    "parent_worker_id": getattr(result, "parent_worker_id", None),
                    "status": str(getattr(result, "status", "")),
                    "output": getattr(result, "output", None),
                    "error": getattr(result, "error", None),
                    "duration_seconds": getattr(result, "duration_seconds", None),
                    "cost_estimate": getattr(result, "cost_estimate", None),
                    "children": getattr(result, "children", []),
                    "artifacts": getattr(result, "artifacts", []),
                    "critic_findings": getattr(result, "critic_findings", None),
                    "verification_status": getattr(result, "verification_status", None),
                    "completed_at": getattr(result, "completed_at", None),
                }
        return serialized


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

def run_execution(
    runs_dir: Path,
    objective: str,
    profile_name: str,
) -> Dict[str, Any]:
    """
    Convenience function that loads profile, architects, validates, compiles,
    executes, integrates results, and returns a summary dict.

    Args:
        runs_dir: Path to the runs directory.
        objective: Natural-language objective string.
        profile_name: Name of the profile to use.

    Returns:
        Summary dict with status, output, and metadata.
    """
    runs_dir = Path(runs_dir)
    summary: Dict[str, Any] = {
        "objective": objective,
        "profile": profile_name,
        "status": "initiated",
        "errors": [],
    }

    try:
        # 1. Load profile
        try:
            profile_mod = __import__("policy", fromlist=["load_profile"])
            profile = profile_mod.load_profile(profile_name)
        except Exception as e:
            summary["errors"].append(f"Profile load failed: {e}")
            profile = ProfilePolicy(name=profile_name)

        # 2. Assess difficulty and architect graph
        try:
            architect_mod = __import__("graph_architect", fromlist=["GraphArchitect", "DifficultyAssessor"])
            architect = architect_mod.GraphArchitect(profile)
            assessor = architect_mod.DifficultyAssessor()
            difficulty, shape, rationale = assessor.assess(objective)
            graph_spec = architect.architect(objective, difficulty, shape)
            summary["difficulty"] = difficulty.value
            summary["shape"] = shape.value
        except Exception as e:
            summary["errors"].append(f"Graph architect failed: {e}")
            raise

        # 3. Validate graph
        validator = PolicyValidator(profile)
        try:
            validation_errors = validator.validate_spec(graph_spec)
            if validation_errors:
                summary["validation_errors"] = validation_errors
                summary["status"] = "validation_failed"
                return summary
        except Exception as e:
            summary["errors"].append(f"Validation failed: {e}")

        # 4. Compile graph
        compiled_graph: Any = graph_spec
        try:
            compiler_mod = __import__("graph_compiler", fromlist=["GraphCompiler"])
            compiler = compiler_mod.GraphCompiler(graph_spec, profile)
            compiled_graph = compiler.compile()
        except Exception as e:
            summary["errors"].append(f"Compilation failed: {e}")
            compiled_graph = graph_spec  # Fall back to spec as compiled graph

        # 5. Execute
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        emitter = PacketEmitter()
        ledger = Ledger(runs_dir=runs_dir)

        engine = ExecutionEngine(
            compiled_graph=compiled_graph,
            emitter=emitter,
            ledger=ledger,
            validator=validator,
            profile=profile,
        )

        final_state = engine.execute(run_id)
        summary["run_id"] = run_id

        # 6. Integrate results
        integrator = ResultIntegrator()
        final_output = integrator.integrate_results(
            graph_spec,
            getattr(final_state, "results", {}),
        )

        # 7. Verify deliverable
        output_contract: Dict[str, Any] = {"type": "object", "required_keys": []}
        for node in getattr(graph_spec, "nodes", []):
            if getattr(node, "role", None) == NodeType.INTEGRATOR:
                contract_str = getattr(node, "result_contract", None)
                if contract_str:
                    output_contract["required_keys"] = []

        is_valid, issues = integrator.verify_deliverable(final_output, output_contract)

        summary.update({
            "status": "completed" if is_valid else "output_invalid",
            "final_output": final_output,
            "output_valid": is_valid,
            "output_issues": issues,
            "results_count": len(getattr(final_state, "results", {})),
            "completed_workers": getattr(final_state, "completed_workers", []),
        })

    except Exception as exc:
        summary["status"] = "failed"
        summary["errors"].append(str(exc))

    return summary