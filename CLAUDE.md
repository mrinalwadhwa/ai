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
