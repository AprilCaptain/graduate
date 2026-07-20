#!/usr/bin/env python3
"""Generate a deterministic internal ID for one completed vocabulary session."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path, PurePosixPath


def normalize_text(value: str) -> str:
    """Normalize Unicode, line endings, and insignificant whitespace."""
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.split("\n")]
    compacted: list[str] = []
    previous_blank = False
    for line in lines:
        is_blank = not line
        if is_blank and previous_blank:
            continue
        compacted.append(line)
        previous_blank = is_blank
    return "\n".join(compacted).strip()


def normalize_item(value: str) -> str:
    """Normalize a list item for order-insensitive hashing."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip().casefold()


def normalize_items(values: list[str]) -> list[str]:
    """Return sorted unique normalized items."""
    return sorted({item for value in values if (item := normalize_item(value))})


def validate_date(value: str) -> str:
    """Require an unambiguous ISO training date."""
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("date must use zero-padded YYYY-MM-DD")
    return value


def validate_source(value: str) -> str:
    """Allow stable labels or project-relative POSIX paths, never absolute paths."""
    normalized = normalize_item(value).replace("\\", "/")
    if not normalized:
        raise argparse.ArgumentTypeError("source must not be empty")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise argparse.ArgumentTypeError(
            "source must be a project-relative path or a stable source label"
        )
    return path.as_posix()


def read_content(args: argparse.Namespace) -> str:
    """Read exactly one content source."""
    if args.content is not None:
        return args.content
    if args.content_file is not None:
        path = Path(args.content_file)
        if path.is_absolute():
            raise ValueError("content file must use a project-relative path")
        resolved = path.resolve()
        try:
            resolved.relative_to(Path.cwd().resolve())
        except ValueError as exc:
            raise ValueError("content file must stay inside the current project") from exc
        return resolved.read_text(encoding="utf-8")
    return sys.stdin.read()


def build_record_id(
    training_date: str,
    source: str,
    content: str,
    new_words: list[str],
    weak_words: list[str],
    sentences: list[str],
) -> str:
    """Hash a canonical JSON payload and prefix it with the training date."""
    payload = {
        "training_date": training_date,
        "source": source,
        "content": normalize_text(content),
        "new_words": normalize_items(new_words),
        "weak_words": normalize_items(weak_words),
        "sentences": normalize_items(sentences),
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]
    return f"{training_date}-{fingerprint}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a stable record_id from one vocabulary training unit."
    )
    parser.add_argument("--date", required=True, type=validate_date, help="Training date")
    parser.add_argument(
        "--source",
        required=True,
        type=validate_source,
        help="Project-relative source path or stable label",
    )
    content_group = parser.add_mutually_exclusive_group()
    content_group.add_argument("--content", help="Training content as a string")
    content_group.add_argument(
        "--content-file",
        metavar="PATH",
        help="Project-local file containing the training unit; otherwise read stdin",
    )
    parser.add_argument("--new-word", action="append", default=[])
    parser.add_argument("--weak-word", action="append", default=[])
    parser.add_argument("--sentence", action="append", default=[])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        content = read_content(args)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not normalize_text(content):
        print("error: normalized training content must not be empty", file=sys.stderr)
        return 2
    print(
        build_record_id(
            args.date,
            args.source,
            content,
            args.new_word,
            args.weak_word,
            args.sentence,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
