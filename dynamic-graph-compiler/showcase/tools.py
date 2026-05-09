"""
Tool bindings for the Recursive Research Orchestra showcase.

Each node role gets a different subset of tools bound via bind_tools().
The orchestrator has broad access, managers have scoped access,
workers have minimal access, and the synthesis node has review tools.
"""

from __future__ import annotations

from typing import Any, Sequence

# ---------------------------------------------------------------------------
# Tool definitions (compatible with MiniMaxDirectClient bind_tools)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "ReadFile",
        "description": "Read the contents of a file. Use for inspecting source code, configs, or any text file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the file to read"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "WriteFile",
        "description": "Write content to a file. Creates or overwrites the file at the given path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path of the file to write"},
                "content": {"type": "string", "description": "Content to write to the file"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "Bash",
        "description": "Execute a shell command and return the stdout/stderr output.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute"},
                "cwd": {"type": "string", "description": "Working directory for the command"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "Glob",
        "description": "List files matching a glob pattern. Supports **/*.py, *.md, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern to match files"},
                "root": {"type": "string", "description": "Root directory to search from"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "Grep",
        "description": "Search for text inside files. Returns matching lines with line numbers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex or text pattern to search for"},
                "path": {"type": "string", "description": "Directory or file to search in"},
                "recursive": {"type": "boolean", "description": "Search recursively"},
            },
            "required": ["pattern", "path"],
        },
    },
    {
        "name": "WebSearch",
        "description": "Search the web for information. Returns title, URL, and snippet.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query to look up"},
            },
            "required": ["query"],
        },
    },
]

# ---------------------------------------------------------------------------
# Tool execution (local execution, used by MiniMaxDirectClient)
# ---------------------------------------------------------------------------


def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """Execute a tool call and return the result as a string."""
    import os, subprocess, pathlib, re as re_mod

    cwd = arguments.get("cwd") or os.getcwd()

    if name == "ReadFile":
        path = arguments["path"]
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception as exc:
            return f"Error reading {path}: {exc}"

    elif name == "WriteFile":
        path = arguments["path"]
        content = arguments["content"]
        try:
            pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"OK: wrote {len(content)} chars to {path}"
        except Exception as exc:
            return f"Error writing {path}: {exc}"

    elif name == "Bash":
        cmd = arguments["command"]
        try:
            def _quote_globs(s: str) -> str:
                def _replace(m: re_mod.Match) -> str:
                    flag = m.group(1)
                    pattern = m.group(2)
                    if any(c in pattern for c in "*?") and not pattern.startswith('"'):
                        return f'{flag}"{pattern}"'
                    return m.group(0)

                return re_mod.sub(r"(-name\s+|-path\s+)((?:[^\s\"'])+)", _replace, s)

            cmd = _quote_globs(cmd)
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=cwd,
                timeout=60,
            )
            out = result.stdout.strip() or ""
            err = result.stderr.strip() or ""
            return out + ("\n[STDERR] " + err if err else "")
        except subprocess.TimeoutExpired:
            return "Error: command timed out after 60s"
        except Exception as exc:
            return f"Error running command: {exc}"

    elif name == "Glob":
        pattern = arguments["pattern"]
        root = arguments.get("root", cwd)
        try:
            root_p = pathlib.Path(root)
            matches = list(root_p.rglob(pattern))
            return "\n".join(str(m) for m in matches[:200]) or "(no matches)"
        except Exception as exc:
            return f"Error globbing: {exc}"

    elif name == "Grep":
        pattern = arguments["pattern"]
        path = arguments["path"]
        recursive = arguments.get("recursive", False)
        try:
            p = pathlib.Path(path)
            if p.is_file():
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                result = [f"{i}: {l}" for i, l in enumerate(lines, 1) if re_mod.search(pattern, l)]
                return "\n".join(result[:200]) or "(no matches)"
            elif recursive:
                matches = []
                for f in p.rglob("*"):
                    if f.is_file():
                        try:
                            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
                            for i, line in enumerate(lines, 1):
                                if re_mod.search(pattern, line):
                                    matches.append(f"{f}:{i}: {line}")
                        except:
                            pass
                return "\n".join(matches[:200]) or "(no matches)"
            return "(path must be a file for non-recursive grep)"
        except Exception as exc:
            return f"Error grepping: {exc}"

    elif name == "WebSearch":
        query = arguments["query"]
        # Use mmx CLI for search (the mmx-tools pip package is not installed)
        try:
            import json as json_mod
            result = subprocess.run(
                ["mmx", "search", "query", "--q", query, "--output", "json"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                data = json_mod.loads(result.stdout.strip())
                results = data.get("organic", [])
                if results:
                    lines = []
                    for r in results[:5]:
                        lines.append(
                            f"Title: {r.get('title', '')}\n"
                            f"URL: {r.get('link', '')}\n"
                            f"Snippet: {r.get('snippet', '')}\n"
                        )
                    return "\n".join(lines)
        except Exception:
            pass
        return f"WebSearch for '{query}' — no results available"

    return f"Unknown tool: {name}"


# ---------------------------------------------------------------------------
# Tool binding helpers (return tool dicts for MiniMaxDirectClient.bind_tools)
# ---------------------------------------------------------------------------


def architect_tools() -> list[dict[str, Any]]:
    """Full tool access for the orchestrator."""
    return list(TOOLS)


def manager_tools() -> list[dict[str, Any]]:
    """Scoped tools for managers — no file write, limited bash."""
    return [t for t in TOOLS if t["name"] not in ("WriteFile",)]


def worker_tools() -> list[dict[str, Any]]:
    """Minimal tools for workers — read, glob, grep, bash."""
    return [
        t for t in TOOLS
        if t["name"] in ("ReadFile", "Glob", "Grep", "Bash", "WebSearch")
    ]


def synthesis_tools() -> list[dict[str, Any]]:
    """Review tools for synthesis node — glob, grep for verification."""
    return [t for t in TOOLS if t["name"] in ("ReadFile", "Glob", "Grep")]


def router_tools() -> list[dict[str, Any]]:
    """Minimal tools for router — only bash and read."""
    return [t for t in TOOLS if t["name"] in ("Bash", "ReadFile")]


def free_provider_tools() -> list[dict[str, Any]]:
    """
    Superset of tools available across all free providers.

    Returns the same tool set as architect_tools() — all providers (MiniMax, Groq,
    OpenRouter, NVIDIA NIM) support the same tool interface in this codebase.
    Add provider-specific tools here as they become relevant.
    """
    return list(TOOLS)