"""The privacy gateway: who may see which recording, and that a refusal sends nothing at all."""
import pytest

from transcriber import privacy
from transcriber.privacy import Gateway, PrivacyBlocked
from transcriber.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "app.db")
    yield s
    s.close()


def add(store, rid, cls="standard"):
    store.create_recording(rid, f"Rec {rid}", None, "en", f"{rid}.m4a")
    store.set_privacy(rid, cls)


class CountingLLM:
    def __init__(self, name="groq:openai/gpt-oss-120b"):
        self.name = name
        self.calls = 0

    def complete_json(self, **kwargs):
        self.calls += 1
        return {"ok": True}


def approve(store, config="groq:openai/gpt-oss-120b", *, private=False, verified=False):
    store.set_policy(config, standard=True, private=private,
                     retention=privacy.RETENTION_VERIFIED if verified else "unverified")


def send(gw, llm, ids, feature="notes"):
    with privacy.scope(feature, ids):
        return gw.guard_llm(llm).complete_json(system="s", user="u", schema={}, schema_name="x")


def test_nothing_is_approved_at_first(store):
    add(store, "a")
    llm = CountingLLM()
    with pytest.raises(PrivacyBlocked) as err:
        send(Gateway(store), llm, ["a"])
    assert err.value.code == "not_approved" and llm.calls == 0


def test_standard_goes_once_approved(store):
    add(store, "a")
    approve(store)
    llm = CountingLLM()
    assert send(Gateway(store), llm, ["a"]) == {"ok": True} and llm.calls == 1


def test_private_needs_a_verified_zero_retention_approval(store):
    add(store, "p", "private")
    approve(store)  # approved for Standard only
    llm = CountingLLM()
    with pytest.raises(PrivacyBlocked) as err:
        send(Gateway(store), llm, ["p"])
    assert err.value.code == "private_not_verified" and llm.calls == 0
    approve(store, private=True, verified=True)
    assert send(Gateway(store), llm, ["p"]) == {"ok": True}


def test_claude_can_never_take_private(store):
    add(store, "p", "private")
    store.set_policy("claude-code", standard=True, private=True, retention=privacy.RETENTION_VERIFIED)
    llm = CountingLLM()
    llm.name = "claude-code"
    with pytest.raises(PrivacyBlocked) as err:
        send(Gateway(store), llm, ["p"])
    assert err.value.code == "private_impossible" and llm.calls == 0


def test_local_only_never_leaves(store):
    add(store, "l", "local_only")
    approve(store, private=True, verified=True)
    llm = CountingLLM()
    with pytest.raises(PrivacyBlocked) as err:
        send(Gateway(store), llm, ["l"])
    assert err.value.code == "local_only" and llm.calls == 0


def test_unknown_provider_scope_and_recording_are_refused(store):
    add(store, "a")
    approve(store)
    gw = Gateway(store)
    odd = CountingLLM()
    odd.name = "mystery:model"
    with pytest.raises(PrivacyBlocked) as err:
        send(gw, odd, ["a"])
    assert err.value.code == "unknown_provider"
    llm = CountingLLM()
    with pytest.raises(PrivacyBlocked) as err:  # no scope at all
        gw.guard_llm(llm).complete_json(system="s", user="u", schema={}, schema_name="x")
    assert err.value.code == "no_scope"
    with pytest.raises(PrivacyBlocked) as err:  # a recording that does not exist
        send(gw, llm, ["ghost"])
    assert err.value.code == "unknown_source"
    with pytest.raises(PrivacyBlocked) as err:  # a scope with nothing in it
        send(gw, llm, [])
    assert err.value.code == "no_context" and llm.calls == 0


def test_strictest_source_wins_for_mixed_requests(store):
    add(store, "a")
    add(store, "p", "private")
    approve(store)
    llm = CountingLLM()
    gw = Gateway(store)
    with pytest.raises(PrivacyBlocked):
        send(gw, llm, ["a", "p"])
    assert gw.eligible_ids("groq:openai/gpt-oss-120b", ["a", "p"]) == {"a"}


def test_combined_recording_inherits_and_keeps_the_floor_after_sources_are_erased(store):
    add(store, "x", "private")
    add(store, "y")
    add(store, "c")
    store.record_sources("c", ["x", "y"])
    approve(store)
    gw = Gateway(store)
    assert store.effective_privacy(["c"])["c"] == ("private", True)
    store.delete_recording("x")  # erased for good
    assert store.effective_privacy(["c"])["c"][0] == "private"  # the floor stays
    with pytest.raises(PrivacyBlocked):
        send(gw, CountingLLM(), ["c"])


def test_a_class_raised_later_reaches_what_was_made_from_it(store):
    add(store, "x")
    add(store, "c")
    store.record_sources("c", ["x"])
    store.set_privacy("x", "private")
    assert store.effective_privacy(["c"])["c"][0] == "private"


def test_untraceable_combined_recording_is_blocked_until_confirmed(store):
    add(store, "c")
    store._execute("UPDATE recordings SET privacy_provenance = 'unknown' WHERE id = 'c'")
    approve(store)
    gw = Gateway(store)
    with pytest.raises(PrivacyBlocked) as err:
        send(gw, CountingLLM(), ["c"])
    assert err.value.code == "unknown_provenance"
    store.confirm_provenance("c")
    assert send(gw, CountingLLM(), ["c"]) == {"ok": True}


def test_each_retry_asks_again_and_a_change_takes_effect_at_once(store):
    add(store, "a")
    approve(store)
    gw, llm = Gateway(store), CountingLLM()
    guarded = gw.guard_llm(llm)
    with privacy.scope("notes", ["a"]):
        guarded.complete_json(system="s", user="u", schema={}, schema_name="x")
        store.set_privacy("a", "private")  # changed between attempts
        with pytest.raises(PrivacyBlocked):
            guarded.complete_json(system="s", user="u", schema={}, schema_name="x")
    assert llm.calls == 1


def test_live_session_class_applies_before_it_has_a_recording(store):
    approve(store)
    gw = Gateway(store)
    with privacy.scope("live", classification="private"):
        with pytest.raises(PrivacyBlocked):
            gw.authorize("groq:openai/gpt-oss-120b")
    with privacy.scope("live", classification="standard"):
        gw.authorize("groq:openai/gpt-oss-120b")


def test_the_log_records_decisions_without_content(store):
    add(store, "a")
    approve(store)
    gw = Gateway(store)
    with pytest.raises(PrivacyBlocked):
        send(gw, CountingLLM(), ["a", "ghost"], feature="ask")
    rows = store.list_transmissions()
    assert rows[0]["decision"] == "blocked" and rows[0]["feature"] == "ask" and rows[0]["recording_ids"] == ["a", "ghost"]
    assert set(rows[0]) == {"id", "at", "feature", "config_id", "recording_ids", "decision", "reason",
                            "policy_revision"}


def test_guarded_remote_gates_uploads_only(store):
    add(store, "a")

    class Server:
        name = "server/whisper"
        submitted = 0

        def submit(self, *a, **k):
            Server.submitted += 1
            return "job"

        def health(self):
            return {"ok": True}

    gw = Gateway(store)
    remote = gw.guard_remote(Server())
    assert remote.health() == {"ok": True}  # nothing of yours in it
    with privacy.scope("upload", ["a"]):
        with pytest.raises(PrivacyBlocked):
            remote.submit("f")
    assert Server.submitted == 0
