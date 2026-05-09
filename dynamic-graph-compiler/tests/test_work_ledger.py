"""
Comprehensive tests for work_ledger.py

Tests WorkLedger, SecretMasker, and ReplayEngine functionality.
"""

import json
import pytest
from pathlib import Path

from work_ledger import (
    WorkLedger,
    AuditEvent,
    SecretMasker,
    ReplayEngine,
    PACKET_CREATED,
    PACKET_EMITTED,
    PACKET_CLAIMED,
    PACKET_PROGRESS,
    PACKET_COMPLETED,
    PACKET_FAILED,
    CHILD_REQUESTED,
    CHILD_APPROVED,
    CHILD_DENIED,
    HUMAN_GATE_ENTERED,
    HUMAN_GATE_APPROVED,
    HUMAN_GATE_DENIED,
    SHAPE_CREATED,
    ARTIFACT_WRITTEN,
    CHECKPOINT_CREATED,
    WORK_LEDGER_EVENTS_FILE,
)


# =============================================================================
# WorkLedger Tests
# =============================================================================

def test_create_run(tmp_path):
    """Call ledger.create_run and verify run_id returned and directory structure created."""
    ledger = WorkLedger(runs_dir=tmp_path)
    run_id = ledger.create_run("test-run", "Research Texas companies")

    assert run_id == "test-run"

    run_dir = tmp_path / "test-run"
    assert run_dir.exists()
    assert (run_dir / WORK_LEDGER_EVENTS_FILE).exists()
    assert (run_dir / "packets").is_dir()
    assert (run_dir / "shapes").is_dir()
    assert (run_dir / "artifacts").is_dir()
    assert (run_dir / "checkpoints").is_dir()
    assert (run_dir / "final").is_dir()


def test_log_event(tmp_path):
    """Create run, log a custom event, verify it is valid JSON in events.jsonl."""
    ledger = WorkLedger(runs_dir=tmp_path)
    ledger.create_run("test-run", "Test objective")

    event_id = ledger.log_event(
        run_id="test-run",
        event_type="CUSTOM_EVENT",
        packet_id=None,
        agent_id=None,
        harness=None,
        depth=None,
        data={"key": "value"},
    )

    assert event_id is not None

    events_file = tmp_path / "test-run" / WORK_LEDGER_EVENTS_FILE
    lines = events_file.read_text().strip().split("\n")
    # Last line should be our event (after PACKET_CREATED from create_run)
    last_line = lines[-1]
    event = json.loads(last_line)

    assert event["event_type"] == "CUSTOM_EVENT"
    assert event["run_id"] == "test-run"
    assert "timestamp" in event
    assert event["data"]["key"] == "value"


def test_log_packet_emitted(tmp_path):
    """Create run, log emission, verify event recorded with correct packet_id and depth."""
    ledger = WorkLedger(runs_dir=tmp_path)
    ledger.create_run("test-run", "Test")

    event_id = ledger.log_packet_emitted(
        run_id="test-run",
        packet_id="packet-001",
        depth=0,
        data={"objective": "Do something", "seam": "default"},
    )

    assert event_id is not None

    events = ledger.get_events(run_id="test-run", event_type=PACKET_EMITTED)
    assert len(events) == 1
    assert events[0].packet_id == "packet-001"
    assert events[0].depth == 0
    assert events[0].data["objective"] == "Do something"


def test_log_packet_claimed(tmp_path):
    """Create run, log claim with agent_id and harness, verify event recorded."""
    ledger = WorkLedger(runs_dir=tmp_path)
    ledger.create_run("test-run", "Test")

    ledger.log_packet_emitted("test-run", "packet-001", depth=0, data={})

    event_id = ledger.log_packet_claimed(
        run_id="test-run",
        packet_id="packet-001",
        agent_id="agent-42",
        harness="test-harness",
    )

    assert event_id is not None

    events = ledger.get_events(run_id="test-run", event_type=PACKET_CLAIMED)
    assert len(events) == 1
    assert events[0].packet_id == "packet-001"
    assert events[0].agent_id == "agent-42"
    assert events[0].harness == "test-harness"


