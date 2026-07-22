//
//  UserVarSidecarTests.swift
//  iTerm2 ModernTests
//
//  Round-trip, deletion, key-handling, invalid-id, and GC coverage for
//  iTermUserVarSidecar — the durable per-session store that lets user.* vars
//  (issue #51) survive session end and app restart independent of arrangement
//  capture. The store is exercised directly against a temporary directory so
//  no PTYSession or real Application Support state is involved.
//

import XCTest
@testable import iTerm2SharedARC

final class UserVarSidecarTests: XCTestCase {
    private var dir: String = ""

    override func setUpWithError() throws {
        dir = (NSTemporaryDirectory() as NSString)
            .appendingPathComponent("UserVarSidecarTests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(atPath: dir,
                                                withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(atPath: dir)
    }

    private func makeSidecar(_ stableID: String) -> iTermUserVarSidecar {
        // Force-unwrap: every caller passes a freshly generated (valid) id.
        return iTermUserVarSidecar(stableID: stableID, directory: dir)!
    }

    // Write two vars, read them back, and confirm they persist to a brand-new
    // sidecar object with the same id+dir (i.e. survive across "sessions").
    func testWriteReadRoundTrip() {
        let id = StableSessionID.generate()
        let sidecar = makeSidecar(id)
        sidecar.setValue("running", forUserVariableKey: "user.agent_status")
        sidecar.setValue("reviewer", forUserVariableKey: "user.agent_role")

        XCTAssertEqual(sidecar.userVariables(),
                       ["user.agent_status": "running", "user.agent_role": "reviewer"])

        // A fresh object reading the same file sees the same values (durability).
        let reopened = makeSidecar(id)
        XCTAssertEqual(reopened.userVariables(),
                       ["user.agent_status": "running", "user.agent_role": "reviewer"])
    }

    // Overwriting a key replaces its value.
    func testOverwrite() {
        let sidecar = makeSidecar(StableSessionID.generate())
        sidecar.setValue("first", forUserVariableKey: "user.k")
        sidecar.setValue("second", forUserVariableKey: "user.k")
        XCTAssertEqual(sidecar.userVariables(), ["user.k": "second"])
    }

    // Setting a value to nil removes just that key; emptying the store deletes
    // the file so no stale sidecar is left behind.
    func testDeletion() {
        let id = StableSessionID.generate()
        let sidecar = makeSidecar(id)
        sidecar.setValue("a", forUserVariableKey: "user.one")
        sidecar.setValue("b", forUserVariableKey: "user.two")

        sidecar.setValue(nil, forUserVariableKey: "user.one")
        XCTAssertEqual(sidecar.userVariables(), ["user.two": "b"])
        XCTAssertTrue(FileManager.default.fileExists(atPath: sidecar.path))

        // Removing the last key deletes the file entirely.
        sidecar.setValue(nil, forUserVariableKey: "user.two")
        XCTAssertEqual(sidecar.userVariables(), [:])
        XCTAssertFalse(FileManager.default.fileExists(atPath: sidecar.path))
    }

    // The store round-trips well-formed dot-free names (the shape screenSetUserVar
    // produces: a single leading "user." with no further dots in the name).
    func testDotFreeUserKeyRoundTrips() {
        let sidecar = makeSidecar(StableSessionID.generate())
        sidecar.setValue("abc123", forUserVariableKey: "user.agent_id")
        XCTAssertEqual(sidecar.userVariables(), ["user.agent_id": "abc123"])
    }

    // Keys not under user.* are ignored (the sidecar only mirrors user vars).
    func testNonUserKeyIgnored() {
        let sidecar = makeSidecar(StableSessionID.generate())
        sidecar.setValue("x", forUserVariableKey: "session.name")
        XCTAssertEqual(sidecar.userVariables(), [:])
        XCTAssertFalse(FileManager.default.fileExists(atPath: sidecar.path))
    }

    // A malformed stable id never yields a sidecar, so it cannot escape into a
    // filesystem path.
    func testInvalidStableIDReturnsNil() {
        XCTAssertNil(iTermUserVarSidecar(stableID: "not-a-stable-id", directory: dir))
        XCTAssertNil(iTermUserVarSidecar(stableID: "", directory: dir))
        // A truncated/checksum-broken id is rejected too.
        XCTAssertNil(iTermUserVarSidecar(stableID: "ptys_AAAA", directory: dir))
    }

    // GC removes an orphaned (not-live) sidecar older than the TTL, keeps a live
    // one even when it is stale, and keeps a fresh orphan (still within TTL).
    func testPruneRemovesStaleOrphansKeepsLiveAndFresh() throws {
        let liveID = StableSessionID.generate()
        let staleOrphanID = StableSessionID.generate()
        let freshOrphanID = StableSessionID.generate()

        for id in [liveID, staleOrphanID, freshOrphanID] {
            makeSidecar(id).setValue("v", forUserVariableKey: "user.k")
        }

        // Backdate the two orphans well beyond the TTL used below.
        let old = Date(timeIntervalSinceNow: -3600)
        for id in [staleOrphanID, freshOrphanID] {
            let path = makeSidecar(id).path
            try FileManager.default.setAttributes([.modificationDate: old],
                                                  ofItemAtPath: path)
        }
        // Re-freshen only the "fresh" orphan.
        let freshPath = makeSidecar(freshOrphanID).path
        try FileManager.default.setAttributes([.modificationDate: Date()],
                                              ofItemAtPath: freshPath)

        // Prune with a 60s TTL, keeping only the live session.
        iTermUserVarSidecar.pruneSidecars(inDirectory: dir,
                                          keepingStableIDs: [liveID],
                                          olderThan: 60)

        XCTAssertTrue(FileManager.default.fileExists(atPath: makeSidecar(liveID).path),
                      "live session sidecar must be kept")
        XCTAssertTrue(FileManager.default.fileExists(atPath: freshPath),
                      "fresh orphan within TTL must be kept")
        XCTAssertFalse(FileManager.default.fileExists(atPath: makeSidecar(staleOrphanID).path),
                       "stale orphan beyond TTL must be pruned")
    }
}
