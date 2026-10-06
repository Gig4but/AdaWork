# /// script
# requires-python = ">=3.12"
# dependencies = ["zstandard"]
# ///
"""Fetch osmium-tool for Windows from conda-forge (no conda needed): the .conda packages of
osmium-tool and its runtime DLL dependencies are unpacked and Library/bin is flattened into
OUT_DIR. Usage: uv run fetch_osmium_tool.py OUT_DIR"""
import hashlib, io, sys, tarfile, urllib.request, zipfile
from pathlib import Path
import zstandard

# Pinned builds and their SHA-256: the packages hold .exe and .dll files.
PKGS = {
    "osmium-tool-1.19.1-h60971b7_0.conda": "cbac55c919fdc8189fc25ea2c7dead293f587200d04e3cb4b7b5b8ecd0f835b0",
    "lz4-c-1.10.0-h6a83c73_2.conda": "6824e36be0f95ae516dc36dddd18f69cbad0e4e07906fcb10a5f26a8dc471903",
    "bzip2-1.0.8-h0ad9c76_10.conda": "04767466ee9227c9c57ab2c6503e0149177d34111c7418d2f420297acb1eb229",
    "libzlib-1.3.2-hfd05255_3.conda": "0629c2cc0404d3bb29d6baa7b4ba62da80797015e86de050db81ea5a07050527",
    "libexpat-2.8.5-hac47afa_0.conda": "70c73d9fd24ad996f2821d1501adae2ca70984b4e2d8faed76227f348ce86078",
    "libboost-1.90.0-h9dfe17d_3.conda": "e82a6fdfc93a7fa72b188ba2bcc0f740d564cf742c001d2b58f15f1c6e0e6437",
}
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
total = 0
for basename, sha256 in PKGS.items():
    data = urllib.request.urlopen(f"https://conda.anaconda.org/conda-forge/win-64/{basename}").read()
    if hashlib.sha256(data).hexdigest() != sha256:
        sys.exit(f"{basename}: SHA-256 does not match the pinned value")
    total += len(data)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        pkg = next(n for n in z.namelist() if n.startswith("pkg-"))
        raw = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(z.read(pkg)))
        with tarfile.open(fileobj=raw, mode="r|") as t:
            for m in t:
                if m.isfile() and m.name.lower().startswith("library/bin/") and m.name.lower().endswith((".exe", ".dll")):
                    (out / Path(m.name).name).write_bytes(t.extractfile(m).read())
    print(f"{basename}: {len(data)} bytes")
print(f"downloaded {total} bytes")