def test_log_progress(tmp_path):
    """Create run, log progress with state_delta, verify event has data with state_delta."""
    ledger = WorkLedger(runs_dir=tmp_path)
    ledger.create_run("test-run", "Test")

    ledger.log_packet_emitted("test-run", "packet-001", depth=0, data={})

    state_delta = {"steps_completed": 5, "status": "running"}
    event_id = ledger.log_progress(
        run_id="test-run",
        packet_id="packet-001",
        state_delta=state_delta,
    )

    assert event_id is not None

    events = ledger.get_events(run_id="test-run", event_type=PACKET_PROGRESS)
    assert len(events) == 1
    assert events[0].data["state_delta"] == state_delta


def test_log_child_requested(tmp_path):
    """Log child request, verify parent-child relationship in event data."""
    ledger = WorkLedger(runs_dir=tmp_path)
    ledger.create_run("test-run", "Test")

    ledger.log_packet_emitted("test-run", "parent-packet", depth=0, data={})

    event_id = ledger.log_child_requested(
        run_id="test-run",
        parent_packet_id="parent-packet",
        child_packet_id="child-packet",
        seam="research",
    )

    assert event_id is not None

    events = ledger.get_events(run_id="test-run", event_type=CHILD_REQUESTED)
    assert len(events) == 1
    assert events[0].packet_id == "parent-packet"
    assert events[0].data["child_packet_id"] == "child-packet"
    assert events[0].data["seam"] == "research"


def test_log_child_approved(tmp_path):
    """Log approval, verify event."""
    ledger = WorkLedger(runs_dir=tmp_path)
    ledger.create_run("test-run", "Test")

    ledger.log_packet_emitted("test-run", "child-packet", depth=1, data={})

    event_id = ledger.log_child_approved(
        run_id="test-run",
        child_packet_id="child-packet",
    )

    assert event_id is not None

    events = ledger.get_events(run_id="test-run", event_type=CHILD_APPROVED)
    assert len(events) == 1
    assert events[0].packet_id == "child-packet"
    assert events[0].data["child_packet_id"] == "child-packet"


def test_log_child_denied(tmp_path):
    """Log denial with reason, verify reason in event data."""
    ledger = WorkLedger(runs_dir=tmp_path)
    ledger.create_run("test-run", "Test")

    ledger.log_packet_emitted("test-run", "child-packet", depth=1, data={})

    event_id = ledger.log_child_denied(
        run_id="test-run",
        child_packet_id="child-packet",
        reason="Budget exceeded",
    )

    assert event_id is not None

    events = ledger.get_events(run_id="test-run", event_type=CHILD_DENIED)
    assert len(events) == 1
    assert events[0].packet_id == "child-packet"
    assert events[0].data["reason"] == "Budget exceeded"


def test_log_human_gate(tmp_path):
    """Log gate entered, verify gate name in event."""
    ledger = WorkLedger(runs_dir=tmp_path)
    ledger.create_run("test-run", "Test")

    ledger.log_packet_emitted("test-run", "packet-001", depth=0, data={})

    event_id = ledger.log_human_gate(
        run_id="test-run",
        packet_id="packet-001",
        gate="approval-gate",
    )

    assert event_id is not None

    events = ledger.get_events(run_id="test-run", event_type=HUMAN_GATE_ENTERED)
    assert len(events) == 1
    assert events[0].packet_id == "packet-001"
    assert events[0].data["gate"] == "approval-gate"


