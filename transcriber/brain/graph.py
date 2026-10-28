"""The brain's map: concepts (sized by how many recordings mention them), the subjects and people they came up
with, and talks with no subject, joined by the mentions. The page lays it out; this only picks what to draw."""
from collections import defaultdict

from . import quality

MAX_CONCEPTS = 60


def graph(brain, subject_id: int | None = None, since: str | None = None, limit: int = MAX_CONCEPTS) -> dict:
    sql = ("SELECT m.concept_id, c.name, c.visibility, m.recording_id, r.title, r.subject_id, s.name AS subject, r.person_id,"
           " p.name AS person FROM concept_mentions m JOIN concepts c ON c.id = m.concept_id"
           " JOIN recordings r ON r.id = m.recording_id AND r.deleted_at IS NULL"
           " LEFT JOIN subjects s ON s.id = r.subject_id LEFT JOIN people p ON p.id = r.person_id")
    where, params = [], []
    if subject_id is not None:
        where.append("r.subject_id = ?")
        params.append(subject_id)
    if since:
        where.append("COALESCE(r.recorded_at, r.created_at) >= ?")
        params.append(since)
    if where:
        sql += " WHERE " + " AND ".join(where)
    rows = brain.store.query(sql, tuple(params))

    recordings_of: dict[int, set[str]] = defaultdict(set)
    mentions_of: dict[int, int] = defaultdict(int)
    names, hosts_of_recording = {}, {}
    for row in rows:
        if quality.hidden(row["name"], row["visibility"]):  # generic headings would crowd the middle of the map
            continue
        cid = row["concept_id"]
        names[cid] = row["name"]
        recordings_of[cid].add(row["recording_id"])
        mentions_of[cid] += 1
        hosts = []
        if row["subject_id"]:
            hosts.append((f"s{row['subject_id']}", "class", row["subject"], row["subject_id"]))
        if row["person_id"]:
            hosts.append((f"p{row['person_id']}", "person", row["person"], row["person_id"]))
        if not hosts:  # a talk or recording with no subject stands for itself
            hosts.append((f"r{row['recording_id']}", "recording", row["title"], row["recording_id"]))
        hosts_of_recording[row["recording_id"]] = hosts

    ranked = sorted(names, key=lambda cid: (-len(recordings_of[cid]), -mentions_of[cid], names[cid].lower()))
    shown = ranked[:limit]
    nodes, edges, host_nodes = [], defaultdict(int), {}
    for cid in shown:
        nodes.append({"id": f"c{cid}", "type": "concept", "ref": cid, "label": names[cid],
                      "weight": len(recordings_of[cid]), "mentions": mentions_of[cid]})
        for rid in recordings_of[cid]:
            for node_id, kind, label, ref in hosts_of_recording[rid]:
                host = host_nodes.setdefault(node_id, {"id": node_id, "type": kind, "ref": ref, "label": label,
                                                       "recordings": set()})
                host["recordings"].add(rid)
                edges[(f"c{cid}", node_id)] += 1
    for host in host_nodes.values():
        host["weight"] = len(host.pop("recordings"))
        nodes.append(host)
    return {"nodes": nodes, "edges": [{"source": a, "target": b, "weight": w} for (a, b), w in edges.items()],
            "total_concepts": len(names), "shown_concepts": len(shown)}
