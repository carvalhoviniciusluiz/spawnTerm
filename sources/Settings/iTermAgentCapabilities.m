//
//  iTermAgentCapabilities.m
//  iTerm2 (it2agent fork)
//

#import "iTermAgentCapabilities.h"

#import "DebugLogging.h"

// User defaults (local-only) holding an explicit path to each it2agent CLI.
// NoSync so a custom-prefs-folder user is not prompted to sync a machine-local
// path.
static NSString *const iTermAgentFlagPathUserDefaultsKey = @"NoSyncIT2AgentFlagPath";

// Environment variable that, when set, overrides all other resolution.
static NSString *const iTermAgentFlagEnvironmentVariable = @"IT2AGENT_FLAG";

// The Team Bridge capability installs/removes the Claude Code agent-teams hook
// in the active project's gitignored .claude/settings.local.json, via
// it2agent-team-hook (a best-effort, project-scoped GUI action — see #96/#93).
static NSString *const iTermAgentTeamHookToolName = @"it2agent-team-hook";
static NSString *const iTermAgentTeamHookPathUserDefaultsKey = @"NoSyncIT2AgentTeamHookPath";
static NSString *const iTermAgentTeamHookEnvironmentVariable = @"IT2AGENT_TEAM_HOOK";

@implementation iTermAgentCapabilities

+ (NSArray<NSString *> *)capabilityIdentifiers {
    // MUST stay in sync with KNOWN_FLAGS in it2agent-flag / it2agent_flag.py.
    static NSArray<NSString *> *identifiers;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        identifiers = @[ @"status_board",
                         @"worktree_isolation",
                         @"messaging",
                         @"inbox",
                         @"cost_dashboard",
                         @"janitor",
                         @"mcp",
                         @"daemon",
                         @"broker",
                         @"review",
                         @"tmux",
                         @"claude_statusbar",
                         @"menubar",
                         @"codex_status",
                         @"native_status",
                         @"team_bridge",
                         @"canonical_port",
                         @"isolate_docker",
                         @"isolate_db" ];
    });
    return identifiers;
}

+ (NSString *)preferenceKeyForCapability:(NSString *)capability {
    return [@"agent." stringByAppendingString:capability];
}

+ (NSArray<NSString *> *)allPreferenceKeys {
    NSMutableArray<NSString *> *keys = [NSMutableArray array];
    for (NSString *capability in [self capabilityIdentifiers]) {
        [keys addObject:[self preferenceKeyForCapability:capability]];
    }
    return keys;
}

+ (NSString *)displayNameForCapability:(NSString *)capability {
    static NSDictionary<NSString *, NSString *> *names;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        names = @{ @"status_board": @"Status Board",
                   @"worktree_isolation": @"Worktree Isolation",
                   @"messaging": @"Messaging",
                   @"inbox": @"Agent Inbox",
                   @"cost_dashboard": @"Cost Dashboard",
                   @"janitor": @"Janitor",
                   @"mcp": @"MCP",
                   @"daemon": @"Daemon",
                   @"broker": @"Broker",
                   @"review": @"Review",
                   @"tmux": @"tmux",
                   @"claude_statusbar": @"Claude Code Status Bar",
                   @"menubar": @"AI Agent Menu Bar",
                   @"codex_status": @"Codex Tab Status",
                   @"native_status": @"Native Tab Status",
                   @"team_bridge": @"Team Bridge",
                   @"canonical_port": @"Canonical Port",
                   @"isolate_docker": @"Docker Isolation",
                   @"isolate_db": @"DB Isolation" };
    });
    NSString *name = names[capability];
    if (name) {
        return name;
    }
    // Fallback: title-case the identifier so an unknown capability still reads.
    return [capability capitalizedString];
}

