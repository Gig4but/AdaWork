"""Build the AI golden set of text ads (PLAN §6.7) from a crawled database.

Labels come from Úřad práce (MPSV) structured data, which is independent of the ad text:
- portal ads that were merged with an MPSV ad: the portal's page text + MPSV's labels;
- MPSV ads with a long description: title, employer, workplace and description + labels.
The salary label is the salary shown in the text (the portal's own figure for portal ads).
Ads are spread over the 9 ISCO major groups. E-mails and phone numbers are removed.

Usage: uv run python tools/make_golden.py <adawork.db> [out.jsonl]
"""

import json
import random
import re
import sys
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from adawork.db.models import Job, JobSource
from adawork.geo.places import PlaceResolver
from adawork.net.http import make_client
from adawork.net.polite import PoliteFetcher
from adawork.scraper.crawl import Lead
from adawork.scraper.sources import jenprace, prace_cz

OUT = Path(__file__).parents[1] / "tests" / "fixtures" / "eval" / "text_ads.jsonl"
PORTALS = {"prace_cz": (prace_cz.SOURCE, 25), "jenprace": (jenprace.SOURCE, 15)}
MPSV_ONLY = 30
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
PHONE = re.compile(r"(\+?420[\s/]?)?\b\d{3}[\s/-]?\d{3}[\s/-]?\d{3}\b")


def anonymize(text: str) -> str:
    return PHONE.sub("[telefon]", EMAIL.sub("[e-mail]", text))


def labels(job: Job, shown) -> dict:
    """`shown`: the ad whose salary appears in the text."""
    loc = job.location
    return {
        "isco": job.isco_code,
        "kraj": loc.kraj if loc else None,
        "municipality": loc.municipality if loc else None,
        "contract_types": sorted(job.contract_types),
        "salary_min": shown.salary_min,
        "salary_max": shown.salary_max,
        "salary_unit": shown.salary_unit if shown.salary_min or shown.salary_max else None,
        "shifts": sorted(job.shifts),
        "education": job.education,
    }


def page_text(
    title: str, employer: str | None, place: str | None, salary: str | None, description: str
) -> str:
    head = [title, employer or "", place or "", salary or ""]
    return anonymize("\n".join(x for x in head if x) + "\n\n" + description.strip())


def salary_line(job) -> str | None:
    if job.salary_min is None and job.salary_max is None:
        return None
    unit = "Kč/hod." if job.salary_unit == "hour" else "Kč/měsíc"
    values = " – ".join(f"{v:,}".replace(",", " ") for v in (job.salary_min, job.salary_max) if v)
    return f"{values} {unit}"


def spread(jobs: list[Job], n: int, rng: random.Random) -> list[Job]:
    """Round-robin over ISCO major groups so warehouse jobs do not dominate."""
    groups: dict[str, list[Job]] = {}
    for job in jobs:
        groups.setdefault(job.isco_code[0], []).append(job)
    for g in groups.values():
        rng.shuffle(g)
    picked: list[Job] = []
    while len(picked) < n and any(groups.values()):
        for key in sorted(groups):
            if groups[key] and len(picked) < n:
                picked.append(groups[key].pop())
    return picked


def main(db: str, out: str | None = None) -> int:
    rng = random.Random(5)
    out_path = Path(out) if out else OUT
    out_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db}")
    rows: list[dict] = []
    with Session(engine) as session, make_client() as client:
        places = PlaceResolver(session)
        fetcher = PoliteFetcher(client)
        for source_id, (source, n) in PORTALS.items():
            stmt = (
                select(Job, JobSource)
                .join(JobSource, JobSource.job_id == Job.id)
                .where(
                    Job.status == "active",
                    JobSource.source_id == source_id,
                    Job.isco_code.is_not(None),
                )
                .where(Job.sources.any(JobSource.source_id == "mpsv"))
            )
            pairs = {job.id: (job, src) for job, src in session.execute(stmt)}
            candidates = spread([job for job, _ in pairs.values()], n * 2, rng)
            got = 0
            for job in candidates:
                if got >= n:
                    break
                src = pairs[job.id][1]
                if not src.url or "jobs.cz" in src.url:
                    continue
                resp = fetcher.get(src.url)
                parsed = resp and source.parse_detail(
                    resp.text, Lead(src.external_id, src.url, src.url), places
                )
                if not parsed or len(parsed.description or "") < 200:
                    continue
                rows.append(
                    {
                        "id": f"{source_id}:{src.external_id}",
                        "text": page_text(
                            parsed.title,
                            parsed.employer_name,
                            parsed.place_text,
                            salary_line(parsed),
                            parsed.description,
                        ),
                        "labels": labels(job, parsed),
                    }
                )
                got += 1
                print(source_id, got, parsed.title, flush=True)

        stmt = select(Job).where(
            Job.status == "active",
            Job.isco_code.is_not(None),
            Job.location_id.is_not(None),
            ~Job.sources.any(JobSource.source_id != "mpsv"),
        )
        long_ads = [j for j in session.scalars(stmt) if len(j.description) >= 600]
        for job in spread(long_ads, MPSV_ONLY, rng):
            place = job.location.address_text or job.place_text or job.location.municipality
            rows.append(
                {
                    "id": f"mpsv:{job.sources[0].external_id}",
                    "text": page_text(
                        job.title, job.employer_name, place, salary_line(job), job.description
                    ),
                    "labels": labels(job, job),
                }
            )
    with out_path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"{len(rows)} ads -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
