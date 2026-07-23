---
name: save-conversation
description: >
  Save the current visible human-agent session into durable Project Conversation files so a later Claude or Codex
  session can resume the work. Use when the user asks to save a conversation, checkpoint progress, preserve context,
  switch agents, end a session, hand off, or prepare for context compaction; when a lifecycle instruction requests an
  automatic save check; or, once that path is armed, at a durable milestone with context unavailable from
  authoritative project sources, before an intentional session pause, or when visible context is at risk. Automatic
  checks are silent and never save recoverable live state. They save unfinished discussion only when the session is
  intentionally pausing or visible context is at risk. Write only managed files under .scratch. Use
  resume-conversation to load.
---

# Save Conversation

Save the visible Agent Session at a safe breakpoint. Preserve what a later agent cannot recover from Git, project documentation, Fluent, or other live systems.

A Project Conversation is a named thread of project work that spans Agent Sessions; it is not a transcript or one client chat. One Agent Session may affect several Project Conversations, and one Project Conversation may link to zero, one, or several Fluent Work Items.

Use `resume-conversation` to load the current `.scratch` projection.

## Activation paths

Use the explicit path when the user asks to save, checkpoint, switch agents, or end the session. Publish one checkpoint even when the normalized Project Conversation did not change, report what was written, and stop after saving.

Use the automatic path when lifecycle context first requests a save check. That request arms later checks only for the same top-level interactive or orchestrating session.

Treat an automatic check as silent maintenance. Do not announce the check, explain the materiality decision, narrate its reads or writes, report validation or success, or explain a no-op. Only report a failure or conflict required by the guardrails.

After the first check, reassess at only these save conditions:

- The work crosses a durable milestone and the visible delta contains a controlling fact that cannot be reconstructed from Git, project documentation, Fluent, artifacts, or a live system. Examples include user direction, a decision and its rationale, a failed attempt or lesson, and evidence or side effects.
- The user explicitly ends, switches, or parks the session, or progress must stop for a named external event.
- Lifecycle context identifies imminent compaction or high context pressure that puts unsaved visible context at risk.

An ordinary end of turn, a question awaiting the user's next reply, and a cadence reminder are not save conditions. Cadence and turn hooks request an evaluation only. Do not initiate another check after each completed substep.

Do not run the automatic path inside a delegated subagent or Fluent worker. Their parent or orchestrating session owns Project Conversation continuity.

Before starting the save procedure on the automatic path, apply both gates from step 8 to the lifecycle cause and visible session. Stop the skill without inspecting saved conversations, Git, Fluent, artifacts, or live systems when either gate clearly fails. Inspect sources only when nonrecoverability is uncertain and a save condition exists. Do not gather evidence merely to justify a no-op.

## Requirements

The bundled scripts require Python 3.9 or newer. Git is required only when collecting Git state.

Write version 1 for every new Conversation Checkpoint, Current Conversation, and Conversation Index:

```text
.scratch/
├── CONVERSATIONS.md
├── <conversation-id>/
│   └── CONVERSATION.md
└── _conversations/
    └── sessions/
        └── <timestamp>-<agent>.md
```

## Guardrails

- Change only managed Project Conversation files while saving. Continue implementation afterward only on the automatic path and only when the current request already authorizes it. Do not edit project documentation, commit, push, deploy, or clean up work merely because a save check ran.
- Never modify a published Conversation Checkpoint. A new checkpoint becomes published after validation passes with its Current Conversation and index links in place and the recovery marker is removed. Correct it before that point; write another checkpoint afterward.
- Preserve existing notes and legacy records. Never rename, rewrite, or move `HANDOFFS.md`, `HANDOFF.md`, or `_handoff/sessions/`; read them only as legacy input.
- Treat an existing `CONVERSATION.md` or `CONVERSATIONS.md` without `managed_by: conversation-continuity` as user-owned. Never replace it in this skill. On the explicit path, report the exact path and ask the user to choose another conversation root or authorize a separate migration that preserves the original verbatim. On the automatic path, make no conversation-file changes.
- Do not expose secrets, credentials, private keys, raw tokens, or sensitive command output. Record a safe locator and the fact that access is required.
- Record observable decisions and stated rationale. Do not include hidden reasoning or claim a verbatim transcript.
- Mark unavailable context. If an earlier part of the Agent Session was compacted, archived, or unavailable, state that the checkpoint covers only the visible context.
- Qualify volatile facts with an `as of` timestamp. Treat Git, Fluent, and live systems as their own sources of truth.
- Do not create a project task ledger. `CONVERSATIONS.md` routes agents to Current Conversations; it contains no unique task detail.

