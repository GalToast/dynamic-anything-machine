"""
Tests for GraphSpec schema validation, enums, and serialization.
"""

import pytest
from pydantic import ValidationError

from dynamic_schema import (
    GraphSpec,
    GraphNode,
    GraphEdge,
    NodeType,
    SideEffectType,
    DifficultyLevel,
    GraphShape,
    DelegationPermission,
    WorkerStatus,
    RunStatus,
    export_schema_as_json,
    GRAPH_SPEC_JSON_SCHEMA,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def minimal_dict():
    """Return a minimal valid graph spec dict."""
    return {
        "run_id": "test-run-001",
        "difficulty": "simple",
        "shape_rationale": "Basic test graph",
        "nodes": [],
        "edges": [],
    }


@pytest.fixture
def valid_node_dict():
    """Return a valid single-node dict."""
    return {
        "node_id": "node-a",
        "role": "worker",
        "task_prompt": "Do the thing",
        "allowed_tools": ["read_file", "grep"],
        "forbidden_tools": [],
        "owned_paths": ["/tmp"],
        "forbidden_paths": [],
        "timeout": 30,
        "budget": 5.0,
        "delegation_permission": "none",
        "max_child_depth": 0,
        "depends_on": [],
    }


@pytest.fixture
def two_node_chain():
    """Return a valid spec with two nodes and an edge between them."""
    return {
        "run_id": "test-chain",
        "difficulty": "simple",
        "shape_rationale": "Chain test",
        "nodes": [
            {
                "node_id": "planner-1",
                "role": "planner",
                "task_prompt": "Plan the work",
                "allowed_tools": [],
                "forbidden_tools": [],
                "owned_paths": [],
                "forbidden_paths": [],
                "depends_on": [],
            },
            {
                "node_id": "worker-1",
                "role": "worker",
                "task_prompt": "Execute the plan",
                "allowed_tools": ["read_file"],
                "forbidden_tools": [],
                "owned_paths": [],
                "forbidden_paths": [],
                "depends_on": ["planner-1"],
            },
        ],
        "edges": [
            {"from_node": "planner-1", "to_node": "worker-1"},
        ],
    }


# ---------------------------------------------------------------------------
# GraphSpec.from_dict
# ---------------------------------------------------------------------------

def test_graph_spec_from_dict_valid(minimal_dict):
    """Test valid graph spec creation from dict."""
    spec = GraphSpec.from_dict(minimal_dict)
    assert spec.run_id == "test-run-001"
    assert spec.difficulty == DifficultyLevel.SIMPLE
    assert spec.nodes == []
    assert spec.edges == []


def test_graph_spec_validates_required_fields():
    """Test that missing required fields are caught."""
    # run_id missing
    with pytest.raises(ValidationError) as exc_info:
        GraphSpec.from_dict({"difficulty": "simple", "shape_rationale": "test"})
    errors = exc_info.value.errors()
    assert any("run_id" in str(e["loc"]) for e in errors)

    # difficulty missing
    with pytest.raises(ValidationError) as exc_info:
        GraphSpec.from_dict({"run_id": "r", "shape_rationale": "test"})
    errors = exc_info.value.errors()
    assert any("difficulty" in str(e["loc"]) for e in errors)

    # shape_rationale missing
    with pytest.raises(ValidationError) as exc_info:
        GraphSpec.from_dict({"run_id": "r", "difficulty": "simple"})
    errors = exc_info.value.errors()
    assert any("shape_rationale" in str(e["loc"]) for e in errors)


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------

def test_graph_spec_cycle_detection():
    """Test that cycles in depends_on are detected."""
    spec_dict = {
        "run_id": "cycle-test",
        "difficulty": "simple",
        "shape_rationale": "Cycle detection",
        "nodes": [
            {
                "node_id": "a",
                "role": "planner",
                "task_prompt": "A",
                "depends_on": ["b"],
            },
            {
                "node_id": "b",
                "role": "worker",
                "task_prompt": "B",
                "depends_on": ["a"],
            },
        ],
        "edges": [],
    }
    with pytest.raises(ValidationError) as exc_info:
        GraphSpec.from_dict(spec_dict)
    assert "Circular dependency" in str(exc_info.value)


def test_graph_spec_self_cycle_detected():
    """Test that a node depending on itself is caught."""
    spec_dict = {
        "run_id": "self-cycle",
        "difficulty": "simple",
        "shape_rationale": "Self reference",
        "nodes": [
            {
                "node_id": "self-node",
                "role": "worker",
                "task_prompt": "Loop",
                "depends_on": ["self-node"],
            },
        ],
        "edges": [],
    }
    with pytest.raises(ValidationError) as exc_info:
        GraphSpec.from_dict(spec_dict)
    # The GraphNode model validator catches self-reference
    assert "cannot depend on itself" in str(exc_info.value)


def test_graph_spec_longer_chain_cycle():
    """Test cycle detection in a longer dependency chain: a -> b -> c -> a"""
    spec_dict = {
        "run_id": "long-cycle",
        "difficulty": "simple",
        "shape_rationale": "Long cycle",
        "nodes": [
            {
                "node_id": "n1",
                "role": "planner",
                "task_prompt": "N1",
                "depends_on": ["n2"],
            },
            {
                "node_id": "n2",
                "role": "worker",
                "task_prompt": "N2",
                "depends_on": ["n3"],
            },
            {
                "node_id": "n3",
                "role": "critic",
                "task_prompt": "N3",
                "depends_on": ["n1"],
            },
        ],
        "edges": [],
    }
    with pytest.raises(ValidationError) as exc_info:
        GraphSpec.from_dict(spec_dict)
    assert "Circular dependency" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Edge validation
# ---------------------------------------------------------------------------

def test_edge_references_valid_nodes():
    """Test that edges reference existing nodes."""
    spec_dict = {
        "run_id": "edge-refs",
        "difficulty": "simple",
        "shape_rationale": "Edge validation",
        "nodes": [
            {
                "node_id": "real-node",
                "role": "worker",
                "task_prompt": "Real",
                "depends_on": [],
            },
        ],
        "edges": [
            {"from_node": "real-node", "to_node": "phantom-node"},
        ],
    }
    with pytest.raises(ValidationError) as exc_info:
        GraphSpec.from_dict(spec_dict)
    assert "phantom-node" in str(exc_info.value)


def test_edge_from_node_unknown():
    """Test that unknown from_node is caught."""
    spec_dict = {
        "run_id": "edge-from-unknown",
        "difficulty": "simple",
        "shape_rationale": "Edge from unknown",
        "nodes": [
            {
                "node_id": "known",
                "role": "worker",
                "task_prompt": "Known",
                "depends_on": [],
            },
        ],
        "edges": [
            {"from_node": "unknown-source", "to_node": "known"},
        ],
    }
    with pytest.raises(ValidationError) as exc_info:
        GraphSpec.from_dict(spec_dict)
    assert "unknown-source" in str(exc_info.value)


def test_edge_same_node_rejected():
    """Test that from_node == to_node is rejected."""
    spec_dict = {
        "run_id": "self-edge",
        "difficulty": "simple",
        "shape_rationale": "Self edge",
        "nodes": [
            {
                "node_id": "solo",
                "role": "worker",
                "task_prompt": "Solo",
                "depends_on": [],
            },
        ],
        "edges": [
            {"from_node": "solo", "to_node": "solo"},
        ],
    }
    with pytest.raises(ValidationError) as exc_info:
        GraphSpec.from_dict(spec_dict)
    assert "must be different" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Serialization roundtrip
# ---------------------------------------------------------------------------

def test_graph_spec_to_dict_roundtrip(two_node_chain):
    """Test serialization roundtrip: spec -> dict -> spec."""
    spec = GraphSpec.from_dict(two_node_chain)
    as_dict = spec.to_dict()

    # Reconstruct from dict
    rebuilt = GraphSpec.from_dict(as_dict)
    assert rebuilt.run_id == spec.run_id
    assert len(rebuilt.nodes) == len(spec.nodes)
    assert rebuilt.nodes[0].node_id == spec.nodes[0].node_id
    assert rebuilt.nodes[1].depends_on == spec.nodes[1].depends_on
    assert len(rebuilt.edges) == len(spec.edges)


def test_graph_spec_to_dict_contains_all_fields(minimal_dict):
    """Test that to_dict includes all expected top-level keys."""
    spec = GraphSpec.from_dict(minimal_dict)
    d = spec.to_dict()
    assert "run_id" in d
    assert "difficulty" in d
    assert "shape_rationale" in d
    assert "nodes" in d
    assert "edges" in d
    assert "seed" in d
    assert "timestamp" in d
    assert "version" in d


# ---------------------------------------------------------------------------
# Enum tests
# ---------------------------------------------------------------------------

def test_difficulty_level_enum():
    """Test difficulty level values."""
    assert DifficultyLevel.TRIVIAL.value == "trivial"
    assert DifficultyLevel.SIMPLE.value == "simple"
    assert DifficultyLevel.MODERATE.value == "moderate"
    assert DifficultyLevel.COMPLEX.value == "complex"
    assert DifficultyLevel.EXTREME.value == "extreme"
    # All values are strings
    assert all(isinstance(v.value, str) for v in DifficultyLevel)


def test_node_type_enum():
    """Test node type values."""
    expected = {"planner", "retriever", "scorer", "router", "critic",
                "worker", "integrator", "reviewer", "approver", "executor"}
    assert {v.value for v in NodeType} == expected


def test_side_effect_type_enum():
    """Test side effect type values."""
    expected = {
        "file_write", "shell_command", "browser_action", "login_flow",
        "send_message", "submit_application", "network_call", "install",
        "deploy", "payment", "delete_file", "expose_secret",
    }
    assert {v.value for v in SideEffectType} == expected


def test_delegation_permission_enum():
    """Test delegation permission values."""
    assert DelegationPermission.NONE.value == "none"
    assert DelegationPermission.REQUEST.value == "request"
    assert DelegationPermission.APPROVE.value == "approve"


def test_worker_status_enum():
    """Test worker status values."""
    expected = {"success", "failure", "timeout", "rejected", "pending"}
    assert {v.value for v in WorkerStatus} == expected


def test_run_status_enum():
    """Test run status values."""
    expected = {"running", "paused", "completed", "failed", "cancelled"}
    assert {v.value for v in RunStatus} == expected


# ---------------------------------------------------------------------------
# JSON Schema export
# ---------------------------------------------------------------------------

def test_json_schema_export(tmp_path):
    """Test JSON schema export."""
    schema = GraphSpec.export_json_schema()
    assert isinstance(schema, dict)
    assert "definitions" in schema
    # Check that enum definitions are present
    definitions = schema["definitions"]
    assert "NodeType" in definitions
    assert definitions["NodeType"]["type"] == "string"
    assert "GraphNode" in definitions
    assert "GraphSpec" in schema or "$ref" in str(schema)

    # Write schema to file
    export_path = tmp_path / "graph_spec_schema.json"
    export_schema_as_json(str(export_path))
    assert export_path.exists()
    imported = __import__('json').loads(export_path.read_text())
    assert "definitions" in imported


def test_graph_spec_json_schema_cached():
    """Test that the pre-computed schema is valid."""
    assert isinstance(GRAPH_SPEC_JSON_SCHEMA, dict)
    assert "definitions" in GRAPH_SPEC_JSON_SCHEMA
    assert "NodeType" in GRAPH_SPEC_JSON_SCHEMA["definitions"]


# ---------------------------------------------------------------------------
# Additional validation tests
# ---------------------------------------------------------------------------

def test_empty_run_id_rejected():
    """Test that empty run_id is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        GraphSpec.from_dict({
            "run_id": "   ",
            "difficulty": "simple",
            "shape_rationale": "Whitespace run_id",
        })
    assert "run_id" in str(exc_info.value)


def test_whitespace_run_id_stripped():
    """Test that whitespace in run_id is stripped."""
    spec = GraphSpec.from_dict({
        "run_id": "  trimmed-123  ",
        "difficulty": "simple",
        "shape_rationale": "Trim test",
    })
    assert spec.run_id == "trimmed-123"


def test_max_depth_constraint():
    """Test that max_depth must be >= 1."""
    with pytest.raises(ValidationError) as exc_info:
        GraphSpec.from_dict({
            "run_id": "depth-test",
            "difficulty": "simple",
            "shape_rationale": "Depth test",
            "max_depth": 0,
        })
    assert "max_depth" in str(exc_info.value)


def test_node_task_prompt_required(valid_node_dict):
    """Test that task_prompt is required on nodes."""
    bad = dict(valid_node_dict)
    del bad["task_prompt"]
    spec_dict = {
        "run_id": "prompt-test",
        "difficulty": "simple",
        "shape_rationale": "Prompt test",
        "nodes": [bad],
        "edges": [],
    }
    with pytest.raises(ValidationError) as exc_info:
        GraphSpec.from_dict(spec_dict)
    assert "task_prompt" in str(exc_info.value)


def test_validate_schema_returns_errors():
    """Test that validate_schema returns a list of error strings."""
    bad_data = {"run_id": "", "difficulty": "invalid_level"}
    errors = GraphSpec.validate_schema(bad_data)
    assert isinstance(errors, list)
    assert len(errors) > 0
    assert any("difficulty" in e or "invalid_level" in e.lower() for e in errors)


def test_validate_schema_returns_empty_for_valid(two_node_chain):
    """Test that validate_schema returns [] for valid data."""
    errors = GraphSpec.validate_schema(two_node_chain)
    assert errors == []


def test_graph_spec_validates_total_timeout():
    """Test that total_timeout must be positive if provided."""
    spec_dict = {
        "run_id": "timeout-test",
        "difficulty": "simple",
        "shape_rationale": "Timeout test",
        "total_timeout": 0,
    }
    with pytest.raises(ValidationError) as exc_info:
        GraphSpec.from_dict(spec_dict)
    assert "total_timeout" in str(exc_info.value)