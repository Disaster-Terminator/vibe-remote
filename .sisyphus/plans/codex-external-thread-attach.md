# Codex External Thread Attach for Vibe Remote

## TL;DR
> **Summary**: Extend the existing native resume flow so Vibe Remote can safely discover, inspect, and bind an IM conversation to a pre-existing local Codex thread using Codex App Server primitives. Treat external attachment as a distinct persisted state, validate workspace identity before bind, and default ambiguous ownership to fork instead of in-place resume.
> **Deliverables**:
> - Codex app-server-backed discover/read/resume/fork attach flow
> - Persisted external-attachment metadata distinct from Vibe-created mappings
> - Cross-platform resume UX extension for inspect + explicit resume/fork
> - Focused unit/scenario/regression coverage for attach safety and continuity
> **Effort**: Large
> **Parallel**: YES - 3 waves
> **Critical Path**: 1 → 2 → 5 → 6 → 8 → 9

## Context
### Original Request
Implement the secondary requirement from `SECONDARY_REQUIREMENT_VR_ATTACH.md`: let Vibe Remote attach to an already-existing local Codex thread that was not originally created or managed by Vibe Remote.

### Interview Summary
- This is a real requirement; do not wait on upstream.
- Do not reinvent a new session system; reuse existing patterns where possible.
- Do not rely on CLI scraping as the long-term substrate.
- Before planning, verify official support, upstream activity, and whether reusable implementations already exist.

### Metis Review (gaps addressed)
- External attach is modeled as a **specialized binding source**, not as a new generic session system.
- The plan separates **Vibe-created mapping** from **external Codex attachment metadata**.
- Workspace validation uses stronger identity than raw cwd string.
- Ambiguous ownership defaults to **fork**, validation failures default to **no attach**.
- Approval, duplicate-attach, stale-thread, and moved-workspace edge cases are explicit scope.

## Work Objectives
### Core Objective
Allow a user to select an existing local Codex thread from the current workspace, inspect it, explicitly choose **Resume** or **Fork**, and continue it from the current IM conversation without breaking existing Vibe-created session behavior.

### Deliverables
- Codex attach service over existing app-server transport with `thread/list`, `thread/read`, `thread/resume`, `thread/fork`
- Persistent attachment metadata and workspace fingerprint validation
- Extended native resume flow and platform UX for Codex attach actions
- Duplicate-attach and approval-safety guardrails
- Unit + scenario + regression evidence

### Definition of Done (verifiable conditions with commands)
- `PYTHONPATH=. pytest tests/test_codex_attach_service.py tests/test_codex_agent.py tests/test_resume_session.py tests/test_native_session_providers.py`
- `PYTHONPATH=. pytest tests/scenarios/codex_attach/test_codex_attach_scenarios.py`
- `ruff check config/v2_sessions.py modules/sessions_facade.py core/handlers/session_handler.py core/handlers/command_handlers.py modules/agents/codex modules/agents/native_sessions tests`
- `./scripts/run_three_regression.sh` completes without reset flags and the service reports healthy for manual platform sanity follow-up

### Must Have
- App-server-backed attach flow; no CLI output parsing
- Inspect-before-bind behavior
- Explicit resume vs fork choice for external Codex threads
- External attachment metadata persisted separately from ordinary session mappings
- Duplicate-attach detection and deterministic rejection/fork policy
- Workspace validation using normalized realpath + repo-root-aware fingerprint

### Must NOT Have (guardrails, AI slop patterns, scope boundaries)
- No generic external-session framework for other backends
- No auto-resume of external Codex threads just because cwd matches
- No mutation of non-Codex resume behavior
- No assumption that external thread ownership is exclusive
- No dependence on `~/.codex/state_5.sqlite` as the source of truth for attach semantics after bind

## Verification Strategy
> ZERO HUMAN INTERVENTION - all verification is agent-executed.
- Test decision: tests-after + existing pytest/unittest/scenario harness
- QA policy: Every task includes agent-executed happy-path and failure-path verification
- Evidence: `.sisyphus/evidence/task-{N}-{slug}.{ext}`

## Execution Strategy
### Parallel Execution Waves
> Target: 5-8 tasks per wave. Shared dependencies are extracted into Wave 1.

Wave 1: persistence/state model, app-server attach service, native session catalog enrichment

Wave 2: session-binding flow, Codex runtime/guardrails, platform attach UX

Wave 3: unit/integration coverage, scenario harness coverage, regression harness validation

### Dependency Matrix (full, all tasks)
- 1 blocks 5, 6, 8, 9
- 2 blocks 3, 5, 6, 8, 9
- 3 blocks 7, 8
- 4 blocks 5, 7
- 5 blocks 7, 8, 9
- 6 blocks 8, 9
- 7 blocks 9
- 8 blocks 9
- 9 blocks Final Verification Wave

