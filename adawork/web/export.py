"""The listed jobs as a CSV or Excel file (PLAN §7.2). Contacts are left out: they may be
personal data of the people who posted the ads (PLAN §12)."""

import csv
import io
import re
import zipfile
from collections.abc import Callable, Iterable, Iterator
from html import escape

from sqlalchemy.engine import Row

from adawork.db.models import Job, Location
from adawork.i18n import translate
from adawork.normalize.vocab import label
from adawork.scraper.sources import LABELS as SOURCE_LABELS

COLUMNS = (
    "title",
    "employer",
    "place",
    "kraj",
    "salary",
    "contract",
    "shifts",
    "education",
    "posted",
    "sources",
    "link",
)


# What `table` and the salary text read.
JOB_COLUMNS = (
    Job.id,
    Job.title,
    Job.employer_name,
    Job.place_text,
    Location.municipality,
    Location.kraj,
    Job.salary_min,
    Job.salary_max,
    Job.salary_unit,
    Job.salary_currency,
    Job.contract_types,
    Job.shifts,
    Job.education,
    Job.posted_at,
)


def table(
    jobs: Iterable[Row],
    sources: dict[int, list[Row]],
    lang: str,
    salary: Callable[[Row], str | None],
) -> Iterator[list[str]]:
    """The header, then one row per job (rows of JOB_COLUMNS, sources by job id)."""
    yield [translate(lang, f"export.{c}") for c in COLUMNS]
    for job in jobs:
        listed = sources.get(job.id, [])
        offered = [s for s in listed if s.status == "active"] or listed
        names = dict.fromkeys(SOURCE_LABELS.get(s.source_id) or s.source_id for s in offered)
        primary = next((s for s in offered if s.is_primary), offered[0] if offered else None)
        yield [
            job.title,
            job.employer_name or "",
            job.place_text or job.municipality or "",
            job.kraj or "",
            salary(job) or "",
            ", ".join(label("contract_types", c, lang) for c in job.contract_types),
            ", ".join(label("shifts", c, lang) for c in job.shifts),
            label("education", job.education, lang) if job.education else "",
            f"{job.posted_at:%Y-%m-%d}" if job.posted_at else "",
            ", ".join(names),
            (primary.url if primary else None) or "",
        ]


def to_csv(rows: Iterable[list[str]]) -> bytes:
    """Semicolons and a BOM, as Excel with Czech settings opens it. Text that a spreadsheet
    would run as a formula gets a leading apostrophe."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    for row in rows:
        writer.writerow(["'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v for v in row])
    return out.getvalue().encode("utf-8-sig")


def to_xlsx(rows: Iterable[list[str]]) -> bytes:
    """A one-sheet workbook. Every cell is inline text, so nothing in an ad runs as a
    formula."""
    lines = []
    for r, row in enumerate(rows, 1):
        cells = "".join(
            f'<c r="{_column(c)}{r}" t="inlineStr"><is><t{_KEEP}>{_xml(v)}</t></is></c>'
            for c, v in enumerate(row)
            if v
        )
        lines.append(f'<row r="{r}">{cells}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{_MAIN}"><sheetData>{"".join(lines)}</sheetData></worksheet>'
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in _XLSX_PARTS.items():
            z.writestr(name, text)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return out.getvalue()


def _column(index: int) -> str:
    """0 -> "A", 25 -> "Z", 26 -> "AA"."""
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def _xml(text: str) -> str:
    """Escaped, without the control characters XML does not allow."""
    return escape(_CONTROL.sub("", text), quote=False)


_KEEP = ' xml:space="preserve"'  # leading and trailing spaces stay
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
_XLSX_PARTS = {
    "[Content_Types].xml": (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    ),
    "_rels/.rels": (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{_PKG}">'
        f'<Relationship Id="rId1" Type="{_REL}/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    ),
    "xl/workbook.xml": (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="{_MAIN}" '
        f'xmlns:r="{_REL}"><sheets><sheet name="AdaWork" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    ),
    "xl/_rels/workbook.xml.rels": (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{_PKG}">'
        f'<Relationship Id="rId1" Type="{_REL}/worksheet" Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    ),
}