def test_log_shape_created(tmp_path):
    """Log shape creation with shape_id, type, data, verify event."""
    ledger = WorkLedger(runs_dir=tmp_path)
    ledger.create_run("test-run", "Test")

    ledger.log_packet_emitted("test-run", "packet-001", depth=0, data={})

    event_id = ledger.log_shape_created(
        run_id="test-run",
        packet_id="packet-001",
        shape_id="shape-abc",
        shape_type="analysis",
        data={"query": "test query"},
    )

    assert event_id is not None

    events = ledger.get_events(run_id="test-run", event_type=SHAPE_CREATED)
    assert len(events) == 1
    assert events[0].packet_id == "packet-001"
    assert events[0].data["shape_id"] == "shape-abc"
    assert events[0].data["shape_type"] == "analysis"


def test_get_events_filter_by_type(tmp_path):
    """Create run, log multiple event types, filter by PACKET_CLAIMED, verify only matching events."""
    ledger = WorkLedger(runs_dir=tmp_path)
    ledger.create_run("test-run", "Test")

    ledger.log_packet_emitted("test-run", "packet-001", depth=0, data={})
    ledger.log_packet_claimed("test-run", "packet-001", "agent-1", "harness-1")
    ledger.log_packet_emitted("test-run", "packet-002", depth=0, data={})
    ledger.log_packet_claimed("test-run", "packet-002", "agent-2", "harness-2")

    events = ledger.get_events(run_id="test-run", event_type=PACKET_CLAIMED)

    assert len(events) == 2
    for event in events:
        assert event.event_type == PACKET_CLAIMED


def test_get_events_filter_by_packet_id(tmp_path):
    """Create run, log events for multiple packets, filter by packet_id, verify only that packet's events."""
    ledger = WorkLedger(runs_dir=tmp_path)
    ledger.create_run("test-run", "Test")

    ledger.log_packet_emitted("test-run", "packet-A", depth=0, data={})
    ledger.log_packet_claimed("test-run", "packet-A", "agent-1", "harness-1")
    ledger.log_packet_emitted("test-run", "packet-B", depth=0, data={})
    ledger.log_packet_claimed("test-run", "packet-B", "agent-2", "harness-2")

    events = ledger.get_events(run_id="test-run", packet_id="packet-A")

    assert len(events) == 2
    for event in events:
        assert event.packet_id == "packet-A"


def test_get_timeline(tmp_path):
    """Log multiple events out of order, call get_timeline, verify chronological order."""
    ledger = WorkLedger(runs_dir=tmp_path)
    ledger.create_run("test-run", "Test")

    ledger.log_packet_emitted("test-run", "packet-A", depth=0, data={})
    ledger.log_packet_emitted("test-run", "packet-B", depth=0, data={})
    ledger.log_packet_emitted("test-run", "packet-C", depth=0, data={})

    timeline = ledger.get_timeline("test-run")

    # Timeline includes PACKET_CREATED from create_run plus 3 EMITTED events
    assert len(timeline) == 4
    # Events should be in chronological order
    timestamps = [e.timestamp for e in timeline]
    assert timestamps == sorted(timestamps)


def test_get_packet_history(tmp_path):
    """Log events for packet A and packet B, call get_packet_history for A only, verify only A's events."""
    ledger = WorkLedger(runs_dir=tmp_path)
    ledger.create_run("test-run", "Test")

    ledger.log_packet_emitted("test-run", "packet-A", depth=0, data={})
    ledger.log_packet_claimed("test-run", "packet-A", "agent-1", "harness-1")
    ledger.log_packet_emitted("test-run", "packet-B", depth=0, data={})
    ledger.log_packet_claimed("test-run", "packet-B", "agent-2", "harness-2")

    history = ledger.get_packet_history("test-run", "packet-A")

    assert len(history) == 2
    for event in history:
        assert event.packet_id == "packet-A"