### Agent Dispatch Summary (wave → task count → categories)
- Wave 1 → 4 tasks → deep, unspecified-high
- Wave 2 → 3 tasks → deep, unspecified-high, quick
- Wave 3 → 2 tasks → unspecified-high, quick

## TODOs
> Implementation + Test = ONE task. Never separate.
> EVERY task MUST have: Agent Profile + Parallelization + QA Scenarios.

- [x] 1. Add persisted external-attachment state and workspace fingerprint model

  **What to do**: Extend `config/v2_sessions.py` and `modules/sessions_facade.py` with a Codex-specific external attachment record stored separately from ordinary `session_mappings`. The persisted record must be keyed by `session_key -> base_session_id` and include: `binding_origin` (`vibe_created` or `external_attached`), `codex_thread_id`, `attach_mode` (`resume` or `fork`), `workspace_realpath`, `workspace_repo_root`, `workspace_fingerprint`, `forked_from_thread_id`, `attached_at`, `last_validated_at`, and `validation_status`. Add facade helpers to upsert, fetch, clear, and reverse-lookup attachments by Codex thread ID so duplicate-attach checks do not need ad-hoc scans.
  **Must NOT do**: Do not redesign the entire session model around backend-native IDs. Do not overload existing `session_mappings` entries with opaque JSON blobs. Do not add any non-Codex external-backend abstraction in this task.

  **Recommended Agent Profile**:
  - Category: `deep` - Reason: persistence and state semantics affect all downstream safety guarantees.
  - Skills: `[]` - No specialized skill is required beyond careful repo-grounded implementation.
  - Omitted: [`git-master`] - No git operation is needed for implementation.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: [5, 6, 8, 9] | Blocked By: []

  **References** (executor has NO interview context - be exhaustive):
  - Pattern: `config/v2_sessions.py:71-81` - existing persisted `SessionState` shape and comment describing `session_mappings`
  - Pattern: `config/v2_sessions.py:154-168` - migration pattern for evolving persisted session state safely
  - Pattern: `modules/sessions_facade.py:31-52` - baseline mapping facade API style for set/get helpers
  - Pattern: `modules/sessions_facade.py:94-106` - base-session prefix matching used by existing cleanup logic
  - Pattern: `modules/sessions_facade.py:189-214` - scoped cleanup pattern for all mappings under one base session
  - API/Type: `core/handlers/session_handler.py:257-263` - current identity tuple is `base_session_id + working_path + composite_key`
  - External: `https://github.com/openclaw/openclaw/blob/bd3ad3436efde3ed2834c4d20ed80765f4b2cd9e/extensions/codex/src/app-server/thread-lifecycle.ts#L45-L70` - binding-file pattern showing persisted external thread ownership can remain separate from runtime thread state

  **Acceptance Criteria** (agent-executable only):
  - [ ] `pytest -q tests/test_codex_attach_state.py` passes and verifies create/read/update/delete of external attachment metadata without altering ordinary `session_mappings`
  - [ ] `pytest -q tests/test_codex_attach_state.py -k duplicate_lookup` proves reverse lookup by `codex_thread_id` returns all conflicting bindings for duplicate-attach prevention
  - [ ] `pytest -q tests/test_codex_attach_state.py -k migration` proves missing/new keys load safely from older session files

  **QA Scenarios** (MANDATORY - task incomplete without these):
  ```
  Scenario: Persist and reload external attachment metadata
    Tool: Bash
    Steps: Run `PYTHONPATH=. pytest -q tests/test_codex_attach_state.py::CodexAttachStateTests::test_round_trip_external_attachment`
    Expected: Test asserts persisted state contains codex thread id, workspace fingerprint, attach mode, and survives reload
    Evidence: .sisyphus/evidence/task-1-attach-state.txt

  Scenario: Duplicate attach lookup detects conflict
    Tool: Bash
    Steps: Run `PYTHONPATH=. pytest -q tests/test_codex_attach_state.py::CodexAttachStateTests::test_reverse_lookup_by_thread_id`
    Expected: Test returns the existing binding and blocks silent second attach to the same external thread
    Evidence: .sisyphus/evidence/task-1-attach-state-error.txt
  ```

  **Commit**: YES | Message: `feat(codex): persist external attachment metadata` | Files: `config/v2_sessions.py`, `modules/sessions_facade.py`, `tests/test_codex_attach_state.py`

