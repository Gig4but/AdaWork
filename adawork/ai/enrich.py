"""Background enrichment after each scrape (PLAN §6.1): fills what the source left out.

1. Rules (no model, seconds): an occupation code from similar Úřad práce titles when the
   vote is clear (it gives the job its categories, so the category filter finds portal ads),
   and contract types named in the description ("HPP", "DPP", "na živnostenský list").
   The kinds of work in the profile are mapped to occupations the same way (`wanted`).
2. Pictures (the model with its vision part): the text of posters and photos of notices
   attached to ads with little text of their own is written out and added to the ad, which
   then goes through the rules and the AI like any other text (PLAN §6.8).
3. AI (the local model, minutes to hours): ads with a description that still lack fields.
   Jobs matching the user's profile go first; on slow PCs only those are processed.

Only empty fields are filled; what a source states is never overwritten. Filled fields are
recorded in `job.extra["origin"]` so the UI can mark them. Each job is processed once per
content version (`rules_hash`, `ai_hash`); answers are cached by input in `llm_cache`.
"""

import hashlib
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from adawork.ai import vision, wanted
from adawork.ai.catalog import DEFAULT_STYLE, PromptStyle
from adawork.ai.client import BadAnswer, LlmError, ServerDown
from adawork.ai.extract import (
    EDUCATION,
    FIELDS,
    PROMPT_VERSION,
    SHIFTS,
    WARNINGS,
    Extraction,
    extract,
    validate,
)
from adawork.ai.isco import IscoCodebook, IscoIndex, Lookup
from adawork.ai.manager import Busy, ModelManager, NoModel
from adawork.config import Profile
from adawork.core import ParsedLocation, utcnow
from adawork.db.models import Job, JobImage, JobSource, LlmCache, Location
from adawork.geo.places import PlaceResolver
from adawork.geo.scope import build_query
from adawork.normalize.text import contract_types, salary_in_text
from adawork.normalize.vocab import UNCATEGORIZED
from adawork.search import kinds

log = logging.getLogger(__name__)

# Shorter ads (listing cards) have nothing more to read: only the occupation is asked, from
# the title, when the rules found no clear one.
MIN_DESCRIPTION = 200
BATCH = 200
AI_CHUNK = 10  # jobs per hold of the AI slot, so user-facing work can step in
WAIT_SECONDS = 60
SLOW_SECONDS = 20.0  # above this per ad, only jobs matching the profile are processed
# Pictures are read for ads whose own text is shorter than this (a poster with a two-line
# post); a long ad with a photo of the workplace gains nothing from it.
IMAGE_TEXT_BELOW = 600
IMAGES_PER_RUN = 100
IMAGE_TEXT_HEADER = "Text z obrázku:"


@dataclass
class EnrichProgress:
    running: bool = False
    phase: str = ""  # rules | images | ai | waiting | done | failed
    done: int = 0
    total: int = 0
    classified: int = 0  # jobs that got an occupation from the rules
    wait_reason: str = ""  # i18n key while waiting
    message: str = ""


