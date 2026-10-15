"""Official checks (PLAN §4.5): ARES/RES for every employer with an IČO, ISIR only when ARES
flags an insolvency, the VAT and agency lists locally; again after 30 days."""

import logging
from collections import Counter

import httpx
from sqlalchemy import or_, select

from adawork.db.models import Employer, Job
from adawork.employers import official
from adawork.employers.common import DUE, Context
from adawork.employers.names import looks_like_agency
from adawork.normalize.text import fold

log = logging.getLogger(__name__)

AGENCY_SHARE = 0.5  # of an employer's ads that speak of agency work
_ENDED_STATES = ("vyrizen", "pravomocn", "odskrtnut", "zrusen", "mylny")


def fill(e: Employer, c: official.Company) -> None:
    """The register's facts on the employer."""
    e.name = c.name or e.name
    e.dic, e.legal_form, e.founded, e.ended = c.dic, c.legal_form, c.founded, c.ended
    e.address, e.nace_main, e.employee_band = c.address, c.nace_main, c.band


class OfficialChecks:
    def __init__(self, ctx: Context, lists: official.Lists) -> None:
        self.ctx = ctx
        self.lists = lists

    def apply_lists(self) -> int:
        """The daily VAT and agency lists changed: every employer is looked up again."""
        lists = self.lists.fresh()
        if self.ctx.state().get("lists") == lists.stamp:
            return 0
        n = 0
        with self.ctx.lock, self.ctx.sessions() as s:
            for e in s.scalars(
                select(Employer).where(Employer.ico.is_not(None), Employer.checked_at.is_not(None))
            ):
                before = (e.vat_unreliable, e.agency, list(e.warnings))
                self._local_checks(e, lists)
                n += before != (e.vat_unreliable, e.agency, list(e.warnings))
            s.commit()
        self.ctx.save_state({"lists": lists.stamp})
        return n

    def check_due(self, limit: int, ids: list[int] | None = None, force: bool = False) -> int:
        """ARES/RES (and ISIR, the lists) for employers never checked or checked 30 days ago."""
        now = self.ctx.clock()
        with self.ctx.sessions() as s:
            stmt = select(Employer.id, Employer.ico).where(Employer.ico.is_not(None))
            if ids is not None:
                stmt = stmt.where(Employer.id.in_(ids))
            if not force:
                stmt = stmt.where(
                    or_(Employer.checked_at.is_(None), Employer.checked_at < now - DUE)
                )
            due = s.execute(stmt.order_by(Employer.jobs_count.desc())).all()
            wanted = self.ctx.profile_employers(s) if ids is None else set()
        # Stable: by jobs within each group.
        due = sorted(due, key=lambda r: r[0] not in wanted)[:limit]
        if not due:
            return 0
        p = self.ctx.progress if ids is None else None  # a card's check is not the pass's
        if p:
            p.done, p.total = 0, len(due)
        lists = self.lists.fresh()
        checked = 0
        with self.ctx.client_factory() as client:
            ares = official.Ares(client)
            for i in range(0, len(due), official.BATCH):
                if self.ctx.stopping():
                    break
                chunk = {ico: eid for eid, ico in due[i : i + official.BATCH] if ico}
                try:
                    companies = ares.companies(list(chunk))
                except httpx.HTTPError as exc:
                    log.warning("ARES check failed, employers wait: %s", exc)
                    break
                proceedings, unknown = {}, set()
                for ico, c in companies.items():
                    if c.insolvency_flag:
                        try:
                            proceedings[ico] = official.insolvency(client, ico)
                        except (httpx.HTTPError, ValueError) as exc:
                            log.info("ISIR %s failed: %s", ico, exc)
                            unknown.add(ico)
                agency_share = self._agency_share(list(chunk.values()))
                with self.ctx.lock, self.ctx.sessions() as s:
                    for ico, eid in chunk.items():
                        e = s.get(Employer, eid)
                        if e is None:
                            continue
                        c = companies.get(ico)
                        if c is not None:
                            fill(e, c)
                            if ico in unknown:  # the flag alone warns until ISIR answers
                                e.insolvency = e.insolvency or [{"state": "", "url": ""}]
                            else:
                                e.insolvency = proceedings.get(ico, [])
                        e.looks_agency = agency_share.get(eid, 0.0) >= AGENCY_SHARE
                        self._local_checks(e, lists, in_ares=c is not None)
                        e.updated_at = now
                        if ico not in unknown:  # else checked again in the next pass
                            e.checked_at = now
                        checked += 1
                    s.commit()
                if p:
                    p.done += len(chunk)
        return checked

    def _agency_share(self, employer_ids: list[int]) -> dict[int, float]:
        """Share of each employer's ads that speak of agency work (or of "our client")."""
        flagged: Counter = Counter()
        total: Counter = Counter()
        with self.ctx.sessions() as s:
            for eid, title, description in s.execute(
                select(Job.employer_id, Job.title, Job.description).where(
                    Job.employer_id.in_(employer_ids), Job.status == "active"
                )
            ):
                if total[eid] >= 30:
                    continue
                total[eid] += 1
                flagged[eid] += looks_like_agency(f"{title}\n{description or ''}")
        return {eid: flagged[eid] / n for eid, n in total.items()}

    @staticmethod
    def _local_checks(e: Employer, lists: official.Lists, in_ares: bool | None = None) -> None:
        """The warnings; `in_ares` None: as the last ARES check found."""
        e.vat_unreliable = lists.unreliable_since(e.dic)
        agency = lists.agencies.get(e.ico or "")
        e.agency = None
        if agency:
            until = agency.until.isoformat() if agency.until else None
            e.agency = {"temp": agency.temp_staffing, "until": until}
        missing = "not_found" in (e.warnings or []) if in_ares is None else not in_ares
        warnings = ["not_found"] if missing else []
        if e.ended:
            warnings.append("ended")
        if any(
            not fold(str(p.get("state", ""))).startswith(_ENDED_STATES) for p in e.insolvency or []
        ):
            warnings.append("insolvency")
        if e.vat_unreliable:
            warnings.append("vat")
        if e.looks_agency and e.agency is None:
            warnings.append("unlicensed")
        e.warnings = warnings
