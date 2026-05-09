"""
State definitions for the Recursive Research Orchestra showcase.

Provides:
- OrchestratorState: Root state flowing through the compiled graph
- SeamState: Per-seam state (one per manager)
- ManagerState: State for manager nodes
- WorkerState: State for worker nodes

These are Pydantic models so they play nicely with LangGraph's state management.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field
from typing_extensions import Annotated


# ---------------------------------------------------------------------------
# Custom reducers for LangGraph Annotated channels
# ---------------------------------------------------------------------------

def _merge_dicts(a: Any, b: Any) -> Any:
    """Merge two dicts (used as Annotated reducer for findings channel)."""
    if isinstance(a, dict) and isinstance(b, dict):
        merged = dict(a)
        merged.update(b)
        return merged
    return b


def _append_list(a: Any, b: Any) -> Any:
    """Append b to a list, or replace if not a list (used for streaming_tokens, active_agents, etc)."""
    if isinstance(a, list):
        if isinstance(b, list):
            return a + b
        return a + [b]
    return b


# ---------------------------------------------------------------------------
# TokenBudget — per-seam spending guard
# ---------------------------------------------------------------------------


class TokenBudget:
    """
    Per-seam token and turn budget to prevent any single worker from
    exhausting the entire run's allocation.

    Workers check ``can_proceed()`` at the start of each turn. If the budget
    is exhausted they fall back to simulation rather than making a billable
    LLM call, preserving any partial results already accumulated.

    Attributes:
        max_tokens:  Maximum billable tokens (input + output) allowed for the
                     entire seam before ``can_proceed()`` returns False.
        max_turns:   Maximum number of LLM tool-use turns before
                     ``can_proceed()`` returns False.
        spent_tokens: Cumulative tokens spent so far (read-only intended —
                      use ``record_turn()`` to update).
        turn_count:  Number of LLM turns consumed (read-only — use
                     ``record_turn()`` to update).
    """

    def __init__(
        self,
        max_tokens: int = 100_000,
        max_turns: int = 10,
    ) -> None:
        self.max_tokens = max_tokens
        self.max_turns = max_turns
        self.spent_tokens = 0
        self.turn_count = 0

    def can_proceed(self) -> bool:
        """Return True only when both token and turn limits are still open."""
        return self.spent_tokens < self.max_tokens and self.turn_count < self.max_turns

    def record_turn(self, input_tokens: int, output_tokens: int) -> None:
        """Record tokens consumed by a single LLM turn."""
        self.spent_tokens += input_tokens + output_tokens
        self.turn_count += 1

    def snapshot(self) -> dict[str, int]:
        """Return a dict snapshot of current spending for logging."""
        return {
            "spent_tokens": self.spent_tokens,
            "turn_count": self.turn_count,
            "max_tokens": self.max_tokens,
            "max_turns": self.max_turns,
        }


# ---------------------------------------------------------------------------
# SeamState
# ---------------------------------------------------------------------------


class SeamState(BaseModel):
    """State for a single work seam, owned by a manager."""

    seam_id: str = Field(..., description="Unique seam identifier")
    description: str = Field("", description="Human-readable seam description")
    status: Literal["pending", "active", "completed", "failed"] = Field(
        default="pending", description="Current seam status"
    )
    manager_id: Optional[str] = Field(default=None, description="Manager node owning this seam")
    worker_ids: list[str] = Field(default_factory=list, description="Worker IDs spawned under this seam")
    result: Any = Field(default=None, description="Seam-level aggregated result")
    findings: dict[str, Any] = Field(default_factory=dict, description="Key findings from this seam")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Confidence score for seam result")


# ---------------------------------------------------------------------------
# ManagerState
# ---------------------------------------------------------------------------


class ManagerState(BaseModel):
    """State for a manager node that coordinates workers for a seam."""

    manager_id: str = Field(..., description="Unique manager identifier")
    seam_id: str = Field(..., description="Seam this manager owns")
    status: Literal["pending", "active", "waiting", "completed", "failed"] = Field(
        default="pending", description="Current manager status"
    )
    spawned_worker_ids: list[str] = Field(default_factory=list, description="Workers spawned by this manager")
    worker_results: dict[str, Any] = Field(default_factory=dict, description="worker_id -> result")
    aggregated_result: Any = Field(default=None, description="Aggregated output from all workers")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Confidence in aggregated result")


# ---------------------------------------------------------------------------
# WorkerState
# ---------------------------------------------------------------------------


class WorkerState(BaseModel):
    """State for an atomic worker node that executes a single task."""

    worker_id: str = Field(..., description="Unique worker identifier")
    parent_manager_id: str = Field(..., description="Manager that spawned this worker")
    seam_id: str = Field(..., description="Seam this worker belongs to")
    task_description: str = Field("", description="What this worker is doing")
    status: Literal["pending", "active", "completed", "failed"] = Field(
        default="pending", description="Current worker status"
    )
    result: Any = Field(default=None, description="Work result")
    tools_used: list[str] = Field(default_factory=list, description="Tools called during execution")
    artifacts: list[str] = Field(default_factory=list, description="File paths or URIs produced")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Confidence in result")


# ---------------------------------------------------------------------------
# OrchestratorState
# ---------------------------------------------------------------------------


class OrchestratorState(BaseModel):
    """
    Root state flowing through the compiled LangGraph.

    Tracks the overall goal, all seams/managers/workers, streaming tokens,
    human-in-the-loop gates, and final output.
    """

    run_id: str = Field(..., description="Unique run identifier")
    goal: str = Field(..., description="The user's original goal")
    mode: Literal["research", "build"] = Field(
        default="research", description="Execution mode derived from goal"
    )

    # Graph structure (built dynamically at runtime)
    seams: dict[str, SeamState] = Field(
        default_factory=dict, description="seam_id -> SeamState"
    )
    managers: dict[str, ManagerState] = Field(
        default_factory=dict, description="manager_id -> ManagerState"
    )
    active_agents: Annotated[list[str], _append_list] = Field(
        default_factory=list, description="Currently executing agent IDs"
    )
    completed_agents: Annotated[list[str], _append_list] = Field(
        default_factory=list, description="Agent IDs that finished"
    )

    # Results and findings
    findings: Annotated[dict[str, Any], _merge_dicts] = Field(
        default_factory=dict, description="seam_id -> aggregated findings"
    )
    final_output: Any = Field(default=None, description="Final compiled output")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Overall confidence")

    # Human-in-the-loop
    needs_human_review: bool = Field(
        default=False, description="Whether execution is paused for human review"
    )
    human_gate_name: Optional[str] = Field(
        default=None, description="Name of the pending human gate"
    )
    human_approved: Optional[bool] = Field(
        default=None, description="Human approval decision (True=approved, False=denied)"
    )

    # Streaming support
    streaming_tokens: Annotated[list[str], _append_list] = Field(
        default_factory=list, description="Tokens emitted for progressive display"
    )

    # Audit trail (appended in order)
    events: list[str] = Field(
        default_factory=list, description="Event IDs in the ledger"
    )

    # Routing
    next_node: Optional[str] = Field(
        default=None, description="Dynamic routing hint for conditional edges"
    )

    # Dynamically-added node registry (used by OrchestratorNode at runtime)
    dynamic_nodes: dict[str, str] = Field(
        default_factory=dict, description="node_id -> node_type for runtime-added nodes"
    )