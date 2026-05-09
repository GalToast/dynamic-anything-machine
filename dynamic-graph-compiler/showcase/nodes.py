"""
Node functions for the Recursive Research Orchestra LangGraph showcase.

Architecture:
- orchestrator_node_fn: Analyzes goal, spawns ManagerNodes at runtime via root_builder
- manager_node_fn: Coordinates workers for a specific seam
- worker_node_fn: Executes atomic tasks, reports back to manager
- synthesis_node_fn: Aggregates all findings into final output
- router_node_fn: Conditional routing based on state
- human_gate_node_fn: Suspends execution awaiting CLI approval

Key innovation: OrchestratorNode calls root_builder.add_node() from INSIDE its
node function (before compile()) to dynamically add ManagerNodes and WorkerNodes.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# WorkLedger (from work_ledger.py)
# ---------------------------------------------------------------------------

from work_ledger import WorkLedger, PACKET_CREATED, PACKET_COMPLETED, RUN_COMPLETED

# ---------------------------------------------------------------------------
# OrchestratorState
# ---------------------------------------------------------------------------

from showcase.states import (
    OrchestratorState,
    SeamState,
    ManagerState,
    WorkerState,
    TokenBudget,
)

# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

from showcase.tools import execute_tool, TOOLS, worker_tools

# ---------------------------------------------------------------------------
# MiniMaxDirectClient (reused from packet_runtime.py)
# ---------------------------------------------------------------------------

from packet_runtime import MiniMaxDirectClient

# ---------------------------------------------------------------------------
# Ledger helper
# ---------------------------------------------------------------------------


def _make_ledger(runs_dir: str = "runs") -> WorkLedger:
    """Create or reuse a WorkLedger instance."""
    from pathlib import Path
    return WorkLedger(runs_dir=Path(runs_dir))


# ---------------------------------------------------------------------------
# Node function helpers
# ---------------------------------------------------------------------------


def _token_append(state: OrchestratorState, token: str) -> None:
    """Append a streaming token to state (used for progressive output)."""
    state.streaming_tokens.append(token)


# ---------------------------------------------------------------------------
# orchestrator_phase1_fn — Phase 1 analysis (produces seam plan)
# ---------------------------------------------------------------------------


def orchestrator_phase1_fn(
    state: OrchestratorState,
    llm_with_tools: Any,
) -> Dict[str, Any]:
    """
    Phase 1 orchestrator: analyzes goal and produces seam plan.

    This function does NOT mutate the graph. It purely analyzes the goal,
    determines seams, and populates state.managers and state.seams with
    the full seam plan (manager + worker assignments).

    Used in Phase 1 of the two-phase orchestration.
    """
    updates: Dict[str, Any] = {}

    _token_append(state, f"\n[ORCHESTRATOR] Analyzing goal: {state.goal[:60]}...\n")

    # Log to WorkLedger
    try:
        ledger = _make_ledger()
        ledger.log_event(
            run_id=state.run_id,
            event_type="orchestrator_start",
            packet_id=None,
            agent_id="orchestrator",
            harness="langgraph",
            depth=0,
            data={"goal": state.goal},
        )
    except Exception:
        pass

    # Use MiniMax M2.7 to analyze the goal and decompose into seams
    analysis = ""
    seams = []

    if llm_with_tools:
        try:
            prompt = _build_orchestrator_prompt(state.goal, state.mode)
            response = llm_with_tools.invoke([{"role": "user", "content": prompt}])
            analysis = getattr(response, "content", "") or str(response)
            _token_append(state, f"[ORCHESTRATOR] LLM analysis: {analysis[:300]}...\n")

            # Try to parse seams from LLM response
            try:
                json_match = re.search(r'\[[\s\S]*\]', analysis)
                if json_match:
                    seams_data = json.loads(json_match.group())
                    seams = [
                        {
                            "id": s.get("id", f"seam_{i}"),
                            "description": s.get("description", ""),
                            "tasks": s.get("tasks", []),
                        }
                        for i, s in enumerate(seams_data)
                    ]
            except (json.JSONDecodeError, KeyError):
                pass
        except Exception as exc:
            _token_append(state, f"[ORCHESTRATOR] LLM unavailable ({exc}), using heuristic\n")

    # Fallback: heuristic decomposition
    if not seams:
        seams = _decompose_into_seams(state.goal, state.mode)

    _token_append(state, f"[ORCHESTRATOR] Identified {len(seams)} seam(s): {[s['id'] for s in seams]}\n")

    # Populate state.managers and state.seams with the full plan
    for seam_info in seams:
        seam_id = seam_info["id"]
        seam_desc = seam_info["description"]
        manager_id = f"manager_{seam_id}"
        tasks = seam_info.get("tasks", [])

        # Create worker IDs for this seam
        worker_ids = [f"worker_{seam_id}_{i}" for i in range(len(tasks))]

        # Register seam
        state.seams[seam_id] = SeamState(
            seam_id=seam_id,
            description=seam_desc,
            status="pending",
            manager_id=manager_id,
            worker_ids=worker_ids,
        )

        # Register manager with full task list
        state.managers[manager_id] = ManagerState(
            manager_id=manager_id,
            seam_id=seam_id,
            status="pending",
            spawned_worker_ids=worker_ids,
        )

    # Mark orchestrator complete
    if "orchestrator" not in state.completed_agents:
        state.active_agents.append("orchestrator")
        state.completed_agents.append("orchestrator")

    if analysis:
        state.findings["orchestrator_analysis"] = analysis

    # Log completion
    try:
        ledger = _make_ledger()
        ledger.log_event(
            run_id=state.run_id,
            event_type="orchestrator_complete",
            packet_id=None,
            agent_id="orchestrator",
            harness="langgraph",
            depth=0,
            data={
                "seam_count": len(seams),
                "managers": list(state.managers.keys()),
                "workers": sum(len(m.spawned_worker_ids) for m in state.managers.values()),
            },
        )
    except Exception:
        pass

    updates["status"] = "orchestrator_done"
    updates["seam_count"] = len(seams)
    updates["seams"] = {k: v.model_dump() for k, v in state.seams.items()}
    updates["managers"] = {k: v.model_dump() for k, v in state.managers.items()}
    updates["completed_agents"] = list(state.completed_agents)
    updates["active_agents"] = list(state.active_agents)

    return updates


# ---------------------------------------------------------------------------
# orchestrator_node_fn — Dynamic Graph Mutation at Runtime!
# ---------------------------------------------------------------------------


def orchestrator_node_fn(
    state: OrchestratorState,
    root_builder: Any,
    llm_with_tools: Any,
) -> Dict[str, Any]:
    """
    OrchestratorNode: analyzes goal and dynamically spawns ManagerNodes.

    This is the KEY innovation of the showcase: calling root_builder.add_node()
    from INSIDE a node function to mutate the graph at runtime.

    The pattern:
    1. root_builder is stored on CompiledGraph BEFORE compile()
    2. When this node runs, it has access to root_builder
    3. It calls root_builder.add_node() to add ManagerNodes for each seam
    4. It calls root_builder.add_edge() to wire them up
    5. After all nodes added, router will route to the new nodes on next iteration

    This enables TRUE runtime graph mutation — nodes are added mid-execution.
    """
    updates: Dict[str, Any] = {}

    _token_append(state, f"\n[ORCHESTRATOR] Analyzing goal: {state.goal[:60]}...\n")

    # Log to WorkLedger
    try:
        ledger = _make_ledger()
        ledger.log_event(
            run_id=state.run_id,
            event_type="orchestrator_start",
            packet_id=None,
            agent_id="orchestrator",
            harness="langgraph",
            depth=0,
            data={"goal": state.goal},
        )
    except Exception:
        pass

    # Use MiniMax M2.7 to analyze the goal and decompose into seams
    analysis = ""
    seams = []

    if llm_with_tools:
        try:
            prompt = _build_orchestrator_prompt(state.goal, state.mode)
            response = llm_with_tools.invoke([{"role": "user", "content": prompt}])
            analysis = getattr(response, "content", "") or str(response)
            _token_append(state, f"[ORCHESTRATOR] LLM analysis: {analysis[:300]}...\n")

            # Try to parse seams from LLM response
            try:
                json_match = re.search(r'\[[\s\S]*\]', analysis)
                if json_match:
                    seams_data = json.loads(json_match.group())
                    seams = [
                        {
                            "id": s.get("id", f"seam_{i}"),
                            "description": s.get("description", ""),
                            "tasks": s.get("tasks", []),
                        }
                        for i, s in enumerate(seams_data)
                    ]
            except (json.JSONDecodeError, KeyError):
                pass
        except Exception as exc:
            _token_append(state, f"[ORCHESTRATOR] LLM unavailable ({exc}), using heuristic\n")

    # Fallback: heuristic decomposition
    if not seams:
        seams = _decompose_into_seams(state.goal, state.mode)

    _token_append(state, f"[ORCHESTRATOR] Identified {len(seams)} seam(s): {[s['id'] for s in seams]}\n")

    # CRITICAL: Dynamic graph mutation!
    # Call root_builder.add_node() from INSIDE this node function
    for seam_info in seams:
        seam_id = seam_info["id"]
        seam_desc = seam_info["description"]
        manager_id = f"manager_{seam_id}"

        # Skip if node already exists in builder (guard against double-add on re-invocation)
        if manager_id in root_builder.nodes:
            _token_append(state, f"[GRAPH] ManagerNode {manager_id} already exists, skipping\n")
        else:
            # Create manager node function
            def make_manager_fn(sid, mid):
                def manager_fn(orch_state: OrchestratorState) -> Dict[str, Any]:
                    return manager_node_fn(orch_state, sid, mid)
                return manager_fn

            # Add manager node to the builder (DYNAMIC GRAPH MUTATION!)
            root_builder.add_node(manager_id, make_manager_fn(seam_id, manager_id))

            # Wire: orchestrator -> manager
            root_builder.add_edge("orchestrator", manager_id)

            # Register in state
            state.seams[seam_id] = SeamState(
                seam_id=seam_id,
                description=seam_desc,
                status="active",
                manager_id=manager_id,
            )
            state.managers[manager_id] = ManagerState(
                manager_id=manager_id,
                seam_id=seam_id,
                status="active",
            )
            state.dynamic_nodes[manager_id] = "manager"

            _token_append(state, f"[GRAPH] Added ManagerNode: {manager_id} for seam: {seam_id}\n")

        # Spawn workers for this seam
        tasks = seam_info.get("tasks", [])
        for i, task_desc in enumerate(tasks):
            worker_id = f"worker_{seam_id}_{i}"

            # Skip if already exists
            if worker_id in root_builder.nodes:
                _token_append(state, f"[GRAPH] WorkerNode {worker_id} already exists, skipping\n")
            else:
                def make_worker_fn(wid, mid, sid, task):
                    def worker_fn(orch_state: OrchestratorState) -> Dict[str, Any]:
                        return worker_node_fn(orch_state, wid, mid, sid, task)
                    return worker_fn

                root_builder.add_node(worker_id, make_worker_fn(worker_id, manager_id, seam_id, task_desc))
                root_builder.add_edge(manager_id, worker_id)

                # Register
                state.managers[manager_id].spawned_worker_ids.append(worker_id)
                state.seams[seam_id].worker_ids.append(worker_id)
                state.dynamic_nodes[worker_id] = "worker"

                _token_append(state, f"[GRAPH] Added WorkerNode: {worker_id} for task: {task_desc[:40]}...\n")

    # Mark orchestrator complete
    if "orchestrator" not in state.completed_agents:
        state.active_agents.append("orchestrator")
        state.completed_agents.append("orchestrator")

    if analysis:
        state.findings["orchestrator_analysis"] = analysis

    # Log completion
    try:
        ledger = _make_ledger()
        ledger.log_event(
            run_id=state.run_id,
            event_type="orchestrator_complete",
            packet_id=None,
            agent_id="orchestrator",
            harness="langgraph",
            depth=0,
            data={
                "seam_count": len(seams),
                "managers": list(state.managers.keys()),
                "workers": sum(len(m.spawned_worker_ids) for m in state.managers.values()),
            },
        )
    except Exception:
        pass

    updates["status"] = "orchestrator_done"
    updates["seam_count"] = len(seams)
    updates["seams"] = {k: v.model_dump() for k, v in state.seams.items()}
    updates["managers"] = {k: v.model_dump() for k, v in state.managers.items()}
    updates["completed_agents"] = list(state.completed_agents)
    updates["active_agents"] = list(state.active_agents)

    return updates


def _build_orchestrator_prompt(goal: str, mode: str) -> str:
    """Build the prompt for orchestrator LLM calls."""
    return f"""You are the Orchestrator for a Recursive Research Orchestra — a multi-agent LangGraph system.