+ (NSString *)descriptionForCapability:(NSString *)capability {
    // One-line, plain-language blurbs shown next to each checkbox. MUST stay in
    // sync with the KNOWN_FLAGS descriptions in it2agent-flag / it2agent_flag.py
    // (the shell twin carries names only, so only the Python map mirrors these).
    static NSDictionary<NSString *, NSString *> *descriptions;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        descriptions = @{ @"status_board": @"Legacy: colors the tab and sets a status variable to show agent state. Prefer Native Tab Status.",
                          @"worktree_isolation": @"Gives each agent its own git worktree and a dedicated port so they never collide.",
                          @"messaging": @"Lets agents send messages to each other across tabs through the broker.",
                          @"inbox": @"Keeps a durable per-agent inbox so messages survive restarts.",
                          @"cost_dashboard": @"Shows a running dashboard of token usage and cost.",
                          @"janitor": @"Cleans up stale worktrees and sessions in the background.",
                          @"mcp": @"Exposes it2agent to your agents as an MCP server.",
                          @"daemon": @"Runs the orchestration daemon that tracks agents and their idle/busy state.",
                          @"broker": @"Runs the durable broker — mailbox, registry, and state over a local socket.",
                          @"review": @"Adds a per-agent diff view to approve-and-merge or request changes on a worktree.",
                          @"tmux": @"Runs agents inside a tmux -CC session so they survive a quit or crash and can reattach.",
                          @"claude_statusbar": @"Adds a status-bar item summarizing Claude Code sessions (Waiting, Working, Idle).",
                          @"menubar": @"Adds a menu-bar item with a live count of busy AI agents.",
                          @"codex_status": @"Shows Codex CLI working/idle activity in the tab status.",
                          @"native_status": @"Publishes agent state to iTerm2’s native tab status and Cockpit via OSC 21337.",
                          @"team_bridge": @"Mirrors Claude Code agent-teams state into the durable broker so it survives the lead session’s death.",
                          @"canonical_port": @"The focused agent also answers on the normal localhost port (e.g. 3000), not just its dynamic one.",
                          @"isolate_docker": @"Sets COMPOSE_PROJECT_NAME per agent so Docker Compose stacks don’t collide.",
                          @"isolate_db": @"Exports a per-agent Postgres schema/search_path so agents don’t share DB state." };
    });
    NSString *description = descriptions[capability];
    return description ?: @"";
}

#pragma mark - Executable resolution

// Per-tool cache of resolved executable paths (NSNull for a cached negative
// result). Keyed by tool basename, guarded by @synchronized(self).
+ (NSMutableDictionary<NSString *, id> *)executablePathCache {
    static NSMutableDictionary<NSString *, id> *cache;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        cache = [NSMutableDictionary dictionary];
    });
    return cache;
}

// Resolve the path to a it2agent CLI once and cache the result (including a
// negative result). Resolution order, mirroring the original it2agent-flag
// resolver:
//   1. The tool's environment variable (explicit executable path).
//   2. The tool's NoSync… user default (configurable in prefs/CLI).
//   3. `command -v <tool>` run in a login shell (honors the user's PATH).
//   4. A short list of common install locations.
// Returns nil if nothing runnable is found, in which case the caller fails safe.
+ (nullable NSString *)executablePathForTool:(NSString *)tool
                                       envVar:(NSString *)envVar
                              userDefaultsKey:(NSString *)userDefaultsKey {
    @synchronized (self) {
        NSMutableDictionary<NSString *, id> *cache = [self executablePathCache];
        id cached = cache[tool];
        if (cached) {
            return [cached isKindOfClass:[NSNull class]] ? nil : cached;
        }
        NSString *resolved = [self resolveExecutablePathForTool:tool
                                                         envVar:envVar
                                                userDefaultsKey:userDefaultsKey];
        cache[tool] = resolved ?: (id)[NSNull null];
        if (resolved) {
            DLog(@"%@ resolved to %@", tool, resolved);
        } else {
            DLog(@"%@ could not be located; related UI fails safe", tool);
        }
        return resolved;
    }
}

// Convenience accessor for it2agent-flag (capability toggles).
+ (nullable NSString *)executablePath {
    return [self executablePathForTool:@"it2agent-flag"
                                envVar:iTermAgentFlagEnvironmentVariable
                       userDefaultsKey:iTermAgentFlagPathUserDefaultsKey];
}

// Convenience accessor for it2agent-team-hook (Team Bridge install/uninstall).
// Uses the identical resolver used for it2agent-flag so env → NoSync default →
// login-shell `command -v` → common install locations all apply.
+ (nullable NSString *)teamHookExecutablePath {
    return [self executablePathForTool:iTermAgentTeamHookToolName
                                envVar:iTermAgentTeamHookEnvironmentVariable
                       userDefaultsKey:iTermAgentTeamHookPathUserDefaultsKey];
}

