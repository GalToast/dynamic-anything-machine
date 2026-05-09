"""
Tests for the HarnessAdapter interface and concrete implementations.

Tests cover:
- HarnessAdapter base class interface
- FakeExecutor: execution returns WorkResult with correct fields
- ClaudeCodeHarness: subprocess call with mocked responses
- HttpApiHarness: HTTP call with mocked responses
- make_harness factory: environment variable detection

Fixtures:
    tmp_path: pytest temporary directory for runs_dir and shapes_dir
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add dynamic-graph-compiler root to path for imports
_dgc_root = Path(__file__).parent.parent
if str(_dgc_root) not in sys.path:
    sys.path.insert(0, str(_dgc_root))

from packet_runtime import (
    FakeExecutor,
    ClaudeCodeHarness,
    HttpApiHarness,
    HarnessAdapter,
    make_harness,
)
from work_packet import (
    WorkPacket,
    WorkResult,
    HarnessType,
    PacketStatus,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def sample_packet():
    """Return a WorkPacket with typical fields for testing."""
    return WorkPacket(
        packet_id="test-packet-1",
        run_id="test-run-1",
        depth=0,
        seam="test-seam",
        objective="Analyze the codebase for Python files",
        success_criteria="List of .py files with line counts",
        allowed_tools=["read", "grep"],
        forbidden_tools=["shell"],
        current_state={"step": "starting"},
    )


# ============================================================================
# HarnessAdapter — base class interface
# ============================================================================

def test_harness_adapter_is_abc():
    """Verify HarnessAdapter cannot be instantiated directly."""
    with pytest.raises(TypeError, match="abstract"):
        HarnessAdapter()


def test_fake_executor_inherits_from_harness_adapter():
    """FakeExecutor is a HarnessAdapter subclass."""
    executor = FakeExecutor()
    assert isinstance(executor, HarnessAdapter)


def test_execute_returns_work_result_object(sample_packet):
    """HarnessAdapter.execute() returns a WorkResult, not a raw dict."""
    executor = FakeExecutor()
    result = executor.execute(sample_packet)
    assert isinstance(result, WorkResult)


def test_execute_result_has_correct_packet_fields(sample_packet):
    """WorkResult from execute() carries the correct packet_id and run_id."""
    executor = FakeExecutor()
    result = executor.execute(sample_packet)
    assert result.packet_id == sample_packet.packet_id
    assert result.run_id == sample_packet.run_id


# ============================================================================
# FakeExecutor
# ============================================================================

def test_fake_executor_returns_completed_status(sample_packet):
    """FakeExecutor returns status COMPLETED."""
    executor = FakeExecutor()
    result = executor.execute(sample_packet)
    assert result.status == PacketStatus.COMPLETED


def test_fake_executor_returns_output_string(sample_packet):
    """FakeExecutor returns a string output."""
    executor = FakeExecutor()
    result = executor.execute(sample_packet)
    assert isinstance(result.output, str)
    assert len(result.output) > 0


def test_fake_executor_duration_is_positive(sample_packet):
    """FakeExecutor duration_seconds is a positive number."""
    executor = FakeExecutor()
    result = executor.execute(sample_packet)
    assert result.duration_seconds is not None
    assert result.duration_seconds > 0


def test_fake_executor_call_count_increments(sample_packet):
    """call_count reflects number of execute() calls."""
    executor = FakeExecutor()
    assert executor.call_count == 0
    executor.execute(sample_packet)
    assert executor.call_count == 1
    executor.execute(sample_packet)
    assert executor.call_count == 2


def test_fake_executor_handles_exception(sample_packet):
    """FakeExecutor.execute() propagates errors as FAILED WorkResult."""
    executor = FakeExecutor()

    # Patch _do_execute to raise
    executor._do_execute = MagicMock(side_effect=ValueError("boom"))

    result = executor.execute(sample_packet)
    assert result.status == PacketStatus.FAILED
    assert "boom" in (result.error or "")


# ============================================================================
# ClaudeCodeHarness — subprocess call
# ============================================================================

def test_claude_code_harness_calls_subprocess(sample_packet):
    """ClaudeCodeHarness invokes the claude binary as a subprocess."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"status": "completed", "output": "done"}),
            stderr="",
        )

        harness = ClaudeCodeHarness(claude_bin="claude")
        result = harness.execute(sample_packet)

        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert "claude" in call_args.args[0]


def test_claude_code_harness_parses_json_response(sample_packet):
    """ClaudeCodeHarness parses a JSON stdout into WorkResult fields."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "status": "completed",
                "output": "analysis complete",
                "cost_estimate": 0.05,
                "children": [],
            }),
            stderr="",
        )

        harness = ClaudeCodeHarness(claude_bin="claude")
        result = harness.execute(sample_packet)

        assert result.status == PacketStatus.COMPLETED
        assert result.output == "analysis complete"
        assert result.cost_estimate == 0.05


def test_claude_code_harness_handles_nonzero_exit(sample_packet):
    """ClaudeCodeHarness returns FAILED when subprocess exits non-zero."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="claude error: something went wrong",
        )

        harness = ClaudeCodeHarness(claude_bin="claude")
        result = harness.execute(sample_packet)

        assert result.status == PacketStatus.FAILED
        assert "something went wrong" in (result.error or "")


