---
name: save-conversation
description: >
  Save the current visible human-agent session into durable Project Conversation files so a later Claude or Codex
  session can resume the work. Use when the user asks to save a conversation, checkpoint progress, preserve context,
  switch agents, end a session, hand off, or prepare for context compaction. Write only managed files under .scratch.
  Do not use to load saved work; use resume-conversation for that.
---

# Save Conversation

Save the visible Agent Session before doing more project work. Preserve what a later agent cannot recover from Git, project documentation, Fluent, or other live systems.

A Project Conversation is a named thread of project work that spans Agent Sessions; it is not a transcript or one client chat. One Agent Session may affect several Project Conversations, and one Project Conversation may link to zero, one, or several Fluent Work Items.

Use `resume-conversation` to load the current `.scratch` projection.

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

- Change only managed Project Conversation files. Do not continue implementation, edit project documentation, commit, push, deploy, or clean up work unless the user separately asks.
- Never modify a published Conversation Checkpoint. Correct the new record during validation, then publish it by reporting completion. Write another checkpoint later.
- Preserve existing notes and legacy records. Never rename, rewrite, or move `HANDOFFS.md`, `HANDOFF.md`, or `_handoff/sessions/`; read them only as legacy input.
- Treat an existing `CONVERSATION.md` or `CONVERSATIONS.md` without `managed_by: conversation-continuity` as user-owned. Ask before replacing it.
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

### 2. Resolve affected Project Conversations

Use a lowercase hyphenated Conversation ID such as `nemotron-eval` or `release-hardening`. Resolve affected conversations in this order:

1. Use an ID explicitly named by the user.
2. Use the enclosing `.scratch/<conversation-id>` directory when the session is already working there.
3. Match existing Current Conversations to the session's goals, artifacts, and Fluent identifiers.
4. Create an ID from the dominant goal when no existing conversation fits.
5. Include every Project Conversation materially changed by the session; ignore conversations mentioned only as background.

Ask one short question only when two plausible choices would write to different places and the visible session does not resolve the choice.

Use status to describe resume readiness:

| Status | Meaning |
|--------|---------|
| `ready` | A later agent can take a safe, meaningful action now. |
| `waiting-user` | The next intended step requires a specific user answer, input, or approval. |
| `waiting-external` | Progress requires a named external event that neither the agent nor user can cause now. |
| `parked` | The user deliberately deferred the conversation; record the reactivation trigger when known. |
| `closed` | The conversation is complete, abandoned, or superseded and has no intended next action. |

Treat `ready`, `waiting-user`, and `waiting-external` as current. Allow several current Project Conversations. Derive status from the first prioritized Resume step, not from every open loop. Do not use `active`, `blocked`, or Fluent's `needs-user` as Project Conversation statuses.

### 3. Read the saved conversation

Read, when present:

- `.scratch/CONVERSATIONS.md`
- `.scratch/<conversation-id>/CONVERSATION.md` for every affected conversation
- each affected conversation's latest checkpoint
- Project Conversation notes used by the visible Agent Session
- project instructions governing `.scratch`

When canonical files do not exist, read `.scratch/HANDOFFS.md`, relevant `.scratch/<id>/HANDOFF.md` files, and linked legacy records as migration input. Never modify those files. Create canonical files alongside them, link relevant legacy records from History, and add the old router or individual records under `## Legacy records` in `CONVERSATIONS.md`.

When canonical and legacy files exist for the same ID, treat the canonical Current Conversation as current and the legacy file as history. Do not merge stale legacy state back into the current projection.

Do not read every checkpoint by default. Follow links from the Current Conversation and read older records only to resolve missing or contradictory facts.

### 4. Inspect current state

Verify only facts that may have changed and matter to the breakpoint.

For Git work, run the read-only collector relative to this SKILL.md:

```sh
python3 scripts/collect_git_state.py --project <project-root> [--compare <ref> ...]
```

