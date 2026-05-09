"""
demo_real.py — Real harness smoke-test using MiniMax and Qwen.

This demo exercises the actual API-bound harnesses with real MiniMax M2.7
and Qwen calls (or graceful fallbacks if credentials are absent).

MiniMax harness:  ``mmx text chat`` → text-only, no tool use.
Qwen harness:     qwen CLI → full tool/CLI access via ALIBABA_CODING_API_KEY.

Each harness executes the same WorkPacket so you can compare their
outputs side-by-side.  Errors are printed but don't crash the demo.
"""

import tempfile
from pathlib import Path
from typing import Any

# Core runtime imports
from packet_runtime import PacketRuntime, make_harness
from work_packet import WorkPacket, HarnessType, PacketStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_str(s: Any) -> str:
    """Convert to string, replacing any characters that can't be encoded in the console."""
    raw = str(s) if s is not None else ""
    return raw.encode("cp1252", errors="replace").decode("cp1252")


def print_result(label: str, result) -> None:
    """Human-readable summary of a WorkResult."""
    status_str = result.status.value if hasattr(result.status, "value") else str(result.status)
    raw_output = result.output if result.output else ""
    output_preview = (
        (raw_output[:200] + "...") if len(raw_output) > 200
        else raw_output
    )
    dur = getattr(result, "duration_seconds", None) or 0.0
    err = getattr(result, "error", None)

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(f"  Status : {status_str}")
    print(f"  Duration: {dur:.3f}s")
    if err:
        print(f"  Error  : {_safe_str(err)}")
    print(f"  Output : {_safe_str(output_preview)}")
    print()


# ---------------------------------------------------------------------------
# Main demo
# ---------------------------------------------------------------------------

def main() -> None:
    # Working directory for this demo's run artifacts
    runs_dir = Path(tempfile.mkdtemp(prefix="demo_real_"))
    print(f"Runs directory: {runs_dir}")

    # Boot the runtime
    runtime = PacketRuntime(runs_dir=runs_dir)

    # -------------------------------------------------
    # 1. Create a goal via the runtime
    # -------------------------------------------------
    objective = "Analyze this directory and list all Python files (*.py) present."
    run_id, packet_id = runtime.create_goal(
        objective=objective,
        success_criteria="Listed Python files with line counts",
    )
    print(f"Goal created: run_id={run_id}, packet_id={packet_id}")

    # -------------------------------------------------
    # 2. Claim the packet so the harness can own it
    # -------------------------------------------------
    # Using 'demo-agent' as the agent ID for this demo run.
    # The harness type doesn't matter for execution since we call the
    # harness directly below; this just satisfies ledger bookkeeping.
    runtime.claim_packet(
        packet_id=packet_id,
        run_id=run_id,
        agent_id="demo-agent",
        harness=HarnessType.MINIMAX,
    )
    print(f"Packet {packet_id} claimed")

    # Build the WorkPacket for execution.
    # WorkPacket(run_id=..., depth=0, seam=..., objective=..., success_criteria=...)
    packet = WorkPacket(
        packet_id=packet_id,
        run_id=run_id,
        depth=0,
        seam="root",
        objective=objective,
        success_criteria="Listed Python files with line counts",
    )

    # -------------------------------------------------
    # 3. Execute with MiniMax harness
    # -------------------------------------------------
    # MiniMax mmx text chat is text-only — no file system access, no tools.
    # It will receive the objective and respond with a text description
    # of what it would do (no actual directory access).
    print("\nExecuting with MiniMax harness (mmx text chat)...")
    minimax = make_harness("minimax")
    mmx_result = minimax.execute(packet)
    print_result("MiniMax result", mmx_result)

    # -------------------------------------------------
    # 4. Execute with Qwen harness
    # -------------------------------------------------
    # Qwen CLI has full tool access via the ALIBABA_CODING_API_KEY.
    # It can actually inspect the filesystem and list the files.
    print("\nExecuting with Qwen harness (qwen CLI/API)...")
    qwen = make_harness("qwen")
    qwen_result = qwen.execute(packet)
    print_result("Qwen result", qwen_result)

    # -------------------------------------------------
    # 5. Summary
    # -------------------------------------------------
    print("Demo complete.")
    mmx_status = mmx_result.status.value if hasattr(mmx_result.status, "value") else mmx_result.status
    qwen_status = qwen_result.status.value if hasattr(qwen_result.status, "value") else qwen_result.status
    print(f"\nMiniMax — status={mmx_status}, duration={getattr(mmx_result, 'duration_seconds', 0):.3f}s")
    print(f"Qwen    — status={qwen_status}, duration={getattr(qwen_result, 'duration_seconds', 0):.3f}s")


if __name__ == "__main__":
    main()