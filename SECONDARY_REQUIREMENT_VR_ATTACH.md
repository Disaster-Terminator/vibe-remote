# Secondary Requirement: Vibe Remote Attach to Existing Local Codex Threads

## Scope

This document captures the secondary requirement discussed in the main planning thread and intentionally excludes the primary requirement about turning Codex into a HermesAgent/OpenClaw-style general agent runtime.

Secondary requirement only:

- Let Vibe Remote attach to an already-existing local Codex thread.
- That existing Codex thread is **not** originally created or managed by Vibe Remote.

This worktree exists so another agent can continue this requirement independently.

## Problem Statement

Current separation is not just a UX annoyance. There are three distinct state layers:

1. Local Codex App / CLI thread
2. Vibe Remote IM thread / agent session
3. Workspace / repo / worktree execution context

Vibe Remote today already supports native session resume flows, but its product model is still fundamentally `Thread = Session`, and Codex runtime ownership is still tightly coupled to its own app-server process and local state.

The secondary requirement is therefore **not** “add a new chat session feature.” It is:

> allow Vibe Remote to discover, attach to, and continue a pre-existing Codex thread that originated outside Vibe Remote.

## What We Confirmed

### 1. This is technically feasible

Official/public Codex capabilities are sufficient to support this in principle:

- `thread/list`
- `thread/read`
- `thread/resume`
- `thread/fork`
- `turn/start`

Codex also publicly supports:

- resumable threads
- app-server mode
- remote client connections
- CLI resume flows

So this is not blocked by a missing primitive.

### 2. The right substrate is Codex App Server / SDK, not CLI scraping

Low-end fallback:

- `codex resume`
- `codex exec resume <SESSION_ID>`

But long-term attach should use structured control surfaces:

- Codex App Server
- TypeScript SDK

Reason:

- structured thread lifecycle
- streamed events
- approval handling
- less fragile than parsing CLI output

### 3. Vibe Remote already has reusable plumbing

Useful existing pieces in this repo:

- chat/session routing
- cross-platform resume UI
- per-channel routing
- persisted session mappings
- Codex app-server runtime code already exists

Important current files identified during repo study:

- `core/controller.py`
- `core/handlers/message_handler.py`
- `core/handlers/session_handler.py`
- `modules/sessions_facade.py`
- `config/v2_sessions.py`
- `modules/agents/native_sessions/`
- `modules/agents/codex/agent.py`
- `modules/agents/codex/transport.py`
- `modules/agents/codex/session.py`
- `modules/agents/codex/event_handler.py`

### 4. The hard coupling is inside Codex backend ownership

Current repo shape strongly suggests:

- one Codex transport is owned per cwd
- one Vibe session maps onto one Codex thread id
- Codex live execution is still owned by a cwd-bound app-server transport

So the seam to target is **transport/thread ownership**, not the IM layer.

## Key Architectural Judgment

The correct goal is **attach and bridge**, not “abolish isolation.”

More specifically:

- do **not** try to make one worker thread hot-swap arbitrary workspaces as a primary semantic
- do **not** assume a Desktop app’s currently active internal thread can be safely multi-master controlled
- do **not** treat Vibe Remote thread isolation as the only problem

Instead:

- let Vibe Remote discover existing Codex threads
- let the user select one to attach/resume/fork
- bind an IM conversation to a chosen Codex thread
- continue the thread through official Codex thread/turn interfaces

## Recommended Product Semantics

### Supported

1. **Attach idle or resumable local Codex thread**
2. **Read thread summary / state before attach**
3. **Resume thread from Vibe Remote**
4. **Fork thread into Vibe-managed continuation when needed**
5. **Bind one IM thread to one chosen Codex thread**

### Not a safe first-class guarantee

1. **Two independent controllers writing to the same active thread concurrently**
2. **Treating a single Codex worker thread as a free-floating multi-workspace general session**
3. **Assuming desktop-internal live session takeover is always safe**

## Recommended Architecture for This Secondary Requirement

### Preferred direction

Add a Codex attach/control layer around App Server semantics:

- `list_threads(cwd?)`
- `read_thread(thread_id)`
- `resume_thread(thread_id)`
- `fork_thread(thread_id)`
- `send_turn(thread_id, input)`

Then connect that layer to existing Vibe Remote resume / modal / routing flows.

### Suggested commands / UX

- `/threads`
- `/attach <thread-id>`
- `/attach latest`
- `/fork`
- `/status`

Likely flow:

1. User invokes attach command from an IM thread
2. Vibe Remote lists matching Codex threads, optionally filtered by cwd
3. User selects thread
4. Vibe Remote reads metadata / state
5. Vibe Remote resumes or forks the thread
6. Session binding is persisted for that IM thread

## Implementation Direction Suggested Earlier

Potential module shape:

```text
modules/agents/codex_appserver/
  client.py
  backend.py
  events.py
  approvals.py
  session_store.py
```

This does **not** have to replace the current Codex backend immediately, but it is the clearest place to isolate attach/resume semantics from the existing cwd-owned transport model.

## Validation / Feasibility Notes

Repo validation baseline already exists:

- pytest config and backend tests
- Codex runtime tests
- Docker E2E harness
- manual regression flow across Slack / Discord / Feishu / WeChat

But this secondary requirement currently lacks dedicated test coverage for:

- attach to externally-created Codex thread
- remote thread ownership transfer/binding semantics
- multi-controller safety constraints
- approval bridging during attach/resume flows

So another agent picking this up should plan new tests around those cases.

## Final Recommendation

For this secondary requirement:

- **Reuse Vibe Remote’s channel layer**
- **Use Codex App Server / SDK as the attach substrate**
- **Do not rely on CLI scraping as the primary long-term interface**
- **Do not model success as “breaking Codex isolation”**
- **Model success as “bridging an external Codex thread into Vibe Remote safely”**

## Explicit Non-Goal

This document does **not** attempt to answer the primary requirement:

> whether Codex itself can be turned into a HermesAgent/OpenClaw-style general agent runtime

That discussion remains in the main planning thread and should be handled separately.
