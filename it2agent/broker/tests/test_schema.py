#!/usr/bin/env python3
"""Tests for the broker sqlite schema layer (#34): WAL, busy timeout, and
idempotent creation/migration."""

import os
import sys
import tempfile
import time
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

    def _build_v3_db(self, conn):
        """Materialize a pre-#95 (v3) db: schema exactly as it shipped before the
        idempotency_key column existed. Applies migrations 1..3 only."""
        conn.execute(schema._META_DDL)
        for version in (1, 2, 3):
            for statement in schema.MIGRATIONS[version]:
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_version(version, applied_at) VALUES (?, ?)",
                (version, time.time()),
            )
        conn.commit()

    def test_v4_migration_upgrades_old_db_additively(self):
        # An existing db at v3 (no idempotency_key column) must upgrade in place:
        # column added, version bumped, and existing rows preserved intact.
        conn = schema.open_db(self.db)
        self.addCleanup(conn.close)
        self._build_v3_db(conn)
        self.assertEqual(schema.current_version(conn), 3)
        # Seed a legacy message on the old schema.
        conn.execute(
            "INSERT INTO messages(sender, recipient, body, created_at, state) "
            "VALUES (?, ?, ?, ?, ?)",
            ("boss", "agent1", "legacy-row", 1.0, "pending"),
        )
        conn.commit()
        cols_before = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
        self.assertNotIn("idempotency_key", cols_before)

        # Upgrade.
        self.assertEqual(schema.apply_schema(conn), schema.SCHEMA_VERSION)
        self.assertEqual(schema.SCHEMA_VERSION, 4)

        # Column is now present and nullable; the legacy row survived with NULL.
        cols_after = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
        self.assertIn("idempotency_key", cols_after)
        row = conn.execute(
            "SELECT body, idempotency_key FROM messages WHERE recipient='agent1'"
        ).fetchone()
        self.assertEqual(row["body"], "legacy-row")
        self.assertIsNone(row["idempotency_key"])

        # The partial-unique index exists.
        indexes = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        self.assertIn("idx_messages_recipient_idempotency_key", indexes)

        # Re-running the migration is a no-op (idempotent, no duplicate ALTER).
        self.assertEqual(schema.apply_schema(conn), schema.SCHEMA_VERSION)

    def test_v4_partial_unique_index_allows_many_null_keys(self):
        # The uniqueness constraint must NOT fire for keyless (NULL) rows —
        # legacy sends can pile up freely; only non-null keys are constrained.
        conn = schema.init_db(self.db)
        self.addCleanup(conn.close)
        for _ in range(3):
            conn.execute(
                "INSERT INTO messages(sender, recipient, body, created_at, state) "
                "VALUES (?, ?, ?, ?, ?)",
                ("s", "agent1", "x", 1.0, "pending"),
            )
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE recipient='agent1'"
        ).fetchone()[0]
        self.assertEqual(count, 3)


if __name__ == "__main__":
    unittest.main()
