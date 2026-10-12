# LangGraph Harness Proof

Small, isolated LangGraph proof for hierarchical agent orchestration:

```text
main supervisor
  -> research team lead
      -> source scout
      -> fit analyst
      -> research reviewer
  -> build team lead
      -> scaffold worker
      -> risk worker
      -> build reviewer
  -> final integrator
```

The default demo is deterministic and runs without an API key. It proves the graph shape, state passing, nested team delegation, and review gates. Provider-backed LLM calls can be added through `model_config.py`.

## Setup

```powershell
cd "C:\Users\HP\Desktop\Temp while my comp is at the shop\ai-models\langgraph-harness"
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python run_harness.py --objective "Find and package 10 AI automation contract roles"
```

## Optional MiniMax / OpenAI-Compatible Config

Copy `.env.example` to `.env` and fill values locally. Do not commit secrets.

```text
LANGGRAPH_MODEL=MiniMax-M2.7
LANGGRAPH_BASE_URL=https://api.minimax.io/v1
MINIMAX_API_KEY=...
```

LangChain's OpenAI chat integration accepts custom OpenAI-compatible base URLs. MiniMax lists `MiniMax-M2.7` on its OpenAI-compatible model endpoint. This demo keeps API use optional so the harness can be tested safely before spending tokens.

## Files

- `run_harness.py` - CLI entrypoint.
- `swarm_graph.py` - parent graph plus nested team subgraphs.
- `cockpit.py` - cockpit-neutral runner for Codex, Claude, Gemini, Qwen, OpenCode, or any other CLI agent.
- `cockpit_graph.py` - LangGraph planner that emits worker packets as a file protocol.
- `profiles/` - reusable swarm profiles.
- `model_config.py` - optional ChatOpenAI/OpenAI-compatible model factory.
- `requirements.txt` - isolated Python dependencies.
- `.env.example` - provider configuration template.

## Cockpit-Neutral Runner

Any CLI agent can drive this by shelling out to `cockpit.py`.

Start a run:

```powershell
.\.venv\Scripts\python cockpit.py start --profile app-drive --objective "Find and package 20 AI automation contract roles"
```

Read the emitted packet prompts under:

```text
runs/<run_id>/packets/
```

Record a result:

```powershell
.\.venv\Scripts\python cockpit.py record <run_id> <worker_id> --harness codex --status complete --summary "Built packet and listed changed paths" --changed-path outreach/example.md
```

Integrate:

```powershell
.\.venv\Scripts\python cockpit.py integrate <run_id>
```

This keeps Codex/Claude/Gemini/OpenCode as possible cockpits while LangGraph acts as the neutral planner and result collector.

## Dynamic Ephemeral Graph Runner

`dynamic_runner.py` is the more general experiment: it creates a fresh graph spec from the objective, validates it against a strict schema, compiles it into LangGraph, executes deterministic read-only nodes, and writes the full run to `runs/<run_id>/`.

Generate a spec:

```powershell
.\.venv\Scripts\python dynamic_runner.py plan --objective "Build an agent system that can decompose any objective into worker packets"
```

Execute a dynamic run:

```powershell
.\.venv\Scripts\python dynamic_runner.py run --run-id smoke-dynamic --objective "Build a general agent delegation system with retrieval, critic, human gate, and worker packets"
```

The dynamic runner is intentionally self-deterministic:

- graph specs are JSON and saved per run,
- validation requires allowed node types only,
- critic and human-gate nodes are mandatory,
- side effects stay disabled unless a human cockpit explicitly approves them,
- run artifacts are auditable under `runs/<run_id>/`.
