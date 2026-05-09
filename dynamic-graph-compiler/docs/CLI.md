# CLI Reference

Command-line interface for the recursive work-packet runtime (`packet_cli.py`).

---

## Running the CLI

```bash
python packet_cli.py <command> [options]   # Single command
python packet_cli.py                      # Interactive mode
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_ID` | `"unknown"` | Identifier for the agent claiming packets |
| `HARNESS_TYPE` | `""` (treated as `UNKNOWN`) | Harness type from `HarnessType` enum |

`HARNESS_TYPE` must be one of: `unknown`, `claude_code`, `codex`, `gemini_cli`, `qwen_cli`, `opencode`, `openclaw`, `minimax`, `human`, `test`.

---

## Interactive Mode

When run with no command argument, `packet_cli.py` enters an interactive loop with a `packet>` prompt. Type `help` to see the full command menu.

```
packet> help
```

Interactive mode supports all commands listed below with a simplified syntax (e.g., `goal "Build auth"` instead of `python packet_cli.py goal "Build auth"`). Use `--flag value` style for options within the interactive session.

Exit interactive mode with `exit`, `quit`, or `q`.

---

## Commands

### goal

Create a new goal packet.

```bash
python packet_cli.py goal "objective text" [--success-criteria "criteria"] [--seam NAME]
```

**Description:** Creates a new run and emits the root goal packet. The packet is immediately available for claiming via `claim`.

**Options:**
- `objective` (positional, required) — Natural-language description of the goal
- `--success-criteria` — How to know the goal is done (default: `"completed"`)
- `--seam` — Work seam name (default: `"root"`)

**Example:**
```bash
python packet_cli.py goal "Build user authentication system" --success-criteria "All tests pass" --seam auth
```

---

### list-seams

List open packets available for claiming.

```bash
python packet_cli.py list-seams [--run-id RUN_ID]
```

**Description:** Shows all EMITTED (unclaimed) packets across all runs or within a specific run. Each row shows `run_id`, `packet_id` (truncated to 8 chars), `seam`, `depth`, and `age`.

**Options:**
- `--run-id` — Filter to a specific run

**Example:**
```bash
python packet_cli.py list-seams
# run_id  packet_  seam     depth  age
# ──────────────────────────────────────────
# abc123  def456   auth     1      5m
# abc123  789abc   database 2      2m
```

---

### claim

Claim a packet for the current agent.

```bash
python packet_cli.py claim <packet_id> [--run-id RUN_ID] [--agent-id ID] [--harness TYPE]
```

**Description:** Takes ownership of an EMITTED or RESUMED packet. The packet must be in a claimable state (not already claimed, completed, or failed). After claiming, displays the full packet summary.

**Options:**
- `packet_id` (positional, required) — ID of the packet to claim
- `--run-id` — Run ID (auto-resolved if omitted)
- `--agent-id` — Override agent identifier (default: from `AGENT_ID` env var)
- `--harness` — Harness type (choices from HarnessType enum)

**Example:**
```bash
python packet_cli.py claim abc123 --agent-id fred --harness claude_code
```

---

### status

Show current packet state and audit trail.

```bash
python packet_cli.py status <packet_id> [--run-id RUN_ID]
```

**Description:** Displays detailed packet information including status, seam, depth, timestamps, objective, success criteria, current state (last 10 key-value pairs), open questions, audit trail (last 10 events), and a full markdown summary.

**Options:**
- `packet_id` (positional, required) — ID of the packet
- `--run-id` — Run ID (auto-resolved if omitted)

**Example:**
```bash
python packet_cli.py status abc123
```

---

### progress

Checkpoint state progress.

```bash
python packet_cli.py progress <packet_id> --state JSON [--artifacts JSON] [--run-id RUN_ID]
```

**Description:** Merges a state update into the packet's `current_state` and logs a `PACKET_PROGRESS` event. Use this to record work-in-progress without completing the packet.

**Options:**
- `packet_id` (positional, required) — ID of the packet
- `--state` (required) — JSON string of a dict to merge into `current_state`
- `--artifacts` — Optional JSON list of artifact paths produced
- `--run-id` — Run ID (auto-resolved if omitted)

**Example:**
```bash
python packet_cli.py progress abc123 --state '{"files_written": 3, "phase": "complete"}'
```

---

### delegate

Request delegation to create a child packet.

```bash
python packet_cli.py delegate <packet_id> <seam> [--objective TEXT] [--constraints JSON] [--run-id RUN_ID]
```

**Description:** Creates a child packet with the specified seam. Whether the child is immediately emitted or stays pending depends on the parent's `delegation_permission` (REQUEST needs approval, APPROVE is auto-approved).

