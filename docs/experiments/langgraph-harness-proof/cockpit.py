from __future__ import annotations

import argparse
import json
import shutil
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

from langchain_core._api.deprecation import LangChainPendingDeprecationWarning

warnings.filterwarnings("ignore", category=LangChainPendingDeprecationWarning)

from cockpit_graph import build_cockpit_graph, load_profile


ROOT = Path(__file__).resolve().parent
RUNS_DIR = ROOT / "runs"


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe_slug(text: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in text)
    parts = [part for part in cleaned.split("-") if part]
    return "-".join(parts[:8]) or "run"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _packet_markdown(packet: dict) -> str:
    return f"""# Worker Packet: {packet['title']}

Worker ID: `{packet['worker_id']}`
Lane: `{packet['lane_id']}`
Mode: `{packet['mode']}`
Recommended harnesses: `{', '.join(packet['recommended_harnesses']) or 'any'}`
Can create child run: `{packet['can_create_child_run']}`
Max child depth: `{packet['max_child_depth']}`
Timeout seconds: `{packet['timeout_seconds']}`

## Objective

{packet['objective']}

## Task

{packet['prompt']}

## Owned Paths

{chr(10).join(f"- `{path}`" for path in packet['owned_paths']) or "- None"}

## Avoid Paths

{chr(10).join(f"- `{path}`" for path in packet['avoid_paths']) or "- None"}

## Deliverable Contract

{chr(10).join(f"- {item}" for item in packet['deliverable_contract']) or "- Return concise findings and changed paths if any."}

## No-Revert Rule

Do not revert unrelated user or peer edits. If edits are needed, keep them inside owned paths and report every changed file.

## Result Recording

After finishing, record the result from the cockpit root:

```powershell
.\\.venv\\Scripts\\python cockpit.py record {packet['run_id']} {packet['worker_id']} --harness <codex|claude|gemini|qwen|opencode|minimax|other> --status complete --summary "..."
```
"""


def start(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile, ROOT)
    run_id = args.run_id or f"{_utc_stamp()}-{_safe_slug(args.objective)}"
    run_dir = RUNS_DIR / run_id
    if run_dir.exists() and not args.force:
        raise FileExistsError(f"Run already exists: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)

    graph = build_cockpit_graph()
    result = graph.invoke(
        {
            "objective": args.objective,
            "profile_name": args.profile,
            "profile": profile,
            "run_id": run_id,
            "worker_packets": [],
            "notes": [],
        }
    )

    manifest = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "objective": args.objective,
        "profile": args.profile,
        "status": "started",
        "notes": result["notes"],
        "worker_packets": result["worker_packets"],
    }
    _write_json(run_dir / "manifest.json", manifest)

    packet_dir = run_dir / "packets"
    for packet in result["worker_packets"]:
        _write_json(packet_dir / f"{packet['worker_id']}.json", packet)
        (packet_dir / f"{packet['worker_id']}.md").write_text(
            _packet_markdown(packet),
            encoding="utf-8",
        )

    print(f"run_id={run_id}")
    print(f"manifest={run_dir / 'manifest.json'}")
    print(f"packets={packet_dir}")
    for packet in result["worker_packets"]:
        print(f"- {packet['worker_id']}: {packet['title']}")
    return 0


def status(args: argparse.Namespace) -> int:
    run_dir = RUNS_DIR / args.run_id
    manifest = _read_json(run_dir / "manifest.json")
    result_dir = run_dir / "results"
    results = {
        path.stem: _read_json(path)
        for path in result_dir.glob("*.json")
        if path.name != "integrated.json"
    }

    print(f"run_id={args.run_id}")
    print(f"objective={manifest['objective']}")
    print(f"profile={manifest['profile']}")
    for packet in manifest["worker_packets"]:
        result = results.get(packet["worker_id"])
        state = result["status"] if result else "pending"
        harness = result.get("harness", "") if result else ""
        print(f"- {packet['worker_id']}: {state} {harness}".rstrip())
    return 0


