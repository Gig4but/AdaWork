"""Jobs -> employers (PLAN §4.5): the IČO in the ad, else the name, learned from ads that
carry both, the curated brand table, the agency register, or ARES; the AI picks among close
candidates, and the user can correct a match."""

import logging
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import httpx
import yaml
from sqlalchemy import bindparam, func, select, update
from sqlalchemy.orm import Session

from adawork.ai.client import BadAnswer, LlmError
from adawork.ai.manager import Busy
from adawork.db.models import (
    Employer,
    EmployerName,
    Job,
    Location,
    Municipality,
    table,
)
from adawork.employers import official, resolver
from adawork.employers.checks import fill
from adawork.employers.common import Context
from adawork.employers.names import (
    core_words,
    known_inside,
    legal_form_in,
    name_key,
    words,
)
from adawork.normalize.text import fold, valid_ico
from adawork.system.resources import Priority

log = logging.getLogger(__name__)

BRANDS_FILE = Path(__file__).with_name("brands.yaml")
LEARNED_SIMILAR = 0.6  # a name learned from ads this unlike the register name is checked

UNSETTLED = ("none", "uncertain")  # name sources an ad with an IČO may overrule
AI_SYSTEM = """Inzerát uvádí zaměstnavatele jménem. Vyber, která firma z obchodního rejstříku \
to je, podle jména, sídla, velikosti, oboru a textu inzerátu. Když žádná zjevně nesedí, vyber \
0. Vrať JSON."""


def _refused(exc: httpx.HTTPError) -> bool:
    """ARES answered, but not for this query (4xx): the next one may still work."""
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    return 400 <= exc.response.status_code < 500 and exc.response.status_code != 429


