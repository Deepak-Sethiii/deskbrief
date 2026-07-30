"""OPTIONAL bootstrapper for templates/deskbrief_template.xlsm.

The supported path is to build the template by hand -- run
`python -m src.cli template-help` for the exact steps. This script just does the
same thing through Excel COM automation so you can skip the clicking.

It needs Excel installed and pywin32. It creates the four sheets, the header
rows and the formatting, then TRIES to import vba/Refresh.bas and attach the
button. That last part needs "Trust access to the VBA project object model",
which is off by default and cannot be switched on programmatically -- if it is
off the script says so and you finish steps 6-7 by hand.

    python tools/build_template.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.report.excel import (  # noqa: E402
    CHART_ANCHOR,
    HEADLINE_HEADERS,
    HEADLINES_FIRST_DATA_ROW,
    REQUIRED_SHEETS,
    SUMMARY_FIRST_DATA_ROW,
    SUMMARY_HEADERS,
    TEMPLATE_PATH,
)

BAS_PATH = PROJECT_ROOT / "vba" / "Refresh.bas"

XL_OPEN_XML_WORKBOOK_MACRO_ENABLED = 52
XL_EDGE_BOTTOM = 9
XL_CENTER = -4108
HEADER_FILL_RGB = 0x5B3A1F  # BGR for a dark navy (#1F3A5B)


def main() -> int:
    try:
        import win32com.client as win32
    except ImportError:
        print("pywin32 is not installed.  pip install pywin32==308")
        return 2

    if not BAS_PATH.exists():
        print(f"missing {BAS_PATH}")
        return 2

    TEMPLATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if TEMPLATE_PATH.exists():
        print(f"{TEMPLATE_PATH} already exists -- refusing to overwrite it.")
        print("Delete it first if you really want to rebuild.")
        return 1

    excel = win32.gencache.EnsureDispatch("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    workbook = excel.Workbooks.Add()

    try:
        # --- sheets -------------------------------------------------------
        while workbook.Sheets.Count > 1:
            workbook.Sheets(workbook.Sheets.Count).Delete()
        workbook.Sheets(1).Name = REQUIRED_SHEETS[0]
        for name in REQUIRED_SHEETS[1:]:
            workbook.Sheets.Add(After=workbook.Sheets(workbook.Sheets.Count)).Name = name

        _build_summary(workbook.Sheets("Summary"), excel)
        _build_headlines(workbook.Sheets("Headlines"))
        _build_charts(workbook.Sheets("Charts"))
        _build_commentary(workbook.Sheets("Commentary"))

        workbook.Sheets("Summary").Activate()
        workbook.SaveAs(str(TEMPLATE_PATH), FileFormat=XL_OPEN_XML_WORKBOOK_MACRO_ENABLED)
        print(f"created {TEMPLATE_PATH}")

        # --- VBA import (needs Trust access to the VBA project object model)
        imported = False
        try:
            workbook.VBProject.VBComponents.Import(str(BAS_PATH))
            imported = True
            print("imported vba/Refresh.bas")
        except Exception as exc:  # noqa: BLE001
            print("\nCOULD NOT IMPORT THE MACRO AUTOMATICALLY.")
            print(f"  reason: {exc}")
            print("  This is expected: Excel blocks programmatic VBA access by default.")
            print("  Turn it on at File -> Options -> Trust Center -> Trust Center")
            print("  Settings -> Macro Settings -> tick 'Trust access to the VBA")
            print("  project object model', then delete the template and re-run.")
            print("  Or just do steps 6-7 of `python -m src.cli template-help` by hand.")

        if imported:
            _add_button(workbook.Sheets("Summary"))
            print("added the Refresh DeskBrief button")

        workbook.Save()
    finally:
        workbook.Close(SaveChanges=False)
        excel.Quit()

    print("\nNow verify:  python -m src.cli verify-template")
    return 0


def _style_header_row(sheet, row: int, columns: int) -> None:
    header = sheet.Range(sheet.Cells(row, 1), sheet.Cells(row, columns))
    header.Font.Bold = True
    header.Font.Color = 0xFFFFFF
    header.Interior.Color = HEADER_FILL_RGB
    header.HorizontalAlignment = XL_CENTER
    header.Borders(XL_EDGE_BOTTOM).Weight = 3


def _build_summary(sheet, app) -> None:
    header_row = SUMMARY_FIRST_DATA_ROW - 1

    sheet.Range("A1").Font.Bold = True
    sheet.Range("A1").Font.Size = 14

    for index, (header, fmt) in enumerate(SUMMARY_HEADERS, start=1):
        sheet.Cells(header_row, index).Value = header
        # Number format lives on the template, never in the writer.
        sheet.Range(
            sheet.Cells(SUMMARY_FIRST_DATA_ROW, index),
            sheet.Cells(400, index),
        ).NumberFormat = fmt

    _style_header_row(sheet, header_row, len(SUMMARY_HEADERS))
    sheet.Columns(f"A:{_letter(len(SUMMARY_HEADERS))}").AutoFit()
    sheet.Columns("A").ColumnWidth = 16
    sheet.Columns("B").ColumnWidth = 22

    # Range.Select() fails on a hidden Excel instance, so freeze via the window.
    sheet.Activate()
    app.ActiveWindow.FreezePanes = False
    app.ActiveWindow.SplitRow = header_row
    app.ActiveWindow.SplitColumn = 0
    app.ActiveWindow.FreezePanes = True


def _build_headlines(sheet) -> None:
    header_row = HEADLINES_FIRST_DATA_ROW - 1
    for index, (header, fmt) in enumerate(HEADLINE_HEADERS, start=1):
        sheet.Cells(header_row, index).Value = header
        sheet.Range(
            sheet.Cells(HEADLINES_FIRST_DATA_ROW, index),
            sheet.Cells(400, index),
        ).NumberFormat = fmt
    _style_header_row(sheet, header_row, len(HEADLINE_HEADERS))
    sheet.Columns("A").ColumnWidth = 18
    sheet.Columns("B").ColumnWidth = 26
    sheet.Columns("C").ColumnWidth = 14
    sheet.Columns("D").ColumnWidth = 80
    sheet.Columns("E").ColumnWidth = 50


def _build_charts(sheet) -> None:
    sheet.Range("A1").Font.Bold = True
    sheet.Range("A1").Font.Size = 12


def _build_commentary(sheet) -> None:
    sheet.Range("A1").Font.Bold = True
    sheet.Range("A1").Font.Size = 14
    sheet.Columns("A").ColumnWidth = 16
    sheet.Columns("B").ColumnWidth = 100
    sheet.Columns("B").WrapText = True


def _add_button(sheet) -> None:
    shape = sheet.Shapes.AddShape(5, 640, 6, 150, 30)  # 5 = rounded rectangle
    shape.TextFrame.Characters().Text = "Refresh DeskBrief"
    shape.TextFrame.Characters().Font.Bold = True
    shape.OnAction = "RefreshDeskBrief"


def _letter(index: int) -> str:
    from openpyxl.utils import get_column_letter

    return get_column_letter(index)


if __name__ == "__main__":
    raise SystemExit(main())
