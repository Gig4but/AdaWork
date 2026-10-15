"""Employer cards (PLAN §4.5): who the employer is, official checks, reviews, web mentions and
an AI summary, kept up to date in the background and refreshed when a card is opened.

The background pass (after each search and every hour):
1. links jobs to employers: the IČO in the ad, else the name — learned from ads that carry
   both, the curated brand table, the agency register, or the resolver (ARES) below;
2. resolves new names through ARES (jobs matching the profile first), the AI choosing among
   close candidates when a model is ready (linking.py);
3. checks every employer with an IČO in ARES/RES (100 per call), ISIR only when ARES flags an
   insolvency, the VAT and agency lists locally; again after 30 days (checks.py);
4. for employers of jobs matching the profile (commute limit + kinds of work): Atmoskop
   reviews, Google ratings (with a key), web mentions, the AI summary — within small daily
   limits, again after 30 days (reviews.py).
"""

import logging
import threading
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

import httpx
from sqlalchemy.orm import Session, sessionmaker

from adawork.ai.manager import Busy, ModelManager
from adawork.config import EmployerSettings
from adawork.core import utcnow
from adawork.db.models import Employer
from adawork.employers import google, official
from adawork.employers.checks import OfficialChecks
from adawork.employers.common import DUE, Context, EmployerProgress
from adawork.employers.linking import NameLinker, load_brands
from adawork.employers.names import short_name
from adawork.employers.reviews import Reviews
from adawork.net.http import make_client
from adawork.net.polite import PoliteFetcher
from adawork.system.resources import Priority
from adawork.worker import Worker

log = logging.getLogger(__name__)

WAKE_EVERY = 3600.0
RESOLVE_PER_PASS = 300  # names (≤ 4 ARES calls each)
CHECK_PER_PASS = 2000  # employers (2 ARES calls per 100)
SUMMARIES_PER_PASS = 20
CARD_RETRY = timedelta(hours=1)  # an opened card whose refresh did not get it all asks again


def card_links(e: Employer, where: str = "") -> dict[str, str]:
    """Where to read more about the employer: review sites, maps, the official registers."""
    name = short_name(e.name)
    q = quote_plus(f"{name} {where}".strip())
    links = {
        "atmoskop": e.links.get("atmoskop"),
        "indeed": e.links.get("indeed")
        or f"https://cz.indeed.com/companies/search?q={quote_plus(name)}",
        "google": e.links.get("google") or f"https://www.google.com/maps/search/?api=1&query={q}",
        "mapy": f"https://mapy.com/cs/zakladni?q={q}",
        "facebook": e.links.get("facebook"),
    }
    if e.ico:
        links["ares"] = f"https://ares.gov.cz/ekonomicke-subjekty?ico={e.ico}"
        links["justice"] = f"https://or.justice.cz/ias/ui/rejstrik-$firma?ico={e.ico}"
    links["isir"] = next((p["url"] for p in e.insolvency or [] if p.get("url")), None)
    return {k: v for k, v in links.items() if v}


