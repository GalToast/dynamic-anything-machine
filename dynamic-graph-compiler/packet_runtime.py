"""
Packet Runtime: core orchestration API for the recursive work-packet system.

Provides:
- PacketRuntime: orchestrates packet lifecycle (create, claim, progress, delegate, complete, resume)
- FakeExecutor: simulated worker execution (no real model calls)
- run_goal: convenience function to run a goal end-to-end with FakeExecutor

Imports:
    work_packet: WorkPacket, HarnessType, PacketStatus, DelegationPermission, ShapeRef, ResumptionSnapshot
    work_ledger: WorkLedger, AuditEvent, PACKET_EMITTED, PACKET_CLAIMED, etc.
    shape_registry: ShapeRegistry, Shape, ShapeType
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

# Attempt to import the mmx-tools MCP search tool.
# If the MCP bridge is unavailable or the tool is not present, we fall back
# to a stub implementation so FakeExecutor can still demonstrate shape discovery.
try:
    from mmx_tools import mmx_search_query
    _MMX_SEARCH_AVAILABLE = True
except ImportError:
    _MMX_SEARCH_AVAILABLE = False

from work_packet import (
    WorkPacket,
    WorkResult,
    HarnessType,
    PacketStatus,
    DelegationPermission,
    ShapeRef,
    ResumptionSnapshot,
)
from work_ledger import (
    WorkLedger,
    AuditEvent,
    PACKET_CREATED,
    PACKET_EMITTED,
    PACKET_CLAIMED,
    PACKET_IN_PROGRESS,
    PACKET_PROGRESS,
    PACKET_PAUSED,
    PACKET_COMPLETED,
    PACKET_FAILED,
    PACKET_RESUMED,
    CHILD_REQUESTED,
    CHILD_APPROVED,
    CHILD_DENIED,
    HUMAN_GATE_ENTERED,
    HUMAN_GATE_APPROVED,
    HUMAN_GATE_DENIED,
    SHAPE_CREATED,
    ARTIFACT_WRITTEN,
    CHECKPOINT_CREATED,
    CHECKPOINT_RESTORED,
    RUN_COMPLETED,
    RUN_FAILED,
    ReplayEngine,
)
from shape_registry import ShapeRegistry, Shape, ShapeType


def _now_iso() -> str:
    """Return current UTC time as an ISO 8601 formatted string."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# PacketRuntime
# ---------------------------------------------------------------------------


