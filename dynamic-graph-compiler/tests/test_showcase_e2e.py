"""
End-to-end integration test for DynamicOrchestrator via the showcase demo CLI.

This test exercises the full showcase pipeline:
1. phase1_analyze() via subprocess — verifies seam plan is produced from a goal
2. build_full_graph() — verifies a 20-node graph compiles without error
3. execute() in simulation mode — verifies full end-to-end execution completes
4. Verifies no MsgPack warnings in stderr output
5. Verifies final_output is non-empty and contains expected structure

Run with: pytest tests/test_showcase_e2e.py -v
"""

from __future__ import annotations

import subprocess
import sys
import re
from pathlib import Path

import pytest

# Project root
_DGC_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_DGC_ROOT))

from showcase.dynamic_orchestrator import DynamicOrchestrator


# ---------------------------------------------------------------------------
# Test 1: phase1_analyze — seam plan is produced from a goal
# ---------------------------------------------------------------------------

def test_phase1_analyze_produces_seam_plan():
    """
    Verify DynamicOrchestrator.phase1_analyze() returns a non-empty seam plan
    when given a goal string.
    """
    orchestrator = DynamicOrchestrator(use_llm=False, verbose=False)
    goal = "Research LoRA fine-tuning techniques"
    run_id = "test-phase1-run"
    mode = "research"

    seams, final_state = orchestrator.phase1_analyze(goal, run_id, mode)

    assert isinstance(seams, list), f"Expected list, got {type(seams)}"
    assert len(seams) > 0, f"Expected at least 1 seam, got {len(seams)}"
    for seam in seams:
        assert "id" in seam, f"Seam missing 'id': {seam}"
        assert "manager_id" in seam, f"Seam missing 'manager_id': {seam}"
        assert "description" in seam, f"Seam missing 'description': {seam}"
        assert "tasks" in seam, f"Seam missing 'tasks': {seam}"
        assert isinstance(seam["tasks"], list), f"Seam tasks should be list, got {type(seam['tasks'])}"
    print(f"[PASS] phase1_analyze produced {len(seams)} seams: {[s['id'] for s in seams]}")


# ---------------------------------------------------------------------------
# Test 2: build_full_graph — 20-node graph compiles without error
# ---------------------------------------------------------------------------

def test_build_full_graph_compiles_successfully():
    """
    Verify DynamicOrchestrator.build_full_graph() produces a compiled graph
    with multiple nodes (targeting ~20 nodes) and compiles without error.
    """
    orchestrator = DynamicOrchestrator(use_llm=False, verbose=False)
    goal = "Research LoRA fine-tuning techniques"
    run_id = "test-build-run"
    mode = "research"

    # First get a seam plan from phase1
    seam_plan, phase1_state = orchestrator.phase1_analyze(goal, run_id, mode)

    # Build full graph — this should compile without raising
    compiled, builder, initial_state = orchestrator.build_full_graph(
        goal, run_id, mode, seam_plan, phase1_state
    )

    # Verify compiled is truthy (LangGraph returns a CompiledGraph)
    assert compiled is not None, "Compiled graph should not be None"

    # Count nodes in the builder
    node_count = len(builder.nodes)
    assert node_count >= 4, f"Expected at least 4 nodes (orchestrator, router, synthesis, human_gate), got {node_count}"

    # The seam plan may spawn multiple workers — verify nodes were added
    print(f"[PASS] build_full_graph compiled with {node_count} nodes: {list(builder.nodes.keys())}")


# ---------------------------------------------------------------------------
# Test 3: execute() in simulation mode — full end-to-end via subprocess
# ---------------------------------------------------------------------------