Goal: {goal}
Mode: {mode}

Analyze this goal and decompose it into logical "seams" — independent work units
that can be executed in parallel by different manager/worker agents.

For a RESEARCH goal, seams might be:
- literature search
- technical deep-dive
- competitive analysis
- summary synthesis

For a BUILD goal, seams might be:
- design/planning
- core implementation
- testing
- documentation

Respond with a JSON array of seams, each with:
- id: short identifier (no spaces)
- description: human-readable description
- tasks: list of 2-3 specific tasks for workers under this seam

Example for "Research LLM fine-tuning":
[
  {{"id": "literature", "description": "Survey existing research on LLM fine-tuning", "tasks": ["Find key papers on LoRA", "Find papers on RLHF", "Map the academic landscape"]}},
  {{"id": "techniques", "description": "Analyze fine-tuning techniques and best practices", "tasks": ["Analyze LoRA technique", "Analyze QLoRA technique", "Compare fine-tuning approaches"]}},
  {{"id": "tools", "description": "Identify tools and frameworks for fine-tuning", "tasks": ["List open-source frameworks", "Compare cloud training options"]}}
]

Respond ONLY with the JSON array, no markdown formatting, no backticks."""


def _build_worker_prompt(task: str, seam_id: str, mode: str = "research") -> str:
    """Build the prompt for worker LLM task execution."""
    if mode == "build":
        if seam_id == "design":
            return f"""You are a Software ArchitectWorker in a Recursive Research Orchestra — a multi-agent LangGraph system.