class PacketRuntime:
    """
    Core orchestration API for the recursive work-packet runtime.

    Orchestrates packet lifecycle: create, claim, progress, delegate, complete,
    resume, and query. All state changes are logged to the WorkLedger for
    auditability and replay.

    Attributes:
        runs_dir: Base directory for all run data.
        ledger: WorkLedger instance for event logging.
        registry: ShapeRegistry instance for reusable shapes.
    """

    def __init__(self, runs_dir: Path | str | None = None) -> None:
        self.runs_dir = Path(runs_dir) if runs_dir else Path("runs")
        self.ledger = WorkLedger(runs_dir=self.runs_dir)
        self.registry = ShapeRegistry(shapes_dir=self.runs_dir / "shapes")

    # -------------------------------------------------------------------------
    # Goal creation
    # -------------------------------------------------------------------------

    def create_goal(
        self,
        objective: str,
        success_criteria: str = "completed",
        seam: str = "root",
    ) -> tuple[str, str]:
        """
        Create a new goal packet, returns (run_id, packet_id).

        Creates a new run and emits the root goal packet. The goal becomes
        the first packet in a new run.

        Args:
            objective: Natural-language description of the goal.
            success_criteria: How we know the goal is done.
            seam: Work seam this packet claims (default "root").

        Returns:
            Tuple of (run_id, packet_id) for the new goal.
        """
        run_id = str(uuid.uuid4())
        packet_id = run_id  # Root packet shares run_id

        # Create run directory structure
        self.ledger.create_run(run_id, objective)

        # Create root packet
        packet = WorkPacket(
            packet_id=packet_id,
            run_id=run_id,
            parent_packet_id=None,
            depth=0,
            seam=seam,
            objective=objective,
            success_criteria=success_criteria,
            created_at=_now_iso(),
            updated_at=_now_iso(),
        )

        # Write packet to disk
        self._write_packet(packet)

        # Log EMITTED event
        self.ledger.log_packet_emitted(
            run_id=run_id,
            packet_id=packet_id,
            depth=0,
            data={
                "objective": objective,
                "success_criteria": success_criteria,
                "seam": seam,
            },
        )

        return run_id, packet_id

    # -------------------------------------------------------------------------
    # Packet claiming
    # -------------------------------------------------------------------------

    def _get_packet_status(self, run_id: str, packet_id: str) -> PacketStatus:
        """
        Derive packet status from ledger events.

        Looks at the event history to determine the current status.
        """
        events = self.ledger.get_timeline(run_id)
        packet_events = [e for e in events if e.packet_id == packet_id]

        if not packet_events:
            return PacketStatus.CREATED

        # Check for terminal states first
        has_completed = any(e.event_type == PACKET_COMPLETED for e in packet_events)
        has_failed = any(e.event_type == PACKET_FAILED for e in packet_events)
        has_claimed = any(e.event_type == PACKET_CLAIMED for e in packet_events)
        has_emitted = any(e.event_type == PACKET_EMITTED for e in packet_events)
        has_paused = any(e.event_type == HUMAN_GATE_ENTERED for e in packet_events)
        has_resumed = any(e.event_type == PACKET_RESUMED for e in packet_events)

        if has_completed:
            return PacketStatus.COMPLETED
        if has_failed:
            return PacketStatus.FAILED
        if has_paused and not has_claimed:
            return PacketStatus.PAUSED
        if has_claimed:
            # Check if there's a later PROGRESS or COMPLETED event
            claimed_idx = None
            for i, e in enumerate(packet_events):
                if e.event_type == PACKET_CLAIMED:
                    claimed_idx = i
            if claimed_idx is not None:
                later_events = packet_events[claimed_idx + 1:]
                if any(e.event_type == PACKET_COMPLETED for e in later_events):
                    return PacketStatus.COMPLETED
                if any(e.event_type == PACKET_FAILED for e in later_events):
                    return PacketStatus.FAILED
                if any(e.event_type == HUMAN_GATE_ENTERED for e in later_events):
                    return PacketStatus.PAUSED
                return PacketStatus.IN_PROGRESS
            return PacketStatus.CLAIMED
        if has_emitted:
            return PacketStatus.EMITTED
        if has_resumed:
            return PacketStatus.RESUMED

        return PacketStatus.CREATED

    def claim_packet(
        self,
        packet_id: str,
        run_id: str | None,
        agent_id: str,
        harness: HarnessType,
    ) -> str:
        """
        Claim ownership of a packet for an agent.

        Args:
            packet_id: ID of the packet to claim.
            run_id: Run ID (if None, searches across all runs).
            agent_id: Identifier of the claiming agent.
            harness: Type of harness the agent is running under.

        Returns:
            event_id of the CLAIMED event.

        Raises:
            FileNotFoundError: If packet not found.
            ValueError: If packet is not in EMITTED state.
        """
        run_id, packet = self._resolve_packet(packet_id, run_id)

        status = self._get_packet_status(run_id, packet_id)
        if status != PacketStatus.EMITTED and status != PacketStatus.RESUMED:
            raise ValueError(
                f"Packet {packet_id} is {status.value}, not claimable "
                f"(must be EMITTED or RESUMED)"
            )

        # Update packet
        packet.claimed_by = agent_id
        packet.claimed_by_harness = harness
        packet.claimed_at = _now_iso()
        packet.updated_at = _now_iso()

        # Write updated packet
        self._write_packet(packet)

        # Log event
        event_id = self.ledger.log_packet_claimed(
            run_id=run_id,
            packet_id=packet_id,
            agent_id=agent_id,
            harness=harness.value,
        )

        # Add event to audit trail
        packet.add_audit_event(event_id)
        self._write_packet(packet)

        return event_id

    def list_seams(self, run_id: str | None = None) -> list[dict[str, Any]]:
        """
        List open (EMITTED) packets available for claiming.

        Args:
            run_id: If provided, scope to this run only. If None, search all runs.

        Returns:
            List of dicts with run_id, packet_id, seam, depth, created_at.
        """
        return self.ledger.list_seams(run_id=run_id)

    # -------------------------------------------------------------------------
    # Progress and checkpoints
    # -------------------------------------------------------------------------

    def progress_packet(
        self,
        packet_id: str,
        run_id: str | None,
        state_update: dict[str, Any],
        artifacts: list[str] | None = None,
    ) -> str:
        """
        Checkpoint packet state progress.

        Merges the state_update into the packet's current_state and logs
        a PROGRESS event.

        Args:
            packet_id: ID of the packet to update.
            run_id: Run ID (if None, resolves automatically).
            state_update: Dictionary of state keys/values to merge.
            artifacts: Optional list of artifact paths produced.

        Returns:
            event_id of the logged PROGRESS event.
        """
        run_id, packet = self._resolve_packet(packet_id, run_id)

        # Merge state update
        packet.progress(state_update)
        packet.updated_at = _now_iso()

        # Log artifacts if provided
        if artifacts:
            for artifact_path in artifacts:
                self.ledger.log_artifact_written(run_id, packet_id, artifact_path)
                if artifact_path not in packet.artifacts_produced:
                    packet.artifacts_produced.append(artifact_path)

        # Write updated packet
        self._write_packet(packet)

        # Log progress event
        event_id = self.ledger.log_progress(run_id, packet_id, state_update)
        packet.add_audit_event(event_id)
        self._write_packet(packet)

        return event_id

    def checkpoint_packet(
        self,
        packet_id: str,
        run_id: str | None,
        label: str,
    ) -> str:
        """
        Create a named checkpoint snapshot for resumption.

        Args:
            packet_id: ID of the packet to checkpoint.
            run_id: Run ID (if None, resolves automatically).
            label: Human-readable label for this checkpoint.

        Returns:
            checkpoint_id of the created checkpoint.
        """
        run_id, packet = self._resolve_packet(packet_id, run_id)

        checkpoint_id = str(uuid.uuid4())

        # Create resumption snapshot
        snapshot = packet.checkpoint(packet.current_state, label)
        snapshot.snapshot_id = checkpoint_id

        # Save checkpoint to disk
        checkpoints_dir = self.runs_dir / run_id / "checkpoints"
        checkpoints_dir.mkdir(parents=True, exist_ok=True)

        checkpoint_path = checkpoints_dir / f"{checkpoint_id}.json"
        with open(checkpoint_path, "w", encoding="utf-8") as f:
            json.dump(snapshot.model_dump(mode="json"), f, indent=2)

        # Log checkpoint event
        event_id = self.ledger.log_checkpoint(
            run_id, packet_id, checkpoint_id, label
        )
        packet.add_audit_event(event_id)
        self._write_packet(packet)

        return checkpoint_id

    # -------------------------------------------------------------------------
    # Delegation
    # -------------------------------------------------------------------------

    def delegate_packet(
        self,
        packet_id: str,
        run_id: str | None,
        seam: str,
        objective: str | None = None,
        constraints: dict[str, Any] | None = None,
    ) -> tuple[str, bool]:
        """
        Request delegation to create a child packet.

        Creates a child packet with the specified seam and delegates from the
        parent. Whether the child is immediately emitted or stays pending
        depends on the parent's delegation_permission.

        Args:
            packet_id: ID of the parent packet.
            run_id: Run ID (if None, resolves automatically).
            seam: Work seam for the child packet.
            objective: Optional objective override for the child.
            constraints: Optional constraints for the child.

        Returns:
            Tuple of (child_packet_id, is_pending) where is_pending indicates
            whether the child needs approval before being emitted.
        """
        run_id, parent = self._resolve_packet(packet_id, run_id)

        child_packet_id = str(uuid.uuid4())
        child_depth = parent.depth + 1

        # Check delegation permission
        if parent.delegation_permission == DelegationPermission.NONE:
            raise ValueError(
                f"Packet {packet_id} has delegation_permission=NONE, cannot delegate"
            )

        if parent.max_child_depth <= 0:
            raise ValueError(
                f"Packet {packet_id} has max_child_depth=0, cannot delegate further"
            )

        # Determine if approval is needed
        needs_approval = parent.delegation_permission == DelegationPermission.REQUEST

        # Child inherits and narrows parent constraints
        child_objective = objective or f"Child work for seam: {seam}"
        child_constraints = constraints or {}

        # Build child packet
        child = WorkPacket(
            packet_id=child_packet_id,
            run_id=run_id,
            parent_packet_id=packet_id,
            depth=child_depth,
            seam=seam,
            objective=child_objective,
            success_criteria=parent.success_criteria,
            current_state=dict(parent.current_state),
            known_context=dict(parent.known_context),
            allowed_tools=child_constraints.get("allowed_tools", parent.allowed_tools[:]),
            forbidden_tools=child_constraints.get("forbidden_tools", parent.forbidden_tools[:]),
            owned_paths=child_constraints.get("owned_paths", parent.owned_paths[:]),
            forbidden_paths=child_constraints.get("forbidden_paths", parent.forbidden_paths[:]),
            timeout_seconds=child_constraints.get(
                "timeout_seconds", parent.timeout_seconds
            ),
            budget=child_constraints.get("budget", parent.budget),
            delegation_permission=DelegationPermission.REQUEST
            if needs_approval
            else DelegationPermission.APPROVE,
            max_child_depth=max(0, parent.max_child_depth - 1),
            created_at=_now_iso(),
            updated_at=_now_iso(),
        )

        # Write child packet
        self._write_packet(child)

        # Log CHILD_REQUESTED event
        self.ledger.log_child_requested(
            run_id=run_id,
            parent_packet_id=packet_id,
            child_packet_id=child_packet_id,
            seam=seam,
        )

        parent.add_audit_event(child_packet_id)
        self._write_packet(parent)

        return child_packet_id, needs_approval

    def approve_child(
        self,
        run_id: str | None,
        child_packet_id: str,
        approver: str = "human",
    ) -> str:
        """
        Approve a pending child packet, emitting it for claiming.

        Args:
            run_id: Run ID (if None, resolves automatically).
            child_packet_id: ID of the child packet to approve.
            approver: Identifier of the approver.

        Returns:
            event_id of the CHILD_APPROVED event.
        """
        run_id, child = self._resolve_packet(child_packet_id, run_id)

        status = self._get_packet_status(run_id, child_packet_id)
        if status != PacketStatus.CREATED:
            raise ValueError(
                f"Child packet {child_packet_id} is {status.value}, "
                f"not pending approval"
            )

        # Update child (status is derived from events, not stored on packet)
        child.updated_at = _now_iso()
        self._write_packet(child)

        # Log EMITTED event for the child
        self.ledger.log_packet_emitted(
            run_id=run_id,
            packet_id=child_packet_id,
            depth=child.depth,
            data={
                "objective": child.objective,
                "seam": child.seam,
                "parent_packet_id": child.parent_packet_id,
            },
        )

        # Log CHILD_APPROVED event
        event_id = self.ledger.log_child_approved(run_id, child_packet_id)
        return event_id

    def deny_child(
        self,
        run_id: str | None,
        child_packet_id: str,
        reason: str,
    ) -> str:
        """
        Deny a pending child packet.

        Args:
            run_id: Run ID (if None, resolves automatically).
            child_packet_id: ID of the child packet to deny.
            reason: Reason for denial.

        Returns:
            event_id of the CHILD_DENIED event.
        """
        run_id, child = self._resolve_packet(child_packet_id, run_id)

        status = self._get_packet_status(run_id, child_packet_id)
        if status != PacketStatus.CREATED:
            raise ValueError(
                f"Child packet {child_packet_id} is {status.value}, "
                f"not pending approval"
            )

        # Update child (status is derived from events)
        child.updated_at = _now_iso()
        child.current_state["denial_reason"] = reason
        self._write_packet(child)

        # Log CHILD_DENIED event
        event_id = self.ledger.log_child_denied(run_id, child_packet_id, reason)
        return event_id

    # -------------------------------------------------------------------------
    # Human gates
    # -------------------------------------------------------------------------

    def human_gate(
        self,
        packet_id: str,
        run_id: str | None,
        name: str,
    ) -> str:
        """
        Pause a packet at a human approval gate.

        Args:
            packet_id: ID of the packet to pause.
            run_id: Run ID (if None, resolves automatically).
            name: Name/description of the gate.

        Returns:
            event_id of the HUMAN_GATE_ENTERED event.
        """
        run_id, packet = self._resolve_packet(packet_id, run_id)

        # Update packet (status is derived from events)
        packet.updated_at = _now_iso()
        packet.open_questions.append(f"human_gate:{name}")
        self._write_packet(packet)

        # Log event
        event_id = self.ledger.log_human_gate(run_id, packet_id, name)
        return event_id

    def resolve_human_gate(
        self,
        run_id: str | None,
        packet_id: str,
        name: str,
        approved: bool,
        approver: str = "human",
    ) -> str:
        """
        Resolve (approve or deny) a human gate.

        Args:
            run_id: Run ID (if None, resolves automatically).
            packet_id: ID of the packet with the gate.
            name: Name of the gate to resolve.
            approved: True to approve, False to deny.
            approver: Identifier of the approver.

        Returns:
            event_id of the HUMAN_GATE_APPROVED or HUMAN_GATE_DENIED event.
        """
        run_id, packet = self._resolve_packet(packet_id, run_id)

        # Update packet (status is derived from events)
        if not approved:
            packet.current_state["gate_denied"] = name

        # Remove from open questions
        packet.open_questions = [
            q for q in packet.open_questions if q != f"human_gate:{name}"
        ]
        packet.updated_at = _now_iso()
        self._write_packet(packet)

        # Log decision
        event_id = self.ledger.log_human_decision(
            run_id, packet_id, name, approved, approver
        )
        return event_id

    # -------------------------------------------------------------------------
    # Completion
    # -------------------------------------------------------------------------

    def complete_packet(
        self,
        packet_id: str,
        run_id: str | None,
        output: str,
        shapes: list[dict[str, Any]] | None = None,
        critic: str | None = None,
    ) -> str:
        """
        Mark a packet as completed with output.

        Args:
            packet_id: ID of the packet to complete.
            run_id: Run ID (if None, resolves automatically).
            output: Human-readable output/result.
            shapes: Optional list of shape dicts discovered during execution.
            critic: Optional critic findings.

        Returns:
            event_id of the PACKET_COMPLETED event.
        """
        run_id, packet = self._resolve_packet(packet_id, run_id)

        # Update packet (status is derived from events)
        packet.completed_at = _now_iso()
        packet.updated_at = _now_iso()
        packet.current_state["output"] = output
        if critic:
            packet.current_state["critic_findings"] = critic

        # Save shapes if provided
        if shapes:
            for shape_data in shapes:
                shape = Shape.from_dict(shape_data)
                shape.created_from_packet_id = packet_id
                shape_id = self.registry.save_shape(shape)
                packet.discover_shape(ShapeRef(shape_id=shape_id, shape_type=shape.shape_type, name=shape.name, tags=shape.tags, created_from_packet_id=packet_id))
                self.ledger.log_shape_created(
                    run_id=run_id,
                    packet_id=packet_id,
                    shape_id=shape_id,
                    shape_type=shape.shape_type.value if hasattr(shape.shape_type, 'value') else str(shape.shape_type),
                    data=shape_data,
                )

        # Write final output
        final_dir = self.runs_dir / run_id / "final"
        final_dir.mkdir(parents=True, exist_ok=True)
        final_path = final_dir / f"{packet_id}.md"
        with open(final_path, "w", encoding="utf-8") as f:
            f.write(f"# Packet Output: {packet_id}\n\n")
            f.write(f"**Seam:** {packet.seam}\n")
            f.write(f"**Completed:** {_now_iso()}\n\n")
            f.write(f"## Output\n\n{output}\n")

        # Write updated packet
        self._write_packet(packet)

        # Log completion event
        event_id = self.ledger.log_event(
            run_id=run_id,
            event_type=PACKET_COMPLETED,
            packet_id=packet_id,
            agent_id=packet.claimed_by,
            harness=packet.claimed_by_harness.value if hasattr(packet.claimed_by_harness, 'value') else str(packet.claimed_by_harness),
            depth=packet.depth,
            data={
                "output": output,
                "shapes_count": len(shapes) if shapes else 0,
                "critic": critic,
            },
        )

        # Check if this completes the run
        self._check_run_completion(run_id)

        return event_id

    def fail_packet(
        self,
        packet_id: str,
        run_id: str | None,
        error: str,
    ) -> str:
        """
        Mark a packet as failed.

        Args:
            packet_id: ID of the packet to fail.
            run_id: Run ID (if None, resolves automatically).
            error: Error message or description.

        Returns:
            event_id of the PACKET_FAILED event.
        """
        run_id, packet = self._resolve_packet(packet_id, run_id)

        # Update packet (status is derived from events)
        packet.completed_at = _now_iso()
        packet.updated_at = _now_iso()
        packet.current_state["error"] = error

        # Write updated packet
        self._write_packet(packet)

        # Log error event
        self.ledger.log_error(run_id, packet_id, error)

        # Log failed event
        event_id = self.ledger.log_event(
            run_id=run_id,
            event_type=PACKET_FAILED,
            packet_id=packet_id,
            agent_id=packet.claimed_by,
            harness=packet.claimed_by_harness.value if hasattr(packet.claimed_by_harness, 'value') else str(packet.claimed_by_harness),
            depth=packet.depth,
            data={"error": error},
        )

        # Log run failed if this is root
        if packet.parent_packet_id is None:
            self.ledger.log_run_failed(run_id, error)

        return event_id

    # -------------------------------------------------------------------------
    # Resumption
    # -------------------------------------------------------------------------

    def resume_packet(
        self,
        packet_id: str,
        run_id: str | None,
    ) -> tuple[str, WorkPacket]:
        """
        Re-emit a paused/failed packet for claiming.

        Args:
            packet_id: ID of the packet to resume.
            run_id: Run ID (if None, resolves automatically).

        Returns:
            Tuple of (event_id, packet) for the RESUMED event and updated packet.
        """
        run_id, packet = self._resolve_packet(packet_id, run_id)

        status = self._get_packet_status(run_id, packet_id)
        if status not in (PacketStatus.PAUSED, PacketStatus.FAILED):
            raise ValueError(
                f"Packet {packet_id} is {status.value}, cannot resume "
                f"(must be PAUSED or FAILED)"
            )

        # Re-emit the packet (status is derived from events)
        packet.updated_at = _now_iso()
        self._write_packet(packet)

        # Log event
        event_id = self.ledger.log_packet_emitted(
            run_id=run_id,
            packet_id=packet_id,
            depth=packet.depth,
            data={
                "objective": packet.objective,
                "seam": packet.seam,
                "resumed": True,
            },
        )

        return event_id, packet

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _resolve_packet(
        self, packet_id: str, run_id: str | None
    ) -> tuple[str, WorkPacket]:
        """
        Resolve a packet_id to (run_id, WorkPacket).

        If run_id is None, searches across all runs to find the packet.

        Raises:
            FileNotFoundError: If packet not found.
        """
        if run_id:
            packet = self._read_packet(run_id, packet_id)
            return run_id, packet

        # Search across all runs
        if not self.runs_dir.exists():
            raise FileNotFoundError(f"No runs directory found at {self.runs_dir}")

        for run_dir in self.runs_dir.iterdir():
            if not run_dir.is_dir():
                continue
            packet_path = run_dir / "packets" / f"{packet_id}.json"
            if packet_path.exists():
                run_id = run_dir.name
                with open(packet_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return run_id, WorkPacket.from_dict(data)

        raise FileNotFoundError(f"Packet {packet_id} not found in any run")

    def _read_packet(self, run_id: str, packet_id: str) -> WorkPacket:
        """Read a packet from disk."""
        packet_path = self.runs_dir / run_id / "packets" / f"{packet_id}.json"
        if not packet_path.exists():
            raise FileNotFoundError(f"Packet {packet_id} not found in run {run_id}")
        with open(packet_path, "r", encoding="utf-8") as f:
            return WorkPacket.from_dict(json.load(f))

    def _write_packet(self, packet: WorkPacket) -> None:
        """Write a packet to disk."""
        packet_dir = self.runs_dir / packet.run_id / "packets"
        packet_dir.mkdir(parents=True, exist_ok=True)
        packet_path = packet_dir / f"{packet.packet_id}.json"
        with open(packet_path, "w", encoding="utf-8") as f:
            json.dump(packet.to_dict(), f, indent=2)

    def _check_run_completion(self, run_id: str) -> None:
        """Check if all packets in a run are complete, and close the run."""
        events = self.ledger.get_timeline(run_id)

        # Find root packet
        root_packet_id = None
        for event in events:
            if event.event_type == PACKET_CREATED and event.packet_id:
                root_packet_id = event.packet_id
                break

        if root_packet_id is None:
            return

        # Check if root is completed
        root_completed = any(
            e.event_type == PACKET_COMPLETED and e.packet_id == root_packet_id
            for e in events
        )
        root_failed = any(
            e.event_type == PACKET_FAILED and e.packet_id == root_packet_id
            for e in events
        )

        if root_completed or root_failed:
            # Log run completion
            event_type = RUN_COMPLETED if root_completed else RUN_FAILED
            summary = {
                "root_packet_id": root_packet_id,
                "total_events": len(events),
            }
            if root_completed:
                self.ledger.log_run_completed(run_id, summary)
            else:
                self.ledger.log_run_failed(run_id, "Root packet failed")


# ---------------------------------------------------------------------------
# HarnessAdapter — pluggable agent execution backend
# ---------------------------------------------------------------------------

class HarnessAdapter(ABC):
    """
    Abstract base for agent execution backends.

    Subclasses translate a WorkPacket into an actual agent invocation and
    return a WorkResult. The harness reads packets from disk and writes
    results back; callers use execute() to get a structured WorkResult.

    Environment variables consulted by concrete adapters:

        HARNESS_TYPE  — one of: fake, claude-code, http-api (default: fake)
        API_KEY       — API key for http-api and claude-code harnesses
        MODEL         — model name (e.g. claude-sonnet-4-20250514)
        BASE_URL      — base URL for http-api harness
    """

    def __init__(self, ledger: WorkLedger | None = None) -> None:
        self.ledger = ledger

    def execute(self, packet: WorkPacket) -> WorkResult:
        """
        Execute a packet and return a structured WorkResult.

        Handles timing and error framing; subclasses override _do_execute()
        to provide the actual agent call.

        Args:
            packet: The WorkPacket dispatched to this worker.

        Returns:
            WorkResult with status, output, artifacts, shapes, etc.
        """
        import time

        start = time.monotonic()
        try:
            return self._do_execute(packet)
        except Exception as exc:  # pragma: no cover — concrete adapters handle errors
            return WorkResult(
                packet_id=packet.packet_id,
                run_id=packet.run_id,
                status=PacketStatus.FAILED,
                error=str(exc),
                duration_seconds=round(time.monotonic() - start, 3),
            )

    @abstractmethod
    def _do_execute(self, packet: WorkPacket) -> WorkResult:
        """
        Subclasses override this to invoke the actual agent.

        Args:
            packet: The WorkPacket to execute.

        Returns:
            WorkResult describing the outcome.
        """
        raise NotImplementedError


class FakeExecutor(HarnessAdapter):
    """
    Simulated worker executor for testing and Milestone 1.

    Returns deterministic fake results without calling any external LLM.
    Kept as the default when HARNESS_TYPE=fake or not set.
    """

    def __init__(
        self,
        ledger: WorkLedger | None = None,
        web_search_tool: WebSearchTool | None = None,
    ) -> None:
        super().__init__(ledger)
        self.web_search_tool = web_search_tool or WebSearchTool()
        self._call_log: list[dict[str, Any]] = []

    def _do_execute(self, packet: WorkPacket) -> WorkResult:
        import random

        objective_snippet = (
            packet.objective[:50] + "..."
            if len(packet.objective) > 50
            else packet.objective
        )

        duration = random.uniform(0.05, 0.5)

        result = WorkResult(
            packet_id=packet.packet_id,
            run_id=packet.run_id,
            status=PacketStatus.COMPLETED,
            output=f"Simulated result for {packet.packet_id}: {objective_snippet}",
            duration_seconds=round(duration, 3),
            cost_estimate=0.0,
        )

        self._call_log.append({
            "packet_id": packet.packet_id,
            "seam": packet.seam,
            "duration": duration,
            "status": "success",
        })

        return result

    @property
    def call_count(self) -> int:
        """Number of execute() calls made."""
        return len(self._call_log)


class ClaudeCodeHarness(HarnessAdapter):
    """
    Harness that invokes the ``claude`` CLI as a subprocess.

    Reads the packet JSON from a temporary file and passes the path to
    ``claude --print``. The subprocess output is parsed as a JSON result.
    """

    def __init__(
        self,
        ledger: WorkLedger | None = None,
        claude_bin: str | None = None,
    ) -> None:
        super().__init__(ledger)
        self.claude_bin = claude_bin or os.environ.get("CLAUDE_BIN", "claude")

    def _do_execute(self, packet: WorkPacket) -> WorkResult:
        import subprocess
        import time

        packet_json = packet.model_dump(mode="json")
        start = time.monotonic()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as tf:
            json.dump(packet_json, tf)
            packet_path = tf.name

        try:
            env = os.environ.copy()
            api_key = os.environ.get("API_KEY", "")
            if api_key:
                env["API_KEY"] = api_key

            proc = subprocess.run(
                [self.claude_bin, "--print"],
                input=json.dumps({"packet": packet_json}),
                capture_output=True,
                text=True,
                env=env,
                timeout=packet.timeout_seconds or 300,
            )

            duration = time.monotonic() - start

            if proc.returncode != 0:
                return WorkResult(
                    packet_id=packet.packet_id,
                    run_id=packet.run_id,
                    status=PacketStatus.FAILED,
                    error=proc.stderr or f"Exit code {proc.returncode}",
                    duration_seconds=round(duration, 3),
                )

            return self._parse_result(packet, proc.stdout, duration)
        finally:
            Path(packet_path).unlink(missing_ok=True)

    def _parse_result(
        self, packet: WorkPacket, stdout: str, duration: float
    ) -> WorkResult:
        """Parse stdout as a JSON WorkResult (or WorkResult-compatible dict)."""
        try:
            data = json.loads(stdout)
            if isinstance(data, dict):
                return WorkResult(
                    packet_id=packet.packet_id,
                    run_id=packet.run_id,
                    status=PacketStatus(data.get("status", "completed")),
                    output=data.get("output"),
                    error=data.get("error"),
                    artifacts_produced=data.get("artifacts", []),
                    shapes_discovered=data.get("shapes", []),
                    duration_seconds=round(duration, 3),
                    cost_estimate=data.get("cost_estimate"),
                    children=data.get("children", []),
                    critic_findings=data.get("critic_findings"),
                )
        except json.JSONDecodeError:
            pass

        return WorkResult(
            packet_id=packet.packet_id,
            run_id=packet.run_id,
            status=PacketStatus.COMPLETED,
            output=stdout.strip(),
            duration_seconds=round(duration, 3),
        )


class HttpApiHarness(HarnessAdapter):
    """
    Generic HTTP client harness for Claude API / OpenAI-compatible endpoints.

    Environment variables::

        HARNESS_TYPE=http-api
        API_KEY=<key>                     # API authentication
        MODEL=claude-sonnet-4-20250514    # model name (default)
        BASE_URL=https://api.anthropic.com # endpoint base (default)
        TIMEOUT=300                       # request timeout in seconds (default)

    Sends a POST to ``<BASE_URL>/v1/messages`` with the packet as JSON.
    """

    def __init__(
        self,
        ledger: WorkLedger | None = None,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: int | None = None,
    ) -> None:
        super().__init__(ledger)
        self.api_key = api_key or os.environ.get("API_KEY", "")
        self.model = model or os.environ.get("MODEL", "claude-sonnet-4-20250514")
        self.base_url = base_url or os.environ.get(
            "BASE_URL", "https://api.anthropic.com"
        )
        self.timeout = timeout or int(os.environ.get("TIMEOUT", "300"))

    def _do_execute(self, packet: WorkPacket) -> WorkResult:
        import time
        import urllib.error
        import urllib.request

        packet_json = packet.model_dump(mode="json")
        start = time.monotonic()

        payload = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": [
                {
                    "role": "user",
                    "content": self._build_prompt(packet),
                }
            ],
        }

        body = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            f"{self.base_url}/v1/messages",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "anthropic-dangerous-direct-browser-access": "true",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                response_data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            duration = time.monotonic() - start
            error_body = exc.read().decode("utf-8") if exc.fp else str(exc)
            return WorkResult(
                packet_id=packet.packet_id,
                run_id=packet.run_id,
                status=PacketStatus.FAILED,
                error=f"HTTP {exc.code}: {error_body}",
                duration_seconds=round(duration, 3),
            )
        except urllib.error.URLError as exc:
            duration = time.monotonic() - start
            return WorkResult(
                packet_id=packet.packet_id,
                run_id=packet.run_id,
                status=PacketStatus.FAILED,
                error=f"Network error: {exc.reason}",
                duration_seconds=round(duration, 3),
            )

        duration = time.monotonic() - start
        return self._parse_response(packet, response_data, duration)

    def _build_prompt(self, packet: WorkPacket) -> str:
        """Build the user prompt from the packet seam and context."""
        lines = [
            f"# Work Packet: {packet.packet_id}",
            f"## Seam: {packet.seam}",
            f"## Objective: {packet.objective}",
            f"## Success Criteria: {packet.success_criteria}",
        ]

        if packet.open_questions:
            lines.append(
                f"## Open Questions: {', '.join(packet.open_questions)}"
            )

        if packet.allowed_tools:
            lines.append(
                f"## Allowed Tools: {', '.join(packet.allowed_tools)}"
            )

        if packet.forbidden_tools:
            lines.append(
                f"## Forbidden Tools: {', '.join(packet.forbidden_tools)}"
            )

        if packet.current_state:
            lines.append(
                f"## Current State: {json.dumps(packet.current_state)}"
            )

        return "\n\n".join(lines)

    def _parse_response(
        self, packet: WorkPacket, response: dict[str, Any], duration: float
    ) -> WorkResult:
        """Parse an Anthropic messages API response into a WorkResult."""
        try:
            content_blocks = response.get("content", [])
            if isinstance(content_blocks, list) and content_blocks:
                block = content_blocks[0]
                output_text = (
                    block.get("text", "") if isinstance(block, dict) else str(block)
                )
            else:
                output_text = str(content_blocks)

            usage = response.get("usage", {})
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            # Rough cost estimate (Anthropic pricing, approximate)
            cost = (
                (input_tokens * 0.000003 + output_tokens * 0.000015)
                if usage
                else None
            )

            return WorkResult(
                packet_id=packet.packet_id,
                run_id=packet.run_id,
                status=PacketStatus.COMPLETED,
                output=output_text,
                duration_seconds=round(duration, 3),
                cost_estimate=cost,
                children=[],
                artifacts_produced=[],
            )
        except Exception as exc:
            return WorkResult(
                packet_id=packet.packet_id,
                run_id=packet.run_id,
                status=PacketStatus.FAILED,
                error=f"Failed to parse API response: {exc}",
                duration_seconds=round(duration, 3),
            )


