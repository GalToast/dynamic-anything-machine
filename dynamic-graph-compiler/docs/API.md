# API Reference

Complete reference for all new work-packet runtime modules.

---

## work_packet.py

Core data models for the recursive work-packet runtime.

### Enums

#### PacketStatus

Lifecycle status of a work packet.

| Value | Description |
|-------|-------------|
| `CREATED` | Initialized, not yet emitted |
| `EMITTED` | Written to disk, available for claiming |
| `CLAIMED` | An agent has picked it up |
| `IN_PROGRESS` | Actively working |
| `PAUSED` | Waiting at a gate or checkpoint |
| `COMPLETED` | Terminal: success |
| `FAILED` | Terminal: failure |
| `RESUMED` | Picked up by a different agent |

#### DelegationPermission

Controls whether and how a packet may delegate work to child packets.

| Value | Description |
|-------|-------------|
| `NONE` | Cannot delegate |
| `REQUEST` | Can request delegation, needs approval |
| `APPROVE` | Can delegate without approval |

#### HarnessType

Identifies the execution harness a packet is associated with.

| Value | Description |
|-------|-------------|
| `UNKNOWN` | Unidentified harness |
| `CLAUDE_CODE` | Claude Code |
| `CODEX` | Codex |
| `GEMINI_CLI` | Gemini CLI |
| `QWEN_CLI` | Qwen CLI |
| `OPENCODE` | Opencode |
| `OPENCLAW` | Openclaw |
| `MINIMAX` | MiniMax |
| `HUMAN` | Human operator |
| `TEST` | Test harness |

#### ShapeType

Classification of reusable shape artifacts produced during execution.

| Value | Description |
|-------|-------------|
| `PROMPT_TEMPLATE` | Reusable prompt structure |
| `SKILL_DEFINITION` | Defined skill with constraints |
| `PACKET_TEMPLATE` | Template for new packets |
| `ORCHESTRATION_SHAPE` | Multi-packet orchestration pattern |
| `EVALUATION_CHECK` | Verification/checklist shape |
| `DELEGATION_PATTERN` | Delegation structure shape |

---

### ShapeRef

Reference to a shape artifact saved to durable storage.

```python
class ShapeRef(BaseModel):
    shape_id: str              # Unique identifier
    shape_type: ShapeType      # Classification from ShapeType enum
    name: str                  # Human-readable name
    tags: list[str]            # Searchable tags (default: empty list)
    created_from_packet_id: str # Packet that created this shape
    created_at: str            # ISO 8601 UTC timestamp
```

---

### ResumptionSnapshot

Frozen checkpoint of a packet's execution state used to resume interrupted work.

```python
class ResumptionSnapshot(BaseModel):
    packet_id: str
    run_id: str
    snapshot_id: str           # Auto-generated UUID (default: uuid.uuid4())
    label: str                 # Human-readable checkpoint name
    state: dict[str, Any]      # Frozen work-in-progress
    artifacts: list[str]       # Artifact paths at checkpoint time
    shapes: list[ShapeRef]     # Shapes discovered up to checkpoint
    event_id: str              # Ledger event ID for this snapshot
    created_at: str            # ISO 8601 UTC timestamp
```

---

### WorkResult

Output produced when a work packet reaches a terminal state.

```python
class WorkResult(BaseModel):
    packet_id: str
    run_id: str
    status: PacketStatus
    output: Any = None                     # Execution output
    error: str | None = None               # Error message if failed
    shapes_discovered: list[ShapeRef]      # Shapes found during execution
    artifacts_produced: list[str]          # Output artifact paths
    duration_seconds: float | None = None  # Execution time
    cost_estimate: float | None = None     # Computed cost
    children: list[str]                    # Child packet IDs
    critic_findings: str | None = None      # Critic observations
    completed_at: str                      # ISO 8601 UTC timestamp
```

---

### WorkPacket

Self-contained unit of delegatable work. The central data structure of the runtime.

