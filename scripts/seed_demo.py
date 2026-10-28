"""Fill a folder with a made-up library, so the app can be tried (and photographed) without anyone's real recordings.

Everything here is invented: a machine learning course, a technical writing class, a startup seminar and a team
meeting, with transcripts, study notes, topics, a concept map and flashcards. It needs no API keys and no audio.

Usage:
    python scripts/seed_demo.py              # writes ./demo-data (an empty folder, or one this script made before)
    python scripts/seed_demo.py some/folder  # or anywhere else
    python scripts/demo_server.py            # then open http://127.0.0.1:8001

The dates are worked out from today, so the calendar and the Today page always look current.
"""
import os
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from transcriber import learn  # noqa: E402
from transcriber.brain.index import Brain  # noqa: E402
from transcriber.notes.model import SECTIONS, notes_from_writer  # noqa: E402
from transcriber.providers.base import Segment  # noqa: E402
from transcriber.store import Store  # noqa: E402

TZ = os.environ.get("DEMO_TZ", "UTC")
WRITER, CHECKER = "groq:openai/gpt-oss-120b", "gemini:gemini-3.8-flash"


def clock(seconds: float) -> str:
    return f"[{int(seconds) // 60:02d}:{int(seconds) % 60:02d}]"


def point(text, at):
    return {"text": text, "cites": [clock(at)]}


def item(heading, text, at, kind="point", term=None):
    return {"heading": heading, "kind": kind, "term": term, "text": text, "cites": [clock(at)]}


# ---- the made-up lectures: (title, minutes, transcript [(start second, words)], notes)

