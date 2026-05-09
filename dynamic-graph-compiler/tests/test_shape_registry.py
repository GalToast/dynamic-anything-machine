"""
Comprehensive tests for ShapeRegistry, Shape, ShapeBuilder, and get_example_shapes.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Import from the dynamic-graph-compiler package
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from shape_registry import (
    ShapeRegistry,
    Shape,
    ShapeBuilder,
    ShapeType,
    get_example_shapes,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def shapes_dir(tmp_path):
    """Return a temporary directory for shape storage."""
    return tmp_path / "shapes"


@pytest.fixture
def registry(shapes_dir):
    """Return a ShapeRegistry instance with a temporary directory."""
    return ShapeRegistry(shapes_dir=shapes_dir)


@pytest.fixture
def sample_shape():
    """Return a basic Shape instance for testing."""
    return Shape(
        shape_id="test-shape-001",
        shape_type=ShapeType.PROMPT_TEMPLATE,
        name="Test Shape",
        description="A shape for testing",
        tags=["test", "sample"],
        seam="testing",
        payload={"key": "value"},
        usage_count=0,
    )


# ============================================================================
# Shape Model Tests
# ============================================================================

class TestShapeModel:
    """Tests for the Shape dataclass model."""

    def test_shape_to_dict(self):
        """Test Shape serialization to dictionary."""
        shape = Shape(
            shape_id="test-123",
            shape_type=ShapeType.PROMPT_TEMPLATE,
            name="My Shape",
            description="Test description",
            tags=["tag1", "tag2"],
            seam="test-seam",
            payload={"foo": "bar"},
            usage_count=5,
        )
        d = shape.to_dict()
        assert d["shape_id"] == "test-123"
        assert d["shape_type"] == "prompt_template"
        assert d["name"] == "My Shape"
        assert d["description"] == "Test description"
        assert d["tags"] == ["tag1", "tag2"]
        assert d["seam"] == "test-seam"
        assert d["payload"] == {"foo": "bar"}
        assert d["usage_count"] == 5

    def test_shape_from_dict(self):
        """Test Shape deserialization from dictionary."""
        data = {
            "shape_id": "test-456",
            "shape_type": "skill_definition",
            "name": "Skill Shape",
            "description": "A skill",
            "tags": ["skill"],
            "seam": "analysis",
            "payload": {"tools": ["grep", "read"]},
            "usage_count": 3,
            "created_at": "2024-01-01T00:00:00+00:00",
        }
        shape = Shape.from_dict(data)
        assert shape.shape_id == "test-456"
        assert shape.shape_type == ShapeType.SKILL_DEFINITION
        assert shape.name == "Skill Shape"
        assert shape.description == "A skill"
        assert shape.tags == ["skill"]
        assert shape.seam == "analysis"
        assert shape.payload == {"tools": ["grep", "read"]}
        assert shape.usage_count == 3

    def test_shape_roundtrip(self):
        """Test Shape serialization and deserialization roundtrip."""
        original = Shape(
            shape_id="roundtrip-789",
            shape_type=ShapeType.PACKET_TEMPLATE,
            name="Roundtrip Test",
            description="Testing roundtrip",
            tags=["test", "roundtrip"],
            seam="data-processing",
            payload={"slots": ["input", "output"]},
            usage_count=10,
        )
        restored = Shape.from_dict(original.to_dict())
        assert restored.shape_id == original.shape_id
        assert restored.shape_type == original.shape_type
        assert restored.name == original.name
        assert restored.description == original.description
        assert restored.tags == original.tags
        assert restored.seam == original.seam
        assert restored.payload == original.payload
        assert restored.usage_count == original.usage_count


# ============================================================================
# ShapeRegistry CRUD Tests
# ============================================================================

class TestShapeRegistryCRUD:
    """Tests for ShapeRegistry basic CRUD operations."""

    def test_save_and_get_shape(self, registry, sample_shape):
        """Test saving a shape and retrieving it."""
        shape_id = registry.save_shape(sample_shape)
        assert shape_id == sample_shape.shape_id

        retrieved = registry.get_shape(shape_id)
        assert retrieved is not None
        assert retrieved.shape_id == sample_shape.shape_id
        assert retrieved.name == sample_shape.name
        assert retrieved.shape_type == sample_shape.shape_type

    def test_save_shape_creates_file(self, registry, sample_shape):
        """Test that saving a shape creates a JSON file."""
        shape_id = registry.save_shape(sample_shape)
        expected_path = registry.shapes_dir / f"{shape_id}.json"
        assert expected_path.exists()

        with open(expected_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["shape_id"] == shape_id
        assert data["name"] == sample_shape.name

    def test_get_shape_not_found(self, registry):
        """Test that getting a non-existent shape returns None."""
        result = registry.get_shape("non-existent-id")
        assert result is None

    def test_update_usage(self, registry, sample_shape):
        """Test updating usage count and last_used_at."""
        shape_id = registry.save_shape(sample_shape)
        assert sample_shape.usage_count == 0
        assert sample_shape.last_used_at is None

        registry.update_usage(shape_id)

        updated = registry.get_shape(shape_id)
        assert updated is not None
        assert updated.usage_count == 1
        assert updated.last_used_at is not None

        # Update again
        registry.update_usage(shape_id)
        updated2 = registry.get_shape(shape_id)
        assert updated2.usage_count == 2

    def test_delete_shape(self, registry, sample_shape):
        """Test deleting a shape."""
        shape_id = registry.save_shape(sample_shape)
        assert registry.get_shape(shape_id) is not None

        result = registry.delete_shape(shape_id)
        assert result is True
        assert registry.get_shape(shape_id) is None

    def test_delete_shape_not_found(self, registry):
        """Test deleting a non-existent shape returns False."""
        result = registry.delete_shape("non-existent-id")
        assert result is False


# ============================================================================
# ShapeRegistry Search Tests
# ============================================================================

class TestShapeRegistrySearch:
    """Tests for ShapeRegistry search operations."""

    def test_find_shapes_by_tag(self, registry):
        """Test finding shapes by tag."""
        shapes = [
            Shape(shape_id="tag-1", shape_type=ShapeType.PROMPT_TEMPLATE, name="Shape 1", tags=["research", "analysis"]),
            Shape(shape_id="tag-2", shape_type=ShapeType.SKILL_DEFINITION, name="Shape 2", tags=["research", "coding"]),
            Shape(shape_id="tag-3", shape_type=ShapeType.PROMPT_TEMPLATE, name="Shape 3", tags=["data", "analysis"]),
        ]
        for s in shapes:
            registry.save_shape(s)

        results = registry.find_by_tag("research")
        assert len(results) == 2
        # Results should be sorted by usage_count descending
        assert {r.shape_id for r in results} == {"tag-1", "tag-2"}

    def test_find_shapes_by_seam(self, registry):
        """Test finding shapes by seam."""
        shapes = [
            Shape(shape_id="seam-1", shape_type=ShapeType.PROMPT_TEMPLATE, name="Shape 1", seam="analysis"),
            Shape(shape_id="seam-2", shape_type=ShapeType.SKILL_DEFINITION, name="Shape 2", seam="analysis"),
            Shape(shape_id="seam-3", shape_type=ShapeType.PROMPT_TEMPLATE, name="Shape 3", seam="retrieval"),
        ]
        for s in shapes:
            registry.save_shape(s)

        results = registry.find_by_seam("analysis")
        assert len(results) == 2
        assert {r.shape_id for r in results} == {"seam-1", "seam-2"}

    def test_find_shapes_by_type(self, registry):
        """Test finding shapes by type."""
        shapes = [
            Shape(shape_id="type-1", shape_type=ShapeType.PROMPT_TEMPLATE, name="Template 1"),
            Shape(shape_id="type-2", shape_type=ShapeType.SKILL_DEFINITION, name="Skill 1"),
            Shape(shape_id="type-3", shape_type=ShapeType.PROMPT_TEMPLATE, name="Template 2"),
        ]
        for s in shapes:
            registry.save_shape(s)

        results = registry.find_shapes(shape_type=ShapeType.PROMPT_TEMPLATE)
        assert len(results) == 2
        assert {r.shape_id for r in results} == {"type-1", "type-3"}

    def test_find_shapes_by_query(self, registry):
        """Test finding shapes by text query."""
        shapes = [
            Shape(shape_id="query-1", shape_type=ShapeType.PROMPT_TEMPLATE, name="Texas Research", description="Research in TX"),
            Shape(shape_id="query-2", shape_type=ShapeType.PROMPT_TEMPLATE, name="California Research", description="Research in CA"),
            Shape(shape_id="query-3", shape_type=ShapeType.PROMPT_TEMPLATE, name="NY Data", description="Data in NY"),
        ]
        for s in shapes:
            registry.save_shape(s)

        results = registry.find_shapes(query="research")
        assert len(results) == 2
        assert {r.shape_id for r in results} == {"query-1", "query-2"}

    def test_find_shapes_query_case_insensitive(self, registry):
        """Test that query search is case insensitive."""
        shapes = [
            Shape(shape_id="case-1", shape_type=ShapeType.PROMPT_TEMPLATE, name="UPPER CASE"),
            Shape(shape_id="case-2", shape_type=ShapeType.PROMPT_TEMPLATE, name="lower case"),
        ]
        for s in shapes:
            registry.save_shape(s)

        results = registry.find_shapes(query="Upper")
        assert len(results) == 1
        assert results[0].shape_id == "case-1"

    def test_find_shapes_multi_criteria(self, registry):
        """Test finding shapes with multiple criteria (AND logic)."""
        shapes = [
            Shape(shape_id="multi-1", shape_type=ShapeType.SKILL_DEFINITION, name="S1", tags=["research"], seam="analysis"),
            Shape(shape_id="multi-2", shape_type=ShapeType.SKILL_DEFINITION, name="S2", tags=["research"], seam="retrieval"),
            Shape(shape_id="multi-3", shape_type=ShapeType.PROMPT_TEMPLATE, name="S3", tags=["data"], seam="analysis"),
            Shape(shape_id="multi-4", shape_type=ShapeType.SKILL_DEFINITION, name="S4", tags=["research"], seam="analysis"),
        ]
        for s in shapes:
            registry.save_shape(s)

        # Find shapes with tag="research" AND seam="analysis"
        results = registry.find_shapes(tags=["research"], seam="analysis")
        assert len(results) == 2
        assert {r.shape_id for r in results} == {"multi-1", "multi-4"}

    def test_find_by_tag_not_found(self, registry):
        """Test searching for non-existent tag returns empty list."""
        results = registry.find_by_tag("non-existent-tag")
        assert results == []

    def test_find_by_seam_not_found(self, registry):
        """Test searching for non-existent seam returns empty list."""
        results = registry.find_by_seam("non-existent-seam")
        assert results == []


# ============================================================================
# ShapeRegistry Utility Tests
# ============================================================================

class TestShapeRegistryUtility:
    """Tests for ShapeRegistry utility methods."""

    def test_most_used(self, registry):
        """Test getting most used shapes."""
        shapes = [
            Shape(shape_id="used-1", shape_type=ShapeType.PROMPT_TEMPLATE, name="S1", usage_count=1),
            Shape(shape_id="used-3", shape_type=ShapeType.PROMPT_TEMPLATE, name="S3", usage_count=3),
            Shape(shape_id="used-2", shape_type=ShapeType.PROMPT_TEMPLATE, name="S2", usage_count=2),
            Shape(shape_id="used-5", shape_type=ShapeType.PROMPT_TEMPLATE, name="S5", usage_count=5),
            Shape(shape_id="used-4", shape_type=ShapeType.PROMPT_TEMPLATE, name="S4", usage_count=4),
        ]
        for s in shapes:
            registry.save_shape(s)

        results = registry.most_used(limit=3)
        assert len(results) == 3
        # Should be sorted by usage_count descending
        assert results[0].usage_count == 5
        assert results[1].usage_count == 4
        assert results[2].usage_count == 3

    def test_recent(self, registry):
        """Test getting most recently created shapes."""
        now = datetime.now(timezone.utc)
        shapes = [
            Shape(shape_id="recent-1", shape_type=ShapeType.PROMPT_TEMPLATE, name="Old", created_at="2024-01-01T00:00:00+00:00"),
            Shape(shape_id="recent-2", shape_type=ShapeType.PROMPT_TEMPLATE, name="New", created_at="2024-06-01T00:00:00+00:00"),
            Shape(shape_id="recent-3", shape_type=ShapeType.PROMPT_TEMPLATE, name="Middle", created_at="2024-03-01T00:00:00+00:00"),
        ]
        for s in shapes:
            registry.save_shape(s)

        results = registry.recent(limit=2)
        assert len(results) == 2
        # Should be sorted by created_at descending (newest first)
        assert results[0].shape_id == "recent-2"
        assert results[1].shape_id == "recent-3"

    def test_list_shapes(self, registry):
        """Test listing shape summaries."""
        shapes = [
            Shape(shape_id="list-1", shape_type=ShapeType.PROMPT_TEMPLATE, name="Shape 1", tags=["test"], usage_count=5),
            Shape(shape_id="list-2", shape_type=ShapeType.SKILL_DEFINITION, name="Shape 2", tags=["data"], usage_count=3),
            Shape(shape_id="list-3", shape_type=ShapeType.PACKET_TEMPLATE, name="Shape 3", tags=["test", "data"], usage_count=1),
        ]
        for s in shapes:
            registry.save_shape(s)

        results = registry.list_shapes()
        assert len(results) == 3

        # Verify summary format
        for summary in results:
            assert "shape_id" in summary
            assert "name" in summary
            assert "shape_type" in summary
            assert "tags" in summary
            assert "usage_count" in summary
            assert "created_at" in summary

    def test_count(self, registry):
        """Test counting shapes."""
        assert registry.count() == 0

        shapes = [
            Shape(shape_id="count-1", shape_type=ShapeType.PROMPT_TEMPLATE, name="S1"),
            Shape(shape_id="count-2", shape_type=ShapeType.PROMPT_TEMPLATE, name="S2"),
        ]
        for s in shapes:
            registry.save_shape(s)

        assert registry.count() == 2


# ============================================================================
# ShapeRegistry Export/Import Tests
# ============================================================================

class TestShapeRegistryExportImport:
    """Tests for ShapeRegistry export and import operations."""

    def test_export_shape(self, registry, sample_shape):
        """Test exporting a shape as dictionary."""
        shape_id = registry.save_shape(sample_shape)
        exported = registry.export_shape(shape_id)

        assert exported["shape_id"] == shape_id
        assert exported["name"] == sample_shape.name
        assert exported["shape_type"] == sample_shape.shape_type.value

    def test_export_shape_not_found(self, registry):
        """Test exporting non-existent shape raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            registry.export_shape("non-existent")
        assert "not found" in str(exc_info.value).lower()

    def test_import_shape(self, registry, sample_shape):
        """Test importing a shape creates a new shape with new ID."""
        original_id = registry.save_shape(sample_shape)
        exported = registry.export_shape(original_id)

        new_id = registry.import_shape(exported)
        assert new_id != original_id

        imported = registry.get_shape(new_id)
        assert imported is not None
        assert imported.name == sample_shape.name
        assert imported.shape_type == sample_shape.shape_type

    def test_import_shape_preserves_data(self, registry):
        """Test that imported shape preserves all data fields."""
        original = Shape(
            shape_id="orig-999",
            shape_type=ShapeType.SKILL_DEFINITION,
            name="Original Skill",
            description="A skill for testing",
            tags=["import", "test"],
            seam="import-testing",
            payload={"tools": ["read", "write"]},
        )
        registry.save_shape(original)
        exported = registry.export_shape("orig-999")

        new_id = registry.import_shape(exported)
        imported = registry.get_shape(new_id)

        assert imported.name == original.name
        assert imported.description == original.description
        assert imported.tags == original.tags
        assert imported.seam == original.seam
        assert imported.payload == original.payload
        assert imported.shape_type == original.shape_type