Pass `--compare` only for relevant refs. Record a missing configured upstream as `none`; do not infer an upstream from a same-commit remote-tracking ref. Keep configured upstream, comparison refs, committed, pushed, deployed, dirty, external, and temporary state distinct.

The collector reports Git-visible state, not a complete write audit. Files under an ignored `.scratch` may not appear in its changes list. Track files written by this invocation separately.

Read [references/evidence.md](references/evidence.md) before writing claims in Verification or Evidence. Inspect relevant test output, processes, services, or generated artifacts only when safe and useful.

If a Project Conversation is explicitly linked to Fluent, read [references/fluent.md](references/fluent.md) and follow its save rules. The presence of `.fluent` alone does not make a conversation Fluent-linked.

### 5. Reconstruct the visible Agent Session

Review the session from the first relevant user request through the save request. Preserve:

- user goals, corrections, constraints, approvals, and unanswered questions
- meaningful agent actions and their results
- decisions, rejected alternatives, and stated tradeoffs
- failed attempts and lessons that prevent repetition
- files, commits, external systems, and temporary processes affected
- validation performed and what it did or did not prove
- the exact breakpoint and next discussion or action

Group routine tool calls that produced no new information. Prefer exact paths, identifiers, commands, and measured results over broad summaries.

### 6. Normalize current state

Build a temporary inventory for each affected Project Conversation:

| Item | State | Basis |
|------|-------|-------|
| <bounded item> | <ready|waiting-user|waiting-external|deferred|done|abandoned> | <source or evidence> |

Use one latest state per bounded item. Describe partial progress inside the item; do not add another state for it. Treat `committed`, `pushed`, `deployed`, `dirty`, `external`, and `temporary` as change qualifiers, not item states.

Give every completion claim a bounded subject. Do not write `everything`, `all work`, `fully done`, or similar claims unless the inventory names a closed set and every member is `done`. A checkpoint may preserve state transitions in order; the Current Conversation states only the latest result.

### 7. Write one Conversation Checkpoint

Create `.scratch/_conversations/sessions/` when needed. Write one checkpoint for the invocation, even when the Agent Session affects several Project Conversations.

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

### 8. Refresh each Current Conversation

Create or replace only the managed `.scratch/<conversation-id>/CONVERSATION.md`. Derive it from the previous Current Conversation, the new checkpoint, relevant legacy history, and current evidence. Keep still-open facts from earlier sessions; remove stale instructions and completed work from the resume path.

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

### 9. Regenerate the Conversation Index

Write `.scratch/CONVERSATIONS.md` last. Build its table from managed Current Conversations and keep no unique facts in it.

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

Copy each Current Conversation's first Resume line verbatim into the table. Sort by `waiting-user`, `ready`, `waiting-external`, `parked`, then `closed`, and by `updated_at` newest first within each group.

Preserve relevant unmanaged or legacy links under `## Legacy records`. Keep a broken legacy link and mark it exactly as `[missing] as of <ISO-8601 timestamp with offset>`. Remove a legacy entry only with user approval. Omit the section when there are no legacy links.

Before publishing, reread the index and affected Current Conversations. If another agent updated them after inspection, reconcile the new state instead of overwriting it.

### 10. Validate before stopping

Run the validator relative to this SKILL.md:

```sh
python3 scripts/validate_conversations.py <project-root> --session <new-checkpoint>
```

Fix every error. Review every warning against source evidence, then read every new or changed conversation file end to end. Check that:

- no earlier checkpoint was overwritten
- every affected Current Conversation links to the new checkpoint
- index links resolve and its table contains no detail absent from a Current Conversation
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

## Report completion

After validation passes, treat the checkpoint as published and immutable. Tell the user which checkpoint and Current Conversations were written, which Project Conversations were affected, and which facts remain unverified. Do not paste the saved conversation into chat or resume project work in the same turn.