Child inherits and narrows parent constraints (`allowed_tools`, `forbidden_tools`, `owned_paths`, `forbidden_paths`, `timeout_seconds`, `budget`). `max_child_depth` is decremented by 1.

**Options:**
- `packet_id` (positional, required) — Parent packet ID
- `seam` (positional, required) — Work seam for the child
- `--objective` — Custom objective for the child (default: `"Child work for seam: <seam>"`)
- `--constraints` — JSON dict of constraint overrides for the child
- `--run-id` — Run ID (auto-resolved if omitted)

**Example:**
```bash
python packet_cli.py delegate abc123 file-parser --objective "Parse config files" --constraints '{"allowed_tools": ["read_file", "grep"]}'
```

---

### approve

Approve a pending child packet.

```bash
python packet_cli.py approve <child_packet_id> [--run-id RUN_ID] [--approver NAME]
```

**Description:** Approves a child packet that was created with `REQUEST` delegation permission. The child is then emitted and becomes available for claiming.

**Options:**
- `child_packet_id` (positional, required) — ID of the child packet to approve
- `--run-id` — Run ID (auto-resolved if omitted)
- `--approver` — Approver identifier (default: `"human"`)

**Example:**
```bash
python packet_cli.py approve def456 --approver fred
```

---

### deny

Deny a child packet.

```bash
python packet_cli.py deny <child_packet_id> --reason TEXT [--run-id RUN_ID]
```

**Description:** Denies a child packet that was created with `REQUEST` delegation permission. Sets `current_state["denial_reason"]` on the child and logs a `CHILD_DENIED` event.

**Options:**
- `child_packet_id` (positional, required) — ID of the child packet to deny
- `--reason` (required) — Reason for denial
- `--run-id` — Run ID (auto-resolved if omitted)

**Example:**
```bash
python packet_cli.py deny def456 --reason "Too broad in scope"
```

---

### complete

Mark packet as completed with output.

```bash
python packet_cli.py complete <packet_id> --output TEXT [--shapes JSON] [--critic TEXT] [--run-id RUN_ID]
```

**Description:** Marks a packet as successfully completed. Writes output to `runs/<run_id>/final/<packet_id>.md`, saves any discovered shapes to the registry, logs a `PACKET_COMPLETED` event, and checks if this completes the run.

**Options:**
- `packet_id` (positional, required) — ID of the packet to complete
- `--output` (required) — Human-readable output/result description
- `--shapes` — Optional JSON list of shape dicts discovered during execution
- `--critic` — Optional critic findings
- `--run-id` — Run ID (auto-resolved if omitted)

**Example:**
```bash
python packet_cli.py complete abc123 --output "Auth system built and tested"
```

---

### fail

Mark packet as failed.

```bash
python packet_cli.py fail <packet_id> --error TEXT [--run-id RUN_ID]
```

**Description:** Marks a packet as failed. Sets `current_state["error"]`, logs an `ERROR` event and a `PACKET_FAILED` event. If the packet is the root packet, also logs `RUN_FAILED`.

**Options:**
- `packet_id` (positional, required) — ID of the packet to fail
- `--error` (required) — Error message
- `--run-id` — Run ID (auto-resolved if omitted)

**Example:**
```bash
python packet_cli.py fail abc123 --error "Timeout exceeded after 300s"
```

---

### gate

Pause at a human approval gate.

```bash
python packet_cli.py gate <packet_id> --name NAME [--run-id RUN_ID]
```

**Description:** Pauses a packet at a human approval gate. Appends `human_gate:<name>` to `open_questions` and logs a `HUMAN_GATE_ENTERED` event. The packet will not proceed until resolved with `resolve-gate`.

**Options:**
- `packet_id` (positional, required) — ID of the packet
- `--name` (required) — Name/description of the gate
- `--run-id` — Run ID (auto-resolved if omitted)

**Example:**
```bash
python packet_cli.py gate abc123 --name "deploy-production"
```

---

### resolve-gate

Resolve a human gate (approve or deny).

```bash
python packet_cli.py resolve-gate <packet_id> --name NAME --decision approve|deny [--approver NAME] [--run-id RUN_ID]
```

**Description:** Resolves a human gate. If approved, the gate is removed from `open_questions` and a `HUMAN_GATE_APPROVED` event is logged. If denied, sets `current_state["gate_denied"]` and logs `HUMAN_GATE_DENIED`.

**Options:**
- `packet_id` (positional, required) — ID of the packet
- `--name` (required) — Name of the gate to resolve
- `--decision` (required) — Either `approve` or `deny`
- `--approver` — Approver identifier (default: `"human"`)
- `--run-id` — Run ID (auto-resolved if omitted)

