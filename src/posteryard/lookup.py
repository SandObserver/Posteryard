import difflib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from posteryard.plex import Item, Plex, is_rating_key

YEAR = re.compile(r"^(.*\S)\s+\(?(\d{4})\)?$")
KINDS = ("movie", "show")
MAX_SUGGESTIONS = 8
CLOSE_MATCH = 0.75


@dataclass(frozen=True)
class Match:
    rating_key: str
    title: str
    year: int | None
    kind: str

    @property
    def label(self) -> str:
        if self.kind == "season":
            return self.title
        details = ", ".join(part for part in (str(self.year) if self.year else "", self.kind) if part)
        return f"{self.title} ({details})" if details else self.title

    @property
    def name_with_year(self) -> str:
        return f"{self.title} {self.year}" if self.year else self.title


class TitleError(Exception):
    def __init__(self, text: str, matches: Sequence[Match], *, ambiguous: bool) -> None:
        super().__init__(text)
        self.text, self.matches, self.ambiguous = text, list(matches), ambiguous

    def explain(self, command: str, options: str = "") -> str:
        if not self.ambiguous:
            lines = [f'No title named "{self.text}".']
            if self.matches:
                lines.append("Did you mean:")
                lines += [f"  {m.label}   {m.rating_key}" for m in self.matches]
            else:
                lines.append("Run `posteryard find WORD` to search by part of the name.")
            return "\n".join([*lines, "Nothing changed."])
        lines = [f'"{self.text}" matches {len(self.matches)} titles. Run again with one of these:']
        width = max(len(m.label) for m in self.matches)
        for m in self.matches:
            unique = sum(1 for other in self.matches if _norm(other.name_with_year) == _norm(m.name_with_year)) == 1
            retry = f'posteryard {command} "{m.name_with_year}"{options}' if unique else ""
            retry = f"{retry}   or {m.rating_key}" if retry else m.rating_key
            lines.append(f"  {m.label.ljust(width)}   {retry}")
        return "\n".join([*lines, "Nothing changed."])


def _norm(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _match(item: Item) -> Match:
    year = item.get("year")
    return Match(str(item["ratingKey"]), str(item.get("title", "")), int(year) if year else None, str(item["type"]))


def library_titles(plex: Plex, libraries: Iterable[str]) -> list[Match]:
    """Every movie and show in the managed libraries."""
    wanted = set(libraries)
    titles: list[Match] = []
    for section in plex.sections():
        if section.get("title") not in wanted:
            continue
        kind = "movie" if section.get("type") == "movie" else "show" if section.get("type") == "show" else None
        if kind is not None:
            titles += [_match(item) for item in plex.section_items(str(section["key"]), kind)]
    return titles


def search(titles: Sequence[Match], query: str) -> list[Match]:
    """Titles that contain the query, then titles spelled almost like it."""
    key = _norm(query)
    if not key:
        return []
    containing = [t for t in titles if key in _norm(t.title)]
    names = {_norm(t.title) for t in titles}
    close = set(difflib.get_close_matches(key, names, n=MAX_SUGGESTIONS, cutoff=CLOSE_MATCH))
    similar = [t for t in titles if _norm(t.title) in close and t not in containing]
    return sorted(containing, key=lambda t: (t.title, t.year or 0)) + sorted(
        similar, key=lambda t: difflib.SequenceMatcher(None, key, _norm(t.title)).ratio(), reverse=True
    )


def resolve(plex: Plex, libraries: Iterable[str], text: str) -> Match:
    """The one movie or show `text` names: a title, a title and year, or a rating key. Never a guess."""
    text = text.strip()
    titles = library_titles(plex, libraries)
    matches = [t for t in titles if _norm(t.title) == _norm(text)]
    year = YEAR.match(text)
    if year:
        name, number = _norm(year.group(1)), int(year.group(2))
        matches += [t for t in titles if _norm(t.title) == name and t.year == number and t not in matches]
    if is_rating_key(text):
        item = plex.item(text)
        if item is not None and str(item.get("ratingKey")) not in {m.rating_key for m in matches}:
            matches.insert(0, _match(item))
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise TitleError(text, matches, ambiguous=True)
    raise TitleError(text, search(titles, text)[:MAX_SUGGESTIONS], ambiguous=False)


def season(plex: Plex, show: Match, number: int) -> Match:
    if show.kind != "show":
        raise ValueError(f"{show.label} is not a show. --season works with shows only")
    children = plex.children(show.rating_key)
    for child in children:
        if int(child.get("index", -1)) == number:
            name = f"{show.title} ({show.year})" if show.year else show.title
            return Match(str(child["ratingKey"]), f"{name} {child.get('title', '')}".strip(), None, "season")
    available = ", ".join(str(child.get("index")) for child in children) or "none"
    raise ValueError(f"{show.label} has no season {number}. Seasons in Plex: {available}")