```python
class WorkPacket(BaseModel):
    # ── Identity ─────────────────────────────────────────────────────
    packet_id: str                           # UUID (auto-generated)
    run_id: str                              # Parent run this packet belongs to
    parent_packet_id: str | None             # Delegating packet, if any
    depth: int                               # Delegation depth (0 = root goal)

    # ── Seam — what this packet owns ─────────────────────────────────
    seam: str                                # Work seam identifier (e.g. "auth-flow")

    # ── Objective ─────────────────────────────────────────────────────
    objective: str                           # What needs to be accomplished
    success_criteria: str                    # How we know this is done

    # ── State ────────────────────────────────────────────────────────
    current_state: dict[str, Any]            # Work-in-progress snapshot
    known_context: dict[str, Any]            # What prior packets established
    open_questions: list[str]                # Things needed but not yet known

    # ── Constraints ─────────────────────────────────────────────────
    allowed_tools: list[str]                 # Permitted tool names
    forbidden_tools: list[str]               # Prohibited tool names
    owned_paths: list[str]                   # Accessible file paths
    forbidden_paths: list[str]              # Inaccessible paths
    timeout_seconds: int | None              # Max execution time (seconds)
    budget: float | None                     # Cost ceiling (ge: 0)

    # ── Permissions ────────────────────────────────────────────────
    delegation_permission: DelegationPermission  # Whether delegation is allowed
    max_child_depth: int                    # Max delegation depth for children
    side_effects_requiring_approval: list[str]   # Side effects needing explicit approval

    # ── Artifacts ──────────────────────────────────────────────────
    available_artifacts: list[str]           # Paths to reusable resources
    created_shapes: list[str]                # Shape IDs created during execution
    artifacts_produced: list[str]            # Output artifact paths produced

    # ── Resumption ──────────────────────────────────────────────────
    resumption_contract: str | None          # What a resuming agent needs to know
    checkpoint_hint: str | None               # Dot-notation path to checkpointable field

    # ── Audit ───────────────────────────────────────────────────────
    audit_trail: list[str]                    # Event IDs recorded in the ledger
    secrets_rule: str | None                 # Policy for secrets handling
    no_revert_rule: bool                     # When True, actions must not be undoable

    # ── Timestamps (ISO 8601 UTC) ────────────────────────────────────
    created_at: str
    updated_at: str
    claimed_at: str | None
    completed_at: str | None

    # ── Ownership ───────────────────────────────────────────────────
    claimed_by: str | None                   # Agent instance that claimed this packet
    claimed_by_harness: HarnessType         # Harness type (default: UNKNOWN)
```

---

### WorkPacket Methods

#### `claim(agent_id: str, harness: HarnessType) -> None`

Mark this packet as claimed by the given agent. Sets `claimed_by`, `claimed_by_harness`, `claimed_at`, advances status to `CLAIMED`, and updates `updated_at`.

#### `checkpoint(state: dict[str, Any], label: str) -> ResumptionSnapshot`

Freeze the current execution state into a named resumption snapshot. Updates `current_state` and `updated_at`, returns a `ResumptionSnapshot` (caller populates `event_id` after logging).

#### `progress(state_update: dict[str, Any]) -> None`

Merge `state_update` into `current_state`. Existing keys are overwritten; new keys are added. Updates `updated_at`.

#### `add_audit_event(event_id: str) -> None`

Append an event ID to the packet's `audit_trail`. Updates `updated_at`.

#### `discover_shape(shape_ref: ShapeRef) -> None`

Record a shape discovered during execution. Deduplicates by `shape_id`. Updates `updated_at`.

#### `to_markdown() -> str`

Render a human-readable summary as a markdown string. Includes identity, objective, state, constraints, permissions, artifacts, resumption info, audit, timestamps, and ownership.

#### `to_dict() -> dict[str, Any]`

Serialize to a plain dictionary using `model_dump(mode="json")`.

#### `from_dict(data: dict[str, Any]) -> WorkPacket`

