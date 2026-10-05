"""JSONL event writer with recursive redaction before persistence."""

import json
import os
import re
from pathlib import Path
from threading import Lock
from typing import Iterable

from .schemas import Event


class JsonlLogger:
    """One process-local writer; supply all configured credential env names.

    Exact secret matching cannot detect transformed or encoded credentials.
    Redaction applies to raw responses too; logs intentionally sacrifice exact
    replay where secrets occur. Missing env variables are valid for offline use.
    """

    def __init__(self, path: Path, *, secret_env_names: Iterable[str] = (),
                 secrets: Iterable[str] = ()):
        self.path = path
        values = list(secrets) + [os.environ.get(key, "") for key in secret_env_names]
        self._secrets = sorted({v for v in values if v}, key=len, reverse=True)
        self._lock = Lock()

    @classmethod
    def create_exclusive(cls, path: Path, *, secret_env_names: Iterable[str] = ()) -> "JsonlLogger":
        """Atomically reserve a new log; never reuse another scientific run."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8"):
            pass
        return cls(path, secret_env_names=secret_env_names)

    def _redact(self, value: object) -> object:
        if isinstance(value, str):
            for secret in self._secrets:
                value = value.replace(secret, "[REDACTED]")
            value = re.sub(r"(?i)(Bearer\s+)[^\s\"']+", r"\1[REDACTED]", value)
            value = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{20,})",
                           "[REDACTED]", value)
            return value
        if isinstance(value, dict):
            return {key: self._redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        return value

    def write(self, event: Event) -> None:
        """Append one complete event, retaining null fields and propagating I/O errors."""
        payload = self._redact(event.model_dump(mode="json"))
        line = json.dumps(payload, ensure_ascii=True, allow_nan=False) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line)
                stream.flush()