class OpenRouterHarness(HttpApiHarness):
    """Harness that calls OpenRouter API (free tier models available)."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "openrouter/auto",
        **kwargs,
    ):
        import os

        key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        super().__init__(
            base_url="https://openrouter.ai/api/v1",
            api_key=key,
            model=model,
            **kwargs,
        )


class QwenHarness(HarnessAdapter):
    """Harness that calls Qwen CLI or API."""

    def _do_execute(self, packet: WorkPacket) -> WorkResult:
        import subprocess
        import os

        # Try qwen CLI first (uses BAILIAN_CODING_PLAN_API_KEY internally)
        try:
            result = subprocess.run(
                ["qwen", "-p", self._build_prompt(packet), "--output", "json"],
                capture_output=True,
                text=True,
                timeout=packet.timeout_seconds or 60,
            )
            if result.returncode == 0:
                return self._parse_response(result.stdout)
        except FileNotFoundError:
            pass

        # Fall back to API if a key is available
        # BAILIAN_CODING_PLAN_API_KEY is what qwen CLI uses internally
        # ALIBABA_CODING_API_KEY is the env var this machine already has set
        api_key = (
            os.environ.get("BAILIAN_CODING_PLAN_API_KEY", "")
            or os.environ.get("ALIBABA_CODING_API_KEY", "")
        )
        if api_key:
            return self._call_api(packet)

        return WorkResult(
            packet_id=packet.packet_id,
            run_id=packet.run_id,
            status=PacketStatus.FAILED,
            error="Qwen CLI not found and no API key set (BAILIAN_CODING_PLAN_API_KEY or ALIBABA_CODING_API_KEY)",
        )

    def _parse_response(self, stdout: str) -> WorkResult:
        import time

        try:
            data = json.loads(stdout)
            if isinstance(data, dict):
                return WorkResult(
                    packet_id=data.get("packet_id", ""),
                    run_id=data.get("run_id", ""),
                    status=PacketStatus(data.get("status", "completed")),
                    output=data.get("output"),
                    error=data.get("error"),
                    duration_seconds=data.get("duration_seconds", 0.0),
                )
        except json.JSONDecodeError:
            pass

        return WorkResult(
            packet_id=self.packet_id if hasattr(self, "packet_id") else "",
            run_id=self.run_id if hasattr(self, "run_id") else "",
            status=PacketStatus.COMPLETED,
            output=stdout.strip(),
            duration_seconds=0.0,
        )

    def _call_api(self, packet: WorkPacket) -> WorkResult:
        import time
        import urllib.error
        import urllib.request
        import os

        api_key = (
            os.environ.get("QWEN_API_KEY", "")
            or os.environ.get("BAILIAN_CODING_PLAN_API_KEY", "")
            or os.environ.get("ALIBABA_CODING_API_KEY", "")
        )
        # qwen3.6-plus is the default model for Alibaba Coding Plan
        model = os.environ.get("QWEN_MODEL", "qwen3.6-plus")
        base_url = os.environ.get(
            "QWEN_BASE_URL",
            "https://coding-intl.dashscope.aliyuncs.com/v1"
        )

        packet_json = packet.model_dump(mode="json")
        start = time.monotonic()

        # Use OpenAI-compatible chat completions endpoint (correct for Coding Plan)
        payload = {
            "model": model,
            "messages": [
                {"role": "user", "content": self._build_prompt(packet)}
            ],
            "max_tokens": 2048,
            "temperature": 0.7,
        }

        body = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=packet.timeout_seconds or 60) as resp:
                response_data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            duration = time.monotonic() - start
            error_body = exc.read().decode("utf-8") if exc.fp else str(exc)
            return WorkResult(
                packet_id=packet.packet_id,
                run_id=packet.run_id,
                status=PacketStatus.FAILED,
                error=f"HTTP {exc.code}: {error_body}",
                duration_seconds=round(duration, 3),
            )
        except urllib.error.URLError as exc:
            duration = time.monotonic() - start
            return WorkResult(
                packet_id=packet.packet_id,
                run_id=packet.run_id,
                status=PacketStatus.FAILED,
                error=f"Network error: {exc.reason}",
                duration_seconds=round(duration, 3),
            )

        duration = time.monotonic() - start
        return self._parse_response_from_api(packet, response_data, duration)

    def _build_prompt(self, packet: WorkPacket) -> str:
        lines = [
            f"# Work Packet: {packet.packet_id}",
            f"## Seam: {packet.seam}",
            f"## Objective: {packet.objective}",
            f"## Success Criteria: {packet.success_criteria}",
        ]
        if packet.open_questions:
            lines.append(f"## Open Questions: {', '.join(packet.open_questions)}")
        if packet.allowed_tools:
            lines.append(f"## Allowed Tools: {', '.join(packet.allowed_tools)}")
        if packet.current_state:
            lines.append(f"## Current State: {json.dumps(packet.current_state)}")
        return "\n\n".join(lines)

    def _parse_response_from_api(
        self, packet: WorkPacket, response: dict[str, Any], duration: float
    ) -> WorkResult:
        try:
            # OpenAI-compatible chat completions response
            choices = response.get("choices", [])
            output_text = ""
            if choices and isinstance(choices, list):
                message = choices[0].get("message", {})
                output_text = message.get("content", "") or str(message)
            elif isinstance(response, dict):
                # Fallback: try to extract anything reasonable
                output_text = response.get("output", {}).get("text", "") if isinstance(response.get("output"), dict) else str(response.get("output", ""))
            return WorkResult(
                packet_id=packet.packet_id,
                run_id=packet.run_id,
                status=PacketStatus.COMPLETED,
                output=output_text.strip() if output_text else "(empty response)",
                duration_seconds=round(duration, 3),
            )
        except Exception as exc:
            return WorkResult(
                packet_id=packet.packet_id,
                run_id=packet.run_id,
                status=PacketStatus.FAILED,
                error=f"Failed to parse API response: {exc}",
                duration_seconds=round(duration, 3),
            )


class GeminiHarness(HarnessAdapter):
    """Harness that calls Google Gemini API."""

    def _do_execute(self, packet: WorkPacket) -> WorkResult:
        import subprocess
        import os

        # Try gemini CLI first
        try:
            result = subprocess.run(
                ["gemini", "--print", json.dumps({"seam": packet.seam, "objective": packet.objective})],
                capture_output=True,
                text=True,
                timeout=packet.timeout_seconds or 60,
            )
            if result.returncode == 0:
                return self._parse_response(result.stdout)
        except FileNotFoundError:
            pass

        # Fall back to API if GEMINI_API_KEY is set
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if api_key:
            return self._call_api(packet)

        return WorkResult(
            packet_id=packet.packet_id,
            run_id=packet.run_id,
            status=PacketStatus.FAILED,
            error="Gemini CLI not found and GEMINI_API_KEY not set",
        )

    def _parse_response(self, stdout: str) -> WorkResult:
        try:
            data = json.loads(stdout)
            if isinstance(data, dict):
                return WorkResult(
                    packet_id=data.get("packet_id", ""),
                    run_id=data.get("run_id", ""),
                    status=PacketStatus(data.get("status", "completed")),
                    output=data.get("output"),
                    error=data.get("error"),
                    duration_seconds=data.get("duration_seconds", 0.0),
                )
        except json.JSONDecodeError:
            pass

        return WorkResult(
            packet_id=getattr(self, "packet_id", ""),
            run_id=getattr(self, "run_id", ""),
            status=PacketStatus.COMPLETED,
            output=stdout.strip(),
            duration_seconds=0.0,
        )

    def _call_api(self, packet: WorkPacket) -> WorkResult:
        import time
        import urllib.error
        import urllib.request
        import os

        api_key = os.environ.get("GEMINI_API_KEY", "")
        model = os.environ.get("MODEL", "gemini-2.0-flash")
        base_url = os.environ.get("BASE_URL", "https://generativelanguage.googleapis.com/v1beta")

        packet_json = packet.model_dump(mode="json")
        start = time.monotonic()

        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": self._build_prompt(packet)}
                    ]
                }
            ],
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 4096},
        }

        body = json.dumps(payload).encode("utf-8")
        url = f"{base_url}/models/{model}:generateContent?key={api_key}"

        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(req, timeout=packet.timeout_seconds or 60) as resp:
                response_data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            duration = time.monotonic() - start
            error_body = exc.read().decode("utf-8") if exc.fp else str(exc)
            return WorkResult(
                packet_id=packet.packet_id,
                run_id=packet.run_id,
                status=PacketStatus.FAILED,
                error=f"HTTP {exc.code}: {error_body}",
                duration_seconds=round(duration, 3),
            )
        except urllib.error.URLError as exc:
            duration = time.monotonic() - start
            return WorkResult(
                packet_id=packet.packet_id,
                run_id=packet.run_id,
                status=PacketStatus.FAILED,
                error=f"Network error: {exc.reason}",
                duration_seconds=round(duration, 3),
            )

        duration = time.monotonic() - start
        return self._parse_response_from_api(packet, response_data, duration)

    def _build_prompt(self, packet: WorkPacket) -> str:
        lines = [
            f"# Work Packet: {packet.packet_id}",
            f"## Seam: {packet.seam}",
            f"## Objective: {packet.objective}",
            f"## Success Criteria: {packet.success_criteria}",
        ]
        if packet.open_questions:
            lines.append(f"## Open Questions: {', '.join(packet.open_questions)}")
        if packet.allowed_tools:
            lines.append(f"## Allowed Tools: {', '.join(packet.allowed_tools)}")
        if packet.current_state:
            lines.append(f"## Current State: {json.dumps(packet.current_state)}")
        return "\n\n".join(lines)

    def _parse_response_from_api(
        self, packet: WorkPacket, response: dict[str, Any], duration: float
    ) -> WorkResult:
        try:
            output_text = ""
            if isinstance(response, dict):
                candidates = response.get("candidates", [])
                if candidates and isinstance(candidates[0], dict):
                    content = candidates[0].get("content", {})
                    parts = content.get("parts", []) if isinstance(content, dict) else []
                    if parts and isinstance(parts[0], dict):
                        output_text = parts[0].get("text", "")
            return WorkResult(
                packet_id=packet.packet_id,
                run_id=packet.run_id,
                status=PacketStatus.COMPLETED,
                output=output_text,
                duration_seconds=round(duration, 3),
            )
        except Exception as exc:
            return WorkResult(
                packet_id=packet.packet_id,
                run_id=packet.run_id,
                status=PacketStatus.FAILED,
                error=f"Failed to parse API response: {exc}",
                duration_seconds=round(duration, 3),
            )


TOOLS = [
    {
        "name": "ReadFile",
        "description": "Read the contents of a file. Use for inspecting source code, configs, or any text file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the file to read"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "WriteFile",
        "description": "Write content to a file. Creates or overwrites the file at the given path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path of the file to write"},
                "content": {"type": "string", "description": "Content to write to the file"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "Bash",
        "description": "Execute a shell command and return the stdout/stderr output.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute"},
                "cwd": {"type": "string", "description": "Working directory for the command"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "Glob",
        "description": "List files matching a glob pattern. Supports **/*.py, *.md, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern to match files"},
                "root": {"type": "string", "description": "Root directory to search from"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "Grep",
        "description": "Search for text inside files. Returns matching lines with line numbers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex or text pattern to search for"},
                "path": {"type": "string", "description": "Directory or file to search in"},
                "recursive": {"type": "boolean", "description": "Search recursively"},
            },
            "required": ["pattern", "path"],
        },
    },
]


def _execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """Execute a tool call and return the result as a string."""
    import os, subprocess, pathlib, re

    cwd = arguments.get("cwd") or os.getcwd()

    if name == "ReadFile":
        path = arguments["path"]
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception as exc:
            return f"Error reading {path}: {exc}"

    elif name == "WriteFile":
        path = arguments["path"]
        content = arguments["content"]
        try:
            pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"OK: wrote {len(content)} chars to {path}"
        except Exception as exc:
            return f"Error writing {path}: {exc}"

    elif name == "Bash":
        cmd = arguments["command"]
        try:
            # On Windows with Git find, auto-quote glob patterns after -name/-path
            # e.g. "find . -name *.py" -> 'find . -name "*.py"'
            import re as re_mod

            def _quote_globs(s: str) -> str:
                # Match -name or -path followed by an unquoted glob pattern
                def _replace(m: re_mod.Match) -> str:
                    flag = m.group(1)  # "-name " or "-path "
                    pattern = m.group(2)  # the pattern part
                    if any(c in pattern for c in "*?") and not pattern.startswith('"'):
                        return f'{flag}"{pattern}"'
                    return m.group(0)

                return re_mod.sub(r"(-name\s+|-path\s+)((?:[^\s\"'])+)", _replace, s)

            cmd = _quote_globs(cmd)
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=cwd,
                timeout=60,
            )
            out = result.stdout.strip() or ""
            err = result.stderr.strip() or ""
            return out + ("\n[STDERR] " + err if err else "")
        except subprocess.TimeoutExpired:
            return "Error: command timed out after 60s"
        except Exception as exc:
            return f"Error running command: {exc}"

    elif name == "Glob":
        pattern = arguments["pattern"]
        root = arguments.get("root", cwd)
        try:
            root_p = pathlib.Path(root)
            matches = list(root_p.rglob(pattern))
            return "\n".join(str(m) for m in matches[:200]) or "(no matches)"
        except Exception as exc:
            return f"Error globbing: {exc}"

    elif name == "Grep":
        pattern = arguments["pattern"]
        path = arguments["path"]
        recursive = arguments.get("recursive", False)
        try:
            p = pathlib.Path(path)
            if p.is_file():
                matches = p.read_text(encoding="utf-8", errors="replace").splitlines()
            elif recursive:
                matches = []
                for f in p.rglob("*"):
                    if f.is_file():
                        try:
                            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
                            for i, line in enumerate(lines, 1):
                                if re.search(pattern, line):
                                    matches.append(f"{f}:{i}: {line}")
                        except:
                            pass
                return "\n".join(matches[:200]) or "(no matches)"
            else:
                return "(path must be a file for non-recursive grep)"
            # simple text search for single file
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            result = [f"{i}: {l}" for i, l in enumerate(lines, 1) if re.search(pattern, l)]
            return "\n".join(result[:200]) or "(no matches)"
        except Exception as exc:
            return f"Error grepping: {exc}"

    return f"Unknown tool: {name}"


def _build_mmx_tools() -> list[dict[str, Any]]:
    """Build the tool list for mmx CLI in Anthropic SDK format."""
    return [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]} for t in TOOLS]


class MiniMaxHarness(HarnessAdapter):
    """
    Harness that calls MiniMax M2.7 via the mmx CLI tool.

    Uses ``mmx text chat`` which is already authenticated on this machine.
    Falls back to direct HTTP API if mmx CLI is unavailable.

    Environment variables::

        HARNESS_TYPE=minimax
        MINIMAX_MODEL=MiniMax-M2.7    # model name (default)
        MINIMAX_API_KEY=<key>         # API key (if using HTTP fallback)
        MINIMAX_BASE_URL=https://api.minimax.io/anthropic  # API base (default)
    """

    def __init__(
        self,
        ledger: WorkLedger | None = None,
        model: str = "MiniMax-M2.7",
        **kwargs,
    ) -> None:
        super().__init__(ledger)
        self.model = model
        # Prefer dedicated MINIMAX_API_KEY env var, fall back to ANTHROPIC_AUTH_TOKEN
        # (which is what mmx CLI uses for its authenticated context)
        self.api_key = os.environ.get(
            "MINIMAX_API_KEY", os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
        )
        self.base_url = os.environ.get(
            "MINIMAX_BASE_URL", "https://api.minimax.io/anthropic"
        )

    def _do_execute(self, packet: WorkPacket) -> WorkResult:
        import time as time_mod
        import urllib.error
        import urllib.request

        if not self.api_key:
            return WorkResult(
                packet_id=packet.packet_id,
                run_id=packet.run_id,
                status=PacketStatus.FAILED,
                error="MiniMax API key not set (ANTHROPIC_AUTH_TOKEN)",
                duration_seconds=0.0,
            )

        prompt_base = self._build_prompt(packet)
        tools = _build_mmx_tools()
        start = time_mod.monotonic()

        # Direct API tool-use loop via /v1/messages
        # (same format as Anthropic Messages API — MiniMax supports tool_use)
        messages = [{"role": "user", "content": prompt_base}]
        max_turns = 10

        for _ in range(max_turns):
            payload = {
                "model": self.model,
                "max_tokens": 2048,
                "messages": messages,
                "tools": tools,
            }
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{self.base_url}/v1/messages",
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "anthropic-version": "2023-06-01",
                    "Authorization": f"Bearer {self.api_key}",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=packet.timeout_seconds or 120) as resp:
                    response_data = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                duration = time_mod.monotonic() - start
                error_body = exc.read().decode("utf-8") if exc.fp else str(exc)
                return WorkResult(
                    packet_id=packet.packet_id,
                    run_id=packet.run_id,
                    status=PacketStatus.FAILED,
                    error=f"MiniMax HTTP {exc.code}: {error_body}",
                    duration_seconds=round(duration, 3),
                )
            except urllib.error.URLError as exc:
                duration = time_mod.monotonic() - start
                return WorkResult(
                    packet_id=packet.packet_id,
                    run_id=packet.run_id,
                    status=PacketStatus.FAILED,
                    error=f"Network error: {exc.reason}",
                    duration_seconds=round(duration, 3),
                )

            content = response_data.get("content", [])
            stop_reason = response_data.get("stop_reason", "")
            tool_calls = [
                b for b in content
                if isinstance(b, dict) and b.get("type") == "tool_use"
            ]
            if not tool_calls:
                # Final text response
                duration = time_mod.monotonic() - start
                return self._parse_http_response(packet, response_data, duration)

            # Execute tools and add results to conversation
            for tc in tool_calls:
                name = tc.get("name", "")
                args = tc.get("input", {})
                tool_result = _execute_tool(name, args)
                messages.append({"role": "user", "content": tool_result})

        duration = time_mod.monotonic() - start
        return WorkResult(
            packet_id=packet.packet_id,
            run_id=packet.run_id,
            status=PacketStatus.FAILED,
            error=f"Exceeded {max_turns} tool-use turns",
            duration_seconds=round(duration, 3),
        )

    def _format_messages(self, conversation: list[dict[str, Any]]) -> str:
        """
        Build context string for mmx CLI.

        For each turn, includes the user prompt and any tool results that
        followed. Role prefixes are omitted as mmx CLI doesn't parse them
        in multi-turn --message format.
        """
        parts = []
        for msg in conversation:
            content = msg.get("content", "")
            if not content:
                continue
            role = msg.get("role", "user")
            if role == "user":
                parts.append(content)
            elif role == "tool":
                parts.append(f"Tool result: {content}")
            # Skip assistant tool calls - model already produced them
            # and doesn't need to re-process them
        return "\n\n".join(parts)

    def _parse_mmx_final_response(
        self, packet: WorkPacket, content: list[dict[str, Any]], response_data: dict[str, Any], duration: float
    ) -> WorkResult:
        try:
            output_text = ""
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    output_text += block.get("text", "")
            usage = response_data.get("usage", {})
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            cost = (
                (input_tokens * 0.000003 + output_tokens * 0.000015)
                if usage
                else None
            )
            return WorkResult(
                packet_id=packet.packet_id,
                run_id=packet.run_id,
                status=PacketStatus.COMPLETED,
                output=output_text.strip(),
                duration_seconds=round(duration, 3),
                cost_estimate=cost,
                children=[],
                artifacts_produced=[],
            )
        except Exception as exc:
            return WorkResult(
                packet_id=packet.packet_id,
                run_id=packet.run_id,
                status=PacketStatus.FAILED,
                error=f"Failed to parse mmx response: {exc}",
                duration_seconds=round(duration, 3),
            )

    def _parse_mmx_response(
        self, packet: WorkPacket, stdout: str, duration: float
    ) -> WorkResult:
        try:
            data = json.loads(stdout)
            content = data.get("content", [])
            output_text = ""
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        output_text += block.get("text", "")
            usage = data.get("usage", {})
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            cost = (
                (input_tokens * 0.000003 + output_tokens * 0.000015)
                if usage
                else None
            )
            return WorkResult(
                packet_id=packet.packet_id,
                run_id=packet.run_id,
                status=PacketStatus.COMPLETED,
                output=output_text.strip(),
                duration_seconds=round(duration, 3),
                cost_estimate=cost,
                children=[],
                artifacts_produced=[],
            )
        except Exception as exc:
            return WorkResult(
                packet_id=packet.packet_id,
                run_id=packet.run_id,
                status=PacketStatus.FAILED,
                error=f"Failed to parse mmx response: {exc}",
                duration_seconds=round(duration, 3),
            )

    def _call_api(self, packet: WorkPacket) -> WorkResult:
        import time as time_mod
        import urllib.error
        import urllib.request

        if not self.api_key:
            return WorkResult(
                packet_id=packet.packet_id,
                run_id=packet.run_id,
                status=PacketStatus.FAILED,
                error="MiniMax API key not set (MINIMAX_API_KEY)",
            )

        start = time_mod.monotonic()
        payload = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": [
                {
                    "role": "user",
                    "content": self._build_prompt(packet),
                }
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/v1/messages",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=packet.timeout_seconds or 120) as resp:
                response_data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            duration = time_mod.monotonic() - start
            error_body = exc.read().decode("utf-8") if exc.fp else str(exc)
            return WorkResult(
                packet_id=packet.packet_id,
                run_id=packet.run_id,
                status=PacketStatus.FAILED,
                error=f"MiniMax HTTP {exc.code}: {error_body}",
                duration_seconds=round(duration, 3),
            )
        except urllib.error.URLError as exc:
            duration = time_mod.monotonic() - start
            return WorkResult(
                packet_id=packet.packet_id,
                run_id=packet.run_id,
                status=PacketStatus.FAILED,
                error=f"Network error: {exc.reason}",
                duration_seconds=round(duration, 3),
            )
        duration = time_mod.monotonic() - start
        return self._parse_http_response(packet, response_data, duration)

    def _build_prompt(self, packet: WorkPacket) -> str:
        """
        Build the user-facing prompt from a work packet.

        The format is task-focused so the model treats it as an imperative
        instruction to execute, not a template to fill in. Success criteria
        are woven into the task, not separated as metadata.
        """
        lines = [
            f"## Task: {packet.objective}",
        ]
        if packet.allowed_tools:
            lines.append(f"## Tools available: {', '.join(packet.allowed_tools)}")
        if packet.open_questions:
            lines.append(f"## Open questions to resolve: {', '.join(packet.open_questions)}")
        if packet.current_state:
            lines.append(f"## Current state: {json.dumps(packet.current_state)}")
        return "\n\n".join(lines)

    def _parse_http_response(
        self, packet: WorkPacket, response: dict[str, Any], duration: float
    ) -> WorkResult:
        try:
            content_blocks = response.get("content", [])
            output_text = ""
            if isinstance(content_blocks, list) and content_blocks:
                for block in content_blocks:
                    if isinstance(block, dict) and block.get("type") == "text":
                        output_text += block.get("text", "")
            usage = response.get("usage", {})
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            cost = (
                (input_tokens * 0.000003 + output_tokens * 0.000015)
                if usage
                else None
            )
            return WorkResult(
                packet_id=packet.packet_id,
                run_id=packet.run_id,
                status=PacketStatus.COMPLETED,
                output=output_text.strip(),
                duration_seconds=round(duration, 3),
                cost_estimate=cost,
                children=[],
                artifacts_produced=[],
            )
        except Exception as exc:
            return WorkResult(
                packet_id=packet.packet_id,
                run_id=packet.run_id,
                status=PacketStatus.FAILED,
                error=f"Failed to parse MiniMax response: {exc}",
                duration_seconds=round(duration, 3),
            )


# ---------------------------------------------------------------------------
# MiniMaxDirectClient — LangChain BaseChatModel with tool support
# ---------------------------------------------------------------------------

class MiniMaxDirectClient:
    """
    LangChain-compatible ChatModel for MiniMax M2.7 via direct HTTP API.

    Implements the minimal BaseChatModel interface needed for LangGraph
    integration: ``_generate()`` and ``_llm_type``. Tool calls are executed
    locally and fed back to the model until the model returns a final text
    response.

    Usage::

        from langchain_core.messages import HumanMessage
        from packet_runtime import MiniMaxDirectClient

        client = MiniMaxDirectClient()          # reads ANTHROPIC_AUTH_TOKEN from env
        client_with_tools = client.bind_tools(TOOLS)

        response = client_with_tools.invoke([
            HumanMessage(content="List all .py files in the current directory.")
        ])
        print(response.content)
    """

    def __init__(
        self,
        model: str = "MiniMax-M2.7",
        api_key: str | None = None,
        base_url: str = "https://api.minimax.io/anthropic",
        max_tokens: int = 2048,
        timeout_seconds: int = 300,
        max_turns: int = 10,
    ) -> None:
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
        self.base_url = base_url
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self.max_turns = max_turns
        self._tools: list[dict[str, Any]] = []

    @property
    def _llm_type(self) -> str:
        return "minimax-chat"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> "MiniMaxDirectClient":
        """Bind tools to this client (copies the client with tools attached)."""
        # Convert to dict format if needed
        converted = []
        for t in tools:
            if isinstance(t, dict):
                converted.append(t)
            elif hasattr(t, "name") and hasattr(t, "description"):
                # Assume BaseTool or similar
                converted.append({
                    "name": getattr(t, "name"),
                    "description": getattr(t, "description", ""),
                    "input_schema": getattr(t, "input_schema", {"type": "object"}),
                })
            # Skip other types (Pydantic classes handled by base class)
        instance = MiniMaxDirectClient(
            model=self.model,
            api_key=self.api_key,
            base_url=self.base_url,
            max_tokens=self.max_tokens,
            timeout_seconds=self.timeout_seconds,
            max_turns=self.max_turns,
        )
        instance._tools = converted
        return instance

    def invoke(
        self,
        input: Sequence[Any] | dict[str, Any] | Any,
        **kwargs: Any,
    ) -> Any:
        """Simple invoke — convert input to messages and call _generate."""
        from langchain_core.messages import BaseMessage
        if isinstance(input, dict):
            messages = input.get("messages", [input])
        elif isinstance(input, (list, tuple)):
            messages = list(input)
        else:
            messages = [input]
        result = self._generate(messages, **kwargs)
        return result.generations[0].message

    def _convert_messages(self, messages: Sequence[Any]) -> list[dict[str, Any]]:
        """Convert LangChain messages to MiniMax /v1/messages format."""
        from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage, SystemMessage
        result = []
        for msg in messages:
            # Handle plain dicts (e.g. {"role": "user", "content": "..."})
            if isinstance(msg, dict):
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "assistant":
                    role = "user"  # MiniMax doesn't have assistant role, treat as user
                result.append({"role": role, "content": content})
            elif isinstance(msg, HumanMessage):
                result.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                if msg.content:
                    result.append({"role": "user", "content": f"[AIMessage]: {msg.content}"})
            elif isinstance(msg, ToolMessage):
                result.append({"role": "user", "content": msg.content})
            elif isinstance(msg, SystemMessage):
                result.append({"role": "user", "content": msg.content})
            elif isinstance(msg, BaseMessage):
                result.append({"role": "user", "content": str(msg.content)})
        return result

    def _generate(
        self,
        messages: Sequence[Any],
        stop: Sequence[str] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Perform the full tool-use loop and return a ChatResult."""
        import time as time_mod
        import urllib.error
        import urllib.request

        from langchain_core.messages import AIMessage
        from langchain_core.outputs import ChatResult, ChatGeneration

        if not self.api_key:
            return ChatResult(generations=[
                ChatGeneration(message=AIMessage(content="Error: ANTHROPIC_AUTH_TOKEN not set"))
            ])

        minimax_messages = self._convert_messages(list(messages))
        tools = self._tools or []
        start = time_mod.monotonic()

        for _ in range(self.max_turns):
            payload = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "messages": minimax_messages,
                "tools": tools,
            }
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{self.base_url}/v1/messages",
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "anthropic-version": "2023-06-01",
                    "Authorization": f"Bearer {self.api_key}",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                    response_data = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                error_body = exc.read().decode("utf-8") if exc.fp else str(exc)
                return ChatResult(generations=[
                    ChatGeneration(message=AIMessage(content=f"HTTP {exc.code}: {error_body}"))
                ])
            except Exception as exc:
                return ChatResult(generations=[
                    ChatGeneration(message=AIMessage(content=f"Error: {exc}"))
                ])

            content = response_data.get("content", [])
            tool_calls = [
                b for b in content
                if isinstance(b, dict) and b.get("type") == "tool_use"
            ]
            if not tool_calls:
                output_text = "".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
                return ChatResult(generations=[ChatGeneration(
                    message=AIMessage(content=output_text.strip())
                )])

            for tc in tool_calls:
                name = tc.get("name", "")
                args = tc.get("input", {})
                tool_result = _execute_tool(name, args)
                minimax_messages.append({"role": "user", "content": tool_result})

        return ChatResult(generations=[
            ChatGeneration(message=AIMessage(content=f"Exceeded {self.max_turns} tool-use turns"))
        ])


