"""Fetch the pinned llama.cpp Windows builds into vendor/llama.cpp/ (developer machines).

The installer bundles the same two folders (PLAN §3.1):
- vulkan/  GPU build for integrated and dedicated Intel/AMD/NVIDIA graphics;
- cpu/     fallback when Vulkan is missing or broken.

Usage: uv run python tools/fetch_llama.py
"""

import hashlib
import io
import re
import shutil
import sys
import zipfile
from pathlib import Path

import httpx

BUILD = "b11449"
URL = "https://github.com/ggml-org/llama.cpp/releases/download/{build}/llama-{build}-bin-win-{flavor}-x64.zip"
SHA256 = {
    "vulkan": "775d77dd6b287a3b3a9c65dd1f5a0f46532ee84d322481629f5a2fd8ca7b7952",
    "cpu": "706ebd6fa5b73e0138cc616a678d4d67022ba91c4892ceb7189b5e5a4d10de14",
}
FLAVORS = tuple(SHA256)
REPO = "https://raw.githubusercontent.com/ggml-org/llama.cpp/{build}/"
# llama.cpp's own license and those of the libraries built into llama-server: a license file,
# or a header that ends with its license.
LICENSES = {
    "LICENSE": "LICENSE",
    "LICENSE-jsonhpp": "licenses/LICENSE-jsonhpp",
    "LICENSE-httplib": "vendor/cpp-httplib/LICENSE",
    "LICENSE-miniaudio": "vendor/miniaudio/miniaudio.h",
    "LICENSE-stb": "vendor/stb/stb_image.h",
}
HEADER_LICENSE = re.compile(r"This software is available (as a choice of|under)")
VENDOR = Path(__file__).parents[1] / "vendor" / "llama.cpp"


def fetch(flavor: str) -> None:
    target = VENDOR / flavor
    stamp = target / "BUILD"
    # A virus scanner may remove a program it does not know yet: fetched again then.
    needed = (*LICENSES, "llama-server.exe")
    if (
        stamp.exists()
        and stamp.read_text().strip() == BUILD
        and all((target / name).exists() for name in needed)
    ):
        print(f"{flavor}: {BUILD} already present")
        return
    url = URL.format(build=BUILD, flavor=flavor)
    print(f"{flavor}: downloading {url}")
    data = httpx.get(url, follow_redirects=True, timeout=300).raise_for_status().content
    digest = hashlib.sha256(data).hexdigest()
    if digest != SHA256[flavor]:
        raise SystemExit(f"{flavor}: sha256 {digest} does not match the pinned build")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        archive.extractall(target)
    for name, path in LICENSES.items():
        url = REPO.format(build=BUILD) + path
        text = httpx.get(url, follow_redirects=True, timeout=60).raise_for_status().text
        if path.endswith(".h"):
            text = text[list(HEADER_LICENSE.finditer(text))[-1].start() :]
        (target / name).write_text(text, "utf-8")
    stamp.write_text(BUILD)
    print(f"{flavor}: {len(data) / 1e6:.1f} MB -> {target}")


def main() -> int:
    if sys.platform != "win32":
        print("Windows builds only; on other systems install llama.cpp and set its path.")
        return 1
    for flavor in FLAVORS:
        fetch(flavor)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