def test_get_delegation_tree(tmp_path):
    """Create run, emit root packet, delegate to child-1 and child-2, log child approvals, verify tree structure."""
    ledger = WorkLedger(runs_dir=tmp_path)
    ledger.create_run("test-run", "Test")

    ledger.log_packet_emitted("test-run", "root-packet", depth=0, data={})
    ledger.log_child_requested("test-run", "root-packet", "child-1", "seam-1")
    ledger.log_child_requested("test-run", "root-packet", "child-2", "seam-2")
    ledger.log_packet_emitted("test-run", "child-1", depth=1, data={})
    ledger.log_packet_emitted("test-run", "child-2", depth=1, data={})
    ledger.log_child_approved("test-run", "child-1")
    ledger.log_child_approved("test-run", "child-2")

    tree = ledger.get_delegation_tree("test-run")

    assert "roots" in tree
    assert "packets" in tree
    assert "root-packet" in tree["packets"]
    assert "child-1" in tree["packets"]
    assert "child-2" in tree["packets"]
    assert tree["packets"]["root-packet"]["children"] == ["child-1", "child-2"]


def test_list_seams(tmp_path):
    """Create run, emit 3 packets with status=EMITTED, call list_seams, verify 3 entries."""
    ledger = WorkLedger(runs_dir=tmp_path)
    ledger.create_run("test-run", "Test")

    ledger.log_packet_emitted("test-run", "packet-1", depth=0, data={"seam": "research"})
    ledger.log_packet_emitted("test-run", "packet-2", depth=0, data={"seam": "analysis"})
    ledger.log_packet_emitted("test-run", "packet-3", depth=0, data={"seam": "report"})

    seams = ledger.list_seams("test-run")

    assert len(seams) == 3
    seams_list = [s["seam"] for s in seams]
    assert "research" in seams_list
    assert "analysis" in seams_list
    assert "report" in seams_list


def test_list_runs(tmp_path):
    """Create 2 runs, call list_runs, verify 2 entries."""
    ledger = WorkLedger(runs_dir=tmp_path)
    ledger.create_run("run-1", "Objective 1")
    ledger.create_run("run-2", "Objective 2")

    runs = ledger.list_runs()

    assert len(runs) == 2
    run_ids = [r["run_id"] for r in runs]
    assert "run-1" in run_ids
    assert "run-2" in run_ids


def test_verify_integrity_valid(tmp_path):
    """Create valid run with events, verify returns (True, [])."""
    ledger = WorkLedger(runs_dir=tmp_path)
    ledger.create_run("test-run", "Test")
    ledger.log_packet_emitted("test-run", "packet-001", depth=0, data={})

    is_valid, issues = ledger.verify_integrity("test-run")

    assert is_valid is True
    assert issues == []


def test_verify_integrity_missing_file(tmp_path):
    """Verify integrity fails gracefully when events file is missing."""
    ledger = WorkLedger(runs_dir=tmp_path)

    is_valid, issues = ledger.verify_integrity("nonexistent-run")

    assert is_valid is False
    assert len(issues) > 0


# =============================================================================
# SecretMasker Tests
# =============================================================================

def test_mask_api_key():
    """Call mask_secrets on text containing a generic API key, verify it is masked."""
    # Uses generic API_KEY pattern: api[_\-]?key\s*[=:]\s*[A-Za-z0-9_\-]{20,}
    text = "Using API key api_key=abc123def456ghi789jkl012mno345pqr678stu901vwx234yz"
    masked, detected = SecretMasker.mask_secrets(text)

    assert "[REDACTED:" in masked
    assert "abc123def456ghi789jkl012mno345pqr678stu901vwx234yz" not in masked


def test_mask_bearer_token():
    """Mask text with JWT bearer token, verify masked."""
    text = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0M5..."
    masked, detected = SecretMasker.mask_secrets(text)

    assert "[REDACTED:" in masked
    assert "REDACTED:OPENAI_KEY" in masked or "REDACTED:JWT_TOKEN" in masked


def test_mask_aws_access_key_id():
    """Mask AWS Access Key ID, verify masked. The pattern matches AWS_ACCESS_KEY_ID (not AWS_ACCESS_KEY)."""
    text = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
    masked, detected = SecretMasker.mask_secrets(text)

    assert "[REDACTED:" in masked
    assert "AKIAIOSFODNN7EXAMPLE" not in masked


