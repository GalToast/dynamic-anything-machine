"""
cockpit.py — CLI cockpit for the dynamic agent graph compiler system.

Usage:
    python cockpit.py <command> [options]

Commands:
    start-run      Create and launch a new run
    status         Show run status and workers
    list-runs      List all runs (with optional status filter)
    show-packet    Display packet details
    show-result    Display worker result
    record-result  Manually record a worker result
    request-child  Request creation of a child worker
    approve-child  Approve or reject a child worker request
    grant-approval Grant or deny a human approval gate
    integrate      Integrate all results and write final.md
    replay         Replay run from ledger
    checkpoint     Create a manual checkpoint
    continue-run   Resume a paused run
    show-events    Show event timeline for a run

Interactive Mode:
    python cockpit.py interactive <run_id>

Tab Completion (bash):
    eval "$(python cockpit.py --completion-bash)"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RUNS_DIR = Path(__file__).parent / "runs"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    RESET = "\033[0m"

    @staticmethod
    def wrap(text: str, width: int = 78) -> str:
        return "\n".join(textwrap.wrap(text, width=width))

    @staticmethod
    def truncate(text: str, max_len: int = 60) -> str:
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
        color = {
            "running": F.GREEN, "completed": F.BLUE,
            "paused": F.YELLOW, "failed": F.RED,
            "cancelled": F.RED, "pending": F.DIM,
            "success": F.GREEN, "failure": F.RED,
            "timeout": F.YELLOW, "rejected": F.RED,
        }.get(status.lower(), F.RESET)
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
        data_rows = ["  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)) for row in rows]
        return "\n".join([header_row, sep] + data_rows)


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

def _get_run_manifest_path(run_id: str) -> Path:
    return RUNS_DIR / run_id / "manifest.json"


def _load_manifest(run_id: str) -> dict[str, Any]:
    path = _get_run_manifest_path(run_id)
    if not path.exists():
        raise FileNotFoundError(f"No manifest found for run '{run_id}'")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_manifest(run_id: str, manifest: dict[str, Any]) -> None:
    path = _get_run_manifest_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, default=str)


def _update_manifest(run_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    manifest = _load_manifest(run_id)
    manifest.update(updates)
    manifest["updated_at"] = _now_iso()
    _save_manifest(run_id, manifest)
    return manifest


def _run_exists(run_id: str) -> bool:
    return _get_run_manifest_path(run_id).exists()


# ---------------------------------------------------------------------------
# Packet / Result formatters
# ---------------------------------------------------------------------------

def _format_packet(packet: Any) -> str:
    if hasattr(packet, "model_dump"):
        d = packet.model_dump(mode="json")
    elif isinstance(packet, dict):
        d = packet
    else:
        return str(packet)

    lines = [
        F.header("WorkerPacket"),
        F.row("run_id", d.get("run_id", "n/a")),
        F.row("worker_id", d.get("worker_id", "n/a")),
        F.row("parent_worker_id", d.get("parent_worker_id") or "(root)"),
        F.row("depth", str(d.get("depth", 0))),
        F.row("role", d.get("role", "n/a")),
        F.row("objective", F.truncate(d.get("objective", ""), 50)),
        F.row("created_at", d.get("created_at", "n/a")),
    ]
    tools = d.get("allowed_tools", [])
    lines.append(F.row("allowed_tools", str(tools) if tools else "(all)"))
    delegation = d.get("delegation_permission", "none")
    lines.append(F.row("delegation", delegation))
    if d.get("timeout"):
        lines.append(F.row("timeout", f"{d['timeout']}s"))
    if d.get("budget"):
        lines.append(F.row("budget", f"${d['budget']:.2f}"))
    task = d.get("task_prompt", "")
    if task:
        lines.append("")
        lines.append(f"  {F.DIM}task_prompt:{F.RESET}")
        for line in F.wrap(task, 72).splitlines():
            lines.append(f"    {line}")
    return "\n".join(lines)


def _format_result(result: Any) -> str:
    if hasattr(result, "model_dump"):
        d = result.model_dump(mode="json")
    elif isinstance(result, dict):
        d = result
    else:
        return str(result)

    lines = [
        F.header("WorkerResult"),
        F.row("run_id", d.get("run_id", "n/a")),
        F.row("worker_id", d.get("worker_id", "n/a")),
        F.row("parent_worker_id", d.get("parent_worker_id") or "(root)"),
        F.row("status", F.badge(d.get("status", "pending"))),
    ]
    if d.get("completed_at"):
        lines.append(F.row("completed_at", d["completed_at"]))
    if d.get("duration_seconds") is not None:
        lines.append(F.row("duration", f"{d['duration_seconds']:.2f}s"))
    if d.get("cost_estimate") is not None:
        lines.append(F.row("cost_estimate", f"${d['cost_estimate']:.4f}"))
    children = d.get("children", [])
    if children:
        lines.append(F.row("children", str(children)))
    output = d.get("output")
    if output:
        lines.append("")
        lines.append(f"  {F.DIM}output:{F.RESET}")
        out_str = str(output)
        if len(out_str) > 300:
            out_str = out_str[:297] + "..."
        for line in out_str.splitlines():
            lines.append(f"    {line}")
    error = d.get("error")
    if error:
        lines.append("")
        lines.append(f"  {F.RED}error:{F.RESET}")
        for line in error.splitlines():
            lines.append(f"    {line}")
    return "\n".join(lines)


def _format_state(state: Any) -> str:
    if hasattr(state, "__dict__"):
        d = state.__dict__
    elif isinstance(state, dict):
        d = state
    else:
        return str(state)

    lines = [
        F.header("RunState"),
        F.row("run_id", d.get("run_id", "n/a")),
        F.row("status", F.badge(str(d.get("status", "unknown")))),
    ]
    if d.get("started_at"):
        lines.append(F.row("started_at", d["started_at"]))
    if d.get("total_cost"):
        lines.append(F.row("total_cost", f"${d['total_cost']:.4f}"))
    if d.get("total_duration"):
        lines.append(F.row("total_duration", f"{d['total_duration']:.2f}s"))
    workers = d.get("workers", {})
    if workers:
        lines.append("")
        lines.append(F.subheader("  Workers"))
        for wid, winfo in workers.items():
            status = winfo.get("status", "unknown") if isinstance(winfo, dict) else "unknown"
            lines.append(f"    {F.badge(status)}  {wid}")
    pending = d.get("pending_approvals", [])
    if pending:
        lines.append("")
        lines.append(F.subheader("  Pending Approvals"))
        for p in pending:
            lines.append(f"    - {p}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Parser builder
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cockpit",
        description="CLI cockpit for the dynamic agent graph compiler system.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run 'python cockpit.py <command> --help' for command-specific help.\n",
    )
    sub = parser.add_subparsers(dest="command", title="commands", required=True)

    p = sub.add_parser("start-run", help="Create and launch a new run")
    p.add_argument("objective", help="Natural-language objective for the run")
    p.add_argument("--profile", default="default", help="Profile name (default: default)")
    p.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    p.set_defaults(handler=_cmd_start_run)

    p = sub.add_parser("status", help="Show run status and worker summary")
    p.add_argument("run_id", help="The run identifier")
    p.set_defaults(handler=_cmd_status)

    p = sub.add_parser("list-runs", help="List all runs")
    p.add_argument("--status", dest="status_filter", default=None,
                   choices=["running", "paused", "completed", "failed", "cancelled"],
                   help="Filter runs by status")
    p.set_defaults(handler=_cmd_list_runs)

    p = sub.add_parser("show-packet", help="Display packet details")
    p.add_argument("run_id", help="Run identifier")
    p.add_argument("worker_id", help="Worker identifier")
    p.set_defaults(handler=_cmd_show_packet)

    p = sub.add_parser("show-result", help="Display worker result")
    p.add_argument("run_id", help="Run identifier")
    p.add_argument("worker_id", help="Worker identifier")
    p.set_defaults(handler=_cmd_show_result)

    p = sub.add_parser("record-result", help="Manually record a worker result")
    p.add_argument("run_id", help="Run identifier")
    p.add_argument("worker_id", help="Worker identifier")
    p.add_argument("--status", default="success",
                   choices=["success", "failure", "timeout", "rejected"])
    p.add_argument("--output", default=None)
    p.add_argument("--error", default=None)
    p.add_argument("--duration", type=float, default=None)
    p.add_argument("--cost", type=float, default=None)
    p.set_defaults(handler=_cmd_record_result)

    p = sub.add_parser("request-child", help="Create a child worker request")
    p.add_argument("run_id", help="Run identifier")
    p.add_argument("parent_worker_id", help="Parent worker identifier")
    p.add_argument("child_role", help="Role for the child worker")
    p.add_argument("child_objective", help="Objective for the child worker")
    p.set_defaults(handler=_cmd_request_child)

    p = sub.add_parser("approve-child", help="Approve or reject a child worker request")
    p.add_argument("run_id", help="Run identifier")
    p.add_argument("parent_worker_id", help="Parent worker identifier")
    p.add_argument("child_worker_id", help="Child worker identifier")
    p.add_argument("--deny", action="store_true", help="Reject instead of approve")
    p.set_defaults(handler=_cmd_approve_child)

    p = sub.add_parser("grant-approval", help="Grant or deny a human approval gate")
    p.add_argument("run_id", help="Run identifier")
    p.add_argument("gate_name", help="Name of the approval gate")
    p.add_argument("--deny", action="store_true", help="Deny instead of grant")
    p.add_argument("--approver", default="human", help="Approver identifier")
    p.set_defaults(handler=_cmd_grant_approval)

    p = sub.add_parser("integrate", help="Integrate all results and write final.md")
    p.add_argument("run_id", help="Run identifier")
    p.set_defaults(handler=_cmd_integrate)

    p = sub.add_parser("replay", help="Replay run from ledger")
    p.add_argument("run_id", help="Run identifier")
    p.add_argument("--from-checkpoint", dest="from_checkpoint", default=None)
    p.set_defaults(handler=_cmd_replay)

    p = sub.add_parser("checkpoint", help="Create a manual checkpoint")
    p.add_argument("run_id", help="Run identifier")
    p.add_argument("--label", default=None)
    p.set_defaults(handler=_cmd_checkpoint)

    p = sub.add_parser("continue-run", help="Continue a paused run")
    p.add_argument("run_id", help="Run identifier")
    p.set_defaults(handler=_cmd_continue_run)

    p = sub.add_parser("show-events", help="Show event timeline for a run")
    p.add_argument("run_id", help="Run identifier")
    p.add_argument("--type", dest="type_filter", default=None)
    p.add_argument("--worker", dest="worker_filter", default=None)
    p.set_defaults(handler=_cmd_show_events)

    p = sub.add_parser("interactive", help="Start interactive cockpit for a run")
    p.add_argument("run_id", nargs="?", default=None)
    p.set_defaults(handler=_cmd_interactive)

    parser.add_argument("--completion-bash", action="store_true")
    return parser


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_start_run(args: argparse.Namespace) -> int:
    print(f"{F.BLUE}=== start-run ==={F.RESET}")
    print(f"  objective : {args.objective}")
    print(f"  profile   : {args.profile}")
    if args.seed is not None:
        print(f"  seed      : {args.seed}")

    # Deferred imports to avoid policy.py module-level DEFAULT_POLICY crash
    from policy import load_profile, PolicyValidator
    from graph_architect import GraphArchitect, DifficultyAssessor
    try:
        from graph_compiler import compile_and_prepare
    except ImportError:
        print(F.error("graph_compiler unavailable: langgraph is not installed"))
        print(F.error("Install with: pip install \"langgraph>=0.2.0\""))
        return 1

    profile = load_profile(args.profile)

    assessor = DifficultyAssessor()
    difficulty, shape, rationale = assessor.assess(args.objective)
    print(f"\n  assessed  : difficulty={difficulty.value}, shape={shape.value}")
    print(f"  rationale : {rationale}")

    architect = GraphArchitect(policy=profile, seed=args.seed)
    spec = architect.architect(objective=args.objective, difficulty=difficulty, shape=shape)
    run_id = spec.run_id
    print(f"\n  {F.GREEN}run_id    : {run_id}{F.RESET}")

    validator = PolicyValidator(profile)
    errors = validator.validate_spec(spec)
    if errors:
        print(F.error("Graph spec validation failed:"))
        for e in errors:
            print(f"    - {e}")
        return 1

    compiled, emitter, ledger = compile_and_prepare(spec, profile, RUNS_DIR)
    if not compiled.is_valid:
        print(F.error("Compilation failed:"))
        for e in compiled.compile_errors:
            print(f"    - {e}")
        return 1

    # Emit initial packets for each node
    for node in spec.nodes:
        if node.node_id not in compiled.execution_order:
            continue
        from dynamic_schema import WorkerPacket
        packet = WorkerPacket(
            run_id=run_id,
            worker_id=node.node_id,
            parent_worker_id=None,
            depth=0,
            role=node.role,
            objective=args.objective,
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
        emitter.emit_packet(run_id, packet)

    # Save manifest
    manifest = {
        "run_id": run_id,
        "objective": args.objective,
        "profile_name": profile.profile_name,
        "seed": args.seed or spec.seed,
        "difficulty": difficulty.value,
        "shape": shape.value,
        "shape_rationale": rationale,
        "status": "running",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "graph_spec_path": str(RUNS_DIR / run_id / "graph_spec.json"),
        "total_cost_estimate": 0.0,
    }
    _save_manifest(run_id, manifest)

    # Log to ledger
    ledger.log_event(
        run_id=run_id,
        event_type="RUN_CREATED",
        worker_id=None,
        depth=0,
        data={
            "objective": args.objective,
            "profile": profile.profile_name,
            "difficulty": difficulty.value,
            "shape": shape.value,
            "node_count": len(spec.nodes),
        },
    )

    print(f"\n{F.success(f'Run {run_id} started successfully.')}")
    print(f"  packets emitted for {len(spec.nodes)} node(s)")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    print(f"{F.BLUE}=== status ==={F.RESET}")
    if not _run_exists(args.run_id):
        print(F.error(f"Run '{args.run_id}' not found."))
        return 1

    manifest = _load_manifest(args.run_id)
    print(F.row("run_id", manifest["run_id"]))
    print(F.row("status", F.badge(manifest["status"])))
    print(F.row("created_at", manifest["created_at"]))
    print(F.row("profile", manifest.get("profile_name", "n/a")))
    print(F.row("difficulty", manifest.get("difficulty", "n/a")))
    print(F.row("shape", manifest.get("shape", "n/a")))

    cost = manifest.get("total_cost_estimate", 0)
    if cost:
        print(F.row("cost_estimate", f"${cost:.4f}"))

    # Load and display state if available
    state_path = RUNS_DIR / args.run_id / "state.json"
    if state_path.exists():
        with open(state_path, encoding="utf-8") as f:
            state_data = json.load(f)
        workers = state_data.get("workers", {})
        if workers:
            print(f"\n{F.subheader('Workers:')}")
            rows = [[wid, w.get("role", ""), F.badge(w.get("status", "pending"))]
                    for wid, w in workers.items()]
            print(F.table(["worker_id", "role", "status"], rows))
        pending = state_data.get("pending_approvals", [])
        if pending:
            print(f"\n{F.subheader('Pending Approvals:')}")
            for p in pending:
                print(f"  - {p}")

    return 0


def _cmd_list_runs(args: argparse.Namespace) -> int:
    print(f"{F.BLUE}=== list-runs ==={F.RESET}")
    if not RUNS_DIR.exists():
        print("  No runs found.")
        return 0

    runs = []
    for rd in RUNS_DIR.iterdir():
        if not rd.is_dir():
            continue
        mp = rd / "manifest.json"
        if mp.exists():
            with open(mp, encoding="utf-8") as f:
                runs.append(json.load(f))

    if not runs:
        print("  No runs found.")
        return 0

    runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    rows = []
    for r in runs:
        if args.status_filter and r.get("status") != args.status_filter:
            continue
        rows.append([
            r.get("run_id", ""),
            F.badge(r.get("status", "unknown")),
            r.get("profile_name", ""),
            r.get("difficulty", ""),
            r.get("created_at", "")[:19],
        ])

    if not rows:
        print("  No runs match the filter.")
        return 0

    print(F.table(["run_id", "status", "profile", "difficulty", "created_at"], rows))
    return 0


def _cmd_show_packet(args: argparse.Namespace) -> int:
    from worker_protocol import PacketReader
    reader = PacketReader(runs_dir=RUNS_DIR)
    try:
        pkt = reader.read_packet(args.run_id, args.worker_id)
        print(_format_packet(pkt))
        return 0
    except FileNotFoundError:
        print(F.error(f"No packet found for worker '{args.worker_id}' in run '{args.run_id}'."))
        return 1
    except Exception as e:
        print(F.error(f"Failed to read packet: {e}"))
        return 1


def _cmd_show_result(args: argparse.Namespace) -> int:
    from worker_protocol import PacketReader
    reader = PacketReader(runs_dir=RUNS_DIR)
    try:
        res = reader.read_result(args.run_id, args.worker_id)
        print(_format_result(res))
        return 0
    except FileNotFoundError:
        print(F.error(f"No result found for worker '{args.worker_id}' in run '{args.run_id}'."))
        return 1
    except Exception as e:
        print(F.error(f"Failed to read result: {e}"))
        return 1


def _cmd_record_result(args: argparse.Namespace) -> int:
    print(f"{F.BLUE}=== record-result ==={F.RESET}")
    output = None
    if args.output:
        try:
            output = json.loads(args.output)
        except Exception:
            output = args.output

    result = {
        "run_id": args.run_id,
        "worker_id": args.worker_id,
        "parent_worker_id": None,
        "status": args.status,
        "output": output,
        "error": args.error,
        "duration_seconds": args.duration,
        "cost_estimate": args.cost,
        "children": [],
        "artifacts": [],
        "completed_at": _now_iso(),
    }

    results_dir = RUNS_DIR / args.run_id / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    result_path = results_dir / f"{args.worker_id}.json"
    result_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    # Log to ledger
    from ledger import Ledger
    ledger = Ledger(runs_dir=RUNS_DIR)
    ledger.log_event(
        run_id=args.run_id,
        event_type="RESULT_RECORDED",
        worker_id=args.worker_id,
        depth=None,
        data={"status": args.status, "duration": args.duration, "cost": args.cost},
    )

    print(F.success(f"Result recorded for worker '{args.worker_id}' in run '{args.run_id}'."))
    return 0


def _cmd_request_child(args: argparse.Namespace) -> int:
    print(f"{F.BLUE}=== request-child ==={F.RESET}")

    from dynamic_schema import NodeType, DelegationPermission, WorkerPacket
    from worker_protocol import PacketEmitter
    from ledger import Ledger

    try:
        role = NodeType(args.child_role)
    except ValueError:
        print(F.error(f"Invalid role '{args.child_role}'. Valid: {[r.value for r in NodeType]}"))
        return 1

    manifest = _load_manifest(args.run_id)
    spec_path = Path(manifest.get("graph_spec_path", str(RUNS_DIR / args.run_id / "graph_spec.json")))
    if not spec_path.exists():
        print(F.error(f"Graph spec not found at {spec_path}."))
        return 1

    from dynamic_schema import GraphSpec
    spec = GraphSpec.from_dict(json.loads(spec_path.read_text(encoding="utf-8")))

    parent_node = next((n for n in spec.nodes if n.node_id == args.parent_worker_id), None)
    if not parent_node:
        print(F.error(f"Parent worker '{args.parent_worker_id}' not found."))
        return 1

    if parent_node.delegation_permission == DelegationPermission.NONE:
        print(F.error(f"Parent has delegation_permission=NONE. Cannot create child."))
        return 1

    if parent_node.max_child_depth <= 0:
        print(F.error(f"Parent max_child_depth is 0. Cannot create child."))
        return 1

    child_id = f"{args.parent_worker_id}-child-{uuid.uuid4().hex[:8]}"
    child_packet = WorkerPacket(
        run_id=args.run_id,
        worker_id=child_id,
        parent_worker_id=args.parent_worker_id,
        depth=1,
        role=role,
        objective=args.child_objective,
        task_prompt=args.child_objective,
        allowed_tools=parent_node.allowed_tools,
        forbidden_tools=parent_node.forbidden_tools,
        owned_paths=parent_node.owned_paths,
        forbidden_paths=parent_node.forbidden_paths,
        timeout=parent_node.timeout,
        budget=parent_node.budget,
        delegation_permission=DelegationPermission.REQUEST,
        max_child_depth=max(0, parent_node.max_child_depth - 1),
    )

    emitter = PacketEmitter(runs_dir=RUNS_DIR)
    emitter.emit_packet(args.run_id, child_packet)

    ledger = Ledger(runs_dir=RUNS_DIR)
    ledger.log_event(
        run_id=args.run_id,
        event_type="CHILD_REQUESTED",
        worker_id=child_id,
        depth=1,
        data={
            "parent_worker_id": args.parent_worker_id,
            "role": role.value,
            "objective": args.child_objective,
        },
    )

    print(F.success(f"Child worker '{child_id}' created and emitted."))
    print(F.row("role", role.value))
    print(F.row("parent", args.parent_worker_id))
    print(F.row("depth", "1"))
    return 0


def _cmd_approve_child(args: argparse.Namespace) -> int:
    print(f"{F.BLUE}=== approve-child ==={F.RESET}")
    from ledger import Ledger
    ledger = Ledger(runs_dir=RUNS_DIR)
    event_type = "CHILD_REJECTED" if args.deny else "CHILD_APPROVED"
    ledger.log_event(
        run_id=args.run_id,
        event_type=event_type,
        worker_id=args.child_worker_id,
        depth=None,
        data={"parent_worker_id": args.parent_worker_id},
    )
    if args.deny:
        print(F.warning(f"Child worker '{args.child_worker_id}' REJECTED."))
    else:
        print(F.success(f"Child worker '{args.child_worker_id}' APPROVED."))
    return 0


def _cmd_grant_approval(args: argparse.Namespace) -> int:
    print(f"{F.BLUE}=== grant-approval ==={F.RESET}")
    from ledger import Ledger
    ledger = Ledger(runs_dir=RUNS_DIR)
    event_type = "HUMAN_APPROVAL_DENIED" if args.deny else "HUMAN_APPROVAL_GRANTED"
    ledger.log_event(
        run_id=args.run_id,
        event_type=event_type,
        worker_id=None,
        depth=None,
        data={"gate": args.gate_name, "approver": args.approver},
    )
    if args.deny:
        print(F.warning(f"Approval gate '{args.gate_name}' DENIED by {args.approver}."))
    else:
        print(F.success(f"Approval gate '{args.gate_name}' GRANTED by {args.approver}."))

    # Remove from pending approvals in state
    state_path = RUNS_DIR / args.run_id / "state.json"
    if state_path.exists():
        with open(state_path, encoding="utf-8") as f:
            state_data = json.load(f)
        if "pending_approvals" in state_data and args.gate_name in state_data["pending_approvals"]:
            state_data["pending_approvals"].remove(args.gate_name)
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(state_data, f, indent=2)
    return 0


def _cmd_integrate(args: argparse.Namespace) -> int:
    print(f"{F.BLUE}=== integrate ==={F.RESET}")
    from worker_protocol import PacketReader

    manifest = _load_manifest(args.run_id)
    reader = PacketReader(runs_dir=RUNS_DIR)

    result_ids = reader.list_result_ids(args.run_id)
    if not result_ids:
        print(F.warning("No results found. Nothing to integrate."))
        return 1

    result_objs = []
    for wid in result_ids:
        try:
            result_objs.append(reader.read_result(args.run_id, wid))
        except Exception:
            pass

    from runtime import ResultIntegrator
    integrator = ResultIntegrator(run_id=args.run_id)
    integrated = integrator.integrate_results(None, {r.worker_id: r for r in result_objs})

    output_dir = RUNS_DIR / args.run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / "final.md"

    lines = [
        "# Run Report\n",
        f"**run_id**: `{args.run_id}`\n",
        f"**status**: {manifest.get('status', 'unknown')}\n",
        f"**profile**: {manifest.get('profile_name', 'n/a')}\n",
        f"**difficulty**: {manifest.get('difficulty', 'n/a')}\n",
        f"**created**: {manifest.get('created_at', '')}\n",
        f"**integrated**: {_now_iso()}\n",
        "\n---\n\n## Summary\n",
        f"Total workers: {len(result_objs)}\n",
        "\n---\n\n## Worker Results\n",
    ]
    for r in result_objs:
        lines.append(f"\n### {r.worker_id}\n")
        lines.append(f"**status**: `{r.status.value if hasattr(r.status, 'value') else r.status}`\n")
        if r.output:
            lines.append(f"\n```\n{r.output}\n```\n")

    final_path.write_text("\n".join(lines), encoding="utf-8")

    _update_manifest(args.run_id, {
        "status": "completed",
        "final_output_path": str(final_path),
        "completed_at": _now_iso(),
    })

    from ledger import Ledger
    ledger = Ledger(runs_dir=RUNS_DIR)
    ledger.log_event(
        run_id=args.run_id,
        event_type="RUN_COMPLETED",
        worker_id=None,
        depth=None,
        data={"result_count": len(result_objs), "output_path": str(final_path)},
    )

    print(F.success(f"Integration complete. Output written to:"))
    print(f"  {final_path}")
    print(f"  Workers processed: {len(result_objs)}")
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    print(f"{F.BLUE}=== replay ==={F.RESET}")
    if not _run_exists(args.run_id):
        print(F.error(f"Run '{args.run_id}' not found."))
        return 1

    from ledger import Ledger
    ledger = Ledger(runs_dir=RUNS_DIR)
    events = ledger.get_events(args.run_id)

    if args.from_checkpoint:
        ck_events = [e for e in events
                     if e.event_type == "CHECKPOINT_CREATED"
                     and e.data.get("checkpoint_id") == args.from_checkpoint]
        if ck_events:
            ck_idx = events.index(ck_events[0])
            events = events[ck_idx + 1:]
        print(f"Replaying from checkpoint '{args.from_checkpoint}'...")
    else:
        print("Replaying from start...")

    print(f"\nTotal events: {len(events)}\n")
    if not events:
        print("  No events found.")
        return 0

    rows = [[e.timestamp[:19], e.event_type, e.worker_id or "-", F.truncate(str(e.data), 50)] for e in events]
    print(F.table(["timestamp", "event_type", "worker_id", "data"], rows))
    return 0


def _cmd_checkpoint(args: argparse.Namespace) -> int:
    print(f"{F.BLUE}=== checkpoint ==={F.RESET}")
    from ledger import Ledger
    ledger = Ledger(runs_dir=RUNS_DIR)
    checkpoint_id = str(uuid.uuid4())
    ledger.log_event(
        run_id=args.run_id,
        event_type="CHECKPOINT_CREATED",
        worker_id=None,
        depth=None,
        data={"label": args.label, "checkpoint_id": checkpoint_id},
    )
    print(F.success(f"Checkpoint '{checkpoint_id}' created for run '{args.run_id}'."))
    if args.label:
        print(f"  label: {args.label}")
    return 0


def _cmd_continue_run(args: argparse.Namespace) -> int:
    print(f"{F.BLUE}=== continue-run ==={F.RESET}")
    manifest = _load_manifest(args.run_id)
    status = manifest.get("status", "")
    if status != "paused":
        print(F.warning(f"Run '{args.run_id}' is not paused (status={status}). Continuing anyway."))

    _update_manifest(args.run_id, {"status": "running"})

    from ledger import Ledger
    ledger = Ledger(runs_dir=RUNS_DIR)
    ledger.log_event(run_id=args.run_id, event_type="RUN_CONTINUED",
                     worker_id=None, depth=None, data={})

    print(F.success(f"Run '{args.run_id}' resumed."))
    return 0


def _cmd_show_events(args: argparse.Namespace) -> int:
    print(f"{F.BLUE}=== show-events ==={F.RESET}")
    from ledger import Ledger
    ledger = Ledger(runs_dir=RUNS_DIR)
    events = ledger.get_events(args.run_id, event_type=args.type_filter, worker_id=args.worker_filter)

    if not events:
        print("  No events found.")
        return 0

    rows = [[e.timestamp[:19], e.event_type, e.worker_id or "-", F.truncate(str(e.data), 50)] for e in events]
    print(F.table(["timestamp", "event_type", "worker_id", "data"], rows))
    print(f"\nTotal: {len(events)} event(s)")
    return 0


def _cmd_interactive(args: argparse.Namespace) -> int:
    if args.run_id and _run_exists(args.run_id):
        session = InteractiveCockpit(args.run_id)
        session.start()
    else:
        print(f"{F.BLUE}=== Interactive Cockpit ==={F.RESET}\nAvailable runs:\n")
        if not RUNS_DIR.exists():
            print("  No runs found.")
            return 0
        runs = [d.name for d in RUNS_DIR.iterdir()
                if d.is_dir() and (d / "manifest.json").exists()]
        if not runs:
            print("  No runs found.")
            return 0
        for r in runs:
            print(f"  - {r}")
        print("\nTo start interactive session:\n  python cockpit.py interactive <run_id>")
    return 0


# ---------------------------------------------------------------------------
# InteractiveCockpit
# ---------------------------------------------------------------------------

HELP_TEXT = """
Available commands:
  help              Show this help menu
  status            Show current run status
  workers           List all workers
  packets           List emitted packets
  results           List recorded results
  show <worker_id>  Show packet and result for a worker
  events            Show event timeline
  approvals         Show pending approvals
  checkpoint [label] Create a checkpoint
  integrate         Integrate results and write final.md
  continue          Resume a paused run
  exit              Exit interactive mode
