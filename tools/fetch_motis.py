"""Fetch the pinned routing programs into vendor/ (developer machines; the installer bundles
the same folders, PLAN §3.1):
- vendor/motis/   MOTIS (routing server and importer), with its tile profiles and map fonts;
- vendor/osmium/  osmium-tool from conda-forge (clips and renumbers OpenStreetMap data on
  machines with less RAM). GPL-3.0, shipped unmodified as a separate program.

Usage: uv run --with zstandard python tools/fetch_motis.py
"""

import hashlib
import io
import re
import shutil
import sys
import tarfile
import zipfile
from collections import deque
from pathlib import Path

import httpx

VENDOR = Path(__file__).parents[1] / "vendor"
MOTIS_VERSION = "v2.11.3"
MOTIS_URL = (
    f"https://github.com/motis-project/motis/releases/download/{MOTIS_VERSION}/motis-windows.zip"
)
MOTIS_SHA256 = "34655045ee02b45f32a7f99f86be3195cb5fa44e1b791ca84ffba2eb284a67e3"
GITHUB = "https://raw.githubusercontent.com/"
MOTIS_REPO = f"{GITHUB}motis-project/motis/{MOTIS_VERSION}/"
# The libraries built into motis.exe: its .pkg file and theirs, each pinned to a commit.
PKG_ENTRY = re.compile(r"\[(.+?)\]\s+url=git@github\.com:(.+?)\.git\s+branch=(.+?)\s+commit=(\w+)")
NOT_BUNDLED = {"docs", "doctest", "googletest"}  # documentation and test frameworks
LICENSE_NAMES = (
    "LICENSE", "LICENSE.txt", "LICENSE.md", "license.txt", "License.txt", "LICENSE_1_0.txt",
    "LICENSE.MIT", "COPYING", "COPYRIGHT",
)  # fmt: skip
# Copies without a license file of their own: upstream's text ({branch} from the .pkg file).
UPSTREAM_LICENSES = {
    "boost": GITHUB + "boostorg/boost/{branch}/LICENSE_1_0.txt",
    "expat": GITHUB + "libexpat/libexpat/R_2_1_0/expat/COPYING",  # expat.h says 2.1.0
    # the map's font (ui/glyphs): Noto Sans
    "pbf-sdf-fonts": GITHUB + "notofonts/latin-greek-cyrillic/NotoSans-v2.015/OFL.txt",
}
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


def fresh(target: Path, version: str, needed: tuple[str, ...]) -> bool:
    stamp = target / "VERSION"
    # A virus scanner may remove a program it does not know yet: fetched again then.
    if (
        stamp.exists()
        and stamp.read_text().strip() == version
        and all((target / name).exists() for name in needed)
    ):
        print(f"{target.name}: {version} already present")
        return False
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    return True


def fetch_motis() -> None:
    target = VENDOR / "motis"
    if not fresh(target, MOTIS_VERSION, ("motis.exe",)):
        motis_licenses(target)
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
    motis_licenses(target)


def motis_licenses(target: Path) -> None:
    """licenses/: MOTIS's license and those of the libraries built into it."""
    folder, stamp = target / "licenses", target / "LICENSES-VERSION"
    if stamp.exists() and stamp.read_text().strip() == MOTIS_VERSION:
        return
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir()
    missing = []
    with httpx.Client(follow_redirects=True, timeout=60) as client:
        main = client.get(MOTIS_REPO + "LICENSE").raise_for_status()
        (folder / "LICENSE-motis.txt").write_bytes(main.content)
        # Breadth first: MOTIS's own pins win over those of the libraries it uses.
        queue = deque(PKG_ENTRY.findall(client.get(MOTIS_REPO + ".pkg").raise_for_status().text))
        seen = set(NOT_BUNDLED)
        while queue:
            name, repo, branch, commit = queue.popleft()
            if name in seen:
                continue
            seen.add(name)
            raw = f"{GITHUB}{repo}/{commit}/"
            urls = [raw + n for n in LICENSE_NAMES]
            if name in UPSTREAM_LICENSES:
                urls = [UPSTREAM_LICENSES[name].format(branch=branch)]
            for url in urls:
                response = client.get(url)
                if response.status_code == 200:
                    (folder / f"{name}-{url.rsplit('/', 1)[-1]}").write_bytes(response.content)
                    break
            else:
                missing.append(name)
            pkg = client.get(raw + ".pkg")
            if pkg.status_code == 200:
                queue.extend(PKG_ENTRY.findall(pkg.text))
    stamp.write_text(MOTIS_VERSION)
    print(f"motis: {len(seen) - len(NOT_BUNDLED) - len(missing)} library licenses -> {folder}")
    if missing:
        print(f"motis: no license file in {', '.join(sorted(missing))}")


def fetch_osmium() -> None:
    import zstandard  # only needed here: conda packages are zstd-compressed

    target = VENDOR / "osmium"
    if not fresh(target, OSMIUM_VERSION, ("licenses", "osmium.exe")):
        return
    (target / "licenses").mkdir()
    for name, sha256 in OSMIUM_PACKAGES.items():
        data = download(CONDA + name, sha256)
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            licenses(package, target / "licenses", name.rsplit("-", 2)[0])
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


def licenses(package: zipfile.ZipFile, target: Path, project: str) -> None:
    """The package's license texts (osmium-tool is GPL-3.0: its text ships with it)."""
    import zstandard

    inner = next(n for n in package.namelist() if n.startswith("info-"))
    raw = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(package.read(inner)))
    with tarfile.open(fileobj=raw, mode="r|") as tar:
        for member in tar:
            if member.isfile() and member.name.startswith("info/licenses/"):
                name = f"{project}-{Path(member.name).name}"
                (target / name).write_bytes(tar.extractfile(member).read())


def main() -> int:
    if sys.platform != "win32":
        print("Windows builds only; on other systems install MOTIS and osmium-tool yourself.")
        return 1
    fetch_motis()
    fetch_osmium()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
