#!/usr/bin/env python3
"""Update multiple affected index shards while reading each shard only once."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import update_index


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read JSONL word updates, update only affected letter shards, and "
            "rewrite the compact root index once."
        )
    )
    parser.add_argument(
        "--input-file",
        help="JSONL file; otherwise read standard input",
    )
    parser.add_argument("--root", default=".")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write; fail if any index file would change",
    )
    return parser.parse_args()


def read_updates(args: argparse.Namespace) -> list[dict[str, str]]:
    if args.input_file:
        text = Path(args.input_file).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()
    updates: list[dict[str, str]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            item: Any = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_number} is not valid JSON") from exc
        if not isinstance(item, dict):
            raise ValueError(f"line {line_number} must contain a JSON object")
        word = item.get("word")
        meaning = item.get("meaning")
        entry = item.get("entry")
        if not isinstance(word, str) or not isinstance(meaning, str):
            raise ValueError(f"line {line_number} requires string word and meaning")
        normalized_word = word.strip().casefold()
        if normalized_word in seen:
            raise ValueError(f"duplicate batch word: {normalized_word}")
        seen.add(normalized_word)
        update: dict[str, str] = {
            "word": normalized_word,
            "meaning": meaning,
        }
        if entry is not None:
            if not isinstance(entry, str):
                raise ValueError(f"line {line_number} entry must be a string")
            update["entry"] = entry
        updates.append(update)
    if not updates:
        raise ValueError("batch contains no word updates")
    return updates


def prepare_word(
    root: Path, update: dict[str, str]
) -> tuple[str, str, str, str]:
    word = update["word"]
    if not re.fullmatch(r"[a-z][a-z0-9'-]*", word):
        raise ValueError(f"unsafe lowercase word: {word!r}")
    meaning = update_index.clean_cell(update["meaning"])
    if not meaning:
        raise ValueError(f"meaning must not be empty for {word}")
    letter = word[0]
    entry = update.get("entry", f"entries/{letter}/{word}.md")
    entry = update_index.validate_entry(root, entry, letter)
    return word, letter, entry, meaning


def changed(path: Path, content: str) -> bool:
    return (
        not path.exists()
        or path.read_text(encoding="utf-8")
        != update_index.normalized_content(content)
    )


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    try:
        updates = read_updates(args)
        grouped: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        for update in updates:
            word, letter, entry, meaning = prepare_word(root, update)
            grouped[letter].append((word, entry, meaning))

        prepared_shards: dict[Path, tuple[str, int]] = {}
        for letter, letter_updates in grouped.items():
            shard_path = root / "indexes" / f"{letter}.md"
            shard_text = (
                shard_path.read_text(encoding="utf-8")
                if shard_path.exists()
                else update_index.default_shard(letter)
            )
            region = update_index.extract_region(
                shard_text,
                update_index.SHARD_START,
                update_index.SHARD_END,
                f"indexes/{letter}.md",
            )
            rows = update_index.parse_shard_rows(region, letter)
            for word, entry, meaning in letter_updates:
                rows[word] = (word, f"../{entry}", meaning)
            body = "\n".join(
                [
                    "| 单词 | 核心含义 |",
                    "|---|---|",
                    *[
                        f"| [{display}]({link}) | {meaning} |"
                        for display, link, meaning in sorted(
                            rows.values(), key=lambda row: row[0].casefold()
                        )
                    ],
                ]
            )
            prepared_shards[shard_path] = (
                update_index.replace_region(
                    shard_text,
                    update_index.SHARD_START,
                    update_index.SHARD_END,
                    body,
                ),
                len(rows),
            )

        root_path = root / "index.md"
        root_text = (
            root_path.read_text(encoding="utf-8")
            if root_path.exists()
            else update_index.default_root_index()
        )
        root_region = update_index.extract_region(
            root_text,
            update_index.ROOT_START,
            update_index.ROOT_END,
            "index.md",
        )
        root_rows = update_index.parse_root_rows(root_region)
        for shard_path, (_content, count) in prepared_shards.items():
            letter = shard_path.stem.upper()
            root_rows[letter] = (count, f"./indexes/{letter.casefold()}.md")
        root_body = "\n".join(
            [
                "| 首字母 | 单词数量 | 分目录 |",
                "|---|---:|---|",
                *[
                    f"| {letter} | {count} | [查看]({link}) |"
                    for letter, (count, link) in sorted(root_rows.items())
                ],
            ]
        )
        new_root_text = update_index.replace_region(
            root_text,
            update_index.ROOT_START,
            update_index.ROOT_END,
            root_body,
        )
        changed_paths = [
            path
            for path, (content, _count) in prepared_shards.items()
            if changed(path, content)
        ]
        if changed(root_path, new_root_text):
            changed_paths.append(root_path)
        if args.check:
            if changed_paths:
                print(
                    "error: batch update would change "
                    + ", ".join(path.relative_to(root).as_posix() for path in changed_paths)
                )
                return 1
            return 0
        if not changed_paths:
            print("no index changes required")
            return 0
        for path, (content, _count) in prepared_shards.items():
            update_index.atomic_write_if_changed(path, content)
        update_index.atomic_write_if_changed(root_path, new_root_text)
        print(
            f"updated {len(updates)} word(s) across "
            f"{len(prepared_shards)} shard(s)"
        )
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
