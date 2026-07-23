# Convention — how it2agent writes Claude Code config (PROJECT-LOCAL, flag-gated)

> **Standing rule (operator directive).** Any configuration that it2agent needs Claude Code to
> pick up — hooks, env, MCP wiring, settings — is **always** written to the **active project's**
> `<git-root>/.claude/settings.local.json`, exposed as a **feature-flag**, with symmetric,
> safe install/uninstall. Never global, never committed, unless the operator explicitly opts into
> a wider scope.

## Why project-local `.claude/settings.local.json`

Claude Code reads settings from several files (precedence high→low): managed → CLI → **local
`.claude/settings.local.json` (gitignored)** → **project `.claude/settings.json` (committed)** →
user `~/.claude/settings.json`. We target the **local** file on purpose:

- **Per-project.** Only the Claude Code that runs in *that* project is affected — not the whole
  machine, not other projects.
- **Local to the machine, not distributed.** `.claude/settings.local.json` is gitignored by Claude
  Code's own convention, so it is **not committed** and cannot reach collaborators who clone the
  repo. This side-steps the documented risk that **project-*committed* hooks run automatically,
  ungated by the workspace-trust dialog** (CVE-2025-59536; Adversis / SonarSource writeups) — a
  cloned repo's `.claude/settings.json` can execute commands before the developer inspects them.
  We never put our hooks in the committed file, so we never create that exposure for anyone else.
- **You installed it, on your machine.** No trust prompt friction, and the command path points at
  *this* machine's it2agent tooling (valid locally).

## The pattern (every Claude-config integration follows this)

1. **A feature-flag** in the schema (`agent.<key>`, default OFF), registered in sync across
   `it2agent/flags/it2agent_flag.py`, `it2agent/flags/it2agent-flag`, and
   `sources/Settings/iTermAgentCapabilities.m`.
2. **Install/uninstall into the active project's `<git-root>/.claude/settings.local.json`:**
   - Resolve the git root of the target cwd (from the CLI's cwd, or — in the GUI — the **active
     terminal session's** working directory). If cwd is not in a git repo, refuse with a clear
     message; do **not** silently fall back to global.
   - **Deep-merge** our entries, preserving everything else in the file. Create the file if missing.
   - **Ensure it is gitignored** (append `.claude/settings.local.json` to the project `.gitignore`
     if not already ignored) — belt-and-suspenders on top of Claude Code's default.
   - `uninstall` removes **only our entries**, idempotently.
   - A settings-path override env (e.g. `IT2AGENT_CLAUDE_SETTINGS`) exists so tests never touch a
     real `~/.claude` or project file.
3. **"Installed = enabled."** Because the config lives in a specific project, its *presence* is the
   per-project opt-in. The global `agent.<key>` config flag is at most an optional **kill-switch**
   (explicit `false` disables; unset/true ⇒ active). This keeps per-project state coherent — a
   single global boolean cannot represent "installed in projects A and B."
4. **GUI (Settings → AI Agents):** the checkbox for such a capability reflects/controls the install
   state of the **active terminal's project**, and shows the exact target so scope is explicit:
   `Aplica-se a: <git-root>/.claude/settings.local.json`. No active git project ⇒ the checkbox is
   disabled with guidance to open a terminal inside a project.
5. **Safety of the hook itself:** any hook we register is an **observer** — it must `exit 0` with no
   stdout under every condition (flag off, broker down, malformed input), so it can never block or
   steer Claude Code (only exit code 2 would block).

## Reuse, don't reimplement

The install/uninstall/gitignore/git-root logic is shared — the first implementation is the team
bridge (`it2agent/team/it2agent-team-hook`, issue #92/#96). New Claude-config flags plug into the
same mechanism rather than re-rolling settings-file editing.

## References

- Claude Code settings & precedence: https://code.claude.com/docs/en/settings.md
- Hooks (scopes, all events): https://code.claude.com/docs/en/hooks.md
- Project-hook trust risk: https://www.adversis.io/blogs/securing-claude-code-for-teams ·
  https://www.sonarsource.com/blog/claude-arbitrary-code-execution/ (CVE-2025-59536)
