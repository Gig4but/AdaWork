"""License texts of everything the app bundles (PLAN M10): copied by the installer build
(packaging/adawork.spec) and shown in Settings → Licence.

The installed app keeps them in its folder: THIRD-PARTY-NOTICES.txt, licenses/ (Python, its
packages, the web libraries) and vendor/*/ (the programs). On a developer machine the same texts
come from the repository (vendor/ by the tools/fetch_*.py scripts) and the installed packages.
"""

import re
import sys
from dataclasses import dataclass
from functools import cache
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement

from adawork import paths

# File names of license texts in Python packages (onnxruntime: ThirdPartyNotices.txt).
PACKAGE_FILES = ("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS", "THIRDPARTYNOTICES")
# Display order; the groups are the folders in licenses/ plus the notices and the programs.
GROUPS = ("notices", "programs", "runtime", "python", "web")
# The programs' texts: name, folder in vendor/, files.
PROGRAMS = (
    ("llama.cpp", "llama.cpp/cpu", "LICENSE*"),
    ("MOTIS", "motis/licenses", "*"),
    ("osmium", "osmium/licenses", "*"),
)
METADATA_FILE = "LICENSE-METADATA.txt"


@dataclass(frozen=True)
class LicenseFile:
    group: str
    title: str
    path: Path | None = None
    text: str = ""  # when there is no file (a package without one, on a developer machine)

    def read(self) -> str:
        if self.path is None:
            return self.text
        return self.path.read_bytes().decode("utf-8", "replace")


def package_key(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def runtime_dists() -> dict[str, metadata.Distribution]:
    """The installed packages the app needs, from its dependencies (not the dev tools)."""
    found, todo = {}, ["adawork"]
    while todo:
        name = todo.pop()
        key = package_key(name)
        if key in found:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        found[key] = dist
        for line in dist.requires or []:
            req = Requirement(line)
            if req.marker is None or req.marker.evaluate({"extra": ""}):
                todo.append(req.name)
    found.pop("adawork", None)
    return found


def package_folder(key: str, dist: metadata.Distribution) -> str:
    """The package's folder in licenses/python/ (tools/fetch_licenses.py adds to it)."""
    return f"{key}-{dist.version}"


def package_files(dist: metadata.Distribution) -> list[tuple[Path, Path]]:
    """(path in the package's folder, source file) of the package's license texts."""
    found: dict[Path, Path] = {}
    for file in dist.files or []:
        if not Path(file).name.upper().startswith(PACKAGE_FILES):
            continue
        # Folders kept: numpy and playwright each have several files named LICENSE.
        parts = Path(file).parts
        if parts[0].endswith(".dist-info"):
            parts = parts[2:] if parts[1] == "licenses" and len(parts) > 2 else parts[1:]
        # numpy has some both in its .dist-info and next to its code.
        found.setdefault(Path(*parts), Path(str(dist.locate_file(file))))
    return list(found.items())


def metadata_license(dist: metadata.Distribution) -> str:
    """For a package without a license text anywhere: the license its metadata names."""
    meta = dist.metadata
    classifiers = [c for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    declared = meta.get("License-Expression") or meta.get("License") or "; ".join(classifiers)
    urls = [meta.get("Home-page")]
    urls += [u.split(",", 1)[-1].strip() for u in meta.get_all("Project-URL") or []]
    return (
        f"{dist.name} {dist.version} ships no license file.\n"
        f"Declared license: {declared or 'not stated'}\n"
        f"Project: {next((u for u in urls if u), 'not stated')}\n"
    )


def collect(app: Path, vendor: Path, installed: bool) -> list[LicenseFile]:
    """Every license text, in display order. `app` is the install folder or the repository."""
    found = []
    own = app / "LICENSE"  # AdaWork's own (MIT)
    notices = app / ("" if installed else "packaging") / "THIRD-PARTY-NOTICES.txt"
    for title, file in (("AdaWork / LICENSE", own), (notices.name, notices)):
        if file.is_file():
            found.append(LicenseFile("notices", title, file))
    for name, folder, pattern in PROGRAMS:
        for file in sorted((vendor / folder).glob(pattern)):
            if file.is_file():
                found.append(LicenseFile("programs", f"{name} / {file.name}", file))
    texts = app / "licenses" if installed else vendor / "licenses"
    for file in texts.rglob("*"):
        rel = file.relative_to(texts)
        if file.is_file() and len(rel.parts) > 1 and rel.parts[0] in GROUPS:
            found.append(LicenseFile(rel.parts[0], " / ".join(rel.parts[1:]), file))
    if not installed:
        # The installer copies these into licenses/python/ (packaging/adawork.spec).
        fetched = {f.title.split(" / ")[0] for f in found if f.group == "python"}
        for key, dist in runtime_dists().items():
            folder = package_folder(key, dist)
            files = package_files(dist)
            for rel, source in files:
                title = " / ".join((folder, *rel.parts))
                found.append(LicenseFile("python", title, source))
            if not files and folder not in fetched:
                title = f"{folder} / {METADATA_FILE}"
                found.append(LicenseFile("python", title, text=metadata_license(dist)))
    return sorted(found, key=lambda f: (GROUPS.index(f.group), f.title.lower()))


@cache
def files() -> tuple[LicenseFile, ...]:
    """The license texts of this app (read once: they do not change while it runs)."""
    installed = getattr(sys, "frozen", False)
    app = Path(sys.executable).parent if installed else Path(__file__).parents[1]
    return tuple(collect(app, paths.vendor_dir(), installed))
