"""Workbook layout: charts must not overlap tables or each other, and must plot the right row."""
import re

import openpyxl
import pytest

import fakedb
import report_service
import verify_report
from scenario import build_world, write_config
from tmone_report import load_config


@pytest.fixture
def books(tmp_path, monkeypatch):
    fakedb.install(build_world(), monkeypatch)
    cfg = write_config(tmp_path)
    res = report_service.run_report("2026-09", config_path=cfg, db_config_path=tmp_path / "d.yaml", output_dir=tmp_path / "out")
    util = tmp_path / "out" / res["files"]["utilization"]
    return openpyxl.load_workbook(util), util, tmp_path / "out" / res["files"]["login"], load_config(cfg), res


def _anchor(chart):
    f = chart.anchor._from
    return f.col, f.row


def _series_row(chart):
    ref = chart.series[0].val.numRef.f            # e.g. 'Alpha'!$D$14:$AA$14
    return int(re.findall(r"\$(\d+)", ref)[0])


def test_tenant_chart_plots_the_agent_row_of_the_agent_peak_day(books):
    wb, *_ = books
    ws = wb["Alpha"]                              # agent peak 2 Sep, supervisor row exists on the SAME day
    assert len(ws._charts) == 1
    row = _series_row(ws._charts[0])
    assert ws.cell(row, 2).value == "Professional-Agent" and ws.cell(row, 1).value.date().isoformat() == "2026-09-02"
    assert max(ws.cell(row, c).value for c in range(4, 28)) == 5          # the real agent peak, not the supervisor's 1


def test_tenant_chart_sits_above_the_table_not_on_it(books):
    wb, *_ = books
    ws = wb["Alpha"]
    col, row = _anchor(ws._charts[0])
    assert (col, row) == (4, 0)                   # E1: top block, free area right of the peak table
    header = next(r for r in range(1, 30) if ws.cell(r, 1).value == "Date" and ws.cell(r, 3).value == "Max count")
    points = sum((ws.row_dimensions[r].height or 15) for r in range(1, header))
    chart_cm = ws._charts[0].anchor.ext.height / 360000                 # EMU -> cm (openpyxl does not read .height back)
    assert chart_cm <= points / 28.35 + 0.01                            # fits in the rows above the table
    assert ws.cell(header - 1, 4).value == 1 and ws.cell(header - 1, 27).value == 24      # short 1..24 axis labels


def test_every_tenant_sheet_with_data_has_exactly_one_chart(books):
    wb, *_ = books
    for name in ("Alpha", "Beta", "Gamma"):
        assert len(wb[name]._charts) == 1, name


def test_dashboard_charts_do_not_overlap_each_other_or_the_tables(books):
    wb, *_ = books
    ws = wb["Dashboard"]
    bar, line = ws._charts
    (bcol, brow), (lcol, lrow) = _anchor(bar), _anchor(line)
    assert bcol == lcol == 6 and brow < lrow
    assert (lrow - brow) * 0.53 >= bar.anchor.ext.height / 360000 - 0.01   # the second chart starts below the first
    # the daily table lives to the right of both charts (18 cm ~ 11 columns from G)
    hdr = next(c for c in range(1, 40) if ws.cell(8, c).value == "Total agents")
    assert hdr - 1 > bcol + 11


def test_verifier_still_reads_the_dashboard_daily_totals(books):
    wb, util, login, config, res = books
    findings = verify_report.verify_workbooks(util, login, config, month="2026-09")
    assert [f for f in findings if f["severity"] == "error"] == []


def test_dashboard_bar_chart_lists_every_tenant_with_a_peak(books):
    wb, *_ = books
    bar = wb["Dashboard"]._charts[0]
    assert bar.type == "bar" and bar.legend is None


# ---- presentation layer: visual only
def test_polish_adds_heat_maps_tab_colors_and_formats_without_changing_values(books):
    wb, util, login, config, res = books
    d = wb["Dashboard"]
    assert d.sheet_properties.tabColor.rgb.endswith("1F4E79") and d.sheet_view.showGridLines is False
    assert len(d.conditional_formatting) >= 2
    s = wb["Summary"]
    assert len(s.conditional_formatting) >= 3 and s.sheet_properties.tabColor is not None
    a = wb["Alpha"]
    assert len(a.conditional_formatting) == 1 and a.sheet_properties.tabColor.rgb.endswith("2E75B6")   # ARC-1
    assert wb["Beta"].sheet_properties.tabColor.rgb.endswith("548235")                                  # ARC-2
    assert a.cell(a.max_row, 1).number_format.startswith("dd-mmm")
    lwb = openpyxl.load_workbook(login)
    sup = lwb["Supervisor_Alpha"]
    assert sup.sheet_properties.tabColor.rgb.endswith("ED7D31") and sup.auto_filter.ref.startswith("A1:D")
    assert sup["B2"].number_format == "dd-mmm-yyyy hh:mm:ss"
    # numbers are untouched: the verifier (which re-reads every value) still passes
    assert [f for f in verify_report.verify_workbooks(util, login, config, month="2026-09") if f["severity"] == "error"] == []
