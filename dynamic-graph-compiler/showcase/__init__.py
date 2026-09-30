"""
Recursive Research Orchestra — LangGraph Dynamic Showcase

A multi-agent LangGraph system demonstrating:
- Two-phase orchestration: an isolated Phase 1 graph produces a seam plan,
  then Phase 2 pre-builds a fixed graph (orchestrator, router, synthesis,
  human gate, per-seam managers/workers) and streams it to completion.
  All add_node calls happen before compile(); the topology is fixed once
  execution starts. (The experimental orchestrator_node_fn that mutates a
  builder from inside a node function is unused by the actual orchestrator.)
- Streaming execution with progressive token output
- Checkpointing with MemorySaver for replay/resume
- Conditional routing based on dynamic state
- Multi-agent coordination via shared OrchestratorState
- Tool binding per node role via MiniMax M2.7
- Human-in-the-loop via HumanGateNode
- WorkLedger append-only audit trail

Usage:
    from showcase import DynamicOrchestrator

    orchestrator = DynamicOrchestrator(runs_dir="runs")
    result = orchestrator.execute("Research LLM fine-tuning")
    for state in orchestrator.stream(compiled_graph, initial_state):
        print(state.streaming_tokens[-1], end="")
"""

from __future__ import annotations

from showcase.states import (
    OrchestratorState,
    SeamState,
    ManagerState,
    WorkerState,
)
from showcase.dynamic_orchestrator import (
    DynamicOrchestrator,
)
from showcase.nodes import (
    orchestrator_node_fn,
    orchestrator_phase1_fn,
    manager_node_fn,
    worker_node_fn,
    synthesis_node_fn,
    router_node_fn,
    human_gate_node_fn,
)

__all__ = [
    "OrchestratorState",
    "SeamState",
    "ManagerState",
    "WorkerState",
    "DynamicOrchestrator",
    "orchestrator_node_fn",
    "orchestrator_phase1_fn",
    "manager_node_fn",
    "worker_node_fn",
    "synthesis_node_fn",
    "router_node_fn",
    "human_gate_node_fn",
]