## Save the Agent Session

### 1. Locate the durable project

Use the project path named by the user. Otherwise use the enclosing Git root, then the current directory when no Git root exists.

Do not assume a tool-created or temporary worktree is durable. Check project instructions and `git worktree list --porcelain`. When the current worktree is disposable, write in the durable checkout and record the active worktree's absolute path. Ask when the durable checkout cannot be identified safely.

Use `<project>/.scratch` as the conversation root unless the user or project instructions name another root.

### 2. Pass the recovery gate

Before reading the Conversation Index or any Current Conversation, check for `.scratch/_conversations/RECOVERY_REQUIRED.json`. It marks a canonical projection that may contain only part of an interrupted publication.

On the automatic path, make no conversation-file changes, report the marker path, and stop the skill. On the explicit path, acquire the publication lock, reread the marker under the lock, and recover before inspecting canonical state. If the marker disappeared while waiting, release the lock and continue with step 3.

Treat recovery data as untrusted until all of these checks pass:

- The marker, manifest, backups, and candidates are regular non-symlink files, and every path component below `.scratch` is a real directory rather than a symlink.
- The marker token equals the manifest token and the staging directory name. The manifest path is exactly `_conversations/.staging/<old-token>/manifest.json`. The old transaction token need not equal the new lock token held for recovery.
- `existing` maps canonical paths to SHA-256 hashes of byte-for-byte copies at `previous/<canonical-path>`. `absent` is a list of canonical paths. `candidate` maps every canonical path to the SHA-256 hash of its file at `candidate/<canonical-path>`.
- `existing` and `absent` are disjoint and contain no duplicates; the `candidate` keys equal their union and contain no duplicates. Their paths resolve inside `.scratch` and are limited to `CONVERSATIONS.md`, `<valid-conversation-id>/CONVERSATION.md`, and exactly one `_conversations/sessions/<schema-valid-checkpoint-name>.md`. The checkpoint is the manifest's `new_checkpoint` and appears only in `absent` and `candidate`.
- Existing Index and Current Conversation backups and every candidate declare `managed_by: conversation-continuity`. Every recorded hash matches its staged file.

Reject malformed or unexpected recovery data without moving, replacing, or deleting anything. Preserve the marker and staging, release the recovery lock, report exact paths and errors, and stop.

To recover valid data, accept a live `existing` target only when it is absent or byte-identical to its recorded previous or candidate copy, then atomically restore the previous bytes. Accept a live `absent` target only when it is absent or byte-identical to its candidate; remove it only in the latter case. Verify every restored byte and absence, remove the marker, remove that staging directory, and release the lock. If any action fails, preserve the marker and staging, release the lock, report every recovery error, and stop. After successful recovery, restart at step 3 and rebuild state from the restored projection.

### 3. Resolve affected Project Conversations

Use a lowercase hyphenated Conversation ID such as `nemotron-eval` or `release-hardening`. Resolve affected conversations in this order:

1. Use an ID explicitly named by the user.
2. Use the enclosing `.scratch/<conversation-id>` directory when the session is already working there.
3. Match existing Current Conversations to the session's goals, artifacts, and Fluent identifiers.
4. Create an ID from the bounded thread when no existing conversation fits.
5. Include every Project Conversation materially changed by the session; ignore conversations mentioned only as background.

Resolve each bounded thread independently. Use one dominant-goal ID only when the session is one thread. When new areas have independent next actions or constraints, give them separate IDs instead of collapsing them into one conversation.

On the explicit path, ask one short question only when two plausible choices would write to different places and the visible session does not resolve the choice. On the automatic path, skip only the unresolved thread, save any other unambiguous affected conversations, and continue already-authorized work. Raise the ambiguity only when it also affects that work.

