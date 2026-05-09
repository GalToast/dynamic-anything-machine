# Design Rationale

Why the work-packet runtime is designed the way it is.

---

## Why Work Packets Instead of Fixed Agents?

A traditional agent system assigns a permanent role to an agent (e.g., "code reviewer", "researcher"). The agent owns that role indefinitely and handles everything in its domain.

Work packets take the opposite approach: **seams over departments**. A seam is a temporary claim on a unit of work — not a permanent role. When an agent claims a packet, it temporarily owns that work. When the packet is done (completed, failed, or paused), the agent moves on and another agent can pick up the next piece.

This has several advantages:

- **Resumability across harnesses.** A packet can be claimed by a Claude Code agent, interrupted, and resumed by a human operator or a different agent runtime. The packet — not the agent — is the unit of continuity.
- **Harness-impartial foundation.** The runtime does not assume any particular harness. An agent on Codex and a human operator and a test harness all interact with the same packet format. The runtime is a shared coordination layer.
- **Explicit constraint inheritance.** When one packet delegates to another, constraints are explicitly narrowed. A parent with `allowed_tools=["read_file", "grep"]` cannot spawn a child that requests `write_file`. This makes the delegation tree auditable and policy-enforceable.
- **No agent identity as the primary key.** Traditional systems track "what is agent X doing right now?" The work-packet system tracks "what is the state of packet Y?" Agents are secondary — they are workers, not owners.

---

## The Seam Concept

A seam is a named unit of work ownership. The seam field on a packet (`seam: str`) identifies what kind of work this packet is doing. Examples:

- `"auth-flow"` — Authentication and authorization logic
- `"file-parser"` — Parsing a specific file format
- `"report-generation"` — Building a report from structured data
- `"database-migration"` — Schema migration tasks
- `"api-integration"` — Third-party API integration

A seam is **not** a role. Roles are assigned; seams are claimed. When a packet is emitted, it declares its seam. Any agent that can do that kind of work can claim it.

### What Makes Something a Seam?

A seam describes a coherent unit of work that:

1. Has a clear boundary — it has a defined input and a defined output
2. Can be executed independently — it does not require tight coupling to the internals of other seams
3. Can be paused and resumed — its progress can be captured in `current_state` and restored
4. Is worth naming — if you find yourself saying "the file parser part" repeatedly, that's probably a seam

### Seams in the Delegation Tree

```
Root Packet (seam="root")
  ├── Child (seam="auth-flow")
  │     ├── Grandchild (seam="session-manager")
  │     └── Grandchild (seam="token-validator")
  └── Child (seam="data-pipeline")
        ├── Grandchild (seam="file-reader")
        ├── Grandchild (seam="transformer")
        └── Grandchild (seam="writer")
```

Each seam can be worked on by a different agent. The parent packet coordinates by delegating to children and waiting for their results.

---

## Delegation with Constraint Inheritance

When a packet delegates to a child, the child does not get a blank slate. The parent's constraints are inherited and can only be narrowed.

### Inheritance Rules

| Constraint | Inheritance Rule |
|------------|-----------------|
| `allowed_tools` | Child gets a subset of parent's allowed tools |
| `forbidden_tools` | Child inherits parent's forbidden tools plus any new ones |
| `owned_paths` | Child gets a subset of parent's owned paths |
| `forbidden_paths` | Child inherits parent's forbidden paths |
| `timeout_seconds` | Child gets at most parent's timeout |
| `budget` | Child gets at most parent's budget |
| `max_child_depth` | Child gets `max(0, parent.max_child_depth - 1)` |

This means a chain of delegations monotonically tightens constraints. A root packet with broad permissions spawns children with narrower permissions, who spawn grandchildren with even narrower permissions.

### `delegation_permission`

Controls whether a packet can delegate at all:

| Value | Meaning |
|-------|---------|
| `NONE` | This packet cannot create children |
| `REQUEST` | Can request delegation, but child needs approval before being emitted |
| `APPROVE` | Can delegate freely, children are immediately emitted |

A root goal typically has `APPROVE` so it can spin up child packets without asking. An agent that wants to delegate to a human for a sensitive operation would use `REQUEST`, causing the child to be held pending approval.

### Example

