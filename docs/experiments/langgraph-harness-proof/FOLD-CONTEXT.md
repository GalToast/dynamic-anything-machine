# LangGraph Harness Proof — a design exploration (folded from its own repo)

**What this is:** a small, isolated LangGraph experiment (~1200 lines, deterministic,
no API key needed) validating a *static fixed-hierarchy* orchestration graph:
supervisor → research team (source scout, fit analyst, reviewer) + build team
(scaffold, risk, reviewer) → final integrator. It proves graph shape, state
passing, nested team delegation, and review gates in LangGraph.

**Why it's here (the design context):** DynamicAnythingMachine's thesis is that
*static agent graphs are brittle* — the graph structure must be known at code
time. This experiment is the documented exploration of the static path: it
validates what a fixed LangGraph hierarchy gives you (clean delegation,
deterministic replay, review gates) and, by contrast, motivates the machine's
*dynamic* approach — constructing the execution graph at runtime from the goal
itself. The machine (initial commit 2026-05) predates this proof (recovered
2026-10-02 from the CodexSandboxOffline sandbox — see MERGE-ASSESSMENT.md);
the proof was a parallel capability check, not the machine's prototype.

**Status:** historical artifact. The experiment is complete (the demo runs
deterministically); it is not under active development. `model_config.py` shows
the provider-LLM hook if you want to run it live. Original file set preserved
as-recovered; no changes made during the fold.

**Fold note (2026-10-12):** this experiment previously lived as its own repo
(`langgraph-harness`, 2 commits). Folded into `docs/experiments/` to keep the
orchestration arc in one place — the exploration record + the machine it
informed.
