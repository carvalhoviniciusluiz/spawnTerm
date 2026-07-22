#!/usr/bin/env python3
"""Tests for the broker sqlite schema layer (#34): WAL, busy timeout, and
idempotent creation/migration."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import schema  # noqa: E402


class TestSchema(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "sub", "broker.db")

    def tearDown(self):
        self._tmp.cleanup()

    def test_open_creates_parent_dirs(self):
        conn = schema.open_db(self.db)
        self.addCleanup(conn.close)
        self.assertTrue(os.path.isdir(os.path.dirname(self.db)))

    def test_wal_mode_is_set(self):
        conn = schema.open_db(self.db)
        self.addCleanup(conn.close)
        self.assertEqual(schema.journal_mode(conn), "wal")

    def test_busy_timeout_is_set(self):
        conn = schema.open_db(self.db)
        self.addCleanup(conn.close)
        row = conn.execute("PRAGMA busy_timeout").fetchone()
        self.assertEqual(int(row[0]), schema.BUSY_TIMEOUT_MS)

    def test_apply_schema_reaches_current_version(self):
        conn = schema.open_db(self.db)
        self.addCleanup(conn.close)
        version = schema.apply_schema(conn)
        self.assertEqual(version, schema.SCHEMA_VERSION)
        self.assertEqual(schema.current_version(conn), schema.SCHEMA_VERSION)

    def test_apply_schema_is_idempotent(self):
        conn = schema.open_db(self.db)
        self.addCleanup(conn.close)
        first = schema.apply_schema(conn)
        # Running again must not error, duplicate rows, or bump the version.
        second = schema.apply_schema(conn)
        self.assertEqual(first, second)
        rows = conn.execute(
            "SELECT COUNT(*) FROM schema_version WHERE version = ?",
            (schema.SCHEMA_VERSION,),
        ).fetchone()[0]
        self.assertEqual(rows, 1)

    def test_idempotent_across_reopen(self):
        # First process migrates; a second open of the same file is a no-op.
        conn1 = schema.init_db(self.db)
        conn1.close()
        conn2 = schema.init_db(self.db)
        self.addCleanup(conn2.close)
        self.assertEqual(schema.current_version(conn2), schema.SCHEMA_VERSION)

    def test_current_version_zero_before_meta(self):
        conn = schema.open_db(self.db)
        self.addCleanup(conn.close)
        # No schema applied yet -> version 0 (no meta table).
        self.assertEqual(schema.current_version(conn), 0)


if __name__ == "__main__":
    unittest.main()