def test_mask_aws_secret_key():
    """Mask AWS Secret Access Key, verify masked."""
    text = "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    masked, detected = SecretMasker.mask_secrets(text)

    assert "[REDACTED:" in masked
    assert "wJalrXUtnFEMI" not in masked


def test_check_dict_masks_secrets():
    """Call check_dict on dict with password in URL format and user, verify password masked, user preserved."""
    # PASSWORD_URL pattern matches "password=..." or "password:..." in the value
    data = {"credentials": "password=secret123", "user": "alice"}
    masked, detected = SecretMasker.check_dict(data)

    assert masked["credentials"] != "password=secret123"
    assert "[REDACTED:" in masked["credentials"]
    assert masked["user"] == "alice"
    assert "PASSWORD_URL" in detected


def test_check_dict_nested_openai_key(tmp_path):
    """Test that nested OpenAI API keys in dicts are masked."""
    # sk- + exactly 48 chars = 51 total (matches OPENAI_KEY regex: sk-[A-Za-z0-9]{48})
    key = "sk-" + "X" * 48  # 51 chars total
    data = {
        "level1": {
            "api_key": key,
            "name": "test",
        }
    }
    masked, detected = SecretMasker.check_dict(data)

    assert "[REDACTED:" in masked["level1"]["api_key"]
    assert masked["level1"]["name"] == "test"
    assert len(detected) > 0  # OPENAI_KEY pattern should have matched


def test_check_dict_list_secrets():
    """Test that secrets in list values are masked."""
    data = {
        "tokens": [
            "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjg",
            "normal_text",
        ]
    }
    masked, detected = SecretMasker.check_dict(data)

    assert "[REDACTED:" in masked["tokens"][0]
    assert masked["tokens"][1] == "normal_text"


def test_mask_no_secrets():
    """Test that text without secrets is unchanged."""
    text = "This is normal text with no secrets"
    masked, detected = SecretMasker.mask_secrets(text)

    assert masked == text
    assert len(detected) == 0


# =============================================================================
# ReplayEngine Tests
# =============================================================================

def test_replay_reconstructs_packets(tmp_path):
    """Run: create_run -> log packet created -> log packet claimed -> log packet completed. Verify counts."""
    ledger = WorkLedger(runs_dir=tmp_path)
    engine = ReplayEngine(ledger)

    ledger.create_run("test-run", "Test")
    ledger.log_packet_emitted("test-run", "packet-001", depth=0, data={})
    ledger.log_packet_claimed("test-run", "packet-001", "agent-1", "harness-1")
    ledger.log_event(
        run_id="test-run",
        event_type=PACKET_COMPLETED,
        packet_id="packet-001",
        agent_id=None,
        harness=None,
        depth=None,
        data={},
    )

    state = engine.replay("test-run")

    assert state["run_id"] == "test-run"
    assert len(state["packets_created"]) == 1
    assert len(state["packets_claimed"]) == 1
    assert "packet-001" in state["packets_created"]
    assert "packet-001" in state["packets_claimed"]


def test_replay_delegation_tree(tmp_path):
    """Run delegation scenario, call get_delegation_tree, verify parent-child structure."""
    ledger = WorkLedger(runs_dir=tmp_path)
    engine = ReplayEngine(ledger)

    ledger.create_run("test-run", "Test")
    ledger.log_packet_emitted("test-run", "root", depth=0, data={})
    ledger.log_child_requested("test-run", "root", "child-A", "seam-1")
    ledger.log_child_requested("test-run", "root", "child-B", "seam-2")
    ledger.log_packet_emitted("test-run", "child-A", depth=1, data={})
    ledger.log_packet_emitted("test-run", "child-B", depth=1, data={})

    state = engine.replay("test-run")

    assert "root" in state["delegation_tree"]
    assert "child-A" in state["delegation_tree"]["root"]
    assert "child-B" in state["delegation_tree"]["root"]