Use status to describe resume readiness:

| Status | Meaning |
|--------|---------|
| `ready` | A later agent can take a safe, meaningful action now. |
| `waiting-user` | The next intended step requires a specific user answer, input, or approval. |
| `waiting-external` | Progress requires a named external event that neither the agent nor user can cause now. |
| `parked` | The user deliberately deferred the conversation; record the reactivation trigger when known. |
| `closed` | The conversation is complete, abandoned, or superseded and has no intended next action. |

Treat `ready`, `waiting-user`, and `waiting-external` as current. Allow several current Project Conversations. Derive status from the first prioritized Resume step, not from every open loop. Do not use `active`, `blocked`, or Fluent's `needs-user` as Project Conversation statuses.

### 4. Read the saved conversation

Read, when present:

- `.scratch/CONVERSATIONS.md`
- `.scratch/<conversation-id>/CONVERSATION.md` for every affected conversation
- each affected conversation's latest checkpoint
- Project Conversation notes used by the visible Agent Session
- project instructions governing `.scratch`

When canonical files do not exist, read `.scratch/HANDOFFS.md`, relevant `.scratch/<id>/HANDOFF.md` files, and linked legacy records as migration input. Never modify those files. Create canonical files alongside them, link relevant legacy records from History, and add the old router or individual records under `## Legacy records` in `CONVERSATIONS.md`.

When canonical and legacy files exist for the same ID, treat the canonical Current Conversation as current and the legacy file as history. Do not merge stale legacy state back into the current projection.

Do not read every checkpoint by default. Follow links from the Current Conversation and read older records only to resolve missing or contradictory facts.

### 5. Inspect current state

Verify only facts that may have changed and matter to the breakpoint.

For Git work, run the read-only collector relative to this SKILL.md:

```sh
python3 scripts/collect_git_state.py --project <project-root> [--compare <ref> ...]
```

Pass `--compare` only for relevant refs. Record a missing configured upstream as `none`; do not infer an upstream from a same-commit remote-tracking ref. Keep configured upstream, comparison refs, committed, pushed, deployed, dirty, external, and temporary state distinct.

The collector reports Git-visible state, not a complete write audit. Files under an ignored `.scratch` may not appear in its changes list. Track files written by this invocation separately.

Read [references/evidence.md](references/evidence.md) before writing claims in Verification or Evidence. Inspect relevant test output, processes, services, or generated artifacts only when safe and useful.

If a Project Conversation is explicitly linked to Fluent, read [references/fluent.md](references/fluent.md) and follow its save rules. The presence of `.fluent` alone does not make a conversation Fluent-linked.

### 6. Reconstruct the visible Agent Session

Review the session from the first relevant user request through the save request. Preserve:

- user goals, corrections, constraints, approvals, and unanswered questions
- meaningful agent actions and their results
- decisions, rejected alternatives, and stated tradeoffs
- failed attempts and lessons that prevent repetition
- files, commits, external systems, and temporary processes affected
- validation performed and what it did or did not prove
- the exact breakpoint and next discussion or action

Group routine tool calls that produced no new information. Prefer exact paths, identifiers, commands, and measured results over broad summaries.

### 7. Normalize current state

Build a temporary inventory for each affected Project Conversation:

| Item | State | Basis |
|------|-------|-------|
| <bounded item> | <ready|waiting-user|waiting-external|deferred|done|abandoned> | <source or evidence> |

Use one latest state per bounded item. Describe partial progress inside the item; do not add another state for it. Treat `committed`, `pushed`, `deployed`, `dirty`, `external`, and `temporary` as change qualifiers, not item states.

Give every completion claim a bounded subject. Do not write `everything`, `all work`, `fully done`, or similar claims unless the inventory names a closed set and every member is `done`. A checkpoint may preserve state transitions in order; the Current Conversation states only the latest result.

### 8. Decide whether the automatic path needs a checkpoint

On the automatic path, publish a checkpoint only when both gates pass:

1. **Nonrecoverable delta:** The visible session contains a controlling fact needed to resume that is absent from the current projection and cannot be reconstructed from Git, project documentation, Fluent, artifacts, or a live system.
2. **Save condition:** The work reached a durable milestone, the session is intentionally pausing, or visible context is at risk.

Do not checkpoint ordinary implementation progress or recoverable live state: file edits, test or build completion, commit or push state, a deploy or release asset that can be queried, tree cleanliness, an app install, permissions, a process or PID, or clipboard contents. Do not checkpoint merely because the Current Conversation's Resume became stale.

Do not checkpoint an unfinished diagnosis, design discussion, or set of alternatives while an immediate user reply is expected. Wait for the decision unless the session is intentionally pausing or visible context is at risk.

A hook, elapsed time, or turn count never satisfies either gate. If either gate fails, do not create directories, update timestamps, or touch any conversation file. Continue to Report completion silently.

Once all canonical targets are absent or managed, the explicit path always continues to publication.

### 9. Acquire the lock and stage publication

Run the lock script relative to this SKILL.md. Acquire the project-local lock before rereading or writing managed files:

```sh
python3 scripts/conversation_lock.py acquire <project-root>
```

Record the token printed by the script. If another save owns a fresh lock, wait for it or report the conflict; do not overwrite its work. The script archives an abandoned lock only after its stale interval.

First reread `.scratch/_conversations/RECOVERY_REQUIRED.json` under the lock. If it appeared while waiting, release the lock on the automatic path, report it, and stop. On the explicit path, recover while retaining the lock, release it after recovery, and restart at step 3.

After the under-lock recovery check passes, reread the index and affected Current Conversations. Reconcile any state published since the earlier inspection. On the automatic path, repeat step 8 under the lock; if another writer already captured the same durable delta, release the lock and finish as a no-op.

Before creating this invocation's staging directory, inspect markerless directories under `.scratch/_conversations/.staging/`. Because the current invocation holds the only fresh publication lock, remove an orphan only when it contains a structurally valid managed manifest, its token matches its directory, no recovery marker references it, and it has no paths outside its own staging directory. Preserve malformed or user-owned directories; report them on the explicit path.

Hold the lock while staging, publishing, validating, and reading back one attempt. Refresh it before writing, before validation, and at least every 10 minutes during a longer save:

```sh
python3 scripts/conversation_lock.py refresh <project-root> <token>
```

Release the lock only after successful publication cleanup, successful rollback, or cleanup of an early exit before canonical files changed:

```sh
python3 scripts/conversation_lock.py release <project-root> <token>
```

Choose the final checkpoint name, then create this private recovery state:

```text
.scratch/_conversations/.staging/<token>/
├── manifest.json
├── previous/
│   ├── CONVERSATIONS.md
│   └── <conversation-id>/CONVERSATION.md
└── candidate/
    ├── CONVERSATIONS.md
    ├── <conversation-id>/CONVERSATION.md
    └── _conversations/sessions/<checkpoint>.md
```

The canonical targets are the Conversation Index, affected Current Conversations, and one new checkpoint. Copy each existing target byte for byte to its deterministic `previous/<canonical-path>` location. In `manifest.json`, record `managed_by: conversation-continuity`, `transaction_version: 1`, the lock token, `new_checkpoint`, and the `existing`, `absent`, and `candidate` collections defined in step 2. Use relative normalized paths only, enforce the same path and symlink checks, and record SHA-256 hashes.

Steps 10 through 12 write only below `candidate/`; they do not change canonical files. Do not install the recovery marker until every candidate and the completed manifest pass the step 2 structural and hash checks.

### 10. Write the candidate Conversation Checkpoint

Write one checkpoint for the invocation below `candidate/_conversations/sessions/`, even when the Agent Session affects several Project Conversations. Its contents and links must describe its final canonical path. Do not create or replace the canonical checkpoint yet.

Name it `<local-ISO-timestamp>-<agent>.md`, using a filesystem-safe timestamp such as `2026-07-21T143000-0700-claude.md`. Add `-2`, `-3`, and so on when the name already exists.

Use this structure:

