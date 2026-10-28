"""A project's page: what is linked to it, plus what the linked recordings say that matters to it (their decisions and the
follow-ups still open), gathered from your notes on this laptop. No AI model."""
from .briefing import follow_ups


def _recording(store, rid: str) -> dict:
    rec = store.get_recording(rid)
    if rec is None:
        return {"ref": rid, "missing": True, "title": "(erased recording)"}
    return {"ref": rid, "id": rec.id, "title": rec.title, "subject": rec.subject, "at": rec.recorded_at or rec.created_at,
            "in_bin": bool(rec.deleted_at), "missing": False, "duration_sec": rec.duration_sec}


def project_detail(store, project_id: int, tz: str) -> dict | None:
    project = store.get_project(project_id)
    if project is None:
        return None
    links = store.project_links(project_id)
    recordings, people, concepts, documents = [], [], [], []
    for link in links:
        kind, ref = link["kind"], link["ref"]
        if kind == "recording":
            recordings.append(_recording(store, ref))
        elif kind == "person":
            person = store.get_person(int(ref)) if ref.isdigit() else None
            people.append({"ref": ref, "name": person.name if person else "(removed person)", "missing": person is None})
        elif kind == "concept":
            rows = store.query("SELECT id, name FROM concepts WHERE id = ?", (int(ref),)) if ref.isdigit() else []
            concepts.append({"ref": ref, "name": rows[0]["name"] if rows else "(merged or removed concept)", "missing": not rows})
        else:
            documents.append({"ref": ref, "label": link["label"] or ref})
    live_ids = {r["id"] for r in recordings if not r["missing"] and not r["in_bin"]}
    decisions = []
    for rid in live_ids:
        record = store.get_notes(rid)
        if record and record.notes is not None and record.status == "ready":
            title = next(r["title"] for r in recordings if r.get("id") == rid)
            decisions += [{"text": d.text, "recording_id": rid, "recording": title, "start": d.cites[0] if d.cites else None}
                          for d in record.notes.decisions]
    open_items = [i for i in follow_ups(store, tz) if i["recording_id"] in live_ids]
    recordings.sort(key=lambda r: r.get("at") or "", reverse=True)
    return {**project, "recordings": recordings, "people": people, "concepts": concepts, "documents": documents,
            "decisions": decisions, "follow_ups": open_items, "tasks": store.project_tasks(project_id)}
