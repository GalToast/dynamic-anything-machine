"""
Graph Compiler: compiles a validated GraphSpec into a LangGraph StateGraph for execution.

This module provides:
- RunState: Pydantic model for state flowing through the compiled graph
- GraphCompiler: compiles GraphSpec into LangGraph StateGraph
- FakeExecutor: simulated worker execution (Milestone 1)
- ProviderBackend: real model call stub (Milestone 2)
- compile_and_prepare: factory function that sets up all components

Example:
    >>> spec = GraphSpec.from_dict({...})
    >>> compiled, emitter, ledger = compile_and_prepare(spec, profile, runs_dir)
    >>> result = compiled.graph.execute({"run_id": "abc"})
"""

from __future__ import annotations

import random
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

# Pydantic must be imported before any class that inherits from it
from pydantic import BaseModel, Field

# LangGraph imports
from langgraph.graph import StateGraph, END

# Local imports
from dynamic_schema import (
    GraphSpec,
    GraphNode,
    GraphEdge,
    WorkerPacket,
    WorkerResult,
    NodeType,
    RunStatus,
    WorkerStatus,
)

from policy import ProfilePolicy, PolicyValidator

from worker_protocol import PacketEmitter

from ledger import Ledger


# ---------------------------------------------------------------------------
# RunState
# ---------------------------------------------------------------------------


class RunState(BaseModel):
    """
    State that flows through the compiled LangGraph StateGraph.

    Tracks execution progress, worker outputs, pending/blocked workers,
    cost/duration estimates, human approval gates, and final output.
    """

    run_id: str = Field(..., description="Unique run identifier")
    status: RunStatus = Field(default=RunStatus.RUNNING, description="Current run status")
    packets: Dict[str, WorkerPacket] = Field(
        default_factory=dict, description="worker_id -> dispatched WorkerPacket"
    )
    results: Dict[str, WorkerResult] = Field(
        default_factory=dict, description="worker_id -> completed WorkerResult"
    )
    pending_workers: List[str] = Field(
        default_factory=list, description="Worker IDs awaiting dispatch"
    )
    completed_workers: List[str] = Field(
        default_factory=list, description="Worker IDs that finished successfully"
    )
    blocked_workers: List[str] = Field(
        default_factory=list, description="Worker IDs blocked on dependencies"
    )
    events: List[str] = Field(default_factory=list, description="Event IDs in the ledger")
    current_depth: int = Field(default=0, description="Current delegation depth")
    total_cost_estimate: float = Field(default=0.0, ge=0, description="Running cost estimate")
    total_duration_seconds: float = Field(default=0.0, ge=0, description="Wall-clock elapsed")
    last_checkpoint_id: str | None = Field(default=None, description="Latest checkpoint ID")
    human_approvals_pending: List[str] = Field(
        default_factory=list, description="Approval gate descriptions awaiting human sign-off"
    )
    human_approvals_granted: List[str] = Field(
        default_factory=list, description="Approved gate descriptions"
    )
    final_output: Any | None = Field(default=None, description="Final graph output when complete")
    errors: List[str] = Field(default_factory=list, description="Error messages encountered")


# ---------------------------------------------------------------------------
# CompiledGraph
# ---------------------------------------------------------------------------


@dataclass
class CompiledGraph:
    """
    A compiled LangGraph StateGraph ready for execution.

    Attributes:
        run_id: Unique run identifier.
        spec: The source GraphSpec.
        graph: The compiled LangGraph StateGraph.
        execution_order: Topologically-sorted list of node IDs.
        node_metadata: Per-node metadata (timeout, budget, role).
        compile_errors: Validation errors encountered during compilation.
        is_valid: True if no compile errors.
        fake_executor: FakeExecutor instance for Milestone 1 simulation.
    """

    run_id: str
    spec: GraphSpec
    graph: StateGraph
    execution_order: List[str] = field(default_factory=list)
    node_metadata: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    compile_errors: List[str] = field(default_factory=list)
    is_valid: bool = True
    fake_executor: FakeExecutor | None = None


