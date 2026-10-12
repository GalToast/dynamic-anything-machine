from __future__ import annotations

import json
from operator import add
from pathlib import Path
from typing import Annotated

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict


class CockpitState(TypedDict):
    objective: str
    profile_name: str
    profile: dict
    run_id: str
    worker_packets: Annotated[list[dict], add]
    notes: Annotated[list[str], add]


def load_profile(name: str, root: Path) -> dict:
    path = root / "profiles" / f"{name}.json"
    if not path.exists():
        available = sorted(p.stem for p in (root / "profiles").glob("*.json"))
        raise FileNotFoundError(f"Unknown profile '{name}'. Available: {', '.join(available)}")
    return json.loads(path.read_text(encoding="utf-8"))


def _supervisor(state: CockpitState) -> dict:
    profile = state["profile"]
    return {
        "notes": [
            f"supervisor loaded profile '{state['profile_name']}' with {len(profile['lanes'])} lanes.",
            f"max_depth={profile.get('max_depth', 1)} max_parallel={profile.get('max_parallel', 3)}",
        ]
    }


def _emit_packets(state: CockpitState) -> dict:
    packets: list[dict] = []
    for index, lane in enumerate(state["profile"]["lanes"], start=1):
        worker_id = f"{state['run_id']}-{index:02d}-{lane['id']}"
        packets.append(
            {
                "run_id": state["run_id"],
                "worker_id": worker_id,
                "lane_id": lane["id"],
                "title": lane["title"],
                "objective": state["objective"],
                "recommended_harnesses": lane.get("recommended_harnesses", []),
                "mode": lane.get("mode", "readonly"),
                "owned_paths": lane.get("owned_paths", []),
                "avoid_paths": lane.get("avoid_paths", []),
                "can_create_child_run": bool(lane.get("can_create_child_run", False)),
                "max_child_depth": int(lane.get("max_child_depth", 0)),
                "timeout_seconds": int(lane.get("timeout_seconds", 900)),
                "prompt": lane["prompt"],
                "deliverable_contract": lane.get("deliverable_contract", []),
            }
        )
    return {
        "worker_packets": packets,
        "notes": [f"emitted {len(packets)} cockpit-neutral worker packets."],
    }


def build_cockpit_graph():
    graph = StateGraph(CockpitState)
    graph.add_node("supervisor", _supervisor)
    graph.add_node("emit_packets", _emit_packets)

    graph.add_edge(START, "supervisor")
    graph.add_edge("supervisor", "emit_packets")
    graph.add_edge("emit_packets", END)
    return graph.compile()