Class method. Deserialize from a dictionary previously produced by `to_dict()`.

---

## work_ledger.py

Append-only event ledger for packet lifecycle tracking. Single source of truth; no in-memory state trusted over the ledger. All timestamps are UTC.

### Event Type Constants (23 total)

| Constant | Description |
|----------|-------------|
| `PACKET_CREATED` | Root packet created for a run |
| `PACKET_EMITTED` | Packet written to disk, available for claiming |
| `PACKET_CLAIMED` | Agent has taken ownership |
| `PACKET_IN_PROGRESS` | Agent started active work |
| `PACKET_PROGRESS` | State checkpoint/update logged |
| `PACKET_PAUSED` | Packet paused at a gate or checkpoint |
| `PACKET_COMPLETED` | Terminal: success |
| `PACKET_FAILED` | Terminal: failure |
| `PACKET_RESUMED` | Different agent picked up the packet |
| `CHILD_REQUESTED` | Parent requested delegation to child |
| `CHILD_APPROVED` | Child packet approved and emitted |
| `CHILD_DENIED` | Child packet denied |
| `HUMAN_GATE_ENTERED` | Packet paused for human approval |
| `HUMAN_GATE_APPROVED` | Human approved the gate |
| `HUMAN_GATE_DENIED` | Human denied the gate |
| `SHAPE_CREATED` | Shape artifact saved to registry |
| `ARTIFACT_WRITTEN` | Artifact file written to disk |
| `CHECKPOINT_CREATED` | Checkpoint snapshot created |
| `CHECKPOINT_RESTORED` | Checkpoint restored during resumption |
| `SECRET_DETECTED` | Secret detected and masked in event data |
| `RUN_COMPLETED` | Run finished successfully |
| `RUN_FAILED` | Run failed |
| `ERROR` | Error event logged |

---

### AuditEvent

```python
@dataclass
class AuditEvent:
    event_id: str
    run_id: str
    packet_id: str | None
    timestamp: str              # ISO 8601 UTC
    event_type: str
    agent_id: str | None
    harness: str | None
    depth: int | None
    data: dict[str, Any]        # Event-specific payload (secrets masked)
    masked_fields: list[str]   # Which fields had secrets redacted
```

**Methods:**
- `to_dict() -> dict[str, Any]` — Serialize to dictionary
- `from_dict(d: dict[str, Any]) -> AuditEvent` — Deserialize (classmethod)

---

### SecretMasker

Detects and redacts API keys, tokens, passwords, bearer tokens, AWS keys, and other secrets from event data before logging.

#### `mask_secrets(text: str) -> tuple[str, list[str]]`

Scans `text` for secret patterns and replaces them with `[REDACTED:TYPE]`. Returns `(masked_text, list_of_detected_types)`.

Supported patterns include: AWS keys, GitHub tokens, bearer tokens, generic API keys, OpenAI keys, Anthropic keys, Stripe keys, JWT tokens, Basic Auth, passwords in URLs, private keys, and GCP service account files.

#### `check_dict(data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]`

Recursively scans a dict for secrets, returning `(masked_copy, detected_types)`. String values are masked, nested dicts and lists are traversed.

---

### WorkLedger

```python
class WorkLedger:
    def __init__(self, runs_dir: Path | str | None = None) -> None:
        # Base directory for all run data (default: "runs")
```

#### Run Management

##### `create_run(run_id: str, objective: str, parent_packet_id: str | None = None) -> str`

Creates the run directory structure (`events.jsonl`, `packets/`, `shapes/`, `artifacts/`, `checkpoints/`, `final/`), logs a `PACKET_CREATED` event, and returns `run_id`.

##### `list_runs() -> list[dict[str, Any]]`

Returns all runs with `{run_id, created_at, event_count}` sorted by creation time (most recent first).

---

#### Event Logging (Sync)

All return `event_id` (UUID string).

