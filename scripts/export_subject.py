"""Export a subject into study files (HTML plus Markdown) in exports/:

    python scripts/export_subject.py "Machine Learning"

- "<Subject> - every class": class by class, all the notes first (lecture notes by topic, key terms, to-dos),
  then the full transcripts folded underneath (Ctrl+F still searches them).
- "<Subject> - exam sheet": one page with everything the lecturer stressed, every definition, the facts and
  numbers, and what you were told to do, across all classes.
A second copy of a class (the laptop recording of a class also recorded on the phone) is skipped."""
import html
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcriber.export import format_timestamp, lecture_text  # noqa: E402
from transcriber.notes.model import Notes  # noqa: E402
from transcriber.postprocess import named_paragraphs  # noqa: E402
from transcriber.store import Store  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "exports"

CSS = """
:root { color-scheme: light dark; --ink: #1d2430; --ink2: #4a5260; --rule: #dbe3ee; --blue: #1f4fa3; --bg: #fdfdfb; --red: #b3332b; }
@media (prefers-color-scheme: dark) { :root { --ink: #e7ebf2; --ink2: #aab3c2; --rule: #263248; --blue: #93b6ff; --bg: #121925; --red: #ff8f84; } }
body { margin: 0; background: var(--bg); color: var(--ink); font: 17px/1.65 Georgia, "Iowan Old Style", serif; }
main { max-width: 52rem; margin: 0 auto; padding: 2rem 1.25rem 5rem; }
h1 { font-size: 2rem; margin: 0 0 .3rem; } h2 { margin: 3rem 0 .2rem; padding-top: 1rem; border-top: 2px solid var(--rule); }
h3 { margin: 1.4rem 0 .3rem; font-size: 1.08rem; color: var(--blue); }
h4 { margin: 1.2rem 0 .2rem; font: 600 .78rem/1.4 system-ui, sans-serif; letter-spacing: .08em; text-transform: uppercase; color: var(--blue); }
.meta, .ts, .from { font: 13px/1.5 system-ui, sans-serif; color: var(--ink2); } .ts { font-variant-numeric: tabular-nums; margin-right: .5rem; }
.part { margin-top: 1.4rem; font: 600 14px/1.5 system-ui, sans-serif; color: var(--ink2); border-bottom: 1px solid var(--rule); }
nav { font: 15px/1.7 system-ui, sans-serif; padding: 1rem 1.2rem; border: 1px solid var(--rule); border-radius: 8px; }
a { color: var(--blue); } ul { padding-left: 1.3rem; } li { margin: .2rem 0; }
.para { margin: .55rem 0; } .who { font-weight: 600; } .star { color: var(--red); }
details.transcript { margin-top: 1.4rem; padding: .6rem 1rem; border: 1px solid var(--rule); border-radius: 8px; }
details.transcript > summary { cursor: pointer; font: 600 15px/1.6 system-ui, sans-serif; color: var(--blue); }
@media print { details.transcript { display: none; } }
"""


def when(rec) -> datetime:
    return datetime.fromisoformat(rec.recorded_at or rec.created_at).astimezone()


def page(title: str, subtitle: str, body: str) -> str:
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,"
            f"initial-scale=1'><title>{html.escape(title)}</title><style>{CSS}</style></head><body><main>"
            f"<h1>{html.escape(title)}</h1><p class='meta'>{subtitle}</p>{body}</main></body></html>")


def stamp(cites: list[float], long_form: bool) -> str:
    return f'<span class="ts">{format_timestamp(cites[0], long_form)}</span>' if cites else ""


