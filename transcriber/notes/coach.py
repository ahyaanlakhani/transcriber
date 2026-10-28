"""Conversation coach for coffee chats: who said what (worked out from the words, never from a voiceprint),
coaching notes on how you came across, and simple numbers about your side of the conversation."""
import json
import re
from dataclasses import replace

from ..llm.base import LLMClient
from ..postprocess import Paragraph
from ..providers.base import Segment
from .model import CONVERSATION_PROPERTIES, WRITER_SCHEMA, Notes, notes_from_writer
from .prompts import context_lines
from .transcript import PART_MAX_CHARS, render, split_parts
from .writer import Call

ME, THEM = "ME", "THEM"
PRACTICE_ORIGINAL = "Practice session"  # original_filename of Practice room sessions  # speaker labels written onto the transcript's segments
ATTRIBUTION_PART_CHARS = 9_000  # line-numbered transcript per request: the reply lists every turn
UNCLEAR_LOGPROB = -1.0  # Whisper was unsure of this stretch: usually mumbled, rushed or swallowed words
FILLERS = ("um", "uh", "you know", "i mean", "kind of", "sort of", "basically")
_FILLER = re.compile(r"\b(" + "|".join(re.escape(f) for f in FILLERS) + r")\b", re.IGNORECASE)

ATTRIBUTION_SYSTEM = """You work out who is speaking in a recorded conversation, such as a coffee chat or a \
networking talk. The person who recorded it is "me". Everyone else is "them".

The transcript lines are numbered. Decide who says each line, using what is said:
- "me" usually introduces themselves early, asks most of the questions, reacts ("that's great", "interesting"), \
thanks the other person and offers to follow up.
- "them" usually answers the questions, tells their own story and career path, and gives advice.
- A question about the other person's own life, work or opinions ("how did you...", "what made you...", "did \
teaching change how you...") is almost always "me". The long answer that follows is "them".
- Whisper sometimes joins the end of one person's turn and the start of the other's on one line. Give the line \
to whoever says most of it.
- A line may start with a label from a speaker-separation tool, like [SPEAKER_01]. Treat it as a strong hint: \
lines with the same label are usually the same person.

Return turns: the line numbers where the speaker changes, in order, starting with line 1, each with who \
("me" or "them"). Also return their_name: the other person's name if it is said, or clear from the recording \
title (for example "Coffee with Dr. Khan" or "Sir Ahmed advice"), with the title they go by; otherwise null."""

ATTRIBUTION_SCHEMA = {
    "type": "object",
    "properties": {
        "turns": {"type": "array", "items": {
            "type": "object", "properties": {"line": {"type": "integer"}, "who": {"type": "string", "enum": ["me", "them"]}},
            "required": ["line", "who"], "additionalProperties": False}},
        "their_name": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": ["turns", "their_name"],
    "additionalProperties": False,
}

COACH_PART_SYSTEM = """You are a warm, honest conversation coach. The transcript is a conversation the user \
recorded, such as a coffee chat with a professor, a networking chat at an event, or an informational interview. \
Lines start with "You:" (the user) or the other person's name. The user wants to get better at introducing \
themselves, asking questions and following up.

Rules:
- Use only the transcript. Never invent what anyone said. Every item cites the timestamp(s) it comes from, \
copied exactly, like "[12:34]".
- about_them: what the user learned about the other person: their role, background and path, the advice they \
gave, what they care about, people or resources they mentioned. One sentence each.
- intro: how the user introduced themselves, close to their own words, and in suggestion a tighter version \
(two or three sentences: who they are, what they are exploring, why this person). Empty if the user does not \
introduce themselves in this part.
- my_questions: every real question the user asked, as asked. kind is "open" (invites a story or opinion) or \
"closed" (yes/no or one fact). suggestion is a sharper version when the question was weak, closed or \
stacked; null when it was already good.
- missed_followups: up to 5 moments where the other person said something worth digging into and the user \
moved on. text says what they said; suggestion is the follow-up question the user could have asked.
- action_items: promises and next steps from either side (send a CV, an introduction, meet again). owner is \
"You" or the other person's name.
- Leave a section empty when it has nothing. Plain English."""

COACH_FINAL_SYSTEM = """You are a warm, honest conversation coach. You are given what was found in each part of \
a conversation the user recorded (what they learned, their intro, their questions, missed follow-ups, promises) \
and numbers about the user's side of the conversation. Write:

- summary: 3 to 5 bullets with what the user learned from this person.
- feedback: 3 to 6 specific coaching points. kind is "strength" for what worked and should be kept, or \
"improve" for what to change next time. Base each on the evidence given: the questions, the missed \
follow-ups, the intro and the numbers. For example, talking more than about 40% of the time in a chat meant to \
learn from someone, many closed questions, or many filler words are worth a point. Be concrete ("Ask what \
surprised them, not whether they liked it"), never generic ("be more confident"). Cite timestamps when a point \
comes from a moment.
- follow_up_draft: a short thank-you message (under 120 words) to send after the chat. Mention one or two \
specific things they said, and anything the user promised to send. Warm and plain, no clichés.
- topics: 1 to 5 short lowercase labels for what the conversation covered.
Use only what you are given; never invent details."""

PART_SCHEMA = {
    "type": "object",
    "properties": {name: CONVERSATION_PROPERTIES[name] for name in ("about_them", "intro", "my_questions",
                                                                    "missed_followups")}
                  | {"action_items": WRITER_SCHEMA["properties"]["action_items"]},
    "required": ["about_them", "intro", "my_questions", "missed_followups", "action_items"],
    "additionalProperties": False,
}
FINAL_SCHEMA = {
    "type": "object",
    "properties": {"summary": WRITER_SCHEMA["properties"]["summary"], "feedback": CONVERSATION_PROPERTIES["feedback"],
                   "follow_up_draft": {"type": "string"}, "topics": {"type": "array", "items": {"type": "string"}}},
    "required": ["summary", "feedback", "follow_up_draft", "topics"],
    "additionalProperties": False,
}


# --- who is speaking

def _attribution_parts(segments: list[Segment]) -> list[list[int]]:
    parts: list[list[int]] = []
    current: list[int] = []
    size = 0
    for index, segment in enumerate(segments):
        length = len(segment.text) + 8
        if current and size + length > ATTRIBUTION_PART_CHARS:
            parts.append(current)
            current, size = [], 0
        current.append(index)
        size += length
    if current:
        parts.append(current)
    return parts


def attribute_speakers(llm: LLMClient, segments: list[Segment], *, call: Call,
                       title: str = "") -> tuple[list[Segment], str | None]:
    """Label every segment ME or THEM from what is said. Returns the labeled segments and the other person's name."""
    labels: dict[int, str] = {}  # line number (1-based, across the whole transcript) -> ME/THEM where a turn starts
    name: str | None = None
    for part in _attribution_parts(segments):
        lines = "\n".join(f"{i + 1}| {f'[{segments[i].speaker}] ' if segments[i].speaker else ''}"
                          f"{' '.join(segments[i].text.split())}" for i in part)
        head = f"Recording title: {title}\n\n" if title else ""
        raw = call(lambda: llm.complete_json(system=ATTRIBUTION_SYSTEM, user=f"{head}TRANSCRIPT:\n{lines}",
                                             schema=ATTRIBUTION_SCHEMA, schema_name="speakers", effort="low"))
        valid = {i + 1 for i in part}
        for turn in raw.get("turns") or []:
            if isinstance(turn, dict) and turn.get("line") in valid and turn.get("who") in ("me", "them"):
                labels[turn["line"]] = ME if turn["who"] == "me" else THEM
        their = " ".join(str(raw.get("their_name") or "").split())
        name = their or name
    current = ME  # the person recording usually speaks first; corrected by the first turn
    labeled = []
    for line, segment in enumerate(segments, 1):
        current = labels.get(line, current)
        labeled.append(replace(segment, speaker=current))
    return labeled, name


# --- your numbers

def conversation_stats(segments: list[Segment]) -> dict | None:
    """Talk share, questions, longest uninterrupted stretch and filler words for your side (needs ME/THEM labels)."""
    if not any(s.speaker == ME for s in segments) or not any(s.speaker == THEM for s in segments):
        return None
    mine = [s for s in segments if s.speaker == ME]
    total = sum(max(0.0, s.end - s.start) for s in segments) or 1.0
    my_seconds = sum(max(0.0, s.end - s.start) for s in mine)
    longest = run = 0.0
    for segment in segments:
        run = run + max(0.0, segment.end - segment.start) if segment.speaker == ME else 0.0
        longest = max(longest, run)
    filler_words: dict[str, int] = {}
    for segment in mine:
        for match in _FILLER.findall(segment.text):
            word = match.lower()
            filler_words[word] = filler_words.get(word, 0) + 1
    words = sum(len(s.text.split()) for s in mine)
    unclear = [{"start": s.start, "text": " ".join(s.text.split())} for s in mine
               if s.avg_logprob is not None and s.avg_logprob < UNCLEAR_LOGPROB]
    return {"my_share": round(my_seconds / total, 3), "my_questions": sum("?" in s.text for s in mine),
            "longest_turn_sec": round(longest, 1), "fillers": sum(filler_words.values()),
            "filler_words": filler_words, "my_minutes": round(my_seconds / 60, 1),
            "wpm": round(words / (my_seconds / 60)) if my_seconds else 0, "unclear": unclear[:8]}


# --- the coaching notes

def write_coaching(llm: LLMClient, *, title: str, paragraphs: list[Paragraph], long_form: bool,
                   existing_topics: list[str], call: Call, stats: dict | None, their_name: str | None,
                   instructions: str = "") -> Notes:
    parts = split_parts(paragraphs, getattr(llm, "max_input_chars", None) or PART_MAX_CHARS)
    if not parts:
        return Notes()
    found: dict[str, list] = {"about_them": [], "intro": [], "my_questions": [], "missed_followups": [],
                              "action_items": []}
    for n, part in enumerate(parts, 1):
        scope = f"This is part {n} of {len(parts)} of the conversation.\n" if len(parts) > 1 else ""
        user = (f"Conversation title: {title}\nThe other person: {their_name or 'not named'}\n"
                f"{context_lines(instructions)}{scope}\nTranscript:\n{render(part, long_form)}")
        raw = call(lambda: llm.complete_json(system=COACH_PART_SYSTEM, user=user, schema=PART_SCHEMA,
                                             schema_name="coaching_part"))
        for key in found:
            items = [item for item in raw.get(key) or [] if isinstance(item, dict)]
            if key == "intro" and found["intro"]:
                continue  # you introduce yourself once; later "intros" are just mentions
            found[key] += items
    topics = ", ".join(existing_topics) if existing_topics else "(none yet)"
    user = (f"Conversation title: {title}\nThe other person: {their_name or 'not named'}\nExisting topics: {topics}\n"
            f"{context_lines(instructions)}\nYOUR NUMBERS:\n{json.dumps(stats or {}, ensure_ascii=False)}"
            f"\n\nFOUND IN THE CONVERSATION:\n{json.dumps(found, ensure_ascii=False)}")
    final = call(lambda: llm.complete_json(system=COACH_FINAL_SYSTEM, user=user, schema=FINAL_SCHEMA,
                                           schema_name="coaching"))
    return notes_from_writer({**found, **final, "person": their_name})
