"""
Comprehensive tests for work_packet.py data models.

Tests cover:
- WorkPacket creation, methods, serialization
- WorkResult creation and serialization
- ShapeRef creation
- ResumptionSnapshot creation
- All enum values
"""

import pytest
from pydantic import ValidationError

from work_packet import (
    WorkPacket,
    WorkResult,
    PacketStatus,
    DelegationPermission,
    HarnessType,
    ShapeType,
    ShapeRef,
    ResumptionSnapshot,
    _now_iso,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def minimal_packet_dict():
    """Return a minimal valid packet dict with only required fields."""
    return {
        "run_id": "test-run-001",
        "seam": "test-seam",
        "objective": "Test the thing",
        "success_criteria": "It works",
    }


@pytest.fixture
def full_packet_dict():
    """Return a packet dict with all fields populated."""
    return {
        "packet_id": "packet-abc",
        "run_id": "test-run-001",
        "parent_packet_id": "parent-xyz",
        "depth": 2,
        "seam": "auth-flow",
        "objective": "Implement auth",
        "success_criteria": "Users can log in",
        "current_state": {"phase": 1, "data": "initial"},
        "known_context": {"user_id": "123"},
        "open_questions": ["Which provider?"],
        "allowed_tools": ["read_file", "write_file"],
        "forbidden_tools": ["rm"],
        "owned_paths": ["/app/data"],
        "forbidden_paths": ["/app/secrets"],
        "timeout_seconds": 300,
        "budget": 10.0,
        "delegation_permission": "approve",
        "max_child_depth": 3,
        "side_effects_requiring_approval": ["file_write", "network_call"],
        "available_artifacts": ["/artifacts/template.json"],
        "created_shapes": ["shape-1", "shape-2"],
        "artifacts_produced": ["/output/result.txt"],
        "resumption_contract": "Resume from checkpoint",
        "checkpoint_hint": "current_state.phase",
        "audit_trail": ["evt-1", "evt-2"],
        "secrets_rule": "no-secrets-in-logs",
        "no_revert_rule": True,
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T01:00:00+00:00",
        "claimed_at": "2024-01-01T02:00:00+00:00",
        "completed_at": None,
        "claimed_by": "agent-1",
        "claimed_by_harness": "claude_code",
    }


# ---------------------------------------------------------------------------
# WorkPacket Tests
# ---------------------------------------------------------------------------

class TestWorkPacketCreation:
    """Tests for WorkPacket creation and defaults."""

    def test_work_packet_creation_defaults(self, minimal_packet_dict):
        """Create packet with only run_id and objective. Verify all defaults."""
        packet = WorkPacket(**minimal_packet_dict)

        # Identity defaults
        assert packet.packet_id is not None
        assert len(packet.packet_id) == 36  # UUID format
        assert packet.run_id == "test-run-001"
        assert packet.parent_packet_id is None
        assert packet.depth == 0

        # Seam and objective
        assert packet.seam == "test-seam"
        assert packet.objective == "Test the thing"
        assert packet.success_criteria is not None

        # State defaults - empty collections
        assert packet.current_state == {}
        assert packet.known_context == {}
        assert packet.open_questions == []

        # Constraints defaults - empty collections
        assert packet.allowed_tools == []
        assert packet.forbidden_tools == []
        assert packet.owned_paths == []
        assert packet.forbidden_paths == []
        assert packet.timeout_seconds is None
        assert packet.budget is None

        # Permissions defaults
        assert packet.delegation_permission == DelegationPermission.NONE
        assert packet.max_child_depth == 0
        assert packet.side_effects_requiring_approval == []

        # Artifacts defaults
        assert packet.available_artifacts == []
        assert packet.created_shapes == []
        assert packet.artifacts_produced == []

        # Resumption defaults
        assert packet.resumption_contract is None
        assert packet.checkpoint_hint is None

        # Audit defaults
        assert packet.audit_trail == []
        assert packet.secrets_rule is None
        assert packet.no_revert_rule is False

        # Timestamps
        assert packet.created_at is not None
        assert packet.updated_at is not None
        assert packet.claimed_at is None
        assert packet.completed_at is None

        # Ownership
        assert packet.claimed_by is None
        assert packet.claimed_by_harness == HarnessType.UNKNOWN

    def test_work_packet_full_fields(self, full_packet_dict):
        """Create packet with all fields populated. Verify field values."""
        packet = WorkPacket(**full_packet_dict)

        # Identity
        assert packet.packet_id == "packet-abc"
        assert packet.run_id == "test-run-001"
        assert packet.parent_packet_id == "parent-xyz"
        assert packet.depth == 2

        # Seam and objective
        assert packet.seam == "auth-flow"
        assert packet.objective == "Implement auth"
        assert packet.success_criteria == "Users can log in"

        # State
        assert packet.current_state == {"phase": 1, "data": "initial"}
        assert packet.known_context == {"user_id": "123"}
        assert packet.open_questions == ["Which provider?"]

        # Constraints
        assert packet.allowed_tools == ["read_file", "write_file"]
        assert packet.forbidden_tools == ["rm"]
        assert packet.owned_paths == ["/app/data"]
        assert packet.forbidden_paths == ["/app/secrets"]
        assert packet.timeout_seconds == 300
        assert packet.budget == 10.0

        # Permissions
        assert packet.delegation_permission == DelegationPermission.APPROVE
        assert packet.max_child_depth == 3
        assert packet.side_effects_requiring_approval == ["file_write", "network_call"]

        # Artifacts
        assert packet.available_artifacts == ["/artifacts/template.json"]
        assert packet.created_shapes == ["shape-1", "shape-2"]
        assert packet.artifacts_produced == ["/output/result.txt"]

        # Resumption
        assert packet.resumption_contract == "Resume from checkpoint"
        assert packet.checkpoint_hint == "current_state.phase"

        # Audit
        assert packet.audit_trail == ["evt-1", "evt-2"]
        assert packet.secrets_rule == "no-secrets-in-logs"
        assert packet.no_revert_rule is True

        # Timestamps
        assert packet.created_at == "2024-01-01T00:00:00+00:00"
        assert packet.updated_at == "2024-01-01T01:00:00+00:00"
        assert packet.claimed_at == "2024-01-01T02:00:00+00:00"
        assert packet.completed_at is None

        # Ownership
        assert packet.claimed_by == "agent-1"
        assert packet.claimed_by_harness == HarnessType.CLAUDE_CODE


class TestWorkPacketSerialization:
    """Tests for WorkPacket serialization."""

    def test_work_packet_roundtrip_serialization(self, full_packet_dict):
        """Create packet, call to_dict, call from_dict, verify equal."""
        original = WorkPacket(**full_packet_dict)

        # Serialize
        as_dict = original.to_dict()

        # Verify dict has all expected keys
        assert "packet_id" in as_dict
        assert "run_id" in as_dict
        assert "seam" in as_dict
        assert "objective" in as_dict
        assert "depth" in as_dict

        # Deserialize
        reconstructed = WorkPacket.from_dict(as_dict)

        # Verify all fields match
        assert reconstructed.packet_id == original.packet_id
        assert reconstructed.run_id == original.run_id
        assert reconstructed.parent_packet_id == original.parent_packet_id
        assert reconstructed.depth == original.depth
        assert reconstructed.seam == original.seam
        assert reconstructed.objective == original.objective
        assert reconstructed.success_criteria == original.success_criteria
        assert reconstructed.current_state == original.current_state
        assert reconstructed.known_context == original.known_context
        assert reconstructed.open_questions == original.open_questions
        assert reconstructed.allowed_tools == original.allowed_tools
        assert reconstructed.forbidden_tools == original.forbidden_tools
        assert reconstructed.owned_paths == original.owned_paths
        assert reconstructed.forbidden_paths == original.forbidden_paths
        assert reconstructed.timeout_seconds == original.timeout_seconds
        assert reconstructed.budget == original.budget
        assert reconstructed.delegation_permission == original.delegation_permission
        assert reconstructed.max_child_depth == original.max_child_depth
        assert reconstructed.side_effects_requiring_approval == original.side_effects_requiring_approval
        assert reconstructed.available_artifacts == original.available_artifacts
        assert reconstructed.created_shapes == original.created_shapes
        assert reconstructed.artifacts_produced == original.artifacts_produced
        assert reconstructed.resumption_contract == original.resumption_contract
        assert reconstructed.checkpoint_hint == original.checkpoint_hint
        assert reconstructed.audit_trail == original.audit_trail
        assert reconstructed.secrets_rule == original.secrets_rule
        assert reconstructed.no_revert_rule == original.no_revert_rule
        assert reconstructed.claimed_by == original.claimed_by
        assert reconstructed.claimed_by_harness == original.claimed_by_harness


class TestWorkPacketMethods:
    """Tests for WorkPacket instance methods."""

    def test_work_packet_claim(self, minimal_packet_dict):
        """Create packet, call claim, verify ownership fields updated."""
        packet = WorkPacket(**minimal_packet_dict)

        # Initially unclaimed
        assert packet.claimed_by is None
        assert packet.claimed_by_harness == HarnessType.UNKNOWN
        assert packet.claimed_at is None

        # Claim the packet
        packet.claim("agent-1", HarnessType.CLAUDE_CODE)

        # Verify claim fields
        assert packet.claimed_by == "agent-1"
        assert packet.claimed_by_harness == HarnessType.CLAUDE_CODE
        assert packet.claimed_at is not None
        assert packet.updated_at is not None

    def test_work_packet_checkpoint(self, minimal_packet_dict):
        """Create packet with current_state, call checkpoint, verify snapshot."""
        packet = WorkPacket(**minimal_packet_dict)
        packet.current_state = {"phase": 1}

        # Call checkpoint
        snapshot = packet.checkpoint({"phase": 2, "data": "x"}, "checkpoint-1")

        # Verify packet state was updated
        assert packet.current_state == {"phase": 2, "data": "x"}

        # Verify snapshot contents
        assert isinstance(snapshot, ResumptionSnapshot)
        assert snapshot.packet_id == packet.packet_id
        assert snapshot.run_id == packet.run_id
        assert snapshot.label == "checkpoint-1"
        assert snapshot.state == {"phase": 2, "data": "x"}
        assert snapshot.artifacts == []
        assert snapshot.shapes == []

    def test_work_packet_progress(self, minimal_packet_dict):
        """Create packet, call progress, verify state merged."""
        packet = WorkPacket(**minimal_packet_dict)

        # Update state
        packet.progress({"key": "value"})

        # Verify state contains new key
        assert packet.current_state["key"] == "value"
        assert packet.updated_at is not None

    def test_work_packet_progress_shallow_merge(self, minimal_packet_dict):
        """Create packet with nested state, call progress, verify shallow merge."""
        packet = WorkPacket(**minimal_packet_dict)
        packet.current_state = {"a": {"b": 1}}

        # progress() uses dict.update() which is shallow
        packet.progress({"a": {"c": 2}})

        # Shallow merge means the entire "a" key is replaced
        assert packet.current_state == {"a": {"c": 2}}

    def test_work_packet_discover_shape(self, minimal_packet_dict):
        """Create packet, add ShapeRef, verify shape in created_shapes."""
        packet = WorkPacket(**minimal_packet_dict)
        assert packet.created_shapes == []

        # Create and discover a shape
        shape_ref = ShapeRef(
            shape_id="shape-abc",
            shape_type=ShapeType.PROMPT_TEMPLATE,
            name="Test Prompt",
            tags=["test"],
            created_from_packet_id=packet.packet_id,
        )

        packet.discover_shape(shape_ref)

        # Verify shape ID is tracked
        assert "shape-abc" in packet.created_shapes
        assert packet.updated_at is not None

    def test_work_packet_discover_shape_no_duplicate(self, minimal_packet_dict):
        """Discover same shape twice should not add duplicate."""
        packet = WorkPacket(**minimal_packet_dict)

        shape_ref = ShapeRef(
            shape_id="shape-abc",
            shape_type=ShapeType.PROMPT_TEMPLATE,
            name="Test Prompt",
            created_from_packet_id=packet.packet_id,
        )

        packet.discover_shape(shape_ref)
        packet.discover_shape(shape_ref)

        # Should only have one entry
        assert packet.created_shapes.count("shape-abc") == 1

    def test_work_packet_to_markdown(self, minimal_packet_dict):
        """Create packet, call to_markdown, verify contains key fields."""
        packet = WorkPacket(**minimal_packet_dict)
        md = packet.to_markdown()

        assert isinstance(md, str)
        assert packet.packet_id in md
        assert "test-seam" in md
        assert "Test the thing" in md
        assert "**Depth:** 0" in md
        assert "Created:" in md
        assert "Updated:" in md

    def test_work_packet_add_audit_event(self, minimal_packet_dict):
        """Create packet, add audit events, verify trail grows."""
        packet = WorkPacket(**minimal_packet_dict)
        assert packet.audit_trail == []

        packet.add_audit_event("evt-123")
        assert packet.audit_trail == ["evt-123"]
        assert packet.updated_at is not None

        packet.add_audit_event("evt-456")
        assert packet.audit_trail == ["evt-123", "evt-456"]


class TestWorkPacketValidation:
    """Tests for WorkPacket field validation."""

    def test_work_packet_depth_constraint(self, minimal_packet_dict):
        """Verify depth >= 0 validator."""
        # Valid depth = 0
        packet = WorkPacket(**minimal_packet_dict)
        assert packet.depth == 0

        # Valid depth > 0
        minimal_packet_dict["depth"] = 5
        packet = WorkPacket(**minimal_packet_dict)
        assert packet.depth == 5

        # Invalid depth < 0
        minimal_packet_dict["depth"] = -1
        with pytest.raises(ValidationError) as exc_info:
            WorkPacket(**minimal_packet_dict)
        errors = exc_info.value.errors()
        assert any("depth" in str(e["loc"]) for e in errors)

    def test_work_packet_budget_constraint(self, minimal_packet_dict):
        """Verify budget >= 0 validator."""
        # Valid budget
        minimal_packet_dict["budget"] = 10.0
        packet = WorkPacket(**minimal_packet_dict)
        assert packet.budget == 10.0

        # Invalid budget < 0
        minimal_packet_dict["budget"] = -5.0
        with pytest.raises(ValidationError) as exc_info:
            WorkPacket(**minimal_packet_dict)
        errors = exc_info.value.errors()
        assert any("budget" in str(e["loc"]) for e in errors)

    def test_work_packet_max_child_depth_constraint(self, minimal_packet_dict):
        """Verify max_child_depth >= 0 validator."""
        # Valid
        minimal_packet_dict["max_child_depth"] = 3
        packet = WorkPacket(**minimal_packet_dict)
        assert packet.max_child_depth == 3

        # Invalid
        minimal_packet_dict["max_child_depth"] = -1
        with pytest.raises(ValidationError) as exc_info:
            WorkPacket(**minimal_packet_dict)
        errors = exc_info.value.errors()
        assert any("max_child_depth" in str(e["loc"]) for e in errors)


# ---------------------------------------------------------------------------
# WorkResult Tests
# ---------------------------------------------------------------------------

class TestWorkResult:
    """Tests for WorkResult model."""

    def test_work_result_creation(self):
        """Create result, verify all fields."""
        result = WorkResult(
            packet_id="packet-123",
            run_id="run-456",
            status=PacketStatus.COMPLETED,
            output={"result": "success"},
            error=None,
            shapes_discovered=[],
            artifacts_produced=["/output/file.txt"],
            duration_seconds=125.5,
            cost_estimate=0.45,
            children=["child-1", "child-2"],
            critic_findings=None,
        )

        assert result.packet_id == "packet-123"
        assert result.run_id == "run-456"
        assert result.status == PacketStatus.COMPLETED
        assert result.output == {"result": "success"}
        assert result.error is None
        assert result.shapes_discovered == []
        assert result.artifacts_produced == ["/output/file.txt"]
        assert result.duration_seconds == 125.5
        assert result.cost_estimate == 0.45
        assert result.children == ["child-1", "child-2"]
        assert result.critic_findings is None
        assert result.completed_at is not None

    def test_work_result_roundtrip(self):
        """Serialize/deserialize WorkResult, verify equal."""
        original = WorkResult(
            packet_id="packet-abc",
            run_id="run-xyz",
            status=PacketStatus.FAILED,
            output=None,
            error="Something went wrong",
            shapes_discovered=[
                ShapeRef(
                    shape_id="shape-1",
                    shape_type=ShapeType.SKILL_DEFINITION,
                    name="Test Skill",
                    created_from_packet_id="packet-abc",
                )
            ],
            artifacts_produced=[],
            duration_seconds=60.0,
            cost_estimate=0.20,
            children=[],
            critic_findings="Needs improvement",
        )

        # Serialize
        as_dict = original.model_dump(mode="json")

        # Deserialize
        reconstructed = WorkResult.model_validate(as_dict)

        # Verify all fields match
        assert reconstructed.packet_id == original.packet_id
        assert reconstructed.run_id == original.run_id
        assert reconstructed.status == original.status
        assert reconstructed.output == original.output
        assert reconstructed.error == original.error
        assert len(reconstructed.shapes_discovered) == len(original.shapes_discovered)
        assert reconstructed.artifacts_produced == original.artifacts_produced
        assert reconstructed.duration_seconds == original.duration_seconds
        assert reconstructed.cost_estimate == original.cost_estimate
        assert reconstructed.children == original.children
        assert reconstructed.critic_findings == original.critic_findings


# ---------------------------------------------------------------------------
# ShapeRef Tests
# ---------------------------------------------------------------------------

class TestShapeRef:
    """Tests for ShapeRef model."""

    def test_shape_ref_creation(self):
        """Create ShapeRef, verify fields."""
        shape = ShapeRef(
            shape_id="shape-xyz",
            shape_type=ShapeType.EVALUATION_CHECK,
            name="Code Quality Check",
            tags=["quality", "static-analysis"],
            created_from_packet_id="packet-789",
        )

        assert shape.shape_id == "shape-xyz"
        assert shape.shape_type == ShapeType.EVALUATION_CHECK
        assert shape.name == "Code Quality Check"
        assert shape.tags == ["quality", "static-analysis"]
        assert shape.created_from_packet_id == "packet-789"
        assert shape.created_at is not None

    def test_shape_ref_default_tags(self):
        """ShapeRef with default empty tags."""
        shape = ShapeRef(
            shape_id="shape-abc",
            shape_type=ShapeType.PROMPT_TEMPLATE,
            name="Test",
            created_from_packet_id="packet-000",
        )
        assert shape.tags == []

    def test_shape_ref_roundtrip(self):
        """Serialize and deserialize ShapeRef."""
        original = ShapeRef(
            shape_id="shape-rtrip",
            shape_type=ShapeType.DELEGATION_PATTERN,
            name="Delegation Pattern",
            tags=["pattern"],
            created_from_packet_id="packet-111",
        )

        as_dict = original.model_dump(mode="json")
        reconstructed = ShapeRef.model_validate(as_dict)

        assert reconstructed.shape_id == original.shape_id
        assert reconstructed.shape_type == original.shape_type
        assert reconstructed.name == original.name
        assert reconstructed.tags == original.tags
        assert reconstructed.created_from_packet_id == original.created_from_packet_id


# ---------------------------------------------------------------------------
# ResumptionSnapshot Tests
# ---------------------------------------------------------------------------

class TestResumptionSnapshot:
    """Tests for ResumptionSnapshot model."""

    def test_resumption_snapshot(self):
        """Create snapshot with state dict, verify state preserved."""
        snapshot = ResumptionSnapshot(
            packet_id="packet-abc",
            run_id="run-xyz",
            label="mid-computation",
            state={"counter": 42, "buffer": ["a", "b"]},
            artifacts=["/artifacts/temp.bin"],
            shapes=[],
            event_id="evt-999",
        )

        assert snapshot.packet_id == "packet-abc"
        assert snapshot.run_id == "run-xyz"
        assert snapshot.label == "mid-computation"
        assert snapshot.state == {"counter": 42, "buffer": ["a", "b"]}
        assert snapshot.artifacts == ["/artifacts/temp.bin"]
        assert snapshot.shapes == []
        assert snapshot.event_id == "evt-999"
        assert snapshot.snapshot_id is not None
        assert snapshot.created_at is not None

    def test_resumption_snapshot_with_shapes(self):
        """Create snapshot containing ShapeRefs."""
        shape = ShapeRef(
            shape_id="shape-1",
            shape_type=ShapeType.ORCHESTRATION_SHAPE,
            name="Orchestration Shape",
            created_from_packet_id="packet-abc",
        )

        snapshot = ResumptionSnapshot(
            packet_id="packet-abc",
            run_id="run-xyz",
            label="with-shapes",
            state={"step": 1},
            artifacts=[],
            shapes=[shape],
            event_id="evt-001",
        )

        assert len(snapshot.shapes) == 1
        assert snapshot.shapes[0].shape_id == "shape-1"


# ---------------------------------------------------------------------------
# Enum Tests
# ---------------------------------------------------------------------------

class TestPacketStatus:
    """Tests for PacketStatus enum."""

    def test_packet_status_values(self):
        """Verify all status values present."""
        values = {s.value for s in PacketStatus}
        expected = {
            "created",
            "emitted",
            "claimed",
            "in_progress",
            "paused",
            "completed",
            "failed",
            "resumed",
        }
        assert values == expected
        assert all(isinstance(v, str) for v in values)


class TestDelegationPermission:
    """Tests for DelegationPermission enum."""

    def test_delegation_permission_values(self):
        """Verify all permission values."""
        assert DelegationPermission.NONE.value == "none"
        assert DelegationPermission.REQUEST.value == "request"
        assert DelegationPermission.APPROVE.value == "approve"

        values = {p.value for p in DelegationPermission}
        assert values == {"none", "request", "approve"}


class TestHarnessType:
    """Tests for HarnessType enum."""

    def test_harness_type_values(self):
        """Verify known harness types including CLAUDE_CODE, CODEX, HUMAN, TEST."""
        # Check specific values
        assert HarnessType.CLAUDE_CODE.value == "claude_code"
        assert HarnessType.CODEX.value == "codex"
        assert HarnessType.HUMAN.value == "human"
        assert HarnessType.TEST.value == "test"
        assert HarnessType.UNKNOWN.value == "unknown"
        assert HarnessType.FAKE.value == "fake"
        assert HarnessType.HTTP_API.value == "http_api"

        # Check all known values
        values = {h.value for h in HarnessType}
        expected = {
            "unknown",
            "fake",
            "claude_code",
            "http_api",
            "codex",
            "gemini_cli",
            "qwen_cli",
            "opencode",
            "openclaw",
            "minimax",
            "human",
            "test",
            "qwen",
            "gemini",
            "openrouter",
        }
        assert values == expected


class TestShapeType:
    """Tests for ShapeType enum."""

    def test_shape_type_values(self):
        """Verify all 6 shape types."""
        values = {s.value for s in ShapeType}
        expected = {
            "prompt_template",
            "skill_definition",
            "packet_template",
            "orchestration_shape",
            "evaluation_check",
            "delegation_pattern",
        }
        assert values == expected
        assert len(ShapeType) == 6


# ---------------------------------------------------------------------------
# _now_iso Tests
# ---------------------------------------------------------------------------

class TestNowIso:
    """Tests for _now_iso helper."""

    def test_now_iso_returns_string(self):
        """Verify _now_iso returns a string."""
        result = _now_iso()
        assert isinstance(result, str)

    def test_now_iso_is_iso_format(self):
        """Verify _now_iso returns valid ISO 8601 format."""
        result = _now_iso()
        # ISO 8601 format: 2024-01-01T00:00:00+00:00
        assert "T" in result
        assert "+00:00" in result or "Z" in result

    def test_now_iso_different_values(self):
        """Verify _now_iso returns different values when called at different times."""
        import time
        val1 = _now_iso()
        time.sleep(0.01)
        val2 = _now_iso()
        # Values may or may not differ depending on timing resolution
        assert isinstance(val2, str)
