from __future__ import annotations

import sqlite3


def migrate_enabled_fired_to_status(connection: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(schedule_entries)").fetchall()
    }
    if "fired" not in columns and "enabled" not in columns:
        return

    if "status" not in columns:
        connection.execute(
            "ALTER TABLE schedule_entries ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'"
        )

    if "fired" in columns and "enabled" in columns:
        connection.execute(
            """
            UPDATE schedule_entries
            SET status = CASE
                WHEN fired = 1 THEN 'fired'
                WHEN enabled = 0 THEN 'disabled'
                ELSE 'pending'
            END
            """
        )

    for col in ("fired", "enabled"):
        if col in columns:
            connection.executescript(
                f"""
                CREATE TABLE schedule_entries_backup AS SELECT
                    id, media_id, start_at, duration, hard_sync, status, one_shot, position
                FROM schedule_entries;
                DROP TABLE schedule_entries;
                ALTER TABLE schedule_entries_backup RENAME TO schedule_entries;
                CREATE INDEX IF NOT EXISTS idx_schedule_entries_position
                    ON schedule_entries(position);
                """
            )
            break

    connection.commit()


def migrate_schedule_entries_for_cron(connection: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(schedule_entries)").fetchall()
    }
    if "cron_id" not in columns:
        connection.execute("ALTER TABLE schedule_entries ADD COLUMN cron_id TEXT")
    if "cron_status_override" not in columns:
        connection.execute("ALTER TABLE schedule_entries ADD COLUMN cron_status_override TEXT")
    if "cron_hard_sync_override" not in columns:
        connection.execute("ALTER TABLE schedule_entries ADD COLUMN cron_hard_sync_override TEXT")
    if "cron_fade_in_override" not in columns:
        connection.execute("ALTER TABLE schedule_entries ADD COLUMN cron_fade_in_override TEXT")
    if "cron_fade_out_override" not in columns:
        connection.execute("ALTER TABLE schedule_entries ADD COLUMN cron_fade_out_override TEXT")
    connection.commit()


def migrate_media_items_greenwich_time_signal(connection: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(media_items)").fetchall()
    }
    if "greenwich_time_signal_enabled" not in columns:
        connection.execute(
            """
            ALTER TABLE media_items
            ADD COLUMN greenwich_time_signal_enabled TEXT NOT NULL DEFAULT 'False'
            """
        )
    connection.commit()


def migrate_schedule_entries_fade_flags(connection: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(schedule_entries)").fetchall()
    }
    if "fade_in" not in columns:
        connection.execute(
            "ALTER TABLE schedule_entries ADD COLUMN fade_in TEXT NOT NULL DEFAULT 'False'"
        )
    if "fade_out" not in columns:
        connection.execute(
            "ALTER TABLE schedule_entries ADD COLUMN fade_out TEXT NOT NULL DEFAULT 'False'"
        )
    connection.commit()


def migrate_cron_entries_fade_flags(connection: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(cron_entries)").fetchall()
    }
    if "fade_in" not in columns:
        connection.execute(
            "ALTER TABLE cron_entries ADD COLUMN fade_in TEXT NOT NULL DEFAULT 'False'"
        )
    if "fade_out" not in columns:
        connection.execute(
            "ALTER TABLE cron_entries ADD COLUMN fade_out TEXT NOT NULL DEFAULT 'False'"
        )
    connection.commit()


def migrate_boolean_storage_to_text(connection: sqlite3.Connection) -> None:
    truthy = "1, '1', 'true', 'TRUE', 'True', 'yes', 'YES', 'Yes', 'on', 'ON', 'On'"
    with connection:
        connection.execute(
            f"""
            UPDATE media_items
            SET greenwich_time_signal_enabled = CASE
                WHEN greenwich_time_signal_enabled IN ({truthy}) THEN 'True'
                ELSE 'False'
            END
            """
        )
        connection.execute(
            f"""
            UPDATE schedule_entries
            SET hard_sync = CASE
                WHEN hard_sync IN ({truthy}) THEN 'True'
                ELSE 'False'
            END
            """
        )
        connection.execute(
            f"""
            UPDATE schedule_entries
            SET fade_in = CASE
                WHEN fade_in IN ({truthy}) THEN 'True'
                ELSE 'False'
            END
            """
        )
        connection.execute(
            f"""
            UPDATE schedule_entries
            SET fade_out = CASE
                WHEN fade_out IN ({truthy}) THEN 'True'
                ELSE 'False'
            END
            """
        )
        connection.execute(
            f"""
            UPDATE schedule_entries
            SET cron_hard_sync_override = CASE
                WHEN cron_hard_sync_override IS NULL THEN NULL
                WHEN cron_hard_sync_override IN ({truthy}) THEN 'True'
                ELSE 'False'
            END
            """
        )
        connection.execute(
            f"""
            UPDATE schedule_entries
            SET cron_fade_in_override = CASE
                WHEN cron_fade_in_override IS NULL THEN NULL
                WHEN cron_fade_in_override IN ({truthy}) THEN 'True'
                ELSE 'False'
            END
            """
        )
        connection.execute(
            f"""
            UPDATE schedule_entries
            SET cron_fade_out_override = CASE
                WHEN cron_fade_out_override IS NULL THEN NULL
                WHEN cron_fade_out_override IN ({truthy}) THEN 'True'
                ELSE 'False'
            END
            """
        )
        connection.execute(
            f"""
            UPDATE cron_entries
            SET hard_sync = CASE
                WHEN hard_sync IN ({truthy}) THEN 'True'
                ELSE 'False'
            END
            """
        )
        connection.execute(
            f"""
            UPDATE cron_entries
            SET fade_in = CASE
                WHEN fade_in IN ({truthy}) THEN 'True'
                ELSE 'False'
            END
            """
        )
        connection.execute(
            f"""
            UPDATE cron_entries
            SET fade_out = CASE
                WHEN fade_out IN ({truthy}) THEN 'True'
                ELSE 'False'
            END
            """
        )