class EmployerService(Worker):
    """The background pass and the cards; the work itself is done by its parts: `linking`,
    `checks` and `reviews`, which share `ctx`."""

    name = "employers"

    def __init__(
        self,
        sessions: sessionmaker[Session],
        lists_dir: Path,
        settings: Callable[[], EmployerSettings] = EmployerSettings,
        *,
        allowed: Callable[[], bool] = lambda: True,
        client_factory: Callable[[], httpx.Client] = make_client,
        manager: ModelManager | None = None,
        google_key: Callable[[], str | None] = lambda: None,
        profile_jobs: Callable[[Session], set[int]] = lambda s: set(),
        ddgs_text: Callable[..., list[dict]] | None = None,
        fetcher_factory: Callable[[httpx.Client], PoliteFetcher] = PoliteFetcher,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        super().__init__()
        self.allowed = allowed
        self.ctx = Context(
            sessions, settings, client_factory, manager, profile_jobs, clock, lambda: self.stopping
        )
        lists = official.Lists(lists_dir)
        self.linking = NameLinker(self.ctx, lists, load_brands())
        self.checks = OfficialChecks(self.ctx, lists)
        self.reviews = Reviews(self.ctx, lists_dir, google_key, ddgs_text, fetcher_factory)
        self.refreshing: dict[int, str] = {}  # employer id -> step, while a card refreshes
        self._refreshed: dict[int, datetime] = {}  # employer id -> its card's last refresh
        self._lock = threading.Lock()  # of the two above

    @property
    def progress(self) -> EmployerProgress:
        return self.ctx.progress

    # --- the background pass ----------------------------------------------------------------

    def work(self) -> float:
        more = False
        try:
            more = self.run_pass()
        except Exception as exc:
            log.exception("employer pass failed")
            self.progress.phase, self.progress.message = "failed", str(exc)[:300]
        finally:
            self.progress.running = False
        return 5 if more else WAKE_EVERY

    def run_pass(self) -> bool:
        """One round of background work; True when more is waiting (a backlog)."""
        if not self.allowed():
            return False
        p = self.ctx.progress = EmployerProgress(running=True, phase="link")
        self.linking.link()
        self.checks.apply_lists()
        if not self.ctx.settings().auto_check:  # the rest goes online: only for opened cards
            p.phase = "done"
            return False
        p.phase = "resolve"
        resolved = self.linking.resolve_names(RESOLVE_PER_PASS)
        if resolved:
            self.linking.link()
        self.linking.ask_ai(profile_only=True)
        p.phase = "check"
        checked = self.checks.check_due(CHECK_PER_PASS)
        if self.linking.recheck_learned(RESOLVE_PER_PASS):
            self.linking.link()
        employers = self.ctx.profile_employers()
        p.phase = "reviews"
        self.reviews.fetch_due(employers)
        p.phase = "summaries"
        self.reviews.write_summaries(employers, SUMMARIES_PER_PASS, Priority.AI)
        p.phase = "done"
        return not self.stopping and (resolved >= RESOLVE_PER_PASS or checked >= CHECK_PER_PASS)

    # --- what the cards ask -----------------------------------------------------------------

    def correct(self, employer_id: int, ico: str) -> int | None:
        target = self.linking.correct(self.current_id(employer_id), ico)
        if target is not None:  # the card of another company: its refresh may run now
            with self._lock:
                self._refreshed.pop(target, None)
        return target

    def current_id(self, eid: int) -> int:
        """The employer a card shows: the one this one was merged into, once it is gone."""
        seen = set()
        while eid in self.linking.merged_into and eid not in seen and self._employer(eid) is None:
            seen.add(eid)
            eid = self.linking.merged_into[eid]
        return eid

    def google_quota(self) -> google.Quota:
        return self.reviews.google_quota()

    def job_municipality(self, s: Session, eid: int) -> str:
        return self.reviews.job_municipality(s, eid)

    def refresh(self, eid: int, force: bool = False) -> bool:
        """What is missing or old on the card, fetched now in the background (or everything,
        with `force`: the "Aktualizovat" button). False when a refresh already runs, or ran
        within `CARD_RETRY`: a source that keeps failing is not asked again on every look."""
        now = self.ctx.clock()
        with self._lock:
            last = self._refreshed.get(eid)
            if eid in self.refreshing or (not force and last and now - last < CARD_RETRY):
                return False
            self.refreshing[eid], self._refreshed[eid] = "check", now
        threading.Thread(
            target=self._refresh, args=(eid, force), name=f"employer-{eid}", daemon=True
        ).start()
        return True

    def needs_refresh(self, e: Employer) -> bool:
        """Something on the card is missing or 30 days old (sources switched off do not
        count, or the card would ask again forever)."""
        cfg = self.ctx.settings()
        old = [e.checked_at] if e.ico else []
        if e.ico and (cfg.atmoskop or (cfg.google and self.reviews.google_key())):
            old.append(e.reviews_at)
        if cfg.mentions:
            old.append(e.mentions_at)
        now = self.ctx.clock()
        return any(t is None or t < now - DUE for t in old)

    def _refresh(self, card: int, force: bool) -> None:
        """`card`: the id the card was opened with (its step is shown under it)."""
        now = self.ctx.clock()

        def old(t) -> bool:
            return force or t is None or t < now - DUE

        try:
            e = self._employer(self.current_id(card))
            if e is not None and e.match == "uncertain":
                self.refreshing[card] = "resolve"
                self.linking.ask_ai(employer_id=e.id)
                e = self._employer(self.current_id(card))  # the model may have picked one
            if e is None:
                return
            if e.ico and old(e.checked_at):
                self.checks.check_due(1, ids=[e.id], force=True)
            with self.ctx.client_factory() as client:
                fetcher = self.reviews.polite(client)
                if e.ico and old(e.reviews_at):
                    self.refreshing[card] = "reviews"
                    self.reviews.fetch_reviews(e.id, client, fetcher)
                if old(e.mentions_at):
                    self.refreshing[card] = "mentions"
                    self.reviews.fetch_mentions(e.id, fetcher)
            self.refreshing[card] = "summary"
            with suppress(Busy):
                self.reviews.write_summary(e.id, Priority.USER)
        except Exception:
            log.exception("employer %s refresh failed", card)
        finally:
            self.refreshing.pop(card, None)

    def _employer(self, eid: int) -> Employer | None:
        with self.ctx.sessions() as s:
            return s.get(Employer, eid)
