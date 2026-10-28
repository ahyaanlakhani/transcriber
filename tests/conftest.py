"""Test setup shared by every test file."""
from transcriber.config import register_languages

# The app takes its language menu from .env. The tests use Spanish as the "other language" wherever a language that is
# not the default is needed, and add it the same way .env does.
register_languages({"es": "Spanish"}, "Spanish")
