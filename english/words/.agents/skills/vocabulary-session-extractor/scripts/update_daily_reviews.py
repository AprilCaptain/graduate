#!/usr/bin/env python3
"""Create or refresh deterministic daily review task lists."""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path


REVIEW_OFFSETS = (1, 2, 4, 7, 15)
REVIEW_HEADING = "## 今日复习"
REVIEW_START = "<!-- daily-review:start -->"
REVIEW_END = "<!-- daily-review:end -->"
EMPTY_REVIEW = "暂无需复习内容。"
RECORD_RE = re.compile(r"<!--\s*record_id:\s*[^\s]+\s*-->")
TRAINING_RE = re.compile(r"^## 第\d+次训练\s*$", re.MULTILINE)
TASK_RE = re.compile(
    r"^- \[([ xX])\] \[(\d{4}-\d{2}-\d{2})\]\(([^)]+)\)$"
)


class ReviewUpdateError(ValueError):
    """Raised when a daily file cannot be updated safely."""


@dataclass(frozen=True)
class StudyDay:
    training_date: date
    path: Path
    text: str

    @property
    def label(self) -> str:
        return self.training_date.isoformat()


@dataclass(frozen=True)
class ReviewRegion:
    content_start: int
    content_end: int
    statuses: dict[str, str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Update 今日复习 from the 1st, 2nd, 4th, 7th, and 15th "
            "previous actual study days."
        )
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--date",
        help="Update one actual study date in YYYY-MM-DD format",
    )
    selection.add_argument(
        "--all",
        action="store_true",
        help="Update every actual study date",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report stale files without writing them",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Vocabulary archive root; defaults to the current directory",
    )
    return parser.parse_args()


def parse_path_date(path: Path, daily_root: Path) -> date | None:
    try:
        relative = path.relative_to(daily_root)
    except ValueError:
        return None
    if len(relative.parts) != 3:
        return None
    year, month, filename = relative.parts
    if not re.fullmatch(r"\d{4}", year) or not re.fullmatch(r"\d{2}", month):
        return None
    if not filename.endswith(".md"):
        return None
    label = filename[:-3]
    try:
        parsed = date.fromisoformat(label)
    except ValueError:
        return None
    if parsed.isoformat() != label or parsed.strftime("%Y/%m") != f"{year}/{month}":
        return None
    return parsed


def discover_study_days(root: Path) -> list[StudyDay]:
    daily_root = root / "daily"
    study_days: list[StudyDay] = []
    if not daily_root.is_dir():
        return study_days
    for path in sorted(daily_root.glob("**/*.md")):
        training_date = parse_path_date(path, daily_root)
        if training_date is None:
            continue
        text = path.read_text(encoding="utf-8")
        if not RECORD_RE.search(text) or not TRAINING_RE.search(text):
            continue
        study_days.append(StudyDay(training_date, path, text))
    study_days.sort(key=lambda item: item.training_date)
    duplicates = [
        current.label
        for previous, current in zip(study_days, study_days[1:])
        if previous.training_date == current.training_date
    ]
    if duplicates:
        raise ReviewUpdateError(
            "duplicate daily files for study date(s): " + ", ".join(duplicates)
        )
    return study_days


def review_targets(study_days: list[StudyDay], index: int) -> list[StudyDay]:
    return [study_days[index - offset] for offset in REVIEW_OFFSETS if index >= offset]


def relative_link(current: Path, target: Path) -> str:
    return os.path.relpath(target, start=current.parent).replace(os.sep, "/")


def _single_line_position(text: str, line: str, label: str) -> tuple[int, int] | None:
    matches = list(re.finditer(rf"^{re.escape(line)}$", text, re.MULTILINE))
    if len(matches) > 1:
        raise ReviewUpdateError(f"{label}: duplicate {line!r} lines")
    if not matches:
        return None
    return matches[0].start(), matches[0].end()


