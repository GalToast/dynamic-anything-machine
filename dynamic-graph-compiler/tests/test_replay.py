"""
Tests for Ledger event logging and replay functionality.

These tests verify:
- Basic event logging (append, entries)
- Packet emitted and result recorded events
- Replay reconstruction of run state
- Delegation tree reconstruction
- Ledger integrity checks (gap detection)
"""

import pytest
import json
from pathlib import Path

# Deferred/deferred ledger import — LedgerEntry doesn't exist in ledger.py
# (the actual class is AuditEvent, and the API uses log_event not append)
try:
    from ledger import Ledger, LedgerEntry
except (ImportError, AttributeError):
    Ledger = None
    LedgerEntry = None

# Skip entire module — these tests were written for the M1 Ledger API contract
# (Ledger.append(), LedgerEntry, ledger.entries() with worker_id filter) that was
# replaced by WorkLedger (work_ledger.py) with log_event() and AuditEvent.
# The tests document a different system; they are kept as documentation.
import pytest
pytest.skip("M1 Ledger API tests — see test_work_ledger.py for current WorkLedger tests", allow_module_level=True)

from dynamic_schema import (
    RunManifest,
    WorkerPacket,
    WorkerResult,
    WorkerStatus,
    RunStatus,
    NodeType,
    DelegationPermission,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_runs_dir(tmp_path):
    """Create a temporary runs directory."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    return runs_dir


@pytest.fixture
def ledger(temp_runs_dir):
    """Create a ledger for a test run."""
    run_id = "test-run-replay"
    return Ledger(run_id=run_id, base_dir=str(temp_runs_dir))


@pytest.fixture
def ledger_with_events(temp_runs_dir):
    """Create a ledger with several events already logged."""
    run_id = "test-run-events"
    ledger = Ledger(run_id=run_id, base_dir=str(temp_runs_dir))

    # Log some initial events
    ledger.append(event_type="run_started", worker_id=None, data={
        "run_id": run_id,
        "profile": "default",
        "objective": "Test objective",
    })

    ledger.append(event_type="node_dispatched", worker_id="planner-1", data={
        "node_id": "planner-1",
        "role": "planner",
    })

    ledger.append(event_type="packet_emitted", worker_id="planner-1", data={
        "worker_id": "planner-1",
        "role": "planner",
        "depth": 0,
    })

    ledger.append(event_type="worker_result", worker_id="planner-1", data={
        "worker_id": "planner-1",
        "status": "success",
        "output": "Plan output",
    })

    return ledger


# ---------------------------------------------------------------------------
# Basic event logging
# ---------------------------------------------------------------------------

def test_ledger_log_event(tmp_path):
    """Test basic event logging."""
    run_id = "test-basic-event"
    ledger = Ledger(run_id=run_id, base_dir=str(tmp_path))

    entry = ledger.append(
        event_type="test_event",
        worker_id=None,
        data={"key": "value"},
    )

    assert entry.run_id == run_id
    assert entry.event_type == "test_event"
    assert entry.timestamp is not None
    assert entry.data == {"key": "value"}

    # Verify the file was written
    ledger_path = tmp_path / run_id / "ledger.jsonl"
    assert ledger_path.exists()

    # Read back and verify
    entries = ledger.entries()
    assert len(entries) == 1
    assert entries[0].event_type == "test_event"


def test_ledger_log_event_with_worker_id(tmp_path):
    """Test logging an event associated with a worker."""
    run_id = "test-worker-event"
    ledger = Ledger(run_id=run_id, base_dir=str(tmp_path))

    entry = ledger.append(
        event_type="worker_started",
        worker_id="worker-1",
        data={"role": "planner"},
    )

    assert entry.worker_id == "worker-1"
    assert entry.event_type == "worker_started"

    # Filter by worker_id
    worker_entries = ledger.entries(worker_id="worker-1")
    assert len(worker_entries) == 1
    assert worker_entries[0].worker_id == "worker-1"

    # Filter by event_type
    event_entries = ledger.entries(event_type="worker_started")
    assert len(event_entries) == 1


def test_ledger_multiple_events(tmp_path):
    """Test logging multiple events."""
    run_id = "test-multi-event"
    ledger = Ledger(run_id=run_id, base_dir=str(tmp_path))

    for i in range(5):
        ledger.append(
            event_type=f"event_{i}",
            worker_id=f"worker-{i}",
            data={"index": i},
        )

    entries = ledger.entries()
    assert len(entries) == 5

    # All entries should have sequential timestamps
    timestamps = [e.timestamp for e in entries]
    assert timestamps == sorted(timestamps)


def test_ledger_log_packet_emitted(tmp_path):
    """Test packet emitted event logging."""
    run_id = "test-packet-emitted"
    ledger = Ledger(run_id=run_id, base_dir=str(tmp_path))

    packet_data = {
        "run_id": run_id,
        "worker_id": "worker-planner-1",
        "parent_worker_id": None,
        "depth": 0,
        "role": "planner",
        "objective": "Create plan",
        "task_prompt": "Decompose the task",
        "allowed_tools": ["read_file", "grep"],
        "forbidden_tools": [],
        "owned_paths": ["/workspace"],
        "forbidden_paths": [],
        "created_at": "2026-01-01T00:00:00Z",
    }

    entry = ledger.append(
        event_type="packet_emitted",
        worker_id="worker-planner-1",
        data=packet_data,
    )

    assert entry.event_type == "packet_emitted"
    assert entry.data["worker_id"] == "worker-planner-1"
    assert entry.data["role"] == "planner"

    # Verify we can filter by event type
    packet_entries = ledger.entries(event_type="packet_emitted")
    assert len(packet_entries) == 1
    assert packet_entries[0].data["depth"] == 0


def test_ledger_log_result_recorded(tmp_path):
    """Test result recorded event logging."""
    run_id = "test-result-recorded"
    ledger = Ledger(run_id=run_id, base_dir=str(tmp_path))

    result_data = {
        "run_id": run_id,
        "worker_id": "worker-1",
        "parent_worker_id": None,
        "status": "success",
        "output": "Task completed successfully",
        "duration_seconds": 2.5,
        "cost_estimate": 0.05,
        "children": [],
        "artifacts": [],
        "critic_findings": None,
        "verification_status": "pass",
        "completed_at": "2026-01-01T00:01:00Z",
    }

    entry = ledger.append(
        event_type="result_recorded",
        worker_id="worker-1",
        data=result_data,
    )

    assert entry.event_type == "result_recorded"
    assert entry.data["status"] == "success"
    assert entry.data["worker_id"] == "worker-1"

    # Filter and verify
    result_entries = ledger.entries(event_type="result_recorded")
    assert len(result_entries) == 1
    assert result_entries[0].data["verification_status"] == "pass"


def test_ledger_log_result_with_children(tmp_path):
    """Test result recorded with child workers."""
    run_id = "test-result-children"
    ledger = Ledger(run_id=run_id, base_dir=str(tmp_path))

    parent_result = {
        "run_id": run_id,
        "worker_id": "parent-worker",
        "status": "success",
        "children": ["child-worker-1", "child-worker-2"],
        "output": "Parent output",
    }

    ledger.append(
        event_type="result_recorded",
        worker_id="parent-worker",
        data=parent_result,
    )

    # Log child results
    for child_id in ["child-worker-1", "child-worker-2"]:
        ledger.append(
            event_type="result_recorded",
            worker_id=child_id,
            data={
                "run_id": run_id,
                "worker_id": child_id,
                "status": "success",
                "parent_worker_id": "parent-worker",
                "children": [],
            },
        )

    entries = ledger.entries()
    assert len(entries) == 3

    parent_entries = ledger.entries(worker_id="parent-worker")
    assert len(parent_entries) == 1
    assert parent_entries[0].data["children"] == ["child-worker-1", "child-worker-2"]


# ---------------------------------------------------------------------------
# Checkpoint and replay
# ---------------------------------------------------------------------------

def test_ledger_create_checkpoint(tmp_path):
    """Test checkpoint creation."""
    run_id = "test-checkpoint"
    ledger = Ledger(run_id=run_id, base_dir=str(tmp_path))

    # Add some events
    ledger.append(event_type="event-a", data={"seq": 1})
    ledger.append(event_type="event-b", data={"seq": 2})

    checkpoint_id = ledger.create_checkpoint(label="after-event-b")

    assert checkpoint_id.startswith("cp-")

    # Verify checkpoint event was logged
    entries = ledger.entries(event_type="checkpoint")
    assert len(entries) == 1
    assert entries[0].data["label"] == "after-event-b"
    assert entries[0].data["checkpoint_id"] == checkpoint_id


def test_ledger_replay_reconstructs_state(tmp_path):
    """Test that replay reconstructs run state."""
    run_id = "test-replay-state"
    ledger = Ledger(run_id=run_id, base_dir=str(tmp_path))

    # Log a sequence of events
    events = [
        ("run_started", {"status": "running"}),
        ("node_dispatched", {"node_id": "planner-1", "role": "planner"}),
        ("packet_emitted", {"worker_id": "worker-1", "depth": 0}),
        ("worker_result", {"worker_id": "worker-1", "status": "success"}),
        ("node_dispatched", {"node_id": "worker-1", "role": "worker"}),
        ("packet_emitted", {"worker_id": "worker-2", "depth": 1}),
        ("worker_result", {"worker_id": "worker-2", "status": "success"}),
        ("run_completed", {"status": "completed"}),
    ]

    for event_type, data in events:
        ledger.append(event_type=event_type, data=data)

    # Replay all entries
    replayed = ledger.replay()

    assert len(replayed) == len(events)
    assert replayed[0].event_type == "run_started"
    assert replayed[-1].event_type == "run_completed"

    # Verify state progression
    statuses = [e.data.get("status") for e in replayed if "status" in e.data]
    assert statuses[-1] == "completed"


def test_ledger_replay_from_checkpoint(tmp_path):
    """Test replay from a specific checkpoint."""
    run_id = "test-replay-checkpoint"
    ledger = Ledger(run_id=run_id, base_dir=str(tmp_path))

    # Add events before checkpoint
    ledger.append(event_type="event-1", data={"seq": 1})
    ledger.append(event_type="event-2", data={"seq": 2})

    checkpoint_id = ledger.create_checkpoint(label="before-critical")

    # Add events after checkpoint
    ledger.append(event_type="event-3", data={"seq": 3})
    ledger.append(event_type="event-4", data={"seq": 4})

    # Replay from checkpoint
    replayed = ledger.replay(from_checkpoint=checkpoint_id)

    # Should get events after checkpoint
    assert len(replayed) == 2
    assert replayed[0].data["seq"] == 3
    assert replayed[-1].data["seq"] == 4


def test_replay_delegation_tree(tmp_path):
    """Test delegation tree reconstruction from ledger entries."""
    run_id = "test-delegation-tree"
    ledger = Ledger(run_id=run_id, base_dir=str(tmp_path))

    # Build a delegation tree: root -> child1, child2 -> grandchild
    ledger.append(event_type="run_started", data={"run_id": run_id})

    # Root worker dispatched
    ledger.append(event_type="packet_emitted", worker_id="root-worker", data={
        "worker_id": "root-worker",
        "depth": 0,
        "role": "planner",
        "parent_worker_id": None,
    })

    ledger.append(event_type="worker_result", worker_id="root-worker", data={
        "worker_id": "root-worker",
        "status": "success",
        "children": ["child-worker-1", "child-worker-2"],
    })

    # Child 1 dispatched
    ledger.append(event_type="packet_emitted", worker_id="child-worker-1", data={
        "worker_id": "child-worker-1",
        "depth": 1,
        "role": "worker",
        "parent_worker_id": "root-worker",
    })

    ledger.append(event_type="worker_result", worker_id="child-worker-1", data={
        "worker_id": "child-worker-1",
        "parent_worker_id": "root-worker",
        "status": "success",
        "children": ["grandchild-worker"],
    })

    # Child 2 dispatched
    ledger.append(event_type="packet_emitted", worker_id="child-worker-2", data={
        "worker_id": "child-worker-2",
        "depth": 1,
        "role": "worker",
        "parent_worker_id": "root-worker",
    })

    ledger.append(event_type="worker_result", worker_id="child-worker-2", data={
        "worker_id": "child-worker-2",
        "parent_worker_id": "root-worker",
        "status": "success",
        "children": [],
    })

    # Grandchild dispatched
    ledger.append(event_type="packet_emitted", worker_id="grandchild-worker", data={
        "worker_id": "grandchild-worker",
        "depth": 2,
        "role": "worker",
        "parent_worker_id": "child-worker-1",
    })

    ledger.append(event_type="worker_result", worker_id="grandchild-worker", data={
        "worker_id": "grandchild-worker",
        "parent_worker_id": "child-worker-1",
        "status": "success",
        "children": [],
    })

    # Replay and reconstruct tree
    replayed = ledger.replay()

    # Build parent->children map from events
    parent_map: dict[str, list[str]] = {}
    worker_depths: dict[str, int] = {}

    for entry in replayed:
        if entry.event_type == "packet_emitted":
            wid = entry.data.get("worker_id")
            parent = entry.data.get("parent_worker_id")
            depth = entry.data.get("depth", 0)
            worker_depths[wid] = depth
            if parent:
                if parent not in parent_map:
                    parent_map[parent] = []
                parent_map[parent].append(wid)

    # Verify tree structure
    assert "root-worker" in worker_depths
    assert worker_depths["root-worker"] == 0

    assert "child-worker-1" in parent_map["root-worker"]
    assert "child-worker-2" in parent_map["root-worker"]

    assert "grandchild-worker" in parent_map["child-worker-1"]


def test_replay_worker_chain(tmp_path):
    """Test that replay correctly orders a chain of worker delegations."""
    run_id = "test-worker-chain"
    ledger = Ledger(run_id=run_id, base_dir=str(tmp_path))

    # Chain: root -> child -> grandchild
    chain = [
        ("root", None, 0),
        ("child-1", "root", 1),
        ("grandchild", "child-1", 2),
    ]

    for worker_id, parent, depth in chain:
        ledger.append(event_type="packet_emitted", worker_id=worker_id, data={
            "worker_id": worker_id,
            "parent_worker_id": parent,
            "depth": depth,
        })
        ledger.append(event_type="worker_result", worker_id=worker_id, data={
            "worker_id": worker_id,
            "parent_worker_id": parent,
            "status": "success",
            "children": [],
        })

    replayed = ledger.replay()
    packet_events = [e for e in replayed if e.event_type == "packet_emitted"]

    assert len(packet_events) == 3
    assert packet_events[0].data["worker_id"] == "root"
    assert packet_events[0].data["depth"] == 0
    assert packet_events[1].data["worker_id"] == "child-1"
    assert packet_events[1].data["depth"] == 1
    assert packet_events[2].data["worker_id"] == "grandchild"
    assert packet_events[2].data["depth"] == 2


# ---------------------------------------------------------------------------
# Ledger integrity
# ---------------------------------------------------------------------------

def test_ledger_integrity_valid(tmp_path):
    """Test ledger integrity check with valid events."""
    run_id = "test-integrity-valid"
    ledger = Ledger(run_id=run_id, base_dir=str(tmp_path))

    # Log a complete sequence of events
    ledger.append(event_type="run_started", data={"run_id": run_id})
    ledger.append(event_type="node_dispatched", worker_id="node-1", data={"node_id": "node-1"})
    ledger.append(event_type="packet_emitted", worker_id="node-1", data={"worker_id": "node-1"})
    ledger.append(event_type="worker_result", worker_id="node-1", data={"worker_id": "node-1", "status": "success"})
    ledger.append(event_type="run_completed", data={"status": "completed"})

    entries = ledger.entries()

    # Verify we can read all entries
    assert len(entries) == 5

    # Verify each entry has required fields
    for entry in entries:
        assert entry.run_id == run_id
        assert entry.event_type is not None
        assert entry.timestamp is not None

    # Verify event sequence is intact
    event_types = [e.event_type for e in entries]
    assert event_types == ["run_started", "node_dispatched", "packet_emitted", "worker_result", "run_completed"]


def test_ledger_integrity_detects_gaps(tmp_path):
    """Test ledger detects missing events (integrity check)."""
    run_id = "test-integrity-gaps"
    ledger = Ledger(run_id=run_id, base_dir=str(tmp_path))

    # Log some events
    ledger.append(event_type="run_started", data={"run_id": run_id})
    ledger.append(event_type="node_dispatched", worker_id="node-1", data={"node_id": "node-1"})
    # Simulate a gap: missing packet_emitted for node-1
    ledger.append(event_type="worker_result", worker_id="node-1", data={
        "worker_id": "node-1",
        "status": "success"
    })

    entries = ledger.entries()

    # The gap is detectable by observing sequence
    packet_emitted = [e for e in entries if e.event_type == "packet_emitted"]
    worker_results = [e for e in entries if e.event_type == "worker_result"]

    # There's a worker_result without a corresponding packet_emitted
    # This is the "gap" - we have a result for node-1 but no packet_emitted
    assert len(packet_emitted) == 0  # No packet_emitted logged
    assert len(worker_results) == 1  # But we have a result

    # Integrity check: for each worker_result, there should be a corresponding packet_emitted
    for result in worker_results:
        wid = result.data.get("worker_id")
        matching_packets = [e for e in entries if e.event_type == "packet_emitted" and e.data.get("worker_id") == wid]
        assert len(matching_packets) > 0, f"Gap detected: worker_result for {wid} has no packet_emitted"


def test_ledger_integrity_checker_function(tmp_path):
    """Test a dedicated integrity check function."""
    run_id = "test-integrity-checker"
    ledger = Ledger(run_id=run_id, base_dir=str(tmp_path))

    # Complete run
    ledger.append(event_type="run_started", data={"run_id": run_id})
    ledger.append(event_type="packet_emitted", worker_id="worker-1", data={
        "worker_id": "worker-1",
    })
    ledger.append(event_type="worker_result", worker_id="worker-1", data={
        "worker_id": "worker-1",
        "status": "success",
    })
    ledger.append(event_type="run_completed", data={})

    # Integrity check: each worker_result must have preceding packet_emitted
    entries = ledger.entries()
    gaps: list[str] = []

    for entry in entries:
        if entry.event_type == "worker_result":
            wid = entry.data.get("worker_id")
            # Find preceding packet_emitted for this worker
            preceding = [e for e in entries if e.event_type == "packet_emitted" and e.data.get("worker_id") == wid]
            if not preceding:
                gaps.append(f"worker_result for {wid} has no preceding packet_emitted")

    assert len(gaps) == 0, f"Integrity gaps found: {gaps}"


# ---------------------------------------------------------------------------
# Replay to file
# ---------------------------------------------------------------------------

def test_replay_to_file(tmp_path):
    """Test replay report generation to file."""
    run_id = "test-replay-file"
    ledger = Ledger(run_id=run_id, base_dir=str(tmp_path))

    # Log events
    ledger.append(event_type="run_started", data={
        "run_id": run_id,
        "profile": "default",
    })
    ledger.append(event_type="packet_emitted", worker_id="worker-1", data={
        "worker_id": "worker-1",
        "role": "planner",
        "depth": 0,
    })
    ledger.append(event_type="worker_result", worker_id="worker-1", data={
        "worker_id": "worker-1",
        "status": "success",
        "output": "Plan output",
    })
    ledger.append(event_type="packet_emitted", worker_id="worker-2", data={
        "worker_id": "worker-2",
        "role": "worker",
        "depth": 1,
        "parent_worker_id": "worker-1",
    })
    ledger.append(event_type="worker_result", worker_id="worker-2", data={
        "worker_id": "worker-2",
        "status": "success",
        "parent_worker_id": "worker-1",
    })

    # Replay and generate report
    replayed = ledger.replay()

    # Build a simple text report
    report_lines = [f"Run ID: {run_id}", f"Total Events: {len(replayed)}", ""]
    report_lines.append("Event Log:")
    for i, entry in enumerate(replayed):
        report_lines.append(f"  {i+1}. [{entry.timestamp}] {entry.event_type} (worker={entry.worker_id})")

    report_path = tmp_path / run_id / "replay_report.txt"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(report_lines), encoding="utf-8")

    assert report_path.exists()
    content = report_path.read_text()
    assert "Run ID: test-replay-file" in content
    assert "Total Events: 5" in content
    assert "packet_emitted" in content
    assert "worker_result" in content


def test_replay_json_report(tmp_path):
    """Test replay generates a JSON report."""
    run_id = "test-replay-json"
    ledger = Ledger(run_id=run_id, base_dir=str(tmp_path))

    ledger.append(event_type="run_started", data={"run_id": run_id})
    ledger.append(event_type="packet_emitted", worker_id="worker-1", data={
        "worker_id": "worker-1",
        "role": "planner",
    })
    ledger.append(event_type="worker_result", worker_id="worker-1", data={
        "worker_id": "worker-1",
        "status": "success",
    })

    replayed = ledger.replay()

    # Export as JSON
    report_data = {
        "run_id": run_id,
        "event_count": len(replayed),
        "events": [
            {
                "timestamp": e.timestamp,
                "event_type": e.event_type,
                "worker_id": e.worker_id,
                "data": e.data,
            }
            for e in replayed
        ],
    }

    report_path = tmp_path / run_id / "replay_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report_data, indent=2), encoding="utf-8")

    assert report_path.exists()
    loaded = json.loads(report_path.read_text())
    assert loaded["run_id"] == run_id
    assert loaded["event_count"] == 3


def test_ledger_empty_run(tmp_path):
    """Test ledger with no events (empty run)."""
    run_id = "test-empty-run"
    ledger = Ledger(run_id=run_id, base_dir=str(tmp_path))

    entries = ledger.entries()
    assert len(entries) == 0

    replayed = ledger.replay()
    assert len(replayed) == 0


def test_ledger_pagination_filter(tmp_path):
    """Test filtering ledger entries by worker_id."""
    run_id = "test-filter"
    ledger = Ledger(run_id=run_id, base_dir=str(tmp_path))

    # Log events for multiple workers
    for worker_id in ["worker-a", "worker-b", "worker-c"]:
        for i in range(3):
            ledger.append(
                event_type="task_event",
                worker_id=worker_id,
                data={"seq": i},
            )

    # Filter by worker
    worker_a_entries = ledger.entries(worker_id="worker-a")
    assert len(worker_a_entries) == 3

    worker_b_entries = ledger.entries(worker_id="worker-b")
    assert len(worker_b_entries) == 3

    # Filter by event_type
    task_events = ledger.entries(event_type="task_event")
    assert len(task_events) == 9

    # Combined filter
    worker_a_task_events = ledger.entries(event_type="task_event", worker_id="worker-a")
    assert len(worker_a_task_events) == 3


def test_ledger_events_persist_across_reads(tmp_path):
    """Test that ledger entries persist across multiple reads."""
    run_id = "test-persist"
    ledger = Ledger(run_id=run_id, base_dir=str(tmp_path))

    ledger.append(event_type="event-1", data={"n": 1})
    ledger.append(event_type="event-2", data={"n": 2})

    # First read
    entries1 = ledger.entries()
    assert len(entries1) == 2

    # Second read (new ledger instance)
    ledger2 = Ledger(run_id=run_id, base_dir=str(tmp_path))
    entries2 = ledger2.entries()
    assert len(entries2) == 2

    # Append more events with first ledger
    ledger.append(event_type="event-3", data={"n": 3})

    # Verify both ledgers see the new event
    entries1_updated = ledger.entries()
    assert len(entries1_updated) == 3

    entries2_updated = ledger2.entries()
    assert len(entries2_updated) == 3


def test_ledger_creates_run_directory(tmp_path):
    """Test that ledger creates its run directory on first append."""
    run_id = "test-dir-create"
    ledger = Ledger(run_id=run_id, base_dir=str(tmp_path))

    assert not (tmp_path / run_id).exists()

    ledger.append(event_type="first_event", data={})

    assert (tmp_path / run_id).exists()
    assert (tmp_path / run_id / "ledger.jsonl").exists()


def test_ledger_entry_structure():
    """Test that LedgerEntry has all expected fields."""
    entry = LedgerEntry(
        timestamp="2026-01-01T00:00:00Z",
        event_type="test",
        run_id="run-123",
        worker_id="worker-1",
        data={"key": "value"},
        checkpoint_id="cp-1",
    )

    assert entry.timestamp == "2026-01-01T00:00:00Z"
    assert entry.event_type == "test"
    assert entry.run_id == "run-123"
    assert entry.worker_id == "worker-1"
    assert entry.data == {"key": "value"}
    assert entry.checkpoint_id == "cp-1"


def test_ledger_replay_order(tmp_path):
    """Test that replay returns entries in chronological order."""
    run_id = "test-order"
    ledger = Ledger(run_id=run_id, base_dir=str(tmp_path))

    import time
    event_types = ["a", "b", "c", "d", "e"]

    for et in event_types:
        ledger.append(event_type=et, data={})

    replayed = ledger.replay()

    # Should be in same order as appended
    assert [e.event_type for e in replayed] == event_types


def test_ledger_replay_with_data_integrity(tmp_path):
    """Test that replay preserves data integrity of event payloads."""
    run_id = "test-data-integrity"
    ledger = Ledger(run_id=run_id, base_dir=str(tmp_path))

    complex_data = {
        "nested": {"key": "value", "list": [1, 2, 3]},
        "numeric": 42.5,
        "boolean": True,
        "null_field": None,
    }

    ledger.append(event_type="complex_event", data=complex_data)

    replayed = ledger.replay()
    assert len(replayed) == 1
    assert replayed[0].data["nested"]["key"] == "value"
    assert replayed[0].data["nested"]["list"] == [1, 2, 3]
    assert replayed[0].data["numeric"] == 42.5
    assert replayed[0].data["null_field"] is None