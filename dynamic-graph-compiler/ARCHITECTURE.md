# Dynamic Graph Compiler — Recursive Work-Packet Runtime

## Architecture Document

> **Design Philosophy**: "Agents are disposable; work state is durable. Intelligence comes from the packet lifecycle, shape memory, and resumability — not from assuming one perfect model will remember everything."

---

## Table of Contents

1. [Core Principles](#1-core-principles)
2. [System Overview](#2-system-overview)
3. [Work Packet](#3-work-packet)
4. [Work Ledger](#4-work-ledger)
5. [Shape Registry](#5-shape-registry)
6. [Packet Runtime](#6-packet-runtime)
7. [Harness Protocol](#7-harness-protocol)
8. [CLI Interface](#8-cli-interface)
9. [File Protocol](#9-file-protocol)
10. [Packet Lifecycle](#10-packet-lifecycle)
11. [Delegation Model](#11-delegation-model)
12. [Resumption and Recovery](#12-resumption-and-recovery)
13. [Security Model](#13-security-model)
14. [Component Inventory](#14-component-inventory)

---

## 1. Core Principles

### 1.1 The Packet Is Everything

The **Work Packet** is the single core primitive of this system. Everything else — agents, harnesses, models, threads — is transient. Only the packet and its associated ledger survive.

```
Agent (Claude Code, Codex, Qwen, Gemini CLI, human)
    |
    +-- picks up --> Work Packet (durable)
    |                    |
    |                    +-- does work
    |                    +-- checkpoints state
    |                    +-- optionally spawns child packets
    |                    +-- eventually completes or fails
    |
    +-- disconnects (agent is disposable)
```

### 1.2 Intelligence Is Structural, Not Magical

The intelligence of this system comes from:

1. **What gets saved** — every decision, state change, and artifact is durable
2. **Shape memory** — reusable patterns emerge from repeated execution
3. **Resumability** — any harness can pick up where another left off

This is **not** a RAG system. There is no semantic retrieval at the foundation. Memory is a later layer.

### 1.3 Determinism from State, Not Models

Given the same `packet_id` and the same checkpoint state, execution will behave identically regardless of which harness resumes it. Model non-determinism is contained by the checkpoint/replay contract.

### 1.4 Seams, Not Roles

"planner" is **not** a job title you join. It is a **seam** you claim when working on planning. Seams are temporary, self-organizing, and defined by the work itself.

### 1.5 Human Gates Are Explicit

Side effects (file writes, shell commands, network calls, deployments) require explicit human approval recorded in the ledger. There are no implicit trust assumptions.

---

## 2. System Overview

### 2.1 High-Level Data Flow

```
                           +------------------+
                           |  Goal (string)   |
                           +--------+---------+
                                    |
                                    v
                    +-------------------------------+
                    |      Packet Runtime          |
                    |   create_goal(objective)     |
                    +---------------+---------------+
                                    |
                                    v
                    +-------------------------------+
                    |        Work Packet            |
                    |   (packet_id, run_id, seam)   |
                    +---------------+---------------+
                                    |
          +-------------------------+-------------------------+
          |                         |                         |
          v                         v                         v
   +--------------+         +--------------+         +--------------+
   | Work Ledger  |         |  Shape       |         |   Artifact  |
   | (append-only |         |  Registry     |         |   Storage   |
   |  events)     |         | (reusable     |         | (durable    |
   |              |         |  shapes)      |         |  outputs)   |
   +--------------+         +--------------+         +--------------+
          ^                         ^                         ^
          |                         |                         |
          +-------------------------+-------------------------+
                                    |
                         +----------+---------+
                         |  Harness Protocol |
                         |  (file-based)     |
                         +----------+---------+
                                    ^
                                    |
                    +---------------+---------------+
                    |  Any Agent Harness          |
                    |  (Claude Code, Codex,       |
                    |   Qwen, Gemini CLI, human)   |
                    +-------------------------------+
```

### 2.2 Component Responsibilities

| Component | Responsibility |
|-----------|----------------|
| **Packet Runtime** | Orchestrates packet lifecycle: create, claim, progress, delegate, complete, resume |
| **Work Ledger** | Append-only event log for audit trail and replay |
| **Shape Registry** | Durable store for reusable patterns, templates, and skill definitions |
| **Harness Protocol** | File-based interface that any agent harness can implement |
| **CLI** | Human-facing interface for operating the runtime |

---

## 3. Work Packet

### 3.1 Data Model

The `WorkPacket` is the central data structure. It is self-contained — a resuming agent can pick up any packet and understand its full context by reading the packet and the ledger.

```python
class WorkPacket(BaseModel):
    # Identity
    packet_id: str           # UUID, globally unique
    run_id: str              # Parent run identifier
    parent_packet_id: str | None  # Delegating packet (None for root)
    depth: int               # Delegation depth (0 = root)

    # What this packet owns
    seam: str                # What this packet claims ownership of (e.g. "auth-flow")
    objective: str           # What needs to be accomplished
    current_state: dict      # JSON snapshot of work-in-progress
    constraints: dict        # Tools, paths, budget, time limits
    permissions: dict        # Delegation level, approval gates needed

    # What this packet knows
    available_artifacts: list[str]  # Paths to reusable resources
    known_context: dict       # What previous packets have established
    open_questions: list[str] # Things this packet doesn't know but needs

    # How we know if we're done
    success_criteria: list[str]    # How we know this is done
    suggested_next_seams: list[str] # What to delegate next

    # What this packet created for others
    reusable_shapes_discovered: list[str]  # shape_ids created during execution
    audit_trail: list[str]      # List of event_ids

    # Resumption contract
    resumption_contract: dict   # What a resuming agent needs to know

    # Timestamps
    created_at: str
    updated_at: str
    claimed_at: str | None
    completed_at: str | None
```

### 3.2 Seam: The Temporary Ownership Claim

The `seam` field is the fundamental organizational unit. It is a freeform string describing what portion of the work this packet owns.

**Examples:**
- `"auth-flow"` — handles all authentication logic
- `"file-processing"` — handles file parsing and transformation
- `"report-generation"` — owns the final report assembly
- `"api-integration"` — owns external API communication
- `"code-sweep"` — parallel code review across a codebase

**Properties of seams:**
- Seams are **claimed**, not assigned. Any packet can claim any seam if it has the right permissions.
- Seams can be **nested** via the delegation chain (parent_packet_id creates child with narrower seam).
- Seams are **discoverable** — the ledger records which seams are open for claiming.

### 3.3 Constraints: What This Packet Cannot Do

```python
class Constraints(BaseModel):
    allowed_tools: list[str]           # Tools this packet may use
    forbidden_tools: list[str]         # Tools this packet may not use
    allowed_paths: list[str]             # File system paths accessible
    forbidden_paths: list[str]           # File system paths forbidden
    budget: float | None                # Cost ceiling in USD
    timeout_seconds: int | None          # Max execution time
    max_delegation_depth: int           # How deep children can go
    side_effects_require_approval: list[str]  # Side effect types needing human gate
```

### 3.4 Resumption Contract: What a Resuming Agent Needs

```python
class ResumptionContract(BaseModel):
    packet_id: str
    seam: str
    last_checkpoint: str | None         # checkpoint_id or None
    work_summary: str                   # Human-readable summary of current state
    what_is_done: list[str]             # Completed sub-tasks
    what_is_pending: list[str]          # Remaining sub-tasks
    key_decisions: list[dict]            # Significant choices made so far
    open_questions: list[str]           # Unanswered questions
    suggested_next_action: str           # What to do next
    parent_context: str | None          # What the parent packet knows
    child_summaries: list[dict]         # What children have completed
```

---

## 4. Work Ledger

### 4.1 Purpose

The Work Ledger is an **append-only** event log. It provides complete auditability, enables replay, and supports the resumption contract.

### 4.2 Event Types

| Event | Description |
|-------|-------------|
| `CREATED` | Packet initialized with goal |
| `EMITTED` | Written to disk for pickup |
| `CLAIMED` | Agent took ownership (agent_id, harness_type, claimed_at) |
| `PROGRESS` | State checkpoint |
| `SHAPE_CREATED` | Packet created a reusable shape |
| `CHILD_REQUESTED` | Delegation request made |
| `CHILD_APPROVED` | Delegation approved |
| `CHILD_DENIED` | Delegation rejected |
| `HUMAN_GATE` | Paused for approval |
| `COMPLETED` | Terminal success |
| `FAILED` | Terminal failure |
| `RESUMED` | Another agent took over |
| `ARTIFACT_WRITTEN` | Durable artifact saved |

### 4.3 Event Schema

```python
class LedgerEvent(BaseModel):
    event_id: str           # UUID
    run_id: str
    packet_id: str
    event_type: str          # One of the event types above
    timestamp: str            # ISO 8601 UTC
    agent_id: str | None      # Who performed this action
    harness_type: str | None # "claude-code", "codex", "qwen", "human", etc.
    depth: int                # Delegation depth at time of event
    data: dict                # Event-specific payload
    masked_fields: list[str]  # Redacted secret fields
```

### 4.4 Secret Detection

The ledger automatically detects and redacts secrets (API keys, tokens, passwords, private keys) before storing events. Detected secrets are logged with a hash prefix only.

### 4.5 Ledger Integrity

The ledger is append-only. Events are never modified or deleted. Integrity can be verified by:
- Checking chronological ordering of timestamps
- Validating event_id uniqueness
- Verifying packet_id references exist
- Confirming state transitions are valid (e.g., no COMPLETED -> PROGRESS)

---

## 5. Shape Registry

### 5.1 Purpose

The Shape Registry is a durable store of reusable patterns discovered during execution. Any packet can save a shape; any packet can look up a shape.

### 5.2 Shape Types

```python
class ShapeType(str, Enum):
    PROMPT_SHAPE = "prompt_shape"           # Reusable prompt templates with slots
    SKILL_DEFINITION = "skill_definition"   # Tool + prompt + budget bundle
    PACKET_TEMPLATE = "packet_template"     # Pre-built packet patterns
    ORCHESTRATION_SHAPE = "orchestration_shape"  # Multi-packet coordination patterns
    EVALUATION_CHECK = "evaluation_check"   # Success criteria definitions
```

### 5.3 Shape Schema

```python
class Shape(BaseModel):
    shape_id: str            # UUID
    shape_type: ShapeType
    name: str                 # Human-readable name
    tags: list[str]          # For lookup: ["auth", "api", "retry"]
    seam: str | None          # Associated seam (optional)
    keywords: list[str]       # For lookup

    # Shape data (varies by type)
    data: dict                # Type-specific payload

    # Provenance
    created_by_packet_id: str  # Which packet discovered this
    created_at: str
    use_count: int            # How many times this shape was applied
    last_used_at: str | None

    # Content (varies by type)
    prompt_template: str | None       # For PROMPT_SHAPE
    tools: list[str] | None           # For SKILL_DEFINITION
    constraints: dict | None          # For PACKET_TEMPLATE
    orchestration_pattern: dict | None # For ORCHESTRATION_SHAPE
    criteria: list[str] | None        # For EVALUATION_CHECK
```

### 5.4 Shape Lookup

Shapes are looked up by:
- **Tags** — exact match on tags
- **Seam** — match by seam name
- **Keywords** — substring match on keywords
- **Use count** — sort by most-used first
- **Recency** — sort by last_used_at

```python
def lookup_shapes(
    tags: list[str] | None = None,
    seam: str | None = None,
    keywords: list[str] | None = None,
    shape_type: ShapeType | None = None,
    sort_by: str = "use_count"
) -> list[Shape]
```

---

## 6. Packet Runtime

### 6.1 Core API

```python
class PacketRuntime:
    def __init__(self, runs_dir: Path):
        self.runs_dir = runs_dir
        self.ledger = WorkLedger(runs_dir)
        self.shape_registry = ShapeRegistry(runs_dir)

    # ---- Lifecycle ----

    def create_goal(self, objective: str, constraints: dict | None = None) -> str:
        """
        Create a new goal, returns packet_id.
        The goal becomes the first packet in a new run.
        """
        run_id = str(uuid.uuid4())
        packet_id = run_id  # Root packet shares run_id
        # ... create packet, emit, log CREATED + EMITTED
        return packet_id

    def claim_packet(self, packet_id: str, agent_id: str, harness: str) -> str:
        """
        Agent claims ownership of a packet.
        Returns event_id.
        Logs CLAIMED event.
        """
        # ... update packet claimed_at, write to disk
        # ... log CLAIMED event
        return event_id

    def progress_packet(
        self,
        packet_id: str,
        state_update: dict,
        artifacts: list[str] | None = None
    ) -> str:
        """
        Checkpoint packet state.
        Returns event_id.
        Logs PROGRESS event.
        """
        # ... merge state_update into current_state
        # ... update updated_at
        # ... log PROGRESS event
        return event_id

    def delegate_packet(
        self,
        packet_id: str,
        child_seam: str,
        child_constraints: dict | None = None,
        child_objective: str | None = None
    ) -> str:
        """
        Request delegation to a child packet.
        Returns child_packet_id.
        Logs CHILD_REQUESTED event.
        """
        child_packet_id = str(uuid.uuid4())
        # ... create child packet with narrower constraints
        # ... log CHILD_REQUESTED event
        return child_packet_id

    def approve_delegation(self, child_packet_id: str, approved: bool = True) -> str:
        """
        Approve or deny a delegation request.
        Returns event_id.
        Logs CHILD_APPROVED or CHILD_DENIED event.
        """
        # ... update child packet status
        # ... log CHILD_APPROVED or CHILD_DENIED
        return event_id

    def save_shape(
        self,
        packet_id: str,
        shape_type: ShapeType,
        shape_data: dict
    ) -> str:
        """
        Save a reusable shape discovered during execution.
        Returns shape_id.
        Logs SHAPE_CREATED event.
        """
        shape_id = str(uuid.uuid4())
        # ... save to shape registry
        # ... log SHAPE_CREATED event
        return shape_id

    def complete_packet(
        self,
        packet_id: str,
        output: dict,
        shapes_discovered: list[str] | None = None
    ) -> str:
        """
        Mark packet as completed.
        Returns event_id.
        Logs COMPLETED event.
        """
        # ... update packet completed_at
        # ... write final output to artifacts/
        # ... log COMPLETED event
        return event_id

    def fail_packet(self, packet_id: str, error: str) -> str:
        """
        Mark packet as failed.
        Returns event_id.
        Logs FAILED event.
        """
        # ... update packet state with error
        # ... log FAILED event
        return event_id

    def resume_packet(self, packet_id: str, agent_id: str, harness: str) -> str:
        """
        Pick up an abandoned packet.
        Returns event_id.
        Logs RESUMED event.
        """
        # ... load packet state from latest checkpoint
        # ... update claimed_at, agent_id, harness
        # ... log RESUMED event
        return event_id

    # ---- Queries ----

    def list_seams(self, run_id: str | None = None) -> list[dict]:
        """
        Return open seams across all runs or a specific run.
        Each entry: {packet_id, seam, run_id, status, age}
        """
        # ... scan packets directory, return claimable packets
        pass

    def get_packet_status(self, packet_id: str) -> dict:
        """
        Return packet state and audit trail.
        """
        # ... load packet, load events, return summary
        pass

    def list_shapes(
        self,
        shape_type: ShapeType | None = None,
        tags: list[str] | None = None
    ) -> list[Shape]:
        """
        List available shapes with optional filters.
        """
        pass

    def use_shape(self, shape_id: str, objective: str) -> str:
        """
        Apply a shape to create a new goal.
        Returns new packet_id.
        Increments shape use_count.
        """
        # ... load shape, apply to new goal, create packet
        pass

    def get_run_history(self, run_id: str) -> list[LedgerEvent]:
        """
        Return full audit trail for a run.
        """
        pass
```

### 6.2 State Transitions

```
                    CREATED
                        |
                        v
                    EMITTED
                        |
                        v
          +--------> CLAIMED <--------+
          |            |              |
          |            v              |
          |       PROGRESS            |
          |            |              |
          |            v              |
          |    +-------+-------+
          |    |               |
          |    v               v
          |   SHAPE_CREATED  CHILD_REQUESTED
          |    |               |
          |    |         +-----+-----+
          |    |         v           v
          |    |    CHILD_APPROVED CHILD_DENIED
          |    |         |
          |    |         v
          |    |    (child packet follows same path)
          |    |
          |    +----> HUMAN_GATE (waiting)
          |              |
          |              v
          |     (approval granted/denied)
          |              |
          +------+-------+
                 |
                 v
            COMPLETED
                 |
                 X
               FAILED
```

---

## 7. Harness Protocol

### 7.1 Philosophy

The Harness Protocol is **file-based** and **harness-agnostic**. Any agent — Claude Code, Codex, Qwen, Gemini CLI, a human with a terminal — can participate by reading and writing files in the prescribed locations.

### 7.2 How an Agent Picks Up Work

```
1. Agent calls: list_seams()
   -> Returns: [{packet_id, seam, run_id, status}]

2. Agent reads: runs/<run_id>/packets/<packet_id>.json
   -> Contains: full WorkPacket with current_state, constraints, etc.

3. Agent reads: runs/<run_id>/packets/<packet_id>.md
   -> Contains: human-readable summary

4. Agent calls: claim_packet(packet_id, agent_id, harness)
   -> Writes: packet with claimed_at, agent_id, harness
   -> Appends: CLAIMED event to events.jsonl

5. Agent does work, periodically calling: progress_packet(packet_id, state_update)
   -> Updates: current_state in packet JSON
   -> Appends: PROGRESS events to events.jsonl

6. Agent completes, calls: complete_packet(packet_id, output)
   -> Writes: final output to artifacts/
   -> Appends: COMPLETED event
```

### 7.3 Harness Types

| Harness | Description |
|---------|-------------|
| `claude-code` | Claude Code CLI |
| `codex` | OpenAI Codex |
| `qwen` | Qwen / Alibaba Cloud |
| `gemini-cli` | Google Gemini CLI |
| `human` | Human at a terminal |
| `test` | Automated test harness |

### 7.4 File Observation Protocol

An agent can observe the state of other packets by reading:
- `runs/<run_id>/packets/<other_packet_id>.json` — to see what others are working on
- `runs/<run_id>/events.jsonl` — to see what has happened

This enables **emergent coordination** without a central scheduler.

---

## 8. CLI Interface

### 8.1 Command Reference

```bash
# Goal management
packet-cli goal <objective> [--constraints <json>]   # Create a new goal, returns packet_id
packet-cli status <packet_id>                         # Show state and audit trail
packet-cli history <run_id>                            # Full audit trail for a run

# Packet lifecycle
packet-cli claim <packet_id>                          # Claim a packet for current agent
packet-cli progress <packet_id> --state <json>        # Checkpoint state
packet-cli complete <packet_id> --output <json>       # Mark as completed
packet-cli fail <packet_id> --error <string>          # Mark as failed

# Delegation
packet-cli delegate <packet_id> <seam> [--objective <string>] [--constraints <json>]
                                                        # Request delegation
packet-cli approve <child_packet_id>                   # Approve delegation
packet-cli deny <child_packet_id>                      # Reject delegation

# Shapes
packet-cli save-shape <packet_id> <shape_type> <data>  # Save a reusable shape
packet-cli shapes [--type <type>] [--tags <tags>]       # List available shapes
packet-cli use-shape <shape_id> <objective>             # Apply shape to new goal

# Recovery
packet-cli seams [--run <run_id>]                      # Show open seams
packet-cli resume <packet_id>                           # Pick up abandoned packet

# Artifacts
packet-cli artifacts <run_id>                          # List artifacts for a run
```

### 8.2 Output Format

All CLI commands output structured JSON or human-readable text based on the `--format` flag (`json` or `text`, default `text`).

---

## 9. File Protocol

### 9.1 Directory Structure

```
runs/
└── <run_id>/
    ├── manifest.json              # Run metadata
    │
    ├── packets/
    │   ├── <packet_id>.json       # WorkPacket (self-contained state)
    │   └── <packet_id>.md         # Human-readable summary
    │
    ├── events.jsonl               # Append-only ledger (all events)
    │
    ├── shapes/
    │   └── <shape_id>.json        # Reusable shape definitions
    │
    ├── artifacts/
    │   └── <artifact_id>          # Durable output files
    │
    ├── checkpoints/
    │   └── <checkpoint_id>.json   # Resumption snapshots
    │
    └── final/
        └── <packet_id>.md          # Completed packet outputs
```

### 9.2 File Schemas

#### manifest.json

```json
{
  "run_id": "uuid",
  "objective": "string",
  "status": "running|paused|completed|failed",
  "created_at": "ISO 8601",
  "updated_at": "ISO 8601",
  "root_packet_id": "uuid",
  "total_cost_estimate": 0.0,
  "total_duration_seconds": 0.0
}
```

#### packets/<packet_id>.json

Full WorkPacket schema — see Section 3.1.

#### events.jsonl

One `LedgerEvent` per line (JSON lines format):

```json
{"event_id": "uuid", "run_id": "uuid", "packet_id": "uuid", "event_type": "CREATED", "timestamp": "...", "agent_id": null, "harness_type": null, "depth": 0, "data": {}, "masked_fields": []}
{"event_id": "uuid", "run_id": "uuid", "packet_id": "uuid", "event_type": "CLAIMED", "timestamp": "...", "agent_id": "agent-xyz", "harness_type": "claude-code", "depth": 0, "data": {}, "masked_fields": []}
...
```

#### shapes/<shape_id>.json

Full Shape schema — see Section 5.3.

#### checkpoints/<checkpoint_id>.json

```json
{
  "checkpoint_id": "uuid",
  "packet_id": "uuid",
  "run_id": "uuid",
  "timestamp": "ISO 8601",
  "label": "string",
  "state": {
    "current_state": {},
    "known_context": {},
    "open_questions": [],
    "completed_subtasks": []
  }
}
```

---

## 10. Packet Lifecycle

### 10.1 Full Lifecycle Diagram

```
                            +-----------------+
                            |  CLI: goal      |
                            |  "Build auth    |
                            |   for service X"|
                            +--------+--------+
                                     |
                                     v
                    +--------------------------------+
                    |         CREATED               |
                    |  (packet_id = run_id, depth=0)|
                    +---------------+----------------+
                                    |
                                    v
                    +--------------------------------+
                    |         EMITTED               |
                    |  (written to disk, claimable) |
                    +---------------+----------------+
                                    |
                    +---------------v----------------+
                    |           CLAIMED              |
                    |  agent_id: "claude-code"       |
                    |  harness: "claude-code"        |
                    +---------------+----------------+
                                    |
                                    v
                    +--------------------------------+
                    |        PROGRESS (loop)          |
                    |  - checkpoint state             |
                    |  - optional: save shapes       |
                    |  - optional: delegate children  |
                    |  - optional: human gate        |
                    +---------------+----------------+
                           /         |         \
                          /          |          \
                         v           v           v
              +-----------+   +-----------+  +--------+
              |SHAPE_CREATED|  |CHILD_REQ  |  |PROGRESS|
              +-----------+   +-----------+  +--------+
                               /         \
                              v           v
                    +-----------+  +-----------+
                    |CHILD_APPRVD|  |CHILD_DENIED|
                    +-----------+  +-----------+
                              \         /
                               v       v
                    +----------------------+
                    |   Child packet       |
                    |  (depth = parent+1)  |
                    |  (seam = narrower)   |
                    +----------------------+
                                     |
                                     v
                    +--------------------------------+
                    |     HUMAN_GATE (if needed)   |
                    |  side effect approval         |
                    +---------------+----------------+
                                    |
                    +---------------v----------------+
                    |           COMPLETED            |
                    |  (output to artifacts/)        |
                    +--------------------------------+
                                    |
                                    v
                    +--------------------------------+
                    |         final/<id>.md          |
                    |  (human-readable summary)     |
                    +--------------------------------+
```

### 10.2 Parallel Execution

Multiple packets at the same depth execute **in parallel**. The ledger records the partial ordering via timestamps. Resumption works correctly because each packet's state is independent.

```
Root packet (depth=0)
    |
    +-- delegate("auth-flow") --> child-1 (depth=1, seam="auth-flow")
    |                              |
    |                              v (parallel)
    |                         COMPLETED
    |
    +-- delegate("api-integration") --> child-2 (depth=1, seam="api-integration")
    |                                      |
    |                                      v (parallel)
    |                                 COMPLETED
    |
    v
Parent waits for all children, then COMPLETES
```

### 10.3 Abandonment and Resumption

If an agent disconnects mid-execution:

1. The packet's `claimed_at` is set, but `completed_at` is null
2. Other agents see it as "in progress but potentially abandoned"
3. Any agent can call `resume_packet(packet_id)` to take over
4. The resuming agent loads the latest checkpoint and continues

```
Agent A (CLAIMED) ---- disconnects
                            |
                            v
              Packet shows: claimed_at=..., completed_at=null
                            |
              Agent B calls: resume_packet(packet_id)
                            |
                            v
              RESUMED event logged
              Packet updated: claimed_at=<now>, agent_id=B
                            |
                            v
              Agent B continues from checkpoint
```

---

## 11. Delegation Model

### 11.1 Constraint Inheritance

Child packets inherit constraints from their parent, with **narrowing**:

| Constraint | Child Rule |
|------------|------------|
| `allowed_tools` | Subset of parent's allowed_tools |
| `forbidden_tools` | Union with parent's forbidden_tools |
| `allowed_paths` | Subset of parent's allowed_paths |
| `budget` | At most parent's budget |
| `timeout_seconds` | At most parent's timeout |
| `max_delegation_depth` | parent's max - 1 |

### 11.2 Delegation Permissions

| Permission | Meaning |
|------------|---------|
| `NONE` | Cannot delegate at all |
| `REQUEST` | Can request delegation, must await approval |
| `APPROVE` | Can delegate without approval (within constraints) |

### 11.3 Child Request Flow

```
1. Parent calls: runtime.delegate_packet(packet_id, "auth-flow")
   -> Creates child packet with seam="auth-flow", depth=parent+1
   -> Child status = PENDING_APPROVAL
   -> Logs CHILD_REQUESTED event

2. If parent.permission == APPROVE:
   -> Automatically approve
   -> Child status = EMITTED
   -> Logs CHILD_APPROVED event

3. If parent.permission == REQUEST:
   -> Child stays PENDING_APPROVAL
   -> Human (or automated gatekeeper) calls: runtime.approve_delegation(child_packet_id)
   -> Child status = EMITTED
   -> Logs CHILD_APPROVED or CHILD_DENIED event
```

### 11.4 Delegation Depth

The `max_delegation_depth` field limits how deep the delegation tree can go.

```
depth=0 (root) --> depth=1 --> depth=2 --> depth=3
                   max_delegation_depth=3
                   (3 levels of delegation below root)
```

When `max_delegation_depth` reaches 0, the packet cannot delegate further.

---

## 12. Resumption and Recovery

### 12.1 Checkpoint Strategy

Checkpoints are created:
- Automatically every N minutes (configurable)
- After significant state changes (child completed, shape discovered)
- Before potentially dangerous operations
- Manually via `packet-cli checkpoint <packet_id> --label <label>`

### 12.2 Resumption Protocol

```python
def resume_packet(packet_id: str, agent_id: str, harness: str) -> dict:
    """
    Resume an abandoned packet.

    Steps:
    1. Load the packet from disk
    2. Find the latest checkpoint
    3. Restore current_state from checkpoint
    4. Update claimed_at, agent_id, harness
    5. Log RESUMED event
    6. Return resumption_contract for the resuming agent
    """
```

### 12.3 Determinism Guarantee

The resumption contract guarantees:

```
Same packet_id + Same checkpoint_id
    = Same current_state
    = Same known_context
    = Same open_questions
    = Same suggested_next_action
```

The **model** may produce different output on resumption, but the **state** is identical. This is the fundamental difference between this system and a system that relies on model memory.

---

## 13. Security Model

### 13.1 No Implicit Trust

- Every side effect requires explicit approval
- Constraints are enforced at the packet level, not just at delegation time
- The ledger provides complete traceability

### 13.2 Secret Handling

- The ledger automatically detects API keys, tokens, passwords, private keys
- Detected secrets are replaced with `***REDACTED***` in stored events
- Original secrets never appear in events.jsonl

### 13.3 Approval Gates

Side effects requiring approval are defined per-packet:

```python
permissions = {
    "delegation_permission": "APPROVE",  # Can delegate freely
    "side_effects_requiring_approval": [
        "file_write",
        "shell_command",
        "network_call",
        "deploy"
    ]
}
```

### 13.4 Constraint Boundaries

Constraints are **not advisory**. They are enforced:
- At delegation time (child cannot exceed parent constraints)
- At checkpoint time (runtime validates constraint compliance)
- At completion time (success criteria must be met)

---

## 14. Component Inventory

### 14.1 File Map

| File | Purpose | Format |
|------|---------|--------|
| `work_packet.py` | WorkPacket data model | Pydantic model |
| `work_ledger.py` | Append-only event log + queries | Class + event types |
| `shape_registry.py` | Reusable shape storage + lookup | Class + Shape model |
| `packet_runtime.py` | Core orchestration API | Class with lifecycle methods |
| `harness_protocol.py` | File-based agent interface | Protocol documentation |
| `packet_cli.py` | Human CLI interface | argparse + command handlers |
| `runs/.gitignore` | Prevents artifact bloat | Git ignore patterns |

### 14.2 Key Data Flow

```
CLI command
    |
    v
packet_cli.py handler
    |
    v
PacketRuntime method
    |
    +--> WorkLedger.log_event() --> runs/<run_id>/events.jsonl
    |
    +--> ShapeRegistry.save_shape() / lookup_shapes()
    |
    +--> File system: runs/<run_id>/packets/<packet_id>.json
    |
    +--> File system: runs/<run_id>/shapes/<shape_id>.json
    |
    +--> File system: runs/<run_id>/artifacts/<artifact_id>
```

### 14.3 Module Boundaries

```
packet_cli.py
    |
    v
packet_runtime.py
    |
    +-- work_ledger.py (audit events)
    |
    +-- shape_registry.py (reusable patterns)
    |
    +-- harness_protocol.py (file conventions)
```

---

## Appendix A: Migration from v1 Architecture

### A.1 Terminology Mapping

| v1 Term | v2 Term | Notes |
|---------|---------|-------|
| `GraphNode` | `WorkPacket` | Nodes are rigid; packets are self-organizing |
| `GraphSpec` | `run_id` + `WorkPacket` tree | No fixed graph; seams define structure |
| `WorkerPacket` | `WorkPacket` | Expanded with seam, shapes, resumption_contract |
| `NodeType` | `seam` | Roles are temporary seams, not departments |
| `GraphCompiler` | `PacketRuntime` | Compilation is implicit; orchestration is dynamic |
| `Ledger` | `WorkLedger` | Expanded event types including shapes |
| (new) | `ShapeRegistry` | No equivalent in v1 |
| `ExecutionEngine` | `PacketRuntime` | Same function, different philosophy |
| `ProfilePolicy` | `constraints` | Constraints are per-packet, not global |

### A.2 File Protocol Changes

| v1 Path | v2 Path | Notes |
|---------|---------|-------|
| `runs/<run_id>/packets/<worker_id>.json` | `runs/<run_id>/packets/<packet_id>.json` | Renamed field |
| `runs/<run_id>/results/<worker_id>.json` | `runs/<run_id>/artifacts/<artifact_id>` | Outputs are artifacts |
| `runs/<run_id>/trace/packets.jsonl` | `runs/<run_id>/events.jsonl` | Unified event log |
| `runs/<run_id>/manifest.json` | `runs/<run_id>/manifest.json` | Same location, expanded schema |
| (new) | `runs/<run_id>/shapes/` | Shape storage |
| (new) | `runs/<run_id>/checkpoints/` | Resumption snapshots |
| (new) | `runs/<run_id>/final/` | Completed packet outputs |

---

## Appendix B: Example Usage

### B.1 Create a Goal

```bash
packet-cli goal "Build a user authentication system for service X"
# Returns: packet_id = "abc-123"
```

### B.2 Claim and Work

```bash
# Agent claims the packet
packet-cli claim abc-123
# Logs: CLAIMED event

# Agent does work, checkpoints periodically
packet-cli progress abc-123 --state '{"completed": ["schema design", "db setup"], "current": "implementing login"}'

# Agent decides to delegate auth-flow to a child
packet-cli delegate abc-123 "auth-flow" --objective "Implement OAuth2 flow for login"
# Returns: child_packet_id = "def-456"

# Agent continues working on its seam while child runs in parallel
packet-cli progress abc-123 --state '{"current": "building user model"}'

# Agent approves child's delegation (parent has APPROVE permission)
packet-cli approve def-456

# Later, child completes
packet-cli complete def-456 --output '{"auth_type": "oauth2", "provider": "google"}'

# Parent finishes
packet-cli complete abc-123 --output '{"auth": "complete", "user_model": "built"}'
```

### B.3 Discover and Save a Shape

```bash
# During execution, agent discovers a reusable OAuth2 pattern
packet-cli save-shape def-456 "skill_definition" '{
  "name": "Google OAuth2 Flow",
  "tags": ["auth", "oauth2", "google"],
  "tools": ["http_request", "json_parse", "token_storage"],
  "prompt": "Implement Google OAuth2 with: 1) redirect, 2) token exchange, 3) refresh"
}'
# Returns: shape_id = "shape-789"

# Later, a different agent searches for it
packet-cli shapes --tags "auth,oauth2"
# Returns: [{"shape_id": "shape-789", "name": "Google OAuth2 Flow", ...}]

# And applies it to a new goal
packet-cli use-shape shape-789 "Add OAuth2 to service Y"
# Creates new packet with the shape's constraints and prompt template
```

---

*This document describes the target architecture for the recursive work-packet runtime system. Implementation proceeds iteratively, with each component independently deployable.*
