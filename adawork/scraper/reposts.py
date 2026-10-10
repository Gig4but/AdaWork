"""Reposts of free-text ads: the same offer posted to several groups or boards (PLAN §4.3).

The portal dedup (scraper/dedup.py) needs an employer and a place; posts on boards and in
social groups rarely name the employer, so the same text posted twice would show twice.
Here posts of the free-text sources are grouped by the start of their text and merged when
the words agree (dedup.same_post). The picture hash does the same for posters (images.py).
"""

import re
from collections import defaultdict
from collections.abc import Collection

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from adawork.db.models import Job, JobImage, JobSource
from adawork.normalize.text import fold
from adawork.scraper.dedup import same_post

MIN_TEXT = 80  # characters: shorter posts ("Hledám brigádu") are too alike to compare
KEY_WORDS = 12  # the first words of a post are its key


def merge(session: Session, older: Job, newer: Job) -> None:
    """The newer job's sources move to the older job; the newer job is deleted."""
    session.execute(
        update(JobSource)
        .where(JobSource.job_id == newer.id)
        .values(job_id=older.id, is_primary=False)
    )
    session.execute(delete(JobImage).where(JobImage.job_id == newer.id))
    session.expire(older, ["sources"])
    session.delete(newer)
    session.flush()


def text_key(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", fold(text[:600]))[:KEY_WORDS])


def merge_text_reposts(session: Session, source_ids: Collection[str]) -> int:
    """Merges active offers of `source_ids` that repeat another post's text."""
    rows = session.execute(
        select(Job.id, Job.description, Job.first_seen_at)
        .join(JobSource, JobSource.job_id == Job.id)
        .where(
            JobSource.source_id.in_(list(source_ids)),
            Job.status == "active",
            Job.is_offer,
        )
        .distinct()
    ).all()
    groups: dict[str, list[tuple]] = defaultdict(list)
    for job_id, text, seen in rows:
        if len(text or "") >= MIN_TEXT:
            groups[text_key(text)].append((seen, job_id))
    merged = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort()
        older = session.get_one(Job, members[0][1])
        for _, job_id in members[1:]:
            newer = session.get(Job, job_id)
            if newer is not None and same_post(
                older.title, older.description, newer.title, newer.description
            ):
                merge(session, older, newer)
                merged += 1
    session.commit()
    return merged
