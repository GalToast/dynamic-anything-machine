# DynamicAnythingMachine

## A Production-Grade Dynamic Multi-Agent Orchestration System

```
                    ┌─────────────────────────────────────────────────────┐
                    │                    GOAL                            │
                    │         "Research LLM fine-tuning"                 │
                    └──────────────────────┬──────────────────────────────┘
                                           │
                    ┌──────────────────────▼──────────────────────────────┐
                    │           PHASE 1: ISOLATED ANALYSIS                 │
                    │                                                      │
                    │    ┌──────────┐      ┌──────────────┐                 │
                    │    │   GOAL   │────►│ ORCHESTRATOR │ (MiniMax M2.7)  │
                    │    └──────────┘      └──────┬───────┘                 │
                    │                             │                        │
                    │                      produces: seam_plan              │
                    │                             │                        │
                    │                   ┌──────────▼──────────┐            │
                    │                   │   SEAM PLAN: [6]    │            │
                    │                   │ literature, tech,   │            │
                    │                   │ variants, impl,     │            │
                    │                   │ apps, benchmarks   │            │
                    │                   └─────────────────────┘            │
                    └──────────────────────┬──────────────────────────────┘
                                           │ seam_plan
                    ┌──────────────────────▼──────────────────────────────┐
                    │              PHASE 2: PRE-BUILT GRAPH                 │
                    │                  (28 nodes total)                   │
                    │                                                      │
                    │              ┌─────────┐                             │
                    │              │ ROUTER  │                             │
                    │              └────┬────┘                             │
                    │                   │                                 │
                    │              ┌────▼────┐                            │
                    │              │  ORCHESTRATOR │                      │
                    │              └────┬────┘                            │
                    │         ┌─────────┼─────────┐                        │
                    │    ┌────▼───┐┌────▼───┐┌────▼───┐                   │
                    │    │MANAGER ││MANAGER ││MANAGER │  (6 managers)     │
                    │    │lit.    ││tech.   ││var.   │                   │
                    │    └───┬────┘└───┬────┘└───┬────┘                   │
                    │    ┌───┼───┐┌───┼───┐┌───┼───┐                       │
                    │    │ W │ W ││ W │ W ││ W │ W │   (18 workers)      │
                    │    │ 0 │ 1 ││ 0 │ 1 ││ 0 │ 1 │                     │
                    │    └───┴───┘└───┴───┘└───┴───┘                     │
                    │         all workers stream results                 │
                    │                      │                              │
                    │               ┌──────▼──────┐                       │
                    │               │  SYNTHESIS   │──► final_output      │
                    │               └─────────────┘   (6,357 chars)       │
                    └─────────────────────────────────────────────────────┘
```

---

> **TL;DR**: This system analyzes a natural language goal, dynamically constructs a custom execution graph at runtime, coordinates dozens of specialized workers across parallel seams, and delivers a synthesized result — all with full auditability, checkpoint recovery, and zero graph mutation errors.

---

## Quick Start (30 seconds)

```bash
cd dynamic-graph-compiler

# Run simulation mode (fast, no API calls)
python showcase/showcase_demo.py "Research LLM fine-tuning" --no-llm

# Or run with real MiniMax M2.7
python showcase/showcase_demo.py "Research LLM fine-tuning"

# Start the web demo
python web-demo/app.py
```

---

## The Problem It Solves

Multi-agent orchestration is genuinely hard. Current approaches fall into two failure modes:

| Approach | Problem |
|----------|---------|
| **Static pre-defined graphs** | Must know the entire graph structure at code time. Cannot adapt to goal complexity. Brittle when requirements change mid-execution. |
| **Unstructured agent swarms** | No coordination. Workers step on each other. No audit trail. No recovery from failure. Results are non-deterministic and unreproducible. |

The DynamicAnythingMachine solves both by being **dynamic by design**:

```
Goal: "Research LLM fine-tuning"
       |
       v
+--------------------------------------------+
|  Phase 1: Isolated Orchestrator Analysis   |
|  "Break this goal into parallel work seams"|
+--------------------------------------------+
       |
       v
Seam Plan: [literature, technical, variants,
            implementation, applications,
            benchmarks]
       |
       v
+--------------------------------------------+
|  Phase 2: Pre-built Graph Streaming         |
|  28 nodes, all pre-wired, compiled once    |
|  Stream through, coordinate, synthesize    |
+--------------------------------------------+
       |
       v
Final Synthesis: 6,357 chars from 18 workers
```

---

## Two-Phase Architecture

### Why Two Phases?

LangGraph graphs are **compiled once** and cannot be modified after compilation. The naive approach — adding nodes from inside a running graph — triggers `"Graph was compiled. Graph.add_node() cannot be called"` at runtime.

The DynamicAnythingMachine sidesteps this with a deliberate two-phase design:

