import pytest

from .wer import normalize_words, word_error_rate


def test_normalize_ignores_case_punctuation_and_hyphens():
    assert normalize_words("Hello, Okonkwo-Marr!  OK.") == ["hello", "okonkwo", "marr", "ok"]


def test_identical_is_zero():
    assert word_error_rate("the cat sat down", "The cat sat down.") == 0.0


def test_substitution():
    assert word_error_rate("the cat sat down", "the bat sat down") == 0.25


def test_insertion_and_deletion():
    assert word_error_rate("the cat sat down", "the cat sat right down") == 0.25
    assert word_error_rate("the cat sat down", "the cat down") == 0.25


def test_empty_reference():
    assert word_error_rate("", "") == 0.0
    assert word_error_rate("", "extra") == pytest.approx(1.0)
