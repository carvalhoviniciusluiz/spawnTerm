//
//  WorkgroupPresets.swift
//  iTerm2SharedARC
//
//  Created by George Nachman on 4/23/26.
//

import Foundation

// Built-in workgroup templates offered from the "Add Preset" menu in the
// workgroups editor. A preset is just a recipe that constructs a fresh
// iTermWorkgroup value; the resulting workgroup is then owned and edited
// the same way as a user-built one.
struct WorkgroupPreset {
    let identifier: String
    let displayName: String
    let build: () -> iTermWorkgroup
}

enum WorkgroupPresets {
    static let all: [WorkgroupPreset] = [
        WorkgroupPreset(
            identifier: "codingAgentPlusDiff",
            displayName: "Coding Agent + Diff",
            build: buildCodingAgentPlusDiff),
        WorkgroupPreset(
            identifier: "codingAgentPlusDiffPlusCodeReview",
            displayName: "Coding Agent + Diff + Code Review",
            build: { buildCodingAgentPlusDiffPlusCodeReview() })
    ]

    private static func buildCodingAgentPlusDiff() -> iTermWorkgroup {
        let rootID = UUID().uuidString
        let diffID = UUID().uuidString
        // Template uses `\(gitBase)`, the workgroup variable bound
        // to the gitBaseSelector's current value (defaults to HEAD).
        // Double backslash keeps the `\(` literal in the stored
        // Swift string. Per-file diffs reuse the same base via the
        // perFileCommand template below.
        let diffCommand = "git diff \\(gitBase)"

        let root = iTermWorkgroupSessionConfig(
            uniqueIdentifier: rootID,
            parentID: nil,
            kind: .root,
            profileGUID: nil,
            command: "",
            urlString: "",
            toolbarItems: [.modeSwitcher, .gitStatus],
            displayName: "")

        let diff = iTermWorkgroupSessionConfig(
            uniqueIdentifier: diffID,
            parentID: rootID,
            kind: .peer,
            profileGUID: nil,
            command: diffCommand,
            urlString: "",
            toolbarItems: [.modeSwitcher,
                           .changedFileSelector,
                           .gitBaseSelector,
                           .navigation(WorkgroupNavigationShortcuts.defaults)],
            displayName: "Diff",
            perFileCommand: "git diff \\(gitBase) -- \\(file)",
            mode: .diff)

        return iTermWorkgroup(
            uniqueIdentifier: UUID().uuidString,
            name: "Coding Agent + Diff",
            sessions: [root, diff])
    }

    // Builds a Chat-root + Diff-peer + Code-Review-peer workgroup. Shared
    // between the user-pickable preset (defaults: fresh UUIDs and a
    // user-friendly name) and the Claude Code onboarding installer, which
    // overrides every ID and the workgroup name so its triggers and saved
    // references keep resolving across upgrades.
    static func buildCodingAgentPlusDiffPlusCodeReview(
        workgroupID: String = UUID().uuidString,
        rootID: String = UUID().uuidString,
        diffID: String = UUID().uuidString,
        reviewID: String = UUID().uuidString,
        name: String = "Coding Agent + Diff + Code Review"
    ) -> iTermWorkgroup {
        let main = iTermWorkgroupSessionConfig(
            uniqueIdentifier: rootID,
            parentID: nil,
            kind: .root,
            profileGUID: nil,
            command: "",
            urlString: "",
            toolbarItems: [.modeSwitcher, .gitStatus, .autoRequestReviewWhenIdle],
            displayName: "Chat")

        let diff = iTermWorkgroupSessionConfig(
            uniqueIdentifier: diffID,
            parentID: rootID,
            kind: .peer,
            profileGUID: nil,
            // `git difftool -x <cmd>` invokes `<cmd> "$LOCAL" "$REMOTE"`. For
            // deleted/added files git passes /dev/null as one side, and vimdiff
            // refuses it ("/dev/null is not a file", blocking on Press ENTER).
            // The inline sh wrapper swaps any /dev/null side for an empty temp
            // file before exec'ing vimdiff; two real sides behave as before.
            command: "git difftool -y -x 'sh -c '\\''l=\"$1\"; r=\"$2\"; [ \"$l\" = /dev/null ] && l=\"$(mktemp)\"; [ \"$r\" = /dev/null ] && r=\"$(mktemp)\"; exec vimdiff \"$l\" \"$r\"'\\'' sh' \\(gitBase)",
            urlString: "",
            toolbarItems: [.modeSwitcher,
                           .changedFileSelector,
                           .gitBaseSelector,
                           .navigation(WorkgroupNavigationShortcuts.defaults)],
            displayName: "Diff",
            perFileCommand: "git difftool -y -x 'sh -c '\\''l=\"$1\"; r=\"$2\"; [ \"$l\" = /dev/null ] && l=\"$(mktemp)\"; [ \"$r\" = /dev/null ] && r=\"$(mktemp)\"; exec vimdiff \"$l\" \"$r\"'\\'' sh' \\(gitBase) -- \\(file)",
            mode: .diff)

        let review = iTermWorkgroupSessionConfig(
            uniqueIdentifier: reviewID,
            parentID: rootID,
            kind: .peer,
            profileGUID: nil,
            // `codeReviewSystemPromptFile` is bound at launch in
            // PTYSession.wrappedCommandForCodeReview to a temp file
            // holding the user-editable system prompt (Settings >
            // General > AI > Prompts), defaulting to the bundled
            // code-review-system-prompt.txt.
            command: "claude \\(codeReviewPrompt) --append-system-prompt-file '\\(codeReviewSystemPromptFile)' --settings '\\(iterm2.appBundlePath)/Contents/Resources/code-review-settings.txt'",
            urlString: "",
            toolbarItems: [.modeSwitcher,
                           .reload(WorkgroupToolbarShortcut.reloadDefault),
                           .autoSendClippingsWhenIdle],
            displayName: "Code Review",
            mode: .codeReview)

        return iTermWorkgroup(
            uniqueIdentifier: workgroupID,
            name: name,
            sessions: [main, diff, review])
    }
}
