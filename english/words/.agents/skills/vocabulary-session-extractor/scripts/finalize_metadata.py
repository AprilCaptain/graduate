#!/usr/bin/env python3
"""Finalize one archive record and its source cursor after content validation."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

import update_index
import validate_archive


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate archive content, then idempotently add one processed record "
            "and advance its source cursor."
        )
    )
    parser.add_argument("--record-id", required=True)
    parser.add_argument("--date", required=True, dest="training_date")
    parser.add_argument("--source", required=True)
    parser.add_argument("--daily-file", required=True)
    parser.add_argument("--content-fingerprint", required=True)
    parser.add_argument("--source-start-message-id")
    parser.add_argument("--source-start-sha256")
    parser.add_argument("--source-end-message-id")
    parser.add_argument("--source-end-sha256")
    parser.add_argument("--source-message-count", type=int)
    parser.add_argument("--source-start-byte-offset", type=int)
    parser.add_argument("--source-end-byte-offset", type=int)
    parser.add_argument("--source-end-prefix-sha256")
    parser.add_argument("--kind", required=True)
    parser.add_argument("--location", required=True)
    parser.add_argument(
        "--expected-last-message-id",
        help=(
            "Required when advancing an existing source; prevents updating from "
            "a stale or unexpected cursor"
        ),
    )
    parser.add_argument("--expected-last-message-sha256")
    parser.add_argument("--expected-last-byte-offset", type=int)
    parser.add_argument("--expected-last-prefix-sha256")
    parser.add_argument("--root", default=".")
    return parser.parse_args()


def safe_relative_file(root: Path, value: str) -> Path:
    posix = PurePosixPath(value.replace("\\", "/"))
    if posix.is_absolute() or ".." in posix.parts:
        raise ValueError("daily file must be a project-relative path")
    path = (root / Path(*posix.parts)).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("daily file must stay inside the archive") from exc
    if not path.is_file():
        raise ValueError(f"daily file does not exist: {value}")
    return path


def validate_args(args: argparse.Namespace, root: Path) -> tuple[Path, str]:
    try:
        if date.fromisoformat(args.training_date).isoformat() != args.training_date:
            raise ValueError
    except ValueError as exc:
        raise ValueError("date must use YYYY-MM-DD") from exc
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}-[0-9a-f]{8}", args.record_id):
        raise ValueError("record ID must use YYYY-MM-DD-xxxxxxxx")
    if not validate_archive.FINGERPRINT_RE.fullmatch(args.content_fingerprint):
        raise ValueError("content fingerprint must use sha256:<64 lowercase hex>")
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", args.source):
        raise ValueError("source must be a stable YAML-safe label")
    if not args.kind.strip() or not args.location.strip():
        raise ValueError("source, kind, and location must not be empty")
    if args.kind == "append-text":
        location = PurePosixPath(args.location.replace("\\", "/"))
        if location.is_absolute() or ".." in location.parts:
            raise ValueError(
                "append-text location must be a project-relative source path"
            )
    message_values = (
        args.source_start_message_id,
        args.source_start_sha256,
        args.source_end_message_id,
        args.source_end_sha256,
        args.source_message_count,
    )
    byte_values = (
        args.source_start_byte_offset,
        args.source_end_byte_offset,
        args.source_end_prefix_sha256,
    )
    has_message = any(value is not None for value in message_values)
    has_byte = any(value is not None for value in byte_values)
    if has_message == has_byte:
        raise ValueError(
            "supply exactly one complete message-range or byte-range boundary"
        )
    if has_message:
        if any(value is None for value in message_values):
            raise ValueError(
                "message range requires start/end IDs, both hashes, and message count"
            )
        if (
            not re.fullmatch(r"[0-9a-f]{64}", args.source_start_sha256)
            or not re.fullmatch(r"[0-9a-f]{64}", args.source_end_sha256)
        ):
            raise ValueError("message hashes must use 64 lowercase hexadecimal characters")
        if args.source_message_count < 1:
            raise ValueError("source message count must be positive")
        boundary_kind = "message-range"
    else:
        if any(value is None for value in byte_values):
            raise ValueError(
                "byte range requires start/end offsets and end prefix hash"
            )
        if (
            args.source_start_byte_offset < 0
            or args.source_end_byte_offset <= args.source_start_byte_offset
        ):
            raise ValueError("byte range must have increasing non-negative offsets")
        if not re.fullmatch(r"[0-9a-f]{64}", args.source_end_prefix_sha256):
            raise ValueError("prefix hash must use 64 lowercase hexadecimal characters")
        boundary_kind = "byte-range"
    if args.kind == "chatgpt-share" and boundary_kind != "message-range":
        raise ValueError("chatgpt-share sources require a message range")
    if args.kind == "append-text" and boundary_kind != "byte-range":
        raise ValueError("append-text sources require a byte range")
    daily_path = safe_relative_file(root, args.daily_file)
    text = daily_path.read_text(encoding="utf-8")
    if args.record_id not in validate_archive.RECORD_RE.findall(text):
        raise ValueError("daily file does not contain the supplied record ID")
    return daily_path, boundary_kind


def yaml_scalar(value: Any) -> str:
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_./:<>=?&%-]+", value):
        return value
    return json.dumps(str(value), ensure_ascii=False)


def dump_mapping(mapping: dict[str, Any], indent: int = 0) -> list[str]:
    lines: list[str] = []
    prefix = " " * indent
    for key, value in mapping.items():
        if isinstance(value, dict):
            if value:
                lines.append(f"{prefix}{key}:")
                lines.extend(dump_mapping(value, indent + 2))
            else:
                lines.append(f"{prefix}{key}: {{}}")
        else:
            lines.append(f"{prefix}{key}: {yaml_scalar(value)}")
    return lines


def dump_yaml(mapping: dict[str, Any]) -> str:
    chunks: list[str] = []
    for key, value in mapping.items():
        chunks.append("\n".join(dump_mapping({key: value})))
    return "\n\n".join(chunks).rstrip() + "\n"


def content_preflight(root: Path) -> list[str]:
    errors: list[str] = []
    entries = validate_archive.validate_entries(root, errors)
    validate_archive.validate_indexes(root, entries, errors)
    validate_archive.validate_daily(root, errors)
    for path in [
        root / "index.md",
        *sorted((root / "indexes").glob("*.md")),
        *sorted((root / "entries").glob("*/*.md")),
        *sorted((root / "daily").glob("**/*.md")),
    ]:
        if path.is_file():
            validate_archive.check_links(path, root, errors)
    return errors


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    try:
        _daily_path, boundary_kind = validate_args(args, root)
        preflight_errors = content_preflight(root)
        if preflight_errors:
            raise ValueError(
                "archive content validation failed:\n- "
                + "\n- ".join(preflight_errors)
            )
        state_path = root / "metadata" / "extraction-state.yml"
        cursor_path = root / "metadata" / "source-cursors.yml"
        state = validate_archive.parse_simple_yaml(
            state_path.read_text(encoding="utf-8"), "metadata/extraction-state.yml"
        )
        cursors = validate_archive.parse_simple_yaml(
            cursor_path.read_text(encoding="utf-8"), "metadata/source-cursors.yml"
        )
        records = state.setdefault("processed_records", {})
        sources = cursors.setdefault("sources", {})
        if not isinstance(records, dict) or not isinstance(sources, dict):
            raise ValueError("metadata record and source collections must be mappings")
        if state.get("version") != 2 or cursors.get("version") != 2:
            raise ValueError("metadata must be migrated to schema version 2")

        record: dict[str, Any] = {
            "training_date": args.training_date,
            "source": args.source,
            "daily_file": args.daily_file,
            "status": "completed",
            "content_fingerprint": args.content_fingerprint,
            "source_boundary_kind": boundary_kind,
        }
        if boundary_kind == "message-range":
            record.update(
                {
                    "source_start_message_id": args.source_start_message_id,
                    "source_start_message_sha256": args.source_start_sha256,
                    "source_end_message_id": args.source_end_message_id,
                    "source_end_message_sha256": args.source_end_sha256,
                    "source_message_count": args.source_message_count,
                }
            )
        else:
            record.update(
                {
                    "source_start_byte_offset": args.source_start_byte_offset,
                    "source_end_byte_offset": args.source_end_byte_offset,
                    "source_end_prefix_sha256": args.source_end_prefix_sha256,
                }
            )
        existing_record = records.get(args.record_id)
        if existing_record is not None and existing_record != record:
            raise ValueError("existing record ID has different metadata")
        for other_id, other_record in records.items():
            if (
                other_id != args.record_id
                and isinstance(other_record, dict)
                and other_record.get("content_fingerprint")
                == args.content_fingerprint
            ):
                raise ValueError(
                    f"content fingerprint is already archived as {other_id}"
                )

        source = sources.get(args.source)
        if source is not None:
            if not isinstance(source, dict):
                raise ValueError("existing source cursor is not a mapping")
            for key, expected in (("kind", args.kind), ("location", args.location)):
                if source.get(key) != expected:
                    raise ValueError(f"existing source has a different {key}")
            if source.get("last_record_id") != args.record_id:
                if boundary_kind == "message-range":
                    if (
                        not args.expected_last_message_id
                        or not args.expected_last_message_sha256
                    ):
                        raise ValueError(
                            "both expected-last-message fields are required to "
                            "advance an existing message source"
                        )
                    if (
                        source.get("last_message_id")
                        != args.expected_last_message_id
                        or source.get("last_message_sha256")
                        != args.expected_last_message_sha256
                    ):
                        raise ValueError(
                            "existing source cursor changed; refusing to advance"
                        )
                else:
                    if (
                        args.expected_last_byte_offset is None
                        or not args.expected_last_prefix_sha256
                    ):
                        raise ValueError(
                            "both expected-last-byte fields are required to "
                            "advance an existing text source"
                        )
                    if (
                        source.get("last_byte_offset")
                        != args.expected_last_byte_offset
                        or source.get("last_prefix_sha256")
                        != args.expected_last_prefix_sha256
                    ):
                        raise ValueError(
                            "existing source cursor changed; refusing to advance"
                        )
        else:
            source = {}
            sources[args.source] = source

        records[args.record_id] = record
        state["last_processed_record"] = args.record_id
        source.update(
            {
                "kind": args.kind,
                "location": args.location,
                "last_record_id": args.record_id,
                "last_training_date": args.training_date,
            }
        )
        if boundary_kind == "message-range":
            source.update(
                {
                    "last_message_id": args.source_end_message_id,
                    "last_message_sha256": args.source_end_sha256,
                }
            )
            source.pop("last_byte_offset", None)
            source.pop("last_prefix_sha256", None)
        else:
            source.update(
                {
                    "last_byte_offset": args.source_end_byte_offset,
                    "last_prefix_sha256": args.source_end_prefix_sha256,
                }
            )
            source.pop("last_message_id", None)
            source.pop("last_message_sha256", None)
        state_text = dump_yaml(state)
        cursor_text = dump_yaml(cursors)
        validate_archive.parse_simple_yaml(state_text, "generated extraction state")
        validate_archive.parse_simple_yaml(cursor_text, "generated source cursors")
        update_index.atomic_write_if_changed(state_path, state_text)
        update_index.atomic_write_if_changed(cursor_path, cursor_text)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    postflight_errors: list[str] = []
    entries = validate_archive.validate_entries(root, postflight_errors)
    validate_archive.validate_indexes(root, entries, postflight_errors)
    daily_ids = validate_archive.validate_daily(root, postflight_errors)
    validate_archive.validate_metadata(root, daily_ids, postflight_errors)
    if postflight_errors:
        for error in postflight_errors:
            print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"finalized {args.record_id} and advanced {args.source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