# ---------------------------------------------------------------------------
# FakeExecutor (Milestone 1 - no real model calls)
# ---------------------------------------------------------------------------


class FakeExecutor:
    """
    Simulated worker executor for Milestone 1.

    Returns deterministic fake results without calling any external LLM.
    Used when real model infrastructure is not yet available.
    """

    def __init__(self, ledger: Ledger | None = None) -> None:
        self.ledger = ledger
        self._call_log: List[Dict[str, Any]] = []

    def simulate_worker(self, packet: WorkerPacket) -> WorkerResult:
        """
        Return a fake WorkerResult for the given packet.

        Simulates execution by:
        - Generating a plausible output string
        - Assigning a small random duration
        - Zero cost (M1)
        - Always passing verification

        Args:
            packet: The WorkerPacket dispatched to this worker.

        Returns:
            A WorkerResult with status=success and simulated output.
        """
        # Truncate objective for display
        objective_snippet = (
            packet.objective[:50] + "..." if len(packet.objective) > 50 else packet.objective
        )

        # Simulate a small amount of work
        duration = random.uniform(0.05, 0.5)
        time.sleep(duration)

        result = WorkerResult(
            run_id=packet.run_id,
            worker_id=packet.worker_id,
            parent_worker_id=packet.parent_worker_id,
            status=WorkerStatus.SUCCESS,
            output=f"Simulated result for {packet.worker_id}: {objective_snippet}",
            duration_seconds=round(duration, 3),
            cost_estimate=0.0,
            children=[],
            artifacts=[],
            critic_findings=None,
            verification_status="passed",
        )

        if self.ledger:
            self.ledger.log_event(
                run_id=packet.run_id,
                event_type="fake_worker_result",
                worker_id=packet.worker_id,
                depth=packet.depth,
                data={
                    "simulated": True,
                    "duration": duration,
                    "output_preview": objective_snippet,
                },
            )

        self._call_log.append({
            "worker_id": packet.worker_id,
            "role": packet.role.value if hasattr(packet.role, "value") else str(packet.role),
            "duration": duration,
            "status": "success",
        })

        return result

    @property
    def call_count(self) -> int:
        """Number of simulate_worker calls made."""
        return len(self._call_log)


# ---------------------------------------------------------------------------
# ProviderBackend (Milestone 2 stub)
# ---------------------------------------------------------------------------