def find_review_region(text: str, label: str) -> ReviewRegion | None:
    heading = _single_line_position(text, REVIEW_HEADING, label)
    start = _single_line_position(text, REVIEW_START, label)
    end = _single_line_position(text, REVIEW_END, label)
    if heading is None and start is None and end is None:
        return None
    if heading is None or start is None or end is None:
        raise ReviewUpdateError(
            f"{label}: 今日复习 must contain one heading and both managed markers"
        )
    if not (heading[0] < start[0] < end[0]):
        raise ReviewUpdateError(f"{label}: invalid 今日复习 marker order")
    next_heading = re.search(r"^## (?!今日复习$).+$", text[heading[1] :], re.MULTILINE)
    section_end = len(text) if next_heading is None else heading[1] + next_heading.start()
    if end[1] > section_end:
        raise ReviewUpdateError(f"{label}: 今日复习 markers leave their section")
    managed_content = text[start[1] : end[0]].strip("\n")
    statuses: dict[str, str] = {}
    for line in managed_content.splitlines():
        match = TASK_RE.fullmatch(line)
        if not match:
            continue
        task_date = match.group(2)
        if task_date in statuses:
            raise ReviewUpdateError(f"{label}: duplicate review task {task_date}")
        statuses[task_date] = "x" if match.group(1).casefold() == "x" else " "
    return ReviewRegion(start[1], end[0], statuses)


def render_managed_content(
    current: StudyDay,
    targets: list[StudyDay],
    statuses: dict[str, str],
) -> str:
    if not targets:
        return f"\n{EMPTY_REVIEW}\n"
    lines = []
    for target in targets:
        status = statuses.get(target.label, " ")
        link = relative_link(current.path, target.path)
        lines.append(f"- [{status}] [{target.label}]({link})")
    return "\n" + "\n".join(lines) + "\n"


def update_daily_text(current: StudyDay, targets: list[StudyDay]) -> str:
    label = current.path.as_posix()
    region = find_review_region(current.text, label)
    if region is not None:
        content = render_managed_content(current, targets, region.statuses)
        return (
            current.text[: region.content_start]
            + content
            + current.text[region.content_end :]
        )

    lines = current.text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != f"# {current.label} 词汇训练":
        raise ReviewUpdateError(
            f"{label}: first line must be '# {current.label} 词汇训练'"
        )
    insertion = (
        "\n"
        f"{REVIEW_HEADING}\n\n"
        f"{REVIEW_START}"
        f"{render_managed_content(current, targets, {})}"
        f"{REVIEW_END}\n"
    )
    return lines[0] + insertion + "".join(lines[1:])


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    try:
        study_days = discover_study_days(root)
        if not study_days:
            raise ReviewUpdateError("no actual study days found")
        selected_indexes: list[int]
        if args.all:
            selected_indexes = list(range(len(study_days)))
        else:
            try:
                requested = date.fromisoformat(args.date)
            except ValueError as exc:
                raise ReviewUpdateError(
                    f"invalid --date value: {args.date!r}"
                ) from exc
            selected_indexes = [
                index
                for index, item in enumerate(study_days)
                if item.training_date == requested
            ]
            if not selected_indexes:
                raise ReviewUpdateError(
                    f"{args.date}: no actual study day with a formal training record"
                )

        changed: list[Path] = []
        for index in selected_indexes:
            current = study_days[index]
            updated = update_daily_text(current, review_targets(study_days, index))
            if updated == current.text:
                continue
            changed.append(current.path)
            if not args.check:
                current.path.write_text(updated, encoding="utf-8")

        if args.check and changed:
            for path in changed:
                print(f"OUTDATED: {path.relative_to(root).as_posix()}")
            print(f"FAILED: {len(changed)} daily review file(s) need updates")
            return 1
        action = "would update" if args.check else "updated"
        print(f"OK: {action} {len(changed)} of {len(selected_indexes)} daily file(s)")
        return 0
    except (OSError, UnicodeError, ReviewUpdateError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
