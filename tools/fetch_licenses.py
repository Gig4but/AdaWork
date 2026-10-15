"""Fetch the license texts that the bundled software does not carry itself into vendor/licenses/
(the installer copies the folder to licenses/; Settings → Licence shows it):
- runtime/  Python and the libraries built into it;
- python/   packages whose wheels lack a text, and libraries bundled in the pyproj, lxml and
            selectolax wheels (folders named as in licenses/python/, adawork/licenses.py);
- web/      htmx, MapLibre GL JS and Pico CSS (adawork/web/static/vendor/).
The programs' texts come with them: tools/fetch_llama.py, tools/fetch_motis.py.

Usage: uv run python tools/fetch_licenses.py
"""

import shutil
import sys
from importlib import metadata
from pathlib import Path

import httpx

from adawork.licenses import package_folder, package_key

TARGET = Path(__file__).parents[1] / "vendor" / "licenses"
GITHUB = "https://raw.githubusercontent.com/"
PYTHON = "{}.{}.{}".format(*sys.version_info[:3])
# Versions in pyproj's wheel (pyproj.libs), read from the DLLs' version information.
CURL, LIBJPEG_TURBO, LIBTIFF, ZLIB, XZ = "8_19_0", "3.1.0", "4.7.1", "1.3.1", "5.8.3"
LEXBOR = "f4cbbcd91359a0ec9499e3ce7e263de629482d61"  # selectolax 1.0.0's lexbor submodule
HTMX, MAPLIBRE, PICO = "2.0.11", "5.24.0", "2.1.1"  # the files in adawork/web/static/vendor/
BZIP2 = "https://sourceware.org/git/?p=bzip2.git;a=blob_plain;f=LICENSE;hb=refs/tags/bzip2-1.0.8"
LGPL21 = "https://www.gnu.org/licenses/old-licenses/lgpl-2.1.txt"  # lxml's iconv


def package(name: str) -> str:
    return "python/" + package_folder(package_key(name), metadata.distribution(name))


def sources() -> dict[str, str]:
    """Path in vendor/licenses/ -> URL of the text."""
    runtime = f"runtime/Python-{PYTHON}/"
    zlib = f"{GITHUB}madler/zlib/v{ZLIB}/LICENSE"
    xz = f"{GITHUB}tukaani-project/xz/v{XZ}/"
    lexbor = f"{GITHUB}lexbor/lexbor/{LEXBOR}/"
    pyproj, lxml, selectolax = package("pyproj"), package("lxml"), package("selectolax")
    return {
        # OpenSSL, libffi, zlib, expat, mpdecimal and the other code Python incorporates.
        runtime + "incorporated-software.rst": f"{GITHUB}python/cpython/v{PYTHON}/Doc/license.rst",
        runtime + "bzip2-LICENSE": BZIP2,
        runtime + "xz-COPYING": xz + "COPYING",
        package("tokenizers") + "/LICENSE": (
            f"{GITHUB}huggingface/tokenizers/v{metadata.version('tokenizers')}/LICENSE"
        ),
        package("primp") + "/LICENSE": f"{GITHUB}deedy5/primp/v{metadata.version('primp')}/LICENSE",
        package("flatbuffers") + "/LICENSE": (
            f"{GITHUB}google/flatbuffers/v{metadata.version('flatbuffers')}/LICENSE"
        ),
        pyproj + "/bundled/curl-COPYING": f"{GITHUB}curl/curl/curl-{CURL}/COPYING",
        pyproj + "/bundled/libjpeg-turbo-LICENSE.md": (
            f"{GITHUB}libjpeg-turbo/libjpeg-turbo/{LIBJPEG_TURBO}/LICENSE.md"
        ),
        pyproj + "/bundled/libjpeg-README.ijg": (
            f"{GITHUB}libjpeg-turbo/libjpeg-turbo/{LIBJPEG_TURBO}/README.ijg"
        ),
        pyproj + "/bundled/libtiff-LICENSE.md": (
            f"https://gitlab.com/libtiff/libtiff/-/raw/v{LIBTIFF}/LICENSE.md"
        ),
        pyproj + "/bundled/zlib-LICENSE": zlib,
        pyproj + "/bundled/xz-COPYING": xz + "COPYING",
        pyproj + "/bundled/xz-COPYING.0BSD": xz + "COPYING.0BSD",
        lxml + "/bundled/zlib-LICENSE": zlib,
        lxml + "/bundled/iconv-LGPL-2.1.txt": LGPL21,
        selectolax + "/bundled/lexbor-LICENSE": lexbor + "LICENSE",
        selectolax + "/bundled/lexbor-NOTICE": lexbor + "NOTICE",
        f"web/htmx-{HTMX}/LICENSE": f"{GITHUB}bigskysoftware/htmx/v{HTMX}/LICENSE",
        f"web/maplibre-gl-js-{MAPLIBRE}/LICENSE.txt": (
            f"{GITHUB}maplibre/maplibre-gl-js/v{MAPLIBRE}/LICENSE.txt"
        ),
        f"web/pico-{PICO}/LICENSE.md": f"{GITHUB}picocss/pico/v{PICO}/LICENSE.md",
    }


def main() -> int:
    wanted = sources()
    python_license = TARGET / f"runtime/Python-{PYTHON}/LICENSE.txt"
    # Texts of versions no longer bundled go; present ones are kept.
    keep = {TARGET / p for p in wanted} | {python_license}
    for file in TARGET.rglob("*") if TARGET.exists() else ():
        if file.is_file() and file not in keep:
            file.unlink()
    python_license.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(sys.base_prefix) / "LICENSE.txt", python_license)
    with httpx.Client(follow_redirects=True, timeout=60) as client:
        for path, url in wanted.items():
            target = TARGET / path
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(client.get(url).raise_for_status().content)
            print(f"{path} <- {url}")
    for folder in sorted(TARGET.rglob("*"), reverse=True):
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    print(f"licenses: {len(keep)} texts -> {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
