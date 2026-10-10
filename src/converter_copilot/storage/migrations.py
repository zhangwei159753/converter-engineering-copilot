"""Versioned, transactional SQLite schema migrations."""

import sqlite3
from collections.abc import Callable

Migration = Callable[[sqlite3.Connection], None]
CURRENT_SCHEMA_VERSION = 7


def _create_base_schema(connection: sqlite3.Connection) -> None:
    statements = (
        """CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            inputs_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS task_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
            stage TEXT NOT NULL,
            status TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_task_events_task_event ON task_events(task_id, event_id)",
        """CREATE TABLE IF NOT EXISTS reports (
            task_id TEXT PRIMARY KEY REFERENCES tasks(task_id) ON DELETE CASCADE,
            payload_json TEXT NOT NULL
        )""",
        "CREATE TABLE IF NOT EXISTS cases (case_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)",
        """CREATE TABLE IF NOT EXISTS task_retries (
            retry_task_id TEXT PRIMARY KEY REFERENCES tasks(task_id) ON DELETE CASCADE,
            original_task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
            created_at TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_task_retries_original ON task_retries(original_task_id)",
    )
    for statement in statements:
        connection.execute(statement)


def _add_task_lifecycle_columns(connection: sqlite3.Connection) -> None:
    task_columns = {row[1] for row in connection.execute("PRAGMA table_info(tasks)")}
    additions = (
        ("idempotency_key", "TEXT"),
        ("request_hash", "TEXT"),
        ("attempt", "INTEGER NOT NULL DEFAULT 0"),
        ("max_attempts", "INTEGER NOT NULL DEFAULT 3"),
        ("lease_owner", "TEXT"),
        ("lease_expires_at", "TEXT"),
        ("error_code", "TEXT"),
    )
    for name, declaration in additions:
        if name not in task_columns:
            connection.execute(f"ALTER TABLE tasks ADD COLUMN {name} {declaration}")

    retry_columns = {row[1] for row in connection.execute("PRAGMA table_info(task_retries)")}
    if "idempotency_key" not in retry_columns:
        connection.execute("ALTER TABLE task_retries ADD COLUMN idempotency_key TEXT")
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_idempotency_key "
        "ON tasks(idempotency_key) WHERE idempotency_key IS NOT NULL"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_task_retries_idempotency_key "
        "ON task_retries(idempotency_key) WHERE idempotency_key IS NOT NULL"
    )