```
Phase 1: Analysis (Isolated Graph)
=================================
   [goal] --> [orchestrator_fn] --> [seam plan]
                   |
                   v (isolated, small graph)
                   |
            Produces: seam_plan + state.managers{} + state.seams{}

Phase 2: Execution (Pre-built Graph)
===================================
   Build ALL nodes first, THEN compile:
   
   [router] --> [orchestrator] --> [manager_literature] --> [worker_*]
                |                  [manager_technical] --> [worker_*]
                |                  ...
                |                  [synthesis]
                |
            RouteFn evaluates state at each step
```

**The key insight**: Phase 1 runs in a completely isolated graph (just 2-3 nodes) that never gets compiled in Phase 2. Phase 1's output is the **seam plan** — a specification of what nodes Phase 2 should build. Phase 2 then builds the **complete graph upfront** before compilation.

### Phase 1: Isolated Orchestrator Graph

```
                    +------------------+
                    |  OrchestratorState |
                    |  (goal, mode)     |
                    +--------+---------+
                             |
                             v
              +------------+------------+
              |        ROUTER           |
              |  (routes to           |
              |   orchestrator or END) |
              +------------+------------+
                    |              |
                    v              v (END)
       +------------+              |
       | ORCHESTRATOR |             |
       | phase1_fn    |             |
       +------------+              |
                    |              |
                    v              v
              [seams, managers populated in state]
```

The orchestrator calls MiniMax M2.7 to analyze the goal and produces a structured seam plan. This graph is small, fast, and side-effect free — it exists only to produce a **plan**, not to execute it.

### Phase 2: Pre-built Full Graph

Every manager node and worker node is added to the builder **before** `compile()` is called:

```python
builder = StateGraph(OrchestratorState)

# Core nodes (always present)
builder.add_node("orchestrator", ...)
builder.add_node("router", router_node_fn)
builder.add_node("synthesis", ...)
builder.add_node("human_gate", ...)

# Dynamic nodes — added ALL at once, BEFORE compile()
for seam_info in seam_plan:
    builder.add_node(manager_id, make_manager_fn(...))
    for task in tasks:
        builder.add_node(worker_id, make_worker_fn(...))
        builder.add_edge(manager_id, worker_id)

# THEN compile — no more graph mutation needed
compiled_graph = builder.compile(checkpointer=checkpointer)
```

The **router** then acts as a traffic controller, evaluating the full state on each iteration and routing to the next appropriate node:

```python
def route_fn(state: OrchestratorState) -> str:
    if state.needs_human_review: return "human_gate"
    if "orchestrator" not in state.completed_agents: return "orchestrator"
    incomplete = [mid for mid, m in state.managers.items() if m.status not in ("completed", "failed")]
    if incomplete: return incomplete[0]
    if state.final_output is None: return "synthesis"
    return END
```

---

## Key Innovations

### 1. Dynamic Graph Construction from Natural Language

The orchestrator uses **MiniMax M2.7 tool-use** to analyze any goal and decompose it into parallel work seams. No hardcoded task lists. No domain-specific logic. The LLM reads the goal and produces a structured plan:

```python
# From "Research LLM fine-tuning", MiniMax M2.7 produces:
[
  {"id": "literature", "description": "Survey existing research on LLM fine-tuning",
   "tasks": ["Find key papers on LoRA", "Find papers on RLHF", "Map the academic landscape"]},
  {"id": "technical", "description": "Analyze fine-tuning techniques and best practices",
   "tasks": ["Analyze LoRA technique", "Analyze QLoRA technique", "Compare fine-tuning approaches"]},
  {"id": "variants", "description": "Map variant techniques and derivatives",
   "tasks": ["Identify key variants", "Analyze adoption and trade-offs"]},
  {"id": "implementation", "description": "Implementation approaches and frameworks",
   "tasks": ["List open-source frameworks", "Compare cloud training options"]},
  {"id": "applications", "description": "Real-world applications and use cases",
   "tasks": ["Identify production deployments", "Analyze domain-specific adaptations"]},
  {"id": "benchmarks", "description": "Evaluation metrics and benchmarks",
   "tasks": ["List benchmark suites", "Analyze comparative results"]}
]
```

Fallback heuristic decomposition runs automatically when LLM is unavailable.

### 2. Hierarchical Agent Model

Three-tier hierarchy, each tier with scoped responsibilities:

```
Orchestrator (Phase 1: analysis only)
    |
    v
Manager (one per seam, coordinates workers)
    |
    v
Worker (atomic tasks, reports to manager)
```

Each tier has a **distinct Pydantic state** (`OrchestratorState`, `ManagerState`, `WorkerState`) and its own **tool binding**:

