#!/usr/bin/env python3
"""Idempotently update one sharded vocabulary index without loading the full catalog."""

from __future__ import annotations

import argparse
import re
from pathlib import Path, PurePosixPath


ROOT_START = "<!-- letter-index:start -->"
ROOT_END = "<!-- letter-index:end -->"
SHARD_START = "<!-- vocabulary-table:start -->"
SHARD_END = "<!-- vocabulary-table:end -->"

ROOT_ROW_RE = re.compile(
    r"^\|\s*([A-Z])\s*\|\s*(\d+)\s*\|\s*\[查看\]\((\./indexes/[a-z]\.md)\)\s*\|$"
)
SHARD_ROW_RE = re.compile(
    r"^\|\s*\[([^\]]+)\]\((\.\./entries/[a-z]/[^)]+\.md)\)\s*\|\s*(.*?)\s*\|$"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Update one letter shard and the compact root vocabulary index."
    )
    parser.add_argument("--word", required=True, help="Lowercase vocabulary headword")
    parser.add_argument("--meaning", required=True, help="One or two concise core meanings")
    parser.add_argument(
        "--entry",
        help="Project-relative entry path; defaults to entries/<letter>/<word>.md",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Vocabulary archive root; defaults to the current directory",
    )
    return parser.parse_args()


def replace_region(text: str, start: str, end: str, body: str) -> str:
    pattern = re.compile(
        rf"{re.escape(start)}\n.*?\n{re.escape(end)}", re.DOTALL
    )
    replacement = f"{start}\n{body.rstrip()}\n{end}"
    if pattern.search(text):
        return pattern.sub(replacement, text, count=1)
    suffix = "" if text.endswith("\n") else "\n"
    return f"{text}{suffix}\n{replacement}\n"


def write_if_changed(path: Path, content: str) -> None:
    normalized = content.rstrip() + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == normalized:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized, encoding="utf-8")


def default_root_index() -> str:
    return (
        "# 词汇目录\n\n"
        "词汇按首字母分片保存。新增或更新单词时只处理对应字母分目录。\n\n"
        f"{ROOT_START}\n"
        "| 首字母 | 单词数量 | 分目录 |\n"
        "|---|---:|---|\n"
        f"{ROOT_END}\n"
    )


def default_shard(letter: str) -> str:
    return (
        f"# {letter.upper()}\n\n"
        "[返回词汇总目录](../index.md)\n\n"
        f"{SHARD_START}\n"
        "| 单词 | 核心含义 |\n"
        "|---|---|\n"
        f"{SHARD_END}\n"
    )


def parse_root_rows(text: str) -> dict[str, tuple[int, str]]:
    rows: dict[str, tuple[int, str]] = {}
    for line in text.splitlines():
        match = ROOT_ROW_RE.match(line)
        if match:
            rows[match.group(1)] = (int(match.group(2)), match.group(3))
    return rows


def parse_shard_rows(text: str) -> dict[str, tuple[str, str, str]]:
    rows: dict[str, tuple[str, str, str]] = {}
    for line in text.splitlines():
        match = SHARD_ROW_RE.match(line)
        if match:
            rows[match.group(1).casefold()] = (
                match.group(1),
                match.group(2),
                match.group(3),
            )
    return rows


def clean_cell(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().replace("|", r"\|")


def validate_entry(root: Path, entry: str, letter: str) -> str:
    posix = PurePosixPath(entry.replace("\\", "/"))
    if posix.is_absolute() or ".." in posix.parts:
        raise ValueError("entry must be a project-relative path")
    if posix.suffix != ".md" or len(posix.parts) < 3:
        raise ValueError("entry must point to a Markdown file under entries/")
    if posix.parts[0] != "entries" or posix.parts[1] != letter:
        raise ValueError(f"entry must stay under entries/{letter}/")
    if not (root / Path(*posix.parts)).is_file():
        raise ValueError(f"entry file does not exist: {posix.as_posix()}")
    return posix.as_posix()


def main() -> int:
    args = parse_args()
    word = args.word.strip().casefold()
    if not re.fullmatch(r"[a-z][a-z0-9'-]*", word):
        raise SystemExit("error: word must start with a-z and use a safe lowercase form")
    meaning = clean_cell(args.meaning)
    if not meaning:
        raise SystemExit("error: meaning must not be empty")

    root = Path(args.root).resolve()
    letter = word[0]
    entry = args.entry or f"entries/{letter}/{word}.md"
    try:
        entry = validate_entry(root, entry, letter)
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from exc

    shard_path = root / "indexes" / f"{letter}.md"
    shard_text = (
        shard_path.read_text(encoding="utf-8")
        if shard_path.exists()
        else default_shard(letter)
    )
    shard_rows = parse_shard_rows(shard_text)
    shard_rows[word] = (word, f"../{entry}", meaning)
    shard_body = "\n".join(
        [
            "| 单词 | 核心含义 |",
            "|---|---|",
            *[
                f"| [{display}]({link}) | {row_meaning} |"
                for display, link, row_meaning in sorted(
                    shard_rows.values(), key=lambda row: row[0].casefold()
                )
            ],
        ]
    )
    write_if_changed(
        shard_path,
        replace_region(shard_text, SHARD_START, SHARD_END, shard_body),
    )

    root_index_path = root / "index.md"
    root_text = (
        root_index_path.read_text(encoding="utf-8")
        if root_index_path.exists()
        else default_root_index()
    )
    root_rows = parse_root_rows(root_text)
    root_rows[letter.upper()] = (
        len(shard_rows),
        f"./indexes/{letter}.md",
    )
    root_body = "\n".join(
        [
            "| 首字母 | 单词数量 | 分目录 |",
            "|---|---:|---|",
            *[
                f"| {row_letter} | {count} | [查看]({link}) |"
                for row_letter, (count, link) in sorted(root_rows.items())
            ],
        ]
    )
    write_if_changed(
        root_index_path,
        replace_region(root_text, ROOT_START, ROOT_END, root_body),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
