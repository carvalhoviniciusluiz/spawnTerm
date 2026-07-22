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
                         @"codex_status" ];
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
                   @"codex_status": @"Codex Tab Status" };
    });
    NSString *name = names[capability];
    if (name) {
        return name;
    }
    // Fallback: title-case the identifier so an unknown capability still reads.
    return [capability capitalizedString];
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
    if (exitStatus) {
        *exitStatus = -1;
    }
    NSTask *task = [[NSTask alloc] init];
    task.launchPath = path;
    task.arguments = arguments;
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

@end