```markdown
---
managed_by: conversation-continuity
conversation_version: 1
created_at: <ISO-8601 timestamp with offset>
agent: <claude|codex|other>
project_root: <absolute durable project path>
reason: <manual|session-end|pre-compaction|logical-boundary>
coverage: <full-visible-context|partial>
conversations:
  - <conversation-id>
---

# Session checkpoint: <short description>

## Resume

<Where work stopped, what matters now, and the first one to three actions or questions.>

## User direction

<Current objective, definition of done, constraints, corrections, and approvals.>

## What happened

<Ordered semantic account of requests, actions, results, failures, and changes in direction.>

## Decisions

<Decisions and rejected alternatives with stated reasons.>

## Changes and side effects

<Conversation-file writes and committed, pushed, deployed, dirty, external, and temporary changes, kept distinct.>

## Verification

<Use Claim/Basis/Source/Result from references/evidence.md, or write `None known.`>

## Open loops

<Unanswered questions, blockers, deferred work, and required approvals.>

## State snapshot

<Relevant Git, workflow, process, service, and artifact state as of a timestamp.>

## Coverage gaps

<Unavailable context, missing evidence, and unverified claims. Write `None known.` when appropriate.>
```

Use one subsection per Project Conversation inside a section when ownership would otherwise be unclear.

Choose `reason` from the semantic boundary that caused the write. A cadence or lifecycle reminder is a trigger, not a reason; do not add `automatic`, `periodic`, or `bootstrap` to the vocabulary.

### 11. Write each candidate Current Conversation

Write a candidate for each managed `.scratch/<conversation-id>/CONVERSATION.md` at `candidate/<conversation-id>/CONVERSATION.md`. Derive it from the previous Current Conversation, the new checkpoint, relevant legacy history, and current evidence. Keep still-open facts from earlier sessions; remove stale instructions and completed work from the resume path. Do not replace canonical Current Conversations yet.

```markdown
---
managed_by: conversation-continuity
conversation_version: 1
conversation: <conversation-id>
status: <ready|waiting-user|waiting-external|parked|closed>
mode: <standalone|fluent-linked>
updated_at: <ISO-8601 timestamp with offset>
latest_checkpoint: <path relative to this file, such as ../_conversations/sessions/<record>.md>
---

# Conversation: <title>

## Resume

<Start with exactly one single-line lead: `Ready for agent: first executable action.`,
`Waiting for user: one exact question.`, `Waiting for event or source: condition and recheck.`,
`Parked: resume when trigger.`, or `Closed: outcome; no next action.`>

## Intent

<User goal, definition of done, latest direction, and constraints.>

## Current state

- [<ready|waiting-user|waiting-external|deferred|done|abandoned>] <bounded item>: <latest state and basis>.

## Decisions

<Current decisions and rejected alternatives whose reasons still matter.>

## Changes

<Committed, pushed, deployed, dirty, external, temporary, and conversation-file changes, kept distinct.>

## Evidence

<Use Claim/Basis/Source/Result from references/evidence.md, or write `None known.`>

## Open questions and risks

<Questions, approvals, dependencies, assumptions, and likely failure modes.>

## Artifacts

<Authoritative files and systems, their paths or identifiers, and whether they are durable or temporary.>

## History

<Newest-first links to checkpoints and relevant legacy records.>
```

For a Fluent-linked conversation, add the fields and section required by `references/fluent.md`. Link to Fluent-owned facts and copy only the permitted session-specific delta.

Keep the first Resume line under 120 characters and do not use a pipe (`|`). For `ready`, `waiting-user`, and `waiting-external`, make the text after its first colon match either the subject or detail of an item with that state. Use the bounded subject in Resume when the exact question or event would exceed the limit; preserve the exact text in the matching item.

Resolve `latest_checkpoint` and History links from `CONVERSATION.md`, not from the project root.

### 12. Write the candidate Conversation Index

Write `candidate/CONVERSATIONS.md` after the other candidates. Replace rows for affected conversations from their candidate Current Conversations. Preserve untouched rows for other conversation IDs so an independent malformed area does not force this save to repair or discard it. Keep no unique facts in the index. Do not replace the canonical Index yet.