+ (nullable NSString *)resolveExecutablePathForTool:(NSString *)tool
                                             envVar:(NSString *)envVar
                                    userDefaultsKey:(NSString *)userDefaultsKey {
    NSFileManager *fileManager = [NSFileManager defaultManager];

    NSString *fromEnvironment = [NSProcessInfo processInfo].environment[envVar];
    if ([self isRunnableFile:fromEnvironment fileManager:fileManager]) {
        return fromEnvironment;
    }

    NSString *fromUserDefaults = [[NSUserDefaults standardUserDefaults] stringForKey:userDefaultsKey];
    if ([self isRunnableFile:fromUserDefaults fileManager:fileManager]) {
        return fromUserDefaults;
    }

    NSString *fromShell = [self pathFromLoginShellLookupForTool:tool];
    if ([self isRunnableFile:fromShell fileManager:fileManager]) {
        return fromShell;
    }

    NSString *home = NSHomeDirectory();
    NSArray<NSString *> *candidates = @[ [home stringByAppendingPathComponent:[@".local/bin" stringByAppendingPathComponent:tool]],
                                         [@"/opt/homebrew/bin" stringByAppendingPathComponent:tool],
                                         [@"/usr/local/bin" stringByAppendingPathComponent:tool],
                                         [@"/usr/bin" stringByAppendingPathComponent:tool] ];
    for (NSString *candidate in candidates) {
        if ([self isRunnableFile:candidate fileManager:fileManager]) {
            return candidate;
        }
    }
    return nil;
}

+ (BOOL)isRunnableFile:(nullable NSString *)path fileManager:(NSFileManager *)fileManager {
    if (path.length == 0) {
        return NO;
    }
    BOOL isDirectory = NO;
    if (![fileManager fileExistsAtPath:path isDirectory:&isDirectory] || isDirectory) {
        return NO;
    }
    return [fileManager isExecutableFileAtPath:path];
}

// Ask the user's login shell where a tool lives. A login shell sources the
// user's profile, so PATH additions (~/.local/bin, homebrew, etc.) apply even
// though the GUI app does not inherit an interactive PATH.
+ (nullable NSString *)pathFromLoginShellLookupForTool:(NSString *)tool {
    NSString *shell = [NSProcessInfo processInfo].environment[@"SHELL"];
    if (shell.length == 0) {
        shell = @"/bin/zsh";
    }
    NSString *command = [@"command -v " stringByAppendingString:tool];
    NSString *output = [self runExecutable:shell
                                 arguments:@[ @"-l", @"-c", command ]
                                exitStatus:NULL];
    NSString *trimmed = [output stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (trimmed.length == 0) {
        return nil;
    }
    // `command -v` may print several lines; take the first.
    return [[trimmed componentsSeparatedByString:@"\n"] firstObject];
}

#pragma mark - Process execution

// Synchronously run an executable and return its stdout as a string. Returns
// nil on failure. When `exitStatus` is non-NULL it receives the exit code (or
// -1 if the process could not be launched). Blocking is acceptable here: the
// flag CLI reads a tiny TOML and returns immediately, and this is only invoked
// from the Settings UI, never a hot path.
+ (nullable NSString *)runExecutable:(NSString *)path
                           arguments:(NSArray<NSString *> *)arguments
                          exitStatus:(nullable int *)exitStatus {
    return [self runExecutable:path arguments:arguments directory:nil exitStatus:exitStatus];
}

// As above, but runs with `directory` as the process working directory (nil to
// inherit the app's). Used by the project-scoped team-bridge commands, which
// resolve the git root from cwd.
+ (nullable NSString *)runExecutable:(NSString *)path
                           arguments:(NSArray<NSString *> *)arguments
                           directory:(nullable NSString *)directory
                          exitStatus:(nullable int *)exitStatus {
    if (exitStatus) {
        *exitStatus = -1;
    }
    NSTask *task = [[NSTask alloc] init];
    task.launchPath = path;
    task.arguments = arguments;
    if (directory.length > 0) {
        task.currentDirectoryPath = directory;
    }
    NSPipe *stdoutPipe = [NSPipe pipe];
    task.standardOutput = stdoutPipe;
    task.standardError = [NSPipe pipe];
    task.standardInput = [NSPipe pipe];

    NSData *data = nil;
    @try {
        [task launch];
        data = [[stdoutPipe fileHandleForReading] readDataToEndOfFile];
        [task waitUntilExit];
    } @catch (NSException *exception) {
        DLog(@"Failed to run %@ %@: %@", path, arguments, exception);
        return nil;
    }
    if (exitStatus) {
        *exitStatus = task.terminationStatus;
    }
    return [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
}

#pragma mark - State cache

// Cache of capability -> @(BOOL) parsed from `it2agent-flag list`. nil means
// not yet loaded (or invalidated).
+ (NSMutableDictionary<NSString *, NSNumber *> *)stateCache {
    static NSMutableDictionary<NSString *, NSNumber *> *cache;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        cache = [NSMutableDictionary dictionary];
    });
    return cache;
}

static BOOL sCacheLoaded = NO;

+ (void)invalidateCache {
    @synchronized (self) {
        sCacheLoaded = NO;
        [[self stateCache] removeAllObjects];
    }
}

