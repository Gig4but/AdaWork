"""`adawork eval`: accuracy and speed of a model on the golden set (PLAN §6.7).

Golden ads (tests/fixtures/eval/text_ads.jsonl, built by tools/make_golden.py) carry labels
from Úřad práce's structured data. The ads themselves are excluded from the ISCO examples, so
the model never sees its own answer.

Reported per model: ISCO at 1/2/4 digits and category overlap, kraj, contract types, salary,
valid JSON, seconds per ad (mean, p90), and tokens.
"""

import argparse
import json
import statistics
import time
from collections import Counter
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from adawork import paths
from adawork.ai import advisor
from adawork.ai.catalog import DEFAULT_STYLE, MODELS, PromptStyle
from adawork.ai.client import LlmClient, LlmError
from adawork.ai.embeddings import Embedder
from adawork.ai.extract import FIELDS, extract
from adawork.ai.isco import IscoCodebook, IscoIndex
from adawork.ai.manager import EMBEDDER_DIR
from adawork.ai.runtime import LlamaServer
from adawork.db.models import Job, JobSource
from adawork.db.session import make_engine
from adawork.geo.places import PlaceResolver
from adawork.normalize.text import contract_types
from adawork.system import hardware

GOLDEN = Path(__file__).parents[2] / "tests" / "fixtures" / "eval" / "text_ads.jsonl"
CODEBOOK_FIXTURE = (
    Path(__file__).parents[2] / "tests" / "fixtures" / "sources" / "mpsv" / "codebooks"
)


def codebook_path() -> Path:
    cached = paths.data_dir() / "cache" / "mpsv" / "codebooks" / "cz-isco.json"
    return cached if cached.exists() else CODEBOOK_FIXTURE / "cz-isco.json"