# ============================================================================
# ShapeBuilder Tests
# ============================================================================

class TestShapeBuilder:
    """Tests for ShapeBuilder factory methods."""

    def test_from_packet_execution(self, registry):
        """Test creating a skill definition shape from packet execution data."""
        shape = ShapeBuilder.from_packet_execution(
            packet_id="packet-abc123",
            run_id="run-xyz",
            seam="file-analysis",
            tools_used=["read_file", "grep", "find"],
            prompt="Analyze the codebase for security issues",
            result={"constraints": {"max_file_size": "100KB", "read_only": True}},
        )

        assert shape.shape_type == ShapeType.SKILL_DEFINITION
        assert shape.name.startswith("Skill from packet")
        assert "packet-a" in shape.name  # packet_id[:8] = "packet-a"
        assert shape.seam == "file-analysis"
        assert shape.tags == ["discovered", "file-analysis", "skill"]
        assert shape.payload["tools"] == ["read_file", "grep", "find"]
        assert shape.payload["prompt"] == "Analyze the codebase for security issues"
        assert shape.payload["constraints"] == {"max_file_size": "100KB", "read_only": True}

    def test_prompt_template_from_text(self):
        """Test creating a prompt template shape from text."""
        template_text = "Extract {entities} from {source} and output as {format}"
        slots = ["entities", "source", "format"]

        shape = ShapeBuilder.prompt_template_from_text(
            name="Entity Extraction",
            description="Extracts structured entities from text",
            text=template_text,
            slots=slots,
        )

        assert shape.shape_type == ShapeType.PROMPT_TEMPLATE
        assert shape.name == "Entity Extraction"
        assert shape.description == "Extracts structured entities from text"
        assert shape.payload["template"] == template_text
        assert shape.payload["slots"] == slots
        assert "entities" in shape.payload["variables"]
        assert "source" in shape.payload["variables"]
        assert "format" in shape.payload["variables"]

    def test_packet_template_from_seam(self):
        """Test creating a packet template shape from a packet-like object."""
        # Create a minimal mock packet object
        class MockPacket:
            def __init__(self):
                self.objective = "Process {input_file} and output {output_file}"
                self.verification_expectations = "Output file exists and is valid JSON"
                self.allowed_tools = ["read_file", "write_file"]
                self.timeout = 600
                self.worker_id = "test-worker"

        packet = MockPacket()
        shape = ShapeBuilder.packet_template_from_seam(
            seam="data-processing",
            packet=packet,
        )

        assert shape.shape_type == ShapeType.PACKET_TEMPLATE
        assert shape.name == "Packet template for data-processing"
        assert shape.seam == "data-processing"
        assert shape.payload["seam"] == "data-processing"
        assert shape.payload["objective_template"] == "Process {input_file} and output {output_file}"
        assert shape.payload["slots"] == ["input_file", "output_file"]
        assert shape.payload["default_constraints"]["allowed_tools"] == ["read_file", "write_file"]
        assert shape.payload["default_constraints"]["timeout"] == 600


