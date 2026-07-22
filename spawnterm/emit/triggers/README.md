# spawnterm/emit/triggers

Tier 0.3 (#9) — **declarative iTerm2 triggers**. A trigger is a `regex → action`
rule iTerm2 evaluates against a session's terminal **output**. These triggers let
an agent's own printed output update its status board state **without any daemon
running** — the passive/regex counterpart to the active `spawnterm-emit` helper
(#7). `scope:external-tooling` — this is static JSON that iTerm2 imports; it does
**not** modify iTerm2 source.

Both paths converge on the same user variables (`agent.status`, `agent.role`,
`agent.task`) so the status board (#2/#8) reads one source of truth regardless of
which path set it.

## Files

- `spawnterm-agent-status.triggers.json` — an importable set of 8 triggers (4
  patterns × 2 actions each). Import it into any profile.

## How to import (iTerm2)

1. iTerm2 → **Settings… → Profiles → Advanced → Triggers → Edit…**
2. In the triggers sheet, click the **gear / ⚙︎ menu** (bottom-left) and choose
   **Import…**, then pick `spawnterm-agent-status.triggers.json`.
   - iTerm2 asks which profile(s) to import into; choose your agent profile.
3. The 8 rules appear in the list. Each row shows its regex and action; the
   `Set User Variable` rows read `agent.status = <value>` and the `Post
   Notification` rows show the message text.

The importer accepts **either a single trigger dict or an array of dicts** and
each rule is created via `Trigger.triggerFromUntrustedDict:`
(`sources/Settings/TriggerController.m`, `+triggersFromFile:` / the comment
“Support both a single dict and an array of dicts”). You can also paste the array
into your own tooling — the file is the exact on-disk shape iTerm2 exports with
**Export to File**.

## The iTerm2 trigger JSON schema (grounded in iTerm2 source)

iTerm2 stores each trigger as a dictionary. The keys used here (all defined in
`sources/Triggers/Trigger.m`):

| Key | Constant (Trigger.m) | Meaning |
| --- | --- | --- |
| `regex` | `kTriggerRegexKey = @"regex"` | the pattern matched against output |
| `action` | `kTriggerActionKey = @"action"` | the trigger class name (see below) |
| `parameter` | `kTriggerParameterKey = @"parameter"` | action argument |
| `partial` | `kTriggerPartialLineKey = @"partial"` | match partial (unterminated) lines too |
| `disabled` | `kTriggerDisabledKey = @"disabled"` | rule on/off |
| `name` | `kTriggerNameKey = @"name"` | optional label shown in the UI |

`matchType` is omitted, which defaults to `iTermTriggerMatchTypeRegex = 0`
(`sources/Settings/Profiles/ITAddressBookMgr.h`) — i.e. a plain regex trigger.

### `action` is the trigger's Objective-C class name

On import, `action` is fed to `NSClassFromString` and must resolve to a `Trigger`
subclass (`Trigger.triggerFromUntrustedDict:`, `sources/Triggers/Trigger.m`).
`Trigger.dictionaryValue` writes `action = NSStringFromClass(self.class)`. The two
actions used here:

- **Set User Variable** → `iTermSetUserVariableTrigger`
  (`@objc(iTermSetUserVariableTrigger)` in
  `sources/Triggers/SetUserVariableTrigger.swift`). Title in the UI:
  “Set User Variable…”.
- **Post Notification** → `iTermUserNotificationTrigger`
  (`sources/Triggers/iTermUserNotificationTrigger.m`). Title in the UI:
  “Post Notification…”. (Legacy synonym `GrowlTrigger` also resolves.)

### `parameter` format per action

- **Post Notification**: the `parameter` is the notification message string
  (backreferences like `\0` for the whole match are allowed but we keep the
  messages static for robustness).
- **Set User Variable**: this is a **two-string** parameter
  (`SetUserVariableTrigger.paramIsTwoStrings() == true`). iTerm2 encodes the two
  strings as `<name><SEP><value>` where `<SEP>` is the control character **U+0001
  (SOH)** — see `TwoParameterTriggerCodec.separator = "\u{1}"` in
  `sources/Triggers/SetUserVariableTrigger.swift`. In JSON that is the escape
  `\u0001`, so the stored parameter for “set `agent.status` to `blocked`” is:

  ```
  "agent.status\u0001blocked"
  ```

  The trigger prepends `user.` itself at runtime (`setVariableNamed: "user." +
  name`), so the resulting variable is `user.agent.status` — the same variable
  `spawnterm-emit status` sets via the `SetUserVar=agent.status=…` escape code.

  > **Note — the issue guessed the parameter format as `user.agent.status=blocked`;
  > the verified iTerm2 format is the SOH-separated two-string codec above.** The
  > file uses the verified format so it imports and round-trips against iTerm2's
  > own Export to File.

## The triggers

Each pattern is realized as **two** rules because one iTerm2 trigger performs one
action: a `Set User Variable` rule and a `Post Notification` rule that share the
same regex.

| Pattern | Regex (summary) | `agent.status` | Notification |
| --- | --- | --- | --- |
| **blocked** | `blocked`, `waiting for (your) input/response/confirmation/approval`, `awaiting …`, `needs your …`, `press enter/return to continue`, `[y/n]`, `(y/n)` — case-insensitive | `blocked` | “agent is blocked / waiting for input” |
| **error** | `build failed/error`, `tests failed`, `N tests failing`, `fatal:`/`fatal error`, `^panic:`, Python `Traceback (most recent call last)`, `npm ERR!`, `FAIL`, Rust `error[E1234]` — case-insensitive | `error` | “agent hit a build/test error” |
| **PR opened** | `https?://github\.com/<owner>/<repo>/pull/<number>` | `pr_open` | “PR opened” |
| **done** | `all tests pass(ed/ing)`, `task complete/completed/done`, `(build) succeeded`, `completed successfully`, `done` at end of line, `✅`, `🎉` — case-insensitive | `done` | “agent finished (done)” |

Design notes on the regexes:

- **Case-insensitive** via the inline `(?i)` flag so `ERROR`, `Error`, `error`
  all match.
- **`blocked` uses `partial: true`** — interactive prompts (`Continue? [y/n]`,
  `Press ENTER to continue`) often print **without a trailing newline**, so the
  rule must be allowed to fire on an unterminated line. The other three use
  `partial: false` (they are complete log lines) to avoid firing repeatedly as a
  line is still being drawn.
- **`error`/`done` are anchored to real failure/completion phrasing** rather than
  a bare `error`/`done` word, to keep false positives low. Tune them for your
  agent's actual output — they are meant to be edited.
- **`pr_open`** is a distinct status (agent finished and opened a PR, awaiting
  review) so the board can distinguish it from a plain `done`.

## Interop with `spawnterm-emit` (the active path)

Two paths, one source of truth:

- **Active path — `spawnterm-emit`** (#7): the agent *chooses* to signal state by
  writing an escape code (`spawnterm-emit status blocked` →
  `SetUserVar=agent.status=<base64>`). Precise, intentional.
- **Passive path — these triggers** (#9): iTerm2 *watches the output* and sets the
  same variable when a known pattern appears, even for agents that never call
  `spawnterm-emit`. Zero cooperation required, zero daemon.

Both write `user.agent.status` (and could write `user.agent.role` /
`user.agent.task` similarly), so the status board / badge / colors (#2, #8) read
one variable no matter who set it. Use them together: emit for the states the
agent knows about, triggers as a safety net for the states it just prints.

## Feature flag

These triggers realize the **`spawnterm.status_board`** capability — the same flag
that gates `spawnterm-emit`. Because triggers are static JSON evaluated inside
iTerm2, there is **no runtime gate** on the JSON itself (nothing here executes
`spawnterm-flag`). The intent is: **import/enable these triggers only when
`spawnterm.status_board` is ON for the profile.** If the capability is off, leave
them unimported (or toggle the rows off with `"disabled": true`).

## Known limitation — dotted user-variable names

Current iTerm2 (this fork included) **rejects a user-variable name that contains a
`.`** in both var-setting paths:

- Escape-code path: `PTYSession.screenSetUserVar:` returns early when the key
  contains `.` (`sources/PTYSession/PTYSession.m`, `if ([kvp.firstObject
  rangeOfString:@"."] …) { … return; }`).
- Trigger path: `SetUserVariableTrigger.variableNameAndValue(_:)` returns `nil`
  when the name contains `.` (`sources/Triggers/SetUserVariableTrigger.swift`,
  `guard !key.contains(".")`).

So on stock iTerm2 the `Set User Variable` rows — like `spawnterm-emit status`
itself — will import fine and the **Post Notification** rows work, but the
`agent.status` variable **is not actually set at runtime** because the name is
dotted. This affects the active emit path (#7) identically; it is a cross-cutting
iTerm2-core concern, not specific to #9. We deliberately keep the `agent.*`
naming here to stay convergent with `spawnterm-emit` and the documented design
(`spawnterm/docs/design.md`). The coordinated fix (a `scope:iterm2-core` change to
allow the dotted `user.agent.*` frame, or a project-wide switch to a flat name
such as `agentStatus` across emit + triggers together) should be tracked
separately so both paths change in lockstep.

## Validation

```
jq -e 'type == "array" and length == 8' spawnterm-agent-status.triggers.json
python3 -m json.tool spawnterm-agent-status.triggers.json > /dev/null
```
