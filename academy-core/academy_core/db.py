import sqlite3
from pathlib import Path
from typing import Union


PathLike = Union[str, Path]


def connect(database: PathLike) -> sqlite3.Connection:
    database_text = str(database)
    if database_text != ":memory:":
        Path(database_text).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_text, timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    if database_text != ":memory:":
        connection.execute("PRAGMA journal_mode = WAL")
    return connection


def initialize(database: PathLike) -> None:
    schema_directory = Path(__file__).resolve().parent / "schema"
    connection = connect(database)
    try:
        for schema_path in sorted(schema_directory.glob("[0-9][0-9][0-9]_*.sql")):
            version = int(schema_path.name.split("_", 1)[0])
            migrations_exist = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
            ).fetchone()
            if migrations_exist is not None:
                applied = connection.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
                ).fetchone()
                if applied is not None:
                    continue
            connection.executescript(schema_path.read_text(encoding="utf-8"))
    finally:
        connection.close()
