//
//  iTermSessionRegistryFilter.h
//  iTerm2
//
//  spawnTerm extension (fork-only). Pure, side-effect-free helpers backing the
//  queryable session registry surfaced through ListSessions (issue #50):
//
//    * label extraction — turns a session's user-vars into a flat list of
//      "<name>=<value>" tags (SessionSummary.tags), and
//    * the match predicate — decides whether a session is included given an
//      optional label/title filter (ListSessionsRequest.filter).
//
//  This is deliberately decoupled from PTYSession, the proto types, and the
//  variables system so it can be unit tested against plain dictionaries and
//  strings. The API handler (-[iTermAPIHelper newListSessionsResponse]) reads
//  the user-vars out of each live session and hands them here.
//
//  Which user-vars become labels: only the spawnTerm agent-identity subset,
//  i.e. user-vars whose (dot-free) name begins with "agent_" — for example
//  user.agent_status, user.agent_role, user.agent_task, user.agent_id (set by
//  spawnterm-emit). Other user-vars (git state, ad-hoc scripting values) are
//  intentionally NOT exposed as labels so ListSessions stays a descriptive
//  identity read model rather than a dump of every variable. Each surviving
//  var becomes the tag "<name>=<value>" (e.g. "agent_status=running").
//

#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

// The dot-free user-var name prefix whose members are exposed as labels.
extern NSString *const iTermSessionRegistryLabelPrefix;

@interface iTermSessionRegistryFilter : NSObject

// Given a session's user variables ({"<name>": "<value>"} with dot-free names,
// as produced by -[iTermVariables stringValuedDictionary] on the "user" child),
// returns the sorted list of label tags "<name>=<value>" for the names in the
// exposed subset (see iTermSessionRegistryLabelPrefix). Non-string values and
// out-of-subset names are ignored. Never returns nil.
+ (NSArray<NSString *> *)tagsFromUserVariables:(NSDictionary<NSString *, id> *)userVariables;

// The match predicate. `tags` is the output of +tagsFromUserVariables:; `title`
// is the session title (may be nil). The three filter parameters are all
// optional (pass nil to omit); a nil/empty parameter imposes no constraint, so
// an all-nil filter matches every session (preserving the pre-filter default of
// returning everything). When present, the constraints are ANDed:
//
//   * labelKey without labelValue: the session must carry some tag whose key is
//     labelKey (i.e. a "labelKey=..." tag).
//   * labelKey with labelValue: the session must carry the exact tag
//     "labelKey=labelValue".
//   * labelValue without labelKey: the session must carry some tag whose value
//     is labelValue (i.e. an "...=labelValue" tag).
//   * titleSubstring: case-insensitive substring of the title.
+ (BOOL)tags:(NSArray<NSString *> *)tags
       title:(nullable NSString *)title
matchLabelKey:(nullable NSString *)labelKey
  labelValue:(nullable NSString *)labelValue
titleSubstring:(nullable NSString *)titleSubstring;

@end

NS_ASSUME_NONNULL_END
