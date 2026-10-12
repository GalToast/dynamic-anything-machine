from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from operator import add
from pathlib import Path
from typing import Annotated, Any

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from dynamic_schema import validate_graph_spec


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[1]


class DynamicState(TypedDict):
    objective: str
    run_id: str
    graph_spec: dict[str, Any]
    artifacts: Annotated[list[dict[str, Any]], add]
    final: str


def stable_run_id(objective: str) -> str:
    digest = hashlib.sha256(objective.encode("utf-8")).hexdigest()[:10]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = "-".join(re.findall(r"[a-z0-9]+", objective.lower())[:6]) or "objective"
    return f"{stamp}-{slug}-{digest}"


def architect_graph_spec(objective: str) -> dict[str, Any]:
    objective_l = objective.lower()
    is_app = any(term in objective_l for term in ["job", "application", "contract", "resume"])
    is_code = any(term in objective_l for term in ["code", "repo", "bug", "implement", "test"])
    is_research = any(term in objective_l for term in ["research", "compare", "market", "source"])

    nodes: list[dict[str, Any]] = [
        {
            "id": "plan",
            "type": "planner",
            "title": "Objective Planner",
            "instruction": "Restate the objective, identify constraints, and choose a bounded execution shape.",
            "tools": [],
        },
        {
            "id": "retrieve_context",
            "type": "retriever",
            "title": "Context Retriever",
            "instruction": "Retrieve relevant local proof, truth, memory, and operating docs.",
            "tools": ["file_read"],
        },
    ]

    if is_app:
        nodes.extend(
            [
                {
                    "id": "score_fit",
                    "type": "scorer",
                    "title": "Opportunity Fit Scorer",
                    "instruction": "Score opportunity fit against AI automation, RAG, workflow, agent, and browser/API proof.",
                    "tools": [],
                },
                {
                    "id": "route",
                    "type": "router",
                    "title": "Application Route Gate",
                    "instruction": "Route into apply-now, approval-needed, or hold/drop based on risk.",
                    "tools": [],
                },
                {
                    "id": "packet",
                    "type": "packet_emitter",
                    "title": "Packet Emitter",
                    "instruction": "Emit worker packets for source scout, packet builder, critic, and integrator lanes.",
                    "tools": [],
                },
            ]
        )
    elif is_code:
        nodes.extend(
            [
                {
                    "id": "code_packet",
                    "type": "packet_emitter",
                    "title": "Code Work Packet Emitter",
                    "instruction": "Emit repo scout, implementation, review, and verification packets.",
                    "tools": [],
                }
            ]
        )
    elif is_research:
        nodes.extend(
            [
                {
                    "id": "research_packet",
                    "type": "packet_emitter",
                    "title": "Research Packet Emitter",
                    "instruction": "Emit scout, synthesis, critic, and integrator packets.",
                    "tools": [],
                }
            ]
        )
    else:
        nodes.extend(
            [
                {
                    "id": "general_packet",
                    "type": "packet_emitter",
                    "title": "General Worker Packet Emitter",
                    "instruction": "Emit specialist worker packets that can be executed by any CLI cockpit.",
                    "tools": [],
                }
            ]
        )

    nodes.extend(
        [
            {
                "id": "critic",
                "type": "critic",
                "title": "Safety And Quality Critic",
                "instruction": "Find overreach, missing evidence, side-effect risk, unclear ownership, and weak success criteria.",
                "tools": [],
            },
            {
                "id": "human_gate",
                "type": "human_gate",
                "title": "Human Approval Gate",
                "instruction": "Require explicit human approval before side effects, spend, browser login, applications, or file mutation.",
                "tools": ["approval"],
            },
            {
                "id": "integrate",
                "type": "integrator",
                "title": "Final Integrator",
                "instruction": "Integrate artifacts into a concise result and next action.",
                "tools": [],
            },
        ]
    )

    node_ids = [node["id"] for node in nodes]
    edges = [["START", node_ids[0]]]
    edges.extend([[node_ids[i], node_ids[i + 1]] for i in range(len(node_ids) - 1)])
    edges.append([node_ids[-1], "END"])

    return {
        "schema_version": "dynamic-langgraph-v1",
        "name": "ephemeral-objective-graph",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "objective": objective,
        "determinism": {
            "architect": "heuristic-v1",
            "temperature": 0,
            "side_effects": "disabled-until-human-gate",
        },
        "nodes": nodes,
        "edges": edges,
        "limits": {"max_nodes": 10, "max_depth": 8, "max_parallel": 4},
    }


