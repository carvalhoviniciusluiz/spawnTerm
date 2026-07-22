//
//  iTermSpawnTermCapabilities.m
//  iTerm2 (spawnTerm fork)
//

#import "iTermSpawnTermCapabilities.h"

#import "DebugLogging.h"

// User default (local-only) holding an explicit path to the spawnterm-flag
// executable. NoSync so a custom-prefs-folder user is not prompted to sync a
// machine-local path.
static NSString *const iTermSpawnTermFlagPathUserDefaultsKey = @"NoSyncSpawnTermFlagPath";

// Environment variable that, when set, overrides all other resolution.
static NSString *const iTermSpawnTermFlagEnvironmentVariable = @"SPAWNTERM_FLAG";

@implementation iTermSpawnTermCapabilities

+ (NSArray<NSString *> *)capabilityIdentifiers {
    // MUST stay in sync with KNOWN_FLAGS in spawnterm-flag / spawnterm_flag.py.
    static NSArray<NSString *> *identifiers;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        identifiers = @[ @"status_board",
                         @"worktree_isolation",
                         @"messaging",
                         @"agent_inbox",
                         @"cost_dashboard",
                         @"janitor",
                         @"mcp",
                         @"daemon",
                         @"broker",
                         @"review",
                         @"tmux",
                         @"claude_statusbar",
                         @"agent_menubar" ];
    });
    return identifiers;
}

+ (NSString *)preferenceKeyForCapability:(NSString *)capability {
    return [@"spawnterm." stringByAppendingString:capability];
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
                   @"agent_inbox": @"Agent Inbox",
                   @"cost_dashboard": @"Cost Dashboard",
                   @"janitor": @"Janitor",
                   @"mcp": @"MCP",
                   @"daemon": @"Daemon",
                   @"broker": @"Broker",
                   @"review": @"Review",
                   @"tmux": @"tmux",
                   @"claude_statusbar": @"Claude Code Status Bar",
                   @"agent_menubar": @"AI Agent Menu Bar" };
    });
    NSString *name = names[capability];
    if (name) {
        return name;
    }
    // Fallback: title-case the identifier so an unknown capability still reads.
    return [capability capitalizedString];
}

#pragma mark - Executable resolution

// Resolve the path to spawnterm-flag once and cache the result (including a
// negative result). Resolution order:
//   1. $SPAWNTERM_FLAG environment variable (explicit executable path).
//   2. The NoSyncSpawnTermFlagPath user default (configurable in prefs/CLI).
//   3. `command -v spawnterm-flag` run in a login shell (honors the user's PATH).
//   4. A short list of common install locations.
// Returns nil if nothing runnable is found, in which case the pane fails safe.
+ (nullable NSString *)executablePath {
    static NSString *cachedPath;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        cachedPath = [self resolveExecutablePath];
        if (cachedPath) {
            DLog(@"spawnterm-flag resolved to %@", cachedPath);
        } else {
            DLog(@"spawnterm-flag could not be located; capabilities pane will be read-only OFF");
        }
    });
    return cachedPath;
}

+ (nullable NSString *)resolveExecutablePath {
    NSFileManager *fileManager = [NSFileManager defaultManager];

    NSString *fromEnvironment = [NSProcessInfo processInfo].environment[iTermSpawnTermFlagEnvironmentVariable];
    if ([self isRunnableFile:fromEnvironment fileManager:fileManager]) {
        return fromEnvironment;
    }

    NSString *fromUserDefaults = [[NSUserDefaults standardUserDefaults] stringForKey:iTermSpawnTermFlagPathUserDefaultsKey];
    if ([self isRunnableFile:fromUserDefaults fileManager:fileManager]) {
        return fromUserDefaults;
    }

    NSString *fromShell = [self pathFromLoginShellLookup];
    if ([self isRunnableFile:fromShell fileManager:fileManager]) {
        return fromShell;
    }

    NSString *home = NSHomeDirectory();
    NSArray<NSString *> *candidates = @[ [home stringByAppendingPathComponent:@".local/bin/spawnterm-flag"],
                                         @"/opt/homebrew/bin/spawnterm-flag",
                                         @"/usr/local/bin/spawnterm-flag",
                                         @"/usr/bin/spawnterm-flag" ];
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

// Ask the user's login shell where spawnterm-flag lives. A login shell sources
// the user's profile, so PATH additions (~/.local/bin, homebrew, etc.) apply
// even though the GUI app does not inherit an interactive PATH.
+ (nullable NSString *)pathFromLoginShellLookup {
    NSString *shell = [NSProcessInfo processInfo].environment[@"SHELL"];
    if (shell.length == 0) {
        shell = @"/bin/zsh";
    }
    NSString *output = [self runExecutable:shell
                                 arguments:@[ @"-l", @"-c", @"command -v spawnterm-flag" ]
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

// Cache of capability -> @(BOOL) parsed from `spawnterm-flag list`. nil means
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
        // Each line: "spawnterm.<cap>   on|off".
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
            if (![key hasPrefix:@"spawnterm."]) {
                continue;
            }
            NSString *capability = [key substringFromIndex:@"spawnterm.".length];
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
        DLog(@"Ignoring set %@=%@: spawnterm-flag unavailable", capability, @(enabled));
        return;
    }
    NSString *subcommand = enabled ? @"enable" : @"disable";
    int status = -1;
    [self runExecutable:path arguments:@[ subcommand, capability ] exitStatus:&status];
    DLog(@"spawnterm-flag %@ %@ exited %d", subcommand, capability, status);
    // Re-query on next read so the cache reflects what the CLI actually wrote.
    [self invalidateCache];
}

@end
