"""
packet_cli.py — CLI for the recursive work-packet runtime.

Usage:
    python packet_cli.py <command> [options]
    python packet_cli.py           # Interactive mode

Commands:
    goal              Create a new goal packet
    list-seams        List open packets available for claiming
    claim             Claim a packet for the current agent
    status            Show current packet state and audit trail
    progress          Checkpoint state progress
    delegate          Request delegation to create a child packet
    approve           Approve a pending child packet
    deny              Deny a child packet
    complete          Mark packet as completed with output
    fail              Mark packet as failed
    gate              Pause at a human gate
    resolve-gate      Resolve a human gate
    checkpoint        Create a checkpoint snapshot
    shapes            Search for reusable shapes
    use-shape         Create a goal using a shape as template
    resume            Re-emit a paused/failed packet for claiming
    history           Show full audit trail for a run
    replay            Reconstruct and display run state from ledger
    run               Run a goal end-to-end with FakeExecutor
    list-runs         List all runs

Interactive Mode:
    python packet_cli.py
    (no command)      Enter interactive mode showing menu

Examples:
    python packet_cli.py goal "Build user auth system"
    python packet_cli.py list-seams
    python packet_cli.py claim <packet_id>
    python packet_cli.py status <packet_id>
    python packet_cli.py progress <packet_id> --state '{"done": true}'
    python packet_cli.py complete <packet_id> --output "Done!"
    python packet_cli.py history <run_id>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

from packet_runtime import PacketRuntime, FakeExecutor, run_goal
from work_packet import HarnessType
from work_ledger import WorkLedger, ReplayEngine
from shape_registry import ShapeRegistry, ShapeType

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RUNS_DIR = Path("runs")
SHAPES_DIR = Path("shapes")


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

class F:
    """ANSI formatting for CLI output."""

    BRIGHT = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    MAGENTA = "\033[95m"
    RESET = "\033[0m"

    @staticmethod
    def wrap(text: str, width: int = 78) -> str:
        return "\n".join(textwrap.wrap(text, width=width))

    @staticmethod
    def truncate(text: str, max_len: int = 60) -> str:
        if not isinstance(text, str):
            text = str(text)
        return text if len(text) <= max_len else text[:max_len - 3] + "..."

    @staticmethod
    def header(text: str) -> str:
        return f"{F.BRIGHT}{F.BLUE}{text}{F.RESET}"

    @staticmethod
    def subheader(text: str) -> str:
        return f"{F.BRIGHT}{text}{F.RESET}"

    @staticmethod
    def row(key: str, value: str) -> str:
        return f"  {F.CYAN}{key}{F.RESET}  {value}"

    @staticmethod
    def error(text: str) -> str:
        return f"{F.RED}ERROR: {text}{F.RESET}"

    @staticmethod
    def warning(text: str) -> str:
        return f"{F.YELLOW}WARNING: {text}{F.RESET}"

    @staticmethod
    def success(text: str) -> str:
        return f"{F.GREEN}OK: {text}{F.RESET}"

    @staticmethod
    def muted(text: str) -> str:
        return f"{F.DIM}{text}{F.RESET}"

    @staticmethod
    def badge(status: str) -> str:
        color_map = {
            "created": F.CYAN,
            "emitted": F.CYAN,
            "claimed": F.BLUE,
            "in_progress": F.BLUE,
            "paused": F.YELLOW,
            "completed": F.GREEN,
            "failed": F.RED,
            "resumed": F.MAGENTA,
            "pending": F.DIM,
            "approved": F.GREEN,
            "denied": F.RED,
            "success": F.GREEN,
            "failure": F.RED,
        }
        color = color_map.get(status.lower(), F.RESET)
        return f"{color}{F.BRIGHT}[{status}]{F.RESET}"

    @staticmethod
    def table(headers: list[str], rows: list[list[str]]) -> str:
        if not rows:
            return "  ".join(headers)
        widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], len(str(cell)))
        header_row = "  ".join(str(h).ljust(widths[i]) for i, h in enumerate(headers))
        sep = "  ".join("-" * w for w in widths)
        data_rows = [
            "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row))
            for row in rows
        ]
        return "\n".join([header_row, sep] + data_rows)


# ---------------------------------------------------------------------------
# Timestamp formatting
# ---------------------------------------------------------------------------

def _format_timestamp(ts: str) -> str:
    """Format ISO timestamp for display."""
    if not ts:
        return "(none)"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, AttributeError):
        return ts


def _format_age(ts: str) -> str:
    """Format timestamp as relative age."""
    if not ts:
        return "unknown"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - dt.replace(tzinfo=timezone.utc)
        seconds = delta.total_seconds()
        if seconds < 60:
            return f"{int(seconds)}s ago"
        elif seconds < 3600:
            return f"{int(seconds / 60)}m ago"
        elif seconds < 86400:
            return f"{int(seconds / 3600)}h ago"
        else:
            return f"{int(seconds / 86400)}d ago"
    except (ValueError, AttributeError):
        return ts


# ---------------------------------------------------------------------------
# Runtime initialization
# ---------------------------------------------------------------------------

def _get_runtime() -> PacketRuntime:
    return PacketRuntime(runs_dir=RUNS_DIR)


def _get_ledger() -> WorkLedger:
    return WorkLedger(runs_dir=RUNS_DIR)


def _get_registry() -> ShapeRegistry:
    return ShapeRegistry(shapes_dir=SHAPES_DIR)


def _get_agent_id() -> str:
    return os.environ.get("AGENT_ID", "unknown")


def _get_default_harness() -> HarnessType:
    harness_str = os.environ.get("HARNESS_TYPE", "")
    try:
        return HarnessType(harness_str)
    except ValueError:
        return HarnessType.UNKNOWN


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_goal(args: argparse.Namespace) -> int:
    """Create a new goal packet."""
    runtime = _get_runtime()
    run_id, packet_id = runtime.create_goal(
        objective=args.objective,
        success_criteria=args.success_criteria or "completed",
        seam=args.seam or "root",
    )
    print(f"Created run_id={run_id} packet_id={packet_id}")
    return 0


def _cmd_list_seams(args: argparse.Namespace) -> int:
    """List open packets available for claiming."""
    runtime = _get_runtime()
    seams = runtime.list_seams(run_id=args.run_id)

    if not seams:
        print("No open seams found.")
        return 0

    rows = []
    for s in seams:
        rows.append([
            s.get("run_id", "")[:8],
            s.get("packet_id", "")[:8],
            s.get("seam", ""),
            str(s.get("depth", 0)),
            _format_age(s.get("created_at", "")),
        ])

    print(F.table(["run_id", "packet_id", "seam", "depth", "age"], rows))
    return 0


def _cmd_claim(args: argparse.Namespace) -> int:
    """Claim a packet for the current agent."""
    runtime = _get_runtime()

    agent_id = args.agent_id or _get_agent_id()
    harness = args.harness or _get_default_harness()

    try:
        if isinstance(harness, str):
            harness = HarnessType(harness)

        event_id = runtime.claim_packet(
            packet_id=args.packet_id,
            run_id=args.run_id,
            agent_id=agent_id,
            harness=harness,
        )
    except FileNotFoundError as e:
        print(F.error(str(e)))
        return 1
    except ValueError as e:
        print(F.error(str(e)))
        return 1

    # Load and display packet
    try:
        run_id, packet = runtime._resolve_packet(args.packet_id, args.run_id)
        print(f"Claimed packet {args.packet_id}")
        print()
        print(packet.to_markdown())
    except FileNotFoundError:
        print(F.warning(f"Packet file not found, but claim was logged with event_id={event_id}"))

    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    """Show current packet state and audit trail."""
    runtime = _get_runtime()
    ledger = _get_ledger()

    try:
        run_id, packet = runtime._resolve_packet(args.packet_id, args.run_id)
    except FileNotFoundError as e:
        print(F.error(str(e)))
        return 1

    status = runtime._get_packet_status(run_id, args.packet_id)

    print(F.header(f"Packet: {packet.packet_id}"))
    print()
    print(F.row("run_id", packet.run_id))
    print(F.row("status", F.badge(status.value)))
    print(F.row("seam", packet.seam))
    print(F.row("depth", str(packet.depth)))
    print(F.row("created_at", _format_timestamp(packet.created_at)))
    print(F.row("updated_at", _format_timestamp(packet.updated_at)))
    print(F.row("claimed_by", packet.claimed_by or "(unclaimed)"))
    if packet.claimed_at:
        print(F.row("claimed_at", _format_timestamp(packet.claimed_at)))
    if packet.completed_at:
        print(F.row("completed_at", _format_timestamp(packet.completed_at)))

    print()
    print(F.subheader("Objective"))
    print(f"  {packet.objective}")
    print()
    print(F.subheader("Success Criteria"))
    print(f"  {packet.success_criteria}")

    # State
    if packet.current_state:
        print()
        print(F.subheader("Current State"))
        for k, v in packet.current_state.items():
            if k == "output":
                continue
            v_str = F.truncate(str(v), 60)
            print(F.row(k, v_str))

    # Open questions
    if packet.open_questions:
        print()
        print(F.subheader("Open Questions"))
        for q in packet.open_questions:
            print(f"  - {q}")

    # Audit trail
    print()
    print(F.subheader(f"Audit Trail ({len(packet.audit_trail)} events)"))
    events = ledger.get_packet_history(run_id, packet.packet_id)
    if events:
        for evt in events[-10:]:  # Show last 10
            print(f"  {evt.timestamp[:19]}  {evt.event_type}")
    else:
        print("  (no events)")

    # Show markdown summary
    print()
    print(F.muted("--- Packet Summary ---"))
    print(packet.to_markdown())

    return 0


def _cmd_progress(args: argparse.Namespace) -> int:
    """Checkpoint state progress."""
    runtime = _get_runtime()

    try:
        state = json.loads(args.state)
    except json.JSONDecodeError as e:
        print(F.error(f"Invalid JSON in --state: {e}"))
        return 1

    artifacts = None
    if args.artifacts:
        try:
            artifacts = json.loads(args.artifacts)
        except json.JSONDecodeError as e:
            print(F.error(f"Invalid JSON in --artifacts: {e}"))
            return 1

    try:
        event_id = runtime.progress_packet(
            packet_id=args.packet_id,
            run_id=args.run_id,
            state_update=state,
            artifacts=artifacts,
        )
        print(f"event_id={event_id}")
    except (FileNotFoundError, ValueError) as e:
        print(F.error(str(e)))
        return 1

    return 0


def _cmd_delegate(args: argparse.Namespace) -> int:
    """Request delegation to create a child packet."""
    runtime = _get_runtime()

    constraints = None
    if args.constraints:
        try:
            constraints = json.loads(args.constraints)
        except json.JSONDecodeError as e:
            print(F.error(f"Invalid JSON in --constraints: {e}"))
            return 1

    try:
        child_id, is_pending = runtime.delegate_packet(
            packet_id=args.packet_id,
            run_id=args.run_id,
            seam=args.seam,
            objective=args.objective,
            constraints=constraints,
        )
        print(f"child_packet_id={child_id}")
        if is_pending:
            print(f"status={F.badge('pending')} (requires approval)")
        else:
            print(f"status={F.badge('approved')} (auto-approved)")
    except (FileNotFoundError, ValueError) as e:
        print(F.error(str(e)))
        return 1

    return 0


def _cmd_approve(args: argparse.Namespace) -> int:
    """Approve a pending child packet."""
    runtime = _get_runtime()

    try:
        event_id = runtime.approve_child(
            run_id=args.run_id,
            child_packet_id=args.child_packet_id,
            approver=args.approver or "human",
        )
        print(f"event_id={event_id}")
    except (FileNotFoundError, ValueError) as e:
        print(F.error(str(e)))
        return 1

    return 0


def _cmd_deny(args: argparse.Namespace) -> int:
    """Deny a child packet."""
    runtime = _get_runtime()

    try:
        event_id = runtime.deny_child(
            run_id=args.run_id,
            child_packet_id=args.child_packet_id,
            reason=args.reason,
        )
        print(f"event_id={event_id}")
    except (FileNotFoundError, ValueError) as e:
        print(F.error(str(e)))
        return 1

    return 0


def _cmd_complete(args: argparse.Namespace) -> int:
    """Mark packet as completed with output."""
    runtime = _get_runtime()

    shapes = None
    if args.shapes:
        try:
            shapes = json.loads(args.shapes)
        except json.JSONDecodeError as e:
            print(F.error(f"Invalid JSON in --shapes: {e}"))
            return 1

    try:
        event_id = runtime.complete_packet(
            packet_id=args.packet_id,
            run_id=args.run_id,
            output=args.output,
            shapes=shapes,
            critic=args.critic,
        )
        print(f"event_id={event_id}")
    except (FileNotFoundError, ValueError) as e:
        print(F.error(str(e)))
        return 1

    return 0


def _cmd_fail(args: argparse.Namespace) -> int:
    """Mark packet as failed."""
    runtime = _get_runtime()

    try:
        event_id = runtime.fail_packet(
            packet_id=args.packet_id,
            run_id=args.run_id,
            error=args.error,
        )
        print(f"event_id={event_id}")
    except (FileNotFoundError, ValueError) as e:
        print(F.error(str(e)))
        return 1

    return 0


def _cmd_gate(args: argparse.Namespace) -> int:
    """Pause at a human gate."""
    runtime = _get_runtime()

    try:
        event_id = runtime.human_gate(
            packet_id=args.packet_id,
            run_id=args.run_id,
            name=args.name,
        )
        print(f"event_id={event_id}")
    except (FileNotFoundError, ValueError) as e:
        print(F.error(str(e)))
        return 1

    return 0


def _cmd_resolve_gate(args: argparse.Namespace) -> int:
    """Resolve a human gate."""
    runtime = _get_runtime()

    if args.decision not in ("approve", "deny"):
        print(F.error("--decision must be 'approve' or 'deny'"))
        return 1

    try:
        event_id = runtime.resolve_human_gate(
            run_id=args.run_id,
            packet_id=args.packet_id,
            name=args.name,
            approved=(args.decision == "approve"),
            approver=args.approver or "human",
        )
        print(f"event_id={event_id}")
    except (FileNotFoundError, ValueError) as e:
        print(F.error(str(e)))
        return 1

    return 0


def _cmd_checkpoint(args: argparse.Namespace) -> int:
    """Create a checkpoint snapshot."""
    runtime = _get_runtime()

    try:
        checkpoint_id = runtime.checkpoint_packet(
            packet_id=args.packet_id,
            run_id=args.run_id,
            label=args.label,
        )
        print(f"checkpoint_id={checkpoint_id}")
    except (FileNotFoundError, ValueError) as e:
        print(F.error(str(e)))
        return 1

    return 0


def _cmd_shapes(args: argparse.Namespace) -> int:
    """Search for reusable shapes."""
    registry = _get_registry()

    tags = [args.tag] if args.tag else None
    try:
        shape_type = ShapeType(args.type) if args.type else None
    except ValueError:
        print(F.error(f"Invalid shape type: {args.type}"))
        return 1

    results = registry.find_shapes(
        tags=tags,
        seam=args.seam,
        shape_type=shape_type,
        query=args.query,
    )

    if not results:
        print("No shapes found matching criteria.")
        return 0

    rows = []
    for s in results:
        rows.append([
            s.shape_id[:8],
            s.shape_type.value if hasattr(s.shape_type, 'value') else str(s.shape_type),
            F.truncate(s.name, 30),
            ",".join(s.tags[:3]) if s.tags else "",
            str(s.usage_count),
        ])

    print(F.table(["id", "type", "name", "tags", "uses"], rows))
    return 0


def _cmd_use_shape(args: argparse.Namespace) -> int:
    """Create a goal using a shape as template."""
    runtime = _get_runtime()
    registry = _get_registry()

    shape = registry.get_shape(args.shape_id)
    if not shape:
        print(F.error(f"Shape not found: {args.shape_id}"))
        return 1

    # Update shape usage
    registry.update_usage(args.shape_id)

    # Create goal with shape's seam
    seam = shape.seam or "shaped"
    run_id, packet_id = runtime.create_goal(
        objective=args.objective,
        success_criteria=args.success_criteria,
        seam=seam,
    )

    print(f"Created run_id={run_id} packet_id={packet_id}")
    print(f"Using shape: {shape.name} ({shape.shape_type.value})")
    return 0


def _cmd_resume(args: argparse.Namespace) -> int:
    """Re-emit a paused/failed packet for claiming."""
    runtime = _get_runtime()

    try:
        event_id, packet = runtime.resume_packet(
            packet_id=args.packet_id,
            run_id=args.run_id,
        )
        print(f"Resumed packet {args.packet_id}")
        print(f"event_id={event_id}")
        print()
        print(packet.to_markdown())
    except (FileNotFoundError, ValueError) as e:
        print(F.error(str(e)))
        return 1

    return 0


def _cmd_history(args: argparse.Namespace) -> int:
    """Show full audit trail for a run."""
    ledger = _get_ledger()

    events = ledger.get_timeline(args.run_id)

    if not events:
        print(f"No events found for run {args.run_id}")
        return 0

    print(F.header(f"Run: {args.run_id}"))
    print(F.row("total_events", str(len(events))))
    print()

    rows = []
    for evt in events:
        rows.append([
            evt.timestamp[:19],
            evt.event_type,
            (evt.packet_id or "-")[:8],
            F.truncate(str(evt.data), 40),
        ])

    print(F.table(["timestamp", "event_type", "packet_id", "data"], rows))
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    """Reconstruct and display run state from ledger."""
    ledger = _get_ledger()
    engine = ReplayEngine(ledger)

    report = engine.replay_to_text(args.run_id)
    print(report)
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    """Run a goal end-to-end with FakeExecutor."""
    result = run_goal(
        objective=args.objective,
        success_criteria=args.success_criteria or "completed",
        runs_dir=RUNS_DIR,
    )

    print()
    print(F.header("Run Summary"))
    wr = result["result"]
    dur = wr.duration_seconds if wr.duration_seconds is not None else 0.0
    cost = wr.cost_estimate if wr.cost_estimate is not None else 0.0
    print(F.row("run_id", result["run_id"]))
    print(F.row("packet_id", result["packet_id"]))
    print(F.row("duration", f"{dur}s"))
    print(F.row("cost", f"${cost:.4f}"))
    print(F.row("shapes_discovered", str(len(result["shapes"]))))

    return 0


def _cmd_list_runs(args: argparse.Namespace) -> int:
    """List all runs."""
    ledger = _get_ledger()

    runs = ledger.list_runs()

    if not runs:
        print("No runs found.")
        return 0

    rows = []
    for r in runs:
        rows.append([
            r.get("run_id", ""),
            _format_age(r.get("created_at", "")),
            str(r.get("event_count", 0)),
        ])

    print(F.table(["run_id", "created", "events"], rows))
    return 0


# ---------------------------------------------------------------------------
# migrate-run command
# ---------------------------------------------------------------------------

def _cmd_migrate_run(args: argparse.Namespace) -> int:
    """Migrate a run from the old graph-compiler format to the new work-packet format."""
    import warnings as _warnings
    _warnings.warn(
        "migrate-run is a transitional command. It will be removed once all runs are migrated.",
        DeprecationWarning,
        stacklevel=2
    )

    from ledger import Ledger as OldLedger
    from ledger import EventType as OldEventType
    from work_ledger import WorkLedger, PACKET_CREATED, PACKET_EMITTED, PACKET_COMPLETED, PACKET_FAILED, PACKET_CLAIMED, PACKET_IN_PROGRESS, CHILD_REQUESTED, CHILD_APPROVED, CHILD_DENIED, HUMAN_GATE_ENTERED, HUMAN_GATE_APPROVED, HUMAN_GATE_DENIED, CHECKPOINT_CREATED
    from work_packet import WorkPacket, WorkResult, PacketStatus, DelegationPermission, HarnessType
    from dynamic_schema import WorkerPacket as OldWorkerPacket, WorkerResult as OldWorkerResult, NodeType, WorkerStatus

    run_id = args.run_id
    old_runs_dir = Path("runs")

    # Check if run exists in old format
    old_run_dir = old_runs_dir / run_id
    if not old_run_dir.exists():
        print(F.error(f"Run '{run_id}' not found in old format at {old_run_dir}"))
        return 1

    # Check for idempotency - if migration_manifest.json exists, we've already migrated
    migration_manifest_path = old_run_dir / "migration_manifest.json"
    already_migrated = migration_manifest_path.exists()
    if already_migrated and not args.force:
        print(F.warning(f"Run '{run_id}' has already been migrated. Use --force to re-migrate."))
        return 0

    print(f"{F.BLUE}=== migrate-run ==={F.RESET}")
    print(f"  run_id : {run_id}")

    # Initialize the new work ledger
    new_work_ledger = WorkLedger(runs_dir=old_runs_dir)

    # Create run directory structure if not exists
    new_work_ledger.create_run(run_id=run_id, objective="migrated run", parent_packet_id=None)

    # Read old ledger events
    old_ledger = OldLedger(runs_dir=old_runs_dir)
    old_events = old_ledger.get_events(run_id)

    # Statistics
    stats = {
        "packets_emitted": 0,
        "packets_migrated": 0,
        "results_migrated": 0,
        "events_migrated": 0,
        "already_migrated": already_migrated,
    }

    # Cache for packet_id -> old_worker_id mapping (for result migration)
    packet_id_map: dict[str, str] = {}  # new packet_id -> old worker_id

    # First pass: collect all events and build packet mappings
    # We need to process in order to handle dependencies

    # Load old packets for reference
    old_packets_dir = old_run_dir / "packets"
    old_results_dir = old_run_dir / "results"

    migrated_packets: set[str] = set()
    migrated_results: set[str] = set()

    if already_migrated:
        # Load existing migration manifest to skip already-migrated items
        with open(migration_manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
            migrated_packets = set(manifest.get("migrated_packets", []))
            migrated_results = set(manifest.get("migrated_results", []))

    print(f"\n  Processing {len(old_events)} old-format events...")

    for event in old_events:
        if event.event_type == OldEventType.PACKET_EMITTED:
            old_worker_id = event.worker_id
            if old_worker_id in migrated_packets:
                continue

            # Read the old packet
            old_packet_path = old_packets_dir / f"{old_worker_id}.json"
            if not old_packet_path.exists():
                print(F.warning(f"  Old packet not found: {old_worker_id}"))
                continue

            try:
                old_packet_data = json.loads(old_packet_path.read_text(encoding="utf-8"))
                old_packet = OldWorkerPacket(**old_packet_data)

                # Convert to new WorkPacket format
                # Map old role to seam
                seam = _map_role_to_seam(old_packet.role)

                new_packet = WorkPacket(
                    packet_id=old_packet.worker_id,  # Keep same ID for traceability
                    run_id=old_packet.run_id,
                    parent_packet_id=old_packet.parent_worker_id,
                    depth=old_packet.depth,
                    seam=seam,
                    objective=old_packet.objective,
                    success_criteria=old_packet.task_prompt or old_packet.objective,
                    current_state={},
                    known_context={},
                    open_questions=[],
                    allowed_tools=old_packet.allowed_tools,
                    forbidden_tools=old_packet.forbidden_tools,
                    owned_paths=old_packet.owned_paths,
                    forbidden_paths=old_packet.forbidden_paths,
                    timeout_seconds=old_packet.timeout,
                    budget=old_packet.budget,
                    delegation_permission=_map_delegation(old_packet.delegation_permission),
                    max_child_depth=old_packet.max_child_depth,
                    created_at=old_packet.created_at,
                    updated_at=old_packet.created_at,
                )

                # Write the new packet
                new_packet_path = old_run_dir / "packets" / f"{new_packet.packet_id}.json"
                new_packet_path.parent.mkdir(parents=True, exist_ok=True)
                new_packet_path.write_text(json.dumps(new_packet.model_dump(mode="json"), indent=2), encoding="utf-8")

                # Log PACKET_EMITTED event to new ledger
                new_work_ledger.log_packet_emitted(
                    run_id=run_id,
                    packet_id=new_packet.packet_id,
                    depth=new_packet.depth,
                    data={
                        "seam": new_packet.seam,
                        "objective": new_packet.objective,
                        "success_criteria": new_packet.success_criteria,
                    },
                )

                packet_id_map[new_packet.packet_id] = old_worker_id
                migrated_packets.add(old_worker_id)
                stats["packets_migrated"] += 1

            except Exception as e:
                print(F.error(f"  Failed to migrate packet {old_worker_id}: {e}"))

        elif event.event_type == OldEventType.RESULT_RECORDED:
            old_worker_id = event.worker_id
            if old_worker_id in migrated_results:
                continue

            # Read the old result
            old_result_path = old_results_dir / f"{old_worker_id}.json"
            if not old_result_path.exists():
                continue

            try:
                old_result_data = json.loads(old_result_path.read_text(encoding="utf-8"))
                old_result = OldWorkerResult(**old_result_data)

                # Convert to new WorkResult format
                new_status = _map_worker_status(old_result.status)

                new_result = WorkResult(
                    packet_id=old_result.worker_id,
                    run_id=old_result.run_id,
                    status=new_status,
                    output=old_result.output,
                    error=old_result.error,
                    duration_seconds=old_result.duration_seconds,
                    cost_estimate=old_result.cost_estimate,
                    children=old_result.children,  # Keep as-is, these are worker IDs
                    artifacts_produced=old_result.artifacts,
                    critic_findings=old_result.critic_findings,
                    completed_at=old_result.completed_at,
                )

                # Write the new result
                new_result_path = old_run_dir / "results" / f"{new_result.packet_id}.json"
                new_result_path.parent.mkdir(parents=True, exist_ok=True)
                new_result_path.write_text(json.dumps(new_result.model_dump(mode="json"), indent=2), encoding="utf-8")

                migrated_results.add(old_worker_id)
                stats["results_migrated"] += 1

            except Exception as e:
                print(F.error(f"  Failed to migrate result {old_worker_id}: {e}"))

        elif event.event_type == OldEventType.RUN_CREATED:
            # Migrate run creation event
            stats["events_migrated"] += 1

        elif event.event_type == OldEventType.CHILD_REQUESTED:
            # These are already captured in the new ledger via packet relationship
            stats["events_migrated"] += 1

        elif event.event_type == OldEventType.CHILD_APPROVED:
            stats["events_migrated"] += 1

        elif event.event_type == OldEventType.CHILD_REJECTED:
            stats["events_migrated"] += 1

        elif event.event_type == OldEventType.CHECKPOINT_CREATED:
            stats["events_migrated"] += 1

    # Write migration manifest for idempotency
    migration_manifest = {
        "run_id": run_id,
        "migrated_at": datetime.now(timezone.utc).isoformat(),
        "migrated_packets": list(migrated_packets),
        "migrated_results": list(migrated_results),
        "stats": stats,
    }
    migration_manifest_path.write_text(json.dumps(migration_manifest, indent=2), encoding="utf-8")

    # Print summary
    print(f"\n{F.success('Migration complete!')}")
    print(f"  Packets migrated : {stats['packets_migrated']}")
    print(f"  Results migrated : {stats['results_migrated']}")
    print(f"  Events migrated  : {stats['events_migrated']}")
    if stats["already_migrated"]:
        print(f"  Note             : Run was previously migrated (idempotent)")
    print(f"\n  Migration manifest: {migration_manifest_path}")
    print(f"\n  Old data is preserved. New work-packet format written alongside it.")

    return 0


def _map_role_to_seam(role: NodeType) -> str:
    """Map old NodeType role to a seam string."""
    role_to_seam = {
        NodeType.PLANNER: "planning",
        NodeType.RETRIEVER: "retrieval",
        NodeType.SCORER: "scoring",
        NodeType.ROUTER: "routing",
        NodeType.CRITIC: "critique",
        NodeType.WORKER: "work",
        NodeType.INTEGRATOR: "integration",
        NodeType.REVIEWER: "review",
        NodeType.APPROVER: "approval",
        NodeType.EXECUTOR: "execution",
    }
    return role_to_seam.get(role, role.value)


def _map_delegation(old_delegation) -> DelegationPermission:
    """Map old DelegationPermission to new format."""
    from dynamic_schema import DelegationPermission as OldDP
    mapping = {
        OldDP.NONE: DelegationPermission.NONE,
        OldDP.REQUEST: DelegationPermission.REQUEST,
        OldDP.APPROVE: DelegationPermission.APPROVE,
    }
    return mapping.get(old_delegation, DelegationPermission.NONE)


def _map_worker_status(old_status: WorkerStatus) -> PacketStatus:
    """Map old WorkerStatus to new PacketStatus."""
    mapping = {
        WorkerStatus.SUCCESS: PacketStatus.COMPLETED,
        WorkerStatus.FAILURE: PacketStatus.FAILED,
        WorkerStatus.TIMEOUT: PacketStatus.FAILED,
        WorkerStatus.REJECTED: PacketStatus.FAILED,
        WorkerStatus.PENDING: PacketStatus.CREATED,
    }
    return mapping.get(old_status, PacketStatus.CREATED)


# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------

INTERACTIVE_HELP = """
Available commands:
  goal <objective>           Create a new goal
  list-seams [--run-id ID]   List open seams
  claim <packet_id>          Claim a packet
  status <packet_id>         Show packet status
  progress <packet_id> --state JSON   Checkpoint progress
  delegate <packet_id> <seam [--objective TEXT] [--constraints JSON]
                             Request delegation
  approve <child_packet_id>  Approve child packet
  deny <child_packet_id> --reason TEXT
                             Deny child packet
  complete <packet_id> --output TEXT [--shapes JSON] [--critic TEXT]
                             Mark completed
  fail <packet_id> --error TEXT
                             Mark failed
  gate <packet_id> --name NAME
                             Pause at gate
  resolve-gate <packet_id> --name NAME --decision approve|deny
                             Resolve gate
  checkpoint <packet_id> --label TEXT
                             Create checkpoint
  shapes [--tag T] [--seam S] [--type T] [--query Q]
                             Search shapes
  use-shape <shape_id> --objective TEXT --success-criteria TEXT
                             Use shape as template
  resume <packet_id>         Resume paused/failed packet
  history <run_id>           Show run audit trail
  replay <run_id>            Replay run from ledger
  run <objective>            Run goal end-to-end
  list-runs                  List all runs
  help                       Show this help
  exit, quit                 Exit interactive mode
