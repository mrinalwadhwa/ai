---
name: save-conversation
description: >
  Save the current visible human-agent session into durable Project Conversation files so a later Claude or Codex
  session can resume the work. Use when the user asks to save a conversation, checkpoint progress, preserve context,
  switch agents, end a session, hand off, or prepare for context compaction; when a lifecycle instruction requests an
  automatic save check; or, once that path is armed, at a durable milestone with context unavailable from
  authoritative project sources, before an intentional session pause, or when visible context is at risk. Automatic
  checks are silent and never save recoverable live state. They save unfinished discussion only when the session is
  intentionally pausing or visible context is at risk. Write project state only to managed files under .scratch. Use
  resume-conversation to load.
---

# Save Conversation

Save the visible Agent Session at a safe breakpoint. Preserve what a later agent cannot recover from Git, project documentation, Fluent, or other live systems.

A Project Conversation is a named thread of project work that spans Agent Sessions; it is not a transcript or one client chat. One Agent Session may affect several Project Conversations, and one Project Conversation may link to zero, one, or several Fluent Work Items.

Use `resume-conversation` to load the current `.scratch` projection.

## Activation paths

Use the explicit path when the user asks to save, checkpoint, switch agents, or end the session. Publish one checkpoint even when the normalized Project Conversation did not change, report what was written, and stop after saving.

Use the automatic path when lifecycle context first requests a save check. That request arms later checks only for the same top-level interactive or orchestrating session.

Treat an automatic check as silent maintenance. Do not announce the check, explain the materiality decision, narrate its reads or writes, report validation or success, or explain a no-op. Report only a missing or incompatible installation, or a failure or conflict required by the guardrails.

When lifecycle context names a publication protocol, require it to match the protocol declared under Requirements. A missing or different declaration means the installed skill is incompatible. Make no conversation-file changes, report this SKILL.md path, and ask the user to reinstall the current save-conversation skill.

After the first check, reassess at only these save conditions:

- The work crosses a durable milestone and the visible delta contains a controlling fact that cannot be reconstructed from Git, project documentation, Fluent, artifacts, or a live system. Examples include user direction, a decision and its rationale, a failed attempt or lesson, and evidence or side effects.
- The user explicitly ends, switches, or parks the session, or progress must stop for a named external event.
- Lifecycle context identifies imminent compaction or high context pressure that puts unsaved visible context at risk.

An ordinary end of turn, a question awaiting the user's next reply, and a cadence reminder are not save conditions. Cadence and turn hooks request an evaluation only. Do not initiate another check after each completed substep.

Do not run the automatic path inside a delegated subagent or Fluent worker. Their parent or orchestrating session owns Project Conversation continuity.

Before starting the save procedure on the automatic path, apply both gates from step 8 to the lifecycle cause and visible session. Stop the skill without inspecting saved conversations, Git, Fluent, artifacts, or live systems when either gate clearly fails. Inspect sources only when nonrecoverability is uncertain and a save condition exists. Do not gather evidence merely to justify a no-op.

## Requirements

The bundled scripts require Python 3.9 or newer. Git is required only when collecting Git state.

Publication protocol: `publisher-v1`.

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

- Change only managed Project Conversation files while saving. One optional request artifact in the client's private temporary directory is transport rather than project state. Continue implementation afterward only on the automatic path and only when the current request already authorizes it. Do not edit project documentation, commit, push, deploy, or clean up work merely because a save check ran.
- Use `scripts/publish_conversation.py` as the only writer for canonical Project Conversation files. Do not use Write, Edit, apply_patch, redirection, or a general-purpose script to change a checkpoint, Current Conversation, Conversation Index, recovery marker, manifest, staging file, or publication lock directly.
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

On the automatic path, make no conversation-file changes, report the marker path, and stop the skill. On the explicit path, run the bundled recovery command relative to this SKILL.md:

```sh
python3 "<save-conversation-skill>/scripts/publish_conversation.py" recover "<project-root>"
```

`<save-conversation-skill>` is the directory containing this SKILL.md. The publisher validates the marker, manifest, backups, candidates, paths, symlinks, hashes, and every live target before restoring anything. It preserves all recovery material when any value is malformed or unexpected.

After `recovered-retry-required`, restart at step 3 and build a fresh request from the restored projection. `no-recovery-needed` means another writer already finished; restart at the recovery gate. `recovered-with-cleanup-warning` or `cleanup-failed` requires reporting every named private path, durability warning, or lock problem before another save.

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
python3 "<save-conversation-skill>/scripts/collect_git_state.py" \
  --project "<project-root>" [--compare "<ref>" ...]
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

### 9. Capture publication bases

Ask the publisher for the exact base state of the Index and every affected Current Conversation:

```sh
python3 "<save-conversation-skill>/scripts/publish_conversation.py" \
  snapshot "<project-root>" \
  --conversation "<affected-id>" [--conversation "<affected-id>" ...]
```

Use the returned `request_headers` only when the status is `snapshot` and `protocol` is `publisher-v1`, and copy each line into the publication request. Run the publisher from the directory containing this SKILL.md; a missing publisher or different protocol means the installation is incompatible. Make no conversation-file changes, report the failing path, and ask the user to reinstall the current save-conversation skill. The publisher rechecks the captured bases while holding its lock. A mismatch returns `conflict` without changing canonical files; reread and reconcile instead of weakening the precondition. Handle `recovery-required` at the recovery gate and `ownership-conflict` under the ownership guardrail. Do not draft or publish a request after any other failed snapshot status. Snapshot warnings name unreclaimed private artifacts but do not invalidate the captured bases.

