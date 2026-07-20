#!/usr/bin/env python3
"""Validate a vocabulary archive without third-party dependencies."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import update_index


LINK_RE = re.compile(r"(?<!!)\[[^\]]*]\(([^)]+)\)")
RECORD_RE = re.compile(r"<!--\s*record_id:\s*([^\s]+)\s*-->")
FINGERPRINT_RE = re.compile(r"sha256:[0-9a-f]{64}")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
TABLE_ROW_RE = re.compile(r"^\|\s*(.*?)\s*\|\s*(.*?)\s*\|$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate entries, daily records, indexes, links, and metadata."
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Vocabulary archive root; defaults to the current directory",
    )
    return parser.parse_args()


def scalar(value: str) -> Any:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    if value.isdigit():
        return int(value)
    return value


def parse_simple_yaml(text: str, label: str) -> dict[str, Any]:
    """Parse the mapping-only YAML subset used by this archive."""
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if "\t" in raw_line[: len(raw_line) - len(raw_line.lstrip())]:
            raise ValueError(f"{label}:{line_number}: YAML indentation uses tabs")
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        match = re.fullmatch(r"([^:#][^:]*):(?:\s*(.*))?", raw_line.strip())
        if not match:
            raise ValueError(f"{label}:{line_number}: unsupported YAML syntax")
        key = match.group(1).strip()
        value = match.group(2) or ""
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise ValueError(f"{label}:{line_number}: invalid YAML indentation")
        parent = stack[-1][1]
        if key in parent:
            raise ValueError(f"{label}:{line_number}: duplicate YAML key {key!r}")
        if value:
            parent[key] = scalar(value)
        else:
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
    return root


def front_matter(text: str, label: str) -> dict[str, Any]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError(f"{label}: missing YAML front matter")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise ValueError(f"{label}: unclosed YAML front matter") from exc
    return parse_simple_yaml("\n".join(lines[1:end]), label)


def valid_date(value: Any) -> bool:
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def section(text: str, heading: str) -> str | None:
    match = re.search(
        rf"^## {re.escape(heading)}\s*$\n(.*?)(?=^## |\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(1) if match else None


def table_rows(section_text: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in section_text.splitlines():
        match = TABLE_ROW_RE.fullmatch(line)
        if match:
            rows.append((match.group(1).strip(), match.group(2).strip()))
    return rows


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def check_links(path: Path, root: Path, errors: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    for line_number, line in enumerate(text.splitlines(), 1):
        if line.endswith((" ", "\t")):
            errors.append(f"{path.relative_to(root)}:{line_number}: trailing whitespace")
        for target in LINK_RE.findall(line):
            target = target.strip()
            if (
                not target
                or target.startswith(("#", "http://", "https://", "mailto:"))
                or "{{" in target
            ):
                continue
            clean_target = target.split("#", 1)[0]
            resolved = (path.parent / clean_target).resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                errors.append(
                    f"{path.relative_to(root)}:{line_number}: link leaves archive: {target}"
                )
                continue
            if not resolved.exists():
                errors.append(
                    f"{path.relative_to(root)}:{line_number}: broken link: {target}"
                )


def validate_entries(root: Path, errors: list[str]) -> dict[str, Path]:
    entries: dict[str, Path] = {}
    for path in sorted((root / "entries").glob("*/*.md")):
        label = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        try:
            metadata = front_matter(text, label)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        for key in ("word", "first_learned", "last_updated"):
            if key not in metadata:
                errors.append(f"{label}: missing front-matter field {key!r}")
        word = metadata.get("word")
        if not isinstance(word, str):
            continue
        key = word.casefold()
        if key in entries:
            errors.append(f"{label}: duplicate entry word {word!r}")
        entries[key] = path
        if word != path.stem or path.parent.name != word[0].casefold():
            errors.append(f"{label}: word/path mismatch")
        if not valid_date(metadata.get("first_learned")):
            errors.append(f"{label}: invalid first_learned date")
        if not valid_date(metadata.get("last_updated")):
            errors.append(f"{label}: invalid last_updated date")
        for forbidden in ("status", "mastery_level"):
            if forbidden in metadata:
                errors.append(f"{label}: forbidden learning-state field {forbidden!r}")
        if re.search(r"^## 易错点\s*$", text, re.MULTILINE):
            errors.append(f"{label}: word entry must not contain an 易错点 section")
        core = section(text, "核心词义")
        if core is None:
            errors.append(f"{label}: missing 核心词义 section")
        else:
            rows = table_rows(core)
            if len(rows) < 3 or rows[0] != ("词性", "核心含义"):
                errors.append(f"{label}: 核心词义 must be a two-column standard table")
        collocations = section(text, "常用搭配")
        if collocations:
            seen_collocations: set[str] = set()
            for row in collocations.splitlines()[2:]:
                match = re.match(r"^\|\s*(.*?)\s*\|", row)
                if not match:
                    continue
                item = normalize(match.group(1))
                if item in seen_collocations:
                    errors.append(f"{label}: duplicate collocation {match.group(1)!r}")
                seen_collocations.add(item)
        sentences = [
            normalize(match.group(1))
            for match in re.finditer(
                r"^#### 原句\s*$\n\s*\n(.+?)(?=\n\s*\n#### |\n\s*\n### |\Z)",
                text,
                re.MULTILINE | re.DOTALL,
            )
        ]
        if len(sentences) != len(set(sentences)):
            errors.append(f"{label}: duplicate training sentence")
    return entries


def validate_indexes(
    root: Path, entries: dict[str, Path], errors: list[str]
) -> None:
    root_path = root / "index.md"
    if not root_path.is_file():
        errors.append("index.md: missing root index")
        return
    try:
        root_region = update_index.extract_region(
            root_path.read_text(encoding="utf-8"),
            update_index.ROOT_START,
            update_index.ROOT_END,
            "index.md",
        )
        root_rows = update_index.parse_root_rows(root_region)
    except ValueError as exc:
        errors.append(f"index.md: {exc}")
        return
    if list(root_rows) != sorted(root_rows):
        errors.append("index.md: letter rows are not sorted")

    indexed: dict[str, Path] = {}
    shard_paths = sorted((root / "indexes").glob("*.md"))
    expected_letters = {path.parent.name.upper() for path in entries.values()}
    actual_letters = {path.stem.upper() for path in shard_paths}
    if actual_letters != expected_letters:
        errors.append("indexes/: shard letters do not match entry letters")
    if set(root_rows) != expected_letters:
        errors.append("index.md: root letters do not match entry letters")

    for shard_path in shard_paths:
        letter = shard_path.stem
        label = shard_path.relative_to(root).as_posix()
        try:
            region = update_index.extract_region(
                shard_path.read_text(encoding="utf-8"),
                update_index.SHARD_START,
                update_index.SHARD_END,
                label,
            )
            rows = update_index.parse_shard_rows(region, letter)
        except ValueError as exc:
            errors.append(f"{label}: {exc}")
            continue
        displays = [row[0].casefold() for row in rows.values()]
        if displays != sorted(displays):
            errors.append(f"{label}: words are not sorted")
        root_row = root_rows.get(letter.upper())
        if root_row and root_row[0] != len(rows):
            errors.append(f"{label}: root count is {root_row[0]}, expected {len(rows)}")
        for word, (_display, link, _meaning) in rows.items():
            resolved = (shard_path.parent / link).resolve()
            expected = entries.get(word)
            if expected is None:
                errors.append(f"{label}: index word has no entry: {word}")
            elif resolved != expected.resolve():
                errors.append(f"{label}: wrong entry link for {word}")
            if word in indexed:
                errors.append(f"{label}: word indexed more than once: {word}")
            indexed[word] = resolved
    if set(indexed) != set(entries):
        missing = sorted(set(entries) - set(indexed))
        if missing:
            errors.append(f"indexes/: entries missing from index: {', '.join(missing)}")


def validate_daily(root: Path, errors: list[str]) -> set[str]:
    record_ids: set[str] = set()
    for path in sorted((root / "daily").glob("**/*.md")):
        label = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        for record_id in RECORD_RE.findall(text):
            if record_id in record_ids:
                errors.append(f"{label}: duplicate record_id {record_id}")
            record_ids.add(record_id)
        sections = re.findall(
            r"^### 新学单词\s*$\n(.*?)(?=^### |\Z)",
            text,
            re.MULTILINE | re.DOTALL,
        )
        if not sections:
            errors.append(f"{label}: missing 新学单词 section")
        for new_words in sections:
            rows = table_rows(new_words)
            if len(rows) < 2 or rows[0] != ("单词", "核心含义"):
                errors.append(f"{label}: 新学单词 must use a two-column table")
            if re.search(r"^\s*[-*+]\s+\[[^\]]+]\(", new_words, re.MULTILINE):
                errors.append(f"{label}: 新学单词 contains a bullet entry")
    return record_ids


def validate_metadata(
    root: Path, daily_record_ids: set[str], errors: list[str]
) -> None:
    state_path = root / "metadata" / "extraction-state.yml"
    cursor_path = root / "metadata" / "source-cursors.yml"
    try:
        state = parse_simple_yaml(
            state_path.read_text(encoding="utf-8"),
            state_path.relative_to(root).as_posix(),
        )
        cursors = parse_simple_yaml(
            cursor_path.read_text(encoding="utf-8"),
            cursor_path.relative_to(root).as_posix(),
        )
    except (OSError, UnicodeError, ValueError) as exc:
        errors.append(str(exc))
        return
    records = state.get("processed_records")
    sources = cursors.get("sources")
    if state.get("version") != 2:
        errors.append("metadata: extraction-state.yml must use schema version 2")
    if cursors.get("version") != 2:
        errors.append("metadata: source-cursors.yml must use schema version 2")
    if not isinstance(records, dict) or not isinstance(sources, dict):
        errors.append("metadata: processed_records and sources must be mappings")
        return
    fingerprints: dict[str, str] = {}
    for record_id, record in records.items():
        if not isinstance(record, dict):
            errors.append(f"metadata: record {record_id} must be a mapping")
            continue
        if record_id not in daily_record_ids:
            errors.append(f"metadata: record missing from daily files: {record_id}")
        for key in (
            "training_date",
            "source",
            "daily_file",
            "status",
            "content_fingerprint",
            "source_boundary_kind",
        ):
            if not record.get(key):
                errors.append(f"metadata: record {record_id} missing {key}")
        if record.get("status") != "completed":
            errors.append(f"metadata: record {record_id} is not completed")
        fingerprint = record.get("content_fingerprint")
        if not isinstance(fingerprint, str) or not FINGERPRINT_RE.fullmatch(
            fingerprint
        ):
            errors.append(f"metadata: record {record_id} has invalid fingerprint")
        elif fingerprint in fingerprints:
            errors.append(
                "metadata: duplicate content fingerprint for "
                f"{fingerprints[fingerprint]} and {record_id}"
            )
        else:
            fingerprints[fingerprint] = record_id
        daily_file = record.get("daily_file")
        if isinstance(daily_file, str) and not (root / daily_file).is_file():
            errors.append(f"metadata: daily file does not exist: {daily_file}")
        source_name = record.get("source")
        source = sources.get(source_name)
        if not isinstance(source, dict):
            errors.append(f"metadata: record source has no cursor: {source_name}")
        else:
            boundary_kind = record.get("source_boundary_kind")
            if boundary_kind == "message-range":
                for key in (
                    "source_start_message_id",
                    "source_start_message_sha256",
                    "source_end_message_id",
                    "source_end_message_sha256",
                    "source_message_count",
                ):
                    if key not in record or record[key] in ("", None):
                        errors.append(f"metadata: record {record_id} missing {key}")
                for key in (
                    "source_start_message_sha256",
                    "source_end_message_sha256",
                ):
                    value = record.get(key)
                    if not isinstance(value, str) or not re.fullmatch(
                        r"[0-9a-f]{64}", value
                    ):
                        errors.append(
                            f"metadata: record {record_id} has invalid {key}"
                        )
                count = record.get("source_message_count")
                if not isinstance(count, int) or count < 1:
                    errors.append(
                        f"metadata: record {record_id} has invalid message count"
                    )
            elif boundary_kind == "byte-range":
                start = record.get("source_start_byte_offset")
                end = record.get("source_end_byte_offset")
                prefix_hash = record.get("source_end_prefix_sha256")
                if (
                    not isinstance(start, int)
                    or not isinstance(end, int)
                    or start < 0
                    or end <= start
                ):
                    errors.append(
                        f"metadata: record {record_id} has invalid byte range"
                    )
                if not isinstance(prefix_hash, str) or not re.fullmatch(
                    r"[0-9a-f]{64}", prefix_hash
                ):
                    errors.append(
                        f"metadata: record {record_id} has invalid prefix hash"
                    )
            else:
                errors.append(
                    f"metadata: record {record_id} has unknown source boundary kind"
                )

            if source.get("last_record_id") == record_id:
                if boundary_kind == "message-range" and (
                    source.get("last_message_id")
                    != record.get("source_end_message_id")
                    or source.get("last_message_sha256")
                    != record.get("source_end_message_sha256")
                ):
                    errors.append(
                        f"metadata: cursor end does not match record {record_id}"
                    )
                if boundary_kind == "byte-range" and (
                    source.get("last_byte_offset")
                    != record.get("source_end_byte_offset")
                    or source.get("last_prefix_sha256")
                    != record.get("source_end_prefix_sha256")
                ):
                    errors.append(
                        f"metadata: text cursor end does not match record {record_id}"
                    )
    if set(records) != daily_record_ids:
        missing = sorted(daily_record_ids - set(records))
        if missing:
            errors.append(
                f"metadata: daily record IDs missing from state: {', '.join(missing)}"
            )
    if state.get("last_processed_record") not in records:
        errors.append("metadata: last_processed_record is not a processed record")
    for source_name, source in sources.items():
        if not isinstance(source, dict):
            errors.append(f"metadata: source {source_name} must be a mapping")
            continue
        for key in ("kind", "location", "last_record_id", "last_training_date"):
            if key not in source or source[key] in ("", None):
                errors.append(f"metadata: source {source_name} missing {key}")
        last_record_id = source.get("last_record_id")
        if last_record_id not in records:
            errors.append(
                f"metadata: source {source_name} points to unknown record "
                f"{last_record_id}"
            )
        has_message = "last_message_id" in source or "last_message_sha256" in source
        has_byte = "last_byte_offset" in source or "last_prefix_sha256" in source
        if has_message == has_byte:
            errors.append(
                f"metadata: source {source_name} must have exactly one cursor type"
            )
        elif has_message:
            cursor_hash = source.get("last_message_sha256")
            if not source.get("last_message_id") or not isinstance(
                cursor_hash, str
            ) or not re.fullmatch(r"[0-9a-f]{64}", cursor_hash):
                errors.append(
                    f"metadata: source {source_name} has invalid message cursor"
                )
        else:
            offset = source.get("last_byte_offset")
            prefix_hash = source.get("last_prefix_sha256")
            if (
                not isinstance(offset, int)
                or offset < 0
                or not isinstance(prefix_hash, str)
                or not re.fullmatch(r"[0-9a-f]{64}", prefix_hash)
            ):
                errors.append(
                    f"metadata: source {source_name} has invalid byte cursor"
                )


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    errors: list[str] = []
    entries = validate_entries(root, errors)
    validate_indexes(root, entries, errors)
    daily_record_ids = validate_daily(root, errors)
    validate_metadata(root, daily_record_ids, errors)
    markdown_paths = [
        root / "index.md",
        *sorted((root / "indexes").glob("*.md")),
        *sorted((root / "entries").glob("*/*.md")),
        *sorted((root / "daily").glob("**/*.md")),
    ]
    for path in markdown_paths:
        if path.is_file():
            check_links(path, root, errors)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        print(f"FAILED: {len(errors)} issue(s)")
        return 1
    print(
        "OK: "
        f"{len(entries)} entries, {len(daily_record_ids)} training record(s), "
        "indexes, links, and metadata are consistent"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
