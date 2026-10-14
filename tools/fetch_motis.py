"""Fetch the pinned routing programs into vendor/ (developer machines; the installer bundles
the same folders, PLAN §3.1):
- vendor/motis/   MOTIS (routing server and importer), with its tile profiles and map fonts;
- vendor/osmium/  osmium-tool from conda-forge (clips and renumbers OpenStreetMap data on
  machines with less RAM). GPL-3.0, shipped unmodified as a separate program.

Usage: uv run --with zstandard python tools/fetch_motis.py
"""

import hashlib
import io
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path

import httpx

VENDOR = Path(__file__).parents[1] / "vendor"
MOTIS_VERSION = "v2.11.3"
MOTIS_URL = (
    f"https://github.com/motis-project/motis/releases/download/{MOTIS_VERSION}/motis-windows.zip"
)
MOTIS_SHA256 = "34655045ee02b45f32a7f99f86be3195cb5fa44e1b791ca84ffba2eb284a67e3"
OSMIUM_VERSION = "1.19.1"
CONDA = "https://conda.anaconda.org/conda-forge/win-64/"
# osmium-tool and the libraries it loads; the packages hold .exe and .dll files.
OSMIUM_PACKAGES = {
    "osmium-tool-1.19.1-h60971b7_0.conda": "cbac55c919fdc8189fc25ea2c7dead293f587200d04e3cb4b7b5b8ecd0f835b0",  # noqa: E501
    "lz4-c-1.10.0-h6a83c73_2.conda": "6824e36be0f95ae516dc36dddd18f69cbad0e4e07906fcb10a5f26a8dc471903",  # noqa: E501
    "bzip2-1.0.8-h0ad9c76_10.conda": "04767466ee9227c9c57ab2c6503e0149177d34111c7418d2f420297acb1eb229",  # noqa: E501
    "libzlib-1.3.2-hfd05255_3.conda": "0629c2cc0404d3bb29d6baa7b4ba62da80797015e86de050db81ea5a07050527",  # noqa: E501
    "libexpat-2.8.5-hac47afa_0.conda": "70c73d9fd24ad996f2821d1501adae2ca70984b4e2d8faed76227f348ce86078",  # noqa: E501
    "libboost-1.90.0-h9dfe17d_3.conda": "e82a6fdfc93a7fa72b188ba2bcc0f740d564cf742c001d2b58f15f1c6e0e6437",  # noqa: E501
}
# Of libboost, only what osmium.exe loads.
BOOST_DLLS = {"boost_program_options.dll"}


def download(url: str, sha256: str) -> bytes:
    data = httpx.get(url, follow_redirects=True, timeout=300).raise_for_status().content
    digest = hashlib.sha256(data).hexdigest()
    if digest != sha256:
        raise SystemExit(f"{url}: sha256 {digest} does not match the pinned file")
    return data


def fresh(target: Path, version: str) -> bool:
    stamp = target / "VERSION"
    if stamp.exists() and stamp.read_text().strip() == version:
        print(f"{target.name}: {version} already present")
        return False
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    return True


def fetch_motis() -> None:
    target = VENDOR / "motis"
    if not fresh(target, MOTIS_VERSION):
        return
    data = download(MOTIS_URL, MOTIS_SHA256)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for member in archive.infolist():
            # The web UI is not used: the app has its own (only the map fonts are kept).
            name = member.filename
            if name.startswith("ui/") and not name.startswith("ui/glyphs/"):
                continue
            archive.extract(member, target)
    (target / "VERSION").write_text(MOTIS_VERSION)
    print(f"motis: {len(data) / 1e6:.1f} MB -> {target}")


def fetch_osmium() -> None:
    import zstandard  # only needed here: conda packages are zstd-compressed

    target = VENDOR / "osmium"
    if not fresh(target, OSMIUM_VERSION):
        return
    for name, sha256 in OSMIUM_PACKAGES.items():
        data = download(CONDA + name, sha256)
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            inner = next(n for n in package.namelist() if n.startswith("pkg-"))
            raw = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(package.read(inner)))
            with tarfile.open(fileobj=raw, mode="r|") as tar:
                for member in tar:
                    path = member.name.lower()
                    if not (member.isfile() and path.startswith("library/bin/")):
                        continue
                    file_name = Path(member.name).name
                    wanted = file_name == "osmium.exe" or (
                        path.endswith(".dll")
                        and (not file_name.startswith("boost_") or file_name in BOOST_DLLS)
                    )
                    if wanted:
                        (target / file_name).write_bytes(tar.extractfile(member).read())
    (target / "VERSION").write_text(OSMIUM_VERSION)
    print(f"osmium: {sum(f.stat().st_size for f in target.iterdir()) / 1e6:.1f} MB -> {target}")


def main() -> int:
    if sys.platform != "win32":
        print("Windows builds only; on other systems install MOTIS and osmium-tool yourself.")
        return 1
    fetch_motis()
    fetch_osmium()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