+ (void)loadCacheIfNeeded {
    @synchronized (self) {
        if (sCacheLoaded) {
            return;
        }
        sCacheLoaded = YES;
        [[self stateCache] removeAllObjects];

        NSString *path = [self executablePath];
        if (!path) {
            return;
        }
        int status = -1;
        NSString *output = [self runExecutable:path arguments:@[ @"list" ] exitStatus:&status];
        if (!output) {
            return;
        }
        // Each line: "agent.<cap>   on|off".
        for (NSString *rawLine in [output componentsSeparatedByString:@"\n"]) {
            NSArray<NSString *> *fields = [rawLine componentsSeparatedByCharactersInSet:[NSCharacterSet whitespaceCharacterSet]];
            NSMutableArray<NSString *> *tokens = [NSMutableArray array];
            for (NSString *field in fields) {
                if (field.length > 0) {
                    [tokens addObject:field];
                }
            }
            if (tokens.count < 2) {
                continue;
            }
            NSString *key = tokens[0];
            NSString *state = tokens.lastObject;
            if (![key hasPrefix:@"agent."]) {
                continue;
            }
            NSString *capability = [key substringFromIndex:@"agent.".length];
            [self stateCache][capability] = @([state isEqualToString:@"on"]);
        }
    }
}

#pragma mark - Public state accessors

+ (BOOL)available {
    return [self executablePath] != nil;
}

+ (BOOL)isEnabledForCapability:(NSString *)capability {
    [self loadCacheIfNeeded];
    @synchronized (self) {
        return [[self stateCache][capability] boolValue];
    }
}

+ (void)setEnabled:(BOOL)enabled forCapability:(NSString *)capability {
    NSString *path = [self executablePath];
    if (!path) {
        DLog(@"Ignoring set %@=%@: it2agent-flag unavailable", capability, @(enabled));
        return;
    }
    NSString *subcommand = enabled ? @"enable" : @"disable";
    int status = -1;
    [self runExecutable:path arguments:@[ subcommand, capability ] exitStatus:&status];
    DLog(@"it2agent-flag %@ %@ exited %d", subcommand, capability, status);
    // Re-query on next read so the cache reflects what the CLI actually wrote.
    [self invalidateCache];
}

#pragma mark - Team Bridge (project-scoped Claude Code hook)

+ (BOOL)teamBridgeStatusForDirectory:(NSString *)directory
                        resolvedPath:(NSString * _Nullable * _Nullable)resolvedPath
                           installed:(BOOL *)installed {
    if (resolvedPath) {
        *resolvedPath = nil;
    }
    if (installed) {
        *installed = NO;
    }
    if (directory.length == 0) {
        return NO;
    }
    NSString *hookPath = [self teamHookExecutablePath];
    if (!hookPath) {
        DLog(@"team-bridge status unavailable: it2agent-team-hook not found");
        return NO;
    }
    int status = -1;
    // `status --scope project` prints the resolved settings.local.json path on
    // stdout and signals state via exit code: 0 = our hook installed, 1 = absent,
    // 2 = the directory is not inside a git repo (or unresolvable). Run it with
    // the session's working directory as cwd so the CLI finds that project root.
    NSString *output = [self runExecutable:hookPath
                                 arguments:@[ @"status", @"--scope", @"project" ]
                                 directory:directory
                                exitStatus:&status];
    if (status != 0 && status != 1) {
        // Not a git repo (2), or the process failed to launch (-1).
        return NO;
    }
    if (resolvedPath) {
        NSString *trimmed = [output stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
        *resolvedPath = trimmed.length > 0 ? trimmed : nil;
    }
    if (installed) {
        *installed = (status == 0);
    }
    return YES;
}

+ (void)setTeamBridgeInstalled:(BOOL)installed forDirectory:(NSString *)directory {
    if (directory.length == 0) {
        DLog(@"Ignoring team-bridge %@: no working directory", installed ? @"install" : @"uninstall");
        return;
    }
    NSString *hookPath = [self teamHookExecutablePath];
    if (!hookPath) {
        DLog(@"Skipping team-bridge %@: it2agent-team-hook unavailable", installed ? @"install" : @"uninstall");
        return;
    }
    NSString *subcommand = installed ? @"install" : @"uninstall";
    int status = -1;
    // Best-effort: runExecutable: is exception-safe and returns nil on launch
    // failure, so a missing tool or a write error never throws into the pane.
    [self runExecutable:hookPath
              arguments:@[ subcommand, @"--scope", @"project" ]
              directory:directory
             exitStatus:&status];
    DLog(@"it2agent-team-hook %@ --scope project (cwd=%@) exited %d", subcommand, directory, status);
}

@end
