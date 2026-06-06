from __future__ import annotations

import argparse
import json

from pathlib import Path

from cpp_index_sqlite import connect_index_db, row_json, sqlite_index_path


def export_table(*, index_root: Path, kind: str, output: Path) -> int:
    table = {"symbols": "symbols", "data": "data"}[kind]
    order = {
        "symbols": "COALESCE(qualifiedName, shortName, ''), relativePath, startLine, endLine",
        "data": "COALESCE(qualifiedName, name, ''), relativePath, startLine, endLine",
    }[kind]
    connection = connect_index_db(sqlite_index_path(index_root))
    count = 0

    try:
        output.parent.mkdir(parents=True, exist_ok=True)

        with output.open("w", encoding="utf-8") as handle:
            for row in connection.execute(f"SELECT * FROM {table} ORDER BY {order}"):
                item = row_json(row)

                if item is None:
                    continue

                handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
                count += 1
    finally:
        connection.close()

    return count


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export SQLite-backed symbols/data indexes to JSONL on demand.",
    )
    parser.add_argument(
        "--index-root",
        type=Path,
        required=True,
        help="Directory containing index.sqlite.",
    )
    parser.add_argument(
        "--kind",
        choices=["symbols", "data"],
        required=True,
        help="Which global lookup table to export.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output JSONL file.",
    )
    args = parser.parse_args()

    db_path = sqlite_index_path(args.index_root)

    if not db_path.exists():
        raise SystemExit(f"SQLite index not found: {db_path}")

    count = export_table(index_root=args.index_root, kind=args.kind, output=args.output)
    print(f"Exported {count} {args.kind} rows to {args.output}")


if __name__ == "__main__":
    main()