```markdown
---
managed_by: conversation-continuity
conversation_version: 1
updated_at: <ISO-8601 timestamp with offset>
---

# Conversations

| Conversation | Status | Mode | Updated | Resume |
|--------------|--------|------|---------|--------|
| [<conversation-id>](<conversation-id>/CONVERSATION.md) | waiting-user | standalone | <timestamp> | <first Resume line verbatim> |

## Legacy records

- [Previous conversation records](HANDOFFS.md)
```

Copy each affected Current Conversation's first Resume line verbatim into its row, and retain untouched rows verbatim. Sort all rows by `waiting-user`, `ready`, `waiting-external`, `parked`, then `closed`, and by the row's `updated_at` newest first within each group.

Preserve relevant unmanaged or legacy links under `## Legacy records`. Keep a broken legacy link and mark it exactly as `[missing] as of <ISO-8601 timestamp with offset>`. Remove a legacy entry only with user approval. Omit the section when there are no legacy links.

Before publishing, verify that every `existing` target still matches its staged prior hash and every `absent` target, including the reserved checkpoint path, is still absent. If either check fails, remove the markerless staging attempt, reconcile the new state instead of overwriting it, and rebuild the transaction.

### 13. Publish and validate

Complete and verify the manifest, then atomically create `.scratch/_conversations/RECOVERY_REQUIRED.json` with `managed_by: conversation-continuity`, `transaction_version: 1`, the transaction token, and the exact manifest path. Refresh the lock. Create only required canonical parent directories. For each target, copy the immutable candidate bytes to a temporary canonical sibling and use `os.replace` or an equivalent atomic replacement, retaining the staged candidate for recovery. Publish in this order: checkpoint, affected Current Conversations, Conversation Index.

Run the validator relative to this SKILL.md:

```sh
python3 scripts/validate_conversations.py <project-root> --session <new-checkpoint> \
  --conversation <affected-id> [--conversation <affected-id> ...]
```

If the validator reports an error, do not edit a canonical file under the recovery marker. Roll back first, correct and rebuild the candidates in a new staged transaction, and retry. Do not repair unrelated conversation areas as part of this save. Review every warning against source evidence, then read every new or changed conversation file end to end. Check that:

- no earlier checkpoint was overwritten
- every affected Current Conversation links to the new checkpoint
- affected index links resolve and their rows contain no detail absent from the affected Current Conversations
- legacy files remain unchanged and relevant links remain reachable
- every Resume action maps to an item with the same actionable or waiting state
- each `waiting-user` item contains the exact unanswered question
- each `waiting-external` item names the event and how to observe it
- no item has terminal and nonterminal state at the same scope
- evidence supports each claim at the same system boundary
- volatile claims have timestamps and provenance
- no pushed commit implies a clean tree, deployment, or configured upstream
- commands include their working directory or enough context to run safely
- no secret or unsafe output was copied
- no file outside the conversation root changed

If a fact cannot be checked, keep it and label it unverified instead of guessing.

Any failure after staging begins enters cleanup before the lock is released. If the marker was not installed, no canonical file changed: remove this invocation's staging directory. If the marker was installed, keep the lock and run the same validated recovery procedure as step 2, regardless of whether the failure came from creating a directory, writing, renaming, launching the validator, validation, readback, or marker removal. A recovery mismatch must preserve the marker and staging rather than overwrite or delete an unexpected live file.

## Report completion

After validation passes and the final readback succeeds, atomically remove the recovery marker; that removal is the publication commit point. Then remove the invocation's staging directory and release the publication lock. If staging cleanup alone fails after the marker is gone, report the leftover private path but do not roll back the published projection. Treat the checkpoint as published and immutable.

On the explicit path, tell the user which checkpoint and Current Conversations were written, which Project Conversations were affected, and which facts remain unverified. Do not paste the saved conversation into chat or resume project work in the same turn.

On the automatic path, a successful save and a no-op are completely silent. Do not state that a save is starting, needed, or complete; name the checkpoint; summarize validation; explain why no save was needed; or repeat the previous response or pending choices. Report only a publication or restoration failure, recovery marker, ownership conflict, or ambiguity that prevents currently authorized work. Give the exact problem, path, and next safe action. Continue or finish only work already authorized.