##### `log_event(run_id, event_type, packet_id, agent_id, harness, depth, data, masked_fields=[]) -> str`

Core method. Appends a JSON line to `runs/<run_id>/events.jsonl`. Automatically detects and masks secrets in `data` using `SecretMasker.check_dict()`.

##### `log_packet_emitted(run_id, packet_id, depth, data) -> str`
##### `log_packet_claimed(run_id, packet_id, agent_id, harness) -> str`
##### `log_progress(run_id, packet_id, state_delta) -> str`
##### `log_child_requested(run_id, parent_packet_id, child_packet_id, seam) -> str`
##### `log_child_approved(run_id, child_packet_id) -> str`
##### `log_child_denied(run_id, child_packet_id, reason) -> str`
##### `log_human_gate(run_id, packet_id, gate) -> str`
##### `log_human_decision(run_id, packet_id, gate, approved, approver) -> str` — Logs `HUMAN_GATE_APPROVED` if `approved=True`, `HUMAN_GATE_DENIED` if `approved=False`
##### `log_shape_created(run_id, packet_id, shape_id, shape_type, data) -> str`
##### `log_artifact_written(run_id, packet_id, artifact_path) -> str`
##### `log_checkpoint(run_id, packet_id, checkpoint_id, label) -> str`
##### `log_error(run_id, packet_id, error) -> str` — auto-masks secrets
##### `log_run_completed(run_id, summary) -> str`
##### `log_run_failed(run_id, reason) -> str`

---

#### Event Logging (Async)

Each async method mirrors its sync counterpart with `*_async` suffix. All return `str` (event_id). Uses `aiofiles` for non-blocking I/O.

```python
async def log_event_async(run_id, event_type, packet_id, agent_id, harness, depth, data, masked_fields=[]) -> str
async def log_packet_emitted_async(run_id, packet_id, depth, data) -> str
async def log_packet_claimed_async(run_id, packet_id, agent_id, harness) -> str
async def log_progress_async(run_id, packet_id, state_delta) -> str
async def log_child_requested_async(run_id, parent_packet_id, child_packet_id, seam) -> str
async def log_child_approved_async(run_id, child_packet_id) -> str
async def log_child_denied_async(run_id, child_packet_id, reason) -> str
async def log_human_gate_async(run_id, packet_id, gate) -> str
async def log_human_decision_async(run_id, packet_id, gate, approved, approver) -> str
async def log_shape_created_async(run_id, packet_id, shape_id, shape_type, data) -> str
async def log_artifact_written_async(run_id, packet_id, artifact_path) -> str
async def log_checkpoint_async(run_id, packet_id, checkpoint_id, label) -> str
async def log_error_async(run_id, packet_id, error) -> str
async def log_run_completed_async(run_id, summary) -> str
async def log_run_failed_async(run_id, reason) -> str
```

---

#### Query Methods

##### `get_events(run_id, event_type=None, packet_id=None) -> list[AuditEvent]`

Reads `events.jsonl`, returns filtered list. Both filters are optional and additive.

##### `get_timeline(run_id) -> list[AuditEvent]`

All events chronologically (already in order from append-only log).

##### `get_packet_history(run_id, packet_id) -> list[AuditEvent]`
##### `get_packet_events(run_id, packet_id) -> list[AuditEvent]` (alias)

All events for a specific packet.

##### `get_delegation_tree(run_id) -> dict[str, Any]`

Builds parent->child packet relationships. Returns:
```python
{
    "roots": list[str],           # Root packet IDs
    "packets": {
        packet_id: {
            "packet_id": str,
            "parent_id": str | None,
            "children": list[str],
            "depth": int | None,
            "events": list[str],   # event_ids
        }
    }
}
```

##### `list_seams(run_id: str | None = None) -> list[dict[str, Any]]`

Lists all EMITTED (unclaimed) packets. If `run_id` is provided, scope to that run. Otherwise search across all runs. Returns `[{run_id, packet_id, seam, objective, depth, created_at}]`.