def load_brands(path: Path = BRANDS_FILE) -> dict[str, str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {name_key(str(k)): str(v) for k, v in data.items()}


class NameLinker:
    def __init__(self, ctx: Context, lists: official.Lists, brands: dict[str, str]) -> None:
        self.ctx = ctx
        self.lists = lists
        self.brands = brands
        self.merged_into: dict[int, int] = {}  # a deleted employer -> the one it joined

    def link(self) -> int:
        """Every active job with an employer name or IČO points to its employer; returns how
        many links changed. Names not known yet wait for `resolve_names`."""
        now = self.ctx.clock()
        with self.ctx.lock, self.ctx.sessions() as s:
            by_ico = dict(
                s.execute(select(Employer.ico, Employer.id).where(Employer.ico.is_not(None))).all()
            )
            names = dict(s.execute(select(EmployerName.key, EmployerName.employer_id)).all())
            # Names the register did not settle: an ad with an IČO settles them.
            unsettled = set(
                s.scalars(select(EmployerName.key).where(EmployerName.source.in_(UNSETTLED)))
            )
            rows = s.execute(
                select(Job.id, Job.employer_name, Job.employer_ico, Job.employer_id).where(
                    Job.status == "active"
                )
            ).all()
            learned: dict[str, Counter] = defaultdict(Counter)
            for _, name, ico, _ in rows:
                if ico and valid_ico(ico):
                    if ico not in by_ico:
                        by_ico[ico] = self._new(s, ico, name or ico, "ad", now).id
                    if name and (key := self._key(name)):
                        learned[key][ico] += 1
            for key, counts in learned.items():
                ico, n = counts.most_common(1)[0]
                if (key not in names or key in unsettled) and n >= 0.8 * sum(counts.values()):
                    s.merge(
                        EmployerName(key=key, employer_id=by_ico[ico], source="ad", resolved_at=now)
                    )
                    names[key] = by_ico[ico]
            lists = self.lists.fresh()
            for _, name, ico, _ in rows:
                key = self._key(name)
                if (ico and valid_ico(ico)) or not key or key in names:
                    continue
                found = (self.brands[key], "brand") if key in self.brands else None
                if found is None and key in lists.agency_names:
                    found = (lists.agency_names[key], "agency")
                if found:
                    ico, source = found
                    if ico not in by_ico:
                        by_ico[ico] = self._new(s, ico, name or key, source, now).id
                    s.add(
                        EmployerName(
                            key=key, employer_id=by_ico[ico], source=source, resolved_at=now
                        )
                    )
                    names[key] = by_ico[ico]
            changes = []
            for job_id, name, ico, current in rows:
                if ico and valid_ico(ico):
                    wanted = by_ico.get(ico)
                else:
                    wanted = names.get(self._key(name)) if name else None
                if wanted != current:
                    changes.append({"job": job_id, "employer": wanted})
            if changes:
                jobs = table(Job)
                s.execute(
                    update(jobs)
                    .where(jobs.c.id == bindparam("job"))
                    .values(employer_id=bindparam("employer")),
                    changes,
                )
            count = (
                select(func.count(Job.id))
                .where(Job.employer_id == Employer.id, Job.status == "active")
                .scalar_subquery()
            )
            s.execute(update(Employer).values(jobs_count=count))
            s.commit()
        return len(changes)

    def _key(self, name: str | None) -> str:
        """The name's key (names.name_key). A registered name whose key is a brand's keeps
        its legal form: "TESLA, a.s." is not Tesla the carmaker, and goes to ARES."""
        key = name_key(name)
        if key in self.brands and name and legal_form_in(name):
            return " ".join(words(name))[:200]
        return key

    @staticmethod
    def _new(s: Session, ico: str | None, name: str, match: str, now: datetime) -> Employer:
        employer = Employer(ico=ico, name=name, match=match, updated_at=now)
        s.add(employer)
        s.flush()
        return employer

    def pending(self, s: Session) -> list[tuple[str, str, resolver.AdHints]]:
        """(key, name as most ads write it, context) of names not resolved yet, those of
        jobs matching the profile first, then by how many jobs use them."""
        known = set(s.scalars(select(EmployerName.key)))
        rows = s.execute(
            select(
                Job.id,
                Job.employer_name,
                Job.employer_ico,
                Job.categories,
                Location.municipality_code,
            )
            .outerjoin(Location, Location.id == Job.location_id)
            .where(Job.status == "active", Job.is_offer.is_(True), Job.employer_name.is_not(None))
        ).all()
        wanted = self.ctx.profile_jobs(s)
        groups: dict[str, dict] = {}
        for job_id, name, ico, categories, municipality in rows:
            key = self._key(name)
            if not name or (ico and valid_ico(ico)) or len(key) < 2 or key in known:
                continue
            g = groups.setdefault(
                key,
                {
                    "names": Counter(),
                    "where": Counter(),
                    "cats": Counter(),
                    "n": 0,
                    "wanted": False,
                },
            )
            g["names"][name.strip()] += 1
            if municipality:
                g["where"][int(municipality)] += 1
            g["cats"].update(categories or [])
            g["n"] += 1
            g["wanted"] = g["wanted"] or job_id in wanted
        order = sorted(groups.items(), key=lambda kv: (not kv[1]["wanted"], -kv[1]["n"], kv[0]))
        return [
            (
                key,
                g["names"].most_common(1)[0][0],
                resolver.AdHints(
                    g["where"].most_common(1)[0][0] if g["where"] else None,
                    tuple(c for c, _ in g["cats"].most_common(3)),
                ),
            )
            for key, g in order
        ]

    def resolve_names(self, limit: int) -> int:
        with self.ctx.sessions() as s:
            todo = self.pending(s)[:limit]
            if not todo:
                return 0
            known = self._known(s)
            places = frozenset(
                fold(n) for n in s.scalars(select(Municipality.name)) if " " not in n
            )
        p = self.ctx.progress
        p.done, p.total = 0, len(todo)
        done = 0
        with self.ctx.client_factory() as client:
            ares = official.Ares(client)
            for key, name, ctx in todo:
                if self.ctx.stopping():
                    break
                inside = known_inside(name, known, places)
                if inside is not None:
                    self._name(key, inside, "inside")
                    done += 1
                    p.done += 1
                    continue
                try:
                    found = resolver.resolve(ares, name, ctx, places)
                except httpx.HTTPError as exc:
                    if not _refused(exc):  # ARES unreachable: the rest waits
                        log.warning("ARES search failed, names wait: %s", exc)
                        break
                    log.info("ARES refused %r: %s", name, exc)  # this name only
                    found = resolver.Resolution(None, 0.0, [], "none")
                self._store(key, name, found)
                done += 1
                p.done += 1
        return done

    @staticmethod
    def _known(s: Session) -> dict[tuple[str, ...], int]:
        """Core words of the names of employers with an IČO -> employer (unambiguous only)."""
        known: dict[tuple[str, ...], int | None] = {}
        rows = s.execute(
            select(EmployerName.key, EmployerName.employer_id)
            .join(Employer, Employer.id == EmployerName.employer_id)
            .where(Employer.ico.is_not(None))
        )
        for key, eid in rows:
            words = tuple(core_words(key))
            if len(words) >= 2:
                known[words] = eid if known.get(words, eid) == eid else None
        return {k: v for k, v in known.items() if v is not None}

    def _name(self, key: str, employer_id: int, source: str) -> None:
        with self.ctx.lock, self.ctx.sessions() as s:
            if s.get(EmployerName, key) is None:
                s.add(
                    EmployerName(
                        key=key,
                        employer_id=employer_id,
                        source=source,
                        resolved_at=self.ctx.clock(),
                    )
                )
                s.commit()

    def _store(self, key: str, name: str, found: resolver.Resolution) -> None:
        now = self.ctx.clock()
        with self.ctx.lock, self.ctx.sessions() as s:
            if s.get(EmployerName, key) is not None:
                return
            if found.ico:
                employer = s.scalar(select(Employer).where(Employer.ico == found.ico))
                if employer is None:
                    employer = self._new(s, found.ico, name, "ares", now)
                    fill(employer, found.companies[found.ico])
                    employer.confidence = found.confidence
                source = "ares"
            else:
                employer = self._new(s, None, name, found.method, now)
                employer.confidence = found.confidence
                employer.candidates = [c.as_dict() for c in found.candidates]
                source = found.method
            s.add(EmployerName(key=key, employer_id=employer.id, source=source, resolved_at=now))
            s.commit()

    def ask_ai(self, profile_only: bool = True, employer_id: int | None = None) -> int:
        """The model picks among close candidates, with the text of one of the ads."""
        m = self.ctx.manager
        if m is None or not m.ready:
            return 0
        with self.ctx.sessions() as s:
            stmt = select(Employer.id).where(Employer.match == "uncertain", Employer.ico.is_(None))
            if employer_id is not None:
                stmt = stmt.where(Employer.id == employer_id)
            ids = list(s.scalars(stmt))
            if profile_only and employer_id is None and ids:
                wanted = self.ctx.profile_employers(s)
                ids = [i for i in ids if i in wanted]
        picked = 0
        for eid in ids:
            with self.ctx.sessions() as s:
                e = s.get(Employer, eid)
                if e is None or e.links.get("asked_ai") or not e.candidates:
                    continue
                job = s.scalar(
                    select(Job).where(Job.employer_id == eid, Job.status == "active").limit(1)
                )
                prompt = self._ai_prompt(e, job)
                candidates = list(e.candidates)
            schema = {
                "type": "object",
                "properties": {"choice": {"enum": list(range(len(candidates) + 1))}},
            }
            try:
                priority = Priority.USER if employer_id is not None else Priority.AI
                with m.session(priority=priority) as client:
                    reply = client.chat(
                        [
                            {"role": "system", "content": AI_SYSTEM},
                            {"role": "user", "content": prompt},
                        ],
                        schema=schema,
                        max_tokens=20,
                    )
            except Busy:
                break
            except (LlmError, BadAnswer) as exc:
                log.info("AI choice for employer %s failed: %s", eid, exc)
                continue
            choice = reply.data.get("choice") if isinstance(reply.data, dict) else 0
            with self.ctx.lock, self.ctx.sessions() as s:
                e = s.get(Employer, eid)
                if e is None:  # merged into another while the model answered
                    continue
                e.links = {**e.links, "asked_ai": True}
                if isinstance(choice, int) and 0 < choice <= len(candidates):
                    self._adopt(s, e, candidates[choice - 1]["ico"], "ai")
                    picked += 1
                s.commit()
        return picked

    @staticmethod
    def _ai_prompt(e: Employer, job: Job | None) -> str:
        lines = [f"Jméno v inzerátu: {e.name}"]
        if job is not None:
            text = re.sub(r"\s+", " ", f"{job.title}. {job.description or ''}")[:700]
            lines.append(f"Inzerát: {text}")
            if job.place_text:
                lines.append(f"Místo práce: {job.place_text}")
        lines.append("Firmy:")
        for i, c in enumerate(e.candidates, 1):
            band = official.BANDS.get(c.get("band") or "", "neuvedeno")
            lines.append(f"{i}. {c['name']} (sídlo {c.get('seat') or '?'}, zaměstnanců {band})")
        return "\n".join(lines)

    def _adopt(self, s: Session, stub: Employer, ico: str, match: str) -> Employer:
        """An unresolved name turns out to be the company `ico`: its jobs and names move."""
        target = s.scalar(select(Employer).where(Employer.ico == ico))
        if target is None:
            stub.ico, stub.match, stub.checked_at, stub.candidates = ico, match, None, []
            stub.updated_at = self.ctx.clock()
            return stub
        s.execute(
            update(table(Job)).where(Job.employer_id == stub.id).values(employer_id=target.id)
        )
        s.execute(
            update(EmployerName)
            .where(EmployerName.employer_id == stub.id)
            .values(employer_id=target.id, source=match)
        )
        s.delete(stub)
        self.merged_into[stub.id] = target.id
        return target

    def correct(self, employer_id: int, ico: str) -> int | None:
        """The user's fix: the ads' names that led here (not the ones with an IČO) belong to
        the company `ico`. Returns the employer they now point to."""
        if not valid_ico(ico):
            return None
        now = self.ctx.clock()
        with self.ctx.lock, self.ctx.sessions() as s:
            e = s.get(Employer, employer_id)
            if e is None:
                return None
            # The names that led here: of its ads without an IČO, and those resolved to it.
            ads = select(Job.employer_name).where(
                Job.employer_id == e.id, Job.employer_ico.is_(None), Job.employer_name.is_not(None)
            )
            keys = {self._key(n) for n in s.scalars(ads)} | set(
                s.scalars(
                    select(EmployerName.key).where(
                        EmployerName.employer_id == e.id, EmployerName.source != "ad"
                    )
                )
            )
            if e.ico is None:
                target = self._adopt(s, e, ico, "manual")
            else:
                target = s.scalar(select(Employer).where(Employer.ico == ico))
                target = target or self._new(s, ico, ico, "manual", now)
            for key in keys - {""}:
                row = s.get(EmployerName, key) or EmployerName(key=key)
                row.employer_id, row.source, row.resolved_at = target.id, "manual", now
                s.add(row)
            s.commit()
            target_id = target.id
        self.link()
        return target_id

    def recheck_learned(self, limit: int) -> int:
        """Names learned from ads whose IČO belongs to another company of a group: an ad of
        "IMOBA, a.s." with Agrofert's IČO teaches "imoba" -> Agrofert. When a learned name
        does not resemble the company's register name, ARES is asked once; a company that
        clearly has the name takes it over. Returns how many names moved."""
        with self.ctx.sessions() as s:
            rows = s.execute(
                select(EmployerName.key, Employer.name, Employer.ico)
                .join(Employer, Employer.id == EmployerName.employer_id)
                .where(EmployerName.source == "ad", Employer.checked_at.is_not(None))
            ).all()
        odd = [
            (k, ico)
            for k, name, ico in rows
            if k not in self.brands
            and len(k.replace(" ", "")) >= 4  # "j a": too short to search by
            and resolver.similarity(k, name) < LEARNED_SIMILAR
        ]
        moved = 0
        with self.ctx.client_factory() as client:
            ares = official.Ares(client)
            for key, ico in odd[:limit]:
                if self.ctx.stopping():
                    break
                try:
                    found = resolver.resolve(ares, key, resolver.AdHints())
                except httpx.HTTPError as exc:
                    if not _refused(exc):
                        log.warning("ARES search failed, names wait: %s", exc)
                        break
                    log.info("ARES refused %r: %s", key, exc)
                    found = resolver.Resolution(None)  # the ad's IČO stays
                with self.ctx.lock, self.ctx.sessions() as s:
                    row = s.get(EmployerName, key)
                    if row is None:
                        continue
                    row.source = "ad_ok"
                    company = found.companies[found.ico] if found.ico else None
                    # Only a company named just so ("Restaurace U Jezera" is not "U Jezera
                    # s.r.o." in another town: the ad's IČO is the better guess then).
                    if (
                        company
                        and company.name
                        and company.ico != ico
                        and resolver.similarity(key, company.name) >= resolver.SAME_NAME
                    ):
                        target = s.scalar(select(Employer).where(Employer.ico == company.ico))
                        if target is None:
                            target = self._new(s, company.ico, key, "ares", self.ctx.clock())
                            fill(target, company)
                        row.employer_id, row.source = target.id, "ares"
                        moved += 1
                    s.commit()
        return moved
