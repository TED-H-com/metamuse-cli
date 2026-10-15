"""Migration errors retain the same internal contention classification as opens."""

import sqlite3

import pytest

from metamuse import store


@pytest.mark.parametrize("location", ["verify", "begin"])
def test_migrate_classifies_busy(ctx, monkeypatch, location):
    error = sqlite3.OperationalError("synthetic unrelated text")
    error.sqlite_errorcode = sqlite3.SQLITE_BUSY_RECOVERY

    if location == "verify":

        def verify(*args):
            raise error

        monkeypatch.setattr(store, "_verify_schema", verify)
    else:
        # Drive the migration BEGIN path without changing a schema or a migration.
        original = ctx.store._open

        class Connection:
            def __init__(self):
                self.conn = original()

            def execute(self, sql, *args):
                if sql == "PRAGMA user_version":
                    return self.conn.execute("SELECT 0")
                if sql == "BEGIN IMMEDIATE":
                    raise error
                return self.conn.execute(sql, *args)

            def close(self):
                self.conn.close()

        monkeypatch.setattr(ctx.store, "_open", Connection)
        monkeypatch.setattr(store, "_verify_schema", lambda *args: None)
        monkeypatch.setattr(ctx.store, "_backup", lambda *args, **kwargs: None)
        # Avoid backup setup: only the observed BEGIN is relevant to error mapping.
        monkeypatch.setattr(ctx.store, "_record_upgrade_backup", lambda *args: None)
        monkeypatch.setattr(store, "_warn_backup", lambda *args, **kwargs: None)

        class Backup:
            warnings = []

        monkeypatch.setattr(ctx.store, "_backup", lambda *args, **kwargs: Backup())

    with pytest.raises(store._StorageBusy) as caught:
        ctx.store.migrate(ctx.store._migrations)
    assert caught.value.sqlite_errorcode == sqlite3.SQLITE_BUSY_RECOVERY
