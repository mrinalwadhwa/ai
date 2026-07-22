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
