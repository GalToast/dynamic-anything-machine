# DynamicAnythingMachine

A dynamic multi-agent orchestration engine: give it a natural-language goal, and it constructs a custom execution graph at runtime, coordinates specialized workers across parallel seams, and delivers a synthesized result — with full auditability, checkpoint recovery, and human gates.

All code lives in [`dynamic-graph-compiler/`](dynamic-graph-compiler/). Start with its [README](dynamic-graph-compiler/README.md).

## What it does

Static agent graphs are brittle (must know the whole structure at code time). Unstructured agent swarms have no coordination or audit trail. This system is dynamic by design:

1. An orchestrator analyzes the goal and decomposes it into parallel work seams.
2. A pre-built graph (28 nodes, wired once) streams work through, coordinating workers per seam.
3. Every step is checkpointed; failures recover; side effects are policy-gated; a human approves or denies child delegation.

## Quick start

```bash
cd dynamic-graph-compiler

# Simulation mode (fast, no API calls)
python showcase/showcase_demo.py "Research LLM fine-tuning" --no-llm

# With a real model (MiniMax)
python showcase/showcase_demo.py "Research LLM fine-tuning"

# Web demo
python web-demo/app.py
# → http://localhost:5000
```

## Status

Research system, not a production product. 195 tests passing (`pytest tests/`). See [docs/](dynamic-graph-compiler/docs/) for API, CLI, and design notes.

## License

MIT — see [LICENSE](LICENSE).