- [x] 2. Implement Codex app-server attach service over existing transport

  **What to do**: Add a Codex-specific attach service/module that wraps the existing app-server transport with explicit methods for `list_threads`, `read_thread`, `resume_thread`, `fork_thread`, and `validate_workspace`. Use `thread/list` and `thread/read` for discovery/inspection only; use `thread/resume` as the actual attach primitive and `thread/fork` when policy requires branching. The service must normalize workspace identity using `realpath(cwd)` plus repo root when available, return structured validation results, and expose enough metadata for UI and session binding decisions.
  **Must NOT do**: Do not query SQLite as the authoritative attach API. Do not bypass the existing `CodexTransport` initialize/JSON-RPC contract. Do not collapse `read` and `resume` into one method.

  **Recommended Agent Profile**:
  - Category: `deep` - Reason: this is the core backend integration seam and carries API correctness risk.
  - Skills: `[]` - Existing transport already encapsulates app-server process lifecycle.
  - Omitted: [`react-best-practices`] - No React work is involved.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: [3, 5, 6, 8, 9] | Blocked By: []

  **References** (executor has NO interview context - be exhaustive):
  - Pattern: `modules/agents/codex/transport.py:18-23` - transport already speaks JSON-RPC to `codex app-server`
  - Pattern: `modules/agents/codex/transport.py:54-99` - initialize/initialized handshake and process startup contract
  - Pattern: `modules/agents/codex/agent.py:359-402` - transport reuse is cwd-scoped and should remain so
  - Pattern: `modules/agents/codex/agent.py:517-548` - current resume path only handles persisted `threadId`, useful baseline for wrapping `thread/resume`
  - External: `https://developers.openai.com/codex/app-server` - official app-server semantics
  - External: `https://github.com/openai/codex/blob/996aa23e4ce900468047ed3ec57d1e7271f8d6de/sdk/python/src/codex_app_server/client.py#L302-L329` - official Python low-level client exposes list/read/resume/fork methods to mirror
  - External: `https://github.com/openai/codex/blob/996aa23e4ce900468047ed3ec57d1e7271f8d6de/codex-rs/app-server/src/codex_message_processor.rs#L3879-L3906` - `thread/read` is snapshot inspection only
  - External: `https://github.com/openai/codex/blob/996aa23e4ce900468047ed3ec57d1e7271f8d6de/codex-rs/app-server/src/codex_message_processor.rs#L4406-L4418` - `thread/resume` performs live listener attach
  - External: `https://github.com/openai/codex/blob/996aa23e4ce900468047ed3ec57d1e7271f8d6de/codex-rs/app-server/src/codex_message_processor.rs#L4835-L5125` - `thread/fork` creates a new thread from an existing rollout

  **Acceptance Criteria** (agent-executable only):
  - [ ] `pytest -q tests/test_codex_attach_service.py` passes and verifies the service issues `thread/list`, `thread/read`, `thread/resume`, and `thread/fork` through the existing transport abstraction
  - [ ] `pytest -q tests/test_codex_attach_service.py -k workspace_validation` proves mismatched realpath/repo-root fingerprints fail validation
  - [ ] `pytest -q tests/test_codex_attach_service.py -k read_vs_resume` proves `read_thread` does not mutate bind state and `resume_thread` does

  **QA Scenarios**:
  ```
  Scenario: Inspect external thread without binding
    Tool: Bash
    Steps: Run `PYTHONPATH=. pytest -q tests/test_codex_attach_service.py::CodexAttachServiceTests::test_read_thread_is_preview_only`
    Expected: Test asserts no persisted mapping or attachment record is created by read-only inspection
    Evidence: .sisyphus/evidence/task-2-attach-service.txt

  Scenario: Workspace mismatch fails closed
    Tool: Bash
    Steps: Run `PYTHONPATH=. pytest -q tests/test_codex_attach_service.py::CodexAttachServiceTests::test_workspace_validation_rejects_realpath_mismatch`
    Expected: Validation result is rejected and resume is never attempted
    Evidence: .sisyphus/evidence/task-2-attach-service-error.txt
  ```

  **Commit**: YES | Message: `feat(codex): add app-server attach service` | Files: `modules/agents/codex/*`, `tests/test_codex_attach_service.py`

