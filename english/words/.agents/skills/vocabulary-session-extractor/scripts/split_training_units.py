#!/usr/bin/env python3
"""Split incremental vocabulary-chat JSONL into completed training units."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SUMMARY_TITLES = (
    "本次学习记录",
    "今日学习记录",
    "本次训练记录",
    "词汇训练总结",
    "今日训练总结",
    "学习总结",
)
SUMMARY_RE = re.compile(
    r"^\s{0,3}(?:#{1,6}\s*)?(?:\*\*|【)?(?:"
    + "|".join(map(re.escape, SUMMARY_TITLES))
    + r")(?:\*\*|】)?(?:\s*[（(][^）)]*[）)])?\s*[:：]?\s*$"
)
START_RE = re.compile(
    r"(?:开始|继续).{0,18}(?:词汇|单词).{0,10}(?:训练|学习)"
    r"|按照.{0,18}(?:计划|安排).{0,10}开始"
    r"|开始第[一二三四五六七八九十0-9]+组"
)


@dataclass(frozen=True)
class Unit:
    start: int
    end: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read message JSONL, identify completed vocabulary-training units, "
            "and emit metadata or exactly one selected unit."
        )
    )
    parser.add_argument(
        "--input-file",
        help="JSONL file to read; otherwise read standard input",
    )
    parser.add_argument(
        "--manifest",
        action="store_true",
        help="Emit boundary metadata only, never message content",
    )
    parser.add_argument(
        "--unit-index",
        type=int,
        metavar="N",
        help="Emit completed unit N (one-based)",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "jsonl", "content"),
        default="markdown",
        help=(
            "Selected-unit output: message markers, original JSONL, or "
            "source-independent role/content text"
        ),
    )
    return parser.parse_args()


def read_messages(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.input_file:
        text = Path(args.input_file).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()
    messages: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_number} is not valid JSON") from exc
        if not isinstance(message, dict):
            raise ValueError(f"line {line_number} must contain a JSON object")
        for field in ("id", "role", "content"):
            if not isinstance(message.get(field), str) or not message[field]:
                raise ValueError(f"line {line_number} has invalid {field!r}")
        if message["id"] in seen_ids:
            raise ValueError(f"duplicate message ID: {message['id']}")
        seen_ids.add(message["id"])
        digest = hashlib.sha256(message["content"].encode("utf-8")).hexdigest()
        stored_digest = message.get("content_sha256")
        if stored_digest is not None and stored_digest != digest:
            raise ValueError(
                f"content hash mismatch for message {message['id']}"
            )
        message["content_sha256"] = digest
        messages.append(message)
    if not messages:
        raise ValueError("no messages were supplied")
    return messages


def normalized_lines(content: str) -> list[str]:
    value = unicodedata.normalize("NFKC", content)
    return [line.strip() for line in value.splitlines() if line.strip()]


def is_summary(message: dict[str, Any]) -> bool:
    if message["role"].casefold() != "assistant":
        return False
    return any(SUMMARY_RE.fullmatch(line) for line in normalized_lines(message["content"]))


def is_start(message: dict[str, Any]) -> bool:
    if message["role"].casefold() != "user":
        return False
    return bool(START_RE.search(unicodedata.normalize("NFKC", message["content"])))


def find_units(messages: list[dict[str, Any]]) -> list[Unit]:
    units: list[Unit] = []
    previous_end = -1
    for end, message in enumerate(messages):
        if not is_summary(message):
            continue
        start = previous_end + 1
        for candidate in range(end, previous_end, -1):
            if is_start(messages[candidate]):
                start = candidate
                break
        if start <= end:
            units.append(Unit(start=start, end=end))
            previous_end = end
    return units


def message_meta(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "content_sha256": message["content_sha256"],
        "id": message["id"],
        "role": message["role"],
    }


def build_manifest(messages: list[dict[str, Any]], units: list[Unit]) -> dict[str, Any]:
    last_end = units[-1].end if units else -1
    tail = messages[last_end + 1 :]
    return {
        "completed_unit_count": len(units),
        "incomplete_tail": {
            "message_count": len(tail),
            "start": message_meta(tail[0]) if tail else None,
        },
        "units": [
            {
                "end": message_meta(messages[unit.end]),
                "index": index,
                "message_count": unit.end - unit.start + 1,
                "start": message_meta(messages[unit.start]),
            }
            for index, unit in enumerate(units, 1)
        ],
    }


def emit_markdown(messages: list[dict[str, Any]]) -> None:
    for message in messages:
        print(
            "<!-- message_id: "
            f"{message['id']} | role: {message['role']} | "
            f"create_time: {message.get('create_time')} | "
            f"content_sha256: {message['content_sha256']} -->"
        )
        print(message["content"])
        print()


def emit_content(messages: list[dict[str, Any]]) -> None:
    for index, message in enumerate(messages):
        if index:
            print()
        print(f"[{message['role'].casefold()}]")
        print(message["content"].strip())


def main() -> int:
    args = parse_args()
    if args.manifest and args.unit_index is not None:
        print("error: --manifest and --unit-index cannot be combined", file=sys.stderr)
        return 2
    try:
        messages = read_messages(args)
        units = find_units(messages)
        if args.manifest:
            print(
                json.dumps(
                    build_manifest(messages, units),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        if not units:
            raise ValueError("no completed training unit was found")
        if args.unit_index is None:
            if len(units) != 1:
                raise ValueError(
                    "multiple completed units found; select exactly one with --unit-index"
                )
            unit = units[0]
        else:
            if args.unit_index < 1 or args.unit_index > len(units):
                raise ValueError(
                    f"unit index must be between 1 and {len(units)}"
                )
            unit = units[args.unit_index - 1]
        selected = messages[unit.start : unit.end + 1]
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.format == "jsonl":
        for message in selected:
            print(json.dumps(message, ensure_ascii=False, sort_keys=True))
    elif args.format == "content":
        emit_content(selected)
    else:
        emit_markdown(selected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