def make_harness(
    harness_type: HarnessType | str | None = None,
    ledger: WorkLedger | None = None,
) -> HarnessAdapter:
    """
    Factory that returns the appropriate HarnessAdapter.

    Resolution order:

    1. ``harness_type`` argument (HarnessType enum or string)
    2. ``HARNESS_TYPE`` environment variable
    3. ``HarnessType.FAKE`` (the default)

    Args:
        harness_type:  Explicit harness type to use, or None to auto-detect.
        ledger:        Optional WorkLedger for event logging.

    Returns:
        A concrete HarnessAdapter instance.
    """
    if harness_type is None:
        harness_str = os.environ.get("HARNESS_TYPE", "fake")
        try:
            harness_type = HarnessType(harness_str)
        except ValueError:
            harness_type = HarnessType.FAKE
    elif isinstance(harness_type, str):
        try:
            harness_type = HarnessType(harness_type)
        except ValueError:
            harness_type = HarnessType.FAKE

    if harness_type == HarnessType.CLAUDE_CODE:
        return ClaudeCodeHarness(ledger=ledger)
    elif harness_type == HarnessType.HTTP_API:
        return HttpApiHarness(ledger=ledger)
    elif harness_type == HarnessType.OPENROUTER:
        return OpenRouterHarness(ledger=ledger)
    elif harness_type == HarnessType.QWEN:
        return QwenHarness(ledger=ledger)
    elif harness_type == HarnessType.GEMINI:
        return GeminiHarness(ledger=ledger)
    elif harness_type == HarnessType.MINIMAX:
        return MiniMaxHarness(ledger=ledger)
    else:
        return FakeExecutor(ledger=ledger)


