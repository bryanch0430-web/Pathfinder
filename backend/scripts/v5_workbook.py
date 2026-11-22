"""Small dependency-free XLSX / SpreadsheetML writer for the study workbook.

openpyxl is unavailable in this environment, so the workbook is written as OOXML
directly. Values, multiple sheets, frozen header rows, filters and widths are
supported. The .xls file is SpreadsheetML 2003, which Excel opens for compatibility.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _text(value: Any) -> str:
    text = "" if value is None else str(value)
    return _ILLEGAL.sub("", text)


def _col(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _cell(value: Any, row: int, col: int, header: bool = False) -> str:
    ref = f"{_col(col)}{row}"
    style = ' s="1"' if header else ' s="2"'
    if isinstance(value, bool):
        return f'<c r="{ref}"{style} t="b"><v>{int(value)}</v></c>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"{style}><v>{value}</v></c>'
    return f'<c r="{ref}"{style} t="inlineStr"><is><t xml:space="preserve">{escape(_text(value))}</t></is></c>'


def _sheet_xml(rows: list[list[Any]], widths: list[int] | None) -> str:
    cols = ""
    if widths:
        cols = "<cols>" + "".join(
            f'<col min="{i+1}" max="{i+1}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths)
        ) + "</cols>"
    body = []
    for r_index, row in enumerate(rows, start=1):
        cells = "".join(_cell(value, r_index, c_index, header=r_index == 1) for c_index, value in enumerate(row))
        body.append(f'<row r="{r_index}">{cells}</row>')
    last = f"A1:{_col(max((len(row) for row in rows), default=1) - 1)}{max(len(rows), 1)}"
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        f"{cols}"
        "<sheetData>" + "".join(body) + "</sheetData>"
        f'<autoFilter ref="{last}"/>'
        "</worksheet>"
    )


def write_xlsx(path: Path, sheets: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            + "".join(
                f'<Override PartName="/xl/worksheets/sheet{i+1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                for i in range(len(sheets))
            )
            + '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            "</Types>",
        )
        z.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        sheet_entries = "".join(
            f'<sheet name="{escape(sheet["title"][:31])}" sheetId="{i+1}" r:id="rId{i+1}"/>'
            for i, sheet in enumerate(sheets)
        )
        z.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{sheet_entries}</sheets></workbook>",
        )
        rels = "".join(
            f'<Relationship Id="rId{i+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i+1}.xml"/>'
            for i in range(len(sheets))
        )
        rels += (
            f'<Relationship Id="rId{len(sheets)+1}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{rels}</Relationships>",
        )
        z.writestr(
            "xl/styles.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="3"><font><sz val="10"/><name val="Calibri"/></font>'
            '<font><b/><sz val="10"/><name val="Calibri"/></font>'
            '<font><sz val="10"/><name val="Calibri"/></font></fonts>'
            '<fills count="3"><fill><patternFill patternType="none"/></fill>'
            '<fill><patternFill patternType="gray125"/></fill>'
            '<fill><patternFill patternType="solid"><fgColor rgb="FFE8F3F5"/><bgColor indexed="64"/></patternFill></fill>'
            '</fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
            '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyAlignment="1">'
            '<alignment wrapText="1" vertical="top"/></xf></cellXfs></styleSheet>',
        )
        for index, sheet in enumerate(sheets, start=1):
            z.writestr(
                f"xl/worksheets/sheet{index}.xml",
                _sheet_xml(sheet["rows"], sheet.get("widths")),
            )


def write_xls(path: Path, sheets: list[dict[str, Any]]) -> None:
    """SpreadsheetML 2003 with an .xls extension, readable by Excel for compatibility."""
    path.parent.mkdir(parents=True, exist_ok=True)
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<?mso-application progid="Excel.Sheet"?>',
        '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" '
        'xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">',
        '<Styles><Style ss:ID="header"><Font ss:Bold="1"/><Interior ss:Color="#E8F3F5" ss:Pattern="Solid"/></Style></Styles>',
    ]
    for sheet in sheets:
        parts.append(f'<Worksheet ss:Name="{escape(sheet["title"][:31])}"><Table>')
        for r_index, row in enumerate(sheet["rows"]):
            parts.append("<Row>")
            for value in row:
                style = ' ss:StyleID="header"' if r_index == 0 else ""
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    parts.append(f'<Cell{style}><Data ss:Type="Number">{value}</Data></Cell>')
                else:
                    parts.append(f'<Cell{style}><Data ss:Type="String">{escape(_text(value))}</Data></Cell>')
            parts.append("</Row>")
        parts.append("</Table></Worksheet>")
    parts.append("</Workbook>")
    path.write_text("".join(parts), encoding="utf-8")
