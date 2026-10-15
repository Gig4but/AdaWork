# PyInstaller build of the window app (PLAN §3.1): dist/AdaWork/ with AdaWork.exe, Python
# inside, the bundled programs in vendor/ and the license texts. Run by packaging/build.ps1.
# ruff: noqa

import re
import shutil
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

from adawork.licenses import METADATA_FILE, metadata_license, package_files, package_folder
from adawork.licenses import runtime_dists

ROOT = Path(SPECPATH).parent
BUILD = ROOT / "build"
VENDOR = ROOT / "vendor"
# llama.cpp releases ship ~20 tools; the app runs only llama-server.
LLAMA = re.compile(r"llama-server\.exe|llama-server-impl\.dll|(llama|llama-common|mtmd|libomp)\.dll"
                   r"|ggml(-base|-cpu-.+|-vulkan)?\.dll|LICENSE.*|BUILD")
# llama.cpp and osmium need the Microsoft C++ runtime, which a clean Windows may not have:
# the redistributable files go next to them (app-local), at least as new as their build tools.
RUNTIME = ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")
RUNTIME_MIN = (14, 44)

datas = collect_data_files("adawork")  # templates, static files, yaml lists
# Alembic reads the migration scripts as files.
datas += collect_data_files("adawork.db", subdir="migrations", include_py_files=True)
hiddenimports = [m for m in collect_submodules("adawork") if not m.startswith("adawork.db.migrations")]

a = Analysis(
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "IPython"],
    # pystray is LGPL-3.0: kept as source files, so a user can replace it.
    module_collection_mode={"pystray": "py"},
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AdaWork",
    icon=str(BUILD / "adawork.ico"),
    console=False,
    upx=False,  # packed programs look suspicious to virus scanners
)
coll = COLLECT(exe, a.binaries, a.datas, name="AdaWork", upx=False)

app = Path(DISTPATH) / "AdaWork"


def file_version(path: Path) -> tuple[int, ...]:
    import pefile

    pe = pefile.PE(str(path), fast_load=True)
    pe.parse_data_directories(
        directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]]
    )
    fixed = pe.VS_FIXEDFILEINFO[0]
    return fixed.FileVersionMS >> 16, fixed.FileVersionMS & 0xFFFF


# Python, the web libraries and the texts that packages lack (tools/fetch_licenses.py), then
# the packages' own texts (adawork/licenses.py lists the same files on a developer machine).
licenses = app / "licenses"
shutil.copytree(VENDOR / "licenses", licenses)
for key, dist in sorted(runtime_dists().items()):
    folder = licenses / "python" / package_folder(key, dist)
    for rel, source in package_files(dist):
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, folder / rel)
    if not folder.exists():
        folder.mkdir(parents=True)
        (folder / METADATA_FILE).write_text(metadata_license(dist), "utf-8")
shutil.copyfile(ROOT / "LICENSE", app / "LICENSE")
shutil.copyfile(ROOT / "packaging" / "THIRD-PARTY-NOTICES.txt", app / "THIRD-PARTY-NOTICES.txt")
shutil.copyfile(ROOT / "packaging" / "navod.html", app / "navod.html")

for folder in ("cpu", "vulkan"):
    source, target = VENDOR / "llama.cpp" / folder, app / "vendor" / "llama.cpp" / folder
    target.mkdir(parents=True)
    for file in source.iterdir():
        if LLAMA.fullmatch(file.name):
            shutil.copy2(file, target)
shutil.copytree(VENDOR / "motis", app / "vendor" / "motis")
shutil.copytree(VENDOR / "osmium", app / "vendor" / "osmium")
system = Path(r"C:\Windows\System32")
for name in RUNTIME:
    if file_version(system / name) < RUNTIME_MIN:
        sys.exit(f"{name} is older than {RUNTIME_MIN}: install the latest VC++ redistributable")
    for folder in ("llama.cpp/cpu", "llama.cpp/vulkan", "osmium"):
        shutil.copy2(system / name, app / "vendor" / folder)
# A virus scanner may have removed one of them since they were fetched.
PROGRAMS = ("llama.cpp/cpu/llama-server.exe", "llama.cpp/vulkan/llama-server.exe",
            "motis/motis.exe", "osmium/osmium.exe")
missing = [p for p in PROGRAMS if not (app / "vendor" / p).exists()]
if missing:
    sys.exit(f"missing in vendor/: {missing}; run the fetch scripts again")