# ---------------------------------------------------------------------------
# WebSearchTool
# ---------------------------------------------------------------------------


class WebSearchTool:
    """
    Wraps MiniMax web search for use within the packet runtime.

    Uses the native `mmx-search-query` tool via the mmx-tools MCP bridge when
    available. When the bridge is unavailable, falls back to a local stub so that
    FakeExecutor can still demonstrate shape-discovery behaviour without making
    real external calls.

    Shape discovery: when FakeExecutor runs a packet it may optionally call
    ``search_for_shapes(seam)`` to discover best-practice shapes for the given
    seam. The search results are returned as a list of plain-result dicts and
    are **not** automatically persisted — the caller is responsible for
    converting them to ``Shape`` objects and saving them via the registry.

    Example
    -------
    >>> tool = WebSearchTool()
    >>> results = tool.search_for_shapes("auth-flow")
    >>> for r in results:
    ...     print(r["title"], r["url"])
    """

    # Number of results to return per search.
    DEFAULT_LIMIT = 5

    # Search result shape returned to callers.
    SearchResult = dict[str, Any]  # {title, url, snippet, source}

    def __init__(self, mcp_available: bool | None = None) -> None:
        """
        Initialise the tool.

        Args:
            mcp_available: Override auto-detection of MCP availability.
                           ``True`` forces the live path, ``False`` forces the
                           stub, ``None`` (default) uses auto-detection.
        """
        if mcp_available is not None:
            self._use_live = bool(mcp_available)
        else:
            self._use_live = _MMX_SEARCH_AVAILABLE

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search_for_shapes(self, seam: str, limit: int = DEFAULT_LIMIT) -> list[SearchResult]:
        """
        Search for best-practice shapes relevant to the given work seam.

        This is the primary entry point for FakeExecutor-driven shape
        discovery.  Results are limited to ``limit`` entries.

        Args:
            seam: The work seam to search (e.g. "auth-flow", "file-parser").
            limit: Maximum number of results to return (default 5).

        Returns:
            List of search result dicts, each with ``title``, ``url``,
            ``snippet``, and ``source`` keys.  Empty list on error or when
            no results are available.
        """
        if self._use_live:
            return self._search_live(seam, limit)
        return self._search_stub(seam, limit)

    # ------------------------------------------------------------------
    # Live path (MCP bridge)
    # ------------------------------------------------------------------

    def _search_live(self, seam: str, limit: int) -> list[SearchResult]:
        """Call the MiniMax MCP search tool and normalise the response."""
        try:
            response = mmx_search_query(query=f"best practices {seam}")
        except Exception:
            # Any failure (network, auth, schema mismatch) falls back silently
            return []

        results: list[SearchResult] = []
        for item in response.get("results", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("snippet", ""),
                "source": "mmx-search",
            })

        return results[:limit]

    # ------------------------------------------------------------------
    # Stub path (no external calls)
    # ------------------------------------------------------------------

    def _search_stub(self, seam: str, limit: int) -> list[SearchResult]:
        """
        Return deterministic stub results for a seam.

        The stub deliberately mimics the structure of real search results so
        that FakeExecutor can exercise shape-discovery logic without any
        external dependency.
        """
        seam_lower = seam.lower().replace(" ", "-")

        stubs: list[SearchResult] = [
            {
                "title": f"Best practices for {seam} — article 1",
                "url": f"https://example.com/{seam_lower}/best-practices",
                "snippet": f"A concise guide to {seam} patterns and common pitfalls.",
                "source": "stub",
            },
            {
                "title": f"{seam} pattern library",
                "url": f"https://example.com/{seam_lower}/patterns",
                "snippet": f"Reusable pattern templates for {seam} implementation.",
                "source": "stub",
            },
            {
                "title": f"Top pitfalls in {seam} and how to avoid them",
                "url": f"https://example.com/{seam_lower}/pitfalls",
                "snippet": f"Common mistakes when working on {seam} and recommended solutions.",
                "source": "stub",
            },
            {
                "title": f"{seam} quick-start guide",
                "url": f"https://example.com/{seam_lower}/quickstart",
                "snippet": f"Step-by-step instructions for getting started with {seam}.",
                "source": "stub",
            },
            {
                "title": f"Advanced techniques for {seam}",
                "url": f"https://example.com/{seam_lower}/advanced",
                "snippet": f"In-depth coverage of advanced {seam} techniques.",
                "source": "stub",
            },
        ]

        return stubs[:limit]

    @property
    def is_live(self) -> bool:
        """``True`` when the live MCP path is being used."""
        return self._use_live