# ============================================================================
# Example Shapes Test
# ============================================================================

class TestExampleShapes:
    """Tests for the get_example_shapes function."""

    def test_get_example_shapes(self):
        """Test that get_example_shapes returns 5 shapes."""
        shapes = get_example_shapes()
        assert len(shapes) == 5

    def test_example_shapes_have_required_fields(self):
        """Test that all example shapes have required fields."""
        shapes = get_example_shapes()
        required_fields = ["shape_id", "shape_type", "name", "description", "tags", "payload", "created_at"]

        for shape in shapes:
            for field in required_fields:
                assert hasattr(shape, field), f"Shape missing field: {field}"
                assert getattr(shape, field) is not None, f"Shape field '{field}' is None"

    def test_example_shapes_types(self):
        """Test that example shapes use expected ShapeType values."""
        shapes = get_example_shapes()
        types_found = {s.shape_type for s in shapes}

        # Should include these types among the 5 shapes
        expected_types = {
            ShapeType.ORCHESTRATION_SHAPE,
            ShapeType.SKILL_DEFINITION,
            ShapeType.DELEGATION_PATTERN,
            ShapeType.EVALUATION_CHECK,
            ShapeType.PROMPT_TEMPLATE,
        }
        assert types_found.issubset(expected_types)

    def test_example_shapes_ids_are_unique(self):
        """Test that all example shapes have unique IDs."""
        shapes = get_example_shapes()
        ids = [s.shape_id for s in shapes]
        assert len(ids) == len(set(ids))

    def test_example_orchestration_shape(self):
        """Test the orchestration shape has expected structure."""
        shapes = get_example_shapes()
        orchestration = next((s for s in shapes if s.shape_type == ShapeType.ORCHESTRATION_SHAPE), None)
        assert orchestration is not None
        assert "Read-Research-Summarize" in orchestration.name
        assert "research" in orchestration.tags
        assert "packet_templates" in orchestration.payload

    def test_example_skill_shape(self):
        """Test the skill definition shape has expected structure."""
        shapes = get_example_shapes()
        skill = next((s for s in shapes if s.shape_type == ShapeType.SKILL_DEFINITION), None)
        assert skill is not None
        assert "code-review" in skill.seam or "Security" in skill.name
        assert "tools" in skill.payload

    def test_example_prompt_template_shape(self):
        """Test the prompt template shape has expected structure."""
        shapes = get_example_shapes()
        prompt = next((s for s in shapes if s.shape_type == ShapeType.PROMPT_TEMPLATE), None)
        assert prompt is not None
        assert "template" in prompt.payload
        assert "slots" in prompt.payload