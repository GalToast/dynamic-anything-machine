from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ALLOWED_NODE_TYPES = {
    "planner",
    "retriever",
    "scorer",
    "router",
    "packet_emitter",
    "critic",
    "human_gate",
    "integrator",
}


@dataclass(frozen=True)
class ValidationPolicy:
    max_nodes: int = 10
    max_edges: int = 16
    max_depth: int = 8
    require_critic: bool = True
    require_human_gate: bool = True


def validate_graph_spec(spec: dict[str, Any], policy: ValidationPolicy | None = None) -> list[str]:
    policy = policy or ValidationPolicy()
    errors: list[str] = []

    if spec.get("schema_version") != "dynamic-langgraph-v1":
        errors.append("schema_version must be dynamic-langgraph-v1")

    nodes = spec.get("nodes")
    edges = spec.get("edges")
    if not isinstance(nodes, list) or not nodes:
        errors.append("nodes must be a non-empty list")
        nodes = []
    if not isinstance(edges, list) or not edges:
        errors.append("edges must be a non-empty list")
        edges = []

    if len(nodes) > policy.max_nodes:
        errors.append(f"too many nodes: {len(nodes)} > {policy.max_nodes}")
    if len(edges) > policy.max_edges:
        errors.append(f"too many edges: {len(edges)} > {policy.max_edges}")

    node_ids: set[str] = set()
    node_types: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            errors.append("each node must be an object")
            continue
        node_id = node.get("id")
        node_type = node.get("type")
        if not isinstance(node_id, str) or not node_id:
            errors.append("node id must be a non-empty string")
            continue
        if node_id in {"START", "END"}:
            errors.append(f"node id is reserved: {node_id}")
        if node_id in node_ids:
            errors.append(f"duplicate node id: {node_id}")
        node_ids.add(node_id)
        if node_type not in ALLOWED_NODE_TYPES:
            errors.append(f"node {node_id} has disallowed type: {node_type}")
        else:
            node_types.add(node_type)

        tools = node.get("tools", [])
        if tools and not isinstance(tools, list):
            errors.append(f"node {node_id} tools must be a list")
        if "shell" in tools or "browser" in tools or "file_write" in tools:
            if node_type != "human_gate":
                errors.append(
                    f"node {node_id} requests side-effect tool {tools}; only human_gate may request approval for side effects"
                )

    if policy.require_critic and "critic" not in node_types:
        errors.append("graph must include a critic node")
    if policy.require_human_gate and "human_gate" not in node_types:
        errors.append("graph must include a human_gate node")
    if "integrator" not in node_types:
        errors.append("graph must include an integrator node")

    outgoing: dict[str, list[str]] = {}
    incoming: dict[str, list[str]] = {}
    for edge in edges:
        if not isinstance(edge, list) or len(edge) != 2:
            errors.append("each edge must be a two-item list [from, to]")
            continue
        source, target = edge
        if source != "START" and source not in node_ids:
            errors.append(f"edge source is unknown: {source}")
        if target != "END" and target not in node_ids:
            errors.append(f"edge target is unknown: {target}")
        outgoing.setdefault(source, []).append(target)
        incoming.setdefault(target, []).append(source)

    if "START" not in outgoing:
        errors.append("graph must have an edge from START")
    if "END" not in incoming:
        errors.append("graph must have an edge to END")

    if not errors:
        errors.extend(_validate_acyclic_depth(outgoing, policy.max_depth))

    return errors


def _validate_acyclic_depth(outgoing: dict[str, list[str]], max_depth: int) -> list[str]:
    errors: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, depth: int) -> None:
        if depth > max_depth:
            errors.append(f"graph depth exceeds {max_depth} at {node}")
            return
        if node in visiting:
            errors.append(f"cycle detected at {node}")
            return
        if node in visited or node == "END":
            return
        visiting.add(node)
        for target in outgoing.get(node, []):
            visit(target, depth + 1)
        visiting.remove(node)
        visited.add(node)

    visit("START", 0)
    return errors
