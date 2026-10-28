"""Word error rate, for checking transcription accuracy against a known script."""
import re


def normalize_words(text: str) -> list[str]:
    return re.sub(r"[^\w\s]", " ", text.lower().replace("-", " ")).split()


def word_error_rate(reference: str, hypothesis: str) -> float:
    ref, hyp = normalize_words(reference), normalize_words(hypothesis)
    previous = list(range(len(hyp) + 1))
    for i, ref_word in enumerate(ref, 1):
        current = [i] + [0] * len(hyp)
        for j, hyp_word in enumerate(hyp, 1):
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ref_word != hyp_word))
        previous = current
    return previous[-1] / max(len(ref), 1)
