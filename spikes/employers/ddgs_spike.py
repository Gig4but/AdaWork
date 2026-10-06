# /// script
# requires-python = ">=3.12"
# dependencies = ["ddgs>=9.0"]
# ///
"""Web mentions spike: ddgs metasearch for employer experience queries.

Run: uv run spikes/employers/ddgs_spike.py
"""

import importlib.metadata
import json
import time
from pathlib import Path

from ddgs import DDGS

FIX = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "employers" / "ddgs"
FIX.mkdir(parents=True, exist_ok=True)

QUERIES = [
    "Penny Market zkušenosti zaměstnanci",
    "Penny Market recenze práce",
    "Stavby Kohout s.r.o. zkušenosti",
]


def main() -> None:
    print("ddgs version:", importlib.metadata.version("ddgs"))
    out = {}
    with DDGS() as d:
        for q in QUERIES:
            t = time.time()
            try:
                res = d.text(q, region="cz-cs", safesearch="moderate", max_results=10)
            except Exception as e:  # noqa: BLE001
                print(f"{q!r}: ERROR {type(e).__name__}: {e}")
                out[q] = {"error": f"{type(e).__name__}: {e}"}
                time.sleep(5)
                continue
            print(f"{q!r}: {len(res)} results in {time.time() - t:.1f}s")
            for r in res:
                print("   ", r.get("href"), "|", (r.get("title") or "")[:70])
            out[q] = res
            time.sleep(5)
    (FIX / "text_results.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
