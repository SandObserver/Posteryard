import re
import unicodedata


def plain(text: str) -> str:
    """Letters and digits only, case-folded and without accents. Guides and art often drop a title's accents."""
    bare = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    return "".join(ch for ch in unicodedata.normalize("NFC", bare).casefold() if ch.isalnum())


def slug(text: str, length: int) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:length]
