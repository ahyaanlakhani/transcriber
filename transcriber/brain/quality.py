"""Which concepts are worth a page. Lecture notes produce headings like "Process", "Step" or "Assignments" alongside
real ideas like "Business model canvas". Nothing is deleted: every name stays indexed and searchable; generic words and
course admin are only kept out of the concept list, the map and the export until you choose to show them.

Plain rules, no model: they cost nothing, send nothing anywhere, and give the same answer every time. Your own choice
for a concept (show it, or hide it) always wins over the rules."""
from .names import key_of

CONCEPT, GENERIC, ADMIN = "concept", "generic", "admin"
LABELS = {CONCEPT: "Concept", GENERIC: "Generic heading", ADMIN: "Course admin"}

# A single word this broad says nothing on its own ("Process", "Step").
GENERIC_WORDS = {
    "analysis", "approach", "aspect", "aspects", "basics", "change", "choice", "choices", "communication", "concept",
    "concepts", "conclusion", "context", "data", "definition", "department", "design", "discussion", "example",
    "examples", "factor", "factors", "focus", "goal", "goals", "growth", "idea", "ideas", "impact", "importance",
    "information", "introduction", "issue", "issues", "level", "management", "measurement", "method", "methods",
    "model", "notes", "overview", "part", "phase", "problem", "problems", "process", "processes", "question",
    "questions", "recap", "review", "rule", "rules", "solution", "solutions", "stage", "step", "steps", "structure",
    "summary", "system", "systems", "technology", "technologies", "thing", "things", "tip", "tips", "topic", "topics",
    "type", "types", "use", "uses", "value", "work", "appearance", "meeting", "meetings",
}
# Words that mark course logistics rather than something taught.
ADMIN_WORDS = {
    "assignment", "assignments", "attendance", "deadline", "deadlines", "exam", "exams", "grade", "grades", "grading",
    "homework", "midterm", "midterms", "quiz", "quizzes", "submission", "submissions", "syllabus", "marks",
}
ADMIN_PHRASES = ("course expectations", "course outline", "group activity", "class activity", "office hours",
                 "exam logistics")


def classify(name: str) -> str:
    key = key_of(name) or name.lower()
    words = key.split()
    plain = " ".join(name.lower().split())  # the normalized key makes words singular; phrases are matched as written
    if any(phrase in plain or phrase in key for phrase in ADMIN_PHRASES) or (words and len(words) <= 2 and words[-1] in ADMIN_WORDS):  # "Midterm exam", not "Assignment problem"
        return ADMIN
    if len(words) == 1 and words[0] in GENERIC_WORDS:
        return GENERIC
    return CONCEPT


def hidden(name: str, visibility: str | None) -> bool:
    """visibility is your own choice ('shown' or 'hidden'); None leaves it to the rules."""
    if visibility == "shown":
        return False
    if visibility == "hidden":
        return True
    return classify(name) != CONCEPT
