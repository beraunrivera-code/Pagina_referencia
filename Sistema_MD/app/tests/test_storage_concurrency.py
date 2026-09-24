"""Regresión de carrera WAL/bootstrap; no reintenta operaciones de negocio."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import threading
from unittest import TestCase
from unittest.mock import MagicMock, patch

from conversion import storage
from tests.support import workspace_temp


class StorageConcurrencyTests(TestCase):
    def setUp(self):
        self.fixture = workspace_temp()
        self.addCleanup(self.fixture.cleanup)
        self.root = Path(self.fixture.name) / "memoria"

    def test_simultaneous_fresh_connections_close_cleanly_and_have_complete_schema(self):
        real_connect = sqlite3.connect
        for round_number in range(5):
            root = self.root / str(round_number)
            barrier = threading.Barrier(3)
            first = threading.local()
            opened = []
            def synchronized(*args, **kwargs):
                # Permite limpiar también el caso de regresión anterior en el hilo
                # de test; cada operación productiva sigue en su hilo creador.
                kwargs["check_same_thread"] = False
                db = real_connect(*args, **kwargs)
                opened.append(db)
                if not getattr(first, "started", False):
                    first.started = True
                    barrier.wait(timeout=10)
                return db
            def run(_):
                db = storage.connect(root)
                try:
                    names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    self.assertTrue({"documents", "origins", "native_cache"} <= names)
                    self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
                    self.assertEqual(db.execute("PRAGMA busy_timeout").fetchone()[0], 20000)
                    self.assertFalse(db.in_transaction)
                finally:
                    db.close()
            try:
                with patch.object(storage.sqlite3, "connect", side_effect=synchronized), ThreadPoolExecutor(3) as pool:
                    list(pool.map(run, range(3)))
            finally:
                for db in opened:
                    db.close()

    def test_busy_initialization_closes_failed_connection_and_reopens_only_bootstrap(self):
        first, second = MagicMock(), MagicMock()
        failure = sqlite3.OperationalError("database is locked")
        failure.sqlite_errorcode = sqlite3.SQLITE_BUSY
        first.execute.side_effect = failure
        second.execute.return_value.fetchone.return_value = ("wal",)
        with patch.object(storage.sqlite3, "connect", side_effect=[first, second]) as create:
            self.assertIs(storage.connect(self.root), second)
        first.close.assert_called_once()
        second.close.assert_not_called()
        self.assertEqual(create.call_count, 2)
        self.assertTrue(all(0 <= call.kwargs["timeout"] <= 20 for call in create.call_args_list))
        sql = [call.args[0] for call in second.execute.call_args_list]
        self.assertNotIn("PRAGMA journal_mode=WAL", sql)
        self.assertIn("BEGIN IMMEDIATE", sql)

    def test_nonbusy_failure_closes_connection_without_retry(self):
        db = MagicMock()
        failure = sqlite3.OperationalError("database disk image is malformed")
        failure.sqlite_errorcode = sqlite3.SQLITE_CORRUPT
        db.execute.side_effect = failure
        with patch.object(storage.sqlite3, "connect", return_value=db) as create:
            with self.assertRaises(sqlite3.OperationalError):
                storage.connect(self.root)
        self.assertEqual(create.call_count, 1)
        db.close.assert_called_once()

    def test_schema_failure_rolls_back_all_ddl_and_releases_file(self):
        real_connect = sqlite3.connect
        class FailOrigins(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                if "CREATE TABLE IF NOT EXISTS origins" in sql:
                    raise sqlite3.OperationalError("synthetic schema failure")
                return super().execute(sql, *args, **kwargs)
        def inject(*args, **kwargs):
            return real_connect(*args, **kwargs, factory=FailOrigins)
        with patch.object(storage.sqlite3, "connect", side_effect=inject):
            with self.assertRaisesRegex(sqlite3.OperationalError, "synthetic"):
                storage.connect(self.root)
        db = real_connect(self.root / "indice.sqlite")
        try:
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [])
        finally:
            db.close()
        # Reapertura normal repara la inicialización incompleta, sin cambiar datos.
        db = storage.connect(self.root)
        db.close()

    def test_busy_budget_is_original_twenty_seconds_not_unbounded(self):
        db = MagicMock()
        failure = sqlite3.OperationalError("database is locked")
        failure.sqlite_errorcode = sqlite3.SQLITE_BUSY
        db.execute.side_effect = failure
        with patch.object(storage.sqlite3, "connect", return_value=db) as create, patch("time.monotonic", side_effect=[0, 0, 20.1]), patch("time.sleep") as sleep:
            with self.assertRaises(sqlite3.OperationalError):
                storage.connect(self.root)
        self.assertEqual(create.call_count, 1)
        db.close.assert_called_once()
        sleep.assert_not_called()

    def test_existing_schema_connect_does_not_take_write_lock_under_another_writer(self):
        writer = storage.connect(self.root)
        try:
            writer.execute("BEGIN IMMEDIATE")
            traced = []
            real_connect = sqlite3.connect
            def track(*args, **kwargs):
                db = real_connect(*args, **kwargs)
                db.set_trace_callback(traced.append)
                return db
            with patch.object(storage.sqlite3, "connect", side_effect=track):
                reader = storage.connect(self.root)
            try:
                self.assertEqual(reader.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 0)
                self.assertFalse(reader.in_transaction)
                self.assertFalse(any(sql.startswith(("BEGIN", "CREATE")) for sql in traced))
            finally:
                reader.close()
        finally:
            writer.rollback()
            writer.close()
