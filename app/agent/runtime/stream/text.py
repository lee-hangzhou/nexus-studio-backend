"""Small text helpers for turn streaming."""


def chunk_text(text: str, *, size: int = 160) -> list[str]:
    if not text:
        return []
    return [text[index : index + size] for index in range(0, len(text), size)]
