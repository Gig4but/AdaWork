"""What people say about an employer (PLAN §4.5): Atmoskop reviews, Google ratings (with the
user's key), web mentions, and the AI summary of them; for employers of jobs matching the
profile within small daily limits, and whenever a card is opened."""

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from adawork.ai.client import BadAnswer, LlmError
from adawork.ai.manager import Busy
from adawork.db.models import (
    Employer,
    EmployerMention,
    EmployerReview,
    EmployerSummary,
    Job,
    Location,
)
from adawork.employers import atmoskop, downloads, google, mentions, summary
from adawork.employers.common import DUE, Context
from adawork.employers.names import short_name
from adawork.net.polite import PoliteFetcher, SourceBlocked, Stopped
from adawork.system.resources import Priority

log = logging.getLogger(__name__)

REFUSED_PAUSE = timedelta(hours=6)  # a site answered 403/429: left alone this long
MIN_RATINGS = 5  # Atmoskop satisfaction counts for the filter from this many ratings


class Reviews:
    def __init__(
        self,
        ctx: Context,
        lists_dir: Path,
        google_key: Callable[[], str | None],
        ddgs_text: Callable[..., list[dict]] | None,
        fetcher_factory: Callable[[httpx.Client], PoliteFetcher],
    ) -> None:
        self.ctx = ctx
        self.atmoskop_index = atmoskop.SlugIndex(lists_dir / downloads.ATMOSKOP_FILE)
        self.google_key = google_key
        self._ddgs_text = ddgs_text
        self.fetcher_factory = fetcher_factory
        self._paused: dict[str, datetime] = {}  # review source -> refused us, left alone until
        self._fetcher: PoliteFetcher | None = None  # the turns per host (see `polite`)
        self._google_lock = threading.Lock()  # the month's count of billed details

    def fetch_due(self, employer_ids: set[int]) -> int:
        """Reviews and mentions of these employers, where 30 days old or missing, within the
        daily limits of the background."""
        if not employer_ids:
            return 0
        now = self.ctx.clock()
        with self.ctx.sessions() as s:
            rows = s.execute(
                select(Employer.id, Employer.reviews_at, Employer.mentions_at)
                .where(Employer.id.in_(list(employer_ids)))
                .order_by(Employer.jobs_count.desc())
            ).all()
        done = 0
        with self.ctx.client_factory() as client:
            fetcher = self.polite(client)
            for eid, reviews_at, mentions_at in rows:
                if self.ctx.stopping():
                    break
                if reviews_at is None or reviews_at < now - DUE:
                    done += self.fetch_reviews(eid, client, fetcher, background=True)
                if mentions_at is None or mentions_at < now - DUE:
                    done += self.fetch_mentions(eid, fetcher, background=True)
        return done

    def polite(self, client: httpx.Client) -> PoliteFetcher:
        """A fetcher over `client` sharing one set of per-host turns and robots.txt rules:
        the background pass and every opened card ask Atmoskop and web pages in turn."""
        with self.ctx.state_lock:
            if self._fetcher is None:
                self._fetcher = self.fetcher_factory(client)
        return self._fetcher.with_client(client)

    def fetch_reviews(
        self, eid: int, client: httpx.Client, fetcher: PoliteFetcher, background: bool = False
    ) -> bool:
        cfg = self.ctx.settings()
        with self.ctx.sessions() as s:
            e = s.get(Employer, eid)
            if e is None or e.ico is None:
                return False
            name, links = e.name, dict(e.links)
            where = self.job_municipality(s, eid)
        now = self.ctx.clock()
        got: dict[str, object] = {}
        asked = deferred = False  # deferred: today's limit is used up, or the site refused us
        if cfg.atmoskop:
            url = links.get("atmoskop") or self.atmoskop_index.fresh().find(name)
            if url and not self._may_ask("atmoskop", background):
                deferred = True
            elif url:
                asked = True
                try:
                    found = atmoskop.fetch(fetcher, url)
                    if found is None:  # gone, or robots.txt says no: the sitemap is asked again
                        url = None
                    else:
                        got["atmoskop"] = found
                except SourceBlocked as exc:  # 403/429: no more pages for a while
                    log.info("Atmoskop refused %s: %s", url, exc)
                    self._paused["atmoskop"] = now + REFUSED_PAUSE
                    asked, deferred = False, True
                except Stopped:
                    return False
                except Exception as exc:  # a timeout or a changed page: Google is still asked
                    log.info("Atmoskop %s failed: %s", url, exc)
                    asked, deferred = False, True
            if url:
                links["atmoskop"] = url
            else:
                links.pop("atmoskop", None)
        key = self.google_key()
        if cfg.google and key and not self._may_ask("google", background):
            deferred = True
        elif cfg.google and key:
            asked = True
            with self._google_lock:  # two cards at once must not both spend the last one
                quota = self.google_quota()
                try:
                    place_id = links.get("google_place") or google.find_place(
                        client, key, f"{short_name(name)} {where}".strip()
                    )
                    if place_id:
                        links["google_place"] = place_id
                        got["google"] = google.details(client, key, place_id, quota)
                except google.QuotaUsed:
                    pass
                except httpx.HTTPError as exc:
                    log.info("Google Places for %s failed: %s", name, exc)
                finally:
                    self.ctx.save_state({"google": quota.dump()})
        with self.ctx.lock, self.ctx.sessions() as s:
            e = s.get(Employer, eid)
            if e is None:
                return False
            for source, data in got.items():
                row = s.scalar(
                    select(EmployerReview).where(
                        EmployerReview.employer_id == eid, EmployerReview.source == source
                    )
                )
                row = row or EmployerReview(employer_id=eid, source=source)
                if isinstance(data, atmoskop.Reviews):
                    row.rating, row.scale, row.count = data.employer_pct, "percent", data.ratings
                    row.scores, row.texts, row.url = data.scores(), data.texts, data.url
                    e.satisfaction = data.employer_pct if data.ratings >= MIN_RATINGS else None
                elif isinstance(data, google.Place):
                    row.rating, row.scale, row.count = data.rating, "stars5", data.count
                    row.scores, row.texts, row.url = {"place": data.name}, data.reviews, data.url
                    links["google"] = data.url
                row.fetched_at = now
                s.add(row)
            e.links = links
            if asked or not deferred:  # nothing asked yet: the next pass tries again
                e.reviews_at = now
            s.commit()
        return bool(got)

    def _may_ask(self, kind: str, background: bool) -> bool:
        """Not paused after a refusal, and (in the background) within today's limit."""
        if self._paused.get(kind, datetime.min) > self.ctx.clock():
            return False
        return not background or self.ctx.spend(kind)

    def google_quota(self) -> google.Quota:
        return google.Quota.load(self.ctx.state().get("google"), self.ctx.clock())

    @staticmethod
    def job_municipality(s: Session, eid: int) -> str:
        """Where most of the employer's ads are: the branch customers rate."""
        row = s.execute(
            select(Location.municipality, func.count())
            .join(Job, Job.location_id == Location.id)
            .where(
                Job.employer_id == eid, Job.status == "active", Location.municipality.is_not(None)
            )
            .group_by(Location.municipality)
            .order_by(func.count().desc())
            .limit(1)
        ).first()
        return (row[0] if row else None) or ""

    def _ddgs(self) -> Callable[..., list[dict]]:
        if self._ddgs_text is None:
            from ddgs import DDGS

            logging.getLogger("primp").setLevel(logging.WARNING)  # a line per search engine
            return lambda *a, **kw: DDGS().text(*a, **kw)
        return self._ddgs_text

    def fetch_mentions(self, eid: int, fetcher: PoliteFetcher, background: bool = False) -> bool:
        if not self.ctx.settings().mentions:
            return False
        with self.ctx.sessions() as s:
            e = s.get(Employer, eid)
            if e is None:
                return False
            name = short_name(e.name)
        if background and not self.ctx.spend("mentions"):
            return False
        found = mentions.search(name, self._ddgs())
        if found.failed:  # the search engine is not answering: what is stored stays
            return False
        mentions.read_pages(found, name, fetcher)
        now = self.ctx.clock()
        with self.ctx.lock, self.ctx.sessions() as s:
            e = s.get(Employer, eid)
            if e is None:
                return False
            for old in s.scalars(
                select(EmployerMention).where(
                    EmployerMention.employer_id == eid, EmployerMention.source == "web"
                )
            ):
                s.delete(old)
            for m in found.mentions[:10]:
                s.add(
                    EmployerMention(
                        employer_id=eid,
                        source="web",
                        url=m.url,
                        title=m.title,
                        snippet=m.snippet,
                        passages=m.passages,
                        fetched_at=now,
                    )
                )
            links = dict(e.links)
            if found.indeed_url:
                links["indeed"] = found.indeed_url
            if found.facebook_url:
                links["facebook"] = found.facebook_url
            e.links, e.mentions_at = links, now
            s.commit()
        return True

    def items(self, s: Session, eid: int) -> list[summary.Item]:
        """The texts the summary is written from: what each Atmoskop review praises and
        criticizes (apart, so a complaint cannot become praise), then web passages."""
        out: list[summary.Item] = []
        review = s.scalar(
            select(EmployerReview).where(
                EmployerReview.employer_id == eid, EmployerReview.source == "atmoskop"
            )
        )
        if review is not None and review.url:
            url = review.url.rstrip("/") + "/hodnoceni"
            for kind in ("plus", "minus"):
                for t in review.texts[:10]:
                    if len(t.get(kind) or "") >= 3:
                        out.append(summary.Item("atmoskop", url, t[kind], kind))
        rows = s.scalars(
            select(EmployerMention)
            .where(EmployerMention.employer_id == eid)
            .order_by(EmployerMention.id)
        )
        for m in rows:
            for passage in [p for p in m.passages if not mentions.boilerplate(p)][:3]:
                out.append(summary.Item(m.source, m.url, passage))
        return out[: summary.MAX_ITEMS]

    def write_summaries(self, employer_ids: set[int], limit: int, priority: Priority) -> int:
        m = self.ctx.manager
        if m is None or not m.ready or not employer_ids:
            return 0
        written = tried = 0
        for eid in sorted(employer_ids):
            if tried >= limit or self.ctx.stopping():
                break
            try:
                done = self.write_summary(eid, priority)
            except Busy:
                break
            written += bool(done)
            tried += done is not None  # None: nothing to write (no texts, or not changed)
        return written

    def write_summary(self, eid: int, priority: Priority) -> bool | None:
        """True when written, False when the model failed; None: nothing to write."""
        m = self.ctx.manager
        if m is None or not m.ready:
            return None
        with self.ctx.sessions() as s:
            e = s.get(Employer, eid)
            items = self.items(s, eid) if e else []
            if e is None or not items:
                return None
            key = summary.inputs_key(items)
            old = s.get(EmployerSummary, eid)
            if old is not None and old.inputs == key:
                return None
            name = short_name(e.name)
        try:
            with m.session(priority=priority) as client:
                result = summary.summarize(client, name, items)
                model = client.model
        except (LlmError, BadAnswer) as exc:
            log.info("summary of employer %s failed: %s", eid, exc)
            return False
        if result.timing:
            m.record(result.timing)
        with self.ctx.lock, self.ctx.sessions() as s:
            if s.get(Employer, eid) is None:  # merged into another while the model wrote
                return False
            row = s.get(EmployerSummary, eid) or EmployerSummary(employer_id=eid)
            row.summary = result.as_dict()
            row.sources = [{"source": it.source, "url": it.url} for it in items]
            row.inputs, row.model = key, str(model)[:40]
            row.prompt_version, row.created_at = summary.PROMPT_VERSION, self.ctx.clock()
            s.add(row)
            s.commit()
        return True
