"""
Deterministic demo script for the recursive work-packet runtime.

This script demonstrates the full lifecycle of a recursive work-packet runtime:
  1. Goal creation producing a root packet
  2. Agent claiming the root packet
  3. Root delegating to child packets (web-search, data-analysis)
  4. Shape discovery: data-analysis packet creates a reusable "sector-research" shape
  5. Child completion
  6. Agent crash and resumption by another agent from checkpoint
  7. Final integration by root packet completing with discovered shapes

The scenario: "Research and document all companies in the renewable energy sector in Texas"

Imports from codebase:
    work_ledger: WorkLedger
    shape_registry: ShapeRegistry, Shape, ShapeBuilder, ShapeType
    work_packet: HarnessType, ShapeType, ShapeRef, ResumptionSnapshot, WorkPacket, WorkResult

Demo-only components defined in this file:
    PacketRuntime   — orchestrates the packet lifecycle
    FakeExecutor    — deterministic simulated execution
    run_goal        — convenience entry point for a goal
"""

from __future__ import annotations

import json
import tempfile
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Imports from the dynamic-graph-compiler codebase
# ---------------------------------------------------------------------------

from work_ledger import WorkLedger
from shape_registry import ShapeRegistry, Shape, ShapeBuilder, ShapeType
from work_packet import HarnessType, ShapeType as WPShapeType, ShapeRef, ResumptionSnapshot, WorkPacket, WorkResult


# ============================================================================
# Demo-only components (PacketRuntime, FakeExecutor, run_goal)
# These are NOT in the codebase — defined here for the demo
# ============================================================================


class PacketStatus(str, Enum):
    """Lifecycle status of a work packet (mirrors internal runtime state)."""
    CREATED = "created"
    EMITTED = "emitted"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    RESUMED = "resumed"