- [x] 3. Replace sqlite-only Codex native session listing with attach-aware catalog enrichment

  **What to do**: Keep the current native session catalog entry point, but change the Codex provider/service so attach candidates are enriched through the attach service instead of relying exclusively on `~/.codex/state_5.sqlite`. The provider should still tolerate missing app-server availability for previews, but when attach is possible it must return enough metadata for the UI to render: title/preview, thread id, validation status, workspace match, last activity, materialized-history availability, and allowed actions (`resume`, `fork`, or `inspect_only`).
  **Must NOT do**: Do not remove the existing `NativeResumeSession`-based service interface. Do not degrade non-Codex providers. Do not assume every listed thread can be resumed.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` - Reason: medium-complexity adapter work with compatibility constraints.
  - Skills: `[]` - Existing provider/service pattern is sufficient.
  - Omitted: [`office-hours`] - No product brainstorming is needed during execution.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: [7, 8] | Blocked By: [2]

  **References** (executor has NO interview context - be exhaustive):
  - Pattern: `modules/agents/native_sessions/codex.py:22-58` - current cwd-scoped sqlite listing behavior to preserve as a compatibility fallback
  - Pattern: `modules/agents/native_sessions/codex.py:60-87` - current preview hydration contract
  - Pattern: `core/handlers/command_handlers.py:716-814` - `/resume` still expects `self._list_recent_native_sessions(context, limit=...)`
  - Test: `tests/test_native_session_providers.py` - existing test anchor for provider/service behavior
  - External: `https://github.com/nikivdev/flow/blob/a747e741ae92c09071d0ae946ab48488adcff1ce/fish/scripts/codex-openai-session.ts#L141-L150` - simple cwd-scoped thread-list UX pattern
  - External: `https://github.com/pingdotgg/t3code/blob/9df3c640210fecccb58f7fbc735f81ca0ee011bd/apps/server/src/codexAppServerManager.ts#L780-L788` - separate read-thread preview path worth mirroring

  **Acceptance Criteria** (agent-executable only):
  - [ ] `pytest -q tests/test_native_session_providers.py -k codex_attach` passes and proves the provider emits allowed-action metadata for each Codex candidate
  - [ ] `pytest -q tests/test_native_session_providers.py -k fallback` proves sqlite fallback still works when attach service cannot connect
  - [ ] `pytest -q tests/test_native_session_providers.py -k materialized_history` proves non-materialized threads are marked `fork_only` or `inspect_only` according to policy

  **QA Scenarios**:
  ```
  Scenario: Provider returns attach-aware candidate metadata
    Tool: Bash
    Steps: Run `PYTHONPATH=. pytest -q tests/test_native_session_providers.py::NativeSessionProviderTests::test_codex_provider_emits_attach_actions`
    Expected: Returned item exposes thread id, workspace validation, preview, and allowed attach actions
    Evidence: .sisyphus/evidence/task-3-native-provider.txt

  Scenario: Missing app-server falls back without crashing resume list
    Tool: Bash
    Steps: Run `PYTHONPATH=. pytest -q tests/test_native_session_providers.py::NativeSessionProviderTests::test_codex_provider_falls_back_when_attach_service_unavailable`
    Expected: Listing still returns basic candidates and marks them as unavailable for direct attach
    Evidence: .sisyphus/evidence/task-3-native-provider-error.txt
  ```

  **Commit**: YES | Message: `feat(codex): enrich native session catalog for attach` | Files: `modules/agents/native_sessions/codex.py`, `modules/agents/native_sessions/service.py`, `tests/test_native_session_providers.py`

