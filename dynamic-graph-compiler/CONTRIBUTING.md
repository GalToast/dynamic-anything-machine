# Contributing Guide

Quick reference for adding new features to the dynamic-graph-compiler project.

---

## Project Structure

This is two systems sharing the same `runs/` directory:

- **Graph compiler**: `graph_compiler.py`, `policy.py`, `dynamic_schema.py`
- **Packet runtime**: `packet_runtime.py`, `work_packet.py`, `work_ledger.py`, `shape_registry.py`

---

## Adding a New CLI Command

1. Edit `packet_cli.py`
2. Add a subparser in the argument parser section:

```python
subparsers = parser.add_subparsers(dest="command", required=True)
# Add your subparser here
my_cmd = subparsers.add_parser("my-cmd", help="Does a thing")
my_cmd.add_argument("packet_id", help="Packet ID")
my_cmd.set_defaults(func=handle_my_cmd)
```

3. Add a handler function:

```python
def handle_my_cmd(args, runtime: PacketRuntime) -> int:
    """Handle my-cmd command."""
    # ... implement
    return 0
```

---

## Adding a New Packet Lifecycle Event

### Step 1: Add constant to `work_ledger.py`

In the event type constants section:

```python
NEW_EVENT = "new_event"
```

### Step 2: Add handler method to `WorkLedger`

```python
def log_new_event(
    self,
    run_id: str,
    packet_id: str,
    # ... other params
) -> str:
    """Log a new event."""
    return self.log_event(
        run_id=run_id,
        event_type=NEW_EVENT,
        packet_id=packet_id,
        agent_id=agent_id,
        harness=harness,
        depth=depth,
        data={...},
    )
```

### Step 3: Call from `packet_runtime.py`

```python
self.ledger.log_new_event(run_id, packet_id, ...)
```

---

## Adding a New Shape Type

### Step 1: Add to `ShapeType` enum in `work_packet.py`

```python
class ShapeType(str, Enum):
    # ... existing types
    NEW_SHAPE_TYPE = "new_shape_type"
```

### Step 2: Add payload schema in `shape_registry.py`

Update `Shape.from_dict()` or create a dedicated method to handle the new shape type's specific fields.

### Step 3: Update `Shape` model if needed

Add any new fields specific to the new shape type in the `Shape` model.

---

## Writing Tests

### Use `conftest.py` Fixtures

All tests use top-level module imports. `conftest.py` ensures the project root is on `sys.path` so imports resolve correctly.

Available fixtures:

```python
@pytest.fixture
def project_root():
    """Project root directory."""
    return Path(__file__).parent.parent

@pytest.fixture
def runs_dir(project_root, tmp_path):
    """A temporary runs directory (sandboxed per-test)."""
    return tmp_path / "runs"

@pytest.fixture
def runtime_factory(tmp_path, shapes_dir):
    """Factory that returns a PacketRuntime with isolated temp directories."""
    def _make(profile=None, agent_id="test-agent"):
        return PacketRuntime(
            runs_dir=tmp_path / "runs",
            shapes_dir=shapes_dir,
            profile=profile,
            agent_id=agent_id,
        )
    return _make

@pytest.fixture
def ledger_factory(tmp_path):
    """Factory that returns a WorkLedger with isolated temp directory."""
    from work_ledger import WorkLedger
    def _make():
        return WorkLedger(runs_dir=tmp_path / "runs")
    return _make

@pytest.fixture
def registry_factory(shapes_dir):
    """Factory that returns a ShapeRegistry with isolated temp directory."""
    from shape_registry import ShapeRegistry
    from work_ledger import WorkLedger
    def _make(ledger=None):
        return ShapeRegistry(shapes_dir=shapes_dir, ledger=ledger or WorkLedger(...))
    return _make
```

### Example Test

```python
def test_my_feature(runtime_factory):
    runtime = runtime_factory()
    run_id, packet_id = runtime.create_goal("Test objective")

    status = runtime._get_packet_status(run_id, packet_id)
    assert status == PacketStatus.EMITTED
```

### Known Issues with Tests

- `policy.py` has a broken `DEFAULT_POLICY` (references `NodeType.INPUT` which may not exist in `dynamic_schema`). If a test uses `DEFAULT_POLICY`, it may fail with an import error.
- Graph-compiler tests may have issues with `dynamic_schema` not being fully implemented.
- Use `conftest.py` workarounds rather than importing `DEFAULT_POLICY` directly.

---

## Running Tests

```bash
# Run all tests
pytest tests/

# Run a specific test file
pytest tests/test_work_packet.py

# Run with verbose output
pytest tests/ -v

# Run only tests that don't use graph compiler (if import errors occur)
pytest tests/ --ignore=tests/test_policy_blocks_side_effects.py
```

---

## Code Style

- Use Pydantic models for all data structures
- Use ISO 8601 UTC timestamps (`datetime.now(timezone.utc).isoformat()`)
- Use `_now_iso()` helper for consistency
- All file paths should use `pathlib.Path`
- Event types should be constants in `work_ledger.py`

---

## Quick Reference

| Task | Location |
|------|---------|
| New CLI command | `packet_cli.py` |
| New event type | `work_ledger.py` (constants + handler method) |
| New shape type | `work_packet.py` (`ShapeType` enum) + `shape_registry.py` |
| New packet field | `work_packet.py` (`WorkPacket` model) |
| Test fixtures | `tests/conftest.py` |
| Policy validation | `policy.py` (`PolicyValidator` class) |
