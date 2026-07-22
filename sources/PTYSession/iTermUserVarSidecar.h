//
//  iTermUserVarSidecar.h
//  iTerm2
//
//  A tiny, per-session durable store for `user.*` variables (set via the
//  OSC 1337 SetUserVar control sequence, see -[PTYSession screenSetUserVar:]).
//
//  Motivation: today user-vars only survive an app restart when the whole-app
//  arrangement is captured *with contents* (SESSION_ARRANGEMENT_VARIABLES is
//  written inside the `if (includeContents)` guard). On an ordinary process
//  exit — or when the arrangement is saved without contents — the session is
//  deallocated and the user-vars are lost, even though the session's durable
//  stable id (SESSION_ARRANGEMENT_STABLE_ID) is always persisted. This sidecar
//  closes that gap by write-through persisting each `user.*` pair to a small
//  per-session file keyed by the stable id, so identity survives normal
//  session end and app restart independent of arrangement capture.
//
//  The store is deliberately dumb and side-effect free (no dependency on
//  PTYSession, advanced settings, or the variable system) so it can be unit
//  tested directly. Gating (opt-in advanced setting) and the decision of when
//  to replay live in the caller.
//

#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

@interface iTermUserVarSidecar : NSObject

// Directory where sidecar files live: <appSupport>/UserVarSidecars.
@property (class, readonly) NSString *defaultDirectory;

// Designated initializer. `directory` is where the sidecar file for this
// session is read/written; tests pass a temporary directory. Returns nil if
// `stableID` is not a well-formed stable session id (so a malformed id can
// never escape into a filesystem path).
- (nullable instancetype)initWithStableID:(NSString *)stableID
                                directory:(NSString *)directory NS_DESIGNATED_INITIALIZER;

// Convenience initializer using +defaultDirectory.
- (nullable instancetype)initWithStableID:(NSString *)stableID;

- (instancetype)init NS_UNAVAILABLE;

// The absolute path of this session's sidecar file (may not exist yet).
@property (nonatomic, readonly, copy) NSString *path;

// Write-through a single user variable. `key` must be a full "user.<name>"
// key with exactly the one leading dot (as produced by screenSetUserVar:). A
// nil `value` removes the key. When the sidecar becomes empty the file is
// deleted. Writing also touches the file's modification time so an active or
// recently-replayed session's sidecar is kept fresh for age-based GC.
- (void)setValue:(nullable NSString *)value forUserVariableKey:(NSString *)key;

// Every persisted user variable, as {"user.<name>": "<value>"}. Empty if none.
- (NSDictionary<NSString *, NSString *> *)userVariables;

// Remove this session's sidecar file entirely.
- (void)removeSidecar;

// Age-based GC. Deletes sidecar files in `directory` whose modification time is
// older than `maxAge` seconds AND whose stable id is not in `liveStableIDs`
// (so a live session's file is never pruned even if somehow stale). A restored
// session touches its sidecar on replay, resetting the clock, so only sidecars
// for sessions that are truly gone age out.
+ (void)pruneSidecarsInDirectory:(NSString *)directory
                 keepingStableIDs:(NSSet<NSString *> *)liveStableIDs
                        olderThan:(NSTimeInterval)maxAge;

// Convenience prune over +defaultDirectory with the default TTL.
+ (void)pruneKeepingStableIDs:(NSSet<NSString *> *)liveStableIDs;

@end

NS_ASSUME_NONNULL_END