def migrate_boolean_column_types_to_text(connection: sqlite3.Connection) -> None:
    schedule_columns = {
        row[1]: str(row[2] or "").upper()
        for row in connection.execute("PRAGMA table_info(schedule_entries)").fetchall()
    }
    cron_columns = {
        row[1]: str(row[2] or "").upper()
        for row in connection.execute("PRAGMA table_info(cron_entries)").fetchall()
    }

    schedule_target_columns = {
        "hard_sync",
        "fade_in",
        "fade_out",
        "cron_hard_sync_override",
        "cron_fade_in_override",
        "cron_fade_out_override",
    }
    cron_target_columns = {"hard_sync", "fade_in", "fade_out"}

    needs_schedule_rebuild = any(
        column in schedule_columns and "TEXT" not in schedule_columns[column]
        for column in schedule_target_columns
    )
    needs_cron_rebuild = any(
        column in cron_columns and "TEXT" not in cron_columns[column]
        for column in cron_target_columns
    )

    if not needs_schedule_rebuild and not needs_cron_rebuild:
        return

    truthy = "1, '1', 'true', 'TRUE', 'True', 'yes', 'YES', 'Yes', 'on', 'ON', 'On'"
    with connection:
        if needs_schedule_rebuild:
            connection.executescript(
                f"""
                CREATE TABLE schedule_entries_new (
                    id TEXT PRIMARY KEY,
                    media_id TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    duration INTEGER,
                    hard_sync TEXT NOT NULL DEFAULT 'False',
                    fade_in TEXT NOT NULL DEFAULT 'False',
                    fade_out TEXT NOT NULL DEFAULT 'False',
                    status TEXT NOT NULL DEFAULT 'pending',
                    one_shot INTEGER NOT NULL DEFAULT 1,
                    cron_id TEXT,
                    cron_status_override TEXT,
                    cron_hard_sync_override TEXT,
                    cron_fade_in_override TEXT,
                    cron_fade_out_override TEXT,
                    position INTEGER NOT NULL
                );

                INSERT INTO schedule_entries_new (
                    id,
                    media_id,
                    start_at,
                    duration,
                    hard_sync,
                    fade_in,
                    fade_out,
                    status,
                    one_shot,
                    cron_id,
                    cron_status_override,
                    cron_hard_sync_override,
                    cron_fade_in_override,
                    cron_fade_out_override,
                    position
                )
                SELECT
                    id,
                    media_id,
                    start_at,
                    duration,
                    CASE WHEN hard_sync IN ({truthy}) THEN 'True' ELSE 'False' END,
                    CASE WHEN fade_in IN ({truthy}) THEN 'True' ELSE 'False' END,
                    CASE WHEN fade_out IN ({truthy}) THEN 'True' ELSE 'False' END,
                    COALESCE(status, 'pending'),
                    COALESCE(one_shot, 1),
                    cron_id,
                    cron_status_override,
                    CASE
                        WHEN cron_hard_sync_override IS NULL THEN NULL
                        WHEN cron_hard_sync_override IN ({truthy}) THEN 'True'
                        ELSE 'False'
                    END,
                    CASE
                        WHEN cron_fade_in_override IS NULL THEN NULL
                        WHEN cron_fade_in_override IN ({truthy}) THEN 'True'
                        ELSE 'False'
                    END,
                    CASE
                        WHEN cron_fade_out_override IS NULL THEN NULL
                        WHEN cron_fade_out_override IN ({truthy}) THEN 'True'
                        ELSE 'False'
                    END,
                    position
                FROM schedule_entries;

                DROP TABLE schedule_entries;
                ALTER TABLE schedule_entries_new RENAME TO schedule_entries;
                CREATE INDEX IF NOT EXISTS idx_schedule_entries_position
                    ON schedule_entries(position);
                """
            )

        if needs_cron_rebuild:
            connection.executescript(
                f"""
                CREATE TABLE cron_entries_new (
                    id TEXT PRIMARY KEY,
                    media_id TEXT NOT NULL,
                    expression TEXT NOT NULL,
                    hard_sync TEXT NOT NULL DEFAULT 'False',
                    fade_in TEXT NOT NULL DEFAULT 'False',
                    fade_out TEXT NOT NULL DEFAULT 'False',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    position INTEGER NOT NULL
                );

                INSERT INTO cron_entries_new (
                    id,
                    media_id,
                    expression,
                    hard_sync,
                    fade_in,
                    fade_out,
                    enabled,
                    created_at,
                    position
                )
                SELECT
                    id,
                    media_id,
                    expression,
                    CASE WHEN hard_sync IN ({truthy}) THEN 'True' ELSE 'False' END,
                    CASE WHEN fade_in IN ({truthy}) THEN 'True' ELSE 'False' END,
                    CASE WHEN fade_out IN ({truthy}) THEN 'True' ELSE 'False' END,
                    COALESCE(enabled, 1),
                    created_at,
                    position
                FROM cron_entries;

                DROP TABLE cron_entries;
                ALTER TABLE cron_entries_new RENAME TO cron_entries;
                CREATE INDEX IF NOT EXISTS idx_cron_entries_position
                    ON cron_entries(position);
                """
            )


def migrate_queue_items_metadata(connection: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(queue_items)").fetchall()
    }
    if "source" not in columns:
        connection.execute(
            "ALTER TABLE queue_items ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'"
        )
    if "schedule_entry_id" not in columns:
        connection.execute("ALTER TABLE queue_items ADD COLUMN schedule_entry_id TEXT")
    connection.commit()
