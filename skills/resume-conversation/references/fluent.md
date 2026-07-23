# Fluent-linked Project Conversations

Read this file only after the user, an existing Current Conversation, or a relevant legacy record explicitly links the Project Conversation to Fluent.

## Contents

- Read live Fluent state
- Keep ownership separate
- When saving
- When resuming

## Read live Fluent state

Treat Fluent as the source of truth for Work Items, Attempts, Tasks, workspaces, review rounds, `needs-user` state, and Merge Candidates. Treat captured Fluent status as a timestamped observation that a later agent must refresh.

Use read-only commands from the durable project root:

```sh
fluent --version
fluent status
fluent work-item list
fluent work-item show <work-item-id>
```

Run `fluent work-item show` for each linked Work Item. Inspect a pending Merge Candidate with the read-only show command reported by `fluent status` or the installed Fluent skill.

During interactive planning before Work Item creation, identify the draft ID, current stage, and last user-confirmed gate. Read the relevant files under `.fluent/drafts/<draft-id>/`. File existence does not prove approval; use the visible Agent Session and explicit confirmation.

After `fluent work-item create`, treat the Work Item's stored planning context as authoritative for execution. Treat its source draft as lineage, not as a competing live plan.

If Fluent is unavailable or a command fails, record the command, error, and time. Do not reconstruct live status from a saved checkpoint.

If the current directory is a delegated Fluent task workspace, find the human-facing project root before reading or writing Project Conversation files.

## Keep ownership separate

Set the Current Conversation's `mode` field to `fluent-linked`, then add:

```yaml
fluent:
  draft_ids:
    - <active-draft-id>
  work_item_ids:
    - <work-item-id>
```

Omit empty lists. Put only active, not-yet-materialized interactive planning under `draft_ids`. Once a Work Item exists, keep the draft only as History lineage unless interactive planning still has unmaterialized user decisions.

Allow one Project Conversation to link to several Work Items. Prefer one owning Project Conversation for each active draft or Work Item; other conversations may reference it as a related artifact without projecting its status independently.

Add a `## Fluent` section to a Fluent-linked Current Conversation.

## When saving

Record only:

- relevant identifiers and why each belongs to the Project Conversation
- the planning stage and last confirmed gate while planning remains interactive
- a timestamped summary of `fluent status` and relevant `fluent work-item show` results
- a link to the exact `needs-user` record and its unresolved human-facing question
- user corrections or decisions not yet represented in a confirmed Fluent artifact, labeled `not materialized`
- the exact read-only commands and Fluent-owned files a later agent must inspect

When Fluent's `needs-user` record is the first Resume step, set Project Conversation status to `waiting-user` and link the exact record. Keep `needs-user` as Fluent's term; do not use it as the Project Conversation status.

Do not copy briefs, behavior files, approaches, plans, Work Item planning context, Task JSON, progress logs, review verdicts, or test output into Project Conversation files. Link to the authoritative artifact.

Do not edit `.fluent`, create or run an Attempt, land a Merge Candidate, clean Fluent state, resolve `needs-user`, or write a Fluent learner `handoff.json`. Those actions are outside Project Conversation save and resume operations.

## When resuming

Refresh live Fluent state before presenting a Resume Brief. Use this authority order:

1. current user direction in the new Agent Session
2. live Fluent status and show commands
3. Work Item planning context after Work Item creation
4. last user-confirmed draft artifacts before Work Item creation
5. Current Conversation
6. older Conversation Checkpoints

Report stale or contradictory saved observations; live Fluent wins for volatile workflow state.

Do not automatically resume an Attempt, resolve `needs-user`, land a Merge Candidate, or modify Fluent state. Loading a Project Conversation is not authorization to execute its Resume step.
