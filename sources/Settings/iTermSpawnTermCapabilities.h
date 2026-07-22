//
//  iTermSpawnTermCapabilities.h
//  iTerm2 (spawnTerm fork)
//
//  Bridges the AI Settings pane to the spawnTerm feature-flag CLI
//  (spawnterm/flags/spawnterm-flag). The TOML at
//  ~/.config/spawnterm/config.toml is the single source of truth; this class
//  never parses or writes it directly — every read and write is delegated to
//  spawnterm-flag. All flags default OFF; if the CLI cannot be located, reads
//  return OFF and writes are no-ops so the UI fails safe.
//

#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

@interface iTermSpawnTermCapabilities : NSObject

// Ordered list of capability identifiers (no prefix), mirroring KNOWN_FLAGS in
// spawnterm-flag / spawnterm_flag.py.
@property (class, nonatomic, readonly) NSArray<NSString *> *capabilityIdentifiers;

// The synthetic preference key for a capability, e.g. "spawnterm.messaging".
+ (NSString *)preferenceKeyForCapability:(NSString *)capability;

// All synthetic preference keys, for registering placeholder defaults.
@property (class, nonatomic, readonly) NSArray<NSString *> *allPreferenceKeys;

// Human-readable label for a capability, e.g. "Worktree Isolation".
+ (NSString *)displayNameForCapability:(NSString *)capability;

// YES if spawnterm-flag was located and is runnable. When NO, the checkboxes
// should be disabled and all reads report OFF.
@property (class, nonatomic, readonly) BOOL available;

// Current on/off state of a capability, read (via a cached `list`) from the CLI.
+ (BOOL)isEnabledForCapability:(NSString *)capability;

// Toggle a capability by shelling out to `spawnterm-flag enable|disable`.
+ (void)setEnabled:(BOOL)enabled forCapability:(NSString *)capability;

// Drops the cached `list` snapshot so the next read re-queries the CLI. Call
// when the pane appears so external TOML edits (feature flags) are reflected.
+ (void)invalidateCache;

@end

NS_ASSUME_NONNULL_END
