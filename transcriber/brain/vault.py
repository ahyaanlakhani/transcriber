"""Export the second brain as an Obsidian vault: a folder of Markdown files joined by [[wikilinks]].

    Home.md, Concepts/, Classes/ (one page per recording), People/, Subjects/

The app remembers which files it wrote and what they said (in .transcriber-export.json). Exporting again replaces only
those, removes the ones it no longer needs, and never touches anything else in the folder: your own notes there are safe,
and a page of yours with the same name as one of the app's makes the app's page take a different name. If you edited a page
the app wrote, that page becomes yours: it is left exactly as you left it, and the app's new version of it is written
under another name."""
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from ..export import format_timestamp, notes_to_md
from .index import Brain

MANIFEST = ".transcriber-export.json"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
_BAD = re.compile(r'[\\/:*?"<>|#^\[\]]+')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def safe_name(name: str) -> str:
    text = " ".join(_BAD.sub(" ", name).split()).strip(" .")[:90] or "Untitled"
    return f"{text} (page)" if text.lower() in _RESERVED else text


def day(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%Y-%m-%d")


class Pages:
    """Picks a unique file for each page, before any page is written, so links can point at them."""

    def __init__(self, root: Path, owned: set[str]):
        self.root, self.owned = root, owned
        self.paths: dict[tuple[str, str], str] = {}  # (folder, key) -> "Folder/Name" (no .md)
        self.taken: set[str] = set()

    def claim(self, folder: str, key: str, name: str) -> str:
        base = f"{folder}/{safe_name(name)}" if folder else safe_name(name)
        path, n = base, 1
        while path.lower() in self.taken or self._theirs(path):  # never write over a page of yours
            n += 1
            path = f"{base} ({n})"
        self.taken.add(path.lower())
        self.paths[(folder, key)] = path
        return path

    def _theirs(self, path: str) -> bool:
        """A file there that the app didn't write: the user's own."""
        return (self.root / f"{path}.md").exists() and f"{path}.md" not in self.owned

    def link(self, folder: str, key, label: str | None = None) -> str:
        path = self.paths.get((folder, str(key)))
        if path is None:
            return label or str(key)
        name = path.rsplit("/", 1)[-1]
        shown = label or name
        return f"[[{path}|{shown}]]" if shown != path else f"[[{path}]]"


def export_vault(brain: Brain, root: Path) -> dict:
    store = brain.store
    brain.catch_up()
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / MANIFEST
    try:
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = {}
    owned = set(previous.get("files", []))
    hashes = previous.get("hashes", {})
    edited = set()  # pages the app wrote that you have changed since: from now on they are yours
    for name in owned:
        target = root / name
        if name in hashes and target.is_file():
            try:
                if digest(target.read_text(encoding="utf-8")) != hashes[name]:
                    edited.add(name)
            except (OSError, UnicodeDecodeError):
                edited.add(name)
    owned -= edited

    concepts = brain.list_concepts()
    pages_of = {c["id"]: brain.concept(c["id"]) for c in concepts}
    eligible = brain.eligible()
    recordings = sorted(eligible.values(), key=lambda r: r.recorded_at or r.created_at)
    subjects = {s.id: s for s in store.list_subjects()}
    people = {p.id: p for p in store.list_people()}

    pages = Pages(root, owned)
    pages.claim("", "home", "Home")
    for c in concepts:
        pages.claim("Concepts", str(c["id"]), c["name"])
    for rec in recordings:
        pages.claim("Classes", rec.id, rec.title if rec.title else rec.id)
    for sid in {r.subject_id for r in recordings if r.subject_id}:
        pages.claim("Subjects", str(sid), subjects[sid].name)
    for pid in {r.person_id for r in recordings if r.person_id}:
        pages.claim("People", str(pid), people[pid].name)

    concepts_of: dict[str, list[int]] = defaultdict(list)  # recording -> concepts it mentions
    for cid, page in pages_of.items():
        for group in page["recordings"]:
            concepts_of[group["recording_id"]].append(cid)
    name_of = {c["id"]: c["name"] for c in concepts}
    written: dict[str, str] = {}

    for cid, page in pages_of.items():
        lines = ["---", "tags: [concept]"]
        if page["aliases"]:
            lines.append("aliases: [" + ", ".join(json.dumps(a, ensure_ascii=False) for a in page["aliases"]) + "]")
        lines += ["---", "", f"# {page['name']}", ""]
        if page["definition"]:
            lines += [f"> {page['definition']}", ""]
        lines += ["## Across your classes", ""]
        for group in page["recordings"]:
            where = " · ".join(x for x in (day(group["at"]), group["subject"] or group["person"]) if x)
            lines.append(f"### {pages.link('Classes', group['recording_id'], group['title'])} · {where}")
            for item in group["items"]:
                stamp = f"{format_timestamp(item['start'], group['long_form'])} " if item["start"] is not None else ""
                lead = "★ " if item["kind"] == "stressed" else ""
                lines.append(f"- {stamp}{lead}{item['text']}")
            lines.append("")
        if page["related"]:
            lines += ["## Related", "", ", ".join(pages.link("Concepts", r["id"], r["name"]) for r in page["related"]), ""]
        if page["people"]:
            lines += ["## Came up with", "", ", ".join(pages.link("People", p["id"], p["name"]) for p in page["people"]), ""]
        if page["classes"]:
            lines += ["## Subjects", "", ", ".join(pages.link("Subjects", s["id"], s["name"]) for s in page["classes"]), ""]
        written[pages.paths[("Concepts", str(cid))]] = "\n".join(lines)

    for rec in recordings:
        record = store.get_notes(rec.id)
        notes = record.notes if record else None
        meta = [f"**Date:** {day(rec.recorded_at or rec.created_at)}"]
        if rec.subject_id:
            meta.append(f"**Subject:** {pages.link('Subjects', rec.subject_id)}")
        if rec.person_id:
            meta.append(f"**With:** {pages.link('People', rec.person_id)}")
        if rec.duration_sec:
            meta.append(f"**Length:** {round(rec.duration_sec / 60)} min")
        lines = ["---", "tags: [recording]", "---", "", f"# {rec.title}", "", " · ".join(meta), ""]
        mentioned = sorted(concepts_of.get(rec.id, []), key=lambda cid: name_of[cid].lower())
        if mentioned:
            lines += ["**Concepts:** " + ", ".join(pages.link("Concepts", cid, name_of[cid]) for cid in mentioned), ""]
        if notes is not None and notes.items():
            lines.append(notes_to_md(notes, (rec.duration_sec or 0) >= 3600))
        mine = (store.get_my_notes(rec.id) or "").strip()
        if mine:
            lines += ["## My notes", "", mine, ""]
        written[pages.paths[("Classes", rec.id)]] = "\n".join(lines)

    for sid in {r.subject_id for r in recordings if r.subject_id}:
        subject = subjects[sid]
        own = [r for r in recordings if r.subject_id == sid]
        top = [c for c in brain.list_concepts(subject_id=sid)][:30]
        lines = ["---", "tags: [subject]", "---", "", f"# {subject.name}", ""]
        if subject.description:
            lines += [subject.description, ""]
        lines += ["## Classes", ""] + [f"- {pages.link('Classes', r.id, r.title)} · {day(r.recorded_at or r.created_at)}"
                                       for r in reversed(own)] + [""]
        if top:
            lines += ["## Concepts", "", ", ".join(pages.link("Concepts", c["id"], c["name"]) for c in top), ""]
        written[pages.paths[("Subjects", str(sid))]] = "\n".join(lines)

    for pid in {r.person_id for r in recordings if r.person_id}:
        person = people[pid]
        chats = [r for r in recordings if r.person_id == pid]
        talked = sorted({cid for r in chats for cid in concepts_of.get(r.id, [])}, key=lambda cid: name_of[cid].lower())
        lines = ["---", "tags: [person]", "---", "", f"# {person.name}", ""]
        if person.note:
            lines += [person.note, ""]
        lines += ["## Chats", ""] + [f"- {pages.link('Classes', r.id, r.title)} · {day(r.recorded_at or r.created_at)}"
                                     for r in reversed(chats)] + [""]
        if talked:
            lines += ["## Talked about", "", ", ".join(pages.link("Concepts", cid, name_of[cid]) for cid in talked), ""]
        written[pages.paths[("People", str(pid))]] = "\n".join(lines)

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    home = ["---", "tags: [home]", "---", "", "# Second brain", "",
            f"Written by Transcriber on {stamp}. Exporting again replaces these pages; your own pages in this folder "
            "are never touched.", ""]
    used_subjects = sorted({r.subject_id for r in recordings if r.subject_id}, key=lambda s: subjects[s].name.lower())
    if used_subjects:
        home += ["## Subjects", ""] + [f"- {pages.link('Subjects', s)}" for s in used_subjects] + [""]
    used_people = sorted({r.person_id for r in recordings if r.person_id}, key=lambda p: people[p].name.lower())
    if used_people:
        home += ["## People", ""] + [f"- {pages.link('People', p)}" for p in used_people] + [""]
    if concepts:
        home += ["## Concepts that come up most", ""] + [
            f"- {pages.link('Concepts', c['id'], c['name'])} · {c['recordings']} recording{'s' if c['recordings'] != 1 else ''}"
            for c in concepts[:40]] + [""]
    home += ["## Latest recordings", ""] + [f"- {pages.link('Classes', r.id, r.title)} · {day(r.recorded_at or r.created_at)}"
                                            for r in list(reversed(recordings))[:20]] + [""]
    written[pages.paths[("", "home")]] = "\n".join(home)

    files = sorted(f"{path}.md" for path in written)
    new_hashes = {}
    for path, text in written.items():
        target = root / f"{path}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        body = text.rstrip() + "\n"
        target.write_text(body, encoding="utf-8")
        new_hashes[f"{path}.md"] = digest(body)
    removed = 0
    for old in owned - set(files):  # pages the app wrote last time and no longer needs (only those)
        target = (root / old).resolve()
        if target.is_file() and target.is_relative_to(root.resolve()):
            target.unlink()
            removed += 1
    manifest_path.write_text(json.dumps({"written_at": stamp, "files": files, "hashes": new_hashes}, indent=1), encoding="utf-8")
    return {"folder": str(root), "files": len(files), "removed": removed, "kept_edited": sorted(edited),
            "concepts": len(concepts), "recordings": len(recordings)}