```
Parent (allowed_tools=["read_file", "grep", "write_file"], max_child_depth=2)
  └── Child via delegate() (inherits all parent constraints)
        └── Grandchild via delegate() (max_child_depth decremented to 1)
              └── Great-grandchild via delegate() → ValueError: max_child_depth=0
```

The constraint system prevents runaway delegation trees and ensures every child has a bounded scope.

---

## Shape Lifecycle

Shapes are reusable artifacts discovered during packet execution. They are not planned in advance — they emerge from actual work.

### Lifecycle

1. **Discovery** — During packet execution, an agent discovers a pattern worth saving (e.g., a prompt that works well for a certain task, a way of structuring a delegation, a skill definition).
2. **Registration** — The agent calls `packet.discover_shape(ShapeRef(...))` to record the shape ID on the packet.
3. **Save** — On `complete_packet()`, any shapes discovered during execution are saved to the `ShapeRegistry` (via `registry.save_shape()`).
4. **Reuse** — Future packets can search the registry with `find_shapes()` or `find_by_tag()`, or a human can use `use-shape <shape_id>` to create a new goal from a shape template.

### Shape Types

| Type | Description |
|------|-------------|
| `PROMPT_TEMPLATE` | A structured prompt with slot placeholders |
| `SKILL_DEFINITION` | A defined skill with tools, constraints, and prompt |
| `PACKET_TEMPLATE` | A templated packet objective with slots |
| `ORCHESTRATION_SHAPE` | A multi-packet pattern (e.g., planner-worker-critic) |
| `EVALUATION_CHECK` | A verification or checklist pattern |
| `DELEGATION_PATTERN` | A way of structuring parent-child delegation |

### Shapes are Ephemeral but Durable

Shapes are not required for packet execution — a packet can run to completion without discovering any shapes. But when a shape is discovered, it is immediately saved to disk and available for the next packet. This means the system gets better over time without requiring explicit training.

---

## Human Gates

Human gates pause a packet for explicit approval before proceeding. They are used when a packet wants to perform a side effect that requires human judgment (e.g., deploying to production, sending a message, deleting a file).

### Flow

1. Agent calls `runtime.human_gate(packet_id, name="deploy-production")`
2. Packet's `open_questions` gains `"human_gate:deploy-production"`
3. A `HUMAN_GATE_ENTERED` event is logged
4. The packet is now PAUSED — no further work happens on it until resolved
5. A human reviews the pending action
6. Human calls `runtime.resolve_human_gate(packet_id, name="deploy-production", approved=True)`
7. `HUMAN_GATE_APPROVED` (or `HUMAN_GATE_DENIED`) event is logged
8. If approved, the gate is removed from `open_questions` and execution continues
9. If denied, `current_state["gate_denied"]` is set and the agent can handle the rejection

### PolicyGate Integration

The `PolicyGate` class (defined in `tests/test_packet_runtime.py`) defines which side effects require approval in each profile. The default profile requires approval for:

- `file_write`
- `filesystem_modify`
- `shell_exec`
- `network_call`
- `secret_access`

Aliases map common names to these: `shell_command` maps to `shell_exec`, `deploy` maps to `file_write`.

---

## Resumption Contract

When a packet is paused (at a gate or checkpoint), a resuming agent needs to know how to pick up where the previous agent left off. The `resumption_contract` field on `WorkPacket` holds this information as a free-form string.

A good resumption contract describes:

1. **What was the last completed step?**
2. **What is the next step?**
3. **What state has been accumulated?** (pointing to `current_state`)
4. **What artifacts exist so far?** (pointing to `artifacts_produced`)

The `resumption_contract` is intentionally not structured — different seams will have different resumption needs. A file-parsing seam might need to know which lines have been processed. An auth-flow seam might need to know which tokens are valid.

The `checkpoint_hint` field provides a dot-notation path to a field within `current_state` that is the primary checkpointable unit (e.g., `"progress.line_count"`).

---

## Determinism

A packet with the same `packet_id` and the same checkpoint state will behave identically regardless of which harness resumes it. This is because:

