"""Unit tests for the atomic-Excel-write helper duplicated in tmone_report.py
and login_export.py (write_utilization_workbook / write_login_workbook).

Before this fix, both writers did `pd.ExcelWriter(path, ...)` directly on the
final production filename (e.g. output/Tmone license-Utilaztion_2026_07.xlsx),
which pandas/openpyxl truncate to 0 bytes for the whole duration of the write.
A kill mid-write (server reboot, an operator killing a stuck run) left a
corrupt file at the exact name an operator would download for billing.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import login_export
import tmone_report


@pytest.mark.parametrize("module", [tmone_report, login_export])
class TestAtomicExcelPath:
    def test_success_replaces_final_path_and_leaves_no_tmp(self, tmp_path, module):
        target = tmp_path / "report.xlsx"
        with module._atomic_excel_path(target) as tmp_write_path:
            tmp_write_path.write_bytes(b"fake xlsx bytes")
        assert target.read_bytes() == b"fake xlsx bytes"
        assert list(tmp_path.glob("*.tmp")) == []

    def test_failure_leaves_final_path_untouched_and_cleans_up_tmp(self, tmp_path, module):
        target = tmp_path / "report.xlsx"
        target.write_bytes(b"previous good report")

        with pytest.raises(RuntimeError):
            with module._atomic_excel_path(target) as tmp_write_path:
                tmp_write_path.write_bytes(b"partial garbage")
                raise RuntimeError("simulated crash mid-write (e.g. killed process)")

        # The production file must still be the last good version, never
        # truncated/partial — this is the exact bug being fixed.
        assert target.read_bytes() == b"previous good report"
        assert list(tmp_path.glob("*.tmp")) == []

    def test_creates_parent_directory(self, tmp_path, module):
        target = tmp_path / "nested" / "output" / "report.xlsx"
        with module._atomic_excel_path(target) as tmp_write_path:
            tmp_write_path.write_bytes(b"x")
        assert target.exists()