##### `verify_integrity(run_id) -> tuple[bool, list[str]]`

Checks `events.jsonl` is valid JSONL. Returns `(is_valid, issues_list)`. Issues include missing required fields, JSON decode errors, and invalid AuditEvent parsing.

---

### ReplayEngine

Reconstructs run state from ledger events.

```python
class ReplayEngine:
    def __init__(self, ledger: WorkLedger) -> None:
        self.ledger = ledger
```

#### `replay(run_id) -> dict[str, Any]`

Reconstructs complete run state from ledger events. Returns:
```python
{
    "run_id": str,
    "packets_created": {packet_id: {packet_id, created_at, depth, data}},
    "packets_claimed": {packet_id: {packet_id, claimed_at, agent_id, harness}},
    "state_checkpoints": {packet_id: [{checkpoint_id, label, created_at}]},
    "shapes_created": {packet_id: [{shape_id, shape_type, created_at}]},
    "delegation_tree": {parent_id: [child_id, ...]},
    "errors": [{packet_id, error, timestamp}],
    "run_summary": dict,
    "total_events": int,
}
```

#### `replay_to_text(run_id) -> str`

Human-readable replay report with sections for packets, claimed packets, delegation tree, shapes, errors, and run summary.

#### `get_state_at_event(run_id, event_id) -> dict[str, Any]`

Reconstructs state up to and including the event with given `event_id`. Returns `{"error": "Event {event_id} not found"}` if not found.

#### `get_packet_events(run_id, packet_id) -> list[AuditEvent]`

Returns all events for a specific packet in chronological order.

---

## shape_registry.py

File-based registry for reusable shapes. Shapes are stored as individual JSON files under `shapes/` directory.

### Shape

```python
@dataclass
class Shape:
    shape_id: str
    shape_type: ShapeType
    name: str
    description: str | None
    tags: list[str]
    seam: str | None
    payload: dict[str, Any]
    usage_count: int = 0
    created_at: str                    # ISO 8601 UTC (default: now)
    created_from_packet_id: str | None
    created_by_agent: str | None
    last_used_at: str | None
    metadata: dict[str, Any]
```

#### `to_dict() -> dict[str, Any]` / `from_dict(data) -> Shape`

Serialization pair. `shape_type` is serialized as its value string.

---

### ShapeRegistry

```python
class ShapeRegistry:
    def __init__(self, shapes_dir: Path | str | None = None, ledger: WorkLedger | None = None) -> None:
        # shapes_dir defaults to "shapes"; ledger is optional for audit integration
```

#### CRUD

##### `save_shape(shape: Shape) -> str`

Saves shape to `shapes_dir/<shape_id>.json`. Auto-generates `shape_id` if missing. Returns the `shape_id`.

##### `get_shape(shape_id: str) -> Shape | None`

Reads shape from disk. Returns `None` if not found or JSON decode fails.

##### `delete_shape(shape_id: str) -> bool`

Deletes `shapes_dir/<shape_id>.json`. Returns `True` if deleted, `False` if not found.

##### `update_usage(shape_id: str) -> None`

Increments `usage_count` and sets `last_used_at` to now. No-op if shape not found.

All CRUD operations have async counterparts: `save_shape_async`, `get_shape_async`, `delete_shape_async`, `update_usage_async`.

#### Search

##### `find_shapes(tags: list[str] | None = None, seam: str | None = None, shape_type: ShapeType | None = None, query: str | None = None, limit: int = 10) -> list[Shape]`

Multi-criteria search. All filters are optional and additive. `tags` requires all specified tags to match. `query` searches `name` and `description` case-insensitively. Results sorted by `usage_count` descending.

##### `find_by_tag(tag: str) -> list[Shape]`
##### `find_by_seam(seam: str) -> list[Shape]`

Convenience wrappers around `find_shapes`.

##### `most_used(limit: int = 10) -> list[Shape]`

Returns most-used shapes by `usage_count` descending.