1. **The packet file is the source of truth.** All constraints, state, and context are stored in `runs/<run_id>/packets/<packet_id>.json`.
2. **The append-only ledger is the audit log.** The sequence of events that led to the current state is recorded in `events.jsonl`. A replay always produces the same state.
3. **No in-memory state is trusted.** The runtime never trusts an in-memory representation over what is written to disk. The ledger's `get_timeline()` always reflects the true event order.
4. **Checkpoint + packet_id = deterministic replay.** Given `checkpoint_id` + `packet_id`, a replay engine can reconstruct the exact state at that checkpoint.

This means that if a long-running packet is interrupted, the resuming agent does not need to trust the previous agent's memory — it reads the packet file, reads the ledger, and knows exactly where it stands.

---

## No Semantic Retrieval as Core Dependency

The runtime does not depend on semantic retrieval (vector embeddings, similarity search, RAG) for its core functionality. Memory and retrieval are intentionally a later layer.

The foundation is **durable packet state**: all the information an agent needs to resume work is explicitly stored in the packet and ledger. This is:

- **Reliable** — No retrieval model can hallucinate or miss a relevant document. The state is the state.
- **Audit-friendly** — The ledger records exactly what happened and when. There is no "the agent might have retrieved X."
- **Portable** — Works across any harness, including human operators who cannot use a retrieval system.

Semantic retrieval (searching past packets, finding similar shapes, memory augmentation) can be built on top of this foundation as a separate layer. But the runtime itself is retrieval-agnostic.

---

## Two Systems Sharing `runs/`

The graph compiler (Milestone 1) and the work-packet runtime (Milestone 2+) both write to the same `runs/` directory structure. They use different packet formats but share:

- **`runs/<run_id>/events.jsonl`** — The shared append-only event log. Both systems write events to the same file format (`AuditEvent`). Event types are prefixed to distinguish the system (e.g., `PACKET_EMITTED` for work packets, different event types for graph compiler).
- **`runs/<run_id>/packets/`** — Packet storage. Graph compiler nodes use `WorkerPacket` (from `dynamic_schema.py`), work packets use `WorkPacket` (from `work_packet.py`). Different schemas, same directory.
- **`runs/<run_id>/shapes/`** — Shape registry. Work-packet shapes stored as JSON files. Graph compiler may store its own artifacts separately.
- **`runs/<run_id>/checkpoints/`**, **`runs/<run_id>/artifacts/`**, **`runs/<run_id>/final/`** — Shared directories for checkpoint snapshots, artifact files, and final outputs.

This shared storage design means a run can include both graph-compiled nodes and work-packet agents in the same event log. An auditor can read `events.jsonl` and see the complete history regardless of which system generated each event.

---

## PolicyGate and Approval Flow

`PolicyGate` is the runtime's policy enforcement layer for side effects. It is a standalone class (defined in `tests/test_packet_runtime.py` for Milestone 1 isolation) that answers a single question: *does this side effect require approval?*

### Default Approval Requirements

Under the `default` profile, the following require explicit approval before execution:

| Side Effect | Aliases |
|------------|---------|
| `file_write` | `deploy` |
| `filesystem_modify` | — |
| `shell_exec` | `shell_command` |
| `network_call` | — |
| `secret_access` | — |

The `side_effects_requiring_approval` field on `WorkPacket` is a list of side effect names. When an agent wants to perform one of these, it calls `runtime.human_gate()` and waits for resolution.

### Approval Flow in the Runtime

```
Packet execution begins
  ↓
Agent wants to perform side_effect: "deploy"
  ↓
PolicyGate.requires_approval("deploy") → True
  ↓
runtime.human_gate(packet_id, "deploy")
  ↓
[HUMAN REVIEWS]
  ↓
runtime.resolve_human_gate(packet_id, "deploy", approved=True)
  ↓
Execution continues
```

The approval decision is recorded as a `HUMAN_GATE_APPROVED` or `HUMAN_GATE_DENIED` event in the ledger, creating a complete audit trail of what was approved and by whom.

### PolicyGate vs ProfilePolicy

`PolicyGate` is a lightweight runtime check for whether a side effect needs approval. `ProfilePolicy` (in `policy.py`) is a more comprehensive policy validator for graph specs and worker requests, including tool allowlists, node type restrictions, depth limits, and structural requirements. They serve different purposes:

- `PolicyGate` — Runtime approval gate for work-packet side effects
- `ProfilePolicy` — Static validation for graph compilation and profile enforcement