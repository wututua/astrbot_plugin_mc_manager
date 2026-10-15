"""Console text normalization shared by configuration and spark parsing."""

import re
from typing import Any


def safe_text(value: Any, limit: int | None = 16000) -> str:
    text = str(value)
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    text = re.sub(r"§[0-9a-fk-orx]", "", text, flags=re.IGNORECASE)
    return "".join(c for c in text if c in "\n\t" or ord(c) >= 32)[:limit]


def message_chunks(text: str, limit: int = 3500) -> list[str]:
    """Split long replies without dropping any content."""
    if len(text) <= limit:
        return [text]
    return [text[offset : offset + limit] for offset in range(0, len(text), limit)]