# ---------------------------------------------------------------------------
# FakeExecutor
# ---------------------------------------------------------------------------


class FakeExecutor(HarnessAdapter):
    """
    Simulated worker executor for testing and Milestone 1.

    Returns deterministic fake results without calling any external LLM.
    Optionally uses a :class:`WebSearchTool` to perform shape discovery during
    simulation, allowing end-to-end tests to exercise the discover-and-save flow
    without real model calls.
    """

    def __init__(
        self,
        ledger: WorkLedger | None = None,
        web_search_tool: WebSearchTool | None = None,
    ) -> None:
        super().__init__(ledger)
        self.web_search_tool = web_search_tool or WebSearchTool()
        self._call_log: list[dict[str, Any]] = []

    def _do_execute(self, packet: WorkPacket) -> WorkResult:
        import random

        objective_snippet = (
            packet.objective[:50] + "..."
            if len(packet.objective) > 50
            else packet.objective
        )

        duration = random.uniform(0.05, 0.5)

        result = WorkResult(
            packet_id=packet.packet_id,
            run_id=packet.run_id,
            status=PacketStatus.COMPLETED,
            output=f"Simulated result for {packet.packet_id}: {objective_snippet}",
            duration_seconds=round(duration, 3),
            cost_estimate=0.0,
        )

        self._call_log.append({
            "packet_id": packet.packet_id,
            "seam": packet.seam,
            "duration": duration,
            "status": "success",
        })

        return result

    def execute(self, packet: WorkPacket) -> WorkResult:
        """
        Execute a packet and return a structured WorkResult.

        When ``web_search_tool`` is configured and the packet's seam is not
        empty, this method performs a live shape-discovery search and
        attaches the results to the returned WorkResult.

        Args:
            packet: The WorkPacket dispatched to this worker.

        Returns:
            WorkResult with status, output, shapes, etc.
        """
        import time

        start = time.monotonic()
        try:
            result = self._do_execute(packet)
            # Perform shape discovery via web search and attach results
            if packet.seam:
                search_results = self.web_search_tool.search_for_shapes(packet.seam)
                if search_results:
                    result.shapes_discovered = [
                        ShapeRef(
                            shape_id=f"stub-{packet.seam}-{i}",
                            shape_type=ShapeType.PROMPT_TEMPLATE,
                            name=r["title"],
                            tags=[packet.seam, "discovered"],
                            created_from_packet_id=packet.packet_id,
                        )
                        for i, r in enumerate(search_results)
                    ]
            return result
        except Exception as exc:
            return WorkResult(
                packet_id=packet.packet_id,
                run_id=packet.run_id,
                status=PacketStatus.FAILED,
                error=str(exc),
                duration_seconds=round(time.monotonic() - start, 3),
            )

    @property
    def call_count(self) -> int:
        """Number of execute() calls made."""
        return len(self._call_log)


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------


