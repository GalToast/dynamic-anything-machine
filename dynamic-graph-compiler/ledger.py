"""
Audit ledger for replayable runs and complete auditability.

Provides event logging, replay, and verification capabilities for dynamic graph
compilation runs with full audit trail.

Events are stored as JSON lines in runs/<run_id>/graph-events.jsonl (or legacy events.jsonl).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiofiles

from dynamic_schema import GraphSpec, WorkerPacket, WorkerResult, RunManifest

# Checkpoint state file name for replay restoration
RUNNING_STATE_FILE = ".running_state.json"

# Configuration
GRAPH_LEDGER_EVENTS_FILE = os.environ.get("GRAPH_LEDGER_EVENTS_FILE", "graph-events.jsonl")
# Fallback for legacy runs
_LEGACY_EVENTS_FILE = "events.jsonl"

# ============================================================================
# Event Type Constants
# ============================================================================


class EventType(str, Enum):
    """Audit event types for complete run traceability."""

    RUN_CREATED = "RUN_CREATED"
    PACKET_EMITTED = "PACKET_EMITTED"
    RESULT_RECORDED = "RESULT_RECORDED"
    CHILD_REQUESTED = "CHILD_REQUESTED"
    CHILD_APPROVED = "CHILD_APPROVED"
    CHILD_REJECTED = "CHILD_REJECTED"
    HUMAN_APPROVAL_GRANTED = "HUMAN_APPROVAL_GRANTED"
    HUMAN_APPROVAL_DENIED = "HUMAN_APPROVAL_DENIED"
    CHECKPOINT_CREATED = "CHECKPOINT_CREATED"
    CHECKPOINT_RESTORED = "CHECKPOINT_RESTORED"
    ERROR_OCCURRED = "ERROR_OCCURRED"
    RUN_COMPLETED = "RUN_COMPLETED"
    RUN_FAILED = "RUN_FAILED"
    RUN_CANCELLED = "RUN_CANCELLED"
    SECRET_DETECTED = "SECRET_DETECTED"
    POLICY_VIOLATION = "POLICY_VIOLATION"


# ============================================================================
# Audit Event Model
# ============================================================================


class AuditEvent:
    """
    Represents a single audit event in the ledger.

    Stored as JSON lines in events.jsonl for each run.
    """

    def __init__(
        self,
        event_id: str,
        run_id: str,
        timestamp: str,
        event_type: str,
        worker_id: Optional[str] = None,
        depth: Optional[int] = None,
        data: Optional[Dict[str, Any]] = None,
        masked_fields: Optional[List[str]] = None,
    ):
        self.event_id = event_id
        self.run_id = run_id
        self.timestamp = timestamp
        self.event_type = event_type
        self.worker_id = worker_id
        self.depth = depth
        self.data = data or {}
        self.masked_fields = masked_fields or []

    def to_dict(self) -> Dict[str, Any]:
        """Serialize event to dictionary."""
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "worker_id": self.worker_id,
            "depth": self.depth,
            "data": self.data,
            "masked_fields": self.masked_fields,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> AuditEvent:
        """Deserialize event from dictionary."""
        return cls(
            event_id=d["event_id"],
            run_id=d["run_id"],
            timestamp=d["timestamp"],
            event_type=d["event_type"],
            worker_id=d.get("worker_id"),
            depth=d.get("depth"),
            data=d.get("data", {}),
            masked_fields=d.get("masked_fields", []),
        )

    def to_json_line(self) -> str:
        """Serialize to JSON line string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_json_line(cls, line: str) -> AuditEvent:
        """Deserialize from JSON line string."""
        return cls.from_dict(json.loads(line))

    def get_human_readable_description(self) -> str:
        """
        Generate human-readable description of this event.

        Returns a concise description suitable for timeline display.
        """
        desc_map = {
            EventType.RUN_CREATED: f"Run created: {self.data.get('description', 'No description')}",
            EventType.PACKET_EMITTED: f"Packet emitted by worker {self.worker_id} at depth {self.depth}",
            EventType.RESULT_RECORDED: f"Result recorded for worker {self.worker_id}",
            EventType.CHILD_REQUESTED: f"Child requested by {self.worker_id}: {self.data.get('child_action', 'unknown')}",
            EventType.CHILD_APPROVED: f"Child approved: {self.data.get('child_worker_id', 'unknown')}",
            EventType.CHILD_REJECTED: f"Child rejected by {self.worker_id}: {self.data.get('reason', 'unknown')}",
            EventType.HUMAN_APPROVAL_GRANTED: f"Human approval granted at gate '{self.data.get('gate', 'unknown')}' by {self.data.get('approver', 'unknown')}",
            EventType.HUMAN_APPROVAL_DENIED: f"Human approval denied at gate '{self.data.get('gate', 'unknown')}' by {self.data.get('approver', 'unknown')}",
            EventType.CHECKPOINT_CREATED: f"Checkpoint created: {self.data.get('checkpoint_id', 'unknown')}",
            EventType.CHECKPOINT_RESTORED: f"Checkpoint restored: {self.data.get('checkpoint_id', 'unknown')}",
            EventType.ERROR_OCCURRED: f"Error occurred: {self.data.get('error', 'unknown')}",
            EventType.RUN_COMPLETED: f"Run completed successfully in {self.data.get('duration_seconds', 0):.2f}s",
            EventType.RUN_FAILED: f"Run failed: {self.data.get('error', 'unknown')}",
            EventType.RUN_CANCELLED: "Run was cancelled",
            EventType.SECRET_DETECTED: f"Secret detected ({self.data.get('secret_type', 'unknown')}) at {self.data.get('location', 'unknown')}",
            EventType.POLICY_VIOLATION: f"Policy violation: {self.data.get('violation', 'unknown')}",
        }
        return desc_map.get(self.event_type, f"Event: {self.event_type}")