You are executing a design task assigned to you by your manager.

## Task
{task}

## Seam: design

## Instructions
- Analyze the goal and produce a technical specification
- Identify the file structure needed
- Define data models, API endpoints, and core modules
- Output a SPEC.md file with full technical details

## Output Format
Write the SPEC.md file with:
1. Project overview and requirements
2. File structure (list all files to create)
3. Data models (class/function signatures with docstrings)
4. API endpoints (HTTP methods, routes, request/response shapes)
5. Dependencies (libraries needed)

## Rules
- Be concrete and actionable — avoid vague descriptions
- Use modern Python best practices
- Specify exact file paths and content structure"""
        elif seam_id == "implementation":
            return f"""You are a Software Engineer Worker in a Recursive Research Orchestra — a multi-agent LangGraph system.

You are executing an implementation task assigned to you by your manager.

## Task
{task}

## Seam: implementation

## Instructions
- Write actual Python code files that implement the specification
- Use proper file paths and Python package structure
- Write runnable, production-quality code with docstrings
- Create __init__.py files where appropriate for modules

## Output Format
Write the actual code files. For each file, output:
```python
# filename: path/to/file.py
[full file content]
```

## Rules
- Write complete, runnable code — no placeholders or TODO comments
- Use type hints and docstrings on all public members
- Follow PEP 8 style guidelines
- Do NOT just describe code — write the actual files"""
        elif seam_id == "testing":
            return f"""You are a QA Engineer Worker in a Recursive Research Orchestra — a multi-agent LangGraph system.

