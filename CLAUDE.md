# Agent instructions

## Worktrees

This repository keeps linked worktrees in a shared parent directory:

```
<project>/
  main/           # permanent worktree
  <branch-name>/  # temporary worktree
```

Use `git worktree list` to locate the `main` worktree. Create temporary worktrees beside it, not inside any
worktree. Remove them after their branches are merged.

## Git history

Keep `main` linear. Do not create merge commits.

Before landing a branch, rebase it onto the current `main`, then update `main` with a fast-forward-only merge.
Use `git merge --ff-only` so Git fails instead of creating a merge commit.

## Commit messages

Write the subject in imperative mood and active voice. Start with a capitalized verb, aim for 50 characters,
and do not end with a period. Use `Fix` for broken behavior and `Improve` for something that already worked.

Describe the durable change, not the process that produced it. Do not mention agents, review runs, planning
artifacts, phase or run identifiers, or test and file counts.

When a body is useful, separate it with a blank line and wrap it at 72 characters. Explain what changed and why.
Use bullets for distinct changes.

Do not add `Co-Authored-By` trailers.
