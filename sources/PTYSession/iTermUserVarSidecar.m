//
//  iTermUserVarSidecar.m
//  iTerm2
//

#import "iTermUserVarSidecar.h"

#import "DebugLogging.h"
#import "NSFileManager+iTerm.h"
#import "iTerm2SharedARC-Swift.h"

// Subdirectory of Application Support holding per-session sidecar files.
static NSString *const iTermUserVarSidecarDirectoryName = @"UserVarSidecars";

// File extension for a sidecar plist.
static NSString *const iTermUserVarSidecarExtension = @"plist";

// Default time-to-live for an orphaned sidecar (a session that no longer
// exists): 30 days. A live or recently-replayed session refreshes its file's
// modification time, so only truly abandoned sidecars age out.
static const NSTimeInterval iTermUserVarSidecarDefaultMaxAge = 30 * 24 * 60 * 60;

@implementation iTermUserVarSidecar {
    NSString *_canonicalStableID;
    NSString *_directory;
    NSString *_path;
}

+ (NSString *)defaultDirectory {
    NSString *appSupport = [[NSFileManager defaultManager] applicationSupportDirectory];
    return [appSupport stringByAppendingPathComponent:iTermUserVarSidecarDirectoryName];
}

- (nullable instancetype)initWithStableID:(NSString *)stableID {
    return [self initWithStableID:stableID directory:[iTermUserVarSidecar defaultDirectory]];
}

- (nullable instancetype)initWithStableID:(NSString *)stableID
                                directory:(NSString *)directory {
    // Validate/normalize the stable id before it ever becomes a path component.
    // A malformed id (checksum/alphabet failure) is rejected so it cannot escape
    // into the filesystem or collide via case/confusable folding.
    NSString *canonical = [iTermStableSessionID canonical:stableID];
    if (!canonical) {
        DLog(@"Refusing to create sidecar for invalid stableID %@", stableID);
        return nil;
    }
    self = [super init];
    if (self) {
        _canonicalStableID = [canonical copy];
        _directory = [directory copy];
        _path = [[directory stringByAppendingPathComponent:canonical]
                    stringByAppendingPathExtension:iTermUserVarSidecarExtension];
    }
    return self;
}

- (NSString *)path {
    return _path;
}

#pragma mark - Read/write

// Caller must hold the class lock.
- (NSMutableDictionary<NSString *, NSString *> *)loadLocked {
    NSDictionary *onDisk = [NSDictionary dictionaryWithContentsOfFile:_path];
    if (![onDisk isKindOfClass:[NSDictionary class]]) {
        return [NSMutableDictionary dictionary];
    }
    // Defensively keep only string→string pairs; a corrupt/foreign file yields
    // an empty store rather than propagating garbage into the variable system.
    NSMutableDictionary<NSString *, NSString *> *result = [NSMutableDictionary dictionary];
    [onDisk enumerateKeysAndObjectsUsingBlock:^(id key, id obj, BOOL *stop) {
        if ([key isKindOfClass:[NSString class]] && [obj isKindOfClass:[NSString class]]) {
            result[key] = obj;
        }
    }];
    return result;
}

- (void)setValue:(nullable NSString *)value forUserVariableKey:(NSString *)key {
    if (![key isKindOfClass:[NSString class]] || ![key hasPrefix:@"user."]) {
        DLog(@"Ignoring non-user key %@ in sidecar", key);
        return;
    }
    @synchronized([iTermUserVarSidecar class]) {
        NSMutableDictionary<NSString *, NSString *> *dict = [self loadLocked];
        if (value == nil) {
            [dict removeObjectForKey:key];
        } else {
            dict[key] = value;
        }
        if (dict.count == 0) {
            // Nothing left to persist: remove the file so an emptied session
            // leaves no stale sidecar behind.
            [[NSFileManager defaultManager] removeItemAtPath:_path error:nil];
            return;
        }
        [self ensureDirectoryLocked];
        if (![dict writeToFile:_path atomically:YES]) {
            DLog(@"Failed to write sidecar to %@", _path);
        }
    }
}

- (NSDictionary<NSString *, NSString *> *)userVariables {
    @synchronized([iTermUserVarSidecar class]) {
        NSMutableDictionary<NSString *, NSString *> *dict = [self loadLocked];
        if (dict.count > 0) {
            // Refresh the modification time so a session that replays its
            // sidecar (i.e. is still in use) is not later pruned as an orphan.
            [self touchLocked];
        }
        return dict;
    }
}

- (void)removeSidecar {
    @synchronized([iTermUserVarSidecar class]) {
        [[NSFileManager defaultManager] removeItemAtPath:_path error:nil];
    }
}

#pragma mark - Filesystem helpers (caller holds class lock)

- (void)ensureDirectoryLocked {
    NSFileManager *fm = [NSFileManager defaultManager];
    BOOL isDir = NO;
    if ([fm fileExistsAtPath:_directory isDirectory:&isDir] && isDir) {
        return;
    }
    NSError *error = nil;
    if (![fm createDirectoryAtPath:_directory
       withIntermediateDirectories:YES
                        attributes:nil
                             error:&error]) {
        DLog(@"Failed to create sidecar directory %@: %@", _directory, error);
    }
}

- (void)touchLocked {
    [[NSFileManager defaultManager] setAttributes:@{ NSFileModificationDate: [NSDate date] }
                                     ofItemAtPath:_path
                                            error:NULL];
}

#pragma mark - GC

+ (void)pruneKeepingStableIDs:(NSSet<NSString *> *)liveStableIDs {
    [self pruneSidecarsInDirectory:[self defaultDirectory]
                  keepingStableIDs:liveStableIDs
                         olderThan:iTermUserVarSidecarDefaultMaxAge];
}

+ (void)pruneSidecarsInDirectory:(NSString *)directory
                 keepingStableIDs:(NSSet<NSString *> *)liveStableIDs
                        olderThan:(NSTimeInterval)maxAge {
    @synchronized([iTermUserVarSidecar class]) {
        NSFileManager *fm = [NSFileManager defaultManager];
        NSArray<NSString *> *entries = [fm contentsOfDirectoryAtPath:directory error:nil];
        if (!entries) {
            return;
        }
        // Normalize the live set to canonical form so comparison matches the
        // canonical filenames we write.
        NSMutableSet<NSString *> *liveCanonical = [NSMutableSet set];
        for (NSString *raw in liveStableIDs) {
            NSString *canonical = [iTermStableSessionID canonical:raw];
            if (canonical) {
                [liveCanonical addObject:canonical];
            }
        }
        NSDate *now = [NSDate date];
        for (NSString *entry in entries) {
            if (![entry.pathExtension isEqualToString:iTermUserVarSidecarExtension]) {
                continue;
            }
            NSString *stableID = entry.stringByDeletingPathExtension;
            if ([liveCanonical containsObject:stableID]) {
                // Never prune a live session's sidecar.
                continue;
            }
            NSString *fullPath = [directory stringByAppendingPathComponent:entry];
            NSDictionary *attrs = [fm attributesOfItemAtPath:fullPath error:nil];
            NSDate *modified = attrs[NSFileModificationDate];
            if (modified && [now timeIntervalSinceDate:modified] < maxAge) {
                // Still fresh; keep it in case its session is restored later.
                continue;
            }
            DLog(@"Pruning orphaned user-var sidecar %@", fullPath);
            [fm removeItemAtPath:fullPath error:nil];
        }
    }
}

@end
