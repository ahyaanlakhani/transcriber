"""The user's own transcription server (see server/): Whisper large-v3 plus speaker labels, via an async job API."""
from pathlib import Path

import httpx

from .base import PermanentError, Segment, TransientError, why

HEALTH_TIMEOUT = 3.0
UPLOAD_TIMEOUT = httpx.Timeout(30.0, write=1800.0, read=120.0)  # an 8-hour FLAC can take a while on Wi-Fi
TOKEN_REJECTED = "Your server rejected the token (check TRANSCRIBE_SERVER_TOKEN in both .env files)"


class RemoteServer:
    def __init__(self, url: str, token: str, *, client: httpx.Client | None = None):
        self.url = url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._client = client or httpx.Client()
        self.model = "whisper"
        self.last_error: str | None = None

    @property
    def name(self) -> str:
        return f"server/{self.model}"

    def health(self) -> dict | None:
        """The server's status, or None when it can't be used right now (reason in `last_error`)."""
        try:
            body = self._request("GET", "/health", timeout=HEALTH_TIMEOUT)
        except (TransientError, PermanentError) as exc:
            self.last_error = str(exc)
            return None
        if not isinstance(body, dict) or not body.get("ok"):
            self.last_error = "Your server answered, but not like a transcription server"
            return None
        self.last_error = None
        self.model = body.get("model") or self.model
        return body

    def submit(self, path: Path, *, language: str | None, prompt: str, speakers: bool) -> str:
        with open(path, "rb") as audio:
            body = self._request("POST", "/jobs", timeout=UPLOAD_TIMEOUT,
                                 files={"file": (path.name, audio, "audio/flac")},
                                 data={"language": language or "", "prompt": prompt,
                                       "speakers": "1" if speakers else "0"})
        return body["id"]

    def poll(self, job_id: str) -> dict:
        return self._request("GET", f"/jobs/{job_id}", timeout=30.0)

    def delete(self, job_id: str) -> None:
        self._request("DELETE", f"/jobs/{job_id}", timeout=30.0)

    def _request(self, method: str, path: str, *, timeout, **kwargs):
        try:
            response = self._client.request(method, self.url + path, headers=self._headers, timeout=timeout,
                                            **kwargs)
        except httpx.HTTPError as exc:  # connection refused, timeouts, dropped connections
            raise TransientError(f"Could not reach your server ({why(exc)})") from exc
        if response.status_code in (401, 403):
            raise PermanentError(TOKEN_REJECTED)
        if response.status_code >= 500:
            raise TransientError(f"Your server had an error ({response.status_code})")
        if response.status_code >= 400:
            raise PermanentError(f"Your server refused the request ({response.status_code})")
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise TransientError("Your server sent an unreadable reply") from exc


def segments_from(job: dict) -> list[Segment]:
    return [
        Segment(
            start=float(s["start"]),
            end=float(s["end"]),
            text=str(s["text"]).strip(),
            avg_logprob=s.get("avg_logprob"),
            no_speech_prob=s.get("no_speech_prob"),
            compression_ratio=s.get("compression_ratio"),
            speaker=s.get("speaker"),
        )
        for s in job.get("segments") or []
    ]

