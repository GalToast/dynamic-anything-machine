# MERGE-ASSESSMENT — CodexSandboxOffline → local

**Date:** 2026-10-02 · **Source:** `C:\Users\CodexSandboxOffline\.codex\.sandbox\cwd\ed78e86044a3e0ef` (54 GB)

## Verdict: there is almost nothing to merge

The sandbox is a **mirror of your live workspace**, not an archive of unique work. Every
non-repo directory tested has an identical live counterpart:

| dir | sandbox files | live files | verdict |
|---|---|---|---|
| `.claude` | 1,480 | 1,480 | MIRROR |
| `.opencode` | 1,764 | 1,764 | MIRROR |
| `harness` | 10,183 | 10,183 | MIRROR |
| `dynamic-graph-compiler` | 728 | 728 | MIRROR |
| `employment` | 567 | 567 | MIRROR |
| `.gemini` | 451 | 451 | MIRROR |
| `clients` / `artifacts` / `conroe-api-exploration` / `harness-bench` / `js` / … | — | identical | MIRROR |
| `ai-models` | 3,314 | live twin exists | MIRROR |
| `archive` | 5,813 | 5,813 | MIRROR |

Plus the repos: `mrt2` sandbox HEAD `9fddfba` is **identical** to live `[SHOP]/mrt2`.
`semantic-explorer`, `chaos-universe-3d`, `introduction-to-github` were clean (0 unbacked).

## What was actually recovered

`C:\Users\HP\repos\langgraph-harness` — commit `f0d9c6b`, 15 tracked files.

- LangGraph proof-of-concept: supervisor → research/build team leads → workers, nested delegation
- Deterministic demo, runs without an API key; optional MiniMax/OpenAI-compatible config via `.env`
- **27 files / 134 KB** of authored source, recovered from the sandbox
- `.venv` (3,235 files) excluded via `.gitignore`; only `.env.example` committed, no secrets

Its own README names its live path (`Desktop\Temp while my comp is at the shop\ai-models\langgraph-harness`),
so a copy already existed. **The recovery is belt-and-braces, not rescue.**

## My corrections — I inflated three separate numbers

| claim I made | reality |
|---|---|
| "54 GB of unique, at-risk content" | mirrors of live dirs |
| "mrt2's 153 commits exist only in the sandbox" | identical HEAD to live `[SHOP]/mrt2` |
| **"2,858 unprotected source files"** | **18 source files + a `.venv`; live copy already existed** |

The `.venv` was the worst: `ai-models/langgraph-harness` reported 3,261 files, and **3,235 of them were
`site-packages`**. I counted a virtualenv as precious source.

Root cause: I never tested whether a sandbox directory was a mirror before counting it, and I used
total file count as a proxy for "source code". Both are wrong on this machine.

## What is genuinely unique (and already secured)

| asset | status |
|---|---|
| 11 verified git bundles, 2,218 MB (`C:\tmp\rescue\`) | all pass `git bundle verify` |
| top-level 44 uncommitted entries | committed `92652da9` |
| working state in 4 nested repos | committed before bundling |
| 6 documents + `recovered-stash-20260525.patch` | copied to `C:\tmp\rescue\DOCS-EXTRACTED\` |

**The genuinely irreplaceable find was `docs/music-project-ownership.md`** (2026-09-28) — the
ownership map for the whole music product — plus `docs/recovered-stash-20260525.patch`, the only
surviving copy of `stash@{0}` (32 files).

## Recommendation

1. **Do not delete the sandbox yet** — it is the parent of your live `[SHOP]` workspace, not a
   separate copy. Deleting `CodexSandboxOffline` frees little and risks the live tree.
2. `langgraph-harness` is a **2026-05-07 demo**. Keep the recovered copy if the pattern interests
   you; otherwise it is 134 KB of dead weight.
3. The bundles are your insurance. Keep `C:\tmp\rescue` until you are satisfied nothing else was missed.

**Nothing was pushed to any remote. No existing local repo was modified.**