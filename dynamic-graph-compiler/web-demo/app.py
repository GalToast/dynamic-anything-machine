"""
Flask server for the DynamicAnythingMachine web demo.

Provides SSE streaming endpoint that wraps DynamicOrchestrator.execute()
and emits real-time events for the frontend visualization.

Usage:
    cd dynamic-graph-compiler
    python web-demo/app.py
    # Then open http://localhost:5000
"""

from __future__ import annotations

import json
import os
import sys
import uuid
import zipfile
import io
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

# Ensure project root on path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from flask import Flask, Response, jsonify, request, send_file
from flask_cors import CORS

# Import from the project
from showcase.dynamic_orchestrator import DynamicOrchestrator
from work_ledger import WorkLedger

WEB_DEMO_DIR = Path(__file__).parent
STATIC_DIR = WEB_DEMO_DIR / "static"
HTML_FILE = WEB_DEMO_DIR / "index.html"

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
CORS(app)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ledger(runs_dir: str = "runs/demo") -> WorkLedger:
    return WorkLedger(runs_dir=Path(runs_dir))


def _emit(event_type: str, data: dict) -> str:
    """Format an SSE event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def _get_run_dir(run_id: str) -> Path:
    return Path("runs/demo") / run_id


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    """Serve the main HTML page."""
    return send_file(Path(__file__).parent / "index.html")


@app.route("/health")
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok", "service": "DynamicAnythingMachine"})


@app.route("/execute", methods=["POST"])
def execute():
    """
    Execute the orchestrator with SSE streaming.

    Request body: {"goal": "...", "mode": "research"}
    Streams events: phase_start, node_started, node_completed, synthesis_progress, final_output
    """
    payload = request.get_json(silent=True) or {}
    goal = payload.get("goal", "")
    mode = payload.get("mode", None)

    if not goal:
        return jsonify({"error": "goal is required"}), 400

    run_id = str(uuid.uuid4())
    runs_dir = "runs/demo"

    def generate() -> Iterator[str]:
        ledger = _make_ledger(runs_dir)

        # Emit run started
        yield _emit("run_started", {"run_id": run_id, "goal": goal, "mode": mode or "research"})

        try:
            # Initialize orchestrator
            orchestrator = DynamicOrchestrator(runs_dir=runs_dir, use_llm=True, verbose=False)

            # Emit Phase 1 start
            yield _emit("phase_start", {"phase": 1, "name": "orchestrator_analysis", "description": "Analyzing goal and planning seams..."})

            # Run phase 1 synchronously but emit events as it progresses
            seam_plan, phase1_state = orchestrator.phase1_analyze(goal, run_id, mode or "research")

            yield _emit("phase_complete", {
                "phase": 1,
                "seams": [{"id": s["id"], "manager_id": s["manager_id"], "task_count": len(s.get("tasks", []))} for s in seam_plan]
            })

            # Emit Phase 2 start
            yield _emit("phase_start", {"phase": 2, "name": "execution", "description": "Executing graph with dynamic nodes..."})

            # Build the full graph
            compiled, builder, initial_state = orchestrator.build_full_graph(goal, run_id, mode or "research", seam_plan, phase1_state)

            # Emit node topology
            node_list = list(builder.nodes.keys())
            yield _emit("graph_built", {
                "nodes": node_list,
                "edge_count": len(builder.edges),
            })

            # Stream through the graph, emitting events for each state update
            final_state = None
            prev_active = set()
            prev_completed = set()

            for state in orchestrator.stream(compiled, initial_state):
                # Detect newly started agents
                current_active = set(state.active_agents)
                newly_active = current_active - prev_active
                for agent_id in newly_active:
                    node_type = "orchestrator" if agent_id == "orchestrator" else \
                                "manager" if agent_id.startswith("manager_") else "worker"
                    yield _emit("node_started", {
                        "agent_id": agent_id,
                        "node_type": node_type,
                        "run_id": run_id,
                    })
                prev_active = current_active

                # Detect newly completed agents
                current_completed = set(state.completed_agents)
                newly_completed = current_completed - prev_completed
                for agent_id in newly_completed:
                    yield _emit("node_completed", {
                        "agent_id": agent_id,
                        "run_id": run_id,
                    })
                prev_completed = current_completed

                # Emit streaming tokens for progressive output
                if state.streaming_tokens:
                    # Get the latest token(s) we haven't sent yet
                    yield _emit("streaming_token", {
                        "tokens": state.streaming_tokens[-50:] if len(state.streaming_tokens) > 50 else state.streaming_tokens,
                        "total": len(state.streaming_tokens),
                    })

                # Emit synthesis progress
                if state.findings:
                    yield _emit("synthesis_progress", {
                        "findings_count": len(state.findings),
                        "findings_keys": list(state.findings.keys()),
                    })

                final_state = state

            # Emit final output
            if final_state and final_state.final_output:
                output_text = final_state.final_output
                if isinstance(output_text, (dict, list)):
                    output_text = json.dumps(output_text, indent=2)
                yield _emit("final_output", {
                    "output": output_text,
                    "confidence": final_state.confidence,
                    "run_id": run_id,
                })
            else:
                # Try to get output from state
                output_text = str(final_state.final_output) if final_state else "No output produced"
                yield _emit("final_output", {
                    "output": output_text,
                    "confidence": getattr(final_state, "confidence", 0.0) if final_state else 0.0,
                    "run_id": run_id,
                })

            # Emit run completed
            yield _emit("run_completed", {
                "run_id": run_id,
                "confidence": getattr(final_state, "confidence", 0.0) if final_state else 0.0,
            })

        except Exception as exc:
            import traceback
            yield _emit("error", {"message": str(exc), "traceback": traceback.format_exc()})
            yield _emit("run_failed", {"run_id": run_id, "error": str(exc)})

    return Response(generate(), mimetype="text/event-stream")


@app.route("/audit-trail/<run_id>")
def audit_trail(run_id: str):
    """
    Return the audit trail (events.jsonl) for a given run.
    """
    ledger = _make_ledger("runs/demo")
    run_dir = _get_run_dir(run_id)
    events_file = run_dir / "packet-events.jsonl"

    if not events_file.exists():
        # Try to find the file in runs directory
        # Search for the run_id in runs subdirectories
        for subdir in Path("runs").iterdir():
            if subdir.is_dir():
                candidate = subdir / "packet-events.jsonl"
                if candidate.exists():
                    events_file = candidate
                    break

    if not events_file.exists():
        return jsonify({"error": "Run not found", "run_id": run_id}), 404

    events = []
    with open(events_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    return jsonify({"run_id": run_id, "events": events, "count": len(events)})


@app.route("/runs")
def list_runs():
    """List available runs from the demo directory."""
    runs_dir = Path("runs/demo")
    if not runs_dir.exists():
        return jsonify({"runs": []})

    runs = []
    for run_path in sorted(runs_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
        if run_path.is_dir():
            events_file = run_path / "packet-events.jsonl"
            event_count = 0
            if events_file.exists():
                with open(events_file, "r") as f:
                    event_count = sum(1 for line in f if line.strip())
            runs.append({
                "run_id": run_path.name,
                "path": str(run_path),
                "event_count": event_count,
            })

    return jsonify({"runs": runs})


@app.route("/download/<run_id>")
def download_artifacts(run_id: str):
    """
    Download all artifacts from a run as a ZIP file.
    Only available for BUILD mode runs.
    """
    run_dir = _get_run_dir(run_id)

    # Try to find run directory if it doesn't exist at expected path
    if not run_dir.exists():
        for subdir in Path("runs").iterdir():
            if subdir.is_dir() and subdir.name == run_id:
                run_dir = subdir
                break
        else:
            # Search by prefix match on run_id
            for subdir in Path("runs").iterdir():
                if subdir.is_dir() and subdir.name.startswith(run_id[:8]):
                    run_dir = subdir
                    break

    if not run_dir.exists():
        return jsonify({"error": "Run not found", "run_id": run_id}), 404

    # Collect all files in run directory
    files = []
    for f in run_dir.rglob("*"):
        if f.is_file() and f.name not in ("packet-events.jsonl",):
            files.append(f)

    if not files:
        return jsonify({"error": "No artifacts found for run", "run_id": run_id}), 404

    # Create ZIP in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            # Use relative path inside ZIP
            arcname = str(f.relative_to(run_dir))
            zf.write(f, arcname)

    zip_buffer.seek(0)
    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"artifacts-{run_id[:8]}.zip",
    )


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Ensure runs/demo directory exists
    Path("runs/demo").mkdir(parents=True, exist_ok=True)
    print("=" * 60)
    print("  DynamicAnythingMachine Web Demo")
    print("  Server: http://localhost:5000")
    print("  Open http://localhost:5000 in your browser")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)