- [x] 4. Extend resume command flow to carry Codex attach actions without creating a new top-level command

  **What to do**: Keep `/resume` as the entrypoint, but extend command-layer payload generation so Codex candidates can open an inspect-first flow and carry an explicit user decision (`resume` or `fork`) into submission. Reuse the existing platform-specific resume modal/card entrypoints; do not introduce `/attach` in this iteration. The command layer must preserve existing behavior for Claude/OpenCode and only add Codex-specific attach metadata to session picker payloads.
  **Must NOT do**: Do not break WeChat’s fallback resume behavior. Do not force all backends through a Codex-specific action flow. Do not bind anything from the command layer; only collect and forward intent.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` - Reason: shared command-layer changes with multi-platform compatibility risk.
  - Skills: `[]` - Existing command handler patterns are sufficient.
  - Omitted: [`browse`] - No browser testing is needed at this layer.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: [5, 7] | Blocked By: []

  **References** (executor has NO interview context - be exhaustive):
  - Pattern: `core/handlers/command_handlers.py:716-814` - existing `/resume` dispatch path across Slack/Discord/Telegram/Lark/WeChat
  - Pattern: `tests/test_resume_session.py:113-172` - baseline resume behavior expectations for thread and DM flows
  - API/Type: `modules/agents/native_sessions/types.py` - current native session item contract that picker payloads depend on
  - Pattern: `modules/im/slack.py`, `modules/im/discord.py`, `modules/im/telegram.py`, `modules/im/feishu.py` - platform resume modal/card entry points already registered by command layer

  **Acceptance Criteria** (agent-executable only):
  - [ ] `pytest -q tests/test_resume_command_codex_attach.py` passes and proves `/resume` keeps existing backend behavior while emitting Codex attach action payloads
  - [ ] `pytest -q tests/test_resume_command_codex_attach.py -k wechat` proves WeChat fallback remains a message-based prompt and does not crash on Codex attach candidates
  - [ ] `pytest -q tests/test_resume_command_codex_attach.py -k explicit_action` proves Codex picker submissions carry `resume`/`fork` explicitly instead of inferring from thread metadata

  **QA Scenarios**:
  ```
  Scenario: /resume emits Codex attach payloads only for Codex candidates
    Tool: Bash
    Steps: Run `PYTHONPATH=. pytest -q tests/test_resume_command_codex_attach.py::ResumeCommandCodexAttachTests::test_resume_lists_codex_attach_actions_without_affecting_other_backends`
    Expected: Slack/Discord/Telegram/Lark payloads contain attach action metadata for Codex entries only
    Evidence: .sisyphus/evidence/task-4-resume-command.txt

  Scenario: WeChat fallback path stays non-interactive but safe
    Tool: Bash
    Steps: Run `PYTHONPATH=. pytest -q tests/test_resume_command_codex_attach.py::ResumeCommandCodexAttachTests::test_wechat_resume_handles_codex_attach_candidates`
    Expected: WeChat sends explanatory text and does not attempt unsupported modal interaction
    Evidence: .sisyphus/evidence/task-4-resume-command-error.txt
  ```

  **Commit**: YES | Message: `feat(resume): carry codex attach actions in resume flow` | Files: `core/handlers/command_handlers.py`, `tests/test_resume_command_codex_attach.py`

- [x] 5. Implement attach state machine in session binding and persist explicit attachment provenance

  **What to do**: Extend `SessionHandler.handle_resume_session_submission()` into a Codex-aware attach state machine: `discover/inspect result already chosen -> validate workspace -> choose resume/fork -> call attach service -> prepare backend -> persist ordinary session mapping + external attachment metadata -> mark IM thread active`. Preserve current routing updates and confirmation messages, but append attach provenance in the confirmation for Codex (e.g. “resumed external thread” vs “forked from external thread”). Bind is the only step that mutates persisted state.
  **Must NOT do**: Do not let `thread/read` mutate bindings. Do not skip validation. Do not write attachment metadata before `thread/resume` or `thread/fork` succeeds.

  **Recommended Agent Profile**:
  - Category: `deep` - Reason: this is the highest-risk orchestration seam across routing, persistence, and runtime prep.
  - Skills: `[]` - Existing session-handler patterns are the main reference.
  - Omitted: [`investigate`] - This is controlled feature work, not root-cause debugging.

  **Parallelization**: Can Parallel: NO | Wave 2 | Blocks: [7, 8, 9] | Blocked By: [1, 2, 4]

  **References** (executor has NO interview context - be exhaustive):
  - Pattern: `core/handlers/session_handler.py:257-263` - current base session/composite key derivation
  - Pattern: `core/handlers/session_handler.py:605-743` - current resume submission flow and where routing + mapping are persisted
  - Pattern: `modules/sessions_facade.py:31-41` - ordinary session mapping persists `base_session_id -> native session/thread id`
  - Pattern: `modules/sessions_facade.py:233-238` - thread activation bookkeeping after successful binding
  - Pattern: `modules/agents/codex/agent.py:231-264` - current resume preparation hook before binding
  - Test: `tests/test_resume_session.py:174-218` - current expected backend preparation contract
  - External: `https://github.com/openai/codex/blob/996aa23e4ce900468047ed3ec57d1e7271f8d6de/codex-rs/app-server/tests/suite/v2/thread_resume.rs#L122-L150` - unmaterialized threads fail resume
  - External: `https://github.com/openai/codex/blob/996aa23e4ce900468047ed3ec57d1e7271f8d6de/codex-rs/app-server/tests/suite/v2/thread_fork.rs#L53-L120` - fork returns a new thread with lineage

  **Acceptance Criteria** (agent-executable only):
  - [ ] `pytest -q tests/test_resume_session.py -k codex_external_attach` passes and proves successful resume writes both ordinary mapping and external attachment metadata
  - [ ] `pytest -q tests/test_resume_session.py -k fork_on_ambiguity` proves ambiguous ownership or duplicate attach forces `fork` instead of in-place resume
  - [ ] `pytest -q tests/test_resume_session.py -k validation_failure` proves failed workspace validation does not mutate routing, mapping, or active-thread state

  **QA Scenarios**:
  ```
  Scenario: External thread is resumed and bound to current IM conversation
    Tool: Bash
    Steps: Run `PYTHONPATH=. pytest -q tests/test_resume_session.py::ResumeSessionTests::test_codex_external_attach_resumes_and_persists_provenance`
    Expected: Test asserts routing update, ordinary session mapping, external attachment metadata, and thread activation all succeed together
    Evidence: .sisyphus/evidence/task-5-session-binding.txt

  Scenario: Validation failure aborts bind cleanly
    Tool: Bash
    Steps: Run `PYTHONPATH=. pytest -q tests/test_resume_session.py::ResumeSessionTests::test_codex_external_attach_rejects_workspace_mismatch`
    Expected: No mapping, no attachment metadata, and an error message is emitted
    Evidence: .sisyphus/evidence/task-5-session-binding-error.txt
  ```

  **Commit**: YES | Message: `feat(resume): bind codex external threads safely` | Files: `core/handlers/session_handler.py`, `tests/test_resume_session.py`