class Enricher:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        manager: ModelManager,
        codebook_path: Path,
        profile: Callable[[], Profile | None] = lambda: None,
        index_cache: Path | None = None,
        wait_seconds: float = WAIT_SECONDS,
        images_dir: Path | None = None,
    ) -> None:
        self.sessions = sessions
        self.manager = manager
        self.codebook_path = codebook_path
        self.profile = profile
        self.index_cache = index_cache
        self.wait_seconds = wait_seconds
        self.images_dir = images_dir
        self.progress = EnrichProgress()
        self._index: IscoIndex | None = None
        self._examples: list[tuple[str, str]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._again = False  # started again during a run: one more run after it
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        """Starts a run; during a run, one more follows it (the run took its jobs already)."""
        with self._lock:
            if self.progress.running:
                self._again = True
                return False
            self._stop.clear()
            self._again = False
            self.progress = EnrichProgress(running=True, phase="rules")
            self._thread = threading.Thread(target=self._run, name="enrich", daemon=True)
            self._thread.start()
            return True

    def stop(self) -> None:
        with self._lock:
            self._again = False
            self._stop.set()

    def wait(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _run(self) -> None:
        while True:
            self._run_once()
            with self._lock:
                if not self._again:
                    self.progress.running = False
                    return
                self._again = False
                self._stop.clear()  # a stop was meant for the run before (its jobs were cleared)
                self.progress = EnrichProgress(running=True, phase="rules")

    def _run_once(self) -> None:
        p = self.progress
        try:
            if self.manager.embedder.available:
                self.run_rules()
                self.resolve_kinds()
            if self.manager.ready and not self._stop.is_set():
                if self.run_images() and self.manager.embedder.available:
                    self.run_rules()  # the ads that gained text from their pictures
                self.run_ai()
                if not self.manager.tier_still_right():
                    self.manager.start_download()
            p.phase = "done"
        except Exception as exc:  # shown in the UI
            if self._stop.is_set():  # the app quits and stops the AI server mid-answer
                log.info("enrichment stopped: %s", exc)
                p.phase = "done"
                return
            log.exception("enrichment failed")
            p.phase, p.message = "failed", str(exc)[:300]

    # --- 1. rules -----------------------------------------------------------------------

    def index(self, session: Session) -> IscoIndex | None:
        path = self.codebook_path
        if not path.exists():
            return None
        codebook = IscoCodebook.load(path)
        examples = IscoIndex.examples(session, codebook)
        if self._index is None or examples != self._examples:
            index = IscoIndex(codebook, self.manager.embedder)
            index.build(examples, cache=self.index_cache)
            self._index, self._examples = index, examples
        return self._index

    def run_rules(self) -> int:
        with self.sessions() as session:
            index = self.index(session)
            if index is None:
                return 0
            stmt = select(Job.id).where(
                Job.status == "active",
                Job.is_offer.is_(True),
                or_(
                    Job.isco_code.is_(None),
                    func.json_array_length(Job.contract_types) == 0,
                    and_(Job.salary_min.is_(None), Job.salary_max.is_(None)),
                ),
                or_(Job.rules_hash.is_(None), Job.rules_hash != Job.content_hash),
            )
            ids = list(session.scalars(stmt))
            self.progress.total = len(ids)
            classified = 0
            for start in range(0, len(ids), BATCH):
                if self._stop.is_set():
                    break
                chunk = list(
                    session.scalars(select(Job).where(Job.id.in_(ids[start : start + BATCH])))
                )
                for job, found in zip(
                    chunk, index.lookup_many([isco_title(j) for j in chunk]), strict=True
                ):
                    if not job.contract_types and job.description:
                        found_types = contract_types(job.description)
                        if found_types:
                            job.contract_types = found_types
                            _mark(job, ["contract_types"], "rules")
                    no_salary = job.salary_min is None and job.salary_max is None
                    if no_salary and (salary := salary_in_text(job.description)):
                        _set_salary(job, salary)
                        _mark(job, ["salary"], "rules")
                    winner = found.clear_winner() if job.isco_code is None else None
                    if winner:
                        _set_isco(job, index.codebook, found.detail_code(winner.code), "knn")
                        classified += 1
                    elif not job.categories:
                        # Shown as "Nezařazeno" until the AI (if any) finds an occupation.
                        job.categories = [UNCATEGORIZED]
                        job.updated_at = utcnow()
                    job.rules_hash = job.content_hash
                session.commit()
                self.progress.done = min(start + BATCH, len(ids))
                self.progress.classified = classified
        return classified

    def resolve_kinds(self) -> None:
        """Maps the profile's new kinds of work to occupations; a phrase mapped without the
        model is mapped again once the model is ready."""
        profile = self.profile()
        typed = kinds.phrases(profile.kinds_of_work) if profile else []
        with self.sessions() as session:
            index = self.index(session)
            if index is None:
                return
            known = kinds.load(session)
            ready = self.manager.ready
            todo = [p for p in typed if p not in known or (ready and not known[p]["ai"])]
            session.commit()  # no transaction open while the model reads
            if todo:
                known.update(self._resolve(index, todo, ready))
            kinds.store(session, {p: known[p] for p in typed if p in known})
            session.commit()

    def _resolve(self, index: IscoIndex, phrases: list[str], ready: bool) -> dict[str, dict]:
        if ready:
            try:
                with self.manager.session() as client:
                    return {p: wanted.resolve(index, p, client) for p in phrases}
            except (Busy, LlmError) as exc:
                log.info("kinds of work mapped without the model: %s", exc)
        return {p: wanted.resolve(index, p) for p in phrases}

    # --- 2. pictures --------------------------------------------------------------------

    def image_queue(self, session: Session) -> list[int]:
        """Stored pictures of short ads not read yet, newest ads first."""
        stmt = (
            select(JobImage.id)
            .join(Job, Job.id == JobImage.job_id)
            .where(
                JobImage.status == "stored",
                JobImage.text.is_(None),
                Job.status == "active",
                Job.is_offer.is_(True),
                func.length(Job.description) < IMAGE_TEXT_BELOW,
            )
            .order_by(Job.first_seen_at.desc(), JobImage.position)
            .limit(IMAGES_PER_RUN)
        )
        return list(session.scalars(stmt))

    def run_images(self) -> int:
        """Writes out the text of pictures; returns the number of ads that gained text."""
        spec = None if self.manager.custom_server else self.manager.active_model()
        if self.images_dir is None or (spec is not None and spec.mmproj is None):
            return 0  # a text-only model cannot read pictures
        p = self.progress
        with self.sessions() as session:
            ids = self.image_queue(session)
        p.phase, p.done, p.total = "images", 0, len(ids)
        changed = 0
        while ids and not self._stop.is_set():
            try:
                with self.manager.session(vision=True) as client, self.sessions() as session:
                    p.phase, p.wait_reason = "images", ""
                    while ids and not self._stop.is_set():
                        image = session.get(JobImage, ids[0])
                        if image is not None and image.file is not None:
                            path = self.images_dir / image.file
                            if path.exists():
                                text = read_before(session, image)
                                _ = image.job.images  # loaded before the session lets go
                                session.commit()  # no transaction open while the model reads
                                try:
                                    if text is None:
                                        text = vision.transcribe(client, path.read_bytes()).data
                                except BadAnswer as exc:
                                    log.warning("picture %s not read: %s", image.id, exc)
                                    text = ""
                                # Not counted in the speed statistics, which are per text ad.
                                image.text, image.text_model = text, client.model[:40]
                                changed += add_image_text(image.job)
                            else:
                                image.status = "failed"
                            session.commit()
                        ids = ids[1:]
                        p.done += 1
            except NoModel:
                break
            except Busy as exc:
                p.phase, p.wait_reason = "waiting", str(exc)
                self._stop.wait(self.wait_seconds)
            except LlmError as exc:  # e.g. an own server without image support
                log.warning("pictures not read: %s", exc)
                break
        return changed

    # --- 3. AI --------------------------------------------------------------------------

    def queue(self, session: Session) -> list[int]:
        """Job ids for the model, profile matches first. On slow PCs only those."""
        mpsv_primary = select(JobSource.job_id).where(
            JobSource.source_id == "mpsv", JobSource.is_primary.is_(True)
        )
        stmt = (
            select(Job.id, Job.title, Job.isco_code, Location.kraj, func.length(Job.description))
            .outerjoin(Location, Location.id == Job.location_id)
            .where(
                Job.status == "active",
                Job.is_offer.is_(True),
                or_(Job.ai_hash.is_(None), Job.ai_hash != Job.content_hash),
                Job.id.not_in(mpsv_primary),  # Úřad práce ads are fully structured
            )
            .where(or_(func.length(Job.description) >= MIN_DESCRIPTION, Job.isco_code.is_(None)))
        )
        profile = self.profile()
        query = build_query(PlaceResolver(session), profile) if profile else None
        kraje = set(query.kraje) if query and query.kraje else set()
        want = kinds.Wanted.load(session, profile.kinds_of_work if profile else [])
        matching, rest = [], []
        for job_id, title, isco_code, kraj, length in session.execute(stmt):
            in_area = not kraje or kraj in kraje or kraj is None
            # Full ads first: they gain the most from being read.
            entry = ((length or 0) < MIN_DESCRIPTION, job_id)
            (matching if in_area and want.matches(title, isco_code) else rest).append(entry)
        matching = [job_id for _, job_id in sorted(matching)]
        rest = [job_id for _, job_id in sorted(rest)]
        speed = self.manager.measured().get(getattr(self.manager.active_model(), "id", ""))
        slow = speed is not None and speed.seconds_per_ad() > SLOW_SECONDS
        return matching if slow else matching + rest

    def run_ai(self) -> int:
        p = self.progress
        with self.sessions() as session:
            index = self.index(session)
            places = PlaceResolver(session)
            ids = self.queue(session)
        p.phase, p.done, p.total = "ai", 0, len(ids)
        processed = 0
        while ids and not self._stop.is_set():
            chunk, ids = ids[:AI_CHUNK], ids[AI_CHUNK:]
            try:
                with self.manager.session() as client, self.sessions() as session:
                    p.phase, p.wait_reason = "ai", ""
                    for job_id in chunk:
                        if self._stop.is_set():
                            break
                        job = session.get(Job, job_id)
                        if job is not None:
                            try:
                                self._enrich(session, job, index, client, places)
                            except BadAnswer as exc:
                                log.warning("AI answer for job %s skipped: %s", job_id, exc)
                                job.ai_hash = job.content_hash
                            except ServerDown:
                                raise  # every next ad would fail the same way
                            except Exception:  # skipped, or every run would stop on this ad
                                log.exception("AI failed on job %s, skipped", job_id)
                                session.rollback()
                                job.ai_hash = job.content_hash
                            session.commit()
                            processed += 1
                        p.done += 1
            except NoModel:
                break
            except Busy as exc:
                ids = chunk + ids
                p.phase, p.wait_reason = "waiting", str(exc)
                self._stop.wait(self.wait_seconds)
        return processed

    def _enrich(self, session, job: Job, index, client, places: PlaceResolver) -> None:
        fields = missing_fields(job)
        found: Lookup | None = (
            index.lookup(isco_title(job)) if index and "profese" in fields else None
        )
        candidates = found.candidates if found else []
        if not candidates:  # no occupation to choose from: not asked
            fields = tuple(f for f in fields if f != "profese")
        text = _ad_text(job)
        spec = None if self.manager.custom_server else self.manager.active_model()
        model = spec.id if spec else client.model
        style = spec.prompt if spec else DEFAULT_STYLE
        key = _cache_key(model, text, [c.code for c in candidates], fields, style)
        cached = session.get(LlmCache, key)
        if cached is not None:
            result = validate(cached.result, text, list(found.votes) if found else [])
        elif not fields:
            result = Extraction()
        else:
            # No transaction stays open while the model reads (seconds): a scrape may write
            # meanwhile, and SQLite then refuses a write from an older read transaction.
            _ = job.location, job.sources
            session.commit()
            result = extract(client, text, candidates, fields, style=style, found=found)
            self.manager.record(result.timing)
            session.add(
                LlmCache(
                    key=key,
                    model=model,
                    prompt_version=PROMPT_VERSION,
                    result=_raw(result),
                    created_at=utcnow(),
                )
            )
        apply(session, job, result, found, index.codebook if index else None, places)


def read_before(session: Session, image: JobImage) -> str | None:
    """The text of the same picture read for another ad (an employer's logo or poster on
    many ads is read once)."""
    if not image.phash:
        return None
    return session.scalar(
        select(JobImage.text)
        .where(JobImage.phash == image.phash, JobImage.text.is_not(None), JobImage.id != image.id)
        .limit(1)
    )


def add_image_text(job: Job) -> bool:
    """Adds the text of the job's pictures to its description, once. The job is then
    processed again by the rules and the AI (its hashes are reset)."""
    texts = [i.text for i in job.images if i.text]
    if not texts or IMAGE_TEXT_HEADER in (job.description or ""):
        return False
    pictures = IMAGE_TEXT_HEADER + "\n" + "\n\n".join(texts)
    job.description = "\n\n".join(x for x in (job.description, pictures) if x)
    extra = dict(job.extra or {})
    if extra.pop("title_from_picture", False):  # an ad the user dropped in as a photo only
        job.title = next(line.strip() for t in texts for line in t.split("\n") if line.strip())[:90]
        job.extra = extra
    job.rules_hash = job.ai_hash = None
    _mark(job, ["description"], "image")
    job.updated_at = utcnow()
    return True


def _ad_text(job: Job) -> str:
    head = [job.title, job.employer_name or "", job.place_text or ""]
    return "\n".join(x for x in head if x) + "\n\n" + (job.description or "")


def isco_title(job: Job) -> str:
    """What the occupation lookup compares: the portal's own occupation name when it gives one
    (jobs.cz, Práce za rohem: "Skladník/manipulant"), else the ad title."""
    occupations = (job.extra or {}).get("occupations") or ()
    return occupations[0] if occupations else job.title


def missing_fields(job: Job) -> tuple[str, ...]:
    """Field groups (extract.FIELDS) the job does not have yet; for a listing card without a
    description only the occupation."""
    if len(job.description or "") < MIN_DESCRIPTION:
        return ("profese",) if job.isco_code is None else ()
    have = {
        "profese": job.isco_code is not None,
        "obec": job.location_id is not None,
        "mzda": job.salary_min is not None or job.salary_max is not None,
        "smeny": bool(job.shifts),
        "vzdelani": job.education is not None,
    }
    return tuple(name for name in FIELDS if not have[name])


def _cache_key(
    model: str, text: str, codes: list[str], fields: tuple[str, ...], style: PromptStyle
) -> str:
    raw = f"{model}\n{PROMPT_VERSION}\n{style}\n{','.join(fields)}\n{','.join(codes)}\n{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _raw(result: Extraction) -> dict:
    """The model's answer in its own vocabulary, so `validate` can re-check it later; the
    occupation as its code, whatever the prompt style offered."""

    def back(table: dict, codes: list) -> list:
        return [k for k, v in table.items() if v in codes]

    return {
        "nabidka": result.is_job_offer,
        "profese": result.isco_group or "jiná",
        "obec": result.town,
        "mzda_od": result.salary_min,
        "mzda_do": result.salary_max,
        "mzda_za": {"month": "měsíc", "hour": "hodina"}.get(result.salary_unit or ""),
        "smeny": back(SHIFTS, result.shifts),
        "vzdelani": next(iter(back(EDUCATION, [result.education])), None),
        "varovani": back(WARNINGS, result.warnings),
    }


def _set_isco(job: Job, codebook: IscoCodebook, code: str, origin: str) -> None:
    job.isco_code = code
    if not job.categories or job.categories == [UNCATEGORIZED]:
        job.categories = codebook.categories(code) or [UNCATEGORIZED]
    _mark(job, ["isco_code", "categories"], origin)


def _set_salary(job: Job, salary) -> None:
    job.salary_min, job.salary_max = salary.min, salary.max
    job.salary_unit, job.salary_currency = salary.unit, salary.currency
    job.update_derived()
    job.updated_at = utcnow()


def _mark(job: Job, fields: list[str], origin: str) -> None:
    extra = dict(job.extra or {})
    origins = dict(extra.get("origin") or {})
    origins.update(dict.fromkeys(fields, origin))
    extra["origin"] = origins
    job.extra = extra  # a new dict, so SQLAlchemy sees the change
    job.updated_at = utcnow()  # search facets are cached until a job changes


def _location_id(session: Session, loc: ParsedLocation) -> int:
    row = session.scalar(select(Location).where(Location.key == loc.key))
    if row is None:
        row = Location(
            key=loc.key,
            precision=loc.precision,
            address_text=loc.address_text,
            municipality=loc.municipality,
            municipality_code=loc.municipality_code,
            okres=loc.okres,
            kraj=loc.kraj,
            lat=loc.lat,
            lon=loc.lon,
        )
        session.add(row)
        session.flush()
    return row.id


def apply(
    session: Session,
    job: Job,
    result: Extraction,
    found: Lookup | None,
    codebook: IscoCodebook | None,
    places: PlaceResolver,
) -> list[str]:
    """Fills the job's empty fields from a checked model answer; returns what was filled."""
    filled: list[str] = []
    if job.isco_code is None and result.isco_group and found and codebook:
        _set_isco(job, codebook, found.detail_code(result.isco_group), "ai")
        filled.append("isco_code")
    elif job.isco_code is None and not job.categories:
        job.categories = [UNCATEGORIZED]  # the AI could not place it either
        job.updated_at = utcnow()
    if job.location_id is None and result.town:
        loc = places.resolve(result.town)
        if loc is not None:
            job.location_id = _location_id(session, loc)
            filled.append("location_id")
    if job.salary_min is None and job.salary_max is None and result.salary_unit:
        job.salary_min, job.salary_max = result.salary_min, result.salary_max
        job.salary_unit, job.salary_currency = result.salary_unit, "CZK"
        filled.append("salary")
    if not job.shifts and result.shifts:
        job.shifts = result.shifts
        filled.append("shifts")
    if job.education is None and result.education:
        job.education = result.education
        filled.append("education")
    extra_changes = {}
    if result.warnings:
        extra_changes["warnings"] = result.warnings
    if not result.is_job_offer:
        extra_changes["not_offer"] = True
    if extra_changes:
        job.extra = {**(job.extra or {}), **extra_changes}
    if filled:
        job.update_derived()
        _mark(job, [f for f in filled if f != "isco_code"], "ai")
        job.updated_at = utcnow()
    job.ai_hash = job.content_hash
    return filled