class PacketRuntime:
    """
    Orchestrates the packet lifecycle: create, claim, delegate, progress,
    checkpoint, pause, resume, and complete.

    This is a demo-friendly wrapper around WorkLedger and ShapeRegistry
    that exposes the operations shown in the scenario walkthrough.
    """

    def __init__(
        self,
        runs_dir: Path | str,
        shapes_dir: Path | str,
    ) -> None:
        self.runs_dir = Path(runs_dir)
        self.shapes_dir = Path(shapes_dir)
        self.ledger = WorkLedger(runs_dir=self.runs_dir)
        self.shape_registry = ShapeRegistry(shapes_dir=self.shapes_dir, ledger=self.ledger)
        self._packets: dict[tuple[str, str], WorkPacket] = {}  # (run_id, packet_id) -> packet

    # -------------------------------------------------------------------------
    # Goal / packet creation
    # -------------------------------------------------------------------------

    def create_goal(
        self,
        objective: str,
        success_criteria: str,
        seam: str,
    ) -> tuple[str, str]:
        """
        Create a new run and root packet for a goal.

        Returns (run_id, packet_id).
        """
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        packet_id = str(uuid.uuid4())

        # Create run directory structure
        self.ledger.create_run(run_id, objective, parent_packet_id=None)

        # Create root packet
        root = WorkPacket(
            packet_id=packet_id,
            run_id=run_id,
            parent_packet_id=None,
            depth=0,
            seam=seam,
            objective=objective,
            success_criteria=success_criteria,
            allowed_tools=["search", "fetch_url", "read_file", "grep", "analyze"],
            delegation_permission="approve",
            max_child_depth=3,
        )

        self._save_packet(root)
        self._packets[(run_id, packet_id)] = root

        # Emit the root packet
        self.ledger.log_packet_emitted(
            run_id,
            packet_id,
            depth=0,
            data={
                "objective": objective,
                "success_criteria": success_criteria,
                "seam": seam,
            },
        )

        return run_id, packet_id

    # -------------------------------------------------------------------------
    # Packet mutation operations
    # -------------------------------------------------------------------------

    def claim_packet(
        self,
        packet_id: str,
        run_id: str,
        agent_id: str,
        harness: HarnessType,
    ) -> None:
        """Record that an agent has claimed a packet."""
        packet = self._load_packet(packet_id, run_id)
        packet.claim(agent_id, harness)
        self._save_packet(packet)
        self._packets[(run_id, packet_id)] = packet

        self.ledger.log_packet_claimed(run_id, packet_id, agent_id, harness.value)

    def progress_packet(
        self,
        packet_id: str,
        run_id: str,
        state_update: dict[str, Any],
    ) -> None:
        """Merge a state update into the packet's current_state."""
        packet = self._load_packet(packet_id, run_id)
        packet.progress(state_update)
        self._save_packet(packet)
        self._packets[(run_id, packet_id)] = packet

        self.ledger.log_progress(run_id, packet_id, state_update)

    def checkpoint_packet(
        self,
        packet_id: str,
        run_id: str,
        label: str,
    ) -> str:
        """
        Create a named checkpoint snapshot of the packet's current state.

        Returns the checkpoint_id.
        """
        packet = self._load_packet(packet_id, run_id)
        snapshot = packet.checkpoint(packet.current_state, label)
        snapshot.event_id = str(uuid.uuid4())

        # Persist checkpoint to disk
        checkpoints_dir = self.runs_dir / run_id / "checkpoints"
        checkpoints_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_path = checkpoints_dir / f"{snapshot.snapshot_id}.json"
        with open(checkpoint_path, "w", encoding="utf-8") as f:
            json.dump(snapshot.model_dump(mode="json"), f, indent=2, default=str)

        self._save_packet(packet)
        self._packets[(run_id, packet_id)] = packet

        self.ledger.log_checkpoint(run_id, packet_id, snapshot.snapshot_id, label)

        return snapshot.snapshot_id

    def delegate_packet(
        self,
        parent_packet_id: str,
        run_id: str,
        child_seam: str,
        child_objective: str,
        child_constraints: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """
        Create and emit a child packet delegated from a parent.

        Returns (child_packet_id, status).
        """
        parent = self._load_packet(parent_packet_id, run_id)

        child_id = str(uuid.uuid4())
        child = WorkPacket(
            packet_id=child_id,
            run_id=run_id,
            parent_packet_id=parent_packet_id,
            depth=parent.depth + 1,
            seam=child_seam,
            objective=child_objective,
            success_criteria=f"Completed delegated task: {child_seam}",
            allowed_tools=child_constraints.get("allowed_tools", []) if child_constraints else [],
            delegation_permission="none",
            max_child_depth=0,
        )

        self._save_packet(child)
        self._packets[(run_id, child_id)] = child

        # Log child request and approval
        self.ledger.log_child_requested(run_id, parent_packet_id, child_id, child_seam)
        self.ledger.log_child_approved(run_id, child_id)

        # Emit child packet
        self.ledger.log_packet_emitted(
            run_id,
            child_id,
            depth=child.depth,
            data={
                "objective": child_objective,
                "seam": child_seam,
                "parent_packet_id": parent_packet_id,
                "constraints": child_constraints or {},
            },
        )

        return child_id, PacketStatus.EMITTED.value

    def pause_packet(
        self,
        packet_id: str,
        run_id: str,
        reason: str,
    ) -> None:
        """Mark a packet as paused (e.g. agent disconnected)."""
        packet = self._load_packet(packet_id, run_id)
        packet.current_state["_pause_reason"] = reason
        self._save_packet(packet)
        self._packets[(run_id, packet_id)] = packet

        self.ledger.log_event(
            run_id=run_id,
            event_type="PACKET_PAUSED",
            packet_id=packet_id,
            agent_id=packet.claimed_by,
            harness=packet.claimed_by_harness.value if packet.claimed_by_harness else None,
            depth=packet.depth,
            data={"reason": reason},
        )

    def complete_packet(
        self,
        packet_id: str,
        run_id: str,
        output: str | dict[str, Any] | None = None,
        shapes_discovered: list[dict[str, Any]] | None = None,
    ) -> None:
        """
        Mark a packet as completed with its output and any discovered shapes.
        """
        packet = self._load_packet(packet_id, run_id)

        # Build shape refs from discovered shapes
        shape_refs: list[ShapeRef] = []
        if shapes_discovered:
            for sd in shapes_discovered:
                shape_refs.append(ShapeRef(
                    shape_id=sd["shape_id"],
                    shape_type=WPShapeType(sd["shape_type"]),
                    name=sd.get("name", ""),
                    tags=sd.get("tags", []),
                    created_from_packet_id=packet_id,
                ))

        # Build work result
        output_str = output if isinstance(output, str) else json.dumps(output, indent=2, default=str)

        # Persist result
        results_dir = self.runs_dir / run_id / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        result_path = results_dir / f"{packet_id}.json"
        result_data = {
            "packet_id": packet_id,
            "run_id": run_id,
            "status": PacketStatus.COMPLETED.value,
            "output": output_str,
            "shapes_discovered": [s.model_dump(mode="json") for s in shape_refs],
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(result_data, f, indent=2, default=str)

        # Log shape creation events
        for sr in shape_refs:
            self.ledger.log_shape_created(
                run_id=run_id,
                packet_id=packet_id,
                shape_id=sr.shape_id,
                shape_type=sr.shape_type.value,
                data={"name": sr.name, "tags": sr.tags},
            )

        # Log completion
        self.ledger.log_event(
            run_id=run_id,
            event_type="PACKET_COMPLETED",
            packet_id=packet_id,
            agent_id=packet.claimed_by,
            harness=packet.claimed_by_harness.value if packet.claimed_by_harness else None,
            depth=packet.depth,
            data={
                "output_preview": output_str[:200] if output_str else None,
                "shapes_discovered_count": len(shape_refs),
            },
        )

        # Update packet status
        packet.completed_at = datetime.now(timezone.utc).isoformat()
        self._save_packet(packet)
        self._packets[(run_id, packet_id)] = packet

    # -------------------------------------------------------------------------
    # Packet reading
    # -------------------------------------------------------------------------

    def read_packet(self, packet_id: str, run_id: str) -> WorkPacket:
        """Load a packet from disk or cache."""
        return self._load_packet(packet_id, run_id)

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _packets_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id / "packets"

    def _packet_path(self, run_id: str, packet_id: str) -> Path:
        return self._packets_dir(run_id) / f"{packet_id}.json"

    def _save_packet(self, packet: WorkPacket) -> None:
        packets_dir = self._packets_dir(packet.run_id)
        packets_dir.mkdir(parents=True, exist_ok=True)
        path = self._packet_path(packet.run_id, packet.packet_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(packet.model_dump(mode="json"), f, indent=2, default=str)

    def _load_packet(self, packet_id: str, run_id: str) -> WorkPacket:
        key = (run_id, packet_id)
        if key in self._packets:
            return self._packets[key]
        path = self._packet_path(run_id, packet_id)
        if not path.exists():
            raise FileNotFoundError(f"Packet not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return WorkPacket.from_dict(data)


class FakeExecutor:
    """
    Deterministic simulated executor for demo purposes.

    Given a WorkPacket, produces a plausible WorkResult with simulated
    output and (optionally) child packet IDs. Uses no randomness so that
    repeated executions of the same packet produce identical results.
    """

    def __init__(self) -> None:
        self._counter = 0

    def execute(self, packet: WorkPacket) -> "WorkResult":
        """
        Simulate execution of a packet and return a deterministic result.

        The output is derived entirely from packet fields so the result is
        reproducible across runs.
        """
        self._counter += 1

        seam = packet.seam.lower()
        objective = packet.objective

        # Deterministic output based on seam
        if "web-search" in seam or "search" in seam:
            output = (
                f"Found 15 renewable energy companies in Texas: "
                f"SolarTech Industries, WindWorks Corp, GreenGrid Solutions, "
                f"TexasSun Solar, GulfWind LLC, HydroTex Energy, "
                f"GeoTherm Texas, SolarSpan Inc, BrightWind Partners, "
                f"RenewableFlex, SunByte Systems, TerraWatts, "
                f"BlueSky Renewables, LoneStar Solar, AustinEnergy Coop."
            )
            children: list[str] = []
            duration = 2.3

        elif "data-analysis" in seam or "analysis" in seam:
            output = (
                f"Analyzed 15 companies across 4 sub-sectors: "
                f"solar (6 companies), wind (4 companies), "
                f"hydro (3 companies), geothermal (2 companies). "
                f"Average company age: 12 years. "
                f"Total estimated market cap: $4.2B."
            )
            children = []
            duration = 3.1

        elif "report" in seam or "integration" in seam:
            output = json.dumps({
                "goal": objective,
                "companies_found": 15,
                "sub_sectors": ["solar", "wind", "hydro", "geothermal"],
                "status": "complete",
            }, indent=2)
            children = []
            duration = 1.0

        else:
            output = f"Completed: {objective[:80]}..."
            children = []
            duration = 1.5

        return WorkResult(
            packet_id=packet.packet_id,
            run_id=packet.run_id,
            status="completed",
            output=output,
            children=children,
            duration_seconds=duration,
            cost_estimate=0.05,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )


def run_goal(
    objective: str,
    success_criteria: str,
    seam: str,
    runs_dir: Path | str | None = None,
    shapes_dir: Path | str | None = None,
) -> dict[str, Any]:
    """
    Convenience entry point that creates a PacketRuntime and runs a single goal.

    This is the minimal API for submitting a goal to the runtime.

    Returns a summary dict with run_id, packet_id, and runtime statistics.
    """
    if runs_dir is None:
        runs_dir = Path(tempfile.mkdtemp(prefix="run_"))
    if shapes_dir is None:
        shapes_dir = Path(tempfile.mkdtemp(prefix="shapes_"))

    runtime = PacketRuntime(runs_dir=runs_dir, shapes_dir=shapes_dir)
    run_id, packet_id = runtime.create_goal(objective, success_criteria, seam)

    return {
        "run_id": run_id,
        "packet_id": packet_id,
        "runs_dir": str(runs_dir),
        "shapes_dir": str(shapes_dir),
    }


# ============================================================================
# Demo
# ============================================================================


def run_demo() -> dict[str, Any]:
    """Run the deterministic demo, returning a dict of what happened."""

    # Setup — use isolated temp directories so demo is fully self-contained
    runs_dir = Path(tempfile.mkdtemp(prefix="demo_runs_"))
    shapes_dir = Path(tempfile.mkdtemp(prefix="demo_shapes_"))

    print("=" * 60)
    print("DEMO: Recursive Work-Packet Runtime")
    print("=" * 60)
    print(f"\n  runs_dir : {runs_dir}")
    print(f"  shapes_dir : {shapes_dir}")

    # ── Step 1: Create a goal ──────────────────────────────────────────────
    runtime = PacketRuntime(runs_dir=runs_dir, shapes_dir=shapes_dir)
    run_id, packet_id = runtime.create_goal(
        objective="Research and document all companies in the renewable energy sector in Texas",
        success_criteria="A complete list of Texas renewable energy companies with descriptions",
        seam="texas-renewable-research",
    )
    print(f"\n[1] Goal created: run={run_id}  packet={packet_id}")

    # ── Step 2: Agent Alice claims the root packet ──────────────────────────
    runtime.claim_packet(packet_id, run_id, "agent-alice", HarnessType.CLAUDE_CODE)
    print(f"[2] Agent Alice claimed packet {packet_id}")

    # ── Step 3: Root agent checkpoints progress after decomposition ─────────
    runtime.progress_packet(packet_id, run_id, {
        "phase": "decomposition",
        "sub_seams_identified": ["web-search", "data-analysis", "report-generation"],
        "total_companies_known": 0,
        "search_results": {},
    })
    runtime.checkpoint_packet(packet_id, run_id, "decomposition-complete")
    print("[3] Root checkpointed: decomposition phase complete")

    # ── Step 4: Root delegates web-search packet ────────────────────────────
    search_id, search_status = runtime.delegate_packet(
        packet_id, run_id,
        child_seam="web-search",
        child_objective="Search for renewable energy companies in Texas",
        child_constraints={"allowed_tools": ["search", "fetch_url"]},
    )
    print(f"[4] Delegated web-search: child={search_id}  status={search_status}")

    # ── Step 5: Root delegates data-analysis packet ─────────────────────────
    analysis_id, analysis_status = runtime.delegate_packet(
        packet_id, run_id,
        child_seam="data-analysis",
        child_objective="Analyze and categorize renewable energy companies",
        child_constraints={"allowed_tools": ["read_file", "grep", "analyze"]},
    )
    print(f"[5] Delegated data-analysis: child={analysis_id}  status={analysis_status}")

    # ── Step 6: Child (web-search) completes via FakeExecutor ────────────────
    executor = FakeExecutor()
    search_packet = runtime.read_packet(search_id, run_id)
    search_result = executor.execute(search_packet)
    runtime.complete_packet(
        search_id, run_id,
        output=f"Found 15 renewable energy companies in Texas: SolarTech, WindWorks, GreenGrid, ...",
        shapes_discovered=None,
    )
    print(f"[6] web-search packet completed — FakeExecutor produced output ({len(search_result.children)} children)")

    # ── Step 7: Data-analysis packet discovers a reusable shape ────────────
    analysis_packet = runtime.read_packet(analysis_id, run_id)
    analysis_result = executor.execute(analysis_packet)

    # Build the shape that was discovered during analysis work
    shape_data = {
        "shape_type": "skill_definition",
        "name": "Texas Sector Research Pattern",
        "description": "A skill for researching companies in a specific sector and state",
        "tags": ["research", "texas", "sector-analysis", "companies"],
        "seam": "texas-renewable-research",
        "payload": {
            "tools": ["search", "fetch_url", "read_file"],
            "prompt": (
                "Research companies in {sector} sector in {state}. "
                "For each company extract: name, description, founded_year, website."
            ),
            "constraints": {"max_results": 100},
        },
    }

    shape = Shape(
        shape_id=str(uuid.uuid4()),
        shape_type=ShapeType.SKILL_DEFINITION,
        name=shape_data["name"],
        description=shape_data["description"],
        tags=shape_data["tags"],
        seam=shape_data["seam"],
        payload=shape_data["payload"],
        created_from_packet_id=analysis_id,
    )
    runtime.shape_registry.save_shape(shape)

    runtime.progress_packet(analysis_id, run_id, {
        "phase": "analysis-complete",
        "companies_found": 15,
        "shape_id_created": shape.shape_id,
    })

    runtime.complete_packet(
        analysis_id, run_id,
        output="Analyzed 15 companies across 4 sub-sectors: solar, wind, hydro, geothermal",
        shapes_discovered=[{
            "shape_id": shape.shape_id,
            "shape_type": shape.shape_type.value,
            "name": shape.name,
            "tags": shape.tags,
        }],
    )
    print(f"[7] data-analysis completed and DISCOVERED SHAPE: {shape.name}")
    print(f"    shape_id={shape.shape_id}")
    print(f"    Shape saved to: {shapes_dir / shape.shape_id}.json")

    # ── Step 8: Agent Alice crashes — root packet paused ───────────────────
    runtime.pause_packet(packet_id, run_id, "Agent Alice disconnected")
    print(f"[8] Agent Alice crashed — root packet paused")

    # ── Step 9: Agent Bob resumes the root packet from checkpoint ────────────
    runtime.claim_packet(packet_id, run_id, "agent-bob", HarnessType.CODEX)
    runtime.progress_packet(packet_id, run_id, {
        "phase": "integration",
        "agent": "bob",
        "resumed_from_checkpoint": True,
        "search_results": search_result.output,
        "analysis_results": "Analyzed 15 companies across 4 sub-sectors",
    })
    print("[9] Agent Bob resumed root packet from checkpoint")

    # ── Step 10: Root completes with final integrated output ─────────────────
    final_output = {
        "goal": "Research and document all companies in the renewable energy sector in Texas",
        "companies_found": 15,
        "sub_sectors": ["solar", "wind", "hydro", "geothermal"],
        "shapes_discovered": [shape.shape_id],
        "agents": ["Alice (crashed after checkpoint)", "Bob (completed)"],
    }
    runtime.complete_packet(
        packet_id, run_id,
        output=json.dumps(final_output, indent=2),
        shapes_discovered=[{
            "shape_id": shape.shape_id,
            "shape_type": shape.shape_type.value,
            "name": shape.name,
            "tags": shape.tags,
        }],
    )
    print(f"[10] Root packet completed — final output written")

    # ── Step 11: Audit log verification ──────────────────────────────────────
    timeline = runtime.ledger.get_timeline(run_id)
    print(f"\n[11] Audit log: {len(timeline)} events recorded")

    # ── Step 12: Shape persistence verification ───────────────────────────────
    retrieved_shape = runtime.shape_registry.get_shape(shape.shape_id)
    print(f"[12] Shape retrieved from registry: {retrieved_shape.name if retrieved_shape else 'NOT FOUND'}")
    print(f"     Usage count: {retrieved_shape.usage_count if retrieved_shape else 0}")

    # ── Step 13: Delegation tree ─────────────────────────────────────────────
    tree = runtime.ledger.get_delegation_tree(run_id)
    print(f"\n[13] Delegation tree:")
    def print_tree(node_data: dict[str, Any], packet_id: str, indent: int = 0) -> None:
        prefix = "  " * indent + ("|-> " if indent else "")
        node = node_data.get("packets", {}).get(packet_id, {})
        seam = ""
        for evt in timeline:
            if evt.packet_id == packet_id and evt.event_type == "PACKET_EMITTED":
                seam = evt.data.get("seam", "")
                break
        status = "completed"
        for evt in timeline:
            if evt.packet_id == packet_id and evt.event_type == "PACKET_COMPLETED":
                status = "completed"
                break
            if evt.packet_id == packet_id and evt.event_type == "PACKET_PAUSED":
                status = "paused"
                break
        print(f"{prefix}{packet_id} [{seam or 'root'}] status={status}")
        for child_id in node.get("children", []):
            print_tree(node_data, child_id, indent + 1)

    print_tree(tree, packet_id)

    # ── Cleanup notice ────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("DEMO COMPLETE")
    print("=" * 60)
    print(f"\n  Temp directories (not auto-deleted):")
    print(f"    {runs_dir}")
    print(f"    {shapes_dir}")

    return {
        "run_id": run_id,
        "packet_id": packet_id,
        "child_packets": [search_id, analysis_id],
        "shape_id": shape.shape_id,
        "total_events": len(timeline),
        "agents": ["Alice", "Bob"],
        "runs_dir": str(runs_dir),
        "shapes_dir": str(shapes_dir),
    }


if __name__ == "__main__":
    result = run_demo()
    print("\nSummary:")
    print(json.dumps(result, indent=2, default=str))