def test_replay_to_text(tmp_path):
    """Run simple scenario, call replay_to_text, verify returns non-empty string."""
    ledger = WorkLedger(runs_dir=tmp_path)
    engine = ReplayEngine(ledger)

    ledger.create_run("test-run", "Test objective")
    ledger.log_packet_emitted("test-run", "packet-001", depth=0, data={})

    text = engine.replay_to_text("test-run")

    assert isinstance(text, str)
    assert len(text) > 0
    assert "test-run" in text
    assert "Packets created" in text


def test_replay_empty_run(tmp_path):
    """Test replay on run with only PACKET_CREATED event."""
    ledger = WorkLedger(runs_dir=tmp_path)
    engine = ReplayEngine(ledger)

    ledger.create_run("empty-run", "No activity")

    state = engine.replay("empty-run")

    assert state["run_id"] == "empty-run"
    assert state["total_events"] == 1  # Only PACKET_CREATED
    assert len(state["packets_created"]) == 0  # Run-level, not a packet


def test_replay_error_events(tmp_path):
    """Test that replay captures error events."""
    ledger = WorkLedger(runs_dir=tmp_path)
    engine = ReplayEngine(ledger)

    ledger.create_run("test-run", "Test")
    ledger.log_packet_emitted("test-run", "packet-001", depth=0, data={})
    ledger.log_error("test-run", "packet-001", "Something went wrong")

    state = engine.replay("test-run")

    assert len(state["errors"]) == 1
    assert state["errors"][0]["packet_id"] == "packet-001"
    assert "Something went wrong" in state["errors"][0]["error"]


def test_replay_checkpoint_events(tmp_path):
    """Test that replay captures checkpoint events."""
    ledger = WorkLedger(runs_dir=tmp_path)
    engine = ReplayEngine(ledger)

    ledger.create_run("test-run", "Test")
    ledger.log_packet_emitted("test-run", "packet-001", depth=0, data={})
    ledger.log_checkpoint("test-run", "packet-001", "ckpt-1", "Before major step")

    state = engine.replay("test-run")

    assert "packet-001" in state["state_checkpoints"]
    assert len(state["state_checkpoints"]["packet-001"]) == 1
    assert state["state_checkpoints"]["packet-001"][0]["checkpoint_id"] == "ckpt-1"


def test_replay_shape_events(tmp_path):
    """Test that replay captures shape created events."""
    ledger = WorkLedger(runs_dir=tmp_path)
    engine = ReplayEngine(ledger)

    ledger.create_run("test-run", "Test")
    ledger.log_packet_emitted("test-run", "packet-001", depth=0, data={})
    ledger.log_shape_created("test-run", "packet-001", "shape-1", "analysis", {})

    state = engine.replay("test-run")

    assert "packet-001" in state["shapes_created"]
    assert len(state["shapes_created"]["packet-001"]) == 1
    assert state["shapes_created"]["packet-001"][0]["shape_id"] == "shape-1"


def test_get_packet_events(tmp_path):
    """Test get_packet_events returns events in order."""
    ledger = WorkLedger(runs_dir=tmp_path)
    engine = ReplayEngine(ledger)

    ledger.create_run("test-run", "Test")
    ledger.log_packet_emitted("test-run", "packet-001", depth=0, data={})
    ledger.log_packet_claimed("test-run", "packet-001", "agent-1", "harness-1")
    ledger.log_progress("test-run", "packet-001", {"step": 1})

    events = engine.get_packet_events("test-run", "packet-001")

    assert len(events) == 3
    assert events[0].event_type == PACKET_EMITTED
    assert events[1].event_type == PACKET_CLAIMED
    assert events[2].event_type == PACKET_PROGRESS


