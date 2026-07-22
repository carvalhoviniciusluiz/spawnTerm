//
//  iTermSendTextDispatchAggregator.h
//  iTerm2
//
//  spawnTerm (fork-only): supports the optional wait_for_dispatch ack on
//  async_send_text. A single send may fan out to several sessions ("all"); this
//  counting latch fires its completion exactly once after every targeted
//  session has reported that its text was handed off to the write pipeline.
//

#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

// A one-shot counting latch. `completion` runs exactly once after -signal has
// been called `count` times. When `count` is zero it runs immediately during
// initialization (nothing to wait for). This object assumes it is used from a
// single dispatch queue (the API server's main queue); it performs no locking.
@interface iTermSendTextDispatchAggregator : NSObject

- (instancetype)initWithCount:(NSUInteger)count
                   completion:(void (^)(void))completion NS_DESIGNATED_INITIALIZER;
- (instancetype)init NS_UNAVAILABLE;

// Report that one targeted session has dispatched its text. Extra signals beyond
// `count` are ignored.
- (void)signal;

@end

NS_ASSUME_NONNULL_END