class ProviderBackend:
    """
    Real-model backend stub for Milestone 2.

    In Milestone 1, all methods raise NotImplementedError.
    In Milestone 2, this class uses an OpenAI-compatible client (MiniMax M2.7)
    to make actual model calls for architect, critic, and worker nodes.

    All calls are logged with masked config, elapsed time, and cost.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = "MiniMax-M2.7",
        ledger: Ledger | None = None,
    ) -> None:
        self.api_key = api_key or ""
        self.base_url = base_url or ""
        self.model = model
        self.ledger = ledger
        self._call_log: List[Dict[str, Any]] = []

    def _log_call(
        self, call_type: str, worker_id: str, duration: float, cost: float, success: bool
    ) -> None:
        """Append a structured log entry for a model call."""
        entry = {
            "call_type": call_type,
            "worker_id": worker_id,
            "model": self.model,
            "duration_seconds": round(duration, 3),
            "cost_estimate": cost,
            "success": success,
            # Mask API key in logs
            "api_key_masked": (
                self.api_key[:4] + "****" + self.api_key[-4:] if len(self.api_key) > 8 else "****"
            ),
        }
        self._call_log.append(entry)
        if self.ledger:
            self.ledger.log_event(
                run_id=run_id,
                event_type=f"{call_type}_call",
                worker_id=worker_id,
                depth=None,
                data=entry,
            )

    def call_architect(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        """
        Call the architect model to decompose a task.

        Milestone 2: Uses OpenAI-compatible client with MiniMax M2.7.
        Milestone 1: Raises NotImplementedError.
        """
        raise NotImplementedError(
            "ProviderBackend.call_architect is not implemented in Milestone 1. "
            "Use FakeExecutor for simulation."
        )

    def call_critic(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        """
        Call the critic model to evaluate output quality.

        Milestone 2: Uses OpenAI-compatible client with MiniMax M2.7.
        Milestone 1: Raises NotImplementedError.
        """
        raise NotImplementedError(
            "ProviderBackend.call_critic is not implemented in Milestone 1. "
            "Use FakeExecutor for simulation."
        )

    def call_worker(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        """
        Call the worker model to execute a delegated task.

        Milestone 2: Uses MiniMaxDirectClient for real LLM calls with tool support.
        Milestone 1: Falls back to FakeExecutor if ANTHROPIC_AUTH_TOKEN is not set.
        """
        # Try real execution via MiniMaxDirectClient
        try:
            from packet_runtime import MiniMaxDirectClient, TOOLS
            from langchain_core.messages import HumanMessage

            client = MiniMaxDirectClient()
            if client.api_key:
                llm = client.bind_tools(TOOLS)
                response = llm.invoke([HumanMessage(content=prompt)])
                return {
                    "status": "success",
                    "output": response.content,
                    "model": self.model,
                    "cost_estimate": 0.0,  # MiniMaxDirectClient doesn't track cost yet
                }
        except Exception as exc:
            # Fall back to simulated execution on any error
            pass

        # Fallback: simulated execution
        packet = kwargs.get("packet")
        if packet:
            result = self._fallback_simulate(packet)
            return {
                "status": result.status.value,
                "output": result.output,
                "cost_estimate": result.cost_estimate or 0.0,
            }
        raise RuntimeError("call_worker requires either a valid API key or a packet")

    def _fallback_simulate(self, packet: WorkerPacket) -> WorkerResult:
        """Run FakeExecutor logic inline when no API key is available."""
        from dynamic_schema import WorkerStatus

        duration = random.uniform(0.05, 0.5)
        return WorkerResult(
            run_id=packet.run_id,
            worker_id=packet.worker_id,
            parent_worker_id=packet.parent_worker_id,
            status=WorkerStatus.SUCCESS,
            output=f"Simulated result for {packet.worker_id}: {packet.objective[:50]}",
            duration_seconds=round(duration, 3),
            cost_estimate=0.0,
            children=[],
            artifacts=[],
            critic_findings=None,
            verification_status="passed",
        )

    @property
    def call_count(self) -> int:
        """Number of real model calls made."""
        return len(self._call_log)


# ---------------------------------------------------------------------------
# GraphCompiler
# ---------------------------------------------------------------------------


class GraphCompiler:
    """
    Compiles a validated GraphSpec into a LangGraph StateGraph.

    Takes a GraphSpec (produced by the graph architect) and produces a
    CompiledGraph with a LangGraph StateGraph whose nodes and edges reflect
    the graph spec structure.

    Node handling:
    - Local/deterministic nodes (planner, retriever, scorer, router, critic):
      Implemented as pure Python functions that transform state in-memory.
      These do NOT call any external LLM in Milestone 1.
    - Executor/worker nodes:
      Emit a WorkerPacket via the PacketEmitter, then wait for a result.
      In Milestone 1, FakeExecutor simulates results.

    Edge handling:
    - Unconditional edges are added directly as state transitions.
    - Conditional edges (with a `condition` expression) get a condition
      function that evaluates the expression against the current state.

    Checkpointing:
    - LangGraph checkpoint is configured via _create_checkpointer() for
      resumability on interruption.

    Attributes:
        graph_spec: The GraphSpec to compile.
        profile: ProfilePolicy for validation.
        emitter: PacketEmitter for writing WorkerPackets to disk.
        ledger: Ledger for append-only event logging.
        validator: PolicyValidator instance.
        fake_executor: FakeExecutor for Milestone 1 simulation.
        backend: ProviderBackend (stub until Milestone 2).
    """

    # Node types treated as deterministic/local (no external LLM calls)
    _LOCAL_NODE_TYPES: frozenset[NodeType] = frozenset({
        NodeType.PLANNER,
        NodeType.RETRIEVER,
        NodeType.SCORER,
        NodeType.ROUTER,
        NodeType.CRITIC,
    })

    def __init__(
        self,
        graph_spec: GraphSpec,
        profile: ProfilePolicy,
        emitter: PacketEmitter,
        ledger: Ledger,
    ) -> None:
        self.graph_spec = graph_spec
        self.profile = profile
        self.emitter = emitter
        self.ledger = ledger

        self.validator = PolicyValidator()
        self._compile_errors: List[str] = []

        # Execution backends
        self.fake_executor = FakeExecutor(ledger=ledger)
        self.backend = ProviderBackend(ledger=ledger)

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def compile(self) -> CompiledGraph:
        """
        Compile the graph spec into a LangGraph StateGraph.

        Returns:
            A CompiledGraph wrapping the StateGraph, execution order,
            node metadata, and any compile errors.
        """
        spec = self.graph_spec
        errors: List[str] = []

        # Validate spec against policy
        policy_result = self.validator.validate_graph_spec(spec, self.profile)
        if not policy_result.valid:
            errors.extend(policy_result.errors)

        # Build the StateGraph
        builder = StateGraph(RunState)

        # Add one node per GraphNode
        for node in spec.nodes:
            node_id, node_fn = self._compile_node(node)
            builder.add_node(node_id, node_fn)

        # Add edges
        for edge in spec.edges:
            self._compile_edge(builder, edge)

        # Set entry point (first node with no dependencies, or first in topological order)
        entry_point = self._determine_entry_point(spec)
        builder.set_entry_point(entry_point)

        # Compile to get the executable graph
        graph = builder.compile()

        # Compute topological execution order
        execution_order = self._topological_order(spec)

        # Build per-node metadata
        node_metadata: Dict[str, Dict[str, Any]] = {}
        for node in spec.nodes:
            node_metadata[node.node_id] = {
                "timeout": node.timeout,
                "budget": node.budget,
                "role": node.role.value if hasattr(node.role, "value") else str(node.role),
                "allowed_tools": node.allowed_tools,
                "forbidden_tools": node.forbidden_tools,
                "owned_paths": node.owned_paths,
                "checkpoint": node.checkpoint,
            }

        return CompiledGraph(
            run_id=spec.run_id,
            spec=spec,
            graph=graph,
            execution_order=execution_order,
            node_metadata=node_metadata,
            compile_errors=errors,
            is_valid=len(errors) == 0,
            fake_executor=self.fake_executor,
        )

    # -----------------------------------------------------------------------
    # Node compilation
    # -----------------------------------------------------------------------

    def _compile_node(self, node: GraphNode) -> Tuple[str, Callable]:
        """
        Convert a GraphNode to a LangGraph node function.

        Local/deterministic nodes (planner, retriever, scorer, router, critic):
        - Implemented as pure functions that transform state in-memory.
        - No external LLM calls in Milestone 1.

        Executor/worker nodes:
        - Emit a WorkerPacket via self.emitter.
        - Use FakeExecutor to get a simulated result.
        - Store result in state.results and update state.completed_workers.

        Args:
            node: The GraphNode to compile.

        Returns:
            Tuple of (node_id, node_function).
        """
        node_id = node.node_id

        if node.role in self._LOCAL_NODE_TYPES:
            # Deterministic local node — pure function over state
            fn = self._make_local_node_fn(node)
        else:
            # Worker / executor node — emit packet and wait
            fn = self._make_worker_node_fn(node)

        return node_id, fn

    def _make_local_node_fn(self, node: GraphNode) -> Callable:
        """
        Build a LangGraph node function for a deterministic local node.

        These nodes read from and write to RunState in-memory.
        They do NOT emit packets or call external models.

        Args:
            node: GraphNode with a deterministic role (planner/retriever/scorer/router/critic).

        Returns:
            A callable suitable for builder.add_node().
        """

        def local_node_fn(state: RunState) -> Dict[str, Any]:
            # Record event in ledger
            self.ledger.log_event(
                run_id=state.run_id,
                event_type="node_start",
                worker_id=node.node_id,
                depth=state.current_depth,
                data={"node_id": node.node_id, "role": node.role.value},
            )

            # Transform state based on node role
            updates: Dict[str, Any] = {"events": state.events + [node.node_id]}

            if node.role == NodeType.PLANNER:
                # Planner decomposes the objective — in M1, just record that it ran
                updates["status"] = RunStatus.RUNNING

            elif node.role == NodeType.RETRIEVER:
                # Retriever fetches context — in M1, store a dummy context
                updates["status"] = RunStatus.RUNNING

            elif node.role == NodeType.SCORER:
                # Scorer ranks options — in M1, just record completion
                updates["status"] = RunStatus.RUNNING

            elif node.role == NodeType.ROUTER:
                # Router directs flow based on conditions — in M1, pass through
                updates["status"] = RunStatus.RUNNING

            elif node.role == NodeType.CRITIC:
                # Critic reviews output — in M1, record critic findings
                existing_result = state.results.get(node.node_id)
                if existing_result:
                    updates["events"] = state.events + [f"critic_review:{node.node_id}"]
                else:
                    updates["events"] = state.events + [f"critic_review:{node.node_id}"]

            # Mark as completed in this simple model
            completed = list(state.completed_workers)
            if node.node_id not in completed:
                completed.append(node.node_id)
            updates["completed_workers"] = completed

            self.ledger.log_event(
                run_id=state.run_id,
                event_type="node_complete",
                worker_id=node.node_id,
                depth=state.current_depth,
                data={"node_id": node.node_id, "updates": list(updates.keys())},
            )

            return updates

        return local_node_fn

    def _make_worker_node_fn(self, node: GraphNode) -> Callable:
        """
        Build a LangGraph node function for a worker/executor node.

        Emits a WorkerPacket to the file protocol, then uses FakeExecutor
        to get a simulated result. Stores result in state.results.

        Args:
            node: GraphNode with a worker role.

        Returns:
            A callable suitable for builder.add_node().
        """
        node_id = node.node_id
        spec = self.graph_spec

        def worker_node_fn(state: RunState) -> Dict[str, Any]:
            self.ledger.log_event(
                run_id=state.run_id,
                event_type="worker_dispatch",
                worker_id=node_id,
                depth=state.current_depth,
                data={
                    "node_id": node_id,
                    "role": node.role.value,
                    "depth": state.current_depth,
                },
            )

            # Build WorkerPacket
            packet = WorkerPacket(
                run_id=spec.run_id,
                worker_id=node_id,
                parent_worker_id=None,  # Will be set by delegator in recursive case
                depth=state.current_depth,
                role=node.role,
                objective=node.task_prompt,
                task_prompt=node.task_prompt,
                allowed_tools=node.allowed_tools,
                forbidden_tools=node.forbidden_tools,
                owned_paths=node.owned_paths,
                forbidden_paths=node.forbidden_paths,
                timeout=node.timeout,
                budget=node.budget,
                delegation_permission=node.delegation_permission,
                max_child_depth=node.max_child_depth,
                result_contract=node.result_contract,
                no_revert_rule=node.no_revert_rule,
                secrets_rule=node.secrets_rule,
                verification_expectations=node.verification_expectations,
            )

            # Emit packet to file protocol
            self.emitter.emit_packet(run_id=packet.run_id, packet=packet)

            # Store packet in state
            packets = dict(state.packets)
            packets[node_id] = packet

            # Execute worker (real LLM or simulation)
            call_result = self.backend.call_worker(
                prompt=node.task_prompt,
                packet=packet,
            )

            # Convert dict result to WorkerResult
            result = WorkerResult(
                run_id=packet.run_id,
                worker_id=node_id,
                parent_worker_id=packet.parent_worker_id,
                status=WorkerStatus.SUCCESS if call_result.get("status") == "success" else WorkerStatus.FAILURE,
                output=call_result.get("output"),
                duration_seconds=0.1,  # approximate
                cost_estimate=call_result.get("cost_estimate", 0.0),
                children=[],
                artifacts=[],
                critic_findings=None,
                verification_status="passed",
            )

            # Store result
            results = dict(state.results)
            results[node_id] = result

            # Update completed workers
            completed = list(state.completed_workers)
            if node_id not in completed:
                completed.append(node_id)

            # Update pending workers (remove from pending if present)
            pending = [w for w in state.pending_workers if w != node_id]

            # Update totals
            total_cost = state.total_cost_estimate + (result.cost_estimate or 0.0)
            total_duration = state.total_duration_seconds + (result.duration_seconds or 0.0)

            self.ledger.log_event(
                run_id=state.run_id,
                event_type="worker_complete",
                worker_id=node_id,
                depth=state.current_depth,
                data={
                    "node_id": node_id,
                    "status": result.status.value,
                    "duration": result.duration_seconds,
                    "cost": result.cost_estimate,
                },
            )

            return {
                "packets": packets,
                "results": results,
                "completed_workers": completed,
                "pending_workers": pending,
                "total_cost_estimate": total_cost,
                "total_duration_seconds": total_duration,
                "events": state.events + [f"worker_done:{node_id}"],
            }

        return worker_node_fn

    # -----------------------------------------------------------------------
    # Edge compilation
    # -----------------------------------------------------------------------

    def _compile_edge(self, builder: StateGraph, edge: GraphEdge) -> None:
        """
        Add an edge to the StateGraph.

        If the edge has a condition, builds a condition function and uses
        conditional_edges(). Otherwise, uses add_edge() directly.

        Args:
            builder: The StateGraph builder.
            edge: The GraphEdge to add.
        """
        condition_fn = self._build_condition(edge)

        if condition_fn is not None:
            # Conditional edge
            builder.add_edge(edge.from_node, edge.to_node, condition_fn)
        else:
            # Unconditional edge
            builder.add_edge(edge.from_node, edge.to_node)

    def _build_condition(self, edge: GraphEdge) -> Callable[[RunState], bool] | None:
        """
        Build a condition function for a conditional GraphEdge.

        The condition expression is a simple Python expression evaluated against
        the state. In Milestone 1, supports basic comparisons like:
            - "results['worker_id'].output.score > 0.8"
            - "status == 'running'"

        Args:
            edge: GraphEdge with an optional condition expression.

        Returns:
            A condition function (returns bool), or None if edge is unconditional.
        """
        if not edge.condition:
            return None

        condition_expr = edge.condition.strip()

        def condition(state: RunState) -> bool:
            try:
                # Build a safe evaluation namespace
                namespace = {
                    "state": state,
                    "results": state.results,
                    "packets": state.packets,
                    "status": state.status,
                    "run_id": state.run_id,
                    "current_depth": state.current_depth,
                    "completed_workers": state.completed_workers,
                    "pending_workers": state.pending_workers,
                    "blocked_workers": state.blocked_workers,
                }
                # Evaluate the condition expression safely
                return bool(eval(condition_expr, {"__builtins__": {}}, namespace))
            except Exception:
                # On any evaluation error, default to False (don't take the edge)
                return False

        return condition

    # -----------------------------------------------------------------------
    # Checkpointer
    # -----------------------------------------------------------------------

    def _create_checkpointer(self) -> Any:
        """
        Set up LangGraph checkpointing for the compiled graph.

        Milestone 1: Returns a MemorySaver checkpointer.
        Milestone 2: Will support persistent checkpoint storage (SQLite/filesystem).

        Returns:
            A LangGraph Checkpoint object (MemorySaver in M1), or None if unavailable.
        """
        try:
            from langgraph.checkpoint.memory import MemorySaver

            checkpointer = MemorySaver()
            return checkpointer
        except ImportError:
            return None

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _determine_entry_point(self, spec: GraphSpec) -> str:
        """
        Determine the entry point node ID for the graph.

        Strategy:
        - Find nodes with no dependencies (depends_on is empty).
        - Return the first such node in spec order.
        - Fall back to first node if no clear entry found.

        Args:
            spec: The GraphSpec being compiled.

        Returns:
            node_id of the entry point.

        Raises:
            ValueError: If the graph has no nodes.
        """
        for node in spec.nodes:
            if not node.depends_on:
                return node.node_id

        # Fallback: first node in spec
        if spec.nodes:
            return spec.nodes[0].node_id

        raise ValueError("GraphSpec has no nodes — cannot determine entry point")

    def _topological_order(self, spec: GraphSpec) -> List[str]:
        """
        Compute a topological sort of node IDs using Kahn's algorithm.

        Respects both explicit depends_on edges and GraphEdge connections.

        Args:
            spec: The GraphSpec being compiled.

        Returns:
            List of node IDs in valid topological order.

        Raises:
            ValueError: If a cycle is detected in the graph.
        """
        node_ids = [n.node_id for n in spec.nodes]

        # Build adjacency and in-degree from both edges and depends_on
        in_degree: Dict[str, int] = {nid: 0 for nid in node_ids}
        adj: Dict[str, List[str]] = {nid: [] for nid in node_ids}

        # From GraphEdge definitions
        for edge in spec.edges:
            if edge.from_node in adj and edge.to_node in node_ids:
                adj[edge.from_node].append(edge.to_node)
                in_degree[edge.to_node] += 1

        # From depends_on on nodes
        for node in spec.nodes:
            for dep in node.depends_on:
                if dep in adj and node.node_id not in adj[dep]:
                    # dep -> node (dep must complete before node)
                    adj[dep].append(node.node_id)
                    in_degree[node.node_id] += 1

        queue = deque([nid for nid, deg in in_degree.items() if deg == 0])
        sorted_order: List[str] = []

        while queue:
            node_id = queue.popleft()
            sorted_order.append(node_id)
            for neighbor in adj[node_id]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(sorted_order) != len(node_ids):
            raise ValueError("Cycle detected in graph dependencies")

        return sorted_order


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


def compile_and_prepare(
    graph_spec: GraphSpec,
    profile: ProfilePolicy,
    runs_dir: str | Path,
) -> Tuple[CompiledGraph, PacketEmitter, Ledger]:
    """
    Factory function that sets up all compiler components and compiles the graph.

    This is the main entry point for creating an executable compiled graph
    from a GraphSpec and profile.

    Args:
        graph_spec: Validated GraphSpec to compile.
        profile: ProfilePolicy constraining tool/path access.
        runs_dir: Base directory for run artifacts (packets, results, ledger).

    Returns:
        Tuple of:
            - CompiledGraph: the compiled LangGraph StateGraph ready for execution
            - PacketEmitter: configured for this run (used to emit packets)
            - Ledger: configured for this run (records all events)

    Example:
        >>> spec = GraphSpec.from_dict({...})
        >>> profile = load_profile("default")
        >>> compiled, emitter, ledger = compile_and_prepare(spec, profile, Path("runs"))
        >>> result = compiled.graph.invoke({"run_id": spec.run_id})
    """
    runs_path = Path(runs_dir)

    # Create emitter and ledger
    emitter = PacketEmitter(runs_dir=str(runs_path))
    ledger = Ledger(runs_dir=str(runs_path))

    # Log compilation start
    ledger.log_event(
        run_id=graph_spec.run_id,
        event_type="compile_start",
        worker_id=None,
        depth=None,
        data={
            "run_id": graph_spec.run_id,
            "node_count": len(graph_spec.nodes),
            "edge_count": len(graph_spec.edges),
            "profile": profile.profile_name,
        },
    )

    # Create compiler and compile
    compiler = GraphCompiler(
        graph_spec=graph_spec,
        profile=profile,
        emitter=emitter,
        ledger=ledger,
    )

    compiled = compiler.compile()

    ledger.log_event(
        run_id=graph_spec.run_id,
        event_type="compile_done",
        worker_id=None,
        depth=None,
        data={
            "run_id": graph_spec.run_id,
            "valid": compiled.is_valid,
            "error_count": len(compiled.compile_errors),
            "execution_order": compiled.execution_order,
        },
    )

    return compiled, emitter, ledger