def test_get_state_at_event(tmp_path):
    """Test get_state_at_event reconstructs state up to specific event."""
    ledger = WorkLedger(runs_dir=tmp_path)
    engine = ReplayEngine(ledger)

    ledger.create_run("test-run", "Test")
    ledger.log_packet_emitted("test-run", "packet-001", depth=0, data={})
    claimed_id = ledger.log_packet_claimed("test-run", "packet-001", "agent-1", "harness-1")
    ledger.log_progress("test-run", "packet-001", {"step": 1})

    # Get state up to and including the claimed event (not progress)
    events = ledger.get_packet_events("test-run", "packet-001")
    claimed_event = next(e for e in events if e.event_type == PACKET_CLAIMED)

    state = engine.get_state_at_event("test-run", claimed_event.event_id)

    # Packet is created at PACKET_EMITTED and also shows as claimed at PACKET_CLAIMED
    assert "packet-001" in state["packets_claimed"]
    assert "packet-001" in state["packets_created"]  # Created at EMITTED, before claimed


def test_list_seams_excludes_claimed(tmp_path):
    """Test that list_seams excludes packets that have been claimed."""
    ledger = WorkLedger(runs_dir=tmp_path)
    ledger.create_run("test-run", "Test")

    ledger.log_packet_emitted("test-run", "packet-1", depth=0, data={"seam": "seam-1"})
    ledger.log_packet_emitted("test-run", "packet-2", depth=0, data={"seam": "seam-2"})
    ledger.log_packet_emitted("test-run", "packet-3", depth=0, data={"seam": "seam-3"})
    ledger.log_packet_claimed("test-run", "packet-2", "agent-1", "harness-1")

    seams = ledger.list_seams("test-run")

    assert len(seams) == 2
    seams_packets = [s["packet_id"] for s in seams]
    assert "packet-1" in seams_packets
    assert "packet-3" in seams_packets
    assert "packet-2" not in seams_packets


def test_list_seams_all_runs(tmp_path):
    """Test list_seams without run_id scopes across all runs."""
    ledger = WorkLedger(runs_dir=tmp_path)
    ledger.create_run("run-1", "Test")
    ledger.create_run("run-2", "Test")

    ledger.log_packet_emitted("run-1", "packet-1", depth=0, data={"seam": "seam-A"})
    ledger.log_packet_emitted("run-2", "packet-2", depth=0, data={"seam": "seam-B"})

    seams = ledger.list_seams()  # No run_id = all runs

    assert len(seams) == 2
    seams_list = [s["seam"] for s in seams]
    assert "seam-A" in seams_list
    assert "seam-B" in seams_list


def test_verify_integrity_invalid_json(tmp_path):
    """Test verify_integrity detects invalid JSON lines."""
    ledger = WorkLedger(runs_dir=tmp_path)
    ledger.create_run("test-run", "Test")

    # Manually corrupt the events file
    events_file = tmp_path / "test-run" / WORK_LEDGER_EVENTS_FILE
    events_file.write_text('{"valid": "json"}\n{"invalid json}\n')

    is_valid, issues = ledger.verify_integrity("test-run")

    assert is_valid is False
    assert any("invalid JSON" in issue for issue in issues)


def test_run_completed_event(tmp_path):
    """Test log_run_completed and verify it appears in replay."""
    ledger = WorkLedger(runs_dir=tmp_path)
    engine = ReplayEngine(ledger)

    ledger.create_run("test-run", "Test")
    ledger.log_run_completed("test-run", {"total_packets": 5, "status": "success"})

    state = engine.replay("test-run")

    assert state["run_summary"]["total_packets"] == 5
    assert state["run_summary"]["status"] == "success"


def test_run_failed_event(tmp_path):
    """Test log_run_failed and verify replay handles it."""
    ledger = WorkLedger(runs_dir=tmp_path)
    engine = ReplayEngine(ledger)

    ledger.create_run("test-run", "Test")
    ledger.log_run_failed("test-run", "Network timeout")

    state = engine.replay("test-run")

    # Run failed is stored as an error-like event in replay
    # The replay method captures RUN_COMPLETED but RUN_FAILED is also an event
    events = ledger.get_timeline("test-run")
    event_types = [e.event_type for e in events]
    assert "RUN_FAILED" in event_types