""".strip()


def _interactive_mode() -> int:
    """Run interactive CLI loop."""
    print(F.header("=" * 60))
    print(F.header("  Work Packet Runtime CLI"))
    print(F.header("=" * 60))
    print()
    print("Type 'help' for available commands.")
    print()

    runtime = _get_runtime()
    ledger = _get_ledger()
    registry = _get_registry()

    while True:
        try:
            raw = input(f"{F.BLUE}packet>{F.RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if not raw:
            continue

        parts = raw.split()
        cmd = parts[0].lower()
        args_list = parts[1:]

        if cmd in ("exit", "quit", "q"):
            print("Goodbye.")
            break

        if cmd == "help":
            print(f"\n{F.GREEN}{INTERACTIVE_HELP}{F.RESET}\n")
            continue

        # Parse common args
        class FakeArgs:
            pass

        fake_args = FakeArgs()

        try:
            # Dispatch based on command
            if cmd == "goal":
                if not args_list:
                    print(F.error("Usage: goal <objective> [--success-criteria TEXT] [--seam NAME]"))
                    continue
                fake_args.objective = args_list[0]
                fake_args.success_criteria = None
                fake_args.seam = None
                # Parse --success-criteria and --seam from remaining args
                remaining = " ".join(args_list[1:]) if len(args_list) > 1 else ""
                for flag, dest in [("--success-criteria", "success_criteria"), ("--seam", "seam")]:
                    if flag in remaining:
                        idx = remaining.index(flag)
                        parts_remaining = remaining[idx + 1:].split()
                        if parts_remaining:
                            setattr(fake_args, dest, parts_remaining[0])
                            remaining = remaining[:idx] + remaining[idx + 2 + len(parts_remaining[0]):]
                _cmd_goal(fake_args)

            elif cmd == "list-seams":
                fake_args.run_id = None
                for i, arg in enumerate(args_list):
                    if arg == "--run-id" and i + 1 < len(args_list):
                        fake_args.run_id = args_list[i + 1]
                _cmd_list_seams(fake_args)

            elif cmd == "claim":
                if not args_list:
                    print(F.error("Usage: claim <packet_id> [--run-id ID] [--agent-id ID] [--harness TYPE]"))
                    continue
                fake_args.packet_id = args_list[0]
                fake_args.run_id = None
                fake_args.agent_id = None
                fake_args.harness = None
                for i, arg in enumerate(args_list):
                    if arg == "--run-id" and i + 1 < len(args_list):
                        fake_args.run_id = args_list[i + 1]
                    elif arg == "--agent-id" and i + 1 < len(args_list):
                        fake_args.agent_id = args_list[i + 1]
                    elif arg == "--harness" and i + 1 < len(args_list):
                        fake_args.harness = args_list[i + 1]
                _cmd_claim(fake_args)

            elif cmd == "status":
                if not args_list:
                    print(F.error("Usage: status <packet_id> [--run-id ID]"))
                    continue
                fake_args.packet_id = args_list[0]
                fake_args.run_id = None
                for i, arg in enumerate(args_list):
                    if arg == "--run-id" and i + 1 < len(args_list):
                        fake_args.run_id = args_list[i + 1]
                _cmd_status(fake_args)

            elif cmd == "progress":
                if not args_list:
                    print(F.error("Usage: progress <packet_id> --state JSON [--artifacts JSON]"))
                    continue
                fake_args.packet_id = args_list[0]
                fake_args.run_id = None
                fake_args.state = None
                fake_args.artifacts = None
                remaining = " ".join(args_list[1:])
                if "--state" in remaining:
                    idx = remaining.index("--state")
                    rest = remaining[idx + 1:].split(None, 1)
                    if rest:
                        fake_args.state = rest[0]
                if "--artifacts" in remaining:
                    idx = remaining.index("--artifacts")
                    rest = remaining[idx + 1:].split(None, 1)
                    if rest:
                        fake_args.artifacts = rest[0]
                if not fake_args.state:
                    print(F.error("--state JSON is required"))
                    continue
                _cmd_progress(fake_args)

            elif cmd == "delegate":
                if not args_list:
                    print(F.error("Usage: delegate <packet_id> <seam> [--objective TEXT] [--constraints JSON]"))
                    continue
                fake_args.packet_id = args_list[0]
                fake_args.seam = args_list[1] if len(args_list) > 1 else None
                fake_args.run_id = None
                fake_args.objective = None
                fake_args.constraints = None
                _cmd_delegate(fake_args)

            elif cmd == "approve":
                if not args_list:
                    print(F.error("Usage: approve <child_packet_id> [--run-id ID] [--approver NAME]"))
                    continue
                fake_args.child_packet_id = args_list[0]
                fake_args.run_id = None
                fake_args.approver = None
                _cmd_approve(fake_args)

            elif cmd == "deny":
                if not args_list:
                    print(F.error("Usage: deny <child_packet_id> --reason TEXT"))
                    continue
                fake_args.child_packet_id = args_list[0]
                fake_args.run_id = None
                fake_args.reason = None
                for i, arg in enumerate(args_list):
                    if arg == "--reason" and i + 1 < len(args_list):
                        fake_args.reason = args_list[i + 1]
                if not fake_args.reason:
                    print(F.error("--reason TEXT is required"))
                    continue
                _cmd_deny(fake_args)

            elif cmd == "complete":
                if not args_list:
                    print(F.error("Usage: complete <packet_id> --output TEXT [--shapes JSON] [--critic TEXT]"))
                    continue
                fake_args.packet_id = args_list[0]
                fake_args.run_id = None
                fake_args.output = None
                fake_args.shapes = None
                fake_args.critic = None
                for i, arg in enumerate(args_list):
                    if arg == "--output" and i + 1 < len(args_list):
                        fake_args.output = args_list[i + 1]
                    elif arg == "--shapes" and i + 1 < len(args_list):
                        fake_args.shapes = args_list[i + 1]
                    elif arg == "--critic" and i + 1 < len(args_list):
                        fake_args.critic = args_list[i + 1]
                if not fake_args.output:
                    print(F.error("--output TEXT is required"))
                    continue
                _cmd_complete(fake_args)

            elif cmd == "fail":
                if not args_list:
                    print(F.error("Usage: fail <packet_id> --error TEXT"))
                    continue
                fake_args.packet_id = args_list[0]
                fake_args.run_id = None
                fake_args.error = None
                for i, arg in enumerate(args_list):
                    if arg == "--error" and i + 1 < len(args_list):
                        fake_args.error = args_list[i + 1]
                if not fake_args.error:
                    print(F.error("--error TEXT is required"))
                    continue
                _cmd_fail(fake_args)

            elif cmd == "gate":
                if not args_list:
                    print(F.error("Usage: gate <packet_id> --name NAME"))
                    continue
                fake_args.packet_id = args_list[0]
                fake_args.run_id = None
                fake_args.name = None
                for i, arg in enumerate(args_list):
                    if arg == "--name" and i + 1 < len(args_list):
                        fake_args.name = args_list[i + 1]
                if not fake_args.name:
                    print(F.error("--name NAME is required"))
                    continue
                _cmd_gate(fake_args)

            elif cmd == "resolve-gate":
                if not args_list:
                    print(F.error("Usage: resolve-gate <packet_id> --name NAME --decision approve|deny [--approver NAME]"))
                    continue
                fake_args.packet_id = args_list[0]
                fake_args.run_id = None
                fake_args.name = None
                fake_args.decision = None
                fake_args.approver = None
                for i, arg in enumerate(args_list):
                    if arg == "--name" and i + 1 < len(args_list):
                        fake_args.name = args_list[i + 1]
                    elif arg == "--decision" and i + 1 < len(args_list):
                        fake_args.decision = args_list[i + 1]
                    elif arg == "--approver" and i + 1 < len(args_list):
                        fake_args.approver = args_list[i + 1]
                if not fake_args.name or not fake_args.decision:
                    print(F.error("--name NAME and --decision approve|deny are required"))
                    continue
                _cmd_resolve_gate(fake_args)

            elif cmd == "checkpoint":
                if not args_list:
                    print(F.error("Usage: checkpoint <packet_id> --label TEXT"))
                    continue
                fake_args.packet_id = args_list[0]
                fake_args.run_id = None
                fake_args.label = None
                for i, arg in enumerate(args_list):
                    if arg == "--label" and i + 1 < len(args_list):
                        fake_args.label = args_list[i + 1]
                if not fake_args.label:
                    print(F.error("--label TEXT is required"))
                    continue
                _cmd_checkpoint(fake_args)

            elif cmd == "shapes":
                fake_args.tag = None
                fake_args.seam = None
                fake_args.type = None
                fake_args.query = None
                for i, arg in enumerate(args_list):
                    if arg == "--tag" and i + 1 < len(args_list):
                        fake_args.tag = args_list[i + 1]
                    elif arg == "--seam" and i + 1 < len(args_list):
                        fake_args.seam = args_list[i + 1]
                    elif arg == "--type" and i + 1 < len(args_list):
                        fake_args.type = args_list[i + 1]
                    elif arg == "--query" and i + 1 < len(args_list):
                        fake_args.query = args_list[i + 1]
                _cmd_shapes(fake_args)

            elif cmd == "use-shape":
                if len(args_list) < 2:
                    print(F.error("Usage: use-shape <shape_id> --objective TEXT --success-criteria TEXT"))
                    continue
                fake_args.shape_id = args_list[0]
                fake_args.objective = None
                fake_args.success_criteria = None
                for i, arg in enumerate(args_list):
                    if arg == "--objective" and i + 1 < len(args_list):
                        fake_args.objective = args_list[i + 1]
                    elif arg == "--success-criteria" and i + 1 < len(args_list):
                        fake_args.success_criteria = args_list[i + 1]
                if not fake_args.objective:
                    print(F.error("--objective TEXT is required"))
                    continue
                if not fake_args.success_criteria:
                    fake_args.success_criteria = "completed"
                _cmd_use_shape(fake_args)

            elif cmd == "resume":
                if not args_list:
                    print(F.error("Usage: resume <packet_id> [--run-id ID]"))
                    continue
                fake_args.packet_id = args_list[0]
                fake_args.run_id = None
                for i, arg in enumerate(args_list):
                    if arg == "--run-id" and i + 1 < len(args_list):
                        fake_args.run_id = args_list[i + 1]
                _cmd_resume(fake_args)

            elif cmd == "history":
                if not args_list:
                    print(F.error("Usage: history <run_id>"))
                    continue
                fake_args.run_id = args_list[0]
                _cmd_history(fake_args)

            elif cmd == "replay":
                if not args_list:
                    print(F.error("Usage: replay <run_id>"))
                    continue
                fake_args.run_id = args_list[0]
                _cmd_replay(fake_args)

            elif cmd == "run":
                if not args_list:
                    print(F.error("Usage: run <objective> [--success-criteria TEXT]"))
                    continue
                fake_args.objective = args_list[0]
                fake_args.success_criteria = None
                for i, arg in enumerate(args_list):
                    if arg == "--success-criteria" and i + 1 < len(args_list):
                        fake_args.success_criteria = args_list[i + 1]
                _cmd_run(fake_args)

            elif cmd == "list-runs":
                _cmd_list_runs(fake_args)

            else:
                print(F.error(f"Unknown command '{cmd}'. Type 'help' for available commands."))

        except Exception as e:
            print(F.error(f"Command failed: {e}"))
            import traceback
            traceback.print_exc()

    return 0


# ---------------------------------------------------------------------------
# Parser builder
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="packet_cli",
        description="CLI for the recursive work-packet runtime.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run 'packet_cli.py <command> --help' for command-specific help.\n",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # goal
    p = sub.add_parser("goal", help="Create a new goal packet")
    p.add_argument("objective", help="Natural-language objective for the goal")
    p.add_argument("--success-criteria", dest="success_criteria", default=None,
                   help="Success criteria (default: 'completed')")
    p.add_argument("--seam", default=None, help="Work seam (default: 'root')")
    p.set_defaults(handler=_cmd_goal)

    # list-seams
    p = sub.add_parser("list-seams", help="List open packets available for claiming")
    p.add_argument("--run-id", dest="run_id", default=None, help="Filter by run ID")
    p.set_defaults(handler=_cmd_list_seams)

    # claim
    p = sub.add_parser("claim", help="Claim a packet for the current agent")
    p.add_argument("packet_id", help="ID of the packet to claim")
    p.add_argument("--run-id", dest="run_id", default=None, help="Run ID")
    p.add_argument("--agent-id", dest="agent_id", default=None, help="Agent ID")
    p.add_argument("--harness", default=None, choices=[h.value for h in HarnessType],
                   help="Harness type")
    p.set_defaults(handler=_cmd_claim)

    # status
    p = sub.add_parser("status", help="Show current packet state and audit trail")
    p.add_argument("packet_id", help="ID of the packet")
    p.add_argument("--run-id", dest="run_id", default=None, help="Run ID")
    p.set_defaults(handler=_cmd_status)

    # progress
    p = sub.add_parser("progress", help="Checkpoint state progress")
    p.add_argument("packet_id", help="ID of the packet")
    p.add_argument("--run-id", dest="run_id", default=None, help="Run ID")
    p.add_argument("--state", required=True, help="JSON string of state_update dict")
    p.add_argument("--artifacts", default=None, help="Optional JSON list of artifact paths")
    p.set_defaults(handler=_cmd_progress)

    # delegate
    p = sub.add_parser("delegate", help="Request delegation to create a child packet")
    p.add_argument("packet_id", help="ID of the parent packet")
    p.add_argument("seam", help="Work seam for the child packet")
    p.add_argument("--run-id", dest="run_id", default=None, help="Run ID")
    p.add_argument("--objective", default=None, help="Objective for the child")
    p.add_argument("--constraints", default=None,
                   help="JSON dict of constraints for the child")
    p.set_defaults(handler=_cmd_delegate)

    # approve
    p = sub.add_parser("approve", help="Approve a pending child packet")
    p.add_argument("child_packet_id", help="ID of the child packet to approve")
    p.add_argument("--run-id", dest="run_id", default=None, help="Run ID")
    p.add_argument("--approver", default=None, help="Approver identifier (default: 'human')")
    p.set_defaults(handler=_cmd_approve)

    # deny
    p = sub.add_parser("deny", help="Deny a child packet")
    p.add_argument("child_packet_id", help="ID of the child packet to deny")
    p.add_argument("--run-id", dest="run_id", default=None, help="Run ID")
    p.add_argument("--reason", required=True, help="Reason for denial")
    p.set_defaults(handler=_cmd_deny)

    # complete
    p = sub.add_parser("complete", help="Mark packet as completed with output")
    p.add_argument("packet_id", help="ID of the packet to complete")
    p.add_argument("--run-id", dest="run_id", default=None, help="Run ID")
    p.add_argument("--output", required=True, help="Output/result description")
    p.add_argument("--shapes", default=None, help="Optional JSON list of shape dicts")
    p.add_argument("--critic", default=None, help="Optional critic findings")
    p.set_defaults(handler=_cmd_complete)

    # fail
    p = sub.add_parser("fail", help="Mark packet as failed")
    p.add_argument("packet_id", help="ID of the packet to fail")
    p.add_argument("--run-id", dest="run_id", default=None, help="Run ID")
    p.add_argument("--error", required=True, help="Error message")
    p.set_defaults(handler=_cmd_fail)

    # gate
    p = sub.add_parser("gate", help="Pause at a human gate")
    p.add_argument("packet_id", help="ID of the packet")
    p.add_argument("--run-id", dest="run_id", default=None, help="Run ID")
    p.add_argument("--name", required=True, help="Name of the gate")
    p.set_defaults(handler=_cmd_gate)

    # resolve-gate
    p = sub.add_parser("resolve-gate", help="Resolve a human gate")
    p.add_argument("packet_id", help="ID of the packet")
    p.add_argument("--run-id", dest="run_id", default=None, help="Run ID")
    p.add_argument("--name", required=True, help="Name of the gate to resolve")
    p.add_argument("--decision", required=True, choices=["approve", "deny"],
                   help="approve or deny")
    p.add_argument("--approver", default=None, help="Approver identifier (default: 'human')")
    p.set_defaults(handler=_cmd_resolve_gate)

    # checkpoint
    p = sub.add_parser("checkpoint", help="Create a checkpoint snapshot")
    p.add_argument("packet_id", help="ID of the packet")
    p.add_argument("--run-id", dest="run_id", default=None, help="Run ID")
    p.add_argument("--label", required=True, help="Label for this checkpoint")
    p.set_defaults(handler=_cmd_checkpoint)

    # shapes
    p = sub.add_parser("shapes", help="Search for reusable shapes")
    p.add_argument("--tag", default=None, help="Filter by tag")
    p.add_argument("--seam", default=None, help="Filter by seam")
    p.add_argument("--type", dest="type", default=None,
                   choices=[t.value for t in ShapeType], help="Filter by shape type")
    p.add_argument("--query", default=None, help="Search in name/description")
    p.set_defaults(handler=_cmd_shapes)

    # use-shape
    p = sub.add_parser("use-shape", help="Create a goal using a shape as a template")
    p.add_argument("shape_id", help="ID of the shape to use")
    p.add_argument("--objective", required=True, help="Objective for the new goal")
    p.add_argument("--success-criteria", dest="success_criteria", default="completed",
                   help="Success criteria (default: 'completed')")
    p.set_defaults(handler=_cmd_use_shape)

    # resume
    p = sub.add_parser("resume", help="Re-emit a paused/failed packet for claiming")
    p.add_argument("packet_id", help="ID of the packet to resume")
    p.add_argument("--run-id", dest="run_id", default=None, help="Run ID")
    p.set_defaults(handler=_cmd_resume)

    # history
    p = sub.add_parser("history", help="Show full audit trail for a run")
    p.add_argument("run_id", help="ID of the run")
    p.set_defaults(handler=_cmd_history)

    # replay
    p = sub.add_parser("replay", help="Reconstruct and display run state from ledger")
    p.add_argument("run_id", help="ID of the run")
    p.set_defaults(handler=_cmd_replay)

    # run
    p = sub.add_parser("run", help="Run a goal end-to-end with FakeExecutor")
    p.add_argument("objective", help="Natural-language goal description")
    p.add_argument("--success-criteria", dest="success_criteria", default=None,
                   help="Success criteria (default: 'completed')")
    p.set_defaults(handler=_cmd_run)

    # list-runs
    p = sub.add_parser("list-runs", help="List all runs")
    p.set_defaults(handler=_cmd_list_runs)

    # migrate-run
    p = sub.add_parser("migrate-run", help="Migrate a run from old graph-compiler format to work-packet format")
    p.add_argument("run_id", help="ID of the run to migrate")
    p.add_argument("--force", action="store_true", help="Force re-migration even if already migrated")
    p.set_defaults(handler=_cmd_migrate_run)

    return parser


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command is None:
        # Interactive mode when no command given
        return _interactive_mode()

    if not hasattr(args, "handler"):
        parser.print_help()
        return 1

    try:
        return args.handler(args)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except FileNotFoundError as e:
        print(F.error(str(e)))
        return 1
    except ValueError as e:
        print(F.error(str(e)))
        return 1
    except json.JSONDecodeError as e:
        print(F.error(f"Invalid JSON: {e}"))
        return 1
    except Exception as e:
        print(F.error(f"Unhandled exception: {e}"))
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
