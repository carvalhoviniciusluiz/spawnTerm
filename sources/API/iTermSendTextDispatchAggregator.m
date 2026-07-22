//
//  iTermSendTextDispatchAggregator.m
//  iTerm2
//

#import "iTermSendTextDispatchAggregator.h"

@implementation iTermSendTextDispatchAggregator {
    NSUInteger _remaining;
    void (^_completion)(void);
}

- (instancetype)initWithCount:(NSUInteger)count
                   completion:(void (^)(void))completion {
    self = [super init];
    if (self) {
        _remaining = count;
        _completion = [completion copy];
        if (_remaining == 0) {
            [self fire];
        }
    }
    return self;
}

- (void)signal {
    if (_remaining == 0) {
        // Already fired (or nothing to wait for). Ignore extra signals.
        return;
    }
    _remaining -= 1;
    if (_remaining == 0) {
        [self fire];
    }
}

- (void)fire {
    void (^completion)(void) = _completion;
    _completion = nil;
    if (completion) {
        completion();
    }
}

@end
