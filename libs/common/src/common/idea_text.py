"""Idea text with sections appended later: clarification answers and applied changes.

Whoever shortens an idea must use ``shorten``: cutting the end would drop exactly what the
student added last (U5, U7). Pure.
"""

import re

CLARIFICATIONS = "Clarifications"
CHANGES = "Changes"

_SECTION = re.compile(rf"\n\n({CLARIFICATIONS}|{CHANGES}):\n- ")
_ENTRY = "\n- "

Sections = list[tuple[str, list[str]]]


def _split(text: str) -> tuple[str, Sections]:
    """The idea as first written, and what was appended to it."""
    parts = _SECTION.split(text)
    sections = [
        (header, body.split(_ENTRY)) for header, body in zip(parts[1::2], parts[2::2], strict=True)
    ]
    return parts[0], sections


def _render(sections: Sections) -> str:
    return "".join(
        f"\n\n{header}:" + "".join(_ENTRY + entry for entry in entries)
        for header, entries in sections
    )


def _cut(entry: str, length: int) -> str:
    """An entry of two parts (a question, then its answer) loses the first part first: what
    the student said comes last and matters most. A third of the room stays for the first."""
    if len(entry) <= length:
        return entry
    first, newline, rest = entry.partition("\n")
    if newline:
        first = first[: max(length - len(rest) - 1, length // 3)]
    return (first + newline + rest)[:length]


def _fit(base: str, sections: Sections, limit: int) -> str:
    """At most ``limit`` characters. The appended entries get at least half of the room when
    the whole is too long, never more unless the idea is short, in equal shares."""
    tail = _render(sections)
    if len(base) + len(tail) <= limit:
        return base + tail
    if not sections:
        return base[:limit]
    room = max(limit - len(base), limit // 2)
    if len(tail) > room:
        count = sum(len(entries) for _, entries in sections)
        fixed = len(_render([(header, [""] * len(entries)) for header, entries in sections]))
        share = max(0, room - fixed) // count
        tail = _render([(h, [_cut(e, share) for e in entries]) for h, entries in sections])[:limit]
    return base[: limit - len(tail)] + tail


def shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return _fit(*_split(text), limit)


def append(text: str, header: str, entries: list[str], limit: int) -> str:
    """``text`` with ``entries`` added to its ``header`` section, within ``limit``. An entry
    cannot start a new line with ``- ``, so it cannot pose as another entry or section."""
    base, sections = _split(text)
    clean = [re.sub(r"\n\s*(?=-)|\n{2,}", "\n", entry).replace("\n-", "\n -") for entry in entries]
    if sections and sections[-1][0] == header:
        sections[-1] = (header, [*sections[-1][1], *clean])
    else:
        sections.append((header, clean))
    return _fit(base, sections, limit)
