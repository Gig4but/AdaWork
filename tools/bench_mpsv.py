"""Dev benchmark: import the live MPSV dump into a scratch database and time searches.

uv run python tools/bench_mpsv.py <work_dir>
"""

import sys
import time
from pathlib import Path

from sqlalchemy import func, select

from adawork.db.models import Job, Location
from adawork.db.session import make_engine, make_sessionmaker, migrate
from adawork.scraper.runner import ScrapeRunner
from adawork.scraper.sources import mpsv
from adawork.search.service import search_jobs

QUERIES = ["", "skladník", "ucetni", "řidič brno", "kuchař praha", "prodavač", "zdravotní sestra"]


def main() -> None:
    work = Path(sys.argv[1])
    work.mkdir(parents=True, exist_ok=True)
    engine = make_engine(work / "bench.db")
    migrate(engine)
    sessions = make_sessionmaker(engine)
    runner = ScrapeRunner(sessions, work / "cache", sources=[mpsv.SOURCE])

    for attempt in ("first import", "second run (unchanged)"):
        started = time.perf_counter()
        runner.start()
        runner.wait()
        p = runner.progress
        print(
            f"{attempt}: {p.phase} {p.message} in {time.perf_counter() - started:.1f} s, "
            f"processed={p.processed}, results={p.results}"
        )

    with sessions() as s:
        print("active jobs:", s.scalar(select(func.count(Job.id)).where(Job.status == "active")))
        print("locations:", s.scalar(select(func.count(Location.id))))
        for q in QUERIES:
            search_jobs(s, q)  # warm up
            r = search_jobs(s, q)
            print(f"  {q!r:22} total={r.total:6}  {r.took_ms:6.1f} ms  first={r.jobs[0].title!r}")
    print("db size MB:", round((work / "bench.db").stat().st_size / 1e6, 1))


if __name__ == "__main__":
    main()
