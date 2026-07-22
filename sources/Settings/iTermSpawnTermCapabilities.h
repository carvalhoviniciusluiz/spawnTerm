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

// The synthetic preference key backing the language popup. Never persisted to
// NSUserDefaults (config.toml owns the value via spawnterm-lang); a placeholder
// string default is registered only so -defineControl: accepts the key.
@property (class, nonatomic, readonly) NSString *languagePreferenceKey;

// Human-readable label for a capability, e.g. "Worktree Isolation". This is the
// built-in English map used as the localization fail-safe.
+ (NSString *)displayNameForCapability:(NSString *)capability;

// Localized display name for a capability, resolved via `spawnterm-i18n t
// cap.<cap>.name` in the active language. Falls back to displayNameForCapability:
// when spawnterm-i18n is unavailable or the key is missing, so labels are never
// blank and never crash.
+ (NSString *)localizedNameForCapability:(NSString *)capability;

// Localized one-line description via `spawnterm-i18n t cap.<cap>.desc` (used as a
// checkbox tooltip). Returns nil when spawnterm-i18n is unavailable or the key is
// missing (there is no built-in English description map).
+ (nullable NSString *)localizedDescForCapability:(NSString *)capability;

// YES if spawnterm-flag was located and is runnable. When NO, the checkboxes
// should be disabled and all reads report OFF.
@property (class, nonatomic, readonly) BOOL available;

// Current on/off state of a capability, read (via a cached `list`) from the CLI.
+ (BOOL)isEnabledForCapability:(NSString *)capability;

// Toggle a capability by shelling out to `spawnterm-flag enable|disable`.
+ (void)setEnabled:(BOOL)enabled forCapability:(NSString *)capability;

// Drops the cached `list` snapshot and the cached localized strings so the next
// read re-queries the CLIs. Call when the pane appears so external TOML edits
// (feature flags or the active language) are reflected.
+ (void)invalidateCache;

#pragma mark - Language

// Supported UI language codes, in menu order: en, pt-BR, system.
@property (class, nonatomic, readonly) NSArray<NSString *> *languageCodes;

// Human-readable label for a language code. Real languages use their endonym
// ("English", "Português"); the "system" pseudo-language uses a localized label
// (via spawnterm-i18n, falling back to "System").
+ (NSString *)displayNameForLanguageCode:(NSString *)code;

// Localized label for the language picker field itself ("Language"/"Idioma"),
// via spawnterm-i18n; falls back to "Language".
@property (class, nonatomic, readonly) NSString *languageFieldLabel;

// The RAW stored language code (en/pt-BR/system) via `spawnterm-lang current` —
// NOT the resolved active language. Falls back to "en" when the CLI is
// unavailable so the picker always has a valid selection.
@property (class, nonatomic, readonly) NSString *currentLanguageCode;

// Persist the language via `spawnterm-lang set <code>`. No-op if spawnterm-lang
// is unavailable. Invalidates caches so localized labels re-resolve.
+ (void)setCurrentLanguageCode:(NSString *)code;

// YES if spawnterm-lang was located and is runnable; when NO the picker is disabled.
@property (class, nonatomic, readonly) BOOL languageAvailable;

@end

NS_ASSUME_NONNULL_END
