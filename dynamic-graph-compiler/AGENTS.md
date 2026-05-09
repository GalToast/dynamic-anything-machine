# DynamicAnythingMachine — Project Status

## What Is This

A production-quality, two-phase LangGraph multi-agent orchestration system. Goal analysis → seam plan → parallel execution → synthesis, with real MiniMax M2.7 tool calls, checkpointing, audit trail, and multi-provider fallback.

## Architecture

```
Goal → [Phase 1: Isolated Graph] → Seam Plan
     → [Phase 2: Pre-built Graph] → Stream Execution → Synthesis Report

Key files:
  showcase/dynamic_orchestrator.py  — two-phase orchestrator
  showcase/nodes.py                  — orchestrator/manager/worker/synthesis/router node functions
  showcase/states.py                 — OrchestratorState, SeamState, ManagerState, WorkerState, TokenBudget
  showcase/tools.py                  — per-role tool bindings + execute_tool() + FreeProviderRouter
  packet_runtime.py                  — MiniMaxDirectClient (LangChain-compatible tool-use loop)
  web-demo/app.py                    — Flask + SSE streaming server
  tests/test_showcase_e2e.py         — 5 CLI-based E2E tests
```

## What's Working

- **Phase 1**: Isolated graph produces real seam plan from MiniMax (6 seams: literature, technical, variants, implementation, applications, benchmarks)
- **Phase 2**: 28-node graph (orchestrator + router + synthesis + human_gate + 6 managers × 3 workers) compiles and streams to completion
- **Real MiniMax**: Tool execution via MiniMaxDirectClient with full tool-use loop, 6,357-char synthesis output
- **Simulation mode**: `python showcase/showcase_demo.py "Research LoRA" --no-llm` — exit 0, no warnings
- **MsgPack checkpointer**: explicit allowlist prevents deserialization warnings
- **Router deduplication**: `dict.fromkeys()` prevents 16x orchestrator entries
- **FreeProviderRouter**: MiniMax → Groq → OpenRouter → NVIDIA NIM with auto-fallback
- **Per-seam TokenBudget**: workers share seam budget, bounded tool calls (3 max), partial result on exhaustion
- **Web demo**: Flask + SSE server at `python web-demo/app.py`, serves `http://localhost:5000`

## Test Results

```
pytest tests/test_showcase_e2e.py -v  → 5 passed
pytest tests/ -x -q                  → 197 passed, 3 skipped
```

## How to Run

```bash
cd dynamic-graph-compiler

# Simulation (fast)
python showcase/showcase_demo.py "Research LLM fine-tuning" --no-llm

# Real MiniMax (~5-10 min)
python showcase/showcase_demo.py "Research LLM fine-tuning"

# Web UI
python web-demo/app.py
# Open http://localhost:5000
```

## Known Limitations

- `SendMessage` subagent→parent or subagent→subagent does NOT work (Anthropic bug #48160)
  — Tree orchestration works because parent is hub; workers don't need peer-to-peer
- Workers hit tool-use limits on vague tasks — improved prompts require structured ## Finding/## Evidence/## Relevance format
- Web demo requires running on same machine (localhost); no cloud deployment yet

## Key Findings

1. `MiniMaxDirectClient._convert_messages()` needed dict handling for LangChain message format
2. `allowed_msgpack_modules=True` still warns — explicit tuple list required: `[("showcase.states", "SeamState"), ...]`
3. Router re-invocation: LangGraph checkpoint race causes duplicate completed_agents entries — `dict.fromkeys()` dedup fixes it
4. `mmx_tools` pip package not installed — WebSearch uses `mmx search query --output json` CLI instead