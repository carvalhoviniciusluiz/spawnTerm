# Implementation plan — spawnTerm capability toggles in Settings → General → AI (issue #12, fork-direct)

**Policy:** per the Fork policy (Epic #1), this is implemented **directly in this personal fork** and
is **never submitted upstream**. No maintainer discussion, no upstream PR.
**Scope:** `scope:iterm2-core` (adds UI to iTerm2 Settings, here in the fork). Verify with a build.
**Depends on:** the feature-flag framework (#11, merged as external tooling).

## Problem
spawnTerm capabilities are per-user feature flags (default OFF) stored in
`~/.config/spawnterm/config.toml` under a `[features]` table and read/written by
`spawnterm/flags/` (`spawnterm-flag` shell + `spawnterm_flag.py`). Today users toggle them by CLI
or by hand-editing the TOML. The operator wants a **native GUI** — a section in
**Settings → General → AI** with a checkbox per capability, reading/writing the **same** config
source (single source of truth).

## How iTerm2 preferences work today (grounding)
- Panel shell: `sources/Settings/PreferencePanel.{h,m,xib}` (an `NSToolbar` + `NSTabView`; per-tab
  view controllers as IBOutlets, `PreferencePanel.m:488-513`).
- Each tab derives from `iTermPreferencesBaseViewController`; controls are registered in
  `-awakeFromNib` via `-defineControl:key:type:`, and the base class maps control → `PreferenceInfo`.
- **General** is `GeneralPreferencesViewController` (`sources/Settings/GeneralPreferencesViewController.{h,m}`);
  it has an inner `NSTabView` whose sub-tabs include **AI**, and the AI sub-tab itself nests a further
  tab view with a **"Features"** pane.
- A checkbox bound to a default (canonical example, `GeneralPreferencesViewController.m:1081`):
  `defineControl:_openBookmark key:kPreferenceKeyOpenBookmark type:kPreferenceInfoTypeCheckbox`;
  the key constant + default live in `sources/Settings/iTermPreferences.m`; read/write route through
  `iTermPreferencesBaseViewController` (`:735`, `:318`).
- **Capability checkboxes already exist in the AI "Features" pane** (`_aiFeatureHostedCodeInterpeter`
  et al., bound as an array of `PreferenceInfo` at `GeneralPreferencesViewController.m:1671-1701`) —
  the closest existing analog to what #12 wants, and the natural attachment point.

## The one real design question: config source of truth
iTerm2 prefs normally live in `NSUserDefaults`/`iTermPreferences`; spawnTerm flags live in an
external TOML. #12 requires the checkbox to read/write the **TOML**, not `NSUserDefaults`.

**iTerm2 already has the exact idiom for this.** `PreferenceInfo` supports
`syntheticGetter`/`syntheticSetter` (`sources/Settings/PreferenceInfo.h:102-103`), and iTerm2 uses
it right here in the AI tab to back a checkbox with a non-`NSUserDefaults` store: `_enableAI` reads
/writes `iTermSecureUserDefaults.instance.enableAI` through synthetic accessors
(`GeneralPreferencesViewController.m:1749-1762`); `_aiCompletions` likewise (`:1766-1780`). The base
class routes synthetic keys through those blocks instead of `NSUserDefaults`
(`iTermPreferencesBaseViewController.m:135-149`).

### Proposed approach (Option A — lowest friction)
A new "spawnTerm" box in the AI **Features** pane with one `kPreferenceInfoTypeCheckbox` per
capability (`status_board`, `worktree_isolation`, `messaging`, `agent_inbox`, `cost_dashboard`,
`janitor`, `mcp`, `daemon`, `broker`, `review`, `tmux` — the current `KNOWN_FLAGS`). Each checkbox's:
- `syntheticGetter` → returns the flag state (shell out to `spawnterm-flag <cap>`, exit 0 = on; or
  read the TOML directly). Batch via `spawnterm-flag list` to avoid one shell per checkbox refresh.
- `syntheticSetter` → `spawnterm-flag enable/disable <cap>`.

This keeps `config.toml` as the single source of truth, reuses an in-file iTerm2 pattern, adds **no**
TOML parser to iTerm2, and does **not** reimplement flag logic in iTerm2 (respects
`scope:external-tooling` for the flag engine itself).

### Alternatives (documented, not preferred)
- **Option B:** a custom controller that parses/writes the TOML directly, modeled on
  `iTermRemotePreferences` (`sources/Settings/iTermRemotePreferences.h`), which already backs prefs
  with an external file. Heavier: needs a TOML reader in-tree or still shells out; risks schema drift
  with `KNOWN_FLAGS`.

## Known caveats to handle during implementation
- **Auto layout:** `PreferencePanel.xib` is auto-layout overall, but the AI controls are a
  fixed-frame island (`fixedFrame="YES"`, `translatesAutoresizingMaskIntoConstraints="NO"`). Per
  `CLAUDE.md:19`, new controls should match the surrounding fixed-frame style, not add constraints.
- **External edits:** if the TOML changes outside the panel, an open Settings window won't auto-
  refresh (iTerm2's observers watch `NSUserDefaults`, not arbitrary files). Acceptable to require
  reopen, or add a lightweight refresh-on-appear.
- **Cost:** shelling to `spawnterm-flag` per refresh — mitigate with a single `list` call.

## Minimal change surface
- `GeneralPreferencesViewController.m` — new IBOutlets + a `defineControl:…` group in `-awakeFromNib`
  with synthetic get/set blocks (mirrors `:1671-1701` and `:1749-1780`).
- The AI Features pane in `PreferencePanel.xib` — add the checkboxes (fixed-frame).
- No new key storage in `iTermPreferences.m` (synthetic keys use placeholder defaults, per the
  existing `// ignored - synthetic value` precedent).

## Recommended next step
Implement directly in the fork: add the synthetic-getter/setter checkbox group to the AI **Features**
pane bound to `spawnterm-flag`, matching the surrounding fixed-frame layout, build with
`tools/build.sh`, and verify the toggles round-trip to `config.toml`. One PR, `Closes #12`. Scheduled
after Tier 4 (#6) since it involves XIB editing.
