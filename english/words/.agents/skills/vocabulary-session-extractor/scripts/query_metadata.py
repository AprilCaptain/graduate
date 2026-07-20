#!/usr/bin/env python3
"""Query small metadata slices without loading whole state files into context."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import validate_archive


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Return one compact JSON view of archive metadata."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--source", help="Return one source cursor")
    group.add_argument("--record", help="Return one processed record")
    group.add_argument("--fingerprint", help="Find a record by content fingerprint")
    group.add_argument(
        "--candidate-cursors",
        action="store_true",
        help="Return compact exact-match cursor strings for source switching",
    )
    group.add_argument(
        "--summary",
        action="store_true",
        help="Return counts and the most recently processed record ID",
    )
    parser.add_argument(
        "--kind",
        help="With --candidate-cursors, limit results to this source kind",
    )
    parser.add_argument("--root", default=".")
    return parser.parse_args()


def load_metadata(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    state_path = root / "metadata" / "extraction-state.yml"
    cursor_path = root / "metadata" / "source-cursors.yml"
    state = validate_archive.parse_simple_yaml(
        state_path.read_text(encoding="utf-8"), "metadata/extraction-state.yml"
    )
    cursors = validate_archive.parse_simple_yaml(
        cursor_path.read_text(encoding="utf-8"), "metadata/source-cursors.yml"
    )
    records = state.get("processed_records")
    sources = cursors.get("sources")
    if state.get("version") != 2 or cursors.get("version") != 2:
        raise ValueError("metadata must use schema version 2")
    if not isinstance(records, dict) or not isinstance(sources, dict):
        raise ValueError("metadata record and source collections must be mappings")
    return state, cursors


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    try:
        if args.kind and not args.candidate_cursors:
            raise ValueError("--kind is only valid with --candidate-cursors")
        state, cursors = load_metadata(root)
        records = state["processed_records"]
        sources = cursors["sources"]
        result: Any
        if args.source:
            if args.source not in sources:
                raise KeyError(f"source not found: {args.source}")
            result = {"name": args.source, **sources[args.source]}
        elif args.record:
            if args.record not in records:
                raise KeyError(f"record not found: {args.record}")
            result = {"record_id": args.record, **records[args.record]}
        elif args.fingerprint:
            matches = [
                {"record_id": record_id, **record}
                for record_id, record in records.items()
                if isinstance(record, dict)
                and record.get("content_fingerprint") == args.fingerprint
            ]
            result = {"matches": matches}
        elif args.candidate_cursors:
            candidates: list[dict[str, str]] = []
            for name, source in sources.items():
                if not isinstance(source, dict):
                    continue
                if args.kind and source.get("kind") != args.kind:
                    continue
                if source.get("last_message_id") and source.get(
                    "last_message_sha256"
                ):
                    candidates.append(
                        {
                            "cursor": (
                                f"{source['last_message_id']}:"
                                f"{source['last_message_sha256']}"
                            ),
                            "cursor_type": "message",
                            "kind": str(source.get("kind", "")),
                            "source": name,
                        }
                    )
                elif source.get("last_byte_offset") is not None and source.get(
                    "last_prefix_sha256"
                ):
                    candidates.append(
                        {
                            "cursor": (
                                f"{source['last_byte_offset']}:"
                                f"{source['last_prefix_sha256']}"
                            ),
                            "cursor_type": "byte-prefix",
                            "kind": str(source.get("kind", "")),
                            "source": name,
                        }
                    )
            result = {"candidates": candidates}
        else:
            result = {
                "last_processed_record": state.get("last_processed_record"),
                "processed_record_count": len(records),
                "source_count": len(sources),
            }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except (OSError, UnicodeError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