You are executing a testing task assigned to you by your manager.

## Task
{task}

## Seam: testing

## Instructions
- Write pytest unit tests for the implementation
- Cover core functionality with positive and negative test cases
- Use proper test fixtures and assertions

## Output Format
Write test files with:
```python
# filename: tests/test_module.py
[full test code]
```

## Rules
- Write actual test functions with assertions
- Use pytest conventions (test_ prefix, clear naming)
- Include both happy-path and edge-case tests"""
        elif seam_id == "delivery":
            return f"""You are a DevOps/Documentation Worker in a Recursive Research Orchestra — a multi-agent LangGraph system.

You are executing a delivery task assigned to you by your manager.

## Task
{task}

## Seam: delivery

## Instructions
- Write README.md with setup, usage, and examples
- Add configuration files (requirements.txt, pyproject.toml, .env.example)
- Ensure the project is ready to run

## Output Format
Write documentation and config files:
```markdown
# filename: README.md
[full documentation]
```

## Rules
- Write clear, complete documentation
- Include installation steps and usage examples
- Specify all dependencies and environment variables"""
        else:
            return f"""You are a WorkerNode in a Recursive Research Orchestra.

Task: {task}
Seam: {seam_id} (build mode)

Return your work product in structured format."""

    # Default research/research-like mode
    return f"""You are a WorkerNode in a Recursive Research Orchestra — a multi-agent LangGraph system.

You are executing a task assigned to you by your manager.

## Task
{task}

## Seam
{seam_id}

IMPORTANT: Return your findings in this exact format — do not deviate:
## Finding
[Your key finding in 1-2 sentences]
## Evidence
[Specific evidence: paper titles, URLs, data points, or "No substantive findings"]
## Relevance
[How this relates to the original goal]