##### `recent(limit: int = 10) -> list[Shape]`

Returns most recently created shapes by `created_at` descending.

#### List / Export

##### `list_shapes() -> list[dict[str, Any]]`

Summary list with `{shape_id, name, shape_type, tags, usage_count, created_at}` for each shape.

##### `export_shape(shape_id: str) -> dict[str, Any]`

Full shape dict for sharing. Raises `ValueError` if not found.

##### `import_shape(data: dict[str, Any]) -> str`

Creates a new shape with a fresh UUID (even if imported data had one). Returns the new `shape_id`.

##### `count() -> int`

Total number of shapes in the registry.

---

### ShapeBuilder

Factory for creating shapes from packet execution data.

```python
class ShapeBuilder:
    @staticmethod
    def from_packet_execution(packet_id, run_id, seam, tools_used, prompt, result, ledger=None) -> Shape
```

Creates a `SKILL_DEFINITION` shape from packet execution data. Extracts constraints from `result` dict if available.

```python
    @staticmethod
    def prompt_template_from_text(name, description, text, slots) -> Shape
```

Creates a `PROMPT_TEMPLATE` shape. Extracts `{slot}` placeholders from `text` automatically.

```python
    @staticmethod
    def packet_template_from_seam(seam, packet) -> Shape
```

Creates a `PACKET_TEMPLATE` shape from a completed packet. Extracts `{slot}` placeholders from `objective`.

---

### `get_example_shapes() -> list[Shape]`

Returns 5 starter shapes for testing:

1. **Read-Research-Summarize** (orchestration_shape) — three-phase research pattern
2. **Security Code Reviewer** (skill_definition) — automated security code review
3. **Parallel Seam Splitter** (delegation_pattern) — splits seam into parallel sub-seams
4. **File Exists and Validates** (evaluation_check) — verifies file was written correctly
5. **Entity Extraction Template** (prompt_template) — extracts structured entities from text

---

## packet_runtime.py

Core orchestration API for the recursive work-packet runtime.

### PacketRuntime

```python
class PacketRuntime:
    def __init__(self, runs_dir: Path | str | None = None) -> None:
        # Initializes WorkLedger (runs_dir/runs) and ShapeRegistry (runs_dir/shapes)
```

**Attributes:**
- `runs_dir: Path` — Base directory for all run data
- `ledger: WorkLedger` — Event logging instance
- `registry: ShapeRegistry` — Shape storage instance

#### Goal Creation

##### `create_goal(objective: str, success_criteria: str = "completed", seam: str = "root") -> tuple[str, str]`

Creates a new run and emits the root goal packet. Returns `(run_id, packet_id)`. The root packet shares the `run_id` as its `packet_id`.

Creates run directory structure, initializes the `WorkPacket` with depth=0, writes it to `runs/<run_id>/packets/<packet_id>.json`, and logs a `PACKET_EMITTED` event.

#### Packet Lifecycle

##### `claim_packet(packet_id, run_id, agent_id, harness) -> str`

Claims ownership of a packet. Verifies status is `EMITTED` or `RESUMED`. Sets `claimed_by`, `claimed_by_harness`, `claimed_at`, updates the packet file, and logs `PACKET_CLAIMED`. Returns `event_id`.

Raises `FileNotFoundError` if packet not found, `ValueError` if not in claimable state.

##### `progress_packet(packet_id, run_id, state_update, artifacts=None) -> str`

Merges `state_update` into the packet's `current_state`, optionally logs artifact paths, writes the packet file, and logs a `PACKET_PROGRESS` event. Returns `event_id`.

##### `checkpoint_packet(packet_id, run_id, label) -> str`

Creates a named `ResumptionSnapshot` from the packet's current state. Saves snapshot to `runs/<run_id>/checkpoints/<checkpoint_id>.json`. Logs `CHECKPOINT_CREATED` event. Returns `checkpoint_id`.

##### `complete_packet(packet_id, run_id, output, shapes=None, critic=None) -> str`