def notes_html(notes: Notes, long_form: bool) -> str:
    out = []
    if notes.summary:
        out.append("<h4>At a glance</h4><ul>" + "".join(f"<li>{html.escape(i.text)}</li>" for i in notes.summary) + "</ul>")
    if notes.lecture:
        out.append("<h4>Lecture notes</h4>")
        heading, items = None, []
        for item in notes.lecture:
            if item.heading and item.heading.lower() != (heading or "").lower():
                if items:
                    out.append("<ul>" + "".join(items) + "</ul>")
                    items = []
                heading = item.heading
                out.append(f"<h3>{html.escape(heading[0].upper() + heading[1:])}</h3>")
            if item.kind == "stressed":
                text = f'<span class="star">★</span> {html.escape(item.text)}'
            elif item.kind == "definition" and item.term:
                text = f"<strong>{html.escape(item.term)}</strong>: {html.escape(item.text)}"
            else:
                text = html.escape(lecture_text(item))
            items.append(f"<li>{stamp(item.cites, long_form)}{text}</li>")
        if items:
            out.append("<ul>" + "".join(items) + "</ul>")
    for key, title in (("action_items", "To do"), ("open_questions", "Open questions")):
        items = getattr(notes, key)
        if items:
            out.append(f"<h4>{title}</h4><ul>" + "".join(
                f"<li>{stamp(i.cites, long_form)}{html.escape(i.text)}"
                + (f' <span class="meta">due {html.escape(i.due)}</span>' if i.due else "") + "</li>" for i in items) + "</ul>")
    return "\n".join(out)


def class_days(store: Store, subject_id: int) -> dict:
    days = defaultdict(list)
    for rec in sorted(store.list_recordings(subject_id=subject_id), key=when):
        if rec.original_filename == "Practice session" or "laptop version" in rec.title.lower():
            continue
        days[when(rec).date()].append(rec)
    return days


def ready_notes(store: Store, rec) -> Notes | None:
    record = store.get_notes(rec.id)
    return record.notes if record and record.status == "ready" and record.notes and record.notes.items() else None


def every_class(store: Store, subject, days: dict) -> tuple[str, str]:
    body, toc, md = [], [], [f"# {subject.name}: every class\n"]
    for day, recs in days.items():
        anchor = f"d{day.isoformat()}"
        label = day.strftime("%A %d %B %Y")
        minutes = round(sum((r.duration_sec or 0) for r in recs) / 60)
        toc.append(f'<li><a href="#{anchor}">{label}</a> <span class="meta">· {minutes} min</span></li>')
        body.append(f'<h2 id="{anchor}">{label}</h2>')
        md.append(f"\n## {label}\n")
        transcripts = []
        for n, rec in enumerate(recs, 1):
            long_form = (rec.duration_sec or 0) >= 3600
            line = (f"Part {n} · " if len(recs) > 1 else "") + \
                f"{rec.title} · recorded {when(rec):%H:%M} · {round((rec.duration_sec or 0) / 60)} min"
            body.append(f'<p class="part">{html.escape(line)}</p>')
            md.append(f"\n### {line}\n")
            notes = ready_notes(store, rec)
            if notes:
                body.append(notes_html(notes, long_form))
                md += [f"- {i.text}" for i in notes.summary]
                md += [f"- {lecture_text(i)}" for i in notes.lecture]
            else:
                body.append('<p class="meta">Notes are still being written for this one.</p>')
            transcript = store.get_transcript(rec.id)
            if transcript:
                transcripts.append(f"<h4>{html.escape(line)}</h4>")
                md.append(f"\n**Transcript: {line}**\n")
                for p in named_paragraphs(transcript.segments, store.get_speaker_names(rec.id)):
                    who = f'<span class="who">{html.escape(p.speaker)}:</span> ' if p.speaker else ""
                    transcripts.append(f'<p class="para"><span class="ts">{format_timestamp(p.start, long_form)}</span>'
                                       f"{who}{html.escape(p.text)}</p>")
                    md.append(f"{format_timestamp(p.start, long_form)} {p.text}\n")
        if transcripts:
            body.append('<details class="transcript"><summary>Full transcript of this class (click to open; Ctrl+F '
                        'searches it too)</summary>' + "".join(transcripts) + "</details>")
    intro = (f"<nav><strong>Classes</strong><ol>{''.join(toc)}</ol>"
             f"<p class='meta'>Short on time? Start with <a href='{html.escape(subject.name)} - exam sheet.html'>the exam "
             f"sheet</a>.</p></nav>")
    return intro + "\n".join(body), "\n".join(md)