def test_claude_code_harness_falls_back_to_raw_stdout(sample_packet):
    """ClaudeCodeHarness uses raw stdout when JSON parse fails."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="raw text output from claude",
            stderr="",
        )

        harness = ClaudeCodeHarness(claude_bin="claude")
        result = harness.execute(sample_packet)

        assert result.status == PacketStatus.COMPLETED
        assert "raw text output" in result.output


# ============================================================================
# HttpApiHarness — HTTP client
# ============================================================================

def test_http_api_harness_sends_post_request(sample_packet):
    """HttpApiHarness sends a POST to the configured BASE_URL."""
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "content": [{"type": "text", "text": "api response"}],
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }).encode("utf-8")
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        harness = HttpApiHarness(
            api_key="test-key",
            model="test-model",
            base_url="https://api.test.com",
        )
        result = harness.execute(sample_packet)

        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args.args[0]
        assert req.full_url == "https://api.test.com/v1/messages"
        assert req.method == "POST"


def test_http_api_harness_parses_response_correctly(sample_packet):
    """HttpApiHarness parses Anthropic API response into WorkResult."""
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "content": [{"type": "text", "text": "analysis complete"}],
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }).encode("utf-8")
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        harness = HttpApiHarness(api_key="test-key", model="test-model")
        result = harness.execute(sample_packet)

        assert result.status == PacketStatus.COMPLETED
        assert "analysis complete" in result.output
        assert result.cost_estimate is not None


def test_http_api_harness_handles_http_error(sample_packet):
    """HttpApiHarness returns FAILED on HTTP error responses."""
    import urllib.error

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://api.test.com",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=MagicMock(read=MagicMock(return_value=b"invalid key")),
        )

        harness = HttpApiHarness(api_key="bad-key", base_url="https://api.test.com")
        result = harness.execute(sample_packet)

        assert result.status == PacketStatus.FAILED
        assert "401" in (result.error or "")


def test_http_api_harness_handles_network_error(sample_packet):
    """HttpApiHarness returns FAILED on network/connection errors."""
    import urllib.error

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = urllib.error.URLError(MagicMock(reason="Connection refused"))

        harness = HttpApiHarness(api_key="test-key")
        result = harness.execute(sample_packet)

        assert result.status == PacketStatus.FAILED
        assert "Network error" in (result.error or "")


# ============================================================================
# make_harness factory
# ============================================================================

def test_make_harness_default_is_fake():
    """make_harness() with no args returns FakeExecutor."""
    harness = make_harness()
    assert isinstance(harness, FakeExecutor)


def test_make_harness_explicit_fake():
    """make_harness(HarnessType.FAKE) returns FakeExecutor."""
    harness = make_harness(HarnessType.FAKE)
    assert isinstance(harness, FakeExecutor)


def test_make_harness_explicit_string_fake():
    """make_harness('fake') returns FakeExecutor."""
    harness = make_harness("fake")
    assert isinstance(harness, FakeExecutor)


def test_make_harness_claude_code():
    """make_harness(HarnessType.CLAUDE_CODE) returns ClaudeCodeHarness."""
    harness = make_harness(HarnessType.CLAUDE_CODE)
    assert isinstance(harness, ClaudeCodeHarness)


def test_make_harness_http_api():
    """make_harness(HarnessType.HTTP_API) returns HttpApiHarness."""
    harness = make_harness(HarnessType.HTTP_API)
    assert isinstance(harness, HttpApiHarness)


def test_make_harness_env_var_fake(monkeypatch):
    """HARNESS_TYPE=fake env var triggers FakeExecutor."""
    monkeypatch.setenv("HARNESS_TYPE", "fake")
    # Re-import to pick up env var change (make_harness reads it at call time)
    harness = make_harness()
    assert isinstance(harness, FakeExecutor)


def test_make_harness_unknown_env_defaults_to_fake(monkeypatch):
    """Unknown HARNESS_TYPE value falls back to FakeExecutor."""
    monkeypatch.setenv("HARNESS_TYPE", "not-a-real-harness")
    harness = make_harness()
    assert isinstance(harness, FakeExecutor)


def test_make_harness_ledger_passed_through():
    """make_harness accepts and passes ledger to concrete harness."""
    from work_ledger import WorkLedger

    ledger = MagicMock(spec=WorkLedger)
    harness = make_harness(HarnessType.FAKE, ledger=ledger)
    assert harness.ledger is ledger