def compile_dynamic_graph(spec: dict[str, Any]):
    errors = validate_graph_spec(spec)
    if errors:
        raise ValueError("invalid graph spec: " + "; ".join(errors))

    graph = StateGraph(DynamicState)
    for node in spec["nodes"]:
        graph.add_node(node["id"], _make_node(node))

    for source, target in spec["edges"]:
        source_ref = START if source == "START" else source
        target_ref = END if target == "END" else target
        graph.add_edge(source_ref, target_ref)
    return graph.compile()


def _make_node(node: dict[str, Any]):
    def run(state: DynamicState) -> dict[str, Any]:
        node_type = node["type"]
        artifact = {
            "node_id": node["id"],
            "node_type": node_type,
            "title": node["title"],
            "instruction": node["instruction"],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if node_type == "planner":
            artifact["summary"] = _plan_summary(state["objective"])
        elif node_type == "retriever":
            artifact["hits"] = retrieve_context(state["objective"])
        elif node_type == "scorer":
            artifact["score"] = score_objective_fit(state["objective"], state["artifacts"])
        elif node_type == "router":
            artifact["route"] = route_from_artifacts(state["artifacts"])
        elif node_type == "packet_emitter":
            artifact["packets"] = emit_worker_packets(state["run_id"], state["objective"], node)
        elif node_type == "critic":
            artifact["findings"] = critic_findings(state["objective"], state["artifacts"])
        elif node_type == "human_gate":
            artifact["approval"] = {
                "required": True,
                "status": "awaiting_human",
                "reason": "Dynamic graph may produce side-effect recommendations; execution stays read-only until approval.",
            }
        elif node_type == "integrator":
            final = integrate_result(state["objective"], state["graph_spec"], state["artifacts"])
            return {"artifacts": [artifact], "final": final}
        return {"artifacts": [artifact]}

    return run


def _plan_summary(objective: str) -> str:
    return (
        "Create a bounded, auditable graph for the objective. Keep the graph ephemeral, "
        "save its spec, require critic and human gate nodes, and execute only deterministic "
        "read-only logic until a cockpit approves stronger tools."
    )


def retrieve_context(objective: str, limit: int = 6) -> list[dict[str, Any]]:
    docs = [
        REPO_ROOT / "employment/collaboration/workspace/resume-system/resume-truth-ledger-20260506.md",
        REPO_ROOT / "outreach/ai-workflow-proof-one-pager-20260507.md",
        REPO_ROOT / "outreach/rag-retrieval-eval-proof-20260507.md",
        REPO_ROOT / "outreach/high-volume-application-engine-20260507.md",
        REPO_ROOT / "memory/local-ai.md",
        REPO_ROOT / "memory/active-context.md",
    ]
    query_terms = set(_tokens(objective))
    hits: list[dict[str, Any]] = []
    for path in docs:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        chunks = _chunks(text)
        for index, chunk in enumerate(chunks):
            terms = set(_tokens(chunk))
            score = len(query_terms & terms)
            if score:
                hits.append(
                    {
                        "path": str(path.relative_to(REPO_ROOT)),
                        "chunk": index,
                        "score": score,
                        "preview": " ".join(chunk.split())[:500],
                    }
                )
    return sorted(hits, key=lambda item: item["score"], reverse=True)[:limit]


def score_objective_fit(objective: str, artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    objective_l = objective.lower()
    positive_terms = ["rag", "langgraph", "agent", "automation", "workflow", "browser", "api", "retrieval", "cli"]
    risky_terms = ["submit", "apply", "login", "payment", "delete", "deploy", "secret", "credential"]
    positives = [term for term in positive_terms if term in objective_l]
    risks = [term for term in risky_terms if term in objective_l]
    return {
        "fit_score": min(10, 4 + len(positives)),
        "positive_signals": positives,
        "risk_signals": risks,
        "context_hits": sum(len(item.get("hits", [])) for item in artifacts),
    }


def route_from_artifacts(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    score = next((item.get("score", {}) for item in artifacts if item.get("node_type") == "scorer"), {})
    risks = score.get("risk_signals", [])
    fit = score.get("fit_score", 0)
    if risks:
        lane = "approval-needed"
    elif fit >= 7:
        lane = "build-next"
    else:
        lane = "clarify-or-hold"
    return {"lane": lane, "fit_score": fit, "risk_signals": risks}


def emit_worker_packets(run_id: str, objective: str, node: dict[str, Any]) -> list[dict[str, Any]]:
    base = {
        "objective": objective,
        "mode": "readonly",
        "result_contract": ["summary", "evidence paths", "risks", "next action"],
    }
    if "code" in node["id"]:
        lanes = ["repo-scout", "implementation-plan", "review", "verification"]
    elif "research" in node["id"]:
        lanes = ["source-scout", "synthesis", "critic", "integrator"]
    else:
        lanes = ["planner", "worker", "critic", "integrator"]
    return [
        {
            **base,
            "worker_id": f"{run_id}-{index:02d}-{lane}",
            "lane": lane,
            "prompt": f"{lane}: work toward the objective with no side effects unless a human gate approves them.",
        }
        for index, lane in enumerate(lanes, start=1)
    ]


def critic_findings(objective: str, artifacts: list[dict[str, Any]]) -> list[str]:
    findings = [
        "Graph is dynamic but constrained by schema; no arbitrary Python/tool access is generated.",
        "Human gate is required before side effects, applications, deployments, login flows, or spend.",
    ]
    if not any(item.get("node_type") == "retriever" and item.get("hits") for item in artifacts):
        findings.append("Retriever produced no context hits; ask for clarification or broaden source docs.")
    if "anything" in objective.lower():
        findings.append("Objective is broad; packet outputs should narrow ownership and success criteria before work starts.")
    return findings


def integrate_result(objective: str, spec: dict[str, Any], artifacts: list[dict[str, Any]]) -> str:
    route = next((item.get("route") for item in artifacts if item.get("node_type") == "router"), None)
    packets = [
        packet
        for item in artifacts
        for packet in item.get("packets", [])
        if item.get("node_type") == "packet_emitter"
    ]
    findings = [
        finding
        for item in artifacts
        for finding in item.get("findings", [])
        if item.get("node_type") == "critic"
    ]
    lines = [
        f"# Dynamic LangGraph Run",
        "",
        f"Objective: {objective}",
        f"Generated nodes: {len(spec['nodes'])}",
        f"Generated edges: {len(spec['edges'])}",
        f"Route: {route['lane'] if route else 'not-applicable'}",
        "",
        "## Worker Packets",
        "",
    ]
    lines.extend(f"- `{packet['worker_id']}`: {packet['lane']}" for packet in packets)
    if not packets:
        lines.append("- None")
    lines.extend(["", "## Critic Findings", ""])
    lines.extend(f"- {finding}" for finding in findings)
    lines.extend(
        [
            "",
            "## Deterministic Boundary",
            "",
            "This run generated and executed an ephemeral graph spec. It did not grant arbitrary tool access, mutate files, submit applications, deploy code, or spend tokens beyond local deterministic execution.",
        ]
    )
    return "\n".join(lines) + "\n"


def _tokens(text: str) -> list[str]:
    stop = {"the", "and", "for", "that", "with", "this", "from", "into", "you", "our", "are"}
    return [term for term in re.findall(r"[a-z0-9]{3,}", text.lower()) if term not in stop]


def _chunks(text: str, words_per_chunk: int = 90) -> list[str]:
    words = text.split()
    return [" ".join(words[i : i + words_per_chunk]) for i in range(0, len(words), words_per_chunk)]