ML = [
    {
        "title": "Supervised vs unsupervised learning", "minutes": 52,
        "transcript": [
            (4, "Welcome back everyone. Today we start from the question every machine learning project begins with: what kind of data do we actually have?"),
            (41, "If every example comes with the right answer attached, we call it labelled data, and learning from it is supervised learning. A photo of a cat tagged as cat is the classic case."),
            (118, "Supervised problems come in two flavours. Classification predicts a category, like spam or not spam. Regression predicts a number, like the price of a flat."),
            (205, "Now suppose nobody labelled anything. You only have the raw data and you ask the algorithm to find structure by itself. That is unsupervised learning."),
            (290, "Clustering is the standard example. A shop groups its customers by buying habits without anyone telling it what the groups should be."),
            (372, "There is a middle ground called semi-supervised learning, where you label a small part of the data and let the algorithm spread those labels to the rest."),
            (455, "A quick word on the training set and the test set. We always keep some data hidden from the model, because the only honest measure of learning is how it does on examples it has never seen."),
            (560, "For the exam, be able to tell me which kind of learning fits a described problem, and be ready to defend your choice in two sentences."),
            (640, "Homework this week: pick a dataset from the course folder and decide whether it is a classification, regression or clustering task."),
        ],
        "notes": {
            "summary": [point("Machine learning problems differ mainly by whether the data carries the right answers.", 41),
                        point("Supervised learning predicts labels; unsupervised learning finds structure; always hold out unseen data to test.", 455)],
            "lecture": [
                item("Kinds of learning", "Learning from examples that come with the correct answer.", 41, "definition", "Supervised learning"),
                item("Kinds of learning", "Finding structure in data that has no labels.", 205, "definition", "Unsupervised learning"),
                item("Kinds of learning", "A small labelled part of the data is used to label the rest.", 372, "definition", "Semi-supervised learning"),
                item("Supervised problems", "Classification predicts a category; regression predicts a number.", 118),
                item("Supervised problems", "Spam filtering is classification; predicting flat prices is regression.", 118, "example"),
                item("Unsupervised problems", "Clustering groups similar items without being told the groups.", 290, "definition", "Clustering"),
                item("Unsupervised problems", "A shop grouping customers by buying habits is clustering.", 290, "example"),
                item("Training and testing", "Keep data hidden from the model: the test set is the honest measure of learning.", 455, "stressed"),
                item("Training and testing", "Be able to say which kind of learning fits a described problem, in two sentences.", 560, "stressed"),
            ],
            "action_items": [{"text": "Classify one course dataset as classification, regression or clustering.", "owner": None, "due": "next lecture", "cites": [clock(640)]}],
            "open_questions": [],
            "topics": ["supervised learning", "unsupervised learning", "training and test sets"],
        },
    },
    {
        "title": "Gradient descent and the learning rate", "minutes": 58,
        "transcript": [
            (6, "Last time we said a model learns by reducing a loss function, a single number that says how wrong its predictions are. Today: how it actually reduces that number."),
            (63, "Picture the loss as a hilly landscape. Every setting of the model's parameters is a point on it, and we want to walk downhill to the lowest valley."),
            (140, "Gradient descent does exactly that. At each step it measures the slope under its feet, the gradient, and moves a little in the opposite direction."),
            (232, "How big is a little? That is the learning rate. It is the most important number you will tune in this course."),
            (318, "Too large and you overshoot the valley and bounce around, or even blow up. Too small and training crawls and may stall on a plateau."),
            (410, "In practice we do not use the whole dataset for each step. Stochastic gradient descent uses one example, and mini-batch gradient descent uses a small handful, which is faster and noisier."),
            (515, "That noise is not all bad. It can shake the model out of shallow dips so it finds a better valley."),
            (601, "I will say this once clearly, because it will be on the exam: the learning rate controls the step size, and the gradient controls the direction."),
            (690, "Please plot the loss against training steps for three different learning rates in the lab, and bring the picture to class."),
        ],
        "notes": {
            "summary": [point("Models learn by repeatedly stepping downhill on the loss, with the learning rate setting the step size.", 140)],
            "lecture": [
                item("Loss", "One number that says how wrong the model's predictions are.", 6, "definition", "Loss function"),
                item("Gradient descent", "Repeatedly measure the slope of the loss and step the opposite way.", 140, "definition", "Gradient descent"),
                item("Gradient descent", "Imagine walking downhill through a landscape of parameter settings.", 63, "example"),
                item("Learning rate", "The size of each step; the most important setting to tune.", 232, "definition", "Learning rate"),
                item("Learning rate", "Too large overshoots and bounces; too small crawls or stalls on a plateau.", 318),
                item("Variants", "Stochastic gradient descent updates from one example; mini-batch uses a small handful.", 410, "definition", "Mini-batch gradient descent"),
                item("Variants", "The noise of small batches can shake the model out of shallow dips.", 515),
                item("Step size and direction", "The learning rate controls the step size; the gradient controls the direction.", 601, "stressed"),
            ],
            "action_items": [{"text": "Lab: plot loss against steps for three learning rates and bring the picture.", "owner": None, "due": "next class", "cites": [clock(690)]}],
            "open_questions": [point("How do we pick a learning rate before training starts?", 232)],
            "topics": ["gradient descent", "learning rate", "loss function"],
        },
    },
    {
        "title": "Overfitting, regularization and bias vs variance", "minutes": 61,
        "transcript": [
            (5, "Here is the most common way a model fails: it learns the training data too well. We call that overfitting."),
            (74, "An overfitted model has memorised the noise and the quirks of the examples it saw, so it looks brilliant on the training set and falls apart on new data."),
            (160, "The opposite failure is underfitting. The model is too simple to capture the pattern at all, so it is poor on both the training set and the test set."),
            (251, "These two failures are the bias and variance story. High bias means too simple and consistently wrong. High variance means too sensitive to the particular data it was shown."),
            (352, "You cannot minimise both for free. Making a model more flexible lowers bias but raises variance. That tension is the bias-variance trade-off."),
            (455, "So how do we fight overfitting? First, more data. Second, a simpler model. Third, regularization."),
            (540, "Regularization adds a penalty to the loss for large parameter values, which nudges the model towards simpler solutions. L2 regularization penalises the squared size of the weights."),
            (640, "We also use a validation set, separate from the test set, to choose settings like the regularization strength without peeking at the test data."),
            (730, "Remember: the test set is touched once, at the very end. If you tune against it, your final number is no longer honest."),
        ],
        "notes": {
            "summary": [point("Overfitting means memorising the training data; regularization and a validation set help.", 74),
                        point("Flexibility trades bias against variance, so the goal is the right amount, not the most.", 352)],
            "lecture": [
                item("Overfitting", "The model has memorised noise in the training data and fails on new data.", 74, "definition", "Overfitting"),
                item("Overfitting", "Looks brilliant on the training set, poor on the test set.", 74),
                item("Underfitting", "The model is too simple to capture the pattern, so it is poor everywhere.", 160, "definition", "Underfitting"),
                item("Bias and variance", "High bias is too simple and consistently wrong; high variance is too sensitive to the sample.", 251),
                item("Bias and variance", "More flexibility lowers bias but raises variance: the bias-variance trade-off.", 352, "stressed"),
                item("Fighting overfitting", "More data, a simpler model, or regularization.", 455),
                item("Regularization", "A penalty on large parameter values that pushes towards simpler solutions.", 540, "definition", "Regularization"),
                item("Regularization", "L2 regularization penalises the squared size of the weights.", 540),
                item("Validation", "A validation set picks settings; the test set is touched once at the very end.", 640, "stressed"),
            ],
            "action_items": [{"text": "Fit a polynomial of degree 1, 3 and 9 and compare training and test error.", "owner": None, "due": "Wednesday", "cites": [clock(730)]}],
            "open_questions": [point("How is the strength of the penalty chosen in practice?", 640)],
            "topics": ["overfitting", "regularization", "bias-variance trade-off", "training and test sets"],
        },
    },
    {
        "title": "Neural networks and backpropagation", "minutes": 56,
        "transcript": [
            (7, "A neural network is just a pile of very simple functions stacked in layers. Each unit takes a weighted sum of its inputs and passes it through an activation function."),
            (92, "Without an activation function the whole stack would collapse into one linear function. The non-linearity is what lets the network bend to fit complicated patterns."),
            (184, "Common activations are the sigmoid, the hyperbolic tangent, and the rectified linear unit, ReLU, which simply outputs zero for negatives and passes positives through."),
            (277, "Training is gradient descent again. The question is how to get the gradient of the loss with respect to every weight, even in the first layer, far from the output."),
            (371, "Backpropagation answers that. It runs the network forward to get a prediction and the loss, then applies the chain rule backwards, layer by layer, to get every gradient."),
            (478, "The key insight is reuse. The gradient for an early layer is built from the gradient of the later layer, so nothing is computed twice."),
            (566, "Deep networks bring a new problem. Gradients can shrink as they travel backwards, the vanishing gradient problem. ReLU and careful initialisation help a lot."),
            (660, "And everything we said about overfitting applies even more strongly here, because big networks have enormous capacity to memorise."),
        ],
        "notes": {
            "summary": [point("A neural network is stacked weighted sums with non-linear activations, trained by gradient descent via backpropagation.", 371)],
            "lecture": [
                item("Structure", "Layers of units, each a weighted sum passed through an activation function.", 7, "definition", "Neural network"),
                item("Activations", "Non-linearity lets the network fit complicated patterns; without it the stack is one linear function.", 92, "stressed"),
                item("Activations", "ReLU outputs zero for negative inputs and passes positive ones through.", 184, "definition", "ReLU"),
                item("Training", "Training is gradient descent; the question is getting the gradient for every weight.", 277),
                item("Backpropagation", "Run forward for the loss, then apply the chain rule backwards layer by layer.", 371, "definition", "Backpropagation"),
                item("Backpropagation", "Gradients of early layers reuse those of later layers, so nothing is computed twice.", 478),
                item("Deep networks", "Gradients can shrink as they travel backwards: the vanishing gradient problem.", 566, "definition", "Vanishing gradient"),
                item("Deep networks", "Big networks overfit easily, so regularization matters even more.", 660),
            ],
            "action_items": [{"text": "Implement a two-layer network on the practice dataset.", "owner": None, "due": "Monday", "cites": [clock(660)]}],
            "open_questions": [],
            "topics": ["neural networks", "backpropagation", "overfitting", "gradient descent"],
        },
    },
    {
        "title": "Evaluating models: precision, recall and the confusion matrix", "minutes": 49,
        "transcript": [
            (5, "Accuracy is the first number people reach for, and it is often the wrong one. Imagine a disease that affects one patient in a hundred."),
            (68, "A model that always says healthy is ninety-nine percent accurate and completely useless. We need measures that look at the kinds of mistakes."),
            (150, "The confusion matrix lays them out: true positives, false positives, true negatives and false negatives. Everything else is built from those four counts."),
            (244, "Precision asks: of everything the model flagged as positive, how much really was? Recall asks: of everything that really was positive, how much did the model find?"),
            (338, "They pull against each other. Flag more cases and recall rises while precision falls. Which one matters depends on the cost of each mistake."),
            (430, "A missed cancer is far worse than a false alarm, so there we favour recall. A spam filter that deletes real email is worse than letting some spam through, so there we favour precision."),
            (520, "The F1 score combines the two into one number, the harmonic mean, which stays low unless both precision and recall are decent."),
            (610, "For the exam: given a small confusion matrix, compute precision, recall and F1 by hand, and explain in a sentence which one the situation needs."),
        ],
        "notes": {
            "summary": [point("Accuracy misleads when classes are unbalanced; precision, recall and F1 describe the kinds of mistakes.", 68)],
            "lecture": [
                item("Why not accuracy", "With rare positives, a model that always says negative is very accurate and useless.", 68, "example"),
                item("Confusion matrix", "Counts of true and false positives and negatives; all other measures come from them.", 150, "definition", "Confusion matrix"),
                item("Precision and recall", "Precision: of the flagged cases, how many were truly positive.", 244, "definition", "Precision"),
                item("Precision and recall", "Recall: of the truly positive cases, how many were found.", 244, "definition", "Recall"),
                item("Precision and recall", "They trade off against each other; the cost of each mistake decides which to favour.", 338),
                item("Choosing", "Missed cancer is worse than a false alarm, so favour recall; deleting real email is worse than spam, so favour precision.", 430, "example"),
                item("F1 score", "The harmonic mean of precision and recall; low unless both are decent.", 520, "definition", "F1 score"),
                item("Computing by hand", "Compute precision, recall and F1 by hand from a small confusion matrix and say which one the situation needs.", 610, "stressed"),
            ],
            "action_items": [{"text": "Practise computing F1 from three confusion matrices.", "owner": None, "due": "before the midterm", "cites": [clock(610)]}],
            "open_questions": [point("What is a good way to choose a threshold when precision and recall conflict?", 338)],
            "topics": ["evaluation", "precision and recall", "confusion matrix"],
        },
    },
]

