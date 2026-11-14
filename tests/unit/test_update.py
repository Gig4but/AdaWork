import hashlib
import json
import threading

import httpx
import pytest

from adawork.desktop import update
from adawork.desktop.update import UpdateError, Updater, newer_release, take_pending

INSTALLER = b"MZ fake installer"
SIZE = len(INSTALLER)
URL = update.ASSET_URL + "v1.1.0/AdaWork-Setup.exe"


def release(tag="v1.1.0", url=URL, size=SIZE, digest=None, **extra):
    digest = digest or "sha256:" + hashlib.sha256(INSTALLER).hexdigest()
    asset = {"name": update.ASSET, "browser_download_url": url, "size": size, "digest": digest}
    return {"tag_name": tag, "draft": False, "prerelease": False, "assets": [asset], **extra}


def github(answer: dict, body: bytes = INSTALLER, seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        if str(request.url) == update.LATEST:
            return httpx.Response(200, json=answer)
        assert str(request.url) == URL
        return httpx.Response(200, content=body)

    return lambda: httpx.Client(transport=httpx.MockTransport(handler))


def test_only_a_newer_release_with_a_checked_installer_is_taken():
    found = newer_release(release(), "1.0.0")
    assert found and found.version == "1.1.0" and found.file.size == len(INSTALLER)
    assert newer_release(release("v1.0"), "1.0.0") is None  # same version
    assert newer_release(release("v0.9.0"), "1.0.0") is None  # never back
    assert newer_release(release(prerelease=True), "1.0.0") is None
    with pytest.raises(UpdateError, match="address"):
        newer_release(release(url="https://evil.example/AdaWork-Setup.exe"), "1.0.0")
    with pytest.raises(UpdateError, match="checksum"):
        newer_release(release(digest="md5:abc"), "1.0.0")
    with pytest.raises(UpdateError, match="size"):
        newer_release(release(size=0), "1.0.0")
    with pytest.raises(UpdateError, match="version"):
        newer_release(release(tag="latest"), "1.0.0")


def test_auto_downloads_a_newer_installer_for_the_next_start(tmp_path):
    folder = tmp_path / "updates"
    folder.mkdir()
    (folder / "AdaWork-Setup-0.9.exe").write_bytes(b"old")  # left by an earlier update
    updater = Updater(folder, lambda: True, "1.0.0", github(release()), can_install=lambda: True)
    updater.work()
    assert updater.status.state == "ready" and updater.status.version == "1.1.0"
    assert updater.installer is None  # auto: installed at the next start, not now
    assert sorted(p.name for p in folder.iterdir()) == ["AdaWork-Setup-1.1.0.exe", "pending.json"]
    pending = take_pending(folder, "1.0.0")
    assert pending is not None and pending[0].read_bytes() == INSTALLER
    assert take_pending(folder, "1.0.0") is None  # tried once only
    assert [p.name for p in folder.iterdir()] == ["tried.txt"]
    updater.work()  # that installer failed: not downloaded and run again by itself
    assert updater.status.state == "available" and not (folder / "pending.json").exists()


def test_a_tampered_installer_is_never_run(tmp_path, monkeypatch):
    folder = tmp_path / "updates"
    updater = Updater(
        folder, lambda: True, "1.0.0", github(release(), body=b"MZ evil!"), lambda: True
    )
    updater.work()
    assert updater.status.state == "failed" and "checksum" in updater.status.error
    assert not (folder / "pending.json").exists()
    updater = Updater(folder, lambda: True, "1.0.0", github(release()), lambda: True)
    updater.work()
    info = json.loads((folder / "pending.json").read_text())
    (folder / "pending.json").write_text(json.dumps(info | {"name": "..\\x.exe"}))
    assert take_pending(folder, "1.0.0") is None  # only an installer name in this folder
    updater.work()
    (folder / "AdaWork-Setup-1.1.0.exe").write_bytes(b"MZ evil!" + b"x" * 9)  # same size
    assert take_pending(folder, "1.0.0") is None
    assert [p.name for p in folder.iterdir()] == []
    started = []
    monkeypatch.setattr(update.subprocess, "Popen", lambda *a, **k: started.append(a))
    file = newer_release(release(), "1.0.0").file
    (folder / file.name).write_bytes(b"MZ evil!" + b"x" * 9)
    assert not update.run_installer(folder / file.name, file) and not started
    (folder / file.name).write_bytes(INSTALLER)
    assert update.run_installer(folder / file.name, file) and started


def test_install_now_quits_the_app_for_the_installer(tmp_path):
    quit = threading.Event()
    updater = Updater(tmp_path, lambda: False, "1.0.0", github(release()), lambda: True)
    updater.quit = quit.set
    updater.install_now()
    updater.work()
    assert quit.wait(5) and updater.status.state == "installing"
    assert updater.installer is not None
    assert updater.installer[0] == tmp_path / "AdaWork-Setup-1.1.0.exe"
    updater.discard()  # switching auto off does not take what the button asked for
    assert updater.installer[0].exists()
    updater._requested = True  # while the button's download runs
    updater.status = update.UpdateStatus("downloading", "1.1.0")
    updater.discard()
    assert updater.installer[0].exists() and not updater._cancel.is_set()


def test_switching_auto_off_while_downloading_keeps_nothing(tmp_path):
    auto = {"on": True}
    updater = Updater(tmp_path, lambda: auto["on"], "1.0.0", lambda: None, lambda: True)

    def download_while_switched_off(release):
        updater.status = update.UpdateStatus("downloading", release.version)
        auto["on"] = False
        updater.discard()
        (tmp_path / "pending.json").write_text("{}")  # the download ended just after
        return tmp_path / release.file.name

    updater._latest = lambda: newer_release(release(), "1.0.0")
    updater._download = download_while_switched_off
    updater.work()
    assert updater.status.state == "available" and not any(tmp_path.iterdir())


def test_an_unexpected_error_ends_as_failed_not_checking(tmp_path):
    def broken():
        raise RuntimeError("boom")

    updater = Updater(tmp_path, lambda: True, "1.0.0", broken, lambda: True)
    updater.work()
    assert updater.status.state == "failed" and updater.status.error == "boom"


def test_without_auto_nothing_is_asked_until_the_user_checks(tmp_path):
    seen = []
    updater = Updater(
        tmp_path, lambda: False, current="1.0.0", http_factory=github(release(), seen=seen),
        can_install=lambda: False,
    )  # fmt: skip
    updater.work()
    assert seen == [] and updater.status.state == "idle"
    updater.check_now()
    updater.work()
    assert seen == [update.LATEST] and updater.status.state == "available"
    updater.install_now()  # a developer's copy cannot install
    updater.work()
    assert updater.status.state == "available" and not any(tmp_path.iterdir())


def test_up_to_date_cleans_what_an_update_left(tmp_path):
    (tmp_path / "pending.json").write_text(json.dumps({"version": "1.0.0"}))
    updater = Updater(tmp_path, lambda: True, current="1.1.0", http_factory=github(release()))
    updater.work()
    assert updater.status.state == "current" and not any(tmp_path.iterdir())


def test_a_download_larger_than_announced_is_stopped(tmp_path):
    from adawork.ai.download import DownloadError, RemoteFile, download

    file = RemoteFile("https://x/f", "f.exe", 4, "0" * 64)
    big = httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * (1 << 21)))
    with httpx.Client(transport=big) as client, pytest.raises(DownloadError, match="larger"):
        download(file, tmp_path, client)
