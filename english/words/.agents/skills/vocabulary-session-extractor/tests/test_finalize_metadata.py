#!/usr/bin/env python3
"""End-to-end regression tests for finalize_metadata.py."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
FINALIZE_SCRIPT = SKILL_ROOT / "scripts" / "finalize_metadata.py"
VALIDATE_SCRIPT = SKILL_ROOT / "scripts" / "validate_archive.py"

RECORD_ID = "2026-01-02-1234abcd"
FINGERPRINT = f"sha256:{'c' * 64}"
SOURCE = "chatgpt-share-test"
DAILY_FILE = "daily/2026/01/2026-01-02.md"


class FinalizeMetadataEndToEndTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self._build_minimal_archive()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _write(self, relative_path: str, content: str) -> None:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")

    def _build_minimal_archive(self) -> None:
        self._write(
            "entries/a/adapt.md",
            """
            ---
            word: "adapt"
            first_learned: "2026-01-02"
            last_updated: "2026-01-02"
            ---

            # adapt

            ## 核心词义

            | 词性 | 核心含义 |
            |---|---|
            | v. | 适应 |

            ## 学习记录

            - 2026-01-02 · 新词 · chatgpt-share-test <!-- record_id: 2026-01-02-1234abcd -->
            """,
        )
        self._write(
            "indexes/a.md",
            """
            # A

            [返回词汇总目录](../index.md)

            <!-- vocabulary-table:start -->
            | 单词 | 核心含义 |
            |---|---|
            | [adapt](../entries/a/adapt.md) | 适应 |
            <!-- vocabulary-table:end -->
            """,
        )
        self._write(
            "index.md",
            """
            # 词汇目录

            <!-- letter-index:start -->
            | 首字母 | 单词数量 | 分目录 |
            |---|---:|---|
            | A | 1 | [查看](./indexes/a.md) |
            <!-- letter-index:end -->
            """,
        )
        self._write(
            DAILY_FILE,
            """
            # 2026-01-02 词汇训练

            ## 今日复习

            <!-- daily-review:start -->
            暂无需复习内容。
            <!-- daily-review:end -->

            ## 第1次训练

            <!-- record_id: 2026-01-02-1234abcd -->

            ### 学习概况

            | 项目 | 内容 |
            |---|---|
            | 学习内容 | 最小端到端测试 |
            | 新词数量 | 1 |
            | 复习词数量 | 0 |
            | 来源 | chatgpt-share-test |

            ### 新学单词

            | 单词 | 核心含义 |
            |---|---|
            | [adapt](../../../entries/a/adapt.md) | v.：适应 |

            ### 代表性训练句

            1. **原句**：People must adapt to changing conditions.<br>
               **译文**：人们必须适应不断变化的环境。
            """,
        )
        self._write(
            "metadata/extraction-state.yml",
            """
            version: 2

            processed_records:

            last_processed_record: null
            """,
        )
        self._write(
            "metadata/source-cursors.yml",
            """
            version: 2

            sources:
            """,
        )

    def _finalize(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(FINALIZE_SCRIPT),
                "--record-id",
                RECORD_ID,
                "--date",
                "2026-01-02",
                "--source",
                SOURCE,
                "--daily-file",
                DAILY_FILE,
                "--content-fingerprint",
                FINGERPRINT,
                "--source-start-message-id",
                "message-start",
                "--source-start-sha256",
                "a" * 64,
                "--source-end-message-id",
                "message-end",
                "--source-end-sha256",
                "b" * 64,
                "--source-message-count",
                "2",
                "--kind",
                "chatgpt-share",
                "--location",
                "https://chatgpt.com/share/test",
                "--root",
                str(self.root),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_finalizes_validates_and_replays_idempotently(self) -> None:
        first_result = self._finalize()
        self.assertEqual(
            first_result.returncode,
            0,
            msg=first_result.stdout + first_result.stderr,
        )
        self.assertIn(f"finalized {RECORD_ID}", first_result.stdout)

        state_path = self.root / "metadata" / "extraction-state.yml"
        cursor_path = self.root / "metadata" / "source-cursors.yml"
        state_after_first_run = state_path.read_text(encoding="utf-8")
        cursor_after_first_run = cursor_path.read_text(encoding="utf-8")
        self.assertIn(f"{RECORD_ID}:", state_after_first_run)
        self.assertIn(f"content_fingerprint: {FINGERPRINT}", state_after_first_run)
        self.assertIn(f"last_record_id: {RECORD_ID}", cursor_after_first_run)
        self.assertIn("last_message_id: message-end", cursor_after_first_run)

        validation_result = subprocess.run(
            [
                sys.executable,
                str(VALIDATE_SCRIPT),
                "--root",
                str(self.root),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            validation_result.returncode,
            0,
            msg=validation_result.stdout + validation_result.stderr,
        )
        self.assertIn(
            "OK: 1 entries, 1 training record(s)",
            validation_result.stdout,
        )

        second_result = self._finalize()
        self.assertEqual(
            second_result.returncode,
            0,
            msg=second_result.stdout + second_result.stderr,
        )
        self.assertEqual(
            state_after_first_run,
            state_path.read_text(encoding="utf-8"),
        )
        self.assertEqual(
            cursor_after_first_run,
            cursor_path.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
