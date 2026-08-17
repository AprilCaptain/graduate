#!/usr/bin/env python3
"""Focused validation tests for daily review schedules."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

import validate_archive  # noqa: E402


UPDATE_SCRIPT = SCRIPTS_ROOT / "update_daily_reviews.py"


class ValidateDailyReviewsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _write_daily(self, label: str) -> Path:
        path = self.root / "daily" / label[:4] / label[5:7] / f"{label}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(
                [
                    f"# {label} 词汇训练",
                    "",
                    "## 第1次训练",
                    "",
                    f"<!-- record_id: {label}-1234abcd -->",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return path

    def _generate(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(UPDATE_SCRIPT),
                "--all",
                "--root",
                str(self.root),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

    def _errors(self) -> list[str]:
        errors: list[str] = []
        validate_archive.validate_daily_reviews(self.root, errors)
        return errors

    def test_accepts_checked_tasks(self) -> None:
        self._write_daily("2026-01-01")
        current = self._write_daily("2026-01-03")
        self._generate()
        current.write_text(
            current.read_text(encoding="utf-8").replace("- [ ]", "- [x]"),
            encoding="utf-8",
        )

        self.assertEqual(self._errors(), [])

    def test_rejects_missing_review_section(self) -> None:
        self._write_daily("2026-01-01")

        self.assertTrue(
            any("missing 今日复习" in error for error in self._errors())
        )

    def test_rejects_wrong_target_order_and_link(self) -> None:
        for label in ("2026-01-01", "2026-01-03", "2026-01-05"):
            current = self._write_daily(label)
        self._generate()
        text = current.read_text(encoding="utf-8")
        text = text.replace(
            "- [ ] [2026-01-03](2026-01-03.md)\n"
            "- [ ] [2026-01-01](2026-01-01.md)",
            "- [ ] [2026-01-01](wrong.md)\n"
            "- [ ] [2026-01-03](2026-01-03.md)",
        )
        current.write_text(text, encoding="utf-8")

        errors = self._errors()

        self.assertTrue(any("dates are" in error for error in errors))

    def test_rejects_invalid_empty_state(self) -> None:
        earliest = self._write_daily("2026-01-01")
        self._generate()
        earliest.write_text(
            earliest.read_text(encoding="utf-8").replace(
                "暂无需复习内容。", "- [ ] 暂无"
            ),
            encoding="utf-8",
        )

        self.assertTrue(
            any("earliest study day" in error for error in self._errors())
        )

    def test_rejects_wrong_relative_link(self) -> None:
        self._write_daily("2026-01-01")
        current = self._write_daily("2026-02-01")
        self._generate()
        current.write_text(
            current.read_text(encoding="utf-8").replace(
                "../01/2026-01-01.md", "2026-01-01.md"
            ),
            encoding="utf-8",
        )

        self.assertTrue(any("review link" in error for error in self._errors()))


if __name__ == "__main__":
    unittest.main()