def record(args: argparse.Namespace) -> int:
    run_dir = RUNS_DIR / args.run_id
    manifest = _read_json(run_dir / "manifest.json")
    valid_ids = {packet["worker_id"] for packet in manifest["worker_packets"]}
    if args.worker_id not in valid_ids:
        raise ValueError(f"Worker ID is not part of run {args.run_id}: {args.worker_id}")

    result: dict = {
        "run_id": args.run_id,
        "worker_id": args.worker_id,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "harness": args.harness,
        "status": args.status,
        "summary": args.summary,
        "changed_paths": args.changed_path,
        "evidence_paths": args.evidence_path,
    }
    if args.result_file:
        source = Path(args.result_file).resolve()
        if not source.exists():
            raise FileNotFoundError(source)
        copied = run_dir / "artifacts" / args.worker_id / source.name
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, copied)
        result["copied_result_file"] = str(copied)

    _write_json(run_dir / "results" / f"{args.worker_id}.json", result)
    print(f"recorded={run_dir / 'results' / f'{args.worker_id}.json'}")
    return 0


def integrate(args: argparse.Namespace) -> int:
    run_dir = RUNS_DIR / args.run_id
    manifest = _read_json(run_dir / "manifest.json")
    result_dir = run_dir / "results"
    results = [
        _read_json(path)
        for path in sorted(result_dir.glob("*.json"))
        if path.name != "integrated.json"
    ]
    completed = [item for item in results if item.get("status") == "complete"]
    pending = [
        packet["worker_id"]
        for packet in manifest["worker_packets"]
        if not any(result["worker_id"] == packet["worker_id"] for result in results)
    ]

    lines = [
        f"# Integrated Run: {args.run_id}",
        "",
        f"Objective: {manifest['objective']}",
        f"Profile: {manifest['profile']}",
        "",
        "## Completed Results",
        "",
    ]
    for item in completed:
        lines.append(f"- `{item['worker_id']}` via `{item['harness']}`: {item['summary']}")
    if not completed:
        lines.append("- None")
    lines.extend(["", "## Pending", ""])
    lines.extend(f"- `{worker_id}`" for worker_id in pending)
    if not pending:
        lines.append("- None")

    integrated = {
        "run_id": args.run_id,
        "integrated_at": datetime.now(timezone.utc).isoformat(),
        "objective": manifest["objective"],
        "completed_count": len(completed),
        "pending_count": len(pending),
        "results": results,
        "pending": pending,
    }
    _write_json(result_dir / "integrated.json", integrated)
    (run_dir / "integrated.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(run_dir / "integrated.md")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cockpit-neutral LangGraph swarm runner."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    start_parser = sub.add_parser("start", help="Start a run and emit worker packets.")
    start_parser.add_argument("--objective", required=True)
    start_parser.add_argument("--profile", default="app-drive")
    start_parser.add_argument("--run-id")
    start_parser.add_argument("--force", action="store_true")
    start_parser.set_defaults(func=start)

    status_parser = sub.add_parser("status", help="Show run status.")
    status_parser.add_argument("run_id")
    status_parser.set_defaults(func=status)

    record_parser = sub.add_parser("record", help="Record one worker result.")
    record_parser.add_argument("run_id")
    record_parser.add_argument("worker_id")
    record_parser.add_argument("--harness", required=True)
    record_parser.add_argument(
        "--status", choices=["complete", "blocked", "failed"], default="complete"
    )
    record_parser.add_argument("--summary", required=True)
    record_parser.add_argument("--changed-path", action="append", default=[])
    record_parser.add_argument("--evidence-path", action="append", default=[])
    record_parser.add_argument("--result-file")
    record_parser.set_defaults(func=record)

    integrate_parser = sub.add_parser("integrate", help="Integrate recorded results.")
    integrate_parser.add_argument("run_id")
    integrate_parser.set_defaults(func=integrate)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