def load_golden(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def golden_job_ids(session: Session, golden: list[dict]) -> set[int]:
    ids = set()
    for ad in golden:
        source, external = ad["id"].split(":", 1)
        job_id = session.scalar(
            select(JobSource.job_id).where(
                JobSource.source_id == source, JobSource.external_id == external
            )
        )
        if job_id is not None:
            ids.add(job_id)
    return ids


def build_index(session: Session, golden: list[dict], embedder: Embedder) -> IscoIndex:
    codebook = IscoCodebook.load(codebook_path())
    excluded = golden_job_ids(session, golden)
    excluded_titles = {
        (t, c)
        for t, c in session.execute(select(Job.title, Job.isco_code).where(Job.id.in_(excluded)))
    }
    # Drop a title only when no other (non-golden) MPSV ad has the same title and code.
    stmt = (
        select(Job.title, Job.isco_code)
        .join(JobSource, JobSource.job_id == Job.id)
        .where(JobSource.source_id == "mpsv", Job.isco_code.is_not(None), Job.id.not_in(excluded))
    )
    others = Counter((t.strip(), c) for t, c in session.execute(stmt))
    examples = [
        pair
        for pair in IscoIndex.examples(session, codebook)
        if pair not in {(t.strip(), c) for t, c in excluded_titles} or others[pair]
    ]
    index = IscoIndex(codebook, embedder)
    index.build(examples, cache=paths.models_dir() / EMBEDDER_DIR / "isco-titles.npz")
    return index


def score(rows: list[dict]) -> dict:
    n = len(rows)
    ok = lambda key: sum(bool(r[key]) for r in rows) / n if n else 0.0  # noqa: E731
    seconds = [r["seconds"] for r in rows if r["seconds"] is not None]
    return {
        "ads": n,
        "valid_json": ok("valid"),
        "isco1": ok("isco1"),
        "isco2": ok("isco2"),
        "isco4": ok("isco4"),
        "category": ok("category"),
        "isco2_knn_only": ok("isco2_knn"),
        "kraj": ok("kraj"),
        "kraj_rules": ok("kraj_rules"),
        # Contract types come from the rules (the model is not asked): how often they find
        # Úřad práce's label, and how often what they find is right.
        "contract_rules_covers": ok("contract_rules"),
        "contract_rules_precision": (
            sum(r["contract_rules"] for r in rows if r["contract_found"])
            / max(1, sum(bool(r["contract_found"]) for r in rows))
        ),
        "contract_found": ok("contract_found"),
        # Shifts and education: how often the model gives one, and how often it agrees with
        # Úřad práce when it does (the ad may state less than the structured data).
        "shifts_given": ok("shifts_given"),
        "shifts_agree": sum(r["shifts_agree"] for r in rows)
        / max(1, sum(r["shifts_given"] for r in rows)),
        "education_given": ok("education_given"),
        "education_agree": sum(r["education_agree"] for r in rows)
        / max(1, sum(r["education_given"] for r in rows)),
        "salary": ok("salary"),
        "seconds_mean": statistics.fmean(seconds) if seconds else None,
        "seconds_p90": statistics.quantiles(seconds, n=10)[-1] if len(seconds) > 2 else None,
        "prompt_tokens_mean": statistics.fmean(r["prompt_tokens"] for r in rows) if rows else 0,
        "output_tokens_mean": statistics.fmean(r["output_tokens"] for r in rows) if rows else 0,
    }


def _same(code: str | None, gold: str, digits: int) -> bool:
    """Equal at `digits` digits; a less detailed gold code ("432") is compared at its own
    length."""
    n = min(digits, len(gold))
    return bool(code) and code[:n] == gold[:n]


def evaluate_ad(ad, index, client, places, codebook, style=DEFAULT_STYLE, fields=FIELDS) -> dict:
    labels = ad["labels"]
    text = ad["text"]
    title = text.split("\n", 1)[0]
    found = index.lookup(title)
    candidates = found.candidates
    clear = found.clear_winner()
    gold = labels["isco"]
    row = {"id": ad["id"], "valid": False, "seconds": None, "prompt_tokens": 0, "output_tokens": 0}
    try:
        result = extract(client, text, candidates, fields, style=style, found=found)
        row["valid"] = True
        row["seconds"] = result.timing.wall_seconds
        row["prompt_tokens"] = result.timing.prompt_tokens
        row["output_tokens"] = result.timing.output_tokens
    except LlmError as exc:
        row["error"] = str(exc)
        result = None

    group = clear.code if clear else (result.isco_group if result else None)
    if group is None and candidates:
        group = candidates[0].code
    code = found.detail_code(group) if group else None
    knn = candidates[0].code if candidates else ""
    row.update(
        predicted_isco=code,
        gold_isco=gold,
        llm_isco=result.isco_group if result else None,
        isco1=_same(code, gold, 1),
        isco2=_same(code, gold, 2),
        isco4=_same(code, gold, 4),
        isco2_knn=_same(knn, gold, 2),
        category=bool(code)
        and bool(set(codebook.categories(code)) & set(codebook.categories(gold))),
    )

    place_line = text.split("\n")[2] if text.count("\n") >= 2 else ""
    rules_loc = places.resolve(place_line.split(",")[-1].strip()) or places.resolve(place_line)
    llm_loc = places.resolve(result.town) if result and result.town else None
    loc = llm_loc or rules_loc
    row["kraj"] = bool(loc) and loc.kraj == labels["kraj"]
    row["kraj_rules"] = bool(rules_loc) and rules_loc.kraj == labels["kraj"]
    row["predicted_kraj"] = loc.kraj if loc else None

    gold_contracts = set(labels["contract_types"])
    rules = set(contract_types(text))
    row["contract_found"] = bool(rules)
    row["contract_rules"] = bool(rules) and bool(rules & gold_contracts)
    row["predicted_contracts"] = sorted(rules)

    gold_salary = (labels["salary_min"], labels["salary_max"])
    got_salary = (result.salary_min, result.salary_max) if result else (None, None)
    row["salary"] = got_salary == gold_salary or (
        gold_salary[0] is not None and got_salary[0] == gold_salary[0]
    )
    shifts = set(result.shifts) if result else set()
    row["shifts_given"] = bool(shifts)
    row["shifts_agree"] = bool(shifts) and shifts <= set(labels["shifts"])
    education = result.education if result else None
    row["education_given"] = education is not None
    row["education_agree"] = education is not None and education == labels["education"]
    row["dropped"] = result.dropped if result else []
    return row


def run(
    model_id: str,
    db: Path,
    golden_path: Path,
    limit: int | None,
    backend: str | None,
    threads: int | None,
    out: Path | None,
    style: PromptStyle | None = None,
    fields: tuple[str, ...] = FIELDS,
    only_unclear: bool = False,
) -> dict:
    """`only_unclear`: just the ads whose title vote is unclear, i.e. where the model chooses
    the occupation (fast prompt experiments with `fields=("profese",)`)."""
    golden = load_golden(golden_path)[:limit]
    hw = hardware.probe()
    spec = MODELS[model_id]
    style = style or spec.prompt
    launch = advisor.launch_for(spec, hw, paths.models_dir() / spec.id)
    if backend == "cpu":
        launch = replace(launch, backend="cpu", gpu_layers=0)
    if threads:
        launch = replace(launch, threads=threads)
    embedder = Embedder(paths.models_dir() / EMBEDDER_DIR, threads=advisor.threads(hw))
    server = LlamaServer(paths.vendor_dir(), paths.log_dir() / "llama-server-eval.log")
    engine = make_engine(db)
    rows = []
    with Session(engine) as session:
        index = build_index(session, golden, embedder)
        places = PlaceResolver(session)
        started = time.perf_counter()
        if only_unclear:
            golden = [
                ad for ad in golden if not index.lookup(ad["text"].split("\n", 1)[0]).clear_winner()
            ]
        client = LlmClient("", model=spec.id, chat_kwargs=spec.chat_kwargs)
        with server.use(launch) as url, closing(client):
            client.base_url = url
            load_seconds = time.perf_counter() - started
            for i, ad in enumerate(golden, 1):
                row = evaluate_ad(ad, index, client, places, index.codebook, style, fields)
                rows.append(row)
                print(
                    f"{i:3} {row['seconds'] or 0:5.1f}s isco {row['predicted_isco']}/"
                    f"{row['gold_isco']} kraj {'ok' if row['kraj'] else '--'} "
                    f"contract {row['predicted_contracts']} {ad['text'][:40]!r}",
                    flush=True,
                )
        server.stop()
    report = {
        "model": spec.id,
        "style": str(style),
        "fields": ",".join(fields),
        "backend": launch.backend,
        "threads": launch.threads,
        "hardware": hw.fingerprint(),
        "load_seconds": round(load_seconds, 1),
        **score(rows),
    }
    if out:
        out.write_text(
            json.dumps({"report": report, "rows": rows}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="adawork eval")
    parser.add_argument("--model", default="qwen3.5-2b", choices=sorted(MODELS))
    parser.add_argument("--db", type=Path, default=None, help="database with MPSV ads")
    parser.add_argument("--golden", type=Path, default=GOLDEN)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--backend", choices=["vulkan", "cpu"])
    parser.add_argument("--threads", type=int)
    parser.add_argument("--out", type=Path, help="write per-ad results as JSON")
    parser.add_argument(
        "--style",
        help="prompt style instead of the model's, e.g. code, code+shares, two+shares, name+en",
    )
    parser.add_argument("--fields", help=f"comma-separated subset of {','.join(FIELDS)}")
    parser.add_argument("--only-unclear", action="store_true")
    args = parser.parse_args(argv)
    style = None
    if args.style:
        parts = set(args.style.split("+"))
        if unknown := parts - {"code", "shares", "name", "en", "two"}:
            parser.error(f"unknown --style parts: {', '.join(sorted(unknown))}")
        if {"two", "en"} <= parts:
            parser.error("two-step prompts are Czech only: use two or en, not both")
        style = PromptStyle(
            shares="shares" in parts,
            by_name="name" in parts,
            english="en" in parts,
            two_step="two" in parts,
        )
    fields = tuple(args.fields.split(",")) if args.fields else FIELDS
    from adawork.db.session import db_file

    report = run(
        args.model,
        args.db or db_file(),
        args.golden,
        args.limit,
        args.backend,
        args.threads,
        args.out,
        style,
        fields,
        args.only_unclear,
    )
    for key, value in report.items():
        shown = (
            f"{value:.0%}"
            if isinstance(value, float)
            and value <= 1
            and key not in ("load_seconds",)
            and not key.startswith(("seconds", "prompt", "output"))
            else value
        )
        print(f"{key:24} {shown if not isinstance(shown, float) else round(shown, 1)}")
    return 0
