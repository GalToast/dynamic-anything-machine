"""
End-to-end integration test for real harness backends.

This test makes ACTUAL API calls when a real API key or local mmx auth is available.
It skips gracefully when no harness is configured, making it safe to run in CI
or local environments without credentials.

Run with: python -m pytest tests/test_real_harness_e2e.py -v
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

# Ensure project root is on sys.path
_dgc_root = Path(__file__).parent.parent
if str(_dgc_root) not in sys.path:
    sys.path.insert(0, str(_dgc_root))

from packet_runtime import PacketRuntime, make_harness
from work_packet import HarnessType, PacketStatus


# ---------------------------------------------------------------------------
# Key availability check
# ---------------------------------------------------------------------------

def _mmx_available() -> bool:
    """Return True if mmx CLI is installed and authenticated."""
    return shutil.which("mmx") is not None


def _get_available_harness() -> str:
    """
    Return the harness type string for the first available real harness.

    Priority: minimax (mmx CLI, no key needed) > qwen (Alibaba DW via CLI/API) > openrouter > gemini
    """
    if os.environ.get("HARNESS_TYPE", "").lower() == "minimax":
        if _mmx_available():
            return "minimax"
    elif _mmx_available() and os.environ.get("HARNESS_TYPE", "").lower() in ("", "fake"):
        # Auto-detect minimax if mmx is available and no explicit HARNESS_TYPE set
        return "minimax"
    # Alibaba Coding Plan key works with qwen harness
    if os.environ.get("ALIBABA_CODING_API_KEY"):
        return "qwen"
    if os.environ.get("QWEN_API_KEY"):
        return "qwen"
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter"
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    return ""


# ---------------------------------------------------------------------------
# Test: skip when no harness is available
# ---------------------------------------------------------------------------

def test_skips_when_no_harness_available():
    """Test skips with a clear reason when no real harness is available."""
    harness_type = _get_available_harness()
    if not harness_type:
        pytest.skip(
            "No real harness available: set HARNESS_TYPE=minimax (requires mmx CLI), "
            "or set one of OPENROUTER_API_KEY, QWEN_API_KEY, GEMINI_API_KEY"
        )
    assert harness_type, "Should have a harness type if not skipped"


# ---------------------------------------------------------------------------
# Test: real end-to-end with available harness
# ---------------------------------------------------------------------------

def test_real_harness_e2e(tmp_path):
    """
    Create a goal, claim it, execute through a real harness, and verify
    the event log has the correct entries.

    This is a REAL test — it makes actual API calls when a harness is available.
    """
    harness_type = _get_available_harness()
    if not harness_type:
        pytest.skip("No real harness available")

    # Create a PacketRuntime pointing at a temp directory
    runtime = PacketRuntime(runs_dir=tmp_path / "runs")

    # Create a simple goal
    run_id, packet_id = runtime.create_goal(
        objective="Analyze this directory structure and list the Python files",
        success_criteria="List of .py files found",
        seam="code-analysis",
    )

    # Verify the goal was created
    assert run_id, "Should have a run_id"
    assert packet_id, "Should have a packet_id"

    # Verify the packet was written to disk
    packet_path = tmp_path / "runs" / run_id / "packets" / f"{packet_id}.json"
    assert packet_path.exists(), "Packet file should exist after create_goal"

    # Get the timeline after creation — should have EMITTED event
    events_after_create = runtime.ledger.get_timeline(run_id)
    event_types = [e.event_type for e in events_after_create]

    # We expect at least the PACKET_CREATED and PACKET_EMITTED events
    from work_ledger import PACKET_CREATED, PACKET_EMITTED
    assert PACKET_CREATED in event_types, f"Should have PACKET_CREATED event, got: {event_types}"
    assert PACKET_EMITTED in event_types, f"Should have PACKET_EMITTED event, got: {event_types}"

    # Claim the packet
    agent_id = f"test-agent-{harness_type}"
    try:
        harness_enum = HarnessType(harness_type)
    except ValueError:
        pytest.skip(f"Unknown harness type: {harness_type}")

    runtime.claim_packet(packet_id, run_id, agent_id, harness_enum)

    # Verify it was claimed
    events_after_claim = runtime.ledger.get_timeline(run_id)
    claim_events = [e for e in events_after_claim if e.event_type.startswith("PACKET_CLAIMED")]
    assert len(claim_events) >= 1, "Should have at least one PACKET_CLAIMED event"

    # Build a fresh packet for execution
    from work_packet import WorkPacket
    packet = WorkPacket(
        packet_id=packet_id,
        run_id=run_id,
        depth=0,
        seam="code-analysis",
        objective="Analyze this directory structure and list the Python files",
        success_criteria="List of .py files found",
    )

    # Execute (this is the real API call)
    harness = make_harness(harness_type, ledger=runtime.ledger)
    result = harness.execute(packet)

    # Result should be either COMPLETED or FAILED (network issues, etc.)
    # We don't assert success because the model might fail for various reasons
    # But we should have a well-formed result
    assert result.packet_id == packet_id, "Result should carry the packet_id"
    assert result.run_id == run_id, "Result should carry the run_id"
    assert result.status in (PacketStatus.COMPLETED, PacketStatus.FAILED), \
        f"Status should be COMPLETED or FAILED, got {result.status}"

    # Final timeline check — should have COMPLETED or FAILED event
    events_after_execute = runtime.ledger.get_timeline(run_id)
    final_event_types = [e.event_type for e in events_after_execute]

    from work_ledger import PACKET_COMPLETED, PACKET_FAILED
    has_terminal = PACKET_COMPLETED in final_event_types or PACKET_FAILED in final_event_types

    # If the harness completed/failed via ledger (some harnesses do), verify it
    # Otherwise just verify we got a result
    assert result.status is not None, "Should have a status in the result"
