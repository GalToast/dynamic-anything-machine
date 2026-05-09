"""
Shape Registry for Recursive Work-Packet Runtime.

Stores reusable shapes discovered during packet execution. Shapes are
file-based under shapes/ directory, each shape as a separate JSON file
named <shape_id>.json.

Imports:
    - work_packet: ShapeType, ShapeRef
    - work_ledger: WorkLedger
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import aiofiles

# Import types from work_packet (expected to be defined elsewhere)
try:
    from work_packet import ShapeType, ShapeRef
except ImportError:
    # Stub types if module not yet available
    class ShapeType(str, Enum):
        PROMPT_TEMPLATE = "prompt_template"
        SKILL_DEFINITION = "skill_definition"
        PACKET_TEMPLATE = "packet_template"
        ORCHESTRATION_SHAPE = "orchestration_shape"
        EVALUATION_CHECK = "evaluation_check"
        DELEGATION_PATTERN = "delegation_pattern"

    @dataclass
    class ShapeRef:
        shape_id: str
        shape_type: ShapeType

# Import WorkLedger (expected to be defined elsewhere)
try:
    from work_ledger import WorkLedger
except ImportError:
    # Stub type if module not yet available
    WorkLedger = Any


# ============================================================================
# Shape Model
# ============================================================================


@dataclass
class Shape:
    """
    A reusable shape discovered during packet execution.

    Attributes:
        shape_id: Unique identifier (UUID)
        shape_type: Type of shape from ShapeType enum
        name: Human-readable name
        description: Optional description
        tags: Searchable tags (e.g. "auth", "file-processing", "api-call")
        seam: Work seam this applies to
        payload: Type-specific data dict
        usage_count: Times this shape has been applied/discovered
        created_at: ISO 8601 timestamp
        created_from_packet_id: Which packet discovered this shape
        created_by_agent: Agent that created this shape
        last_used_at: ISO 8601 timestamp of last use
        metadata: Arbitrary additional metadata
    """

    shape_id: str
    shape_type: ShapeType
    name: str
    description: str | None = None
    tags: list[str] = field(default_factory=list)
    seam: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    usage_count: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    created_from_packet_id: str | None = None
    created_by_agent: str | None = None
    last_used_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize shape to dictionary."""
        return {
            "shape_id": self.shape_id,
            "shape_type": self.shape_type.value if isinstance(self.shape_type, Enum) else self.shape_type,
            "name": self.name,
            "description": self.description,
            "tags": self.tags,
            "seam": self.seam,
            "payload": self.payload,
            "usage_count": self.usage_count,
            "created_at": self.created_at,
            "created_from_packet_id": self.created_from_packet_id,
            "created_by_agent": self.created_by_agent,
            "last_used_at": self.last_used_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Shape:
        """Deserialize shape from dictionary."""
        shape_type = data.get("shape_type")
        if isinstance(shape_type, str):
            shape_type = ShapeType(shape_type)

        return cls(
            shape_id=data["shape_id"],
            shape_type=shape_type,
            name=data["name"],
            description=data.get("description"),
            tags=data.get("tags", []),
            seam=data.get("seam"),
            payload=data.get("payload", {}),
            usage_count=data.get("usage_count", 0),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
            created_from_packet_id=data.get("created_from_packet_id"),
            created_by_agent=data.get("created_by_agent"),
            last_used_at=data.get("last_used_at"),
            metadata=data.get("metadata", {}),
        )


# ============================================================================
# ShapeRegistry Class
# ============================================================================


class ShapeRegistry:
    """
    File-based registry for reusable shapes.

    Shapes are stored as individual JSON files under shapes/ directory.
    Provides synchronous and async operations for shape management.
    """

    def __init__(
        self,
        shapes_dir: Path | str | None = None,
        ledger: WorkLedger | None = None,
    ) -> None:
        """
        Initialize the shape registry.

        Args:
            shapes_dir: Directory to store shape JSON files. Defaults to "shapes".
            ledger: Optional work ledger for audit integration.
        """
        self.shapes_dir = Path(shapes_dir) if shapes_dir else Path("shapes")
        self.ledger = ledger
        self._ensure_shapes_dir()

    def _ensure_shapes_dir(self) -> None:
        """Ensure shapes directory exists."""
        self.shapes_dir.mkdir(parents=True, exist_ok=True)

    def _shape_path(self, shape_id: str) -> Path:
        """Get the file path for a shape."""
        return self.shapes_dir / f"{shape_id}.json"

    def _now_iso(self) -> str:
        """Get current UTC timestamp in ISO 8601 format."""
        return datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------------
    # Core CRUD Operations
    # ------------------------------------------------------------------------

    def save_shape(self, shape: Shape) -> str:
        """
        Save a shape to disk.

        Args:
            shape: The Shape instance to save.

        Returns:
            The shape_id of the saved shape.
        """
        shape_id = shape.shape_id
        if not shape_id:
            shape_id = str(uuid.uuid4())
            shape.shape_id = shape_id

        path = self._shape_path(shape_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(shape.to_dict(), f, indent=2)

        return shape_id

    async def save_shape_async(self, shape: Shape) -> str:
        """
        Save a shape to disk asynchronously.

        Args:
            shape: The Shape instance to save.

        Returns:
            The shape_id of the saved shape.
        """
        shape_id = shape.shape_id
        if not shape_id:
            shape_id = str(uuid.uuid4())
            shape.shape_id = shape_id

        path = self._shape_path(shape_id)
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(shape.to_dict(), indent=2))

        return shape_id

    def get_shape(self, shape_id: str) -> Shape | None:
        """
        Retrieve a shape from disk.

        Args:
            shape_id: The UUID of the shape to retrieve.

        Returns:
            Shape instance if found, None otherwise.
        """
        path = self._shape_path(shape_id)
        if not path.exists():
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return Shape.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    async def get_shape_async(self, shape_id: str) -> Shape | None:
        """
        Retrieve a shape from disk asynchronously.

        Args:
            shape_id: The UUID of the shape to retrieve.

        Returns:
            Shape instance if found, None otherwise.
        """
        path = self._shape_path(shape_id)
        if not path.exists():
            return None

        try:
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                content = await f.read()
            data = json.loads(content)
            return Shape.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def update_usage(self, shape_id: str) -> None:
        """
        Increment usage count and update last_used_at timestamp.

        Args:
            shape_id: The UUID of the shape to update.
        """
        shape = self.get_shape(shape_id)
        if shape is None:
            return

        shape.usage_count += 1
        shape.last_used_at = self._now_iso()
        self.save_shape(shape)

    async def update_usage_async(self, shape_id: str) -> None:
        """
        Increment usage count and update last_used_at timestamp asynchronously.

        Args:
            shape_id: The UUID of the shape to update.
        """
        shape = await self.get_shape_async(shape_id)
        if shape is None:
            return

        shape.usage_count += 1
        shape.last_used_at = self._now_iso()
        await self.save_shape_async(shape)

    def delete_shape(self, shape_id: str) -> bool:
        """
        Delete a shape from disk.

        Args:
            shape_id: The UUID of the shape to delete.

        Returns:
            True if deleted, False if not found.
        """
        path = self._shape_path(shape_id)
        if not path.exists():
            return False

        try:
            path.unlink()
            return True
        except OSError:
            return False

    async def delete_shape_async(self, shape_id: str) -> bool:
        """
        Delete a shape from disk asynchronously.

        Args:
            shape_id: The UUID of the shape to delete.

        Returns:
            True if deleted, False if not found.
        """
        path = self._shape_path(shape_id)
        if not path.exists():
            return False

        try:
            await aiofiles.os.remove(path)
            return True
        except OSError:
            return False

    # ------------------------------------------------------------------------
    # Search Operations
    # ------------------------------------------------------------------------

    def find_shapes(
        self,
        tags: list[str] | None = None,
        seam: str | None = None,
        shape_type: ShapeType | None = None,
        query: str | None = None,
        limit: int = 10,
    ) -> list[Shape]:
        """
        Multi-criteria shape search.

        Args:
            tags: Filter by required tags (all must match).
            seam: Filter by work seam.
            shape_type: Filter by shape type.
            query: Text search in name and description.
            limit: Maximum results to return.

        Returns:
            List of matching shapes sorted by usage_count descending.
        """
        shapes = []
        for path in self.shapes_dir.glob("*.json"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                shape = Shape.from_dict(data)
            except (json.JSONDecodeError, KeyError):
                continue

            # Apply filters
            if tags:
                if not all(tag in shape.tags for tag in tags):
                    continue

            if seam is not None and shape.seam != seam:
                continue

            if shape_type is not None and shape.shape_type != shape_type:
                continue

            if query:
                query_lower = query.lower()
                if query_lower not in shape.name.lower():
                    if shape.description is None or query_lower not in shape.description.lower():
                        continue

            shapes.append(shape)

        # Sort by usage_count descending
        shapes.sort(key=lambda s: s.usage_count, reverse=True)
        return shapes[:limit]

    def find_by_tag(self, tag: str) -> list[Shape]:
        """
        Find all shapes with a specific tag.

        Args:
            tag: Tag to search for.

        Returns:
            List of matching shapes sorted by usage_count descending.
        """
        return self.find_shapes(tags=[tag])

    def find_by_seam(self, seam: str) -> list[Shape]:
        """
        Find all shapes for a specific work seam.

        Args:
            seam: Work seam name.

        Returns:
            List of matching shapes sorted by usage_count descending.
        """
        return self.find_shapes(seam=seam)

    def most_used(self, limit: int = 10) -> list[Shape]:
        """
        Get the most used shapes.

        Args:
            limit: Maximum number of shapes to return.

        Returns:
            List of shapes sorted by usage_count descending.
        """
        return self.find_shapes(limit=limit)

    def recent(self, limit: int = 10) -> list[Shape]:
        """
        Get the most recently created shapes.

        Args:
            limit: Maximum number of shapes to return.

        Returns:
            List of shapes sorted by created_at descending.
        """
        shapes = []
        for path in self.shapes_dir.glob("*.json"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                shapes.append(Shape.from_dict(data))
            except (json.JSONDecodeError, KeyError):
                continue

        shapes.sort(key=lambda s: s.created_at, reverse=True)
        return shapes[:limit]

    # ------------------------------------------------------------------------
    # List and Export Operations
    # ------------------------------------------------------------------------

    def list_shapes(self) -> list[dict[str, Any]]:
        """
        Get a summary list of all shapes.

        Returns:
            List of shape summaries with id, name, type, tags, usage, created.
        """
        summaries = []
        for path in self.shapes_dir.glob("*.json"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                summaries.append({
                    "shape_id": data["shape_id"],
                    "name": data["name"],
                    "shape_type": data["shape_type"],
                    "tags": data.get("tags", []),
                    "usage_count": data.get("usage_count", 0),
                    "created_at": data.get("created_at"),
                })
            except (json.JSONDecodeError, KeyError):
                continue

        return summaries

    def export_shape(self, shape_id: str) -> dict[str, Any]:
        """
        Export a full shape as a dict for sharing.

        Args:
            shape_id: The UUID of the shape to export.

        Returns:
            Full shape dictionary ready for export/serialization.
        """
        shape = self.get_shape(shape_id)
        if shape is None:
            raise ValueError(f"Shape not found: {shape_id}")
        return shape.to_dict()

    def import_shape(self, data: dict[str, Any]) -> str:
        """
        Import a shape from a dictionary.

        Creates a new shape with a new UUID even if the imported
        data contained an existing shape_id.

        Args:
            data: Shape dictionary to import.

        Returns:
            The new shape_id assigned during import.
        """
        # Remove old shape_id to generate a new one
        imported_data = {k: v for k, v in data.items() if k != "shape_id"}
        imported_data["shape_id"] = str(uuid.uuid4())

        shape = Shape.from_dict(imported_data)
        return self.save_shape(shape)

    # ------------------------------------------------------------------------
    # Bulk Operations
    # ------------------------------------------------------------------------

    def get_all_shapes(self) -> list[Shape]:
        """
        Get all shapes in the registry.

        Returns:
            List of all shapes sorted by usage_count descending.
        """
        return self.find_shapes(limit=1000)

    def count(self) -> int:
        """
        Get the total number of shapes in the registry.

        Returns:
            Number of shapes stored.
        """
        return len(list(self.shapes_dir.glob("*.json")))


# ============================================================================
# ShapeBuilder Helper
# ============================================================================


class ShapeBuilder:
    """
    Factory for creating shapes from packet execution.

    Provides static methods for extracting and constructing reusable
    shapes from actual packet execution data.
    """

    @staticmethod
    def from_packet_execution(
        packet_id: str,
        run_id: str,
        seam: str,
        tools_used: list[str],
        prompt: str,
        result: Any,
        ledger: WorkLedger | None = None,
    ) -> Shape:
        """
        Create a skill_definition shape from packet execution data.

        Args:
            packet_id: ID of the packet that executed.
            run_id: ID of the run containing the packet.
            seam: Work seam where this skill was discovered.
            tools_used: List of tool names the packet used.
            prompt: The instructional prompt used.
            result: The result/output from execution.
            ledger: Optional work ledger for audit.

        Returns:
            A Shape representing this skill definition.
        """
        # Extract constraints from result if available
        constraints = {}
        if isinstance(result, dict):
            constraints = result.get("constraints", {})

        payload = {
            "tools": tools_used,
            "prompt": prompt,
            "constraints": constraints,
            "budget": 0.0,  # Could be computed from ledger
        }

        return Shape(
            shape_id=str(uuid.uuid4()),
            shape_type=ShapeType.SKILL_DEFINITION,
            name=f"Skill from packet {packet_id[:8]}",
            description=f"Discovered skill on seam '{seam}' using tools {tools_used}",
            tags=["discovered", seam, "skill"],
            seam=seam,
            payload=payload,
            usage_count=0,
            created_at=datetime.now(timezone.utc).isoformat(),
            created_from_packet_id=packet_id,
            created_by_agent=None,  # Would come from ledger context
        )

    @staticmethod
    def prompt_template_from_text(
        name: str,
        description: str,
        text: str,
        slots: list[str],
    ) -> Shape:
        """
        Extract a prompt template from example text.

        Args:
            name: Human-readable name for the template.
            description: Description of what this template does.
            text: Example prompt text with {slot} placeholders.
            slots: List of slot names found in the template.

        Returns:
            A Shape representing this prompt template.
        """
        payload = {
            "template": text,
            "slots": slots,
            "variables": {slot: f"description of {slot}" for slot in slots},
            "model_hint": None,
        }

        return Shape(
            shape_id=str(uuid.uuid4()),
            shape_type=ShapeType.PROMPT_TEMPLATE,
            name=name,
            description=description,
            tags=["prompt", "template"],
            payload=payload,
        )

    @staticmethod
    def packet_template_from_seam(seam: str, packet: Any) -> Shape:
        """
        Create a packet template from a completed packet.

        Args:
            seam: The work seam this packet operated on.
            packet: The packet instance to create template from.

        Returns:
            A Shape representing this packet template.
        """
        # Extract slots from objective if it contains placeholders
        objective = getattr(packet, "objective", "")
        slots = []
        if objective:
            import re
            slots = re.findall(r"\{(\w+)\}", objective)

        payload = {
            "seam": seam,
            "objective_template": objective,
            "success_criteria_template": getattr(packet, "verification_expectations", ""),
            "default_constraints": {
                "allowed_tools": getattr(packet, "allowed_tools", []),
                "timeout": getattr(packet, "timeout", 300),
            },
            "slots": slots,
        }

        return Shape(
            shape_id=str(uuid.uuid4()),
            shape_type=ShapeType.PACKET_TEMPLATE,
            name=f"Packet template for {seam}",
            description=f"Template derived from {getattr(packet, 'worker_id', 'unknown')}",
            tags=["packet", "template", seam],
            seam=seam,
            payload=payload,
        )


# ============================================================================
# Example / Starter Shapes
# ============================================================================


def get_example_shapes() -> list[Shape]:
    """
    Get 5 starter shapes useful for testing the registry.

    Returns:
        List containing:
        1. A "read-research-summarize" orchestration shape
        2. A "code-review" skill definition
        3. A "parallel-splitter" delegation pattern
        4. A "file-exists-check" evaluation check
        5. An "extract-entities" prompt template
    """
    now = datetime.now(timezone.utc).isoformat()

    shapes: list[Shape] = []

    # 1. Read-Research-Summarize orchestration shape
    orchestration_shape = Shape(
        shape_id="example-0001-0000-0000-0001",
        shape_type=ShapeType.ORCHESTRATION_SHAPE,
        name="Read-Research-Summarize Pattern",
        description="Classic three-phase research pattern: gather information, analyze, synthesize",
        tags=["research", "orchestration", "three-phase"],
        payload={
            "description": "Planner-Worker-Critic research pattern",
            "packet_templates": [
                {"seam": "reader", "role": "retriever"},
                {"seam": "researcher", "role": "worker"},
                {"seam": "synthesizer", "role": "integrator"},
            ],
            "edges": [
                {"from": "reader", "to": "researcher", "condition": None},
                {"from": "researcher", "to": "synthesizer", "condition": None},
            ],
            "seam_sequences": [["reader", "researcher", "synthesizer"]],
        },
        created_at=now,
    )
    shapes.append(orchestration_shape)

    # 2. Code review skill definition
    skill_shape = Shape(
        shape_id="example-0002-0000-0000-0002",
        shape_type=ShapeType.SKILL_DEFINITION,
        name="Security Code Reviewer",
        description="Automated security-focused code review skill",
        tags=["security", "code-review", "static-analysis"],
        seam="code-review",
        payload={
            "tools": ["read_file", "grep", "find"],
            "prompt": "You are a security-focused code reviewer. Analyze the provided code for:\n1. SQL injection vulnerabilities\n2. Path traversal risks\n3. Authentication/authorization issues\n4. Secret exposure\n5. Input validation problems",
            "constraints": {
                "max_file_size": "100KB",
                "forbidden_extensions": [".exe", ".dll", ".so"],
                "read_only": True,
            },
            "budget": 0.50,
        },
        created_at=now,
    )
    shapes.append(skill_shape)

    # 3. Parallel splitter delegation pattern
    delegation_shape = Shape(
        shape_id="example-0003-0000-0000-0003",
        shape_type=ShapeType.DELEGATION_PATTERN,
        name="Parallel Seam Splitter",
        description="Splits a work seam into parallel sub-seams for concurrent execution",
        tags=["delegation", "parallel", "split"],
        payload={
            "description": "How to split a seam into parallel sub-seams",
            "parent_seam": "research",
            "child_seams": ["web-search", "document-read", "data-fetch"],
            "split_strategy": "parallel",
            "merge_strategy": "all_pass",
        },
        created_at=now,
    )
    shapes.append(delegation_shape)

    # 4. File exists check evaluation
    eval_shape = Shape(
        shape_id="example-0004-0000-0000-0004",
        shape_type=ShapeType.EVALUATION_CHECK,
        name="File Exists and Validates",
        description="Verifies that a file exists and passes validation",
        tags=["evaluation", "file", "validation"],
        payload={
            "description": "Verify file was written correctly",
            "check_type": "file_exists_and_valid",
            "params": {
                "path_template": "{output_path}",
                "validator": "json",
            },
            "pass_if": "file exists and parses as valid JSON",
        },
        created_at=now,
    )
    shapes.append(eval_shape)

    # 5. Entity extraction prompt template
    prompt_shape = Shape(
        shape_id="example-0005-0000-0000-0005",
        shape_type=ShapeType.PROMPT_TEMPLATE,
        name="Entity Extraction Template",
        description="Template for extracting structured entities from unstructured text",
        tags=["prompt", "template", "extraction", "nlp"],
        payload={
            "template": "Extract {entity_types} from the following text.\n\nText: {source}\n\nInstructions:\n1. Identify all {entity_types}\n2. For each entity, extract its name and attributes\n3. Return results as structured {output_format}",
            "slots": ["entity_types", "source", "output_format"],
            "variables": {
                "entity_types": "comma-separated list of entity types (e.g., 'persons, organizations, dates')",
                "source": "the text content to extract from",
                "output_format": "desired output format (e.g., 'JSON', 'CSV', 'table')",
            },
            "model_hint": "claude-sonnet-4",
        },
        created_at=now,
    )
    shapes.append(prompt_shape)

    return shapes