def exam_sheet(store: Store, subject, days: dict) -> tuple[str, str]:
    stressed, terms, facts, todo = [], {}, [], []
    for day, recs in days.items():
        for rec in recs:
            notes = ready_notes(store, rec)
            if not notes:
                continue
            long_form = (rec.duration_sec or 0) >= 3600
            source = f'<span class="from">{day:%a %d %b}</span>'
            for item in notes.lecture:
                where = f'<span class="from">{day:%a %d %b}' + (
                    f" · {format_timestamp(item.cites[0], long_form)}" if item.cites else "") + "</span>"
                if item.kind == "stressed":
                    stressed.append((item.text, where))
                elif item.kind == "definition" and item.term:
                    terms.setdefault(item.term.strip().lower(), (item.term.strip(), item.text, where))
                elif item.kind == "fact":
                    facts.append((item.text, where))
            for item in notes.action_items:
                todo.append((item.text + (f" (due {item.due})" if item.due else ""), source))
    def block(title: str, rows: list[tuple[str, str]], note: str = "") -> str:
        if not rows:
            return ""
        return (f"<h2>{title}</h2>{note}<ul>" + "".join(f"<li>{html.escape(text)} {where}</li>" for text, where in rows)
                + "</ul>")
    term_rows = sorted(terms.values(), key=lambda t: t[0].lower())
    body = (block("★ What the lecturer stressed", stressed,
                  "<p class='meta'>Things repeated or flagged as important. Most likely to come up.</p>")
            + (("<h2>Key terms</h2><ul>" + "".join(
                f"<li><strong>{html.escape(term)}</strong>: {html.escape(text)} {where}</li>"
                for term, text, where in term_rows) + "</ul>") if term_rows else "")
            + block("Facts and numbers", facts)
            + block("What you were told to do", todo)
            + f"<p class='meta'>For the full explanation of any point, find it by date in "
              f"<a href='{html.escape(subject.name)} - every class.html'>every class</a>.</p>")
    md = [f"# {subject.name}: exam sheet\n", "## What the lecturer stressed"] + [f"- {t}" for t, _ in stressed]
    md += ["", "## Key terms"] + [f"- **{term}**: {text}" for term, text, _ in term_rows]
    md += ["", "## Facts and numbers"] + [f"- {t}" for t, _ in facts]
    md += ["", "## What you were told to do"] + [f"- {t}" for t, _ in todo]
    return body, "\n".join(md)


def main(subject_name: str) -> None:
    store = Store(ROOT / "data" / "app.db")
    try:
        subject = next((s for s in store.list_subjects() if s.name.lower() == subject_name.lower()), None)
        if subject is None:
            sys.exit(f"No subject called {subject_name!r}")
        days = class_days(store, subject.id)
        OUT.mkdir(exist_ok=True)
        made = datetime.now().strftime("%d %b %Y, %H:%M")
        body, md = every_class(store, subject, days)
        name = f"{subject.name} - every class"
        (OUT / f"{name}.html").write_text(page(f"{subject.name}: every class",
                                               f"{len(days)} class days · made {made} · press Ctrl+F to search", body),
                                          encoding="utf-8")
        (OUT / f"{name}.md").write_text(md, encoding="utf-8")
        body, md = exam_sheet(store, subject, days)
        name = f"{subject.name} - exam sheet"
        (OUT / f"{name}.html").write_text(page(f"{subject.name}: exam sheet",
                                               f"From {len(days)} class days · made {made}", body), encoding="utf-8")
        (OUT / f"{name}.md").write_text(md, encoding="utf-8")
        print(OUT)
    finally:
        store.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "Machine Learning")
