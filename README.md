# AI

Skills, expertise, and configuration for working with AI agents.

## Install skills

```sh
npx skills add mrinalwadhwa/ai --skill save-conversation --skill resume-conversation --global
```

## Install configuration

```sh
./configuration/install --conversation-continuity
```

Install both conversation skills first; the lifecycle checks invoke them by name.

The optional flag installs lifecycle checks for top-level Claude and Codex sessions. A new or cleared session conditionally loads matching Project Conversation state, and save checks run after the first turn, every three later turns, after 15 minutes, and after compaction. Claude also requests checks at 55% and 75% context use when it does not already have a custom status line.

Checks follow the project associated with the session's working directory. The controller resolves a `main/` durable checkout from its workspace container and from linked worktrees. For work deliberately conducted across another project, use an explicit save or resume request to name that project.

The checks are best-effort. An interrupt or client crash can occur before a Stop hook runs. Save checks remain pending while the client reports plan mode and run after plan mode ends.

Automatic first-save bootstrapping requires a Git checkout or an existing managed Conversation Index. Use an explicit save request for a new non-Git directory.

The installer preserves unrelated Claude and Codex hooks. Review and trust the installed Codex definitions with `/hooks`. Set `AGENT_CONVERSATION_CONTINUITY=off` to disable the controller for a process, or `force` to run it in bypass-permission mode. Optional threshold overrides live in `~/.agents/conversation-continuity.json`:

```json
{
  "save_every_turns": 3,
  "save_every_minutes": 15,
  "context_thresholds": [55, 75]
}
```

## Skills

| Skill | Use |
|-------|-----|
| [save-conversation](skills/save-conversation/SKILL.md) | Save the visible agent session as durable Project Conversation state |
| [resume-conversation](skills/resume-conversation/SKILL.md) | Load saved Project Conversation state without changing project files |
