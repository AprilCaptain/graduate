#!/usr/bin/env python3
"""Fail-closed incremental extraction for append-oriented UTF-8 text sources."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify a byte-offset/prefix-hash cursor and emit only appended UTF-8 "
            "text. Edited prefixes fail instead of replaying the whole source."
        )
    )
    parser.add_argument(
        "--input-file",
        help="UTF-8 text file to read; otherwise read standard input as bytes",
    )
    parser.add_argument(
        "--after-byte-offset",
        type=int,
        help="Emit bytes after this verified UTF-8 boundary",
    )
    parser.add_argument(
        "--after-prefix-sha256",
        help="Expected SHA-256 of all bytes through --after-byte-offset",
    )
    parser.add_argument(
        "--candidate-cursor",
        action="append",
        default=[],
        metavar="BYTE_OFFSET:SHA256",
        help="Candidate cursor from another source snapshot; latest exact match wins",
    )
    parser.add_argument(
        "--cursor-info",
        nargs="?",
        const="last",
        metavar="BYTE_OFFSET",
        help=(
            "Emit JSON cursor metadata for BYTE_OFFSET; omit the offset to inspect "
            "the end of the current text"
        ),
    )
    return parser.parse_args()


def read_bytes(args: argparse.Namespace) -> bytes:
    if args.input_file:
        path = Path(args.input_file)
        if path.is_absolute():
            raise ValueError("input file must use a project-relative path")
        resolved = path.resolve()
        try:
            resolved.relative_to(Path.cwd().resolve())
        except ValueError as exc:
            raise ValueError("input file must stay inside the current project") from exc
        return resolved.read_bytes()
    return sys.stdin.buffer.read()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_utf8_boundary(data: bytes, offset: int) -> None:
    if offset < 0 or offset > len(data):
        raise ValueError(f"byte offset must be between 0 and {len(data)}")
    try:
        data[:offset].decode("utf-8")
        data[offset:].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("byte offset is not a valid UTF-8 boundary") from exc


def parse_candidate(value: str) -> tuple[int, str]:
    try:
        offset_text, expected_hash = value.split(":", 1)
        offset = int(offset_text)
    except ValueError as exc:
        raise ValueError("candidate cursor must use BYTE_OFFSET:SHA256") from exc
    if offset < 0 or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ValueError("candidate cursor contains an invalid offset or SHA-256")
    return offset, expected_hash


def latest_candidate(data: bytes, values: list[str]) -> tuple[int, str] | None:
    if not values:
        return None
    candidates = sorted(
        {parse_candidate(value) for value in values},
        key=lambda item: item[0],
        reverse=True,
    )
    for offset, expected_hash in candidates:
        if offset <= len(data) and digest(data[:offset]) == expected_hash:
            validate_utf8_boundary(data, offset)
            return offset, expected_hash
    raise ValueError("no candidate cursor matched the current text prefix")


def main() -> int:
    args = parse_args()
    try:
        data = read_bytes(args)
        data.decode("utf-8")
        if args.cursor_info is not None:
            if (
                args.after_byte_offset is not None
                or args.after_prefix_sha256
                or args.candidate_cursor
            ):
                raise ValueError(
                    "--cursor-info cannot be combined with extraction cursors"
                )
            if args.cursor_info == "last":
                offset = len(data)
            else:
                try:
                    offset = int(args.cursor_info)
                except ValueError as exc:
                    raise ValueError("--cursor-info must use an integer byte offset") from exc
            validate_utf8_boundary(data, offset)
            print(
                json.dumps(
                    {
                        "byte_offset": offset,
                        "prefix_sha256": digest(data[:offset]),
                        "total_bytes": len(data),
                    },
                    sort_keys=True,
                )
            )
            return 0

        explicit = args.after_byte_offset is not None or args.after_prefix_sha256
        if explicit and args.candidate_cursor:
            raise ValueError(
                "use either an explicit cursor or candidate cursors, not both"
            )
        if explicit:
            if (
                args.after_byte_offset is None
                or not args.after_prefix_sha256
                or not re.fullmatch(r"[0-9a-f]{64}", args.after_prefix_sha256)
            ):
                raise ValueError(
                    "--after-byte-offset and a valid --after-prefix-sha256 "
                    "must be supplied together"
                )
            offset = args.after_byte_offset
            validate_utf8_boundary(data, offset)
            if digest(data[:offset]) != args.after_prefix_sha256:
                raise ValueError(
                    "text before the cursor changed; incremental extraction is unsafe"
                )
        else:
            candidate = latest_candidate(data, args.candidate_cursor)
            offset = candidate[0] if candidate else 0
        sys.stdout.write(data[offset:].decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