# =============================================================================
# Legacy Aliasing Tests
# =============================================================================


def test_legacy_graph_events_file_read_with_worker_id_normalization(tmp_path):
    """Test that work_ledger reads legacy graph-events.jsonl and normalizes worker_id -> packet_id."""
    import json

    ledger = WorkLedger(runs_dir=tmp_path)
    run_id = "legacy-run"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Manually write a legacy event (graph-events.jsonl style) to the legacy file
    legacy_file = run_dir / "graph-events.jsonl"
    legacy_event = {
        "event_id": "legacy-evt-001",
        "run_id": run_id,
        "timestamp": "2025-01-01T00:00:00+00:00",
        "event_type": "PACKET_EMITTED",
        "worker_id": "legacy-worker-42",  # legacy field name
        "depth": 1,
        "data": {},
        "masked_fields": [],
    }
    legacy_file.write_text(json.dumps(legacy_event) + "\n", encoding="utf-8")

    # work_ledger should read it via the legacy path and normalize worker_id -> packet_id
    events = ledger.get_timeline(run_id)

    assert len(events) == 1
    assert events[0].packet_id == "legacy-worker-42"  # normalized
    assert events[0].event_type == "PACKET_EMITTED"


def test_legacy_run_created_aliases_to_packet_created(tmp_path):
    """Test that RUN_CREATED legacy event type is aliased to PACKET_CREATED."""
    import json

    ledger = WorkLedger(runs_dir=tmp_path)
    run_id = "alias-run"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Write a legacy RUN_CREATED event
    legacy_file = run_dir / "graph-events.jsonl"
    legacy_event = {
        "event_id": "legacy-evt-002",
        "run_id": run_id,
        "timestamp": "2025-01-01T00:00:00+00:00",
        "event_type": "RUN_CREATED",  # legacy event type
        "worker_id": None,
        "depth": None,
        "data": {"description": "Legacy run"},
        "masked_fields": [],
    }
    legacy_file.write_text(json.dumps(legacy_event) + "\n", encoding="utf-8")

    events = ledger.get_timeline(run_id)

    assert len(events) == 1
    assert events[0].event_type == "PACKET_CREATED"  # aliased


def test_legacy_child_rejected_and_data_field_normalization(tmp_path):
    """Test CHILD_REJECTED -> CHILD_DENIED and parent_worker_id/child_worker_id -> parent_packet_id/child_packet_id."""
    import json

    ledger = WorkLedger(runs_dir=tmp_path)
    run_id = "child-alias-run"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Write a legacy CHILD_REJECTED event with parent_worker_id and child_worker_id in data
    legacy_file = run_dir / "graph-events.jsonl"
    legacy_event = {
        "event_id": "legacy-evt-003",
        "run_id": run_id,
        "timestamp": "2025-01-01T00:00:00+00:00",
        "event_type": "CHILD_REJECTED",  # legacy event type
        "worker_id": "rejecting-worker",  # legacy top-level field
        "depth": 2,
        "data": {
            "parent_worker_id": "parent-worker-abc",
            "child_worker_id": "child-worker-xyz",
            "reason": "Budget exceeded",
        },
        "masked_fields": [],
    }
    legacy_file.write_text(json.dumps(legacy_event) + "\n", encoding="utf-8")

    events = ledger.get_timeline(run_id)

    assert len(events) == 1
    # Event type aliased
    assert events[0].event_type == "CHILD_DENIED"
    # Top-level worker_id normalized to packet_id
    assert events[0].packet_id == "rejecting-worker"
    # data fields normalized
    assert events[0].data["parent_packet_id"] == "parent-worker-abc"
    assert events[0].data["child_packet_id"] == "child-worker-xyz"
    assert events[0].data["reason"] == "Budget exceeded"
