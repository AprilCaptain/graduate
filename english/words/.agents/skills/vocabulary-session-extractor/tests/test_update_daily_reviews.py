#!/usr/bin/env python3
"""Regression tests for update_daily_reviews.py."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
UPDATE_SCRIPT = SKILL_ROOT / "scripts" / "update_daily_reviews.py"


class UpdateDailyReviewsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _daily_path(self, label: str) -> Path:
        return self.root / "daily" / label[:4] / label[5:7] / f"{label}.md"

    def _write_daily(self, label: str, sessions: int = 1) -> Path:
        path = self._daily_path(label)
        path.parent.mkdir(parents=True, exist_ok=True)
        sections = []
        for session in range(1, sessions + 1):
            sections.append(
                "\n".join(
                    [
                        f"## 第{session}次训练",
                        "",
                        f"<!-- record_id: {label}-0000000{session} -->",
                        "",
                        "### 新学单词",
                        "",
                        "| 单词 | 核心含义 |",
                        "|---|---|",
                    ]
                )
            )
        path.write_text(
            f"# {label} 词汇训练\n\n" + "\n\n".join(sections) + "\n",
            encoding="utf-8",
        )
        return path

    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(UPDATE_SCRIPT),
                *arguments,
                "--root",
                str(self.root),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_builds_discontinuous_cross_month_and_cross_year_links(self) -> None:
        labels = [
            "2025-12-30",
            "2026-01-05",
            "2026-01-20",
            "2026-02-02",
            "2026-02-10",
            "2026-02-18",
            "2026-03-01",
            "2026-03-10",
        ]
        for label in labels:
            self._write_daily(label)

        result = self._run("--all")

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        earliest = self._daily_path(labels[0]).read_text(encoding="utf-8")
        self.assertIn("## 今日复习", earliest)
        self.assertIn("暂无需复习内容。", earliest)
        current = self._daily_path(labels[-1]).read_text(encoding="utf-8")
        expected_tasks = "\n".join(
            [
                "- [ ] [2026-03-01](2026-03-01.md)",
                "- [ ] [2026-02-18](../02/2026-02-18.md)",
                "- [ ] [2026-02-02](../02/2026-02-02.md)",
                "- [ ] [2025-12-30](../../2025/12/2025-12-30.md)",
            ]
        )
        self.assertIn(expected_tasks, current)

    def test_preserves_completion_and_content_outside_managed_region(self) -> None:
        self._write_daily("2026-01-01")
        current_path = self._write_daily("2026-01-03")
        first = self._run("--all")
        self.assertEqual(first.returncode, 0, msg=first.stdout + first.stderr)

        text = current_path.read_text(encoding="utf-8")
        text = text.replace(
            "- [ ] [2026-01-01]", "- [x] [2026-01-01]"
        ).replace(
            "<!-- daily-review:end -->",
            "<!-- daily-review:end -->\n\n人工复习备注。",
        )
        current_path.write_text(text, encoding="utf-8")

        second = self._run("--date", "2026-01-03")

        self.assertEqual(second.returncode, 0, msg=second.stdout + second.stderr)
        updated = current_path.read_text(encoding="utf-8")
        self.assertIn("- [x] [2026-01-01]", updated)
        self.assertIn("人工复习备注。", updated)

    def test_is_idempotent_and_check_mode_is_read_only(self) -> None:
        for label in ("2026-01-01", "2026-01-04", "2026-01-10"):
            self._write_daily(label)
        first = self._run("--all")
        self.assertEqual(first.returncode, 0, msg=first.stdout + first.stderr)
        snapshot = {
            path: path.read_text(encoding="utf-8")
            for path in self.root.glob("daily/**/*.md")
        }

        second = self._run("--all")
        check = self._run("--all", "--check")

        self.assertEqual(second.returncode, 0, msg=second.stdout + second.stderr)
        self.assertIn("updated 0 of 3", second.stdout)
        self.assertEqual(check.returncode, 0, msg=check.stdout + check.stderr)
        self.assertEqual(
            snapshot,
            {path: path.read_text(encoding="utf-8") for path in snapshot},
        )

    def test_check_reports_missing_reviews_without_writing(self) -> None:
        path = self._write_daily("2026-01-01")
        before = path.read_text(encoding="utf-8")

        result = self._run("--all", "--check")

        self.assertEqual(result.returncode, 1)
        self.assertIn("OUTDATED: daily/2026/01/2026-01-01.md", result.stdout)
        self.assertEqual(before, path.read_text(encoding="utf-8"))

    def test_backdated_study_day_recalculates_later_targets(self) -> None:
        for label in ("2026-01-01", "2026-01-03", "2026-01-05"):
            self._write_daily(label)
        first = self._run("--all")
        self.assertEqual(first.returncode, 0, msg=first.stdout + first.stderr)
        before = self._daily_path("2026-01-05").read_text(encoding="utf-8")
        self.assertIn("- [ ] [2026-01-01]", before)

        self._write_daily("2026-01-02", sessions=2)
        second = self._run("--all")

        self.assertEqual(second.returncode, 0, msg=second.stdout + second.stderr)
        after = self._daily_path("2026-01-05").read_text(encoding="utf-8")
        self.assertIn("- [ ] [2026-01-03]", after)
        self.assertIn("- [ ] [2026-01-02]", after)
        self.assertNotIn("- [ ] [2026-01-01]", after)
        inserted = self._daily_path("2026-01-02").read_text(encoding="utf-8")
        self.assertEqual(inserted.count("## 今日复习"), 1)


if __name__ == "__main__":
    unittest.main()
