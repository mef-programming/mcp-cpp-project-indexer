from __future__ import annotations

import json
import sqlite3
import threading

from pathlib import Path
from typing import Any, Iterable

from cpp_index_model import INDEXER_VERSION, SCANNER_VERSION


SQLITE_INDEX_SCHEMA = "cpp.project_index.sqlite.v1"
SQLITE_INDEX_FILENAME = "index.sqlite"


def sqlite_index_path(index_root: Path) -> Path:
    return index_root / SQLITE_INDEX_FILENAME


def connect_index_db(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def connect_readonly_index_db(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_posix()
    connection = sqlite3.connect(
        f"file:{uri}?mode=ro",
        uri=True,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    return connection


class ThreadLocalIndexConnections:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.local = threading.local()
        self.all_connections: list[sqlite3.Connection] = []
        self.lock = threading.Lock()
        self.generation = 0

    def get(self) -> sqlite3.Connection:
        connection = getattr(self.local, "connection", None)
        generation = getattr(self.local, "generation", None)

        if connection is not None and generation == self.generation:
            return connection

        connection = connect_readonly_index_db(self.path)
        self.local.connection = connection
        self.local.generation = self.generation

        with self.lock:
            self.all_connections.append(connection)

        return connection

    def close(self) -> None:
        with self.lock:
            connections = list(self.all_connections)
            self.all_connections.clear()
            self.generation += 1

        for connection in connections:
            try:
                connection.close()
            except sqlite3.Error:
                pass


def initialize_index_db(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;

        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS symbols (
            symbolId TEXT PRIMARY KEY,
            fileId TEXT NOT NULL,
            relativePath TEXT NOT NULL,
            shortName TEXT,
            qualifiedName TEXT,
            type TEXT,
            container TEXT,
            startLine INTEGER NOT NULL,
            endLine INTEGER NOT NULL,
            signature TEXT
        );

        CREATE TABLE IF NOT EXISTS symbol_names (
            name TEXT NOT NULL,
            symbolId TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            PRIMARY KEY (name, symbolId)
        );

        CREATE TABLE IF NOT EXISTS data (
            dataId TEXT PRIMARY KEY,
            fileId TEXT NOT NULL,
            relativePath TEXT NOT NULL,
            name TEXT,
            qualifiedName TEXT,
            declarationKind TEXT,
            scopeKind TEXT,
            container TEXT,
            typeText TEXT,
            storage TEXT,
            initializerKind TEXT,
            startLine INTEGER NOT NULL,
            endLine INTEGER NOT NULL,
            signature TEXT
        );

        CREATE TABLE IF NOT EXISTS data_names (
            name TEXT NOT NULL,
            dataId TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            PRIMARY KEY (name, dataId)
        );
        """
    )


def recreate_index_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")

    if temp_path.exists():
        temp_path.unlink()

    connection = connect_index_db(temp_path)
    initialize_index_db(connection)
    return connection


def replace_index_db(connection: sqlite3.Connection, final_path: Path) -> None:
    temp_path = Path(str(connection.execute("PRAGMA database_list").fetchone()["file"]))
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.commit()
    connection.close()

    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(temp_path) + suffix)
        if sidecar.exists():
            sidecar.unlink()

    final_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path.replace(final_path)


def set_metadata(connection: sqlite3.Connection, values: dict[str, Any]) -> None:
    rows = [(key, json.dumps(value, ensure_ascii=False, separators=(",", ":"))) for key, value in values.items()]
    connection.executemany(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES(?, ?)",
        rows,
    )


def row_json(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None

    keys = set(row.keys())

    if "json" in keys:
        return json.loads(str(row["json"]))

    if "symbolId" in keys:
        return {
            "symbolId": row["symbolId"],
            "fileId": row["fileId"],
            "relativePath": row["relativePath"],
            "type": row["type"],
            "shortName": row["shortName"],
            "qualifiedName": row["qualifiedName"],
            "container": row["container"],
            "startLine": int(row["startLine"]),
            "endLine": int(row["endLine"]),
            "signature": row["signature"],
        }

    storage_text = row["storage"] if "storage" in keys else "[]"

    try:
        storage = json.loads(str(storage_text or "[]"))
    except json.JSONDecodeError:
        storage = []

    return {
        "dataId": row["dataId"],
        "fileId": row["fileId"],
        "relativePath": row["relativePath"],
        "declarationKind": row["declarationKind"],
        "scopeKind": row["scopeKind"],
        "name": row["name"],
        "qualifiedName": row["qualifiedName"],
        "container": row["container"],
        "startLine": int(row["startLine"]),
        "endLine": int(row["endLine"]),
        "signature": row["signature"],
        "typeText": row["typeText"] or "",
        "storage": storage,
        "initializerKind": row["initializerKind"] or "unknown",
    }


def build_symbol_rows(symbols: Iterable[dict[str, Any]]) -> Iterable[tuple[Any, ...]]:
    for symbol in symbols:
        yield (
            symbol["symbolId"],
            symbol["fileId"],
            symbol.get("relativePath") or "",
            symbol.get("shortName"),
            symbol.get("qualifiedName"),
            symbol.get("type"),
            symbol.get("container"),
            int(symbol.get("startLine") or 0),
            int(symbol.get("endLine") or 0),
            symbol.get("signature"),
        )


def build_data_rows(data_items: Iterable[dict[str, Any]]) -> Iterable[tuple[Any, ...]]:
    for item in data_items:
        yield (
            item["dataId"],
            item["fileId"],
            item.get("relativePath") or "",
            item.get("name"),
            item.get("qualifiedName"),
            item.get("declarationKind"),
            item.get("scopeKind"),
            item.get("container"),
            item.get("typeText"),
            json.dumps(item.get("storage") or [], ensure_ascii=False, separators=(",", ":")),
            item.get("initializerKind") or "unknown",
            int(item.get("startLine") or 0),
            int(item.get("endLine") or 0),
            item.get("signature"),
        )


def insert_symbols(connection: sqlite3.Connection, symbols: list[dict[str, Any]]) -> int:
    connection.executemany(
        """
        INSERT OR REPLACE INTO symbols(
            symbolId, fileId, relativePath, shortName, qualifiedName, type,
            container, startLine, endLine, signature
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        build_symbol_rows(symbols),
    )
    return len(symbols)


def insert_symbol_names(connection: sqlite3.Connection, names: dict[str, list[str]]) -> int:
    rows: list[tuple[str, str, int]] = []

    for name, symbol_ids in names.items():
        for ordinal, symbol_id in enumerate(symbol_ids):
            rows.append((name, symbol_id, ordinal))

    connection.executemany(
        "INSERT OR REPLACE INTO symbol_names(name, symbolId, ordinal) VALUES (?, ?, ?)",
        rows,
    )
    return len(names)


def insert_data(connection: sqlite3.Connection, data_items: list[dict[str, Any]]) -> int:
    connection.executemany(
        """
        INSERT OR REPLACE INTO data(
            dataId, fileId, relativePath, name, qualifiedName, declarationKind,
            scopeKind, container, typeText, storage, initializerKind,
            startLine, endLine, signature
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        build_data_rows(data_items),
    )
    return len(data_items)


def insert_data_names(connection: sqlite3.Connection, data_names: dict[str, list[str]]) -> int:
    rows: list[tuple[str, str, int]] = []

    for name, data_ids in data_names.items():
        for ordinal, data_id in enumerate(data_ids):
            rows.append((name, data_id, ordinal))

    connection.executemany(
        "INSERT OR REPLACE INTO data_names(name, dataId, ordinal) VALUES (?, ?, ?)",
        rows,
    )
    return len(data_names)


def create_lookup_indexes(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_symbols_file_range
            ON symbols(fileId, startLine, endLine);
        CREATE INDEX IF NOT EXISTS idx_symbols_short_name
            ON symbols(shortName);
        CREATE INDEX IF NOT EXISTS idx_symbols_type
            ON symbols(type);
        CREATE INDEX IF NOT EXISTS idx_symbols_container
            ON symbols(container);
        CREATE INDEX IF NOT EXISTS idx_symbol_names_name_order
            ON symbol_names(name, ordinal);

        CREATE INDEX IF NOT EXISTS idx_data_file_range
            ON data(fileId, startLine, endLine);
        CREATE INDEX IF NOT EXISTS idx_data_name
            ON data(name);
        CREATE INDEX IF NOT EXISTS idx_data_container
            ON data(container);
        CREATE INDEX IF NOT EXISTS idx_data_names_name_order
            ON data_names(name, ordinal);
        """
    )


def build_sqlite_index(
    *,
    index_root: Path,
    symbols: list[dict[str, Any]],
    names: dict[str, list[str]],
    data_items: list[dict[str, Any]],
    data_names: dict[str, list[str]],
    counts: dict[str, int],
) -> Path:
    db_path = sqlite_index_path(index_root)
    connection = recreate_index_db(db_path)

    try:
        with connection:
            set_metadata(
                connection,
                {
                    "schema": SQLITE_INDEX_SCHEMA,
                    "indexerVersion": INDEXER_VERSION,
                    "scannerVersion": SCANNER_VERSION,
                    "counts": counts,
                },
            )
            insert_symbols(connection, symbols)
            insert_symbol_names(connection, names)
            insert_data(connection, data_items)
            insert_data_names(connection, data_names)
            create_lookup_indexes(connection)
    except Exception:
        connection.close()
        temp_path = db_path.with_suffix(db_path.suffix + ".tmp")
        if temp_path.exists():
            temp_path.unlink()
        raise

    replace_index_db(connection, db_path)
    return db_path


def count_lookup_rows(index_root: Path) -> dict[str, int]:
    connection = connect_index_db(sqlite_index_path(index_root))

    try:
        return count_lookup_rows_from_connection(connection)
    finally:
        connection.close()


def count_lookup_rows_from_connection(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        "symbols": int(connection.execute("SELECT COUNT(*) AS count FROM symbols").fetchone()["count"]),
        "names": int(connection.execute("SELECT COUNT(DISTINCT name) AS count FROM symbol_names").fetchone()["count"]),
        "data": int(connection.execute("SELECT COUNT(*) AS count FROM data").fetchone()["count"]),
        "dataNames": int(connection.execute("SELECT COUNT(DISTINCT name) AS count FROM data_names").fetchone()["count"]),
    }


def replace_file_lookup_rows(
    *,
    index_root: Path,
    changed_file_ids: set[str],
    symbols: list[dict[str, Any]],
    names: dict[str, list[str]],
    data_items: list[dict[str, Any]],
    data_names: dict[str, list[str]],
) -> dict[str, int]:
    db_path = sqlite_index_path(index_root)

    if not db_path.exists():
        raise FileNotFoundError(db_path)

    connection = connect_index_db(db_path)

    try:
        with connection:
            for file_id in changed_file_ids:
                connection.execute(
                    """
                    DELETE FROM symbol_names
                    WHERE symbolId IN (SELECT symbolId FROM symbols WHERE fileId = ?)
                    """,
                    (file_id,),
                )
                connection.execute("DELETE FROM symbols WHERE fileId = ?", (file_id,))
                connection.execute(
                    """
                    DELETE FROM data_names
                    WHERE dataId IN (SELECT dataId FROM data WHERE fileId = ?)
                    """,
                    (file_id,),
                )
                connection.execute("DELETE FROM data WHERE fileId = ?", (file_id,))

            insert_symbols(connection, symbols)
            insert_symbol_names(connection, names)
            insert_data(connection, data_items)
            insert_data_names(connection, data_names)

        return count_lookup_rows_from_connection(connection)
    finally:
        connection.close()
