import contextlib
import json
import threading
import time

from adawork.config import Settings


def test_one_invalid_setting_keeps_the_others(tmp_path):
    path = tmp_path / "config.json"
    data = {
        "language": "en",
        "wizard_completed": True,
        "profile": {"home_address": "Kolín", "max_minutes": 999, "kinds_of_work": ["a", 5]},
        "automation": {"enabled": True, "interval_hours": 7},
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    settings = Settings.load(path)
    assert settings.language == "en" and settings.wizard_completed
    assert settings.profile.home_address == "Kolín"
    assert settings.profile.max_minutes == 45 and settings.profile.kinds_of_work == []
    assert settings.automation.enabled and settings.automation.interval_hours == 12  # consent kept
    assert json.loads((tmp_path / "config.json.broken").read_text("utf-8")) == data


def test_settings_that_are_not_an_object_fall_back_to_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('["en"]', encoding="utf-8")
    assert Settings.load(path).model_dump() == Settings().model_dump()


def test_concurrent_saves_do_not_clash(tmp_path):
    path = tmp_path / "config.json"
    Settings().save(path)
    errors, done = [], threading.Event()

    def save(language):
        try:
            for _ in range(10):
                Settings(language=language).save(path)
        except Exception as exc:
            errors.append(exc)

    def read():  # an open reader makes os.replace fail on Windows
        while not done.is_set():
            with contextlib.suppress(OSError):
                path.read_bytes()
            time.sleep(0.005)

    reader = threading.Thread(target=read)
    reader.start()
    savers = [threading.Thread(target=save, args=(lang,)) for lang in ["cs", "en"] * 2]
    for thread in savers:
        thread.start()
    for thread in savers:
        thread.join()
    done.set()
    reader.join()
    assert errors == []
    assert Settings.load(path).language in ("cs", "en")
    assert [p.name for p in tmp_path.iterdir()] == ["config.json"]
