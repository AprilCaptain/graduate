#!/usr/bin/env python3
"""Extract only the unprocessed current-branch messages from ChatGPT share HTML."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


ENQUEUE_RE = re.compile(
    r'streamController\.enqueue\(("(?:\\.|[^"\\])*")\)', re.DOTALL
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read ChatGPT share-page HTML and emit current-branch messages after "
            "a stored message cursor."
        )
    )
    parser.add_argument(
        "--html-file",
        help="HTML file to read; otherwise read HTML from standard input",
    )
    parser.add_argument(
        "--after-message-id",
        help="Emit only messages after this message ID on the current branch",
    )
    parser.add_argument(
        "--after-content-sha256",
        help="Verify the cursor message content before slicing",
    )
    parser.add_argument(
        "--candidate-cursor",
        action="append",
        default=[],
        metavar="MESSAGE_ID:SHA256",
        help=(
            "Candidate boundary from any known source cursor. When one or more "
            "candidates are supplied, use the latest exact match and fail if none match."
        ),
    )
    parser.add_argument(
        "--cursor-info",
        action="store_true",
        help="Emit JSON metadata for the final message instead of message content",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "jsonl"),
        default="markdown",
        help="Output format for extracted messages",
    )
    return parser.parse_args()


def read_html(args: argparse.Namespace) -> str:
    if args.html_file:
        return Path(args.html_file).read_text(encoding="utf-8")
    return sys.stdin.read()


def find_flat_payload(html: str) -> list[Any]:
    for match in ENQUEUE_RE.finditer(html):
        try:
            decoded = json.loads(match.group(1))
            flat = json.loads(decoded)
        except (json.JSONDecodeError, TypeError):
            continue
        if (
            isinstance(flat, list)
            and "mapping" in flat
            and "current_node" in flat
        ):
            return flat
    raise ValueError("ChatGPT conversation payload was not found")


class FlatConversation:
    def __init__(self, flat: list[Any]) -> None:
        self.flat = flat
        self.string_indexes: dict[str, int] = {}
        for index, value in enumerate(flat):
            if isinstance(value, str) and value not in self.string_indexes:
                self.string_indexes[value] = index

    def deref(self, reference: Any) -> Any:
        if not isinstance(reference, int) or reference < 0:
            return None
        return self.flat[reference]

    def prop_ref(self, obj: dict[str, Any], name: str) -> Any:
        key_index = self.string_indexes.get(name)
        if key_index is None:
            return None
        return obj.get(f"_{key_index}")

    def prop(self, obj: dict[str, Any], name: str) -> Any:
        return self.deref(self.prop_ref(obj, name))

    def conversation_data(self) -> dict[str, Any]:
        mapping_key = self.string_indexes["mapping"]
        current_key = self.string_indexes["current_node"]
        for value in self.flat:
            if (
                isinstance(value, dict)
                and f"_{mapping_key}" in value
                and f"_{current_key}" in value
            ):
                return value
        raise ValueError("conversation data object was not found")

    def mapping(self, data: dict[str, Any]) -> dict[str, int]:
        mapping_obj = self.prop(data, "mapping")
        if not isinstance(mapping_obj, dict):
            raise ValueError("conversation mapping was not found")
        result: dict[str, int] = {}
        for encoded_key, node_ref in mapping_obj.items():
            if not encoded_key.startswith("_"):
                continue
            node_id = self.deref(int(encoded_key[1:]))
            if isinstance(node_id, str) and isinstance(node_ref, int):
                result[node_id] = node_ref
        return result

    def message_from_node(self, node_ref: int) -> dict[str, Any] | None:
        node = self.deref(node_ref)
        if not isinstance(node, dict):
            return None
        message = self.prop(node, "message")
        if not isinstance(message, dict):
            return None
        message_id = self.prop(message, "id")
        author = self.prop(message, "author")
        role = self.prop(author, "role") if isinstance(author, dict) else None
        create_time = self.prop(message, "create_time")
        content = self.prop(message, "content")
        parts = self.prop(content, "parts") if isinstance(content, dict) else None
        text_parts: list[str] = []
        if isinstance(parts, list):
            for part_ref in parts:
                part = self.deref(part_ref)
                if isinstance(part, str):
                    text_parts.append(part)
        text = "\n".join(text_parts).strip()
        parent = self.prop(node, "parent")
        return {
            "id": message_id,
            "parent": parent,
            "role": role,
            "create_time": create_time,
            "content": text,
            "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }

    def current_branch(self) -> list[dict[str, Any]]:
        data = self.conversation_data()
        mapping = self.mapping(data)
        current = self.prop(data, "current_node")
        if not isinstance(current, str):
            raise ValueError("current conversation node was not found")
        branch: list[dict[str, Any]] = []
        visited: set[str] = set()
        while current and current not in visited:
            visited.add(current)
            node_ref = mapping.get(current)
            if node_ref is None:
                raise ValueError(f"current branch node is missing: {current}")
            message = self.message_from_node(node_ref)
            node = self.deref(node_ref)
            parent = self.prop(node, "parent") if isinstance(node, dict) else None
            if message and message["content"]:
                branch.append(message)
            current = parent if isinstance(parent, str) else ""
        branch.reverse()
        return branch


def slice_after_cursor(
    messages: list[dict[str, Any]],
    message_id: str | None,
    expected_hash: str | None,
) -> list[dict[str, Any]]:
    if not message_id:
        return messages
    for index, message in enumerate(messages):
        if message["id"] == message_id:
            if expected_hash and message["content_sha256"] != expected_hash:
                raise ValueError(
                    "cursor message content changed; incremental extraction is unsafe"
                )
            return messages[index + 1 :]
    raise ValueError("stored message cursor is not on the current conversation branch")


def latest_candidate_cursor(
    messages: list[dict[str, Any]], candidates: list[str]
) -> tuple[str, str] | None:
    if not candidates:
        return None
    parsed: dict[str, str] = {}
    for candidate in candidates:
        try:
            message_id, content_hash = candidate.split(":", 1)
        except ValueError as exc:
            raise ValueError(
                "candidate cursor must use MESSAGE_ID:SHA256"
            ) from exc
        if not message_id or not re.fullmatch(r"[0-9a-f]{64}", content_hash):
            raise ValueError("candidate cursor contains an invalid ID or SHA-256")
        parsed[message_id] = content_hash
    for message in reversed(messages):
        expected_hash = parsed.get(message["id"])
        if expected_hash and message["content_sha256"] == expected_hash:
            return message["id"], expected_hash
    raise ValueError("no candidate cursor matched the current conversation branch")


def emit_markdown(messages: list[dict[str, Any]]) -> None:
    for message in messages:
        print(
            "<!-- message_id: "
            f"{message['id']} | role: {message['role']} | "
            f"create_time: {message['create_time']} -->"
        )
        print(message["content"])
        print()


def main() -> int:
    args = parse_args()
    try:
        flat = find_flat_payload(read_html(args))
        messages = FlatConversation(flat).current_branch()
        if not messages:
            raise ValueError("no text messages were found on the current branch")
        if args.cursor_info:
            final = messages[-1]
            print(
                json.dumps(
                    {
                        key: final[key]
                        for key in (
                            "id",
                            "role",
                            "create_time",
                            "content_sha256",
                        )
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        candidate = latest_candidate_cursor(messages, args.candidate_cursor)
        cursor_id = args.after_message_id
        cursor_hash = args.after_content_sha256
        if candidate:
            if cursor_id:
                raise ValueError(
                    "use either an explicit cursor or candidate cursors, not both"
                )
            cursor_id, cursor_hash = candidate
        selected = slice_after_cursor(messages, cursor_id, cursor_hash)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.format == "jsonl":
        for message in selected:
            print(json.dumps(message, ensure_ascii=False, sort_keys=True))
    else:
        emit_markdown(selected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