- [x] 6. Add Codex runtime guardrails for duplicate attach, approval handoff, and resumed-thread continuity

  **What to do**: Extend the Codex runtime layer so external attachments can be resumed/forked safely after bind. On attach, record lineage in `CodexSessionManager`, reject or fork duplicate live attachment to the same external thread according to policy, and ensure resumed threads preserve streaming/event handling just like Vibe-created ones. Add approval-handoff handling so pending approval requests on resumed external threads surface through the same IM interaction channel, with stale or conflicting approval state failing closed.
  **Must NOT do**: Do not allow silent multi-IM shared writers to the same external thread. Do not skip event-handler registration on resumed external threads. Do not assume approval state is fresh just because `thread/resume` succeeds.

  **Recommended Agent Profile**:
  - Category: `deep` - Reason: concurrency and event-routing mistakes here will create subtle cross-thread corruption.
  - Skills: `[]` - Existing Codex event/turn infrastructure is the right implementation base.
  - Omitted: [`qa`] - This task is implementation; QA belongs in later tasks.

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: [8, 9] | Blocked By: [1, 2]

  **References** (executor has NO interview context - be exhaustive):
  - Pattern: `modules/agents/codex/agent.py:21-27` - one transport per cwd, multiple sessions per transport
  - Pattern: `modules/agents/codex/agent.py:80-99` - current runtime bootstraps session key/cwd tracking before thread resolution
  - Pattern: `modules/agents/codex/agent.py:517-548` - current persisted-thread resume logic
  - Pattern: `modules/agents/codex/agent.py:550-589` - turn/start path after thread resolution
  - Pattern: `modules/agents/codex/agent.py:344-349` - transport eviction invalidates in-memory thread state while preserving persisted mapping
  - Pattern: `modules/agents/codex/session.py` - current `base_session_id -> threadId/session_key/cwd` ownership registry
  - Pattern: `modules/agents/codex/event_handler.py` - existing event streaming and pending-turn tracking must remain shared
  - External: `https://github.com/openai/codex/blob/996aa23e4ce900468047ed3ec57d1e7271f8d6de/codex-rs/app-server/src/codex_message_processor.rs#L4525-L4655` - reattaching to a running thread is a supported server-side path
  - External: `https://github.com/openai/codex/blob/996aa23e4ce900468047ed3ec57d1e7271f8d6de/codex-rs/app-server/tests/suite/v2/thread_name_websocket.rs#L47-L86` - shared-subscription semantics imply duplicate attach guardrails are needed client-side

  **Acceptance Criteria** (agent-executable only):
  - [ ] `pytest -q tests/test_codex_agent.py -k external_attach` passes and proves resumed external threads reuse event handling and can start a new turn after bind
  - [ ] `pytest -q tests/test_codex_agent.py -k duplicate_attach` proves duplicate live attach is rejected or forked by policy
  - [ ] `pytest -q tests/test_codex_agent.py -k approval_handoff` proves pending approval state on resumed external threads is surfaced or rejected deterministically

  **QA Scenarios**:
  ```
  Scenario: Resumed external thread continues with normal turn streaming
    Tool: Bash
    Steps: Run `PYTHONPATH=. pytest -q tests/test_codex_agent.py::CodexAgentAttachTests::test_external_resume_preserves_event_streaming`
    Expected: Event handler receives turn lifecycle events and the next turn starts on the resumed thread id
    Evidence: .sisyphus/evidence/task-6-codex-runtime.txt

  Scenario: Duplicate external attach is blocked or forked by policy
    Tool: Bash
    Steps: Run `PYTHONPATH=. pytest -q tests/test_codex_agent.py::CodexAgentAttachTests::test_duplicate_external_attach_forces_policy_decision`
    Expected: Second bind does not silently write to the same external thread
    Evidence: .sisyphus/evidence/task-6-codex-runtime-error.txt
  ```

  **Commit**: YES | Message: `feat(codex): guard external thread runtime attachment` | Files: `modules/agents/codex/agent.py`, `modules/agents/codex/session.py`, `modules/agents/codex/event_handler.py`, `tests/test_codex_agent.py`