def _add_import_ledger(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE imports (
            import_id TEXT PRIMARY KEY,
            content_sha256 TEXT NOT NULL,
            source_name TEXT NOT NULL,
            file_type TEXT NOT NULL,
            source_path TEXT NOT NULL,
            parser_version TEXT NOT NULL,
            chunker_version TEXT NOT NULL,
            expected_chunk_count INTEGER NOT NULL,
            indexed_chunks INTEGER NOT NULL DEFAULT 0,
            imported_cases INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            error_code TEXT,
            UNIQUE(content_sha256, file_type, parser_version, chunker_version)
        )"""
    )
    connection.execute(
        """CREATE TABLE import_chunks (
            import_id TEXT NOT NULL REFERENCES imports(import_id) ON DELETE CASCADE,
            chunk_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            locator TEXT NOT NULL,
            PRIMARY KEY(import_id, chunk_id)
        )"""
    )
    connection.execute(
        """CREATE TABLE import_case_candidates (
            import_id TEXT NOT NULL REFERENCES imports(import_id) ON DELETE CASCADE,
            case_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY(import_id, case_id)
        )"""
    )
    connection.execute(
        """CREATE TABLE import_cases (
            import_id TEXT NOT NULL REFERENCES imports(import_id) ON DELETE CASCADE,
            case_id TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
            PRIMARY KEY(import_id, case_id)
        )"""
    )
    connection.execute("CREATE INDEX idx_imports_status_created ON imports(status, created_at)")


def _add_import_candidate_ready(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(imports)")}
    if "case_candidates_ready" not in columns:
        connection.execute("ALTER TABLE imports ADD COLUMN case_candidates_ready INTEGER NOT NULL DEFAULT 0")


def _add_evaluation_reviews(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE evaluation_bundles (
            bundle_id TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            dataset_version TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            imported_count INTEGER NOT NULL,
            skipped_count INTEGER NOT NULL,
            imported_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE evaluation_examples (
            review_id TEXT PRIMARY KEY,
            bundle_id TEXT NOT NULL REFERENCES evaluation_bundles(bundle_id) ON DELETE CASCADE,
            example_id TEXT NOT NULL,
            mode TEXT NOT NULL,
            source_type TEXT NOT NULL,
            inputs_json TEXT NOT NULL,
            task_id TEXT NOT NULL,
            claims_json TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            reviewer_id TEXT,
            reviewed_at TEXT,
            label_version INTEGER NOT NULL DEFAULT 0,
            evidence_labels_json TEXT NOT NULL DEFAULT '[]',
            expected_retrieval_ids_json TEXT NOT NULL DEFAULT '[]',
            note TEXT,
            UNIQUE(bundle_id, example_id)
        )"""
    )
    connection.execute("CREATE INDEX idx_evaluation_examples_mode_status ON evaluation_examples(mode, status, review_id)")
    connection.execute(
        """CREATE TABLE evaluation_review_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            review_id TEXT NOT NULL REFERENCES evaluation_examples(review_id) ON DELETE CASCADE,
            label_version INTEGER NOT NULL,
            reviewer_id TEXT NOT NULL,
            reviewed_at TEXT NOT NULL,
            evidence_labels_json TEXT NOT NULL,
            expected_retrieval_ids_json TEXT NOT NULL,
            note TEXT
        )"""
    )
    connection.execute("CREATE INDEX idx_evaluation_review_events_review ON evaluation_review_events(review_id, event_id)")


def _add_work_order_reviews(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE work_order_analyses (
            work_order_id TEXT NOT NULL,
            task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            PRIMARY KEY(work_order_id, task_id)
        )"""
    )
    connection.execute("CREATE INDEX idx_work_order_analyses_order ON work_order_analyses(work_order_id, created_at)")
    connection.execute(
        """CREATE TABLE work_order_review_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_order_id TEXT NOT NULL,
            task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
            version INTEGER NOT NULL,
            reviewer TEXT NOT NULL,
            verdict TEXT NOT NULL CHECK(verdict IN ('accepted', 'needs_more_information', 'rejected')),
            note TEXT,
            reviewed_at TEXT NOT NULL,
            UNIQUE(work_order_id, task_id, version),
            FOREIGN KEY(work_order_id, task_id)
                REFERENCES work_order_analyses(work_order_id, task_id) ON DELETE CASCADE
        )"""
    )
    connection.execute(
        "CREATE INDEX idx_work_order_review_history ON work_order_review_events(work_order_id, task_id, version)"
    )


def _add_evaluation_output_context(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(evaluation_examples)")}
    if "output_context_json" not in columns:
        connection.execute(
            "ALTER TABLE evaluation_examples ADD COLUMN output_context_json TEXT NOT NULL DEFAULT '{}'"
        )


MIGRATIONS: list[tuple[int, Migration]] = [
    (1, _create_base_schema),
    (2, _add_task_lifecycle_columns),
    (3, _add_import_ledger),
    (4, _add_import_candidate_ready),
    (5, _add_evaluation_reviews),
    (6, _add_work_order_reviews),
    (7, _add_evaluation_output_context),
]


def migrate(connection: sqlite3.Connection) -> int:
    """Apply each pending migration in an immediate transaction."""
    current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    known_versions = [version for version, _ in MIGRATIONS]
    latest_version = max(known_versions, default=0)
    if current_version > latest_version:
        raise RuntimeError(
            f"Database schema version {current_version} is newer than supported version {latest_version}."
        )

    for version, migration in MIGRATIONS:
        if version <= current_version:
            continue
        connection.execute("BEGIN IMMEDIATE")
        try:
            migration(connection)
            connection.execute(f"PRAGMA user_version = {version}")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        current_version = version
    return current_version
