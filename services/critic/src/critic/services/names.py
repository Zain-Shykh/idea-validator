"""Names a claim uses, so each can be looked for in the cited evidence (M9-4, AVOID-4). Pure.

# ponytail: capitalisation rules for English. Lower-case names, all-capitals names and a plain
# capitalised word that opens a sentence without a product verb are missed; the judge prompt
# covers those only partly. Use an NER model if Z10 shows that is not enough.
"""

# ruff: noqa: SIM905  (word lists read better as text)
import re
import unicodedata
from collections.abc import Iterable

CARD_ID = re.compile(r"\bcard_[A-Za-z0-9]+")

_TOKEN = re.compile(r"[^\W_]+(?:['.\-][^\W_]+)*")
_DOMAIN = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|io|ai|app|org|net|co|dev|edu|so|me)")
# quotes, brackets and list markers that may stand between a sentence end and its first word
_OPENERS = " \t\r\"'([*>#-"
# a capitalised word right before one of these, even at the start of a sentence, names a product
_PRODUCT_VERBS = frozenset(
    "offers provides lets supports charges launched raised dominates sells integrates "
    "automates generates".split()
)
# capitalised by grammar when they open a sentence, never a name there
_STARTERS = frozenset(
    "a all an and another any because both but each every existing few for however if in it its "
    "like many most no nobody none nothing one only other our several similar since so some "
    "someone such that the their there these they this those today unlike when while with"
    .split()
)  # fmt: skip
# general terms that are not a product or company
_GENERIC = frozenset(
    "ai api apis app apps gpu cpu csv html http https json llm llms ml nlp ocr pdf pdfs sdk sql "
    "ui ux url urls mvp saas paas iot qr ios android web internet english i i'm".split()
)  # fmt: skip


def _opens_sentence(text: str, start: int) -> bool:
    end = start
    while end and text[end - 1] in _OPENERS:
        end -= 1
    return not end or text[end - 1] in ".!?:;\n"


def normalise(text: str) -> str:
    """Lower case, with everything but letters and digits as single spaces."""
    text = unicodedata.normalize("NFKC", text).replace("\u2019", "'")
    text = re.sub(r"'s\b", "", text.casefold())
    return " ".join(re.findall(r"[^\W_]+", text))


def _inner_capital(word: str) -> bool:
    return any(c.isupper() for c in word[1:]) and any(c.islower() for c in word)


def names_in(text: str) -> list[str]:
    """Words and runs of capitalised words that look like names, in order, without repeats."""
    text = unicodedata.normalize("NFKC", text).replace("\u2019", "'")
    tokens = [(m.group().removesuffix("'s"), m.start(), m.end()) for m in _TOKEN.finditer(text)]
    found: list[str] = []
    index = 0
    while index < len(tokens):
        word, start, end = tokens[index]
        index += 1
        if _DOMAIN.fullmatch(word.lower()):
            found.append(word)
            continue
        if not (word[0].isupper() or _inner_capital(word)):
            continue
        at_start = _opens_sentence(text, start)
        run = [word]
        # consecutive capitalised words, with only spaces between them, are one name
        while index < len(tokens):
            following, next_start, next_end = tokens[index]
            if not following[0].isupper() or text[end:next_start].strip(" \t"):
                break
            run.append(following.removesuffix("'s"))
            end = next_end
            index += 1
        if at_start and run[0].lower() in _STARTERS:
            run, at_start = run[1:], False
        while run and run[0].lower() in _GENERIC | _STARTERS:
            run, at_start = run[1:], False
        while run and run[-1].lower() in _GENERIC:
            run = run[:-1]
        if not run:
            continue
        if at_start and len(run) == 1 and not _inner_capital(run[0]):
            verb = tokens[index][0].lower() if index < len(tokens) else ""
            if verb not in _PRODUCT_VERBS:
                continue
        found.append(" ".join(run))
    return list(dict.fromkeys(found))


def missing_names(names: Iterable[str], texts: Iterable[str]) -> list[str]:
    """The names that no text mentions as whole words."""
    haystacks = [f" {normalise(text)} " for text in texts]
    return [n for n in names if not any(f" {normalise(n)} " in h for h in haystacks)]
