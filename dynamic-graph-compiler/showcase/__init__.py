"""
Recursive Research Orchestra — LangGraph Dynamic Showcase

A multi-agent LangGraph system demonstrating:
- Dynamic graph mutation at runtime (add_node from inside a node function)
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