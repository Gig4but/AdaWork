from importlib import metadata

from adawork import licenses


def write(path, text="text"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")


def test_the_installed_app_lists_its_folder_in_display_order(tmp_path):
    app, vendor = tmp_path / "AdaWork", tmp_path / "AdaWork" / "vendor"
    write(app / "THIRD-PARTY-NOTICES.txt")
    write(app / "LICENSE")
    write(app / "licenses" / "web" / "htmx-2.0.11" / "LICENSE")
    write(app / "licenses" / "python" / "numpy-2.5.3" / "numpy" / "libdivide" / "LICENSE.txt")
    write(app / "licenses" / "python" / "numpy-2.5.3" / "LICENSE.txt")
    write(app / "licenses" / "runtime" / "Python-3.12.14" / "LICENSE.txt")
    write(app / "licenses" / "stray.txt")  # not in a group: left out
    write(vendor / "llama.cpp" / "cpu" / "LICENSE")
    write(vendor / "llama.cpp" / "cpu" / "BUILD")
    write(vendor / "motis" / "licenses" / "boost-LICENSE_1_0.txt")
    found = licenses.collect(app, vendor, installed=True)
    assert [(f.group, f.title) for f in found] == [
        ("notices", "AdaWork / LICENSE"),
        ("notices", "THIRD-PARTY-NOTICES.txt"),
        ("programs", "llama.cpp / LICENSE"),
        ("programs", "MOTIS / boost-LICENSE_1_0.txt"),
        ("runtime", "Python-3.12.14 / LICENSE.txt"),
        ("python", "numpy-2.5.3 / LICENSE.txt"),
        ("python", "numpy-2.5.3 / numpy / libdivide / LICENSE.txt"),
        ("web", "htmx-2.0.11 / LICENSE"),
    ]
    assert found[0].read() == "text"


def test_a_developer_machine_reads_the_installed_packages(tmp_path):
    vendor = tmp_path / "vendor"
    write(vendor / "licenses" / "python" / "tokenizers-0.23.2" / "LICENSE", "Apache")
    found = licenses.collect(tmp_path, vendor, installed=False)
    titles = [f.title for f in found if f.group == "python"]
    fastapi = f"fastapi-{metadata.version('fastapi')} / LICENSE"
    assert fastapi in titles and "tokenizers-0.23.2 / LICENSE" in titles
    assert not any(t.startswith("tokenizers-") and t.endswith("METADATA.txt") for t in titles)
    assert not any(t.startswith(("pytest-", "ruff-", "pyinstaller-")) for t in titles)  # dev tools
    # A package without any text: what its metadata says.
    proxy = next(f for f in found if f.title.startswith("proxy-tools-"))
    assert proxy.path is None and "Declared license: MIT" in proxy.read()


def test_files_of_one_name_keep_their_folders():
    targets = [rel for rel, _ in licenses.package_files(metadata.distribution("numpy"))]
    assert len(targets) == len(set(targets)) > 1
    assert all(not rel.parts[0].endswith(".dist-info") for rel in targets)