- [x] 7. Update platform resume UIs to support inspect-first Codex attach actions

  **What to do**: Update the Slack modal, Discord picker, Telegram picker, and Feishu card resume flows so Codex entries can render richer attach metadata and present explicit actions. Required UX: user selects a Codex candidate, sees preview + validation + allowed actions, then chooses **Resume** or **Fork**; unsupported platforms must degrade gracefully to explanatory text instead of silent failure. Keep the UI model shared where possible and avoid divergent business logic in IM adapters.
  **Must NOT do**: Do not add a web UI. Do not hardcode Codex-only business rules independently in every IM adapter. Do not regress existing resume flows for non-Codex backends.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` - Reason: multi-adapter UI plumbing with moderate coordination cost.
  - Skills: `[]` - Existing modal/card patterns are the main reference.
  - Omitted: [`frontend-ui-ux`] - This is adapter payload work, not standalone frontend design.

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: [9] | Blocked By: [3, 4, 5]

  **References** (executor has NO interview context - be exhaustive):
  - Pattern: `core/handlers/command_handlers.py:724-809` - platform-specific modal/card openings already branched by platform
  - Pattern: `modules/im/slack.py` - Slack resume modal implementation
  - Pattern: `modules/im/discord.py` - Discord resume picker implementation
  - Pattern: `modules/im/telegram.py` - Telegram resume picker implementation
  - Pattern: `modules/im/feishu.py` - Feishu resume card implementation
  - Test: `tests/test_resume_session.py` - downstream session binding behavior must remain compatible with existing message-thread expectations

  **Acceptance Criteria** (agent-executable only):
  - [ ] `pytest -q tests/test_codex_attach_ui.py` passes and proves each supported IM adapter can render action-bearing Codex attach entries
  - [ ] `pytest -q tests/test_codex_attach_ui.py -k fallback` proves unsupported or degraded platforms emit deterministic fallback text instead of crashing
  - [ ] `pytest -q tests/test_codex_attach_ui.py -k non_codex_unchanged` proves Claude/OpenCode picker payloads are unchanged

  **QA Scenarios**:
  ```
  Scenario: Slack/Discord/Telegram/Feishu render explicit Resume and Fork actions for Codex candidates
    Tool: Bash
    Steps: Run `PYTHONPATH=. pytest -q tests/test_codex_attach_ui.py::CodexAttachUiTests::test_supported_platforms_render_codex_attach_actions`
    Expected: Action metadata is rendered for supported adapters and maps back to the command/session flow correctly
    Evidence: .sisyphus/evidence/task-7-attach-ui.txt

  Scenario: Non-Codex and fallback paths remain stable
    Tool: Bash
    Steps: Run `PYTHONPATH=. pytest -q tests/test_codex_attach_ui.py::CodexAttachUiTests::test_non_codex_entries_and_fallback_paths_remain_stable`
    Expected: No adapter crashes and no non-Codex behavior regression
    Evidence: .sisyphus/evidence/task-7-attach-ui-error.txt
  ```

  **Commit**: YES | Message: `feat(im): surface codex attach actions in resume pickers` | Files: `modules/im/*.py`, `tests/test_codex_attach_ui.py`

- [x] 8. Add focused unit and integration coverage for attach state, service, handler, and runtime semantics

  **What to do**: Add or extend focused tests so the feature is proven without relying on manual chat testing. Required suites: persisted attach state tests, attach service tests, session-handler attach tests, Codex runtime attach tests, native provider tests, and command/UI payload tests. Use fake or mocked app-server responses for list/read/resume/fork/approval flows. Cover both happy path and fail-closed behavior.
  **Must NOT do**: Do not leave attach behavior verified only by broad regression scripts. Do not add a brand-new test framework. Do not skip assertions on persisted attachment metadata.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` - Reason: broad but straightforward coverage work touching several existing test styles.
  - Skills: `[]` - Existing pytest/unittest patterns are enough.
  - Omitted: [`playwright`] - Browser automation is not the primary proof here.

  **Parallelization**: Can Parallel: NO | Wave 3 | Blocks: [9] | Blocked By: [1, 2, 3, 5, 6]

  **References** (executor has NO interview context - be exhaustive):
  - Test: `tests/test_resume_session.py:113-218` - current resume-flow unit style and backend-prepare assertions
  - Test: `tests/test_codex_agent.py` - existing Codex runtime tests to extend for external attach semantics
  - Test: `tests/test_native_session_providers.py` - provider/service verification anchor
  - Test: `tests/test_codex_session.py` - session manager state tests
  - Pattern: `pyproject.toml` - pytest configuration already in repo
  - External: `https://github.com/openai/codex/blob/996aa23e4ce900468047ed3ec57d1e7271f8d6de/codex-rs/app-server/tests/suite/v2/thread_resume.rs#L215-L255` - resume should restore persisted history semantics
  - External: `https://github.com/openai/codex/blob/996aa23e4ce900468047ed3ec57d1e7271f8d6de/codex-rs/app-server/tests/suite/v2/thread_fork.rs#L226-L260` - non-materialized threads cannot be forked directly

  **Acceptance Criteria** (agent-executable only):
  - [ ] `PYTHONPATH=. pytest tests/test_codex_attach_state.py tests/test_codex_attach_service.py tests/test_resume_command_codex_attach.py tests/test_codex_attach_ui.py` passes
  - [ ] `PYTHONPATH=. pytest tests/test_resume_session.py -k codex_external_attach tests/test_codex_agent.py -k external_attach tests/test_native_session_providers.py -k codex_attach` passes
  - [ ] Every new attach failure mode has one direct test: workspace mismatch, missing thread, non-materialized history, duplicate attach, pending approval conflict

  **QA Scenarios**:
  ```
  Scenario: Full attach unit suite passes
    Tool: Bash
    Steps: Run `PYTHONPATH=. pytest tests/test_codex_attach_state.py tests/test_codex_attach_service.py tests/test_resume_command_codex_attach.py tests/test_codex_attach_ui.py`
    Expected: All attach-focused unit and adapter suites pass with no skipped cases for core flows
    Evidence: .sisyphus/evidence/task-8-unit-suite.txt

  Scenario: Failure-path coverage is explicit and passing
    Tool: Bash
    Steps: Run `PYTHONPATH=. pytest tests/test_resume_session.py -k 'workspace_mismatch or fork_on_ambiguity' tests/test_codex_agent.py -k 'duplicate_attach or approval_handoff'`
    Expected: Each safety guardrail has an executable test and all pass
    Evidence: .sisyphus/evidence/task-8-unit-suite-error.txt
  ```

  **Commit**: YES | Message: `test(codex): cover external thread attach semantics` | Files: `tests/test_*.py`

- [x] 9. Add scenario-harness coverage and regression validation for cross-platform attach continuity

  **What to do**: Add a dedicated scenario-harness capability for Codex external attach so the behavior is validated end-to-end at the service boundary: discover candidate, inspect metadata, choose resume or fork, bind to IM conversation, send next turn, and assert it runs on the expected Codex thread. Then run the repo’s three-regression Docker harness to ensure cross-platform routing still starts cleanly after the feature lands. Record evidence files for both scenario tests and regression command output.
  **Must NOT do**: Do not rely solely on broad manual chat checks as proof of correctness. Do not use reset flags for regression. Do not broaden the scenario to non-Codex backends.

  **Recommended Agent Profile**:
  - Category: `quick` - Reason: this is mostly test/scenario wiring once core feature tasks are done.
  - Skills: `[]` - Existing scenario harness and regression script are the intended tools.
  - Omitted: [`ship`] - No PR/deploy work is needed.

  **Parallelization**: Can Parallel: NO | Wave 3 | Blocks: [F1, F2, F3, F4] | Blocked By: [1, 2, 5, 6, 7, 8]

  **References** (executor has NO interview context - be exhaustive):
  - Pattern: `tests/scenario_harness/README.md` - scenario harness structure to follow
  - Pattern: `tests/scenarios/message_delivery/test_message_delivery_scenarios.py` - service-boundary scenario style already used in repo
  - Pattern: `tests/scenarios/auth_setup/test_auth_setup_scenarios.py` - multi-step backend flow scenario style
  - Pattern: `scripts/run_three_regression.sh` - unified Slack/Discord/Feishu/WeChat regression harness
  - Test: `tests/test_prepare_three_regression.py` - regression config generation expectations

  **Acceptance Criteria** (agent-executable only):
  - [ ] `PYTHONPATH=. pytest tests/scenarios/codex_attach/test_codex_attach_scenarios.py` passes and verifies both resume and fork attach paths
  - [ ] `./scripts/run_three_regression.sh` runs without `--reset-config`, `--reset-all`, or `--no-build` and the resulting service status is healthy
  - [ ] Evidence files capture the bound thread id / forked thread id chosen by each scenario

  **QA Scenarios**:
  ```
  Scenario: Scenario harness proves resume and fork continuity
    Tool: Bash
    Steps: Run `PYTHONPATH=. pytest tests/scenarios/codex_attach/test_codex_attach_scenarios.py`
    Expected: One scenario proves in-place resume on a safe external thread; one proves fork on ambiguity; both assert the subsequent turn uses the expected thread id
    Evidence: .sisyphus/evidence/task-9-scenario-suite.txt

  Scenario: Regression harness still boots all supported IM transports cleanly
    Tool: Bash
    Steps: Run `./scripts/run_three_regression.sh`
    Expected: Container build/start completes successfully and health checks pass without wiping prior config/state
    Evidence: .sisyphus/evidence/task-9-three-regression.txt
  ```

  **Commit**: YES | Message: `test(regression): add codex attach scenario coverage` | Files: `tests/scenarios/codex_attach/*`, `tests/test_prepare_three_regression.py`

## Final Verification Wave (MANDATORY — after ALL implementation tasks)
> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.
> **Do NOT auto-proceed after verification. Wait for user's explicit approval before marking work complete.**
> **Never mark F1-F4 as checked before getting user's okay.** Rejection or user feedback -> fix -> re-run -> present again -> wait for okay.
- [ ] F1. Plan Compliance Audit — oracle
- [ ] F2. Code Quality Review — unspecified-high
- [ ] F3. Real Manual QA — unspecified-high (+ playwright if UI)
- [ ] F4. Scope Fidelity Check — deep

## Commit Strategy
- Commit 1: `feat(codex): add external thread attach state and service`
- Commit 2: `feat(resume): add codex attach flow and verification coverage`

## Success Criteria
- A current IM conversation can bind to an existing local Codex thread through inspect → explicit resume/fork → continue.
- Persisted state distinguishes Vibe-created bindings from external attachments.
- Unsafe or ambiguous cases fail closed or fork by policy.
- Existing Claude/OpenCode/native resume behavior remains unchanged.