# ============================================================================
# Secret Masker Utility
# ============================================================================


class SecretMasker:
    """
    Utility for detecting and masking secrets in text and data structures.

    Detects API keys, tokens, passwords, cookies, and other sensitive data.
    """

    # Regex patterns for secret detection
    PATTERNS = {
        "API_KEY": re.compile(r"(?i)(api[_-]?key|apikey)\s*[:=]\s*['\"]?([a-zA-Z0-9_\-]{20,})['\"]?"),
        "BEARER_TOKEN": re.compile(r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{20,}"),
        "AWS_KEY": re.compile(r"(?i)(aws[_-]?(access[_-]?key[_-]?id|secret[_-]?access[_-]?key))\s*[:=]\s*['\"]?([a-zA-Z0-9/\+]{20,})['\"]?"),
        "GITHUB_TOKEN": re.compile(r"(?i)github(?:[_-]?token)?\s*[:=]\s*['\"]?(gh[pousr]_[a-zA-Z0-9_\-]{20,})['\"]?"),
        "PASSWORD": re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*['\"]?([^\s'\"]{8,})['\"]?"),
        "JWT": re.compile(r"(?i)jwt\s*[:=]\s*['\"]?([a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+)['\"]?"),
        "COOKIE": re.compile(r"(?i)cookie\s*[:=]\s*['\"]?([^\s'\"]{20,})['\"]?"),
        "PRIVATE_KEY": re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
        "GENERIC_TOKEN": re.compile(r"(?i)(token|auth[_-]?token|access[_-]?token)\s*[:=]\s*['\"]?([a-zA-Z0-9_\-\.]{20,})['\"]?"),
        "SECRET": re.compile(r"(?i)(secret|client[_-]?secret)\s*[:=]\s*['\"]?([a-zA-Z0-9_\-]{16,})['\"]?"),
    }

    # Placeholder for masked secrets
    MASK_PLACEHOLDER = "***REDACTED***"

    @classmethod
    def mask_secrets(cls, text: str) -> Tuple[str, List[str]]:
        """
        Detect and mask secrets in text.

        Args:
            text: Input text to scan for secrets

        Returns:
            Tuple of (masked_text, list_of_detected_secrets)
        """
        if not text or not isinstance(text, str):
            return text, []

        detected = []
        masked = text

        for secret_type, pattern in cls.PATTERNS.items():
            matches = pattern.findall(masked)
            for match in matches:
                if isinstance(match, tuple):
                    # Group-based match (e.g., "API_KEY", "key_value")
                    secret = match[-1] if len(match) > 1 else match[0]
                else:
                    secret = match

                if secret and secret not in detected:
                    detected.append(f"{secret_type}:{secret[:8]}...")

                # Replace with placeholder
                if isinstance(match, tuple) and len(match) > 1:
                    full_match = pattern.search(masked)
                    if full_match:
                        masked = masked[:full_match.start()] + f"{match[0]}: {cls.MASK_PLACEHOLDER}" + masked[full_match.end():]
                else:
                    masked = pattern.sub(f"{cls.MASK_PLACEHOLDER}", masked)

        return masked, detected

    @classmethod
    def check_for_secrets(cls, data: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
        """
        Recursively scan a dictionary for secret patterns.

        Args:
            data: Dictionary to scan

        Returns:
            Tuple of (masked_copy, list_of_detected_secrets)
        """
        if not isinstance(data, dict):
            return data, []

        all_secrets = []
        masked = {}

        for key, value in data.items():
            if isinstance(value, dict):
                masked[key], secrets = cls.check_for_secrets(value)
                all_secrets.extend(secrets)
            elif isinstance(value, str):
                masked_value, secrets = cls.mask_secrets(value)
                masked[key] = masked_value
                all_secrets.extend(secrets)
            elif isinstance(value, list):
                masked_list = []
                for item in value:
                    if isinstance(item, str):
                        m_item, secrets = cls.mask_secrets(item)
                        masked_list.append(m_item)
                        all_secrets.extend(secrets)
                    else:
                        masked_list.append(item)
                masked[key] = masked_list
            else:
                masked[key] = value

        return masked, all_secrets


# ============================================================================
# Ledger Class
# ============================================================================


class Ledger:
    """
    Audit ledger for recording and replaying run events.

    Provides complete auditability with event logging, filtering, verification,
    and replay capabilities.
    """

    def __init__(self, runs_dir: Path | str):
        """
        Initialize the ledger with the runs directory.

        Args:
            runs_dir: Base directory containing run subdirectories
        """
        self.runs_dir = Path(runs_dir)
        self._ensure_runs_dir()

    def _ensure_runs_dir(self) -> None:
        """Ensure runs directory exists."""
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def _get_run_dir(self, run_id: str) -> Path:
        """Get the run-specific directory."""
        return self.runs_dir / run_id

    def _get_events_file(self, run_id: str) -> Path:
        """Get the events file path for a run, with legacy fallback for reads only."""
        return self._get_run_dir(run_id) / GRAPH_LEDGER_EVENTS_FILE

    def _get_events_file_for_read(self, run_id: str) -> Path:
        """Get the events file path for reading, with legacy fallback."""
        primary = self._get_run_dir(run_id) / GRAPH_LEDGER_EVENTS_FILE
        if primary.exists():
            return primary
        # Fall back to legacy events.jsonl for existing runs
        legacy = self._get_run_dir(run_id) / _LEGACY_EVENTS_FILE
        if legacy.exists():
            return legacy
        return primary

    def _now_iso(self) -> str:
        """Get current timestamp in ISO format."""
        return datetime.now(timezone.utc).isoformat()

    def log_event(
        self,
        run_id: str,
        event_type: str,
        worker_id: Optional[str],
        depth: Optional[int],
        data: Dict[str, Any],
        masked_fields: Optional[List[str]] = None,
    ) -> str:
        """
        Append an event to the ledger.

        Args:
            run_id: Unique identifier for the run
            event_type: Type of event (from EventType enum)
            worker_id: ID of the worker that generated this event
            depth: Depth in the delegation tree
            data: Event-specific payload
            masked_fields: List of field names that were redacted

        Returns:
            The generated event_id
        """
        event_id = str(uuid.uuid4())
        event = AuditEvent(
            event_id=event_id,
            run_id=run_id,
            timestamp=self._now_iso(),
            event_type=event_type,
            worker_id=worker_id,
            depth=depth,
            data=data,
            masked_fields=masked_fields or [],
        )

        run_dir = self._get_run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        events_file = self._get_events_file(run_id)

        with open(events_file, "a", encoding="utf-8") as f:
            f.write(event.to_json_line() + "\n")

        return event_id

    def log_packet_emitted(self, run_id: str, packet: WorkerPacket) -> str:
        """
        Log a packet emission event.

        Args:
            run_id: Unique identifier for the run
            packet: The worker packet that was emitted

        Returns:
            The generated event_id
        """
        data = {
            "action": packet.action,
            "packet_type": packet.packet_type if hasattr(packet, "packet_type") else "unknown",
        }
        return self.log_event(
            run_id=run_id,
            event_type=EventType.PACKET_EMITTED,
            worker_id=packet.worker_id,
            depth=packet.depth,
            data=data,
        )

    def log_result_recorded(self, run_id: str, result: WorkerResult) -> str:
        """
        Log a result recording event.

        Args:
            run_id: Unique identifier for the run
            result: The worker result that was recorded

        Returns:
            The generated event_id
        """
        data = {
            "success": result.success if hasattr(result, "success") else True,
            "output_summary": str(result)[:500] if result else "",
        }
        worker_id = result.worker_id if hasattr(result, "worker_id") else None
        depth = result.depth if hasattr(result, "depth") else None
        return self.log_event(
            run_id=run_id,
            event_type=EventType.RESULT_RECORDED,
            worker_id=worker_id,
            depth=depth,
            data=data,
        )

    def log_child_requested(
        self, run_id: str, parent_worker_id: str, child_packet: WorkerPacket
    ) -> str:
        """
        Log a child worker request event.

        Args:
            run_id: Unique identifier for the run
            parent_worker_id: ID of the parent worker
            child_packet: The child packet that was requested

        Returns:
            The generated event_id
        """
        data = {
            "parent_worker_id": parent_worker_id,
            "child_action": child_packet.action,
            "child_worker_id": child_packet.worker_id,
        }
        return self.log_event(
            run_id=run_id,
            event_type=EventType.CHILD_REQUESTED,
            worker_id=parent_worker_id,
            depth=None,
            data=data,
        )

    def log_child_approved(
        self, run_id: str, parent_worker_id: str, child_worker_id: str
    ) -> str:
        """
        Log a child worker approval event.

        Args:
            run_id: Unique identifier for the run
            parent_worker_id: ID of the parent worker
            child_worker_id: ID of the approved child worker

        Returns:
            The generated event_id
        """
        data = {
            "parent_worker_id": parent_worker_id,
            "child_worker_id": child_worker_id,
        }
        return self.log_event(
            run_id=run_id,
            event_type=EventType.CHILD_APPROVED,
            worker_id=parent_worker_id,
            depth=None,
            data=data,
        )

    def log_child_rejected(
        self, run_id: str, parent_worker_id: str, reason: str
    ) -> str:
        """
        Log a child worker rejection event.

        Args:
            run_id: Unique identifier for the run
            parent_worker_id: ID of the parent worker
            reason: Reason for rejection

        Returns:
            The generated event_id
        """
        data = {
            "parent_worker_id": parent_worker_id,
            "reason": reason,
        }
        return self.log_event(
            run_id=run_id,
            event_type=EventType.CHILD_REJECTED,
            worker_id=parent_worker_id,
            depth=None,
            data=data,
        )

    def log_human_approval(
        self, run_id: str, gate: str, decision: str, approver: str
    ) -> str:
        """
        Log a human approval decision.

        Args:
            run_id: Unique identifier for the run
            gate: The approval gate name
            decision: Either "granted" or "denied"
            approver: Identifier of the human approver

        Returns:
            The generated event_id
        """
        event_type = EventType.HUMAN_APPROVAL_GRANTED if decision == "granted" else EventType.HUMAN_APPROVAL_DENIED
        data = {
            "gate": gate,
            "decision": decision,
            "approver": approver,
        }
        return self.log_event(
            run_id=run_id,
            event_type=event_type,
            worker_id=None,
            depth=None,
            data=data,
        )

    def log_policy_violation(
        self, run_id: str, violation: str, details: Dict[str, Any]
    ) -> str:
        """
        Log a policy violation.

        Args:
            run_id: Unique identifier for the run
            violation: Description of the violation
            details: Additional details about the violation

        Returns:
            The generated event_id
        """
        data = {
            "violation": violation,
            "details": details,
        }
        return self.log_event(
            run_id=run_id,
            event_type=EventType.POLICY_VIOLATION,
            worker_id=None,
            depth=None,
            data=data,
        )

    def log_secret_detected(
        self, run_id: str, secret_type: str, location: str
    ) -> str:
        """
        Log a secret detection event.

        Args:
            run_id: Unique identifier for the run
            secret_type: Type of secret detected
            location: Location where the secret was found

        Returns:
            The generated event_id
        """
        data = {
            "secret_type": secret_type,
            "location": location,
        }
        return self.log_event(
            run_id=run_id,
            event_type=EventType.SECRET_DETECTED,
            worker_id=None,
            depth=None,
            data=data,
        )

    def log_error(self, run_id: str, error: str, stack_trace: Optional[str]) -> str:
        """
        Log an error occurrence.

        Args:
            run_id: Unique identifier for the run
            error: Error message
            stack_trace: Optional stack trace

        Returns:
            The generated event_id
        """
        data = {
            "error": error,
            "stack_trace": stack_trace,
        }
        return self.log_event(
            run_id=run_id,
            event_type=EventType.ERROR_OCCURRED,
            worker_id=None,
            depth=None,
            data=data,
        )

    def get_events(
        self,
        run_id: str,
        event_type: Optional[str] = None,
        worker_id: Optional[str] = None,
    ) -> List[AuditEvent]:
        """
        Get events for a run with optional filtering.

        Args:
            run_id: Unique identifier for the run
            event_type: Optional event type filter
            worker_id: Optional worker ID filter

        Returns:
            List of matching AuditEvent objects
        """
        events_file = self._get_events_file_for_read(run_id)
        if not events_file.exists():
            return []

        events = []
        with open(events_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = AuditEvent.from_json_line(line)
                    # Apply filters
                    if event_type and event.event_type != event_type:
                        continue
                    if worker_id and event.worker_id != worker_id:
                        continue
                    events.append(event)
                except (json.JSONDecodeError, KeyError):
                    # Skip malformed lines
                    continue

        return events

    def get_run_timeline(self, run_id: str) -> List[AuditEvent]:
        """
        Get all events for a run in chronological order.

        Args:
            run_id: Unique identifier for the run

        Returns:
            List of all AuditEvent objects in chronological order
        """
        events_file = self._get_events_file_for_read(run_id)
        if not events_file.exists():
            return []

        events = []
        with open(events_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = AuditEvent.from_json_line(line)
                    events.append(event)
                except (json.JSONDecodeError, KeyError):
                    continue

        # Sort by timestamp
        events.sort(key=lambda e: e.timestamp)
        return events

        events = []
        with open(events_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = AuditEvent.from_json_line(line)
                    events.append(event)
                except (json.JSONDecodeError, KeyError):
                    continue

        # Sort by timestamp
        events.sort(key=lambda e: e.timestamp)
        return events

    def verify_ledger_integrity(self, run_id: str) -> Tuple[bool, List[str]]:
        """
        Verify the integrity of a run's ledger.

        Checks:
        - graph-events.jsonl (or legacy events.jsonl) is valid JSONL
        - All events have required fields
        - Timestamps are in chronological order
        - No orphaned references

        Args:
            run_id: Unique identifier for the run

        Returns:
            Tuple of (is_valid, list_of_issues)
        """
        issues = []
        events_file = self._get_events_file_for_read(run_id)

        if not events_file.exists():
            issues.append(f"{events_file.name} does not exist")
            return False, issues

        # Check JSONL validity and required fields
        worker_ids = set()
        timestamps = []
        line_num = 0

        with open(events_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                line_num += 1

                try:
                    event = AuditEvent.from_json_line(line)

                    # Check required fields
                    if not event.event_id:
                        issues.append(f"Line {line_num}: Missing event_id")
                    if not event.run_id:
                        issues.append(f"Line {line_num}: Missing run_id")
                    if not event.event_type:
                        issues.append(f"Line {line_num}: Missing event_type")

                    # Track worker IDs
                    if event.worker_id:
                        worker_ids.add(event.worker_id)

                    # Track timestamps
                    timestamps.append(event.timestamp)

                except (json.JSONDecodeError, KeyError, ValueError) as e:
                    issues.append(f"Line {line_num}: Invalid JSON or missing fields: {e}")
                    continue

        # Check chronological order
        for i in range(1, len(timestamps)):
            try:
                prev_dt = datetime.fromisoformat(timestamps[i - 1])
                curr_dt = datetime.fromisoformat(timestamps[i])
                if curr_dt < prev_dt:
                    issues.append(f"Timestamps out of order at index {i}")
            except ValueError:
                issues.append(f"Invalid timestamp format at index {i}")

        # Check if run has any events
        if line_num == 0:
            issues.append("No events recorded")

        return len(issues) == 0, issues

    def compute_run_stats(self, run_id: str) -> Dict[str, Any]:
        """
        Compute statistics for a run.

        Args:
            run_id: Unique identifier for the run

        Returns:
            Dictionary containing run statistics:
            - total_events: Total number of events
            - total_workers: Number of unique workers
            - total_duration: Duration from first to last event (seconds)
            - total_cost_estimate: Estimated cost (placeholder)
            - child_requests_count: Number of child requests
            - approvals_count: Number of approvals granted
            - violations_count: Number of policy violations
        """
        events = self.get_run_timeline(run_id)

        if not events:
            return {
                "total_events": 0,
                "total_workers": 0,
                "total_duration": 0.0,
                "total_cost_estimate": 0.0,
                "child_requests_count": 0,
                "approvals_count": 0,
                "violations_count": 0,
            }

        worker_ids = set()
        child_requests = 0
        approvals = 0
        violations = 0

        for event in events:
            if event.worker_id:
                worker_ids.add(event.worker_id)

            if event.event_type == EventType.CHILD_REQUESTED:
                child_requests += 1
            elif event.event_type == EventType.CHILD_APPROVED:
                approvals += 1
            elif event.event_type == EventType.POLICY_VIOLATION:
                violations += 1

        # Calculate duration
        try:
            first_ts = datetime.fromisoformat(events[0].timestamp)
            last_ts = datetime.fromisoformat(events[-1].timestamp)
            duration = (last_ts - first_ts).total_seconds()
        except (ValueError, IndexError):
            duration = 0.0

        return {
            "total_events": len(events),
            "total_workers": len(worker_ids),
            "total_duration": duration,
            "total_cost_estimate": 0.0,  # Placeholder for cost tracking
            "child_requests_count": child_requests,
            "approvals_count": approvals,
            "violations_count": violations,
        }


# ============================================================================
# Replay Engine Class
# ============================================================================


class ReplayEngine:
    """
    Engine for replaying runs from ledger entries.

    Reconstructs run state from audit events for debugging, analysis,
    and complete run reproduction.
    """

    def __init__(self, ledger: Ledger, runs_dir: Path | str):
        """
        Initialize the replay engine.

        Args:
            ledger: The Ledger instance to read events from
            runs_dir: Base directory containing run subdirectories
        """
        self.ledger = ledger
        self.runs_dir = Path(runs_dir)

    def _get_run_dir(self, run_id: str) -> Path:
        """Get the run-specific directory."""
        return self.runs_dir / run_id

    def _load_graph_spec(self, run_id: str) -> Optional[GraphSpec]:
        """
        Load the graph specification for a run.

        Args:
            run_id: Unique identifier for the run

        Returns:
            GraphSpec if found, None otherwise
        """
        run_dir = self._get_run_dir(run_id)
        graph_spec_file = run_dir / "graph_spec.json"

        if not graph_spec_file.exists():
            return None

        try:
            with open(graph_spec_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return GraphSpec.from_dict(data)
        except (json.JSONDecodeError, KeyError, ValueError):
            return None

    def _load_manifest(self, run_id: str) -> Optional[RunManifest]:
        """
        Load the run manifest.

        Args:
            run_id: Unique identifier for the run

        Returns:
            RunManifest if found, None otherwise
        """
        run_dir = self._get_run_dir(run_id)
        manifest_file = run_dir / "manifest.json"

        if not manifest_file.exists():
            return None

        try:
            with open(manifest_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return RunManifest.from_dict(data)
        except (json.JSONDecodeError, KeyError, ValueError):
            return None

    def replay(self, run_id: str, from_checkpoint: Optional[str] = None) -> Dict[str, Any]:
        """
        Reconstruct run state from ledger entries.

        Args:
            run_id: Unique identifier for the run
            from_checkpoint: Optional checkpoint ID to start from

        Returns:
            Dictionary containing reconstructed state:
            - run_id, graph_spec, manifest
            - packets: list of packets emitted
            - results: list of results recorded
            - delegation_tree: worker delegation hierarchy
    - events: all events in order
            - timeline: human-readable event descriptions
        """
        events = self.ledger.get_run_timeline(run_id)

        graph_spec = self._load_graph_spec(run_id)
        manifest = self._load_manifest(run_id)

        # Build state from events
        packets_emitted = []
        results_recorded = []
        delegation_tree: Dict[str, Any] = {"workers": {}, "relationships": []}
        current_workers = {}

        for event in events:
            if from_checkpoint and event.event_type == EventType.CHECKPOINT_CREATED:
                if event.data.get("checkpoint_id") == from_checkpoint:
                    from_checkpoint = None  # Start replaying from here
                continue

            if event.event_type == EventType.PACKET_EMITTED:
                packet_info = {
                    "event_id": event.event_id,
                    "worker_id": event.worker_id,
                    "depth": event.depth,
                    "action": event.data.get("action"),
                    "timestamp": event.timestamp,
                }
                packets_emitted.append(packet_info)

                if event.worker_id:
                    current_workers[event.worker_id] = {
                        "depth": event.depth,
                        "parent": None,
                        "children": [],
                    }

            elif event.event_type == EventType.RESULT_RECORDED:
                result_info = {
                    "event_id": event.event_id,
                    "worker_id": event.worker_id,
                    "timestamp": event.timestamp,
                    "success": event.data.get("success", True),
                }
                results_recorded.append(result_info)

            elif event.event_type == EventType.CHILD_APPROVED:
                parent_id = event.data.get("parent_worker_id")
                child_id = event.data.get("child_worker_id")

                if parent_id and child_id:
                    delegation_tree["relationships"].append({
                        "parent": parent_id,
                        "child": child_id,
                        "timestamp": event.timestamp,
                    })

                    if parent_id in current_workers:
                        current_workers[parent_id]["children"].append(child_id)
                    if child_id in current_workers:
                        current_workers[child_id]["parent"] = parent_id

        delegation_tree["workers"] = current_workers

        # Build timeline with human-readable descriptions
        timeline = [
            {
                "timestamp": event.timestamp,
                "event_type": event.event_type,
                "description": event.get_human_readable_description(),
                "worker_id": event.worker_id,
            }
            for event in events
        ]

        return {
            "run_id": run_id,
            "graph_spec": graph_spec.to_dict() if graph_spec else None,
            "manifest": manifest.to_dict() if manifest else None,
            "packets_emitted": packets_emitted,
            "results_recorded": results_recorded,
            "delegation_tree": delegation_tree,
            "events": [e.to_dict() for e in events],
            "timeline": timeline,
        }

    def replay_to_file(self, run_id: str, output_path: Path | str) -> None:
        """
        Write a human-readable replay report to a file.

        Args:
            run_id: Unique identifier for the run
            output_path: Path to write the report to
        """
        state = self.replay(run_id)

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        lines = []
        lines.append("=" * 80)
        lines.append(f"AUDIT REPLAY REPORT - Run {run_id}")
        lines.append("=" * 80)
        lines.append("")

        # Run info
        lines.append("RUN INFORMATION")
        lines.append("-" * 40)
        lines.append(f"Run ID: {state['run_id']}")
        lines.append(f"Total Events: {len(state['events'])}")
        lines.append(f"Packets Emitted: {len(state['packets_emitted'])}")
        lines.append(f"Results Recorded: {len(state['results_recorded'])}")
        lines.append("")

        # Timeline
        lines.append("EVENT TIMELINE")
        lines.append("-" * 40)
        for entry in state["timeline"]:
            lines.append(f"[{entry['timestamp']}] {entry['description']}")
        lines.append("")

        # Delegation tree
        lines.append("DELEGATION TREE")
        lines.append("-" * 40)
        tree = state["delegation_tree"]
        for rel in tree.get("relationships", []):
            lines.append(f"  {rel['parent']} -> {rel['child']}")
        lines.append("")

        # Statistics
        stats = self.ledger.compute_run_stats(run_id)
        lines.append("RUN STATISTICS")
        lines.append("-" * 40)
        lines.append(f"Total Events: {stats['total_events']}")
        lines.append(f"Total Workers: {stats['total_workers']}")
        lines.append(f"Duration: {stats['total_duration']:.2f} seconds")
        lines.append(f"Child Requests: {stats['child_requests_count']}")
        lines.append(f"Approvals: {stats['approvals_count']}")
        lines.append(f"Policy Violations: {stats['violations_count']}")
        lines.append("")
        lines.append("=" * 80)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def get_delegation_tree(self, run_id: str) -> Dict[str, Any]:
        """
        Get the worker delegation tree for a run.

        Args:
            run_id: Unique identifier for the run

        Returns:
            Dictionary containing:
            - workers: dict mapping worker_id to worker info (depth, parent, children)
            - relationships: list of parent-child relationships
        """
        events = self.ledger.get_run_timeline(run_id)

        workers: Dict[str, Dict[str, Any]] = {}
        relationships: List[Dict[str, str]] = []

        for event in events:
            if event.worker_id and event.worker_id not in workers:
                workers[event.worker_id] = {
                    "depth": event.depth,
                    "parent": None,
                    "children": [],
                }

            if event.event_type == EventType.CHILD_APPROVED:
                parent_id = event.data.get("parent_worker_id")
                child_id = event.data.get("child_worker_id")

                if parent_id and child_id:
                    relationships.append({"parent": parent_id, "child": child_id})

                    if parent_id in workers and child_id not in workers[parent_id]["children"]:
                        workers[parent_id]["children"].append(child_id)
                    if child_id in workers:
                        workers[child_id]["parent"] = parent_id

        return {"workers": workers, "relationships": relationships}


# ============================================================================
# Async Ledger Operations (Optional async interface)
# ============================================================================


class AsyncLedger(Ledger):
    """
    Async version of the Ledger class for high-performance scenarios.

    Uses aiofiles for non-blocking file operations.
    """

    async def async_log_event(
        self,
        run_id: str,
        event_type: str,
        worker_id: Optional[str],
        depth: Optional[int],
        data: Dict[str, Any],
        masked_fields: Optional[List[str]] = None,
    ) -> str:
        """
        Append an event to the ledger asynchronously.

        Args:
            run_id: Unique identifier for the run
            event_type: Type of event (from EventType enum)
            worker_id: ID of the worker that generated this event
            depth: Depth in the delegation tree
            data: Event-specific payload
            masked_fields: List of field names that were redacted

        Returns:
            The generated event_id
        """
        event_id = str(uuid.uuid4())
        event = AuditEvent(
            event_id=event_id,
            run_id=run_id,
            timestamp=self._now_iso(),
            event_type=event_type,
            worker_id=worker_id,
            depth=depth,
            data=data,
            masked_fields=masked_fields or [],
        )

        run_dir = self._get_run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        events_file = self._get_events_file(run_id)

        async with aiofiles.open(events_file, "a", encoding="utf-8") as f:
            await f.write(event.to_json_line() + "\n")

        return event_id

    async def async_get_events(
        self,
        run_id: str,
        event_type: Optional[str] = None,
        worker_id: Optional[str] = None,
    ) -> List[AuditEvent]:
        """
        Get events for a run asynchronously with optional filtering.

        Args:
            run_id: Unique identifier for the run
            event_type: Optional event type filter
            worker_id: Optional worker ID filter

        Returns:
            List of matching AuditEvent objects
        """
        events_file = self._get_events_file_for_read(run_id)
        if not events_file.exists():
            return []

        events = []
        async with aiofiles.open(events_file, "r", encoding="utf-8") as f:
            async for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = AuditEvent.from_json_line(line)
                    if event_type and event.event_type != event_type:
                        continue
                    if worker_id and event.worker_id != worker_id:
                        continue
                    events.append(event)
                except (json.JSONDecodeError, KeyError):
                    continue

        return events

    async def async_get_run_timeline(self, run_id: str) -> List[AuditEvent]:
        """
        Get all events for a run in chronological order asynchronously.

        Args:
            run_id: Unique identifier for the run

        Returns:
            List of all AuditEvent objects in chronological order
        """
        events_file = self._get_events_file_for_read(run_id)
        if not events_file.exists():
            return []

        events = []
        async with aiofiles.open(events_file, "r", encoding="utf-8") as f:
            async for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = AuditEvent.from_json_line(line)
                    events.append(event)
                except (json.JSONDecodeError, KeyError):
                    continue

        events.sort(key=lambda e: e.timestamp)
        return events