""".strip()


class InteractiveCockpit:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id

    def start(self) -> None:
        print(f"\n{F.header('=' * 60)}")
        print(f"{F.header('  Interactive Cockpit')}")
        print(f"{F.header('=' * 60)}")
        print(f"  run_id: {F.CYAN}{self.run_id}{F.RESET}")
        print(f"  Type {F.GREEN}help{F.RESET} for available commands.\n")

        try:
            manifest = _load_manifest(self.run_id)
            print(f"  Status  : {F.badge(manifest.get('status', 'unknown'))}")
            print(f"  Profile : {manifest.get('profile_name', 'n/a')}")
            print(f"  Shape   : {manifest.get('shape', 'n/a')}")
            print(f"  Created : {manifest.get('created_at', '')[:19]}")
        except FileNotFoundError:
            print(F.warning("Run manifest not found."))

        running = True
        while running:
            try:
                raw = input(f"{F.BLUE}cockpit>{F.RESET} ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nExiting.")
                break
            if not raw:
                continue
            parts = raw.split()
            cmd, args = parts[0].lower(), parts[1:]
            running = self._dispatch(cmd, args)

    def _dispatch(self, cmd: str, args: list[str]) -> bool:
        if cmd == "help":
            print(f"\n{F.GREEN}{HELP_TEXT}{F.RESET}\n")
        elif cmd == "status":
            self._do_status()
        elif cmd == "workers":
            self._do_workers()
        elif cmd == "packets":
            self._do_packets()
        elif cmd == "results":
            self._do_results()
        elif cmd == "show":
            if not args:
                print(F.error("Usage: show <worker_id>"))
            else:
                self._do_show(args[0])
        elif cmd == "events":
            self._do_events(args)
        elif cmd == "approvals":
            self._do_approvals()
        elif cmd == "checkpoint":
            self._do_checkpoint(args)
        elif cmd == "integrate":
            self._do_integrate()
        elif cmd == "continue":
            self._do_continue()
        elif cmd in ("exit", "quit", "q"):
            return False
        else:
            print(F.error(f"Unknown command '{cmd}'. Type 'help'."))
        return True

    def _do_status(self) -> None:
        try:
            manifest = _load_manifest(self.run_id)
            state_path = RUNS_DIR / self.run_id / "state.json"
            if state_path.exists():
                with open(state_path, encoding="utf-8") as f:
                    state_data = json.load(f)
                print(_format_state(state_data))
            else:
                print(F.row("status", F.badge(manifest.get("status", "unknown"))))
        except FileNotFoundError:
            print(F.error(f"Run '{self.run_id}' not found."))

    def _do_workers(self) -> None:
        state_path = RUNS_DIR / self.run_id / "state.json"
        if not state_path.exists():
            print("  No state file found.")
            return
        with open(state_path, encoding="utf-8") as f:
            state_data = json.load(f)
        workers = state_data.get("workers", {})
        if not workers:
            print("  No workers registered.")
            return
        rows = [[wid, w.get("role", ""), F.badge(w.get("status", "pending"))]
                for wid, w in workers.items()]
        print(F.table(["worker_id", "role", "status"], rows))

    def _do_packets(self) -> None:
        from worker_protocol import PacketReader
        reader = PacketReader(runs_dir=RUNS_DIR)
        ids = reader.list_workers(self.run_id)
        if not ids:
            print("  No packets emitted yet.")
        else:
            print(f"  {len(ids)} packet(s):")
            for wid in ids:
                print(f"    - {wid}")

    def _do_results(self) -> None:
        from worker_protocol import PacketReader
        reader = PacketReader(runs_dir=RUNS_DIR)
        ids = reader.list_result_ids(self.run_id)
        if not ids:
            print("  No results recorded yet.")
        else:
            print(f"  {len(ids)} result(s):")
            for wid in ids:
                try:
                    r = reader.read_result(self.run_id, wid)
                    status = r.status.value if hasattr(r.status, "value") else str(r.status)
                except Exception:
                    status = "?"
                print(f"    {F.badge(status)}  {wid}")

    def _do_show(self, worker_id: str) -> None:
        from worker_protocol import PacketReader
        reader = PacketReader(runs_dir=RUNS_DIR)
        print(f"\n{F.header(f'=== Worker: {worker_id} ===')}")
        try:
            pkt = reader.read_packet(self.run_id, worker_id)
            print(f"\n{F.subheader('Packet:')}")
            print(_format_packet(pkt))
        except FileNotFoundError:
            print(f"\n  {F.muted('No packet found.')}")
        except Exception as e:
            print(f"\n  {F.muted(f'No packet ({e}).')}")
        try:
            res = reader.read_result(self.run_id, worker_id)
            print(f"\n{F.subheader('Result:')}")
            print(_format_result(res))
        except FileNotFoundError:
            print(f"\n  {F.muted('No result recorded yet.')}")
        except Exception as e:
            print(f"\n  {F.muted(f'No result ({e}).')}")

    def _do_events(self, args: list[str]) -> None:
        from ledger import Ledger
        ledger = Ledger(runs_dir=RUNS_DIR)
        type_f = None
        worker_f = None
        if "--type" in args and len(args) > args.index("--type") + 1:
            type_f = args[args.index("--type") + 1]
        if "--worker" in args and len(args) > args.index("--worker") + 1:
            worker_f = args[args.index("--worker") + 1]
        events = ledger.get_events(self.run_id, event_type=type_f, worker_id=worker_f)
        if not events:
            print("  No events found.")
            return
        rows = [[e.timestamp[:19], e.event_type, e.worker_id or "-", F.truncate(str(e.data), 50)] for e in events]
        print(F.table(["timestamp", "event_type", "worker_id", "data"], rows))
        print(f"\nTotal: {len(events)}")

    def _do_approvals(self) -> None:
        state_path = RUNS_DIR / self.run_id / "state.json"
        if not state_path.exists():
            print(F.error("No state file found."))
            return
        with open(state_path, encoding="utf-8") as f:
            state_data = json.load(f)
        pending = state_data.get("pending_approvals", [])
        if not pending:
            print("  No pending approvals.")
        else:
            for p in pending:
                print(f"  - {p}")

    def _do_checkpoint(self, args: list[str]) -> None:
        from ledger import Ledger
        ledger = Ledger(runs_dir=RUNS_DIR)
        label = args[0] if args else None
        checkpoint_id = str(uuid.uuid4())
        ledger.log_event(run_id=self.run_id, event_type="CHECKPOINT_CREATED",
                         worker_id=None, depth=None,
                         data={"label": label, "checkpoint_id": checkpoint_id})
        print(F.success(f"Checkpoint '{checkpoint_id}' created."))

    def _do_integrate(self) -> None:
        class FakeArgs:
            run_id = self.run_id
        exit_code = _cmd_integrate(FakeArgs())
        print(F.success("Integration complete.") if exit_code == 0 else F.error("Integration failed."))

    def _do_continue(self) -> None:
        class FakeArgs:
            run_id = self.run_id
        exit_code = _cmd_continue_run(FakeArgs())
        print(F.success("Run resumed.") if exit_code == 0 else F.error("Failed to resume run."))


# ---------------------------------------------------------------------------
# Bash completion
# ---------------------------------------------------------------------------

def _bash_completion_script() -> str:
    runs_list = " ".join(
        d.name for d in RUNS_DIR.iterdir()
        if d.is_dir() and (d / "manifest.json").exists()
    )
    commands = (
        "start-run status list-runs show-packet show-result record-result "
        "request-child approve-child grant-approval integrate replay "
        "checkpoint continue-run show-events interactive"
    )
    return textwrap.dedent(f"""\
        _cockpit_completions()
        {{
            local cur prev
            COMPREPLY=()
            cur="${{COMP_WORDS[COMP_CWORD]}}"
            prev="${{COMP_WORDS[COMP_CWORD-1]}}"
            local run_dirs="{runs_list}"
            local cmds="{commands}"
            case "$prev" in
                interactive|status|replay|checkpoint|continue-run|integrate|show-events|show-packet|show-result|record-result|request-child|approve-child|grant-approval)
                    COMPREPLY=( $(compgen -W "$run_dirs" -- $cur) ); return 0 ;;
            esac
            [[ "$cur" == -* ]] && COMPREPLY=( $(compgen -W "--profile --seed --status --from-checkpoint --label --deny --approver --type --worker --output --error --duration --cost" -- $cur) ) || COMPREPLY=( $(compgen -W "$cmds" -- $cur) )
        }}
        complete -F _cockpit_completions cockpit.py
    """)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    import warnings
    warnings.warn(
        "cockpit.py is deprecated. The graph-compiler subsystem is superseded by the work-packet runtime. "
        "Use packet_cli.py for all operations. Cockpit and its supporting modules will be removed in 6 months.",
        DeprecationWarning,
        stacklevel=2
    )
    parser = _build_parser()
    args = parser.parse_args()

    if args.completion_bash:
        print(_bash_completion_script())
        return 0

    if not hasattr(args, "handler"):
        parser.print_help()
        return 1

    try:
        return args.handler(args)
    except FileNotFoundError as e:
        print(F.error(str(e)))
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except Exception as e:
        print(F.error(f"Unhandled exception: {e}"))
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())