Marks packet as completed. Saves shapes to registry, writes output to `runs/<run_id>/final/<packet_id>.md`, logs `PACKET_COMPLETED`. If this is the root packet, checks for run completion. Returns `event_id`.

##### `fail_packet(packet_id, run_id, error) -> str`

Marks packet as failed. Updates `current_state["error"]`, logs `ERROR` and `PACKET_FAILED`. If this is the root packet, logs `RUN_FAILED`. Returns `event_id`.

##### `human_gate(packet_id, run_id, name) -> str`

Pauses a packet at a human approval gate. Appends `human_gate:<name>` to `open_questions`. Logs `HUMAN_GATE_ENTERED`. Returns `event_id`.

##### `resolve_human_gate(run_id, packet_id, name, approved, approver="human") -> str`

Resolves a human gate. If denied, sets `current_state["gate_denied"]`. Removes gate from `open_questions`. Logs `HUMAN_GATE_APPROVED` or `HUMAN_GATE_DENIED`. Returns `event_id`.

#### Delegation

##### `delegate_packet(packet_id, run_id, seam, objective=None, constraints=None) -> tuple[str, bool]`

Requests delegation to create a child packet. Returns `(child_packet_id, is_pending)` where `is_pending` indicates whether approval is required before the child is emitted.

Child inherits and narrows parent constraints: `allowed_tools`, `forbidden_tools`, `owned_paths`, `forbidden_paths`, `timeout_seconds`, `budget`, `max_child_depth` (decremented by 1, min 0).

Child's `delegation_permission` becomes `REQUEST` if parent is `REQUEST`, otherwise `APPROVE`.

Raises `ValueError` if parent has `delegation_permission=NONE` or `max_child_depth=0`.

##### `approve_child(run_id, child_packet_id, approver="human") -> str`

Approves a pending child packet. Updates child's packet file and logs `PACKET_EMITTED` (for visibility) + `CHILD_APPROVED`. Returns `event_id`.

##### `deny_child(run_id, child_packet_id, reason) -> str`

Denies a pending child packet. Sets `current_state["denial_reason"]`, logs `CHILD_DENIED`. Returns `event_id`.

#### Query

##### `list_seams(run_id: str | None = None) -> list[dict[str, Any]]`

Returns open (EMITTED, unclaimed) packets with `{run_id, packet_id, seam, depth, created_at}`. Delegates to `WorkLedger.list_seams()`.

##### `get_packet_status(run_id, packet_id) -> PacketStatus`

Derives packet status from ledger events by examining the event timeline for the packet. Returns the appropriate `PacketStatus` enum value.

#### Resumption

##### `resume_packet(packet_id, run_id) -> tuple[str, WorkPacket]`

Re-emits a paused or failed packet for claiming. Verifies current status is `PAUSED` or `FAILED`. Writes updated packet file, logs a `PACKET_EMITTED` event. Returns `(event_id, packet)`.

#### Internal Helpers

##### `_resolve_packet(packet_id, run_id) -> tuple[str, WorkPacket]`

Resolves `packet_id` to `(run_id, WorkPacket)`. If `run_id` is `None`, searches across all runs. Raises `FileNotFoundError` if not found.

##### `_read_packet(run_id, packet_id) -> WorkPacket`
##### `_write_packet(packet: WorkPacket) -> None`

Disk I/O helpers. Packets stored at `runs/<run_id>/packets/<packet_id>.json`.

---

### PolicyGate

A standalone policy gate implementation for managing side-effect approvals on work packets. Defined in `tests/test_packet_runtime.py` for Milestone 1 isolation.

```python
class PolicyGate:
    DEFAULT_SIDE_EFFECTS_REQUIRING_APPROVAL = frozenset([
        "file_write",
        "filesystem_modify",
        "shell_exec",
        "network_call",
        "secret_access",
    ])

    def __init__(self, profile_name: str = "default"):
        # Maps aliases: shell_command -> shell_exec, deploy -> file_write
```

