//
//  iTermSendTextDispatchAggregatorTests.m
//  ModernTests
//
//  Unit coverage for the spawnTerm fork-only wait_for_dispatch ack on
//  async_send_text. iTermSendTextDispatchAggregator is the fan-out latch the API
//  handler uses to fire a single deferred SendTextResponse once every targeted
//  session has reported its text handed off to the write pipeline. The handler's
//  own branch is exercised indirectly here by modeling the exact call sequence it
//  makes; the PTY write path itself needs a live session and is out of scope.
//

#import <XCTest/XCTest.h>

#import "iTermSendTextDispatchAggregator.h"

@interface iTermSendTextDispatchAggregatorTests : XCTestCase
@end

@implementation iTermSendTextDispatchAggregatorTests

// One target that dispatches once: completion fires exactly once, and only after
// the signal (mirrors a single-session wait_for_dispatch send).
- (void)testSingleTargetFiresOnceAfterSignal {
    __block NSInteger fired = 0;
    iTermSendTextDispatchAggregator *aggregator =
        [[iTermSendTextDispatchAggregator alloc] initWithCount:1
                                                    completion:^{
        fired += 1;
    }];
    XCTAssertEqual(fired, 0, @"Must not fire before the target dispatches");
    [aggregator signal];
    XCTAssertEqual(fired, 1, @"Must fire once the single target dispatches");
}

// Multi-session "all" fan-out: the ack aggregates and fires exactly once, only
// after the last of N targets dispatches.
- (void)testFanOutFiresOnceAfterLastSignal {
    __block NSInteger fired = 0;
    const NSUInteger count = 3;
    iTermSendTextDispatchAggregator *aggregator =
        [[iTermSendTextDispatchAggregator alloc] initWithCount:count
                                                    completion:^{
        fired += 1;
    }];
    for (NSUInteger i = 0; i < count; i++) {
        XCTAssertEqual(fired, 0, @"Must not fire until every target dispatches");
        [aggregator signal];
    }
    XCTAssertEqual(fired, 1, @"Must fire exactly once after the last target dispatches");
}

// No writable targets (e.g. an "all" send that resolves to only browser
// sessions): the ack fires immediately during init so the caller never hangs.
- (void)testZeroTargetsFiresImmediately {
    __block NSInteger fired = 0;
    iTermSendTextDispatchAggregator *aggregator __attribute__((unused)) =
        [[iTermSendTextDispatchAggregator alloc] initWithCount:0
                                                    completion:^{
        fired += 1;
    }];
    XCTAssertEqual(fired, 1, @"Zero targets must fire the completion immediately");
}

// Extra signals beyond the count are ignored: the completion never fires twice.
- (void)testExtraSignalsAreIgnored {
    __block NSInteger fired = 0;
    iTermSendTextDispatchAggregator *aggregator =
        [[iTermSendTextDispatchAggregator alloc] initWithCount:2
                                                    completion:^{
        fired += 1;
    }];
    [aggregator signal];
    [aggregator signal];
    [aggregator signal];  // extra
    [aggregator signal];  // extra
    XCTAssertEqual(fired, 1, @"Completion must fire exactly once regardless of extra signals");
}

@end
