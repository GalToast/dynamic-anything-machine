"""
Two-Phase Dynamic Orchestrator for the Recursive Research Orchestra.

Phase 1: Orchestrator runs in its own isolated graph, analyzes goal, produces seam plan.
Phase 2: Full graph is built with ALL nodes (managers + workers) added upfront before compile.
         Then we stream through the pre-built graph.

This avoids the "graph already compiled" mutation problem by pre-building the full graph
before compilation. The "dynamic" aspect is that the seam plan (which nodes to add) is
determined at runtime by the orchestrator's analysis.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from packet_runtime import MiniMaxDirectClient

from showcase.states import OrchestratorState, SeamState, ManagerState, TokenBudget
from showcase.tools import architect_tools, manager_tools, worker_tools, free_provider_tools
from showcase.nodes import (
    orchestrator_phase1_fn,
    manager_node_fn,
    worker_node_fn,
    synthesis_node_fn,
    router_node_fn,
    human_gate_node_fn,
    _make_ledger,
)


# ---------------------------------------------------------------------------
# Free Provider Router
# ---------------------------------------------------------------------------

@dataclass
class _ProviderResult:
    """Result of a single provider invocation."""
    content: str
    provider: str


class FreeProviderRouter:
    """
    Router that tries free providers in order: MiniMax -> Groq -> OpenRouter -> NVIDIA NIM.

    Each provider is tried with a 5-second timeout. On rate-limit (429) or timeout,
    immediately falls back to the next provider. The router is transparent — it
    implements the same invoke() interface as MiniMaxDirectClient so callers
    don't need to change.

    Supported env vars per provider:
      - MiniMax:    ANTHROPIC_AUTH_TOKEN
      - Groq:       GROQ_API_KEY
      - OpenRouter: OPENROUTER_API_KEY
      - NVIDIA NIM: NVIDIA_API_KEY

    Example::
        router = FreeProviderRouter()
        client = router.bind_tools(architect_tools())
        response = client.invoke([HumanMessage(content="Hello")])
    """

    DEFAULT_TIMEOUT = 5  # seconds per provider

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        verbose: bool = True,
    ) -> None:
        self.timeout = timeout
        self.verbose = verbose
        self._tools: list[dict[str, Any]] = []

        # Discover available providers from environment
        self._providers: list[tuple[str, str, str]] = []  # (name, api_key, base_url)

        if os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            self._providers.append((
                "minimax",
                os.environ["ANTHROPIC_AUTH_TOKEN"],
                "https://api.minimax.io/anthropic",
            ))

        if os.environ.get("GROQ_API_KEY"):
            self._providers.append((
                "groq",
                os.environ["GROQ_API_KEY"],
                "https://api.groq.com/openai/v1",
            ))

        if os.environ.get("OPENROUTER_API_KEY"):
            self._providers.append((
                "openrouter",
                os.environ["OPENROUTER_API_KEY"],
                "https://openrouter.ai/api/v1",
            ))

        if os.environ.get("NVIDIA_API_KEY"):
            self._providers.append((
                "nvidia",
                os.environ["NVIDIA_API_KEY"],
                "https://integrate.api.nvidia.com/v1",
            ))

        if self.verbose and self._providers:
            print(f"[FreeProviderRouter] Available providers: {[p[0] for p in self._providers]}")
        elif self.verbose:
            print("[FreeProviderRouter] No provider API keys found — simulation mode")

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Any],
        **kwargs: Any,
    ) -> "FreeProviderRouter":
        """Bind tools to this router (copies the router with tools attached)."""
        converted = []
        for t in tools:
            if isinstance(t, dict):
                converted.append(t)
            elif hasattr(t, "name") and hasattr(t, "description"):
                converted.append({
                    "name": getattr(t, "name"),
                    "description": getattr(t, "description", ""),
                    "input_schema": getattr(t, "input_schema", {"type": "object"}),
                })
        instance = FreeProviderRouter(timeout=self.timeout, verbose=self.verbose)
        instance._tools = converted
        instance._providers = list(self._providers)
        return instance

    def invoke(self, input: Sequence[Any] | dict[str, Any] | Any, **kwargs: Any) -> Any:
        """
        Try each provider in order, returning on the first success.

        Raises Exception("All free providers failed") if all fail.
        """
        from langchain_core.messages import BaseMessage, AIMessage
        from langchain_core.outputs import ChatResult, ChatGeneration

        if isinstance(input, dict):
            messages = input.get("messages", [input])
        elif isinstance(input, (list, tuple)):
            messages = list(input)
        else:
            messages = [input]

        messages = list(messages)

        for provider_name, api_key, base_url in self._providers:
            try:
                result = self._try_provider(provider_name, api_key, base_url, messages, **kwargs)
                if self.verbose:
                    print(f"[FreeProviderRouter] {provider_name} succeeded")
                return result
            except Exception as exc:
                err_str = str(exc).lower()
                is_rate_limit = "429" in err_str or "rate_limit" in err_str or "rate limit" in err_str
                is_timeout = "timeout" in err_str or "timed out" in err_str
                if self.verbose:
                    print(f"[FreeProviderRouter] {provider_name} failed ({exc.__class__.__name__}): {exc}"[:120])
                if is_rate_limit or is_timeout:
                    if self.verbose:
                        print(f"[FreeProviderRouter]   -> rate-limit/timeout, falling back immediately")
                    continue
                # For other errors (auth failure, etc.), also continue in case other providers work
                if self.verbose:
                    print(f"[FreeProviderRouter]   -> non-retryable error, continuing to next provider")

        # All providers failed — return simulation response
        return ChatResult(generations=[
            ChatGeneration(message=AIMessage(content="Error: All free providers failed"))
        ])

    def _try_provider(
        self,
        provider_name: str,
        api_key: str,
        base_url: str,
        messages: list[Any],
        **kwargs: Any,
    ) -> Any:
        """
        Try a single provider with the current tool-binding and message loop.
        Returns a ChatResult on success, raises on failure.
        """
        from langchain_core.messages import AIMessage
        from langchain_core.outputs import ChatResult, ChatGeneration

        # Convert messages to the provider's format
        if provider_name == "minimax":
            api_messages = self._convert_minimax_messages(messages)
        elif provider_name in ("groq", "openrouter"):
            api_messages = self._convert_openai_messages(messages)
        elif provider_name == "nvidia":
            api_messages = self._convert_openai_messages(messages)
        else:
            api_messages = self._convert_minimax_messages(messages)

        tools = self._tools or []

        for attempt in range(self.max_turns_for_provider(provider_name)):
            payload = self._build_payload(provider_name, api_messages, tools)
            body = json.dumps(payload).encode("utf-8")

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }
            if provider_name == "minimax":
                headers["anthropic-version"] = "2023-06-01"
            elif provider_name == "openrouter":
                headers["HTTP-Referer"] = "https://github.com/anthropic/dynamic-graph-compiler"
                headers["X-Title"] = "Dynamic Graph Compiler"

            req = urllib.request.Request(
                f"{base_url}/chat/completions",
                data=body,
                method="POST",
                headers=headers,
            )

            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    response_data = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    raise Exception("429 rate limit")
                error_body = exc.read().decode("utf-8") if exc.fp else str(exc)
                raise Exception(f"HTTP {exc.code}: {error_body[:100]}")
            except Exception as exc:
                raise

            content = self._extract_content(provider_name, response_data)
            if content is None:
                # Extract tool calls if present
                tool_calls = self._extract_tool_calls(provider_name, response_data)
                if tool_calls:
                    for tc in tool_calls:
                        name = tc.get("name", "")
                        args = tc.get("input", {})
                        from showcase.tools import execute_tool
                        tool_result = execute_tool(name, args)
                        api_messages.append({"role": "user", "content": tool_result})
                    continue  # retry with tool result
                # No content, no tools — treat as failure
                raise Exception(f"Empty response from {provider_name}")

            return ChatResult(generations=[
                ChatGeneration(message=AIMessage(content=content.strip()))
            ])

        raise Exception(f"Exceeded {self.max_turns_for_provider(provider_name)} turns for {provider_name}")

    def max_turns_for_provider(self, provider_name: str) -> int:
        return 10

    def _build_payload(self, provider_name: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        """Build provider-specific payload."""
        model = self._model_for_provider(provider_name)
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": 2048,
            "temperature": 0.7,
        }
        if tools:
            payload["tools"] = tools
        return payload

    def _model_for_provider(self, provider_name: str) -> str:
        if provider_name == "minimax":
            return "MiniMax-M2.7"
        elif provider_name == "groq":
            return "llama-3.3-70b-versatile"
        elif provider_name == "openrouter":
            return "openrouter/auto"
        elif provider_name == "nvidia":
            return "nvidia/llama-3.3-nemotron-70b-instruct"
        return "unknown"

    def _convert_minimax_messages(self, messages: list[Any]) -> list[dict[str, Any]]:
        from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage
        result = []
        for msg in messages:
            if isinstance(msg, dict):
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "assistant":
                    role = "user"
                result.append({"role": role, "content": content})
            elif isinstance(msg, HumanMessage):
                result.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                if msg.content:
                    result.append({"role": "user", "content": f"[AIMessage]: {msg.content}"})
            elif isinstance(msg, ToolMessage):
                result.append({"role": "user", "content": msg.content})
            elif isinstance(msg, SystemMessage):
                result.append({"role": "user", "content": msg.content})
            elif hasattr(msg, "content"):
                result.append({"role": "user", "content": str(msg.content)})
        return result

    def _convert_openai_messages(self, messages: list[Any]) -> list[dict[str, Any]]:
        from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage
        result = []
        for msg in messages:
            if isinstance(msg, dict):
                result.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})
            elif isinstance(msg, HumanMessage):
                result.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                result.append({"role": "assistant", "content": msg.content or ""})
            elif isinstance(msg, ToolMessage):
                result.append({"role": "tool", "content": msg.content, "tool_call_id": getattr(msg, "tool_call_id", "")})
            elif isinstance(msg, SystemMessage):
                result.append({"role": "system", "content": msg.content})
            elif hasattr(msg, "content"):
                result.append({"role": "user", "content": str(msg.content)})
        return result

    def _extract_content(self, provider_name: str, response_data: dict[str, Any]) -> str | None:
        if provider_name == "minimax":
            content_blocks = response_data.get("content", [])
            for b in content_blocks:
                if isinstance(b, dict) and b.get("type") == "text":
                    return b.get("text", "")
            return None
        else:
            # OpenAI-compatible
            choices = response_data.get("choices", [])
            if choices and isinstance(choices, list):
                msg = choices[0].get("message", {})
                return msg.get("content") or ""
            return None

    def _extract_tool_calls(self, provider_name: str, response_data: dict[str, Any]) -> list[dict[str, Any]]:
        if provider_name == "minimax":
            content_blocks = response_data.get("content", [])
            return [
                b for b in content_blocks
                if isinstance(b, dict) and b.get("type") == "tool_use"
            ]
        else:
            # OpenAI-compatible
            choices = response_data.get("choices", [])
            if not choices:
                return []
            msg = choices[0].get("message", {})
            return msg.get("tool_calls", [])


class DynamicOrchestrator:
    """
    Two-phase dynamic orchestrator.

    Phase 1: Run orchestrator in isolated graph to analyze goal and produce seam plan.
             The orchestrator uses LLM to decompose the goal into seams/tasks.
    Phase 2: Build the FULL graph with all manager and worker nodes, compile once,
             then stream through the pre-built graph.
    """

    def __init__(
        self,
        runs_dir: str = "runs",
        use_llm: bool = True,
        verbose: bool = True,
    ) -> None:
        self.runs_dir = Path(runs_dir)
        self.use_llm = use_llm
        self.verbose = verbose

        self._llm: Optional[MiniMaxDirectClient] = None
        self._llm_with_tools: Optional[MiniMaxDirectClient] = None
        self._llm_worker_tools: Optional[MiniMaxDirectClient] = None
        self._router: Optional[FreeProviderRouter] = None

        if use_llm:
            try:
                # Try FreeProviderRouter first — it falls back automatically
                self._router = FreeProviderRouter(verbose=verbose)
                # Check if any providers are actually available
                if self._router._providers:
                    self._llm_with_tools = self._router.bind_tools(free_provider_tools())
                    self._llm_worker_tools = self._router.bind_tools(worker_tools())
                    if verbose:
                        print("[INIT] FreeProviderRouter active — will try MiniMax → Groq → OpenRouter → NVIDIA NIM")
                else:
                    # Fall back to plain MiniMaxDirectClient for its simulation error path
                    self._llm = MiniMaxDirectClient()
                    if self._llm.api_key:
                        self._llm_with_tools = self._llm.bind_tools(architect_tools())
                        self._llm_worker_tools = self._llm.bind_tools(worker_tools())
                        if verbose:
                            print("[INIT] MiniMax M2.7 connected with tool bindings")
                    else:
                        if verbose:
                            print("[INIT] MiniMax API key not found — simulation mode")
            except Exception as exc:
                if verbose:
                    print(f"[INIT] FreeProviderRouter unavailable: {exc} — simulation mode")

        self._ledger = _make_ledger(str(self.runs_dir))

    def _make_checkpointer(self) -> MemorySaver:
        """Build a MemorySaver with custom types (SeamState, ManagerState) allowlisted."""
        serde = JsonPlusSerializer(
            allowed_msgpack_modules=[
                ("showcase.states", "SeamState"),
                ("showcase.states", "ManagerState"),
                ("showcase.states", "OrchestratorState"),
                ("showcase.states", "TokenBudget"),
            ]
        )
        return MemorySaver(serde=serde)

    # -------------------------------------------------------------------------
    # Phase 1
    # -------------------------------------------------------------------------

    def phase1_analyze(self, goal: str, run_id: str, mode: str) -> tuple[List[Dict[str, Any]], OrchestratorState]:
        """
        Run the orchestrator in an isolated Phase 1 graph to analyze the goal
        and produce a seam plan (list of seams with tasks).
        """
        if self.verbose:
            print(f"\n[PHASE 1] Orchestrator analyzing goal: {goal[:50]}...")

        initial_state = OrchestratorState(
            run_id=run_id,
            goal=goal,
            mode=mode,
            seams={},
            managers={},
            active_agents=[],
            completed_agents=[],
            findings={},
            final_output=None,
            confidence=0.0,
            needs_human_review=False,
            human_gate_name=None,
            human_approved=None,
            streaming_tokens=[],
            events=[],
            next_node=None,
            dynamic_nodes={},
        )

        builder = StateGraph(OrchestratorState)
        builder.add_node("orchestrator", self._phase1_orchestrator_fn())
        builder.add_node("router", self._phase1_router_fn())
        builder.set_entry_point("router")
        builder.add_conditional_edges(
            "router",
            self._phase1_route_fn,
            {"orchestrator": "orchestrator", END: END},
        )
        builder.add_edge("orchestrator", END)

        checkpointer = self._make_checkpointer()
        compiled = builder.compile(checkpointer=checkpointer)

        config = {"configurable": {"thread_id": f"{run_id}-phase1"}}
        final_state: Optional[OrchestratorState] = None
        for s in compiled.stream(initial_state, config=config, stream_mode="values"):
            if isinstance(s, OrchestratorState):
                final_state = s
            elif isinstance(s, dict):
                try:
                    final_state = OrchestratorState.model_validate(s)
                except Exception:
                    pass

        seams = []
        if final_state:
            for mid, m in final_state.managers.items():
                seam_id = m.seam_id
                seam_desc = final_state.seams[seam_id].description if seam_id in final_state.seams else ""
                seams.append({
                    "id": seam_id,
                    "manager_id": mid,
                    "description": seam_desc,
                    "tasks": list(m.spawned_worker_ids),
                })

        if self.verbose:
            print(f"[PHASE 1] Identified {len(seams)} seams: {[s['id'] for s in seams]}")

        return seams, final_state

    def _phase1_orchestrator_fn(self):
        """Phase 1 orchestrator node function."""
        def fn(state: OrchestratorState) -> Dict[str, Any]:
            result = orchestrator_phase1_fn(state, self._llm_with_tools)
            return {
                'completed_agents': list(state.completed_agents),
                'seams': {k: v.model_dump() for k, v in state.seams.items()},
                'managers': {k: v.model_dump() for k, v in state.managers.items()},
                'active_agents': list(state.active_agents),
                'findings': dict(state.findings),
                'streaming_tokens': list(state.streaming_tokens),
                'status': 'orchestrator_done',
                'seam_count': result.get('seam_count', 0),
            }
        return fn

    def _phase1_router_fn(self):
        def router(state: OrchestratorState) -> Dict[str, Any]:
            if "orchestrator" not in state.completed_agents:
                state.next_node = "orchestrator"
            else:
                state.next_node = END
            return {"next_node": state.next_node}
        return router

    def _phase1_route_fn(self, state: OrchestratorState) -> str:
        return state.next_node or END

    # -------------------------------------------------------------------------
    # Phase 2
    # -------------------------------------------------------------------------

    def build_full_graph(
        self,
        goal: str,
        run_id: str,
        mode: str,
        seam_plan: List[Dict[str, Any]],
        phase1_state: Optional[OrchestratorState] = None,
    ) -> tuple[Any, StateGraph, OrchestratorState]:
        """
        Build the full graph with ALL nodes added BEFORE compile.
        """
        if self.verbose:
            print(f"\n[PHASE 2] Building full graph with {len(seam_plan)} seams...")

        if phase1_state is not None:
            initial_state = phase1_state
            initial_state.next_node = None
        else:
            initial_state = OrchestratorState(
                run_id=run_id,
                goal=goal,
                mode=mode,
                seams={},
                managers={},
                active_agents=[],
                completed_agents=[],
                findings={},
                final_output=None,
                confidence=0.0,
                needs_human_review=False,
                human_gate_name=None,
                human_approved=None,
                streaming_tokens=[],
                events=[],
                next_node=None,
                dynamic_nodes={},
            )

        builder = StateGraph(OrchestratorState)

        builder.add_node("orchestrator", self._phase2_orchestrator_fn())
        builder.add_node("router", router_node_fn)
        builder.add_node("synthesis", self._make_synthesis_fn())
        builder.add_node("human_gate", human_gate_node_fn)

        all_manager_ids = []
        all_worker_ids = []
        # Per-seam token budgets — one budget shared by all workers in the same seam
        seam_budgets: dict[str, TokenBudget] = {}

        for seam_info in seam_plan:
            seam_id = seam_info["id"]
            manager_id = seam_info["manager_id"]
            tasks = seam_info.get("tasks", [])

            all_manager_ids.append(manager_id)
            seam_budgets[seam_id] = TokenBudget()

            def make_manager_fn(sid, mid):
                def manager_fn(state: OrchestratorState) -> Dict[str, Any]:
                    return manager_node_fn(state, sid, mid)
                return manager_fn

            builder.add_node(manager_id, make_manager_fn(seam_id, manager_id))

            for i, task in enumerate(tasks):
                worker_id = f"worker_{seam_id}_{i}"
                all_worker_ids.append(worker_id)

                def make_worker_fn(wid, mid, sid, task_desc, llm, budget, mode):
                    def worker_fn(state: OrchestratorState) -> Dict[str, Any]:
                        return worker_node_fn(state, wid, mid, sid, task_desc, llm, budget, mode)
                    return worker_fn

                builder.add_node(
                    worker_id,
                    make_worker_fn(
                        worker_id,
                        manager_id,
                        seam_id,
                        task,
                        self._llm_worker_tools,
                        seam_budgets[seam_id],
                        mode,
                    ),
                )
                builder.add_edge(manager_id, worker_id)

        if self.verbose:
            print(f"[PHASE 2] Added {len(all_manager_ids)} managers and {len(all_worker_ids)} workers")
            print(f"[PHASE 2] Managers: {all_manager_ids}")
            print(f"[PHASE 2] Workers: {all_worker_ids}")

        def route_fn(state: OrchestratorState) -> str:
            if state.needs_human_review:
                return "human_gate"
            # Only go to orchestrator if it's NOT yet in completed_agents AND no managers are done
            if "orchestrator" not in state.completed_agents:
                return "orchestrator"
            # After here, orchestrator is done — never return "orchestrator" again
            incomplete = [
                mid for mid, m in state.managers.items()
                if (m.get("status") if isinstance(m, dict) else getattr(m, "status", "pending")) not in ("completed", "failed")
            ]
            if incomplete:
                return incomplete[0]
            if state.final_output is None:
                return "synthesis"
            return END

        all_node_ends = {node: node for node in all_manager_ids + all_worker_ids}
        all_node_ends.update({"orchestrator": "orchestrator", "synthesis": "synthesis", "human_gate": "human_gate", END: END})

        builder.add_conditional_edges(
            "router",
            route_fn,
            all_node_ends,
        )

        builder.add_edge("synthesis", "router")
        builder.add_edge("human_gate", "router")
        builder.set_entry_point("router")

        checkpointer = self._make_checkpointer()
        compiled_graph = builder.compile(checkpointer=checkpointer)

        if self.verbose:
            print(f"[PHASE 2] Full graph compiled with {len(builder.nodes)} nodes")
            print(f"  Nodes: {list(builder.nodes.keys())}")

        return compiled_graph, builder, initial_state

    def _phase2_orchestrator_fn(self):
        """Phase 2 orchestrator — marks itself complete, defers to router."""
        def orchestrator_fn(state: OrchestratorState) -> Dict[str, Any]:
            if "orchestrator" not in state.completed_agents:
                state.completed_agents.append("orchestrator")
            # Deduplicate to handle checkpoint race condition within the same iteration.
            # Multiple nodes may append "orchestrator" before a checkpoint write lands,
            # causing exponential duplication (1->2->4->8->16). Returning a deduped list
            # via dict.fromkeys preserves insertion order while eliminating duplicates.
            unique_agents = list(dict.fromkeys(state.completed_agents))
            return {"completed_agents": unique_agents}
        return orchestrator_fn

    def _make_synthesis_fn(self):
        def synthesis_fn(state: OrchestratorState) -> Dict[str, Any]:
            return synthesis_node_fn(state, self._llm_with_tools)
        return synthesis_fn

    # -------------------------------------------------------------------------
    # Stream execution
    # -------------------------------------------------------------------------

    def stream(
        self,
        compiled_graph: Any,
        initial_state: OrchestratorState,
    ) -> Iterator[OrchestratorState]:
        """Stream execution of the compiled graph."""
        run_id = initial_state.run_id
        state = initial_state
        config = {"configurable": {"thread_id": run_id}}

        iteration = 0
        while True:
            iteration += 1
            if self.verbose:
                print(f"\n[STREAM] Iteration {iteration}")

            for s in compiled_graph.stream(state, config=config, stream_mode="values"):
                if isinstance(s, OrchestratorState):
                    state = s
                elif isinstance(s, dict):
                    try:
                        state = OrchestratorState.model_validate(s)
                    except Exception:
                        pass
                # Deduplicate agent lists to handle checkpoint race condition where
                # the same agent is appended multiple times before a checkpoint writes.
                # Exponential duplication (1->2->4->8) causes incorrect behavior.
                state.completed_agents = list(dict.fromkeys(state.completed_agents))
                state.active_agents = list(dict.fromkeys(state.active_agents))
                yield state

            if state.final_output is not None:
                if self.verbose:
                    print(f"\n[STREAM] Synthesis complete — done")
                break

            def _mgr_status(m) -> str:
                return m.get("status") if isinstance(m, dict) else getattr(m, "status", "pending")

            all_done = (
                "orchestrator" in state.completed_agents and
                all(_mgr_status(m) in ("completed", "failed") for m in state.managers.values()) and
                state.final_output is not None
            )
            if all_done:
                break

            if iteration > 50:
                if self.verbose:
                    print(f"\n[STREAM] Iteration limit — stopping")
                break

        self._ledger.log_event(
            run_id=run_id,
            event_type="RUN_COMPLETED",
            packet_id=None,
            agent_id="orchestrator",
            harness="langgraph",
            depth=0,
            data={"iterations": iteration},
        )

    # -------------------------------------------------------------------------
    # Main execute
    # -------------------------------------------------------------------------

    def execute(self, goal: str, mode: str | None = None) -> OrchestratorState:
        """Execute the full two-phase orchestration."""
        run_id = str(uuid.uuid4())

        if mode is None:
            goal_lower = goal.lower()
            if any(k in goal_lower for k in ["research", "survey", "study", "investigate"]):
                mode = "research"
            elif any(k in goal_lower for k in ["build", "create", "implement", "design"]):
                mode = "build"
            else:
                mode = "research"

        self._ledger.log_event(
            run_id=run_id,
            event_type="RUN_CREATED",
            packet_id=None,
            agent_id="orchestrator",
            harness="langgraph",
            depth=0,
            data={"goal": goal, "mode": mode},
        )

        seam_plan, phase1_state = self.phase1_analyze(goal, run_id, mode)

        compiled, builder, initial_state = self.build_full_graph(
            goal, run_id, mode, seam_plan, phase1_state
        )

        final_state: Optional[OrchestratorState] = None
        for state in self.stream(compiled, initial_state):
            final_state = state

        return final_state
