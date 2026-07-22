//
//  iTermSessionRegistryFilterTests.m
//  ModernTests
//
//  Unit coverage for the spawnTerm fork-only queryable session registry
//  (issue #50). iTermSessionRegistryFilter is the pure logic behind
//  SessionSummary.tags (label extraction from user-vars) and
//  ListSessionsRequest.filter (the include/exclude predicate). It is decoupled
//  from PTYSession and the proto types so it can be tested against plain
//  dictionaries and strings; the API handler's tree walking/pruning needs live
//  sessions and is out of scope here.
//

#import <XCTest/XCTest.h>

#import "iTermSessionRegistryFilter.h"

@interface iTermSessionRegistryFilterTests : XCTestCase
@end

@implementation iTermSessionRegistryFilterTests

#pragma mark - Label extraction

// Only agent_* user-vars become tags, formatted "<name>=<value>" and sorted.
- (void)testTagsExtractsAgentSubsetSorted {
    NSDictionary *userVars = @{
        @"agent_status": @"running",
        @"agent_role": @"worker",
        @"gitBranch": @"main",         // not agent_* -> excluded
        @"hostname": @"box",           // not agent_* -> excluded
    };
    NSArray<NSString *> *tags = [iTermSessionRegistryFilter tagsFromUserVariables:userVars];
    XCTAssertEqualObjects(tags, (@[ @"agent_role=worker", @"agent_status=running" ]));
}

// No agent_* vars -> empty (not nil).
- (void)testTagsEmptyWhenNoAgentVars {
    NSArray<NSString *> *tags =
        [iTermSessionRegistryFilter tagsFromUserVariables:@{ @"gitBranch": @"main" }];
    XCTAssertNotNil(tags);
    XCTAssertEqual(tags.count, 0);
}

// Empty input -> empty output.
- (void)testTagsEmptyInput {
    XCTAssertEqual([iTermSessionRegistryFilter tagsFromUserVariables:@{}].count, 0);
}

// Non-string values are ignored (defensive; user-vars are string-valued).
- (void)testTagsIgnoresNonStringValues {
    NSDictionary *userVars = @{ @"agent_status": @(42), @"agent_role": @"worker" };
    NSArray<NSString *> *tags = [iTermSessionRegistryFilter tagsFromUserVariables:userVars];
    XCTAssertEqualObjects(tags, (@[ @"agent_role=worker" ]));
}

#pragma mark - Filter predicate

// An all-nil filter matches everything (preserves the "return everything"
// default when ListSessionsRequest.filter is absent).
- (void)testEmptyFilterMatchesAll {
    XCTAssertTrue([iTermSessionRegistryFilter tags:@[ @"agent_role=worker" ]
                                             title:@"zsh"
                                     matchLabelKey:nil
                                        labelValue:nil
                                    titleSubstring:nil]);
    XCTAssertTrue([iTermSessionRegistryFilter tags:@[]
                                             title:nil
                                     matchLabelKey:nil
                                        labelValue:nil
                                    titleSubstring:nil]);
}

// key+value requires the exact "key=value" tag.
- (void)testLabelKeyValueExactMatch {
    NSArray *tags = @[ @"agent_role=worker", @"agent_status=running" ];
    XCTAssertTrue([iTermSessionRegistryFilter tags:tags title:nil
                                     matchLabelKey:@"agent_role" labelValue:@"worker"
                                    titleSubstring:nil]);
    XCTAssertFalse([iTermSessionRegistryFilter tags:tags title:nil
                                      matchLabelKey:@"agent_role" labelValue:@"boss"
                                     titleSubstring:nil]);
}

// key alone requires any tag with that key.
- (void)testLabelKeyOnly {
    NSArray *tags = @[ @"agent_status=running" ];
    XCTAssertTrue([iTermSessionRegistryFilter tags:tags title:nil
                                     matchLabelKey:@"agent_status" labelValue:nil
                                    titleSubstring:nil]);
    XCTAssertFalse([iTermSessionRegistryFilter tags:tags title:nil
                                      matchLabelKey:@"agent_role" labelValue:nil
                                     titleSubstring:nil]);
}

// value alone requires any tag with that value.
- (void)testLabelValueOnly {
    NSArray *tags = @[ @"agent_status=running" ];
    XCTAssertTrue([iTermSessionRegistryFilter tags:tags title:nil
                                     matchLabelKey:nil labelValue:@"running"
                                    titleSubstring:nil]);
    XCTAssertFalse([iTermSessionRegistryFilter tags:tags title:nil
                                      matchLabelKey:nil labelValue:@"stopped"
                                     titleSubstring:nil]);
}

// No tags at all -> any label constraint fails.
- (void)testLabelConstraintFailsWithNoTags {
    XCTAssertFalse([iTermSessionRegistryFilter tags:@[] title:@"zsh"
                                      matchLabelKey:@"agent_role" labelValue:@"worker"
                                     titleSubstring:nil]);
}

// Title substring is case-insensitive.
- (void)testTitleSubstringCaseInsensitive {
    XCTAssertTrue([iTermSessionRegistryFilter tags:@[] title:@"MyBuildLog"
                                     matchLabelKey:nil labelValue:nil
                                    titleSubstring:@"buildlog"]);
    XCTAssertFalse([iTermSessionRegistryFilter tags:@[] title:@"MyBuildLog"
                                      matchLabelKey:nil labelValue:nil
                                     titleSubstring:@"deploy"]);
}

// A nil title cannot satisfy a title-substring constraint.
- (void)testTitleSubstringWithNilTitleFails {
    XCTAssertFalse([iTermSessionRegistryFilter tags:@[] title:nil
                                      matchLabelKey:nil labelValue:nil
                                     titleSubstring:@"anything"]);
}

// Constraints are ANDed: label matches but title does not -> excluded.
- (void)testConstraintsAreAnded {
    NSArray *tags = @[ @"agent_role=worker" ];
    XCTAssertTrue([iTermSessionRegistryFilter tags:tags title:@"build server"
                                     matchLabelKey:@"agent_role" labelValue:@"worker"
                                    titleSubstring:@"build"]);
    XCTAssertFalse([iTermSessionRegistryFilter tags:tags title:@"build server"
                                      matchLabelKey:@"agent_role" labelValue:@"worker"
                                     titleSubstring:@"deploy"]);
}

@end
