from __future__ import annotations

from operator import add
from typing import Annotated, Literal

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict


class TeamState(TypedDict):
    objective: str
    team: Literal["research", "build"]
    assignments: list[str]
    findings: Annotated[list[str], add]
    team_summary: str


class SwarmState(TypedDict):
    objective: str
    plan: list[str]
    findings: Annotated[list[str], add]
    final: str


def _team_lead(state: TeamState) -> dict:
    if state["team"] == "research":
        assignments = [
            "source_scout: identify likely channels and role families",
            "fit_analyst: score wheelhouse fit and risk",
        ]
    else:
        assignments = [
            "scaffold_worker: define deliverable files and run path",
            "risk_worker: identify overclaim, secret, and runaway-agent risks",
        ]

    return {
        "assignments": assignments,
        "findings": [f"{state['team']} lead split objective into {len(assignments)} worker lanes."],
    }


def _source_or_scaffold_worker(state: TeamState) -> dict:
    if state["team"] == "research":
        result = (
            "source_scout: prioritize Wellfound, LinkedIn filtered search, niche AI "
            "automation boards, and direct company ATS pages; avoid generic low-signal blasts."
        )
    else:
        result = (
            "scaffold_worker: create isolated proof folders, deterministic smoke commands, "
            "and paste-ready notes before adding provider-backed autonomy."
        )

    return {"findings": [result]}


def _fit_or_risk_worker(state: TeamState) -> dict:
    if state["team"] == "research":
        result = (
            "fit_analyst: score highest when role asks for RAG, workflow automation, "
            "browser/API tooling, human review gates, or CLI-agent operations."
        )
    else:
        result = (
            "risk_worker: cap delegation depth, assign file ownership, log every send/action, "
            "and keep unverified LangChain/LangGraph claims bounded."
        )

    return {"findings": [result]}


def _team_reviewer(state: TeamState) -> dict:
    summary = f"{state['team']} team complete: " + " | ".join(state["findings"])
    return {
        "team_summary": summary,
        "findings": [f"{state['team']} reviewer accepted {len(state['findings'])} findings."],
    }


def build_team_graph():
    graph = StateGraph(TeamState)
    graph.add_node("team_lead", _team_lead)
    graph.add_node("worker_primary", _source_or_scaffold_worker)
    graph.add_node("worker_secondary", _fit_or_risk_worker)
    graph.add_node("team_reviewer", _team_reviewer)

    graph.add_edge(START, "team_lead")
    graph.add_edge("team_lead", "worker_primary")
    graph.add_edge("team_lead", "worker_secondary")
    graph.add_edge("worker_primary", "team_reviewer")
    graph.add_edge("worker_secondary", "team_reviewer")
    graph.add_edge("team_reviewer", END)
    return graph.compile()


TEAM_GRAPH = build_team_graph()


def _supervisor(state: SwarmState) -> dict:
    plan = [
        "research_team: find and score the best channels",
        "build_team: define proof, safety, and run controls",
        "final_integrator: merge results into next action",
    ]
    return {
        "plan": plan,
        "findings": [f"supervisor created {len(plan)} top-level lanes."],
    }


def _research_team_node(state: SwarmState) -> dict:
    result = TEAM_GRAPH.invoke(
        {
            "objective": state["objective"],
            "team": "research",
            "assignments": [],
            "findings": [],
            "team_summary": "",
        }
    )
    return {"findings": [result["team_summary"]]}


def _build_team_node(state: SwarmState) -> dict:
    result = TEAM_GRAPH.invoke(
        {
            "objective": state["objective"],
            "team": "build",
            "assignments": [],
            "findings": [],
            "team_summary": "",
        }
    )
    return {"findings": [result["team_summary"]]}


def _final_integrator(state: SwarmState) -> dict:
    final = (
        f"Objective: {state['objective']}\n\n"
        "Decision: use a bounded hierarchical swarm: one supervisor, two team leads, "
        "parallel workers, and one final reviewer. Keep depth at 2 until logs, cost, "
        "and ownership are boringly reliable.\n\n"
        "Findings:\n- " + "\n- ".join(state["findings"])
    )
    return {"final": final}


def build_swarm_graph():
    graph = StateGraph(SwarmState)
    graph.add_node("supervisor", _supervisor)
    graph.add_node("research_team", _research_team_node)
    graph.add_node("build_team", _build_team_node)
    graph.add_node("final_integrator", _final_integrator)

    graph.add_edge(START, "supervisor")
    graph.add_edge("supervisor", "research_team")
    graph.add_edge("supervisor", "build_team")
    graph.add_edge("research_team", "final_integrator")
    graph.add_edge("build_team", "final_integrator")
    graph.add_edge("final_integrator", END)
    return graph.compile()