OTHER = [
    {
        "subject": "Technical Writing", "title": "Writing a clear technical abstract", "minutes": 34, "style": "lecture",
        "transcript": [
            (5, "An abstract is a promise. In under two hundred words it tells a busy reader what you did, why it matters and what they will learn."),
            (80, "Start with the problem in one sentence. Then the method, then the result with a number if you have one, then why it matters."),
            (165, "Avoid vague verbs. Replace we looked at with we measured, and replace improved things with reduced latency by thirty percent."),
            (250, "Write the abstract last. You cannot summarise a paper that does not exist yet."),
            (330, "Read it aloud. If you run out of breath in the middle of a sentence, the sentence is too long."),
        ],
        "notes": {
            "summary": [point("A good abstract states the problem, method, result and significance in under 200 words.", 80)],
            "lecture": [
                item("Structure", "Problem, method, result with a number, then why it matters.", 80, "stressed"),
                item("Style", "Prefer concrete verbs: measured, reduced, compared.", 165),
                item("Process", "Write the abstract last; read it aloud to find sentences that are too long.", 250),
            ],
            "action_items": [{"text": "Draft a 150-word abstract for your project report.", "owner": None, "due": "Friday", "cites": [clock(330)]}],
            "open_questions": [], "topics": ["abstracts", "clear writing"],
        },
    },
    {
        "subject": "Startup Seminar", "title": "Customer interviews: finding the real problem", "minutes": 47, "style": "lecture",
        "transcript": [
            (6, "Founders fall in love with their solution. Customer interviews exist to pull you back to the problem."),
            (95, "Ask about the past, not the future. What did you do the last time this happened is a real answer. Would you use this is a polite lie."),
            (190, "Never pitch during an interview. The moment you describe your idea, people start being nice instead of honest."),
            (284, "Listen for the workaround. If someone has built a spreadsheet to cope, you have found a real problem."),
            (372, "Aim for ten interviews before you write any code. Patterns show up around the seventh."),
        ],
        "notes": {
            "summary": [point("Interviews should uncover the real problem by asking about past behaviour, never by pitching.", 95)],
            "lecture": [
                item("Asking", "Ask about the past, not the future: what did you do the last time it happened.", 95, "stressed"),
                item("Asking", "Never pitch during an interview; people turn polite instead of honest.", 190),
                item("Signals", "A homemade workaround, like a spreadsheet, signals a real problem.", 284, "definition", "Workaround"),
                item("Quantity", "Do about ten interviews before writing code; patterns appear around the seventh.", 372),
            ],
            "action_items": [{"text": "Book three customer interviews this week.", "owner": None, "due": "Sunday", "cites": [clock(372)]}],
            "open_questions": [], "topics": ["customer interviews", "problem discovery"],
        },
    },
]