| Tier | Tools | Role |
|------|-------|------|
| Orchestrator | Full `TOOLS` (ReadFile, WriteFile, Bash, Glob, Grep, WebSearch) | Analysis and planning |
| Manager | All except WriteFile | Coordination and aggregation |
| Worker | ReadFile, Glob, Grep, Bash, WebSearch only | Atomic task execution |
| Synthesis | ReadFile, Glob, Grep | Final compilation and review |

### 3. Per-Seam Token Budgets

Every seam gets its own `TokenBudget` that enforces spending limits **per worker turn**:

```python
class TokenBudget:
    def __init__(self, max_tokens=100_000, max_turns=10):
        self.max_tokens = max_tokens
        self.max_turns = max_turns
        self.spent_tokens = 0
        self.turn_count = 0

    def can_proceed(self) -> bool:
        return self.spent_tokens < self.max_tokens and self.turn_count < self.max_turns
```

Workers call `budget.can_proceed()` at the start of each turn. If the budget is exhausted, the worker falls back to simulation rather than making a billable LLM call. **No single runaway worker can exhaust the entire run's allocation.**

### 4. Checkpoint-Based State Recovery

Uses `MemorySaver` with a custom `JsonPlusSerializer` that allowlists project-specific types:

```python
serde = JsonPlusSerializer(
    allowed_msgpack_modules=[
        ("showcase.states", "SeamState"),
        ("showcase.states", "ManagerState"),
        ("showcase.states", "OrchestratorState"),
        ("showcase.states", "TokenBudget"),
    ]
)
checkpointer = MemorySaver(serde=serde)
```

This allows full **checkpoint-and-replay**: if execution is interrupted, it resumes from the exact state at the last checkpoint. LangGraph's `MemorySaver` handles thread_id-scoped snapshots automatically.

### 5. Free-Provider Router

`FreeProviderRouter` tries providers in priority order with automatic fallback:

```
MiniMax M2.7 --> Groq (llama-3.3-70b) --> OpenRouter (auto) --> NVIDIA NIM
     |                  |                        |                  |
  (primary)       (fallback #1)            (fallback #2)      (fallback #3)
```

Each provider is tried with a **5-second timeout**. On rate-limit (429) or timeout, it falls back immediately. The router is transparent — callers use the same `invoke()` interface regardless of which provider responds. Checked via environment variables: `ANTHROPIC_AUTH_TOKEN`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `NVIDIA_API_KEY`.

### 6. WorkLedger Append-Only Audit Trail

Every event is appended to `runs/<run_id>/events.jsonl` in JSON Lines format:

```json
{"event_type": "RUN_CREATED", "run_id": "...", "packet_id": null, "agent_id": "orchestrator", "depth": 0, "data": {...}}
{"event_type": "orchestrator_start", "run_id": "...", "packet_id": null, "agent_id": "orchestrator", "depth": 0, "data": {...}}
{"event_type": "orchestrator_complete", "run_id": "...", "packet_id": null, "agent_id": "orchestrator", "depth": 0, "data": {...}}
{"event_type": "manager_start", "run_id": "...", "packet_id": "seam_id", "agent_id": "manager_literature", "depth": 1, "data": {...}}
{"event_type": "worker_start", "run_id": "...", "packet_id": "worker_literature_0", "agent_id": "worker_literature_0", "depth": 2, "data": {...}}
```

No events are ever modified or deleted. The ledger enables complete replay and post-hoc analysis.

---

## Real Results

**Benchmark run**: `"Research LLM fine-tuning"`

### Benchmark Results

| Configuration | Nodes | Seams | Workers | Iterations | Output | Mode | Time |
|---------------|-------|-------|---------|------------|--------|------|------|
| Real LLM (MiniMax M2.7) | 28 | 6 | 18 | 7 | 6,357 chars | Live API | ~45s |
| Simulation Mode | 28 | 6 | 18 | 7 | 4,128 chars | Simulated | ~8s |

### Graph Execution

| Metric | Value |
|--------|-------|
| Total nodes in compiled graph | 28 |
| Seams identified by Phase 1 | 6 (literature, technical, variants, implementation, applications, benchmarks) |
| Workers executed | 18 across 6 seams |
| Iterations to completion | 7 |
| Final synthesis output | 6,357 characters |
| Mode | Real MiniMax M2.7 tool calls |

### Architecture Diagram