## Rules
- Use at MOST 3 tool calls. Stop when you have enough evidence.
- If you cannot find concrete evidence after 2 tool calls, say "No substantive findings" and move on.
- Do NOT loop on research — produce structured output quickly.
- Focus on quality evidence over quantity."""


def _decompose_into_seams(goal: str, mode: str) -> List[Dict[str, Any]]:
    """Heuristic seam decomposition (used when LLM unavailable or as fallback)."""
    goal_lower = goal.lower()

    if mode == "research" or any(k in goal_lower for k in ["research", "survey", "study", "investigate"]):
        seams = [
            {
                "id": "literature",
                "description": "Literature survey and background research",
                "tasks": [
                    "Use WebSearch to find 3-5 recent papers on the topic published 2024-2025",
                    "Use WebSearch to identify 2-3 key researchers or institutions in this field",
                    "Find recent surveys or review articles that map the academic landscape",
                ],
            },
            {
                "id": "technical",
                "description": "Technical deep-dive on core concepts",
                "tasks": [
                    "Analyze the core techniques and methodologies from the literature found",
                    "Identify best practices and common patterns from the papers",
                    "Document trade-offs, limitations, and open problems mentioned in research",
                ],
            },
            {
                "id": "applications",
                "description": "Real-world applications and tools",
                "tasks": [
                    "Use WebSearch to find 3-5 popular tools, libraries, or frameworks in this area",
                    "Identify 2-3 industry applications or case studies",
                    "Compare features, pricing, or adoption of competing tools",
                ],
            },
            {
                "id": "synthesis",
                "description": "Synthesize findings into actionable insights",
                "tasks": [
                    "Compile key findings and rank by importance/relevance",
                    "Generate actionable recommendations based on evidence",
                    "Produce a structured final report with clear sections",
                ],
            },
        ]
    elif mode == "build" or any(k in goal_lower for k in ["build", "create", "implement", "design"]):
        seams = [
            {
                "id": "design",
                "description": "Design and architecture planning",
                "tasks": [
                    "Define requirements and constraints from the goal",
                    "Design system architecture with data models",
                    "Create technical specification in SPEC.md with endpoints, data structures, and file layout",
                    "Identify key modules and their responsibilities",
                ],
            },
            {
                "id": "implementation",
                "description": "Core implementation",
                "tasks": [
                    "Write the main application entry point (e.g., app.py or main.py)",
                    "Implement core business logic modules with classes/functions",
                    "Build API endpoints with proper routing and request handling",
                    "Create configuration and environment setup",
                ],
            },
            {
                "id": "testing",
                "description": "Testing and validation",
                "tasks": [
                    "Write pytest unit tests for core functions and classes",
                    "Create integration tests for API endpoints",
                    "Add tests for edge cases and error handling",
                ],
            },
            {
                "id": "delivery",
                "description": "Documentation and delivery",
                "tasks": [
                    "Write README.md with setup instructions and usage examples",
                    "Add docstrings to all public functions and classes",
                    "Create requirements.txt or pyproject.toml with dependencies",
                ],
            },
        ]
    else:
        seams = [
            {
                "id": "investigation",
                "description": "Initial investigation and research",
                "tasks": [
                    "Gather background information",
                    "Identify key questions",
                    "Map scope and constraints",
                ],
            },
            {
                "id": "analysis",
                "description": "Detailed analysis",
                "tasks": [
                    "Analyze gathered information",
                    "Identify patterns and insights",
                    "Evaluate options and trade-offs",
                ],
            },
            {
                "id": "synthesis",
                "description": "Synthesis and conclusions",
                "tasks": [
                    "Compile findings",
                    "Generate recommendations",
                    "Produce final output",
                ],
            },
        ]

    return seams


# ---------------------------------------------------------------------------
# manager_node_fn
# ---------------------------------------------------------------------------


def manager_node_fn(
    state: OrchestratorState,
    seam_id: str,
    manager_id: str,
) -> Dict[str, Any]:
    """
    ManagerNode: coordinates workers for a specific seam.

    The manager claims ownership of its seam, coordinates the workers spawned
    by the orchestrator, aggregates their results, and reports completion.
    """
    updates: Dict[str, Any] = {}

    _token_append(state, f"\n[MANAGER:{manager_id}] Taking ownership of seam '{seam_id}'\n")

    # Log to WorkLedger
    try:
        ledger = _make_ledger()
        ledger.log_event(
            run_id=state.run_id,
            event_type="manager_start",
            packet_id=seam_id,
            agent_id=manager_id,
            harness="langgraph",
            depth=1,
            data={"seam_id": seam_id, "manager_id": manager_id},
        )
    except Exception:
        pass

    # Get seam and manager state
    seam = state.seams.get(seam_id)
    manager_state = state.managers.get(manager_id)

    if not seam:
        _token_append(state, f"[MANAGER:{manager_id}] Error: seam '{seam_id}' not found\n")
        updates["status"] = "failed"
        return updates

    # Update to active
    seam.status = "active"
    if manager_state:
        manager_state.status = "active"

    if seam.description:
        _token_append(state, f"[MANAGER:{manager_id}] Seam description: {seam.description}\n")

    # Coordinate workers
    if manager_state and manager_state.spawned_worker_ids:
        worker_ids = manager_state.spawned_worker_ids
        _token_append(state, f"[MANAGER:{manager_id}] Coordinating {len(worker_ids)} worker(s)\n")

        # Aggregate results from workers (stored in shared state.findings)
        results = {}
        for wid in worker_ids:
            if wid in state.findings:
                results[wid] = state.findings[wid]

        if results:
            manager_state.worker_results = results
            manager_state.aggregated_result = f"Aggregated from {len(results)} workers"
            _token_append(state, f"[MANAGER:{manager_id}] Aggregated {len(results)} worker result(s)\n")

    # Mark as completed
    if manager_state:
        manager_state.status = "completed"

    seam.status = "completed"
    seam.result = manager_state.aggregated_result if manager_state else None

    if manager_id not in state.completed_agents:
        state.active_agents.append(manager_id)
        state.completed_agents.append(manager_id)

    # Log completion
    try:
        ledger = _make_ledger()
        ledger.log_event(
            run_id=state.run_id,
            event_type="manager_complete",
            packet_id=seam_id,
            agent_id=manager_id,
            harness="langgraph",
            depth=1,
            data={"seam_id": seam_id, "worker_count": len(worker_ids) if manager_state else 0},
        )
    except Exception:
        pass

    updates["seams"] = dict(state.seams)
    updates["managers"] = dict(state.managers)
    updates["status"] = "manager_done"

    return updates


# ---------------------------------------------------------------------------
# worker_node_fn
# ---------------------------------------------------------------------------


def worker_node_fn(
    state: OrchestratorState,
    worker_id: str,
    parent_manager_id: str,
    seam_id: str,
    task_description: str,
    llm_with_tools: Any,
    budget: TokenBudget | None = None,
    mode: str = "research",
) -> Dict[str, Any]:
    """
    WorkerNode: executes an atomic task and reports result.

    Workers have focused tasks and use scoped tools. They report results
    back to the parent manager via shared state (state.findings).

    Budget enforcement: if a ``budget`` is supplied and ``budget.can_proceed()``
    returns False, the LLM call is skipped and a simulation result is returned
    instead. This prevents any single runaway worker from exhausting the entire
    run's token allocation.
    """
    updates: Dict[str, Any] = {}

    _token_append(state, f"\n[WORKER:{worker_id}] Starting: {task_description[:50]}...\n")

    # Log to WorkLedger
    try:
        ledger = _make_ledger()
        ledger.log_event(
            run_id=state.run_id,
            event_type="worker_start",
            packet_id=worker_id,
            agent_id=worker_id,
            harness="langgraph",
            depth=2,
            data={"task": task_description, "seam_id": seam_id},
        )
    except Exception:
        pass

    # Execute the task using MiniMax M2.7 with worker tools
    result_text = ""
    tools_used = []
    confidence = 0.0

    # Respect per-seam budget when available
    budget_ok = budget.can_proceed() if budget else True
    budget_info = ""
    if budget:
        budget_info = f" | budget: {budget.spent_tokens}/{budget.max_tokens} tokens, {budget.turn_count}/{budget.max_turns} turns"

    if llm_with_tools and budget_ok:
        try:
            # Build a prompt for the worker task
            worker_prompt = _build_worker_prompt(task_description, seam_id, mode)
            response = llm_with_tools.invoke([{"role": "user", "content": worker_prompt}])
            result_text = getattr(response, "content", "") or str(response)

            # Estimate token usage from response length as a rough approximation.
            # MiniMax API response doesn't surface per-call token counts in the
            # ChatResult, so we record a conservative estimate per turn.
            # In production, parse response.usage from the raw API response.
            est_input = len(worker_prompt) // 4  # ~1 token per 4 chars
            est_output = len(result_text) // 4
            if budget:
                budget.record_turn(est_input, est_output)

            tools_used = ["MiniMaxDirectClient"]
            confidence = 0.9
            _token_append(state, f"[WORKER:{worker_id}] LLM returned {len(result_text)} chars{budget_info}\n")
        except Exception as exc:
            _token_append(state, f"[WORKER:{worker_id}] LLM error ({exc}), falling back\n")
            result_text = f"Executed task: {task_description}\nError: {exc}"
            confidence = 0.3
    elif budget and not budget_ok:
        _token_append(state, f"[WORKER:{worker_id}] Budget exhausted — using simulation{budget_info}\n")
        result_text = f"Executed task: {task_description}\nNote: Budget exhausted, returned partial result."
        tools_used = ["simulated"]
        confidence = 0.6
    else:
        # No LLM available and no budget to check
        import time
        time.sleep(0.05)
        result_text = f"[SIMULATED] Worker {worker_id} completed task: {task_description}"
        tools_used = ["simulated"]
        confidence = 0.85

    # Create worker state
    worker_state = WorkerState(
        worker_id=worker_id,
        parent_manager_id=parent_manager_id,
        seam_id=seam_id,
        task_description=task_description,
        status="completed",
        result=result_text,
        tools_used=tools_used,
        artifacts=[],
        confidence=confidence,
    )

    # Store findings in shared state
    state.findings[worker_id] = {
        "task": task_description,
        "result": result_text,
        "seam_id": seam_id,
        "confidence": confidence,
    }

    if worker_id not in state.completed_agents:
        state.active_agents.append(worker_id)
        state.completed_agents.append(worker_id)

    # Log completion
    try:
        ledger = _make_ledger()
        ledger.log_event(
            run_id=state.run_id,
            event_type="worker_complete",
            packet_id=worker_id,
            agent_id=worker_id,
            harness="langgraph",
            depth=2,
            data={"task": task_description, "result_preview": result_text[:50]},
        )
    except Exception:
        pass

    _token_append(state, f"[WORKER:{worker_id}] Done.\n")

    updates["findings"] = dict(state.findings)
    updates["status"] = "worker_done"

    return updates


# ---------------------------------------------------------------------------
# synthesis_node_fn
# ---------------------------------------------------------------------------


def synthesis_node_fn(
    state: OrchestratorState,
    llm_with_tools: Any,
) -> Dict[str, Any]:
    """
    SynthesisNode: compiles all findings into final output.

    The final pass that collects all seam results, aggregates worker findings,
    and produces a coherent structured report.
    """
    updates: Dict[str, Any] = {}

    _token_append(state, "\n[SYNTHESIS] Compiling final output from all seams...\n")

    # Log to WorkLedger
    try:
        ledger = _make_ledger()
        ledger.log_event(
            run_id=state.run_id,
            event_type="synthesis_start",
            packet_id=None,
            agent_id="synthesis",
            harness="langgraph",
            depth=0,
            data={"seam_count": len(state.seams), "findings_count": len(state.findings)},
        )
    except Exception:
        pass

    # Collect all seam results
    seam_results = {}
    for seam_id, seam in state.seams.items():
        seam_results[seam_id] = {
            "description": seam.description,
            "status": seam.status,
            "result": seam.result,
            "findings": seam.findings,
            "worker_ids": seam.worker_ids,
        }

    # Collect worker results
    worker_results = {}
    for worker_id, finding in state.findings.items():
        if worker_id.startswith("worker_"):
            worker_results[worker_id] = finding

    # Build final output
    if llm_with_tools:
        try:
            prompt = _build_synthesis_prompt(state.goal, seam_results, worker_results)
            response = llm_with_tools.invoke([{"role": "user", "content": prompt}])
            final_output = getattr(response, "content", "") or str(response)
            _token_append(state, f"[SYNTHESIS] Generated {len(final_output)} char output\n")
        except Exception as exc:
            _token_append(state, f"[SYNTHESIS] LLM unavailable ({exc}), using heuristic synthesis\n")
            final_output = None
    else:
        final_output = None

    # Fallback synthesis
    if final_output is None:
        final_output = _heuristic_synthesis(state.goal, seam_results, worker_results)

    state.final_output = final_output
    state.confidence = 0.9

    # Log completion
    try:
        ledger = _make_ledger()
        ledger.log_event(
            run_id=state.run_id,
            event_type="synthesis_complete",
            packet_id=None,
            agent_id="synthesis",
            harness="langgraph",
            depth=0,
            data={"output_length": len(final_output), "seam_count": len(state.seams)},
        )
        ledger.log_event(
            run_id=state.run_id,
            event_type=RUN_COMPLETED,
            packet_id=None,
            agent_id="orchestrator",
            harness="langgraph",
            depth=0,
            data={"final_output_length": len(final_output)},
        )
    except Exception:
        pass

    updates["final_output"] = final_output
    updates["confidence"] = 0.9
    updates["status"] = "completed"

    _token_append(state, "\n[SYNTHESIS] Done. Final output ready.\n")

    return updates


def _build_synthesis_prompt(
    goal: str,
    seam_results: Dict[str, Any],
    worker_results: Dict[str, Any],
) -> str:
    """Build the prompt for synthesis LLM calls."""
    seam_text = "\n".join([
        f"## {sid}\nDescription: {s.get('description', '')}\nStatus: {s.get('status', '')}\nResult: {s.get('result', '')}"
        for sid, s in seam_results.items()
    ])

    worker_text = "\n".join([
        f"### {wid}\nTask: {w.get('task', '')}\nResult: {w.get('result', '')}"
        for wid, w in worker_results.items()
    ])

    return f"""You are the SynthesisNode for a Recursive Research Orchestra.