MEETING = {
    "subject": "Team project", "title": "Weekly project sync", "minutes": 18, "style": "meeting",
    "transcript": [
        (3, "Okay, quick status round. Maya, how is the data pipeline?"),
        (22, "The loader works on the full dataset now. The remaining problem is that two columns arrive with different date formats."),
        (51, "Good. Then let's decide: we freeze the feature list today and only fix bugs from here to the demo."),
        (84, "Agreed. Jonas, can you own the evaluation notebook? We need precision and recall for the report."),
        (110, "Yes, I will have the first version by Thursday. One open question is whether we compare against the baseline from the course."),
        (146, "Let's ask the teaching assistant on Wednesday. Maya, please send the date-format fix before that."),
    ],
    "notes": {
        "summary": [point("The pipeline loads the full dataset; two date formats remain to fix.", 22),
                    point("The feature list is frozen until the demo.", 51)],
        "decisions": [point("Freeze the feature list today; only bug fixes until the demo.", 51)],
        "action_items": [
            {"text": "Own the evaluation notebook with precision and recall.", "owner": "Jonas", "due": "Thursday", "cites": [clock(84)]},
            {"text": "Send the date-format fix.", "owner": "Maya", "due": "Wednesday", "cites": [clock(146)]},
        ],
        "open_questions": [point("Do we compare against the course baseline?", 110)],
        "topics": ["project planning", "evaluation"],
    },
}


