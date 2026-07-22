# it2agent/emit/triggers

Tier 0.3 (#9) — **declarative iTerm2 triggers**. A trigger is a `regex → action`
rule iTerm2 evaluates against a session's terminal **output**. These triggers let
an agent's own printed output update its status board state **without any daemon
running** — the passive/regex counterpart to the active `it2agent-emit` helper
(#7). `scope:external-tooling` — this is static JSON that iTerm2 imports; it does
**not** modify iTerm2 source.

Both paths converge on the same user variables (`agent_status`, `agent_role`,
`agent_task`, exposed by iTerm2 as `user.agent_status` etc.) so the status board
(#2/#8) reads one source of truth regardless of which path set it. The names are
deliberately **dot-free** — see “Why underscored names” below.

## Files

- `it2agent-agent-status.triggers.json` — an importable set of 8 triggers (4
  patterns × 2 actions each). Import it into any profile.

## How to import (iTerm2)

1. iTerm2 → **Settings… → Profiles → Advanced → Triggers → Edit…**
2. In the triggers sheet, click the **gear / ⚙︎ menu** (bottom-left) and choose
   **Import…**, then pick `it2agent-agent-status.triggers.json`.
   - iTerm2 asks which profile(s) to import into; choose your agent profile.
3. The 8 rules appear in the list. Each row shows its regex and action; the
   `Set User Variable` rows read `agent_status = <value>` and the `Post
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
  `\u0001`, so the stored parameter for “set `agent_status` to `blocked`” is:

  ```
  "agent_status\u0001blocked"
  ```

  The trigger prepends `user.` itself at runtime (`setVariableNamed: "user." +
  name`), so the resulting variable is `user.agent_status` — the same variable
  `it2agent-emit status` sets via the `SetUserVar=agent_status=…` escape code,
  and the same variable the badge reads as `\(user.agent_status)`.

  > **Note — the issue guessed the parameter format as `user.agent.status=blocked`;
  > the verified iTerm2 format is the SOH-separated two-string codec above.** The
  > file uses the verified format so it imports and round-trips against iTerm2's
  > own Export to File.

## The triggers

Each pattern is realized as **two** rules because one iTerm2 trigger performs one
action: a `Set User Variable` rule and a `Post Notification` rule that share the
same regex.

| Pattern | Regex (summary) | `agent_status` | Notification |
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

## Interop with `it2agent-emit` (the active path)

Two paths, one source of truth:

- **Active path — `it2agent-emit`** (#7): the agent *chooses* to signal state by
  writing an escape code (`it2agent-emit status blocked` →
  `SetUserVar=agent_status=<base64>`). Precise, intentional.
- **Passive path — these triggers** (#9): iTerm2 *watches the output* and sets the
  same variable when a known pattern appears, even for agents that never call
  `it2agent-emit`. Zero cooperation required, zero daemon.

Both write `user.agent_status` (and could write `user.agent_role` /
`user.agent_task` similarly), so the status board / badge / colors (#2, #8) read
one variable no matter who set it — the badge template is `\(user.agent_status)`.
Use them together: emit for the states the agent knows about, triggers as a safety
net for the states it just prints.

## Feature flag

These triggers realize the **`agent.status_board`** capability — the same flag
that gates `it2agent-emit`. Because triggers are static JSON evaluated inside
iTerm2, there is **no runtime gate** on the JSON itself (nothing here executes
`it2agent-flag`). The intent is: **import/enable these triggers only when
`agent.status_board` is ON for the profile.** If the capability is off, leave
them unimported (or toggle the rows off with `"disabled": true`).

## Why underscored names (`agent_status`, not `agent.status`)

iTerm2 **rejects a user-variable name that contains a `.`** in both var-setting
paths:

- Escape-code path: `PTYSession.screenSetUserVar:` returns early when the key
  contains `.` (`sources/PTYSession/PTYSession.m`, `if ([kvp.firstObject
  rangeOfString:@"."] …) { … return; }`).
- Trigger path: `SetUserVariableTrigger.variableNameAndValue(_:)` returns `nil`
  when the name contains `.` (`sources/Triggers/SetUserVariableTrigger.swift:54`,
  `guard !key.contains(".")`).

So we use the **dot-free** name `agent_status`. With no `.` in the key the
`guard !key.contains(".")` check passes and the variable **is** set at runtime;
iTerm2 exposes it as `user.agent_status`. This matches the coordinated hotfix
(#23) that moved the emit helper and the badge to the same underscored names, so
the passive (trigger) and active (emit) paths converge on the identical variable
the badge reads with `\(user.agent_status)`.

## Validation

```
jq -e 'type == "array" and length == 8' it2agent-agent-status.triggers.json
python3 -m json.tool it2agent-agent-status.triggers.json > /dev/null
```