**Example:**
```bash
python packet_cli.py resolve-gate abc123 --name deploy-production --decision approve --approver fred
```

---

### checkpoint

Create a checkpoint snapshot.

```bash
python packet_cli.py checkpoint <packet_id> --label TEXT [--run-id RUN_ID]
```

**Description:** Creates a named `ResumptionSnapshot` from the packet's current state. The snapshot is saved to `runs/<run_id>/checkpoints/<checkpoint_id>.json` and a `CHECKPOINT_CREATED` event is logged. The checkpoint can be used for resumption.

**Options:**
- `packet_id` (positional, required) — ID of the packet
- `--label` (required) — Human-readable label for the checkpoint
- `--run-id` — Run ID (auto-resolved if omitted)

**Example:**
```bash
python packet_cli.py checkpoint abc123 --label "mid-file-write"
```

---

### shapes

Search for reusable shapes.

```bash
python packet_cli.py shapes [--tag TAG] [--seam SEAM] [--type TYPE] [--query QUERY]
```

**Description:** Searches the shape registry using multi-criteria filtering. Returns a table of matching shapes showing truncated `shape_id`, `type`, `name`, tags (first 3), and usage count.

**Options:**
- `--tag` — Filter by required tag
- `--seam` — Filter by work seam
- `--type` — Filter by shape type (choices from ShapeType enum)
- `--query` — Text search in name and description

**Example:**
```bash
python packet_cli.py shapes --tag security --type skill_definition
```

---

### use-shape

Create a goal using a shape as a template.

```bash
python packet_cli.py use-shape <shape_id> --objective TEXT [--success-criteria TEXT]
```

**Description:** Retrieves a shape from the registry, increments its `usage_count`, and creates a new goal using the shape's `seam` as the goal seam. The shape's `payload` and other fields are available for the agent to use but are not automatically populated into the new packet.

**Options:**
- `shape_id` (positional, required) — ID of the shape to use
- `--objective` (required) — Objective for the new goal
- `--success-criteria` — Success criteria (default: `"completed"`)

**Example:**
```bash
python packet_cli.py use-shape abc123 --objective "Run security review on codebase" --success-criteria "No critical findings"
```

---

### resume

Re-emit a paused/failed packet for claiming.

```bash
python packet_cli.py resume <packet_id> [--run-id RUN_ID]
```

**Description:** Re-emits a PAUSED or FAILED packet for claiming by another agent. The packet must be in PAUSED or FAILED state. Logs a `PACKET_EMITTED` event and displays the updated packet summary.

**Options:**
- `packet_id` (positional, required) — ID of the packet to resume
- `--run-id` — Run ID (auto-resolved if omitted)

**Example:**
```bash
python packet_cli.py resume abc123
```

---

### history

Show full audit trail for a run.

```bash
python packet_cli.py history <run_id>
```

**Description:** Displays all events for a run in chronological order as a table: `timestamp` (truncated to 19 chars), `event_type`, `packet_id` (truncated to 8 chars, or `-` if none), and `data` (truncated to 40 chars).

**Options:**
- `run_id` (positional, required) — ID of the run

**Example:**
```bash
python packet_cli.py history abc123
```

---

### replay

Reconstruct and display run state from ledger.

```bash
python packet_cli.py replay <run_id>
```

**Description:** Uses `ReplayEngine` to reconstruct the complete run state from the append-only event ledger. Displays a human-readable report including packet counts, claimed packets, delegation tree, shapes created, errors, and run summary.

**Options:**
- `run_id` (positional, required) — ID of the run

**Example:**
```bash
python packet_cli.py replay abc123
```

---

### run

Run a goal end-to-end with FakeExecutor.

```bash
python packet_cli.py run <objective> [--success-criteria TEXT]
```

**Description:** Creates a goal, claims it (using `AGENT_ID` and `HARNESS_TYPE` env vars), simulates work with `FakeExecutor`, logs progress, and completes the packet. A complete demonstration of the full packet lifecycle without any real LLM calls.

**Options:**
- `objective` (positional, required) — Natural-language goal description
- `--success-criteria` — Success criteria (default: `"completed"`)

**Example:**
```bash
python packet_cli.py run "Process all files in the input directory"
```

---

### list-runs

List all runs.

```bash
python packet_cli.py list-runs
```

**Description:** Shows all runs in the `runs/` directory as a table: `run_id`, `created` (relative age), and `events` (event count). Sorted by creation time, most recent first.

**Example:**
```bash
python packet_cli.py list-runs
# run_id  created  events
# ───────────────────────────
# abc123  5m ago   12
# def456  2h ago   8
```