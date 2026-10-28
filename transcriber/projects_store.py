"""Projects: a named place that ties together recordings, people, concepts and documents, with its own tasks.
Deleting a project removes only the project and its links. It never touches a recording."""
from datetime import UTC, datetime

LINK_KINDS = ("recording", "person", "concept", "document")
STATUSES = ("active", "done")
MAX_NAME = 120
MAX_TEXT = 300


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class ProjectsStoreMixin:
    def create_project(self, name: str, description: str = "") -> int:
        name = " ".join(name.split())[:MAX_NAME]
        if not name:
            raise ValueError("A project needs a name")
        return self._insert("INSERT INTO projects (name, description, status, created_at) VALUES (?, ?, 'active', ?)",
                            (name, description.strip(), _now()))

    def get_project(self, project_id: int) -> dict | None:
        rows = self._query("SELECT * FROM projects WHERE id = ?", (project_id,))
        return dict(rows[0]) if rows else None

    def list_projects(self) -> list[dict]:
        rows = self._query(
            "SELECT p.*, (SELECT COUNT(*) FROM project_links l WHERE l.project_id = p.id) AS links,"
            " (SELECT COUNT(*) FROM project_tasks t WHERE t.project_id = p.id AND t.done = 0) AS open_tasks"
            " FROM projects p ORDER BY (p.status = 'done'), p.id DESC")
        return [dict(r) for r in rows]

    def update_project(self, project_id: int, *, name: str | None = None, description: str | None = None,
                       status: str | None = None) -> bool:
        if self.get_project(project_id) is None:
            return False
        if name is not None:
            name = " ".join(name.split())[:MAX_NAME]
            if not name:
                raise ValueError("A project needs a name")
            self._execute("UPDATE projects SET name = ? WHERE id = ?", (name, project_id))
        if description is not None:
            self._execute("UPDATE projects SET description = ? WHERE id = ?", (description.strip(), project_id))
        if status is not None:
            if status not in STATUSES:
                raise ValueError("A project is active or done")
            self._execute("UPDATE projects SET status = ? WHERE id = ?", (status, project_id))
        return True

    def delete_project(self, project_id: int) -> bool:
        return self._execute("DELETE FROM projects WHERE id = ?", (project_id,)) > 0

    # ---- links

    def add_project_link(self, project_id: int, kind: str, ref: str, label: str = "") -> bool:
        if kind not in LINK_KINDS:
            raise ValueError("A project links recordings, people, concepts or documents")
        ref = str(ref).strip()
        if not ref or self.get_project(project_id) is None:
            raise ValueError("Pick something to link")
        return self._execute("INSERT OR IGNORE INTO project_links (project_id, kind, ref, label) VALUES (?, ?, ?, ?)",
                             (project_id, kind, ref, label.strip()[:MAX_TEXT])) > 0

    def remove_project_link(self, project_id: int, kind: str, ref: str) -> bool:
        return self._execute("DELETE FROM project_links WHERE project_id = ? AND kind = ? AND ref = ?",
                             (project_id, kind, str(ref))) > 0

    def project_links(self, project_id: int) -> list[dict]:
        return [dict(r) for r in self._query("SELECT kind, ref, label FROM project_links WHERE project_id = ? ORDER BY rowid",
                                             (project_id,))]

    # ---- tasks

    def add_project_task(self, project_id: int, text: str) -> int:
        text = " ".join(text.split())[:MAX_TEXT]
        if not text or self.get_project(project_id) is None:
            raise ValueError("Write the task first")
        return self._insert("INSERT INTO project_tasks (project_id, text, done, created_at) VALUES (?, ?, 0, ?)",
                            (project_id, text, _now()))

    def project_tasks(self, project_id: int) -> list[dict]:
        return [dict(r) for r in self._query("SELECT * FROM project_tasks WHERE project_id = ? ORDER BY done, id", (project_id,))]

    def set_project_task(self, task_id: int, done: bool) -> bool:
        return self._execute("UPDATE project_tasks SET done = ? WHERE id = ?", (int(done), task_id)) > 0

    def delete_project_task(self, task_id: int) -> bool:
        return self._execute("DELETE FROM project_tasks WHERE id = ?", (task_id,)) > 0

    def projects_of(self, kind: str, ref: str) -> list[dict]:
        """The projects something belongs to, for showing on a recording's sheet."""
        return [dict(r) for r in self._query(
            "SELECT p.id, p.name FROM projects p JOIN project_links l ON l.project_id = p.id"
            " WHERE l.kind = ? AND l.ref = ? ORDER BY p.name", (kind, str(ref)))]