def mark_checked(notes) -> None:
    """The made-up checker 'agrees' with every point, as the real one does for well-supported notes."""
    for section in SECTIONS:
        for entry in getattr(notes, section, None) or []:
            entry.verdict = "supported"


def last_days(weekdays: set[int], count: int, today: date) -> list[date]:
    days, day = [], today - timedelta(days=1)
    while len(days) < count:
        if day.weekday() in weekdays:
            days.append(day)
        day -= timedelta(days=1)
    return list(reversed(days))


def start_of(day: date, hour: int) -> str:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=ZoneInfo(TZ)).astimezone(ZoneInfo("UTC")).isoformat()


def add(store: Store, number: int, subject_id: int, entry: dict, day: date, hour: int, style: str) -> None:
    rid = f"demo{number:02d}"
    store.create_recording(rid, entry["title"], subject_id, "en", f"{entry['title']}.m4a")
    segments = [Segment(start, start + 8.0, words) for start, words in entry["transcript"]]
    store.save_transcript(rid, " ".join(words for _, words in entry["transcript"]), segments, "groq/whisper-large-v3")
    store.update_recording(rid, status="done", recorded_at=start_of(day, hour), duration_sec=entry["minutes"] * 60.0,
                           notes_style=style)
    store.queue_notes(rid)
    notes = notes_from_writer(entry["notes"])
    mark_checked(notes)
    store.save_notes(rid, notes, writer=WRITER, checker=CHECKER, check_detail=None)
    store.suggest_topics(rid, notes.topics)
    for topic in store.recording_topics(rid):
        store.accept_topic(rid, topic["id"])


def main(target: Path) -> None:
    marker = target / ".demo-library"
    if target.exists() and any(target.iterdir()):
        if not marker.exists():  # never wipe a folder this script didn't make
            sys.exit(f"{target} already has files in it that this script did not create. Pick an empty folder.")
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    marker.write_text("Made by scripts/seed_demo.py; it is safe to delete and re-create.\n", encoding="utf-8")
    store = Store(target / "app.db")
    today = datetime.now(ZoneInfo(TZ)).date()
    store.update_settings({"time_zone": TZ})

    ml = store.create_subject("Machine Learning", "Introductory course: how models learn from data.")
    writing = store.create_subject("Technical Writing", "Writing clearly about technical work.")
    startup = store.create_subject("Startup Seminar", "Guest speakers and customer discovery.")
    team = store.create_subject("Team project", "The group project for the machine learning course.")
    store.update_subject(ml.id, schedule={"days": [0, 2], "start": "10:00", "minutes": 90})
    store.update_subject(writing.id, schedule={"days": [1], "start": "14:00", "minutes": 60})
    store.update_subject(startup.id, schedule={"days": [4], "start": "15:00", "minutes": 75})

    number = 1
    for entry, day in zip(ML, last_days({0, 2}, len(ML), today)):
        add(store, number, ml.id, entry, day, 10, "lecture")
        number += 1
    for entry, subject, weekday, hour in ((OTHER[0], writing, 1, 14), (OTHER[1], startup, 4, 15)):
        add(store, number, subject.id, entry, last_days({weekday}, 1, today)[0], hour, "lecture")
        number += 1
    add(store, number, team.id, MEETING, today - timedelta(days=2), 16, "meeting")

    Brain(store).catch_up()  # the concept map and the "where it came up" lists
    learn.sync_cards(store, TZ)  # flashcards from the definitions and the stressed points
    store.close()
    print(f"Made-up library written to {target}  ({number} recordings). Now run: python scripts/demo_server.py")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "demo-data")