def run_goal(
    objective: str,
    success_criteria: str = "completed",
    runs_dir: Path | str | None = None,
    web_search_tool: WebSearchTool | None = None,
) -> dict[str, Any]:
    """
    Run a goal end-to-end with FakeExecutor (no real model).

    Demonstrates the full packet lifecycle: create -> claim -> progress ->
    complete.  When ``web_search_tool`` is provided, the FakeExecutor uses it
    to discover shapes for the packet's seam.

    Args:
        objective: Natural-language goal description.
        success_criteria: How we know the goal is done.
        runs_dir: Optional directory for run data.
        web_search_tool: Optional :class:`WebSearchTool` instance for shape
            discovery during simulation.

    Returns:
        Dict with run_id, packet_id, result, shapes discovered.
    """
    runtime = PacketRuntime(runs_dir=runs_dir)
    executor = FakeExecutor(ledger=runtime.ledger, web_search_tool=web_search_tool)

    # Create goal
    run_id, packet_id = runtime.create_goal(objective, success_criteria)
    print(f"Created goal: run_id={run_id}, packet_id={packet_id}")

    # Get agent ID and harness
    agent_id = os.environ.get("AGENT_ID", "test-agent")
    harness_str = os.environ.get("HARNESS_TYPE", "test")
    try:
        harness = HarnessType(harness_str)
    except ValueError:
        harness = HarnessType.TEST

    # Claim
    runtime.claim_packet(packet_id, run_id, agent_id, harness)
    print(f"Claimed packet: agent={agent_id}, harness={harness.value}")

    # Execute work
    result = executor.execute(
        WorkPacket(
            packet_id=packet_id,
            run_id=run_id,
            depth=0,
            seam="root",
            objective=objective,
            success_criteria=success_criteria,
        )
    )
    print(f"Simulated work: duration={result.duration_seconds}s")

    # Progress
    runtime.progress_packet(
        packet_id,
        run_id,
        {"progress": "100%", "simulated": True},
    )
    print("Progress logged")

    # Complete
    event_id = runtime.complete_packet(
        packet_id,
        run_id,
        output=result.output or "",
    )
    print(f"Completed: event_id={event_id}")

    return {
        "run_id": run_id,
        "packet_id": packet_id,
        "result": result,
        "shapes": [],
    }
