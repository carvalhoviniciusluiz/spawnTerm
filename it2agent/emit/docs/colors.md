# it2agent-emit — lifecycle palette & color/badge escape codes

Tier 0.2 (#8) extends `it2agent-emit` with two subcommands that theme a
session by the agent's lifecycle: `color` (tab color) and `badge` (session
badge). This documents the palette, the colorblind-safety rationale, and the
exact bytes emitted. Both the shell (`it2agent-emit`) and Python
(`it2agent_emit.py`) implementations emit byte-identical sequences.

## `color <role-or-status>` — tab color

Emits (framing: `OSC = ESC ]`, terminator `ST = BEL 0x07`):

```
ESC ] 1337 ; SetColors=tab=RRGGBB BEL
```

The key is **`tab`**, not `tabbg`. This is the color key iTerm2 documents and
implements for the tab color: its bundled `it2setcolor` utility lists `tab`
among the valid `SetColors` names, and the terminal parser routes
`SetColors=tab=…` (and the reset form `SetColors=tab=default`) to the tab color.
iTerm2 has no `tabbg` key. (The historical `OSC 6 ; 1 ; bg ; …` path still
exists in iTerm2 but is the legacy Linux-console form; `SetColors=tab` is the
modern documented path.)

`RRGGBB` is a 6-digit hex (a 3-digit `RGB` is also accepted, as is a raw hex
passed directly instead of a status name). No leading `#`.

### Lifecycle palette (colorblind-safe)

The four lifecycle statuses map to colors drawn from the **Okabe-Ito** palette,
a qualitative palette engineered so its entries stay distinguishable under the
common forms of color-vision deficiency (deuteranopia and protanopia, the
red-green types, and tritanopia). The mapping deliberately does **not** rely on
a red/green distinction: the primary busy↔blocked contrast rides the
blue↔orange axis, which is exactly the axis that survives red-green CVD, and the
four colors are additionally separated by lightness so they remain readable even
in grayscale.

| Status    | Hex      | Okabe-Ito name | Rationale |
| --------- | -------- | -------------- | --------- |
| `busy`    | `0072B2` | Blue           | Calm "working" state; dark blue, the anchor of the CVD-safe blue↔orange axis. |
| `blocked` | `E69F00` | Orange         | High-attention; maximally separated from `busy` blue for deuteran/protan viewers, and the brightest of the four. |
| `done`    | `009E73` | Bluish green   | "Complete"; a teal-leaning green that reads distinctly from both blue and orange under CVD (it is *not* a pure red-green signal). |
| `idle`    | `999999` | Neutral gray   | Deliberately chromatically neutral so an idle session recedes; mid-lightness, unambiguous against the three saturated states. |

Why not the intuitive red=blocked / green=done? Because a red/green pair is the
single worst choice for the ~8% of men with red-green CVD — the two would look
nearly identical. Okabe-Ito's orange and bluish-green stand in for that
semantic pair while staying discriminable.

Raw hex is accepted too (`color a1b2c3`, `color fff`) for roles or ad-hoc
theming that falls outside the four lifecycle statuses. Anything that is neither
a known status nor a valid 3/6-digit hex is a usage error (exit 2).

### Exact bytes per status

```
color busy     ->  ESC ] 1337 ; SetColors=tab=0072B2 BEL
color blocked  ->  ESC ] 1337 ; SetColors=tab=E69F00 BEL
color done     ->  ESC ] 1337 ; SetColors=tab=009E73 BEL
color idle     ->  ESC ] 1337 ; SetColors=tab=999999 BEL
```

Hex bytes (from `od`), e.g. `color busy`:

```
1b 5d 31 33 33 37 3b 53 65 74 43 6f 6c 6f 72 73 3d 74 61 62 3d 30 30 37 32 42 32 07
```

## `badge [format]` — session badge

Emits:

```
ESC ] 1337 ; SetBadgeFormat=<base64(format)> BEL
```

iTerm2 requires the badge format to be **base64-encoded** in the escape code.
The format string may interpolate iTerm2 variables with `\(…)`. Because the
`role` and `task` subcommands set the user vars `agent_role` / `agent_task` (via
`SetUserVar`, which iTerm2 exposes under the `user.` namespace — note iTerm2
forbids `.` in a SetUserVar key, so the names are underscored), the default
badge shows role and task:

```
\(user.agent_role) · \(user.agent_task)
```

That default base64-encodes to
`XCh1c2VyLmFnZW50X3JvbGUpIMK3IFwodXNlci5hZ2VudF90YXNrKQ==`, so:

```
badge  ->  ESC ] 1337 ; SetBadgeFormat=XCh1c2VyLmFnZW50X3JvbGUpIMK3IFwodXNlci5hZ2VudF90YXNrKQ== BEL
```

Hex bytes (from `od`):

```
1b 5d 31 33 33 37 3b 53 65 74 42 61 64 67 65 46 6f 72 6d 61 74 3d 58 43 68 31
63 32 56 79 4c 6d 46 6e 5a 57 35 30 58 33 4a 76 62 47 55 70 49 4d 4b 33 49 46
77 6f 64 58 4e 6c 63 69 35 68 5a 32 56 75 64 46 39 30 59 58 4e 72 4b 51 3d 3d 07
```

Pass any other format to override (`badge '\(user.agent_role): \(user.agent_status)'`).
The separator in the default is a middle dot `·` (U+00B7), encoded as the UTF-8
bytes `C2 B7`; both implementations base64 the same UTF-8 bytes.

## Gating

`color` and `badge` gate on the **same** feature flag as the rest of
`it2agent-emit` — `agent.status_board`, checked via the `it2agent-flag`
helper (#11) through the shared `gate_open` code path (no second gate). When the
flag is OFF, absent, or `it2agent-flag` is not on `PATH`, they emit nothing and
exit `0`. Bypass for local testing with `--no-gate` or `IT2AGENT_FORCE=1`.
Validation (unknown status, bad hex, wrong arg count) happens before the gate,
so bad input is a usage error (exit 2) even when gated off.

The only bytes written to stdout are the escape sequence itself (base64 where
iTerm2 requires it). Nothing is logged.
