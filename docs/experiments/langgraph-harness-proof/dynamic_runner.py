from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

from langchain_core._api.deprecation import LangChainPendingDeprecationWarning

warnings.filterwarnings("ignore", category=LangChainPendingDeprecationWarning)

from dynamic_graph import architect_graph_spec, compile_dynamic_graph, stable_run_id
from dynamic_schema import validate_graph_spec


ROOT = Path(__file__).resolve().parent
RUNS_DIR = ROOT / "runs"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def plan(args: argparse.Namespace) -> int:
    spec = architect_graph_spec(args.objective)
    errors = validate_graph_spec(spec)
    payload = {"valid": not errors, "errors": errors, "graph_spec": spec}
    if args.output:
        _write_json(Path(args.output), payload)
    print(json.dumps(payload, indent=2))
    return 0 if not errors else 1


def run(args: argparse.Namespace) -> int:
    run_id = args.run_id or stable_run_id(args.objective)
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    if args.spec_file:
        spec = json.loads(Path(args.spec_file).read_text(encoding="utf-8"))
    else:
        spec = architect_graph_spec(args.objective)

    errors = validate_graph_spec(spec)
    if errors:
        _write_json(run_dir / "validation-errors.json", {"errors": errors, "graph_spec": spec})
        raise ValueError("invalid graph spec: " + "; ".join(errors))

    graph = compile_dynamic_graph(spec)
    result = graph.invoke(
        {
            "objective": args.objective,
            "run_id": run_id,
            "graph_spec": spec,
            "artifacts": [],
            "final": "",
        },
        config={"configurable": {"thread_id": run_id}},
    )

    _write_json(run_dir / "graph_spec.json", spec)
    _write_json(
        run_dir / "run.json",
        {
            "run_id": run_id,
            "objective": args.objective,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "artifact_count": len(result["artifacts"]),
            "artifacts": result["artifacts"],
        },
    )
    (run_dir / "final.md").write_text(result["final"], encoding="utf-8")

    print(f"run_id={run_id}")
    print(f"graph_spec={run_dir / 'graph_spec.json'}")
    print(f"run={run_dir / 'run.json'}")
    print(f"final={run_dir / 'final.md'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dynamic ephemeral LangGraph architect/compiler/executor."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    plan_parser = sub.add_parser("plan", help="Generate and validate a graph spec.")
    plan_parser.add_argument("--objective", required=True)
    plan_parser.add_argument("--output")
    plan_parser.set_defaults(func=plan)

    run_parser = sub.add_parser("run", help="Generate, compile, and execute a graph.")
    run_parser.add_argument("--objective", required=True)
    run_parser.add_argument("--run-id")
    run_parser.add_argument("--spec-file")
    run_parser.set_defaults(func=run)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