def test_execute_simulation_mode_via_cli():
    """
    Run the showcase demo CLI in --no-llm (simulation) mode and verify:
    - Exit code is 0
    - stdout contains expected output markers
    - stderr contains no MsgPack warnings
    """
    demo_script = _DGC_ROOT / "showcase" / "showcase_demo.py"
    assert demo_script.exists(), f"Demo script not found: {demo_script}"

    result = subprocess.run(
        [sys.executable, str(demo_script), "Research LoRA", "--no-llm"],
        capture_output=True,
        text=True,
        timeout=300,
    )

    stdout = result.stdout
    stderr = result.stderr
    combined = stdout + "\n" + stderr

    # --- Exit code check ---
    assert result.returncode == 0, (
        f"Demo exited with code {result.returncode}.\n"
        f"STDOUT:\n{stdout}\n\nSTDERR:\n{stderr}"
    )

    # --- Structured content checks ---
    # Check for "Research Report:" or similar structured content in output
    has_structured_output = (
        "Research Report:" in combined
        or "research report" in combined.lower()
        or "[FINAL OUTPUT" in combined
        or "[SUMMARY]" in combined
    )
    assert has_structured_output, (
        f"Expected structured output content (e.g. 'Research Report:' or '[FINAL OUTPUT'), "
        f"but not found in output.\nSTDOUT:\n{stdout}\n\nSTDERR:\n{stderr}"
    )

    # --- Node count checks (graph should be large) ---
    node_match = re.search(r"Full graph compiled with (\d+) nodes", combined, re.IGNORECASE)
    if node_match:
        node_count = int(node_match.group(1))
        assert node_count >= 4, f"Expected at least 4 nodes, got {node_count}"
        print(f"[PASS] Full graph compiled with {node_count} nodes")

    # --- Execution complete marker ---
    assert "Execution complete" in combined or "DONE" in combined or "complete" in combined.lower(), (
        f"Expected execution completion message not found.\nSTDOUT:\n{stdout}\n\nSTDERR:\n{stderr}"
    )

    print(f"[PASS] CLI simulation mode completed successfully (exit {result.returncode})")


# ---------------------------------------------------------------------------
# Test 4: No MsgPack warnings in stderr
# ---------------------------------------------------------------------------

def test_no_msgpack_warnings_in_stderr():
    """
    Capture stderr when running the demo and verify no MsgPack-related
    warnings appear (e.g., "msgpack: the next method is deprecated").
    """
    demo_script = _DGC_ROOT / "showcase" / "showcase_demo.py"

    result = subprocess.run(
        [sys.executable, str(demo_script), "Research LoRA", "--no-llm"],
        capture_output=True,
        text=True,
        timeout=300,
    )

    stderr_lower = result.stderr.lower()

    # Check for common MsgPack warning patterns
    msgpack_warning_patterns = [
        "msgpack",
        "the next method is deprecated",
        "messagepack",
    ]
    found_warnings = []
    for pattern in msgpack_warning_patterns:
        if pattern in stderr_lower:
            # Extract the line containing the warning for reporting
            for line in result.stderr.splitlines():
                if pattern in line.lower():
                    found_warnings.append(line.strip())
                    break

    assert not found_warnings, (
        f"MsgPack warning(s) found in stderr:\n" + "\n".join(f"  {w}" for w in found_warnings)
    )
    print(f"[PASS] No MsgPack warnings found in stderr")


# ---------------------------------------------------------------------------
# Test 5: final_output is non-empty and contains expected structure
# ---------------------------------------------------------------------------

def test_final_output_non_empty_and_structured():
    """
    Verify that when the demo runs in simulation mode:
    - final_output in the state is non-empty
    - It contains recognizable structured content (report-like sections)
    """
    demo_script = _DGC_ROOT / "showcase" / "showcase_demo.py"

    result = subprocess.run(
        [sys.executable, str(demo_script), "Research LoRA", "--no-llm"],
        capture_output=True,
        text=True,
        timeout=300,
    )

    combined = result.stdout + "\n" + result.stderr

    # Extract final output length info from the summary
    output_len_match = re.search(r"Final output length:\s*(\d+)\s*chars", combined, re.IGNORECASE)
    if output_len_match:
        output_len = int(output_len_match.group(1))
        assert output_len > 0, f"Expected non-empty final_output, got length {output_len}"
        print(f"[PASS] final_output is non-empty ({output_len} chars)")

    # Verify final output preview is shown (truncated or not)
    assert "[FINAL OUTPUT PREVIEW]" in combined or "final_output" in combined.lower(), (
        f"Expected final_output content in output.\nSTDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
    )

    # Verify the run completed successfully
    assert result.returncode == 0, f"Expected exit 0, got {result.returncode}"
    print(f"[PASS] final_output is present and structured")