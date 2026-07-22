//
//  iTermSessionRegistryFilter.m
//  iTerm2
//
//  spawnTerm extension (fork-only). See iTermSessionRegistryFilter.h.
//

#import "iTermSessionRegistryFilter.h"

NSString *const iTermSessionRegistryLabelPrefix = @"agent_";

@implementation iTermSessionRegistryFilter

+ (NSArray<NSString *> *)tagsFromUserVariables:(NSDictionary<NSString *, id> *)userVariables {
    NSMutableArray<NSString *> *tags = [NSMutableArray array];
    for (NSString *name in userVariables) {
        if (![name isKindOfClass:[NSString class]]) {
            continue;
        }
        if (![name hasPrefix:iTermSessionRegistryLabelPrefix]) {
            continue;
        }
        id value = userVariables[name];
        if (![value isKindOfClass:[NSString class]]) {
            continue;
        }
        [tags addObject:[NSString stringWithFormat:@"%@=%@", name, (NSString *)value]];
    }
    [tags sortUsingSelector:@selector(compare:)];
    return tags;
}

+ (BOOL)tags:(NSArray<NSString *> *)tags
       title:(NSString *)title
matchLabelKey:(NSString *)labelKey
  labelValue:(NSString *)labelValue
titleSubstring:(NSString *)titleSubstring {
    const BOOL haveKey = labelKey.length > 0;
    const BOOL haveValue = labelValue.length > 0;
    if (haveKey || haveValue) {
        // Reconstruct the "<key>=<value>" we would expect (or its key/value
        // halves) and require at least one tag to satisfy the constraint.
        BOOL matched = NO;
        for (NSString *tag in tags) {
            const NSRange eq = [tag rangeOfString:@"="];
            if (eq.location == NSNotFound) {
                continue;
            }
            NSString *tagKey = [tag substringToIndex:eq.location];
            NSString *tagValue = [tag substringFromIndex:NSMaxRange(eq)];
            if (haveKey && ![tagKey isEqualToString:labelKey]) {
                continue;
            }
            if (haveValue && ![tagValue isEqualToString:labelValue]) {
                continue;
            }
            matched = YES;
            break;
        }
        if (!matched) {
            return NO;
        }
    }
    if (titleSubstring.length > 0) {
        if (title == nil) {
            return NO;
        }
        const NSRange r = [title rangeOfString:titleSubstring
                                       options:NSCaseInsensitiveSearch];
        if (r.location == NSNotFound) {
            return NO;
        }
    }
    return YES;
}

@end
