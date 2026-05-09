"""
Append-only event ledger for recursive work-packet runtime lifecycle tracking.

Single source of truth for packet lifecycle - no in-memory state trusted over ledger.
All timestamps use UTC.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import aiofiles

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WORK_LEDGER_EVENTS_FILE = os.environ.get("WORK_LEDGER_EVENTS_FILE", "packet-events.jsonl")
GRAPH_EVENTS_FILE = os.environ.get("GRAPH_LEDGER_EVENTS_FILE", "graph-events.jsonl")

# ---------------------------------------------------------------------------
# EventType Constants (plain strings for JSON serialization)
# ---------------------------------------------------------------------------

PACKET_CREATED = "PACKET_CREATED"
PACKET_EMITTED = "PACKET_EMITTED"
PACKET_CLAIMED = "PACKET_CLAIMED"
PACKET_IN_PROGRESS = "PACKET_IN_PROGRESS"
PACKET_PROGRESS = "PACKET_PROGRESS"
PACKET_PAUSED = "PACKET_PAUSED"
PACKET_COMPLETED = "PACKET_COMPLETED"
PACKET_FAILED = "PACKET_FAILED"
PACKET_RESUMED = "PACKET_RESUMED"
CHILD_REQUESTED = "CHILD_REQUESTED"
CHILD_APPROVED = "CHILD_APPROVED"
CHILD_DENIED = "CHILD_DENIED"
HUMAN_GATE_ENTERED = "HUMAN_GATE_ENTERED"
HUMAN_GATE_APPROVED = "HUMAN_GATE_APPROVED"
HUMAN_GATE_DENIED = "HUMAN_GATE_DENIED"
SHAPE_CREATED = "SHAPE_CREATED"
ARTIFACT_WRITTEN = "ARTIFACT_WRITTEN"
CHECKPOINT_CREATED = "CHECKPOINT_CREATED"
CHECKPOINT_RESTORED = "CHECKPOINT_RESTORED"
SECRET_DETECTED = "SECRET_DETECTED"
RUN_COMPLETED = "RUN_COMPLETED"
RUN_FAILED = "RUN_FAILED"
ERROR = "ERROR"

# Legacy event type aliases (for cross-read from graph-events.jsonl)
_RUN_CREATED = "RUN_CREATED"
_CHILD_REJECTED = "CHILD_REJECTED"

# ---------------------------------------------------------------------------
# AuditEvent Model
# ---------------------------------------------------------------------------


@dataclass
class AuditEvent:
    event_id: str
    run_id: str
    packet_id: str | None
    timestamp: str
    event_type: str
    agent_id: str | None
    harness: str | None
    depth: int | None
    data: dict[str, Any]
    masked_fields: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AuditEvent:
        return cls(**d)


# ---------------------------------------------------------------------------
# SecretMasker
# ---------------------------------------------------------------------------


class SecretMasker:
    """Detects and redacts API keys, tokens, passwords, bearer tokens, AWS keys, etc."""

    # Compiled patterns for secret detection
    PATTERNS: list[tuple[str, re.Pattern[str]]] = [
        ("AWS_ACCESS_KEY", re.compile(r"AWS_ACCESS_KEY_ID\s*[=:]\s*[A-Z0-9]{20}", re.IGNORECASE)),
        ("AWS_SECRET_KEY", re.compile(r"AWS_SECRET_ACCESS_KEY\s*[=:]\s*[A-Za-z0-9/+=]{40}", re.IGNORECASE)),
        ("GITHUB_TOKEN", re.compile(r"github(?:_token)?\s*[=:]\s*ghp_[A-Za-z0-9]{36}", re.IGNORECASE)),
        ("BEARER_TOKEN", re.compile(r"(?:bearer\s+)?token\s*[=:]\s*[A-Za-z0-9_\-\.]+", re.IGNORECASE)),
        ("API_KEY_GENERIC", re.compile(r"api[_\-]?key\s*[=:]\s*[A-Za-z0-9_\-]{20,}", re.IGNORECASE)),
        ("OPENAI_KEY", re.compile(r"sk-[A-Za-z0-9]{48}", re.IGNORECASE)),
        ("ANTHROPIC_KEY", re.compile(r"sk-ant-[A-Za-z0-9_\-]{50,}", re.IGNORECASE)),
        ("STRIPE_KEY", re.compile(r"sk_live_[A-Za-z0-9]{24}", re.IGNORECASE)),
        ("STRIPE_KEY_TEST", re.compile(r"sk_test_[A-Za-z0-9]{24}", re.IGNORECASE)),
        ("JWT_TOKEN", re.compile(r"eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+", re.IGNORECASE)),
        ("BASIC_AUTH", re.compile(r"Basic\s+[A-Za-z0-9+\/=]{20,}")),
        ("PASSWORD_URL", re.compile(r"password\s*[=:]\s*[^\s&]+", re.IGNORECASE)),
        ("PRIVATE_KEY", re.compile(r"-----BEGIN\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE\s+KEY-----", re.IGNORECASE)),
        ("GCP_KEY", re.compile(r'"type":\s*"service_account"\s*,\s*"project_id"', re.IGNORECASE)),
    ]

    @classmethod
    def mask_secrets(cls, text: str) -> tuple[str, list[str]]:
        """
        Scan text for secret patterns and replace them with [REDACTED:TYPE].
        Returns (masked_text, list_of_detected_types).
        """
        detected: list[str] = []
        masked = text

        for name, pattern in cls.PATTERNS:
            matches = pattern.findall(text)
            if matches:
                detected.append(name)
                for match in matches:
                    masked = masked.replace(match, f"[REDACTED:{name}]")

        return masked, detected

    @classmethod
    def check_dict(cls, data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """
        Recursively scan a dict for secrets, returning a masked copy and list of detected types.
        """
        detected: list[str] = []
        masked: dict[str, Any] = {}

        for key, value in data.items():
            if isinstance(value, str):
                masked_value, found = cls.mask_secrets(value)
                masked[key] = masked_value
                detected.extend(found)
            elif isinstance(value, dict):
                sub_masked, sub_detected = cls.check_dict(value)
                masked[key] = sub_masked
                detected.extend(sub_detected)
            elif isinstance(value, list):
                sub_masked_list = []
                for item in value:
                    if isinstance(item, str):
                        m, f = cls.mask_secrets(item)
                        sub_masked_list.append(m)
                        detected.extend(f)
                    elif isinstance(item, dict):
                        m, f = cls.check_dict(item)
                        sub_masked_list.append(m)
                        detected.extend(f)
                    else:
                        sub_masked_list.append(item)
                masked[key] = sub_masked_list
            else:
                masked[key] = value

        return masked, detected


# ---------------------------------------------------------------------------
# WorkLedger
# ---------------------------------------------------------------------------


class WorkLedger:
    """
    Append-only event ledger for packet lifecycle tracking.
    All timestamps UTC. Ledger is single source of truth.
    """

    def __init__(self, runs_dir: Path | str | None = None) -> None:
        self.runs_dir = Path(runs_dir) if runs_dir else Path("runs")

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _new_event_id() -> str:
        import uuid
        return str(uuid.uuid4())

    def _run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def _packets_dir(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "packets"

    def _events_file(self, run_id: str) -> Path:
        return self._run_dir(run_id) / WORK_LEDGER_EVENTS_FILE

    def _legacy_events_file(self, run_id: str) -> Path:
        """Fallback events file for cross-read from legacy graph-events.jsonl."""
        return self._run_dir(run_id) / GRAPH_EVENTS_FILE

    def _shapes_dir(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "shapes"

    def _artifacts_dir(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "artifacts"

    def _checkpoints_dir(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "checkpoints"

    def _final_dir(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "final"

    # -------------------------------------------------------------------------
    # Sync write operations
    # -------------------------------------------------------------------------

    def create_run(self, run_id: str, objective: str, parent_packet_id: str | None = None) -> str:
        """
        Create run directory structure and initial PACKET_CREATED event.
        Returns run_id.
        """
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)

        (run_dir / WORK_LEDGER_EVENTS_FILE).touch()
        (self._packets_dir(run_id)).mkdir(exist_ok=True)
        (self._shapes_dir(run_id)).mkdir(exist_ok=True)
        (self._artifacts_dir(run_id)).mkdir(exist_ok=True)
        (self._checkpoints_dir(run_id)).mkdir(exist_ok=True)
        (self._final_dir(run_id)).mkdir(exist_ok=True)

        # Log initial packet created event
        self.log_event(
            run_id=run_id,
            event_type=PACKET_CREATED,
            packet_id=None,  # run-level event
            agent_id=None,
            harness=None,
            depth=None,
            data={
                "objective": objective,
                "parent_packet_id": parent_packet_id,
            },
        )

        return run_id

    def log_event(
        self,
        run_id: str,
        event_type: str,
        packet_id: str | None,
        agent_id: str | None,
        harness: str | None,
        depth: int | None,
        data: dict[str, Any],
        masked_fields: list[str] = [],
    ) -> str:
        """Append JSON line to events.jsonl. Returns event_id."""
        event_id = self._new_event_id()
        timestamp = self._utc_now()

        # Auto-detect and mask secrets in data
        masked_data, detected = SecretMasker.check_dict(data)
        if detected:
            masked_fields = list(masked_fields) + detected

        event = AuditEvent(
            event_id=event_id,
            run_id=run_id,
            packet_id=packet_id,
            timestamp=timestamp,
            event_type=event_type,
            agent_id=agent_id,
            harness=harness,
            depth=depth,
            data=masked_data,
            masked_fields=masked_fields,
        )

        events_file = self._events_file(run_id)
        events_file.parent.mkdir(parents=True, exist_ok=True)

        with open(events_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

        return event_id

    def log_packet_emitted(self, run_id: str, packet_id: str, depth: int, data: dict[str, Any]) -> str:
        return self.log_event(
            run_id=run_id,
            event_type=PACKET_EMITTED,
            packet_id=packet_id,
            agent_id=None,
            harness=None,
            depth=depth,
            data=data,
        )

    def log_packet_claimed(self, run_id: str, packet_id: str, agent_id: str, harness: str) -> str:
        return self.log_event(
            run_id=run_id,
            event_type=PACKET_CLAIMED,
            packet_id=packet_id,
            agent_id=agent_id,
            harness=harness,
            depth=None,
            data={"agent_id": agent_id, "harness": harness},
        )

    def log_progress(self, run_id: str, packet_id: str, state_delta: dict[str, Any]) -> str:
        return self.log_event(
            run_id=run_id,
            event_type=PACKET_PROGRESS,
            packet_id=packet_id,
            agent_id=None,
            harness=None,
            depth=None,
            data={"state_delta": state_delta},
        )

    def log_child_requested(
        self, run_id: str, parent_packet_id: str, child_packet_id: str, seam: str
    ) -> str:
        return self.log_event(
            run_id=run_id,
            event_type=CHILD_REQUESTED,
            packet_id=parent_packet_id,
            agent_id=None,
            harness=None,
            depth=None,
            data={"child_packet_id": child_packet_id, "seam": seam},
        )

    def log_child_approved(self, run_id: str, child_packet_id: str) -> str:
        return self.log_event(
            run_id=run_id,
            event_type=CHILD_APPROVED,
            packet_id=child_packet_id,
            agent_id=None,
            harness=None,
            depth=None,
            data={"child_packet_id": child_packet_id},
        )

    def log_child_denied(self, run_id: str, child_packet_id: str, reason: str) -> str:
        return self.log_event(
            run_id=run_id,
            event_type=CHILD_DENIED,
            packet_id=child_packet_id,
            agent_id=None,
            harness=None,
            depth=None,
            data={"child_packet_id": child_packet_id, "reason": reason},
        )

    def log_human_gate(self, run_id: str, packet_id: str, gate: str) -> str:
        return self.log_event(
            run_id=run_id,
            event_type=HUMAN_GATE_ENTERED,
            packet_id=packet_id,
            agent_id=None,
            harness=None,
            depth=None,
            data={"gate": gate},
        )

    def log_human_decision(
        self, run_id: str, packet_id: str, gate: str, approved: bool, approver: str
    ) -> str:
        event_type = HUMAN_GATE_APPROVED if approved else HUMAN_GATE_DENIED
        return self.log_event(
            run_id=run_id,
            event_type=event_type,
            packet_id=packet_id,
            agent_id=approver,
            harness=None,
            depth=None,
            data={"gate": gate, "approved": approved, "approver": approver},
        )

    def log_shape_created(
        self, run_id: str, packet_id: str, shape_id: str, shape_type: str, data: dict[str, Any]
    ) -> str:
        return self.log_event(
            run_id=run_id,
            event_type=SHAPE_CREATED,
            packet_id=packet_id,
            agent_id=None,
            harness=None,
            depth=None,
            data={"shape_id": shape_id, "shape_type": shape_type, **data},
        )

    def log_artifact_written(self, run_id: str, packet_id: str, artifact_path: str) -> str:
        return self.log_event(
            run_id=run_id,
            event_type=ARTIFACT_WRITTEN,
            packet_id=packet_id,
            agent_id=None,
            harness=None,
            depth=None,
            data={"artifact_path": artifact_path},
        )

    def log_checkpoint(self, run_id: str, packet_id: str, checkpoint_id: str, label: str) -> str:
        return self.log_event(
            run_id=run_id,
            event_type=CHECKPOINT_CREATED,
            packet_id=packet_id,
            agent_id=None,
            harness=None,
            depth=None,
            data={"checkpoint_id": checkpoint_id, "label": label},
        )

    def log_error(self, run_id: str, packet_id: str | None, error: str) -> str:
        masked_error, _ = SecretMasker.mask_secrets(error)
        return self.log_event(
            run_id=run_id,
            event_type=ERROR,
            packet_id=packet_id,
            agent_id=None,
            harness=None,
            depth=None,
            data={"error": masked_error},
        )

    def log_run_completed(self, run_id: str, summary: dict[str, Any]) -> str:
        return self.log_event(
            run_id=run_id,
            event_type=RUN_COMPLETED,
            packet_id=None,
            agent_id=None,
            harness=None,
            depth=None,
            data=summary,
        )

    def log_run_failed(self, run_id: str, reason: str) -> str:
        return self.log_event(
            run_id=run_id,
            event_type=RUN_FAILED,
            packet_id=None,
            agent_id=None,
            harness=None,
            depth=None,
            data={"reason": reason},
        )

    # -------------------------------------------------------------------------
    # Async write operations
    # -------------------------------------------------------------------------

    async def create_run_async(self, run_id: str, objective: str, parent_packet_id: str | None = None) -> str:
        """Async version of create_run."""
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)

        (run_dir / WORK_LEDGER_EVENTS_FILE).touch()
        (self._packets_dir(run_id)).mkdir(exist_ok=True)
        (self._shapes_dir(run_id)).mkdir(exist_ok=True)
        (self._artifacts_dir(run_id)).mkdir(exist_ok=True)
        (self._checkpoints_dir(run_id)).mkdir(exist_ok=True)
        (self._final_dir(run_id)).mkdir(exist_ok=True)

        await self.log_event_async(
            run_id=run_id,
            event_type=PACKET_CREATED,
            packet_id=None,
            agent_id=None,
            harness=None,
            depth=None,
            data={
                "objective": objective,
                "parent_packet_id": parent_packet_id,
            },
        )

        return run_id

    async def log_event_async(
        self,
        run_id: str,
        event_type: str,
        packet_id: str | None,
        agent_id: str | None,
        harness: str | None,
        depth: int | None,
        data: dict[str, Any],
        masked_fields: list[str] = [],
    ) -> str:
        """Async append JSON line to events.jsonl. Returns event_id."""
        event_id = self._new_event_id()
        timestamp = self._utc_now()

        masked_data, detected = SecretMasker.check_dict(data)
        if detected:
            masked_fields = list(masked_fields) + detected

        event = AuditEvent(
            event_id=event_id,
            run_id=run_id,
            packet_id=packet_id,
            timestamp=timestamp,
            event_type=event_type,
            agent_id=agent_id,
            harness=harness,
            depth=depth,
            data=masked_data,
            masked_fields=masked_fields,
        )

        events_file = self._events_file(run_id)
        events_file.parent.mkdir(parents=True, exist_ok=True)

        async with aiofiles.open(events_file, "a", encoding="utf-8") as f:
            await f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

        return event_id

    async def log_packet_emitted_async(self, run_id: str, packet_id: str, depth: int, data: dict[str, Any]) -> str:
        return await self.log_event_async(
            run_id=run_id,
            event_type=PACKET_EMITTED,
            packet_id=packet_id,
            agent_id=None,
            harness=None,
            depth=depth,
            data=data,
        )

    async def log_packet_claimed_async(self, run_id: str, packet_id: str, agent_id: str, harness: str) -> str:
        return await self.log_event_async(
            run_id=run_id,
            event_type=PACKET_CLAIMED,
            packet_id=packet_id,
            agent_id=agent_id,
            harness=harness,
            depth=None,
            data={"agent_id": agent_id, "harness": harness},
        )

    async def log_progress_async(self, run_id: str, packet_id: str, state_delta: dict[str, Any]) -> str:
        return await self.log_event_async(
            run_id=run_id,
            event_type=PACKET_PROGRESS,
            packet_id=packet_id,
            agent_id=None,
            harness=None,
            depth=None,
            data={"state_delta": state_delta},
        )

    async def log_child_requested_async(
        self, run_id: str, parent_packet_id: str, child_packet_id: str, seam: str
    ) -> str:
        return await self.log_event_async(
            run_id=run_id,
            event_type=CHILD_REQUESTED,
            packet_id=parent_packet_id,
            agent_id=None,
            harness=None,
            depth=None,
            data={"child_packet_id": child_packet_id, "seam": seam},
        )

    async def log_child_approved_async(self, run_id: str, child_packet_id: str) -> str:
        return await self.log_event_async(
            run_id=run_id,
            event_type=CHILD_APPROVED,
            packet_id=child_packet_id,
            agent_id=None,
            harness=None,
            depth=None,
            data={"child_packet_id": child_packet_id},
        )

    async def log_child_denied_async(self, run_id: str, child_packet_id: str, reason: str) -> str:
        return await self.log_event_async(
            run_id=run_id,
            event_type=CHILD_DENIED,
            packet_id=child_packet_id,
            agent_id=None,
            harness=None,
            depth=None,
            data={"child_packet_id": child_packet_id, "reason": reason},
        )

    async def log_human_gate_async(self, run_id: str, packet_id: str, gate: str) -> str:
        return await self.log_event_async(
            run_id=run_id,
            event_type=HUMAN_GATE_ENTERED,
            packet_id=packet_id,
            agent_id=None,
            harness=None,
            depth=None,
            data={"gate": gate},
        )

    async def log_human_decision_async(
        self, run_id: str, packet_id: str, gate: str, approved: bool, approver: str
    ) -> str:
        event_type = HUMAN_GATE_APPROVED if approved else HUMAN_GATE_DENIED
        return await self.log_event_async(
            run_id=run_id,
            event_type=event_type,
            packet_id=packet_id,
            agent_id=approver,
            harness=None,
            depth=None,
            data={"gate": gate, "approved": approved, "approver": approver},
        )

    async def log_shape_created_async(
        self, run_id: str, packet_id: str, shape_id: str, shape_type: str, data: dict[str, Any]
    ) -> str:
        return await self.log_event_async(
            run_id=run_id,
            event_type=SHAPE_CREATED,
            packet_id=packet_id,
            agent_id=None,
            harness=None,
            depth=None,
            data={"shape_id": shape_id, "shape_type": shape_type, **data},
        )

    async def log_artifact_written_async(self, run_id: str, packet_id: str, artifact_path: str) -> str:
        return await self.log_event_async(
            run_id=run_id,
            event_type=ARTIFACT_WRITTEN,
            packet_id=packet_id,
            agent_id=None,
            harness=None,
            depth=None,
            data={"artifact_path": artifact_path},
        )

    async def log_checkpoint_async(self, run_id: str, packet_id: str, checkpoint_id: str, label: str) -> str:
        return await self.log_event_async(
            run_id=run_id,
            event_type=CHECKPOINT_CREATED,
            packet_id=packet_id,
            agent_id=None,
            harness=None,
            depth=None,
            data={"checkpoint_id": checkpoint_id, "label": label},
        )

    async def log_error_async(self, run_id: str, packet_id: str | None, error: str) -> str:
        masked_error, _ = SecretMasker.mask_secrets(error)
        return await self.log_event_async(
            run_id=run_id,
            event_type=ERROR,
            packet_id=packet_id,
            agent_id=None,
            harness=None,
            depth=None,
            data={"error": masked_error},
        )

    async def log_run_completed_async(self, run_id: str, summary: dict[str, Any]) -> str:
        return await self.log_event_async(
            run_id=run_id,
            event_type=RUN_COMPLETED,
            packet_id=None,
            agent_id=None,
            harness=None,
            depth=None,
            data=summary,
        )

    async def log_run_failed_async(self, run_id: str, reason: str) -> str:
        return await self.log_event_async(
            run_id=run_id,
            event_type=RUN_FAILED,
            packet_id=None,
            agent_id=None,
            harness=None,
            depth=None,
            data={"reason": reason},
        )

    # -------------------------------------------------------------------------
    # Read operations
    # -------------------------------------------------------------------------

    @staticmethod
    def _normalize_legacy_event(d: dict[str, Any]) -> dict[str, Any]:
        """
        Normalize legacy graph-events.jsonl fields to work_ledger schema.
        - worker_id -> packet_id
        - parent_worker_id -> parent_packet_id in data
        - child_worker_id -> child_packet_id in data
        - RUN_CREATED -> PACKET_CREATED
        - CHILD_REJECTED -> CHILD_DENIED
        """
        d = dict(d)

        # Normalize top-level field: worker_id -> packet_id
        if "worker_id" in d and "packet_id" not in d:
            d["packet_id"] = d.pop("worker_id")
        elif "worker_id" in d:
            d.pop("worker_id")

        # Normalize data dict: parent_worker_id -> parent_packet_id, child_worker_id -> child_packet_id
        if "data" in d and isinstance(d["data"], dict):
            data = dict(d["data"])
            if "parent_worker_id" in data and "parent_packet_id" not in data:
                data["parent_packet_id"] = data.pop("parent_worker_id")
            elif "parent_worker_id" in data:
                data.pop("parent_worker_id")
            if "child_worker_id" in data and "child_packet_id" not in data:
                data["child_packet_id"] = data.pop("child_worker_id")
            elif "child_worker_id" in data:
                data.pop("child_worker_id")
            d["data"] = data

        # Normalize event type aliases
        if d.get("event_type") == _RUN_CREATED:
            d["event_type"] = PACKET_CREATED
        elif d.get("event_type") == _CHILD_REJECTED:
            d["event_type"] = CHILD_DENIED

        # Ensure required fields for AuditEvent have defaults
        if "agent_id" not in d:
            d["agent_id"] = None
        if "harness" not in d:
            d["harness"] = None

        return d

    def _iter_events(self, run_id: str) -> Iterator[AuditEvent]:
        """Yield AuditEvent objects, trying packet-events.jsonl first, then graph-events.jsonl (legacy)."""
        events_file = self._events_file(run_id)
        if not events_file.exists():
            # Fall back to legacy graph-events.jsonl for cross-read compatibility
            events_file = self._legacy_events_file(run_id)
            if not events_file.exists():
                return
            is_legacy = True
        else:
            is_legacy = False

        with open(events_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    if is_legacy:
                        d = self._normalize_legacy_event(d)
                    yield AuditEvent.from_dict(d)
                except (json.JSONDecodeError, TypeError):
                    continue

    def get_events(
        self, run_id: str, event_type: str | None = None, packet_id: str | None = None
    ) -> list[AuditEvent]:
        """Read events.jsonl, return filtered list."""
        events = list(self._iter_events(run_id))

        if event_type is not None:
            events = [e for e in events if e.event_type == event_type]

        if packet_id is not None:
            events = [e for e in events if e.packet_id == packet_id]

        return events

    def get_timeline(self, run_id: str) -> list[AuditEvent]:
        """All events chronologically (already in order from append-only log)."""
        return list(self._iter_events(run_id))

    def get_packet_history(self, run_id: str, packet_id: str) -> list[AuditEvent]:
        """All events for a specific packet."""
        return self.get_events(run_id, packet_id=packet_id)

    # Alias for get_packet_history — clearer name for replay scenarios
    def get_packet_events(self, run_id: str, packet_id: str) -> list[AuditEvent]:
        """All events for a specific packet (alias for get_packet_history)."""
        return self.get_events(run_id, packet_id=packet_id)

    def get_delegation_tree(self, run_id: str) -> dict[str, Any]:
        """
        Builds tree of parent->child packet relationships.
        Returns dict: {packet_id: {parent_id, children[], depth, ...}}
        """
        events = self.get_timeline(run_id)
        tree: dict[str, dict[str, Any]] = {}

        # First pass: collect all packet info
        for event in events:
            pid = event.packet_id
            if pid is None:
                continue

            if pid not in tree:
                tree[pid] = {
                    "packet_id": pid,
                    "parent_id": None,
                    "children": [],
                    "depth": event.depth,
                    "events": [],
                }

            tree[pid]["events"].append(event.event_id)

            # Check for child relationships
            if event.event_type == CHILD_REQUESTED:
                child_id = event.data.get("child_packet_id")
                if child_id:
                    if child_id not in tree:
                        tree[child_id] = {
                            "packet_id": child_id,
                            "parent_id": pid,
                            "children": [],
                            "depth": None,
                            "events": [],
                        }
                    tree[child_id]["parent_id"] = pid
                    if pid not in tree[child_id]["children"]:
                        tree[pid]["children"].append(child_id)

        # Convert to tree structure
        roots: list[str] = []
        for pid, info in tree.items():
            if info["parent_id"] is None:
                roots.append(pid)

        return {
            "roots": roots,
            "packets": tree,
        }

    def list_seams(self, run_id: str | None = None) -> list[dict[str, Any]]:
        """
        List all EMITTED (unclaimed) packets with their seams.
        If run_id provided: scope to that run.
        If None: list across all runs.
        """
        seams: list[dict[str, Any]] = []

        if run_id is not None:
            run_ids = [run_id]
        else:
            run_ids = []
            if self.runs_dir.exists():
                for d in self.runs_dir.iterdir():
                    if d.is_dir():
                        run_ids.append(d.name)

        for rid in run_ids:
            events_file = self._events_file(rid)
            if not events_file.exists():
                continue

            # Track emitted packets and their claims
            emitted_packets: dict[str, dict[str, Any]] = {}
            claimed_packets: set[str] = set()

            with open(events_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = AuditEvent.from_dict(json.loads(line))
                    except (json.JSONDecodeError, TypeError):
                        continue

                    if event.event_type == PACKET_EMITTED:
                        pid = event.packet_id
                        if pid:
                            emitted_packets[pid] = {
                                "run_id": rid,
                                "packet_id": pid,
                                "seam": event.data.get("seam", ""),
                                "objective": event.data.get("objective", ""),
                                "depth": event.depth,
                                "created_at": event.timestamp,
                            }

                    elif event.event_type == PACKET_CLAIMED:
                        pid = event.packet_id
                        if pid:
                            claimed_packets.add(pid)

            # Only include unclaimed packets
            for pid, info in emitted_packets.items():
                if pid not in claimed_packets:
                    seams.append(info)

        return seams

    def list_runs(self) -> list[dict[str, Any]]:
        """Returns all runs with: run_id, created_at, event_count."""
        runs: list[dict[str, Any]] = []

        if not self.runs_dir.exists():
            return runs

        for d in self.runs_dir.iterdir():
            if not d.is_dir():
                continue

            run_id = d.name
            events_file = self._events_file(run_id)
            event_count = 0

            if events_file.exists():
                with open(events_file, "r", encoding="utf-8") as f:
                    event_count = sum(1 for line in f if line.strip())

            # Get first event timestamp as created_at
            created_at = ""
            if events_file.exists():
                with open(events_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = AuditEvent.from_dict(json.loads(line))
                            created_at = event.timestamp
                            break
                        except (json.JSONDecodeError, TypeError):
                            continue

            runs.append({
                "run_id": run_id,
                "created_at": created_at,
                "event_count": event_count,
            })

        return runs

    def verify_integrity(self, run_id: str) -> tuple[bool, list[str]]:
        """
        Checks events.jsonl is valid JSONL.
        Returns (is_valid, issues).
        """
        issues: list[str] = []
        events_file = self._events_file(run_id)

        if not events_file.exists():
            issues.append(f"events.jsonl not found for run {run_id}")
            return False, issues

        line_num = 0
        with open(events_file, "r", encoding="utf-8") as f:
            for line in f:
                line_num += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    # Verify required fields
                    required = ["event_id", "run_id", "timestamp", "event_type"]
                    for field in required:
                        if field not in d:
                            issues.append(f"Line {line_num}: missing required field '{field}'")
                    # Verify it can be parsed as AuditEvent
                    AuditEvent.from_dict(d)
                except json.JSONDecodeError as e:
                    issues.append(f"Line {line_num}: invalid JSON - {e}")
                except TypeError as e:
                    issues.append(f"Line {line_num}: invalid AuditEvent - {e}")

        return len(issues) == 0, issues


# ---------------------------------------------------------------------------
# ReplayEngine
# ---------------------------------------------------------------------------


class ReplayEngine:
    """Reconstruct run state from ledger events."""

    def __init__(self, ledger: WorkLedger) -> None:
        self.ledger = ledger

    def replay(self, run_id: str) -> dict[str, Any]:
        """
        Reconstruct run state from ledger events.
        Builds: packets_created, packets_claimed, state_checkpoints,
        shapes_created, delegation_tree.
        """
        events = self.ledger.get_timeline(run_id)

        packets_created: dict[str, dict[str, Any]] = {}
        packets_claimed: dict[str, dict[str, Any]] = {}
        state_checkpoints: dict[str, list[dict[str, Any]]] = {}
        shapes_created: dict[str, list[dict[str, Any]]] = {}
        delegation_tree: dict[str, list[str]] = {}
        errors: list[dict[str, Any]] = []
        run_summary: dict[str, Any] = {}

        for event in events:
            pid = event.packet_id

            if event.event_type == PACKET_CREATED:
                if pid:
                    packets_created[pid] = {
                        "packet_id": pid,
                        "created_at": event.timestamp,
                        "depth": event.depth,
                        "data": event.data,
                    }

            elif event.event_type == PACKET_EMITTED:
                if pid:
                    if pid not in packets_created:
                        packets_created[pid] = {
                            "packet_id": pid,
                            "created_at": event.timestamp,
                            "depth": event.depth,
                            "data": event.data,
                        }
                    else:
                        packets_created[pid]["depth"] = event.depth

            elif event.event_type == PACKET_CLAIMED:
                if pid:
                    packets_claimed[pid] = {
                        "packet_id": pid,
                        "claimed_at": event.timestamp,
                        "agent_id": event.agent_id,
                        "harness": event.harness,
                    }

            elif event.event_type == CHILD_REQUESTED:
                parent_id = pid
                child_id = event.data.get("child_packet_id")
                if parent_id and child_id:
                    if parent_id not in delegation_tree:
                        delegation_tree[parent_id] = []
                    delegation_tree[parent_id].append(child_id)

            elif event.event_type == CHECKPOINT_CREATED:
                if pid:
                    if pid not in state_checkpoints:
                        state_checkpoints[pid] = []
                    state_checkpoints[pid].append({
                        "checkpoint_id": event.data.get("checkpoint_id"),
                        "label": event.data.get("label"),
                        "created_at": event.timestamp,
                    })

            elif event.event_type == SHAPE_CREATED:
                if pid:
                    if pid not in shapes_created:
                        shapes_created[pid] = []
                    shapes_created[pid].append({
                        "shape_id": event.data.get("shape_id"),
                        "shape_type": event.data.get("shape_type"),
                        "created_at": event.timestamp,
                    })

            elif event.event_type == ERROR:
                errors.append({
                    "packet_id": pid,
                    "error": event.data.get("error", ""),
                    "timestamp": event.timestamp,
                })

            elif event.event_type == RUN_COMPLETED:
                run_summary = event.data

        return {
            "run_id": run_id,
            "packets_created": packets_created,
            "packets_claimed": packets_claimed,
            "state_checkpoints": state_checkpoints,
            "shapes_created": shapes_created,
            "delegation_tree": delegation_tree,
            "errors": errors,
            "run_summary": run_summary,
            "total_events": len(events),
        }

    def replay_to_text(self, run_id: str) -> str:
        """Generate human-readable replay report."""
        state = self.replay(run_id)
        lines: list[str] = []

        lines.append(f"=== Replay Report: {run_id} ===")
        lines.append(f"Total events: {state['total_events']}")
        lines.append("")

        # Packets
        packets = state["packets_created"]
        lines.append(f"Packets created: {len(packets)}")
        for pid, info in packets.items():
            depth = info.get("depth")
            lines.append(f"  [{pid}] depth={depth} created={info['created_at']}")

        lines.append("")

        # Claimed packets
        claimed = state["packets_claimed"]
        lines.append(f"Packets claimed: {len(claimed)}")
        for pid, info in claimed.items():
            lines.append(f"  [{pid}] by={info['agent_id']} harness={info['harness']} at={info['claimed_at']}")

        lines.append("")

        # Delegation tree
        lines.append("Delegation tree:")
        tree = state["delegation_tree"]
        if tree:
            for parent, children in tree.items():
                lines.append(f"  [{parent}] -> {children}")
        else:
            lines.append("  (none)")

        lines.append("")

        # Shapes
        shapes = state["shapes_created"]
        lines.append(f"Shapes created: {sum(len(v) for v in shapes.values())}")
        for pid, shape_list in shapes.items():
            for shape in shape_list:
                lines.append(f"  [{pid}] {shape['shape_type']}:{shape['shape_id']}")

        lines.append("")

        # Errors
        errors = state["errors"]
        lines.append(f"Errors: {len(errors)}")
        for err in errors:
            lines.append(f"  [{err['packet_id']}] {err['error']}")

        lines.append("")

        # Run summary
        summary = state["run_summary"]
        if summary:
            lines.append("Run summary:")
            for k, v in summary.items():
                lines.append(f"  {k}: {v}")

        return "\n".join(lines)

    def get_state_at_event(self, run_id: str, event_id: str) -> dict[str, Any]:
        """
        Reconstruct state up to and including the event with given event_id.
        """
        events = self.ledger.get_timeline(run_id)

        # Find event index
        target_idx = None
        for idx, event in enumerate(events):
            if event.event_id == event_id:
                target_idx = idx
                break

        if target_idx is None:
            return {"error": f"Event {event_id} not found"}

        # Replay up to and including target event
        return self._replay_events(events[:target_idx + 1], run_id)

    def get_packet_events(self, run_id: str, packet_id: str) -> list[AuditEvent]:
        """Get all events for a specific packet in order."""
        return self.ledger.get_packet_history(run_id, packet_id)

    def _replay_events(self, events: list[AuditEvent], run_id: str) -> dict[str, Any]:
        """Internal: replay a specific slice of events."""
        packets_created: dict[str, dict[str, Any]] = {}
        packets_claimed: dict[str, dict[str, Any]] = {}
        delegation_tree: dict[str, list[str]] = {}

        for event in events:
            pid = event.packet_id

            if event.event_type == PACKET_CREATED and pid:
                packets_created[pid] = {
                    "packet_id": pid,
                    "created_at": event.timestamp,
                    "depth": event.depth,
                }

            elif event.event_type == PACKET_EMITTED and pid:
                if pid not in packets_created:
                    packets_created[pid] = {
                        "packet_id": pid,
                        "created_at": event.timestamp,
                        "depth": event.depth,
                    }

            elif event.event_type == PACKET_CLAIMED and pid:
                packets_claimed[pid] = {
                    "packet_id": pid,
                    "claimed_at": event.timestamp,
                    "agent_id": event.agent_id,
                    "harness": event.harness,
                }

            elif event.event_type == CHILD_REQUESTED:
                parent_id = pid
                child_id = event.data.get("child_packet_id")
                if parent_id and child_id:
                    if parent_id not in delegation_tree:
                        delegation_tree[parent_id] = []
                    delegation_tree[parent_id].append(child_id)

        return {
            "run_id": run_id,
            "packets_created": packets_created,
            "packets_claimed": packets_claimed,
            "delegation_tree": delegation_tree,
            "events_replayed": len(events),
        }