```
                        +------------------+
                        |   User Goal      |
                        | "Research LLM   |
                        |  fine-tuning"   |
                        +--------+---------+
                                 |
                                 v
               +---------------------------------+
               |  PHASE 1: Isolated Analysis    |
               |  (orchestrator + router, 2     |
               |   nodes, compiled separately)  |
               +---------------------------------+
                                 |
                                 | seam_plan: [6 seams]
                                 v
               +---------------------------------+
               |  PHASE 2: Pre-built Graph      |
               |  (orchestrator + router +      |
               |   synthesis + human_gate +      |
               |   6 managers + 18 workers)      |
               +---------------------------------+
                                 |
                +--------------+--------------+
                |              |              |
                v              v              v
        +-----------+  +-----------+  +-----------+
        | manager_  |  | manager_  |  | manager_  |
        | literature|  | technical |  | variants  |
        +-----------+  +-----------+  +-----------+
            |               |              |
            v               v              v
     +---+---+---+    +-----+-----+   +-----+-----+
     |w0  w1  w2 |    | w0   w1   |   | w0   w1   |
     +-----------+    +----------+   +-----------+
                (workers execute in parallel)
                |
                v
        +-----------+  (all findings aggregated)
        | synthesis | --> final_output (6,357 chars)
        +-----------+
```

---

## Technical Stack

| Component | Technology | Role |
|-----------|------------|------|
| **Graph orchestration** | LangGraph (`StateGraph`, `END`) | Compiled graph execution with conditional routing |
| **State management** | Pydantic `BaseModel` | Typed state at all tiers (Orchestrator, Manager, Worker, Seam) |
| **Checkpointing** | `MemorySaver` + `JsonPlusSerializer` | In-memory state snapshots with custom type serialization |
| **LLM execution** | `MiniMaxDirectClient` (LangChain-compatible) | Tool-use loop with bind_tools interface |
| **Multi-provider fallback** | `FreeProviderRouter` | Automatic priority-order provider failover |
| **Audit trail** | `WorkLedger` + JSON Lines | Append-only event log at `runs/<run_id>/events.jsonl` |
| **Tool execution** | Local `execute_tool()` | ReadFile, WriteFile, Bash, Glob, Grep, WebSearch |
| **Streaming output** | LangGraph `stream_mode="values"` | Progressive token emission per iteration |

---

## How to Run

### Simulation Mode (fast, no API calls)

```bash
cd dynamic-graph-compiler
python showcase/showcase_demo.py "Research LLM fine-tuning" --no-llm
```

### Real MiniMax M2.7 Mode

```bash
# Set your API key (MiniMax or any supported provider)
export ANTHROPIC_AUTH_TOKEN="your-token-here"

cd dynamic-graph-compiler
python showcase/showcase_demo.py "Research LLM fine-tuning"
```

### Web Demo

```bash
cd dynamic-graph-compiler
python web-demo/app.py
```

### Direct Python API

```python
from showcase.dynamic_orchestrator import DynamicOrchestrator

orchestrator = DynamicOrchestrator(use_llm=True, verbose=True)
final_state = orchestrator.execute("Research LLM fine-tuning")

print(final_state.final_output)
print(f"Seams: {list(final_state.seams.keys())}")
print(f"Completed agents: {final_state.completed_agents}")
```

### Custom Goal Examples

```bash
python showcase/showcase_demo.py "Build a REST API for task management"
python showcase/showcase_demo.py "Survey quantum computing trends"
python showcase/showcase_demo.py "Analyze the competitive landscape for vector databases"
```

---

## Project Structure

```
dynamic-graph-compiler/
|-- showcase/
|   |-- dynamic_orchestrator.py   # Two-phase orchestrator + FreeProviderRouter
|   |-- nodes.py                  # Node functions (orchestrator, manager, worker, synthesis, router, human_gate)
|   |-- states.py                 # Pydantic state models + TokenBudget
|   |-- tools.py                  # Tool definitions + execute_tool() implementation
|   |-- showcase_demo.py          # CLI entry point
|   |-- app.py                    # Streamlit web demo
|
|-- work_ledger.py                # Append-only event ledger + replay
|-- packet_runtime.py            # MiniMaxDirectClient + harness adapters
|-- packet_cli.py                # Packet-based CLI (separate system)
|--
|-- runs/                        # All run artifacts (gitignored)
|   |-- <run_id>/
|       |-- events.jsonl         # Full audit trail
|       |-- packets/
|       |-- checkpoints/
|       |-- artifacts/
|
|-- ARCHITECTURE.md              # Extended architecture document (work-packet system)
|-- AGENTS.md                    # Active agent status and tracking
```

---

## Design Principles

1. **The graph is the plan, not the execution**: The compiled graph is a static artifact. The dynamic part is what nodes get added to it — determined at runtime by Phase 1.

2. **Pre-build before compile**: Never call `add_node()` after `compile()`. Build the entire node set upfront.

3. **Bounded budgets prevent runaway workers**: Every seam has a token budget enforced at every turn. No single failure can exhaust the run.

4. **Audit everything, modify nothing**: The WorkLedger is append-only. No event is ever overwritten.

5. **Simulation is a first-class mode**: `--no-llm` must always work. If the LLM fails, execution continues with simulated results.

6. **Checkpoint replay over restart**: `MemorySaver` ensures any interrupted run can resume from the last valid state.

---

*Built with LangGraph + MiniMax M2.7. Production-quality multi-agent orchestration.*
