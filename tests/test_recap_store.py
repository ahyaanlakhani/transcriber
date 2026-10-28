from transcriber.store import Store


def make(tmp_path):
    return Store(tmp_path / "app.db")


def test_queue_update_and_get(tmp_path):
    store = make(tmp_path)
    store.create_recording("a", "Class", None, "en", "a.m4a")
    assert store.get_recap("a") is None
    store.queue_recap("a")
    assert store.get_recap("a").status == "queued" and store.next_recap_queued() == "a"
    store.update_recap("a", status="ready", script="Hello.", voice="af_heart", speed=1.1, duration_sec=300.0)
    recap = store.get_recap("a")
    assert (recap.status, recap.script, recap.duration_sec) == ("ready", "Hello.", 300.0)
    assert store.next_recap_queued() is None
    store.queue_recap("a")  # remake: back to queued, old script kept until replaced
    assert store.get_recap("a").status == "queued" and store.get_recap("a").script == "Hello."
    store.close()


def test_deleted_recordings_are_skipped_and_erasing_removes_the_recap(tmp_path):
    store = make(tmp_path)
    store.create_recording("a", "Class", None, "en", "a.m4a")
    store.queue_recap("a")
    store.soft_delete("a")
    assert store.next_recap_queued() is None
    store.delete_recording("a")
    assert store.get_recap("a") is None
    store.close()


def test_interrupted_recaps_are_requeued(tmp_path):
    store = make(tmp_path)
    store.create_recording("a", "Class", None, "en", "a.m4a")
    store.queue_recap("a")
    store.update_recap("a", status="speaking")
    store.reset_interrupted()
    assert store.get_recap("a").status == "queued"
    store.close()


def test_latest_recap_per_subject(tmp_path):
    store = make(tmp_path)
    tech = store.get_or_create_subject("Machine Learning")
    law = store.get_or_create_subject("Law")
    for rid, subject, when, status in (("old", tech, "2026-10-05T05:00:00+00:00", "ready"),
                                       ("new", tech, "2026-10-07T05:00:00+00:00", "ready"),
                                       ("newest-failed", tech, "2026-10-08T05:00:00+00:00", "failed"),
                                       ("law", law, "2026-10-06T05:00:00+00:00", "ready"),
                                       ("loose", None, "2026-10-09T05:00:00+00:00", "ready")):
        store.create_recording(rid, rid, subject, "en", f"{rid}.m4a")
        store.update_recording(rid, recorded_at=when)
        store.queue_recap(rid)
        store.update_recap(rid, status=status)
    assert [rec.id for rec, _ in store.latest_recaps()] == ["new", "law"]
    store.close()


def test_recap_settings_defaults(tmp_path):
    store = make(tmp_path)
    settings = store.get_settings()
    assert (settings["recaps_on"], settings["recap_voice"], settings["recap_speed"]) == ("1", "af_heart", "1.1")
    store.close()
