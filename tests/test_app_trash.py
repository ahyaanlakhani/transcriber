"""Recently Deleted and the No subject filter through the API."""
from datetime import UTC, datetime, timedelta

from .test_app import detail, env, upload, wav  # noqa: F401


def test_delete_moves_to_recently_deleted_then_restore(env, wav):
    rec = upload(env, wav, subject="Team")
    rid = rec["id"]
    assert env.client.delete(f"/api/recordings/{rid}").status_code == 204
    assert env.client.get("/api/recordings").json() == []
    trash = env.client.get("/api/recordings?deleted=1").json()
    assert [r["id"] for r in trash] == [rid] and trash[0]["deleted_at"]
    assert (env.data_dir / "recordings" / rid).exists()  # audio kept while in the bin
    assert env.client.get("/api/recordings/counts").json()["deleted"] == 1
    assert detail(env, rid)["deleted_at"]
    assert env.client.post(f"/api/recordings/{rid}/restore").json()["deleted_at"] is None
    assert [r["id"] for r in env.client.get("/api/recordings").json()] == [rid]


def test_delete_forever_and_empty(env, wav):
    a, b = upload(env, wav)["id"], upload(env, wav)["id"]
    env.client.post("/api/recordings/delete", json={"ids": [a, b]})
    assert env.client.get("/api/recordings/counts").json() == {"all": 0, "starred": 0, "no_subject": 0, "deleted": 2}
    assert env.client.delete(f"/api/recordings/{a}?forever=1").status_code == 204
    assert not (env.data_dir / "recordings" / a).exists()
    assert env.client.get(f"/api/recordings/{a}").status_code == 404
    assert env.client.delete("/api/trash").json() == {"erased": 1}
    assert not (env.data_dir / "recordings" / b).exists()


def test_old_deletions_are_erased_after_30_days(env, wav):
    rid = upload(env, wav)["id"]
    old = (datetime.now(UTC) - timedelta(days=31)).isoformat(timespec="seconds")
    env.store.soft_delete(rid, at=old)
    assert env.client.get("/api/recordings?deleted=1").json() == []  # listing the bin tidies it
    assert not (env.data_dir / "recordings" / rid).exists()


def test_no_subject_filter(env, wav):
    upload(env, wav, subject="Team")
    loose = upload(env, wav)["id"]
    assert [r["id"] for r in env.client.get("/api/recordings?no_subject=1").json()] == [loose]


def test_deleted_recordings_cannot_be_combined_or_merged(env, wav):
    a, b = upload(env, wav)["id"], upload(env, wav)["id"]
    while env.worker.run_once():
        pass
    env.client.delete(f"/api/recordings/{a}")
    assert env.client.post("/api/recordings/combine", json={"ids": [a, b]}).status_code == 400
    assert env.client.post("/api/recordings/merge", json={"ids": [a, b]}).status_code == 400
