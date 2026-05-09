"""
Recursive Research Orchestra — Main Demo Entry Point

A fully dynamic LangGraph multi-agent system demonstrating:
1. Dynamic graph mutation (add_node from inside a node function at runtime)
2. Streaming execution with progressive token output
3. Checkpointing with MemorySaver for replay
4. Conditional routing based on dynamic state
5. Multi-agent coordination via shared OrchestratorState
6. Tool binding per node role (MiniMax M2.7)
7. Human-in-the-loop via HumanGateNode
8. WorkLedger append-only audit trail

Usage:
    python showcase/showcase_demo.py "Research LLM fine-tuning"
    python showcase/showcase_demo.py "Build a REST API"
    python showcase/showcase_demo.py "Survey quantum computing trends"
    python showcase/showcase_demo.py --no-llm "Research something"
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root on path before any showcase imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import json
import uuid
from typing import Any, Dict, Iterator, List, Optional

# LangGraph imports
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

# Import from project
from showcase.states import OrchestratorState, SeamState, ManagerState
from showcase.dynamic_orchestrator import DynamicOrchestrator


# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------


def print_banner():
    """Print the demo banner."""
    banner = r"""
+========================================================================+
|        Recursive Research Orchestra -- LangGraph Dynamic Showcase      |
|                                                                        |
|  Demonstrating:                                                         |
|  * Dynamic graph mutation (add_node from inside a node fn)              |
|  * Streaming execution with progressive token output                    |
|  * Checkpointing with MemorySaver for replay                            |
|  * Conditional routing based on dynamic state                           |
|  * Multi-agent coordination via shared OrchestratorState                 |
|  * Tool binding per node role (MiniMax M2.7)                            |
|  * Human-in-the-loop via HumanGateNode                                  |
|  * WorkLedger append-only audit trail                                   |
+========================================================================+
"""
    print(banner)


# ---------------------------------------------------------------------------
# Demo runner
# ---------------------------------------------------------------------------


def run_demo(goal: str, runs_dir: str = "runs/showcase", use_llm: bool = True):
    """Run the full demo with streaming output."""
    print_banner()

    print(f"[DEMO] Goal: {goal}\n")
    print(f"[DEMO] Runs directory: {runs_dir}\n")

    # Create orchestrator
    orchestrator = DynamicOrchestrator(runs_dir=runs_dir, use_llm=use_llm, verbose=True)

    print(f"\n{'='*60}")
    print("[DEMO] Executing two-phase orchestration...")
    print(f"{'='*60}\n")

    # Execute two-phase orchestration
    final_state = orchestrator.execute(goal)

    print("\n" + "="*60)
    print("[DEMO] Execution complete!")
    print("="*60 + "\n")

    # Print summary
    if final_state:
        print("\n[SUMMARY]")
        print(f"  Run ID: {final_state.run_id}")
        print(f"  Mode: {final_state.mode}")
        print(f"  Seams created: {list(final_state.seams.keys())}")
        print(f"  Managers spawned: {list(final_state.managers.keys())}")
        print(f"  Active agents: {final_state.active_agents}")
        print(f"  Completed agents: {final_state.completed_agents}")
        print(f"  Confidence: {final_state.confidence:.2f}")
        print(f"  Final output length: {len(final_state.final_output) if final_state.final_output else 0} chars")

        # Print final output preview
        if final_state.final_output:
            print("\n[FINAL OUTPUT PREVIEW]")
            preview = final_state.final_output[:500]
            if len(final_state.final_output) > 500:
                preview += "\n... [truncated]"
            print(preview)

        # Print ledger file location
        ledger_path = Path(runs_dir) / final_state.run_id / "events.jsonl"
        if ledger_path.exists():
            print(f"\n[AUDIT] WorkLedger events: {ledger_path}")

    return final_state


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Recursive Research Orchestra — LangGraph Dynamic Showcase",
        epilog="""
Examples:
  python showcase/showcase_demo.py "Research LLM fine-tuning"
  python showcase/showcase_demo.py "Build a REST API for a task manager"
  python showcase/showcase_demo.py "Survey quantum computing trends"
  python showcase/showcase_demo.py --no-llm "Research something"
        """,
    )
    parser.add_argument(
        "goal",
        nargs="?",
        default="Research LLM fine-tuning",
        help="The goal to execute (default: 'Research LLM fine-tuning')",
    )
    parser.add_argument(
        "--runs-dir",
        default="runs/showcase",
        help="Directory for run artifacts (default: runs/showcase)",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Run without LLM (simulation mode)",
    )

    args = parser.parse_args()

    try:
        final_state = run_demo(
            goal=args.goal,
            runs_dir=args.runs_dir,
            use_llm=not args.no_llm,
        )

        if final_state and final_state.final_output:
            sys.exit(0)
        else:
            sys.exit(1)

    except KeyboardInterrupt:
        print("\n[DEMO] Interrupted by user")
        sys.exit(130)
    except Exception as exc:
        print(f"\n[DEMO] Error: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()