Goal: {goal}

## Seam Results
{seam_text}

## Worker Findings
{worker_text}

## Your Task
Synthesize all findings into a coherent, well-structured final report.

## Format Requirements
- Start with a brief executive summary (2-3 sentences)
- Use hierarchical headers (## for major sections, ### for subsections)
- Include specific evidence, citations, and data points where available
- Highlight actionable recommendations
- End with a "Key Takeaways" section (3-5 bullet points)

## Quality Standards
- Do not just summarize — synthesize into new understanding
- Connect findings across seams to reveal patterns or contradictions
- Flag any areas where worker findings were incomplete or contradictory
- If a seam has weak results, acknowledge it honestly and note what would be needed"""


def _heuristic_synthesis(
    goal: str,
    seam_results: Dict[str, Any],
    worker_results: Dict[str, Any],
) -> str:
    """Heuristic synthesis when LLM is unavailable."""
    lines = [f"# Research Report: {goal}\n"]
    lines.append(f"\nThis report synthesizes findings from {len(seam_results)} work seams.\n")

    for seam_id, seam in seam_results.items():
        lines.append(f"\n## {seam_id.title()}: {seam.get('description', '')}\n")
        lines.append(f"**Status:** {seam.get('status', 'unknown')}\n")
        if seam.get('result'):
            lines.append(f"**Result:** {seam.get('result')}\n")

    lines.append(f"\n## Worker Findings\n")
    for wid, wres in worker_results.items():
        lines.append(f"- **{wid}**: {wres.get('task', '')} — {str(wres.get('result', ''))[:100]}\n")

    lines.append(f"\n---\n*Synthesized by Recursive Research Orchestra*")

    return "".join(lines)


# ---------------------------------------------------------------------------
# router_node_fn
# ---------------------------------------------------------------------------


def router_node_fn(state: OrchestratorState) -> Dict[str, Any]:
    """
    RouterNode: directs flow based on current state (conditional routing).

    Routes to:
    - human_gate if needs review
    - orchestrator if not complete
    - first incomplete manager
    - synthesis if managers done but output not done
    - END if all done
    """
    updates: Dict[str, Any] = {}

    _token_append(state, f"[ROUTER] Evaluating routing conditions...\n")

    # Check for human review gate
    if state.needs_human_review:
        updates["next_node"] = "human_gate"
        _token_append(state, "[ROUTER] Routing to human_gate (needs review)\n")
        return updates

    # Check orchestration progress
    if "orchestrator" not in state.completed_agents:
        updates["next_node"] = "orchestrator"
        _token_append(state, "[ROUTER] Routing to orchestrator\n")
        return updates

    # Check incomplete managers
    incomplete = [
        mid for mid, mstate in state.managers.items()
        if mstate.status not in ("completed", "failed")
    ]
    if incomplete:
        updates["next_node"] = incomplete[0]
        _token_append(state, f"[ROUTER] Routing to manager: {incomplete[0]}\n")
        return updates

    # Check synthesis
    if state.final_output is None:
        updates["next_node"] = "synthesis"
        _token_append(state, "[ROUTER] Routing to synthesis\n")
        return updates

    # All done
    updates["next_node"] = "END"
    _token_append(state, "[ROUTER] All done — routing to END\n")

    return updates


# ---------------------------------------------------------------------------
# human_gate_node_fn
# ---------------------------------------------------------------------------


def human_gate_node_fn(state: OrchestratorState) -> Dict[str, Any]:
    """
    HumanGateNode: suspends execution, waits for CLI approval.

    In a real system, this would use LangGraph's interrupt mechanism.
    Here it simply records the gate and continues based on human_approved.
    """
    updates: Dict[str, Any] = {}

    gate_name = state.human_gate_name or "default"

    _token_append(state, f"\n[HUMAN_GATE] Execution paused at gate: {gate_name}\n")
    _token_append(state, "[HUMAN_GATE] Waiting for approval via CLI...\n")

    # Log to WorkLedger
    try:
        ledger = _make_ledger()
        ledger.log_event(
            run_id=state.run_id,
            event_type="HUMAN_GATE_ENTERED",
            packet_id=gate_name,
            agent_id="human_gate",
            harness="langgraph",
            depth=0,
            data={"gate_name": gate_name},
        )
    except Exception:
        pass

    # Check if already resolved
    if state.human_approved is not None:
        approved = state.human_approved
        _token_append(state, f"[HUMAN_GATE] Decision received: {'APPROVED' if approved else 'DENIED'}\n")

        try:
            ledger = _make_ledger()
            event_type = "HUMAN_GATE_APPROVED" if approved else "HUMAN_GATE_DENIED"
            ledger.log_event(
                run_id=state.run_id,
                event_type=event_type,
                packet_id=gate_name,
                agent_id="human",
                harness="cli",
                depth=0,
                data={"gate_name": gate_name, "approved": approved},
            )
        except Exception:
            pass

        if approved:
            state.needs_human_review = False
            state.human_approved = None
        else:
            state.confidence = 0.0
            updates["confidence"] = 0.0

        updates["needs_human_review"] = state.needs_human_review
        updates["human_approved"] = state.human_approved
        updates["status"] = "gate_resolved"
        return updates

    # Not resolved — stay paused
    updates["needs_human_review"] = True
    updates["human_gate_name"] = gate_name

    _token_append(state, "[HUMAN_GATE] Awaiting external resolution...\n")

    return updates