Read [references/publish-request.md](references/publish-request.md) before assembling the request. Keep the complete request in memory when the client can send a quoted stdin block. Otherwise write exactly one request file inside a client-managed temporary directory accessible only to the current OS user, ensure the file is created exclusively with mode `0600` or an equivalent ACL, record it as a temporary side effect, and pass it with `--request`. If the client cannot guarantee those protections, use stdin. Do not create candidate files inside the project. The normal path uses one base-snapshot call and one canonical mutation call. The request-file fallback adds one temporary Write.

### 10. Draft the Conversation Checkpoint

Draft one complete checkpoint part for the invocation, even when the Agent Session affects several Project Conversations. Do not write it to its canonical path. Use `@CHECKPOINT@` anywhere the final filename appears; the publisher reserves a collision-free name under the lock and replaces the placeholder.

Propose `<local-ISO-timestamp>-<agent>.md`, using a filesystem-safe timestamp such as `2026-07-21T143000-0700-claude.md`. The publisher adds a numeric suffix when that name already exists.

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

### 11. Draft each Current Conversation

Draft one complete Current Conversation part for each affected ID. Derive it from the previous Current Conversation, the new checkpoint, relevant legacy history, and current evidence. Keep still-open facts from earlier sessions; remove stale instructions and completed work from the resume path. Use `@CHECKPOINT@` in `latest_checkpoint`, History, and any other reference to the new checkpoint. Do not edit a canonical Current Conversation directly.

```markdown
---
managed_by: conversation-continuity
conversation_version: 1
conversation: <conversation-id>
status: <ready|waiting-user|waiting-external|parked|closed>
mode: <standalone|fluent-linked>
updated_at: <ISO-8601 timestamp with offset>
latest_checkpoint: ../_conversations/sessions/@CHECKPOINT@
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

### 12. Review the complete request

Review the checkpoint and every Current Conversation together before publication. Check that:

- no earlier checkpoint was overwritten
- every affected Current Conversation links to the new checkpoint
- each Current Conversation contains the status, mode, updated time, and first Resume line needed to derive its index row
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
- the request declares exactly the affected conversations
- every base value comes from step 9
- every Current Conversation uses `@CHECKPOINT@`

If a fact cannot be checked, keep it and label it unverified instead of guessing.

### 13. Publish once

Send the complete request to the publisher in one invocation:

```sh
python3 "<save-conversation-skill>/scripts/publish_conversation.py" \
  publish "<project-root>" --request - <<'CONVERSATION_SAVE_9f2a4c8e1b6d7350'
<complete version 1 request>
CONVERSATION_SAVE_9f2a4c8e1b6d7350
```

Replace the example heredoc nonce with a fresh value and verify that its complete delimiter line does not occur in the request. Quoting disables shell expansion; the fresh delimiter prevents saved content from ending the heredoc early. When stdin is unavailable, pass the one temporary request file with `--request "<request-file>"`.

The publisher generates the Conversation Index, acquires and refreshes the lock, checks ownership and base hashes, reserves the checkpoint name, stages backups and candidates, validates the complete projection, installs the recovery marker, publishes atomically, validates and reads back canonical bytes, commits by removing the marker, and cleans up. Do not perform any of those writes yourself.

Handle its status as follows:

- `published`: publication finished.
- `published-with-cleanup-warning`: publication finished; report every leftover private path or lock problem without retrying the save.
- `published-with-durability-warning`: the new projection is installed, but durable removal of its recovery marker was not confirmed; report the preserved staging path and stop without retrying or claiming a durable save.
- `invalid-request` or `request-review-required`: no canonical file changed; correct the one request and retry.
- `conflict`: no canonical file changed; rerun step 9, reread changed state, and reconcile.
- `ownership-conflict`: a target is user-owned or unparseable; follow the ownership guardrail.
- `rolled-back`: publication failed and the prior projection was restored; report the failure and do not claim a checkpoint was published.
- `rolled-back-with-cleanup-warning`: the prior projection was restored, but cleanup, durability, or a lock needs attention; report every warning and do not retry yet.
- `recovery-required`: preserve the named recovery files and report the exact safe next action.
- `publication-failed`, `failed`, `cleanup-failed`, or an unknown status: do not claim publication; check the reported marker and lock paths before retrying.

## Report completion

On the explicit path, tell the user which checkpoint and Current Conversations were written, which Project Conversations were affected, and which facts remain unverified. Do not paste the saved conversation into chat or resume project work in the same turn.

On the automatic path, a successful save and a no-op are completely silent. Do not state that a save is starting, needed, or complete; name the checkpoint; summarize validation; explain why no save was needed; or repeat the previous response or pending choices. Report only a missing or incompatible installation, publication or restoration failure, recovery marker, ownership conflict, or ambiguity that prevents currently authorized work. Give the exact problem, path, and next safe action. For an installation problem, name the failing installed path and ask the user to reinstall the current save-conversation skill. Continue or finish only work already authorized.