#### `requires_approval(side_effect: str) -> bool`

Returns `True` if the side effect type requires approval under this profile. Normalizes aliases before checking against the default set.

#### `get_pending_gates(packet: WorkPacket) -> list[str]`

Returns list of side effect types from `packet.side_effects_requiring_approval` that are in the default approval-required set.

#### `approve_gate(packet_id: str, gate: str) -> None`

No-op in current design. Approval state is tracked via ledger events (`HUMAN_GATE_APPROVED` / `HUMAN_GATE_DENIED`), not PolicyGate.

---

### FakeExecutor

Simulated worker executor for testing and milestone 1. Returns deterministic fake results without any external LLM calls.

When constructed with a ``web_search_tool`` argument, the executor uses it to perform shape discovery during simulation, attaching a ``discovered_shapes`` key to the result dict.

```python
class FakeExecutor:
    def __init__(self, ledger: WorkLedger | None = None, web_search_tool: WebSearchTool | None = None) -> None:
        self.ledger = ledger
        self.web_search_tool = web_search_tool or WebSearchTool()
        self._call_log: list[dict[str, Any]] = []

    def simulate_worker(packet: WorkPacket) -> dict[str, Any]
```

Returns:
```python
{
    "packet_id": str,
    "run_id": str,
    "status": "success",
    "output": str,           # "Simulated result for {packet_id}: {objective_snippet}"
    "duration_seconds": float,
    "cost_estimate": 0.0,
    "children": [],
    "artifacts": [],
    "critic_findings": None,
    "discovered_shapes": list[dict],  # present only when web_search_tool is set and seam is non-empty
}
```

`call_count` property returns the number of `simulate_worker` calls made.

---

### WebSearchTool

Wraps MiniMax web search for use within the packet runtime. Uses the native `mmx-search-query` tool via the mmx-tools MCP bridge when available; falls back to an in-process stub so that FakeExecutor can exercise shape-discovery without external dependencies.

```python
class WebSearchTool:
    def __init__(self, mcp_available: bool | None = None) -> None:
        # mcp_available=True  → always use live MCP search
        # mcp_available=False → always use stub (no network calls)
        # mcp_available=None (default) → auto-detect from import success
    def search_for_shapes(seam: str, limit: int = 5) -> list[dict[str, Any]]
    @property is_live: bool  # True when using live MCP path
```

#### `search_for_shapes(seam, limit=5)`

Search for best-practice shapes relevant to a work seam. Results are returned as a list of plain dicts:

```python
{"title": str, "url": str, "snippet": str, "source": str}
```

The `source` field is `"mmx-search"` for live results or `"stub"` for the in-process fallback.

The live path calls `mmx_search_query(query=f"best practices {seam}")` and normalises the response into result dicts. Any exception is swallowed and an empty list is returned, so the tool is safe to use in tests without mocking.

The stub path returns 5 deterministic results with consistent field names regardless of the seam, allowing FakeExecutor to exercise the complete shape-discovery code path in environments where the MCP bridge is unavailable.

#### `is_live` property

Returns ``True`` when the live MCP path is being used, ``False`` when running the stub. Useful for test assertions and debugging.

---

### `run_goal(objective: str, success_criteria: str = "completed", runs_dir: Path | str | None = None, web_search_tool: WebSearchTool | None = None) -> dict[str, Any]`

Convenience function that runs a goal end-to-end with `FakeExecutor`. Demonstrates the full lifecycle: `create_goal` -> `claim_packet` -> simulate work -> `progress_packet` -> `complete_packet`.

**Returns:**
```python
{
    "run_id": str,
    "packet_id": str,
    "result": dict,     # FakeExecutor result
    "shapes": list,     # Shapes discovered (empty for FakeExecutor)
}
```

Agent ID and harness are read from environment variables `AGENT_ID` and `HARNESS_TYPE` (defaulting to `"test-agent"` and `HarnessType.TEST`).