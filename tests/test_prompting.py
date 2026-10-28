from transcriber.prompting import TAIL_MAX_CHARS, VOCAB_MAX_CHARS, build_prompt, parse_terms


def test_empty_inputs_give_empty_prompt():
    assert build_prompt("", "") == ""


def test_parse_terms_splits_on_commas_and_newlines():
    assert parse_terms("Zorvath, Quillbrook\nKPIX\n\n , ") == ["Zorvath", "Quillbrook", "KPIX"]


def test_glossary_only():
    assert build_prompt("Zorvath, Quillbrook\nKPIX", "") == "Glossary: Zorvath, Quillbrook, KPIX."


def test_tail_only():
    assert build_prompt("", "We agreed to ship on Friday.") == "We agreed to ship on Friday."


def test_glossary_then_tail():
    assert build_prompt("Zorvath", "Ship it.") == "Glossary: Zorvath. Ship it."


def test_tail_whitespace_is_collapsed():
    assert build_prompt("", "a\n\n  b") == "a b"


def test_glossary_capped_at_term_boundary():
    terms = [f"Term{i:03d}" for i in range(100)]
    prompt = build_prompt(", ".join(terms), "")
    joined = prompt.removeprefix("Glossary: ").removesuffix(".")
    kept = joined.split(", ")
    assert len(joined) <= VOCAB_MAX_CHARS
    assert kept == terms[: len(kept)]
    assert len(kept) == 50


def test_tail_capped_at_word_boundary():
    words = " ".join(f"word{i}" for i in range(100))
    tail = build_prompt("", words)
    assert len(tail) <= TAIL_MAX_CHARS
    assert words.endswith(" " + tail)


def test_single_huge_term_is_dropped():
    assert build_prompt("x" * 500, "") == ""
