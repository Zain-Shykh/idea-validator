"""Rules that spot text written to instruct a model (ETH-5, M12-2, §16). Pure.

One layer of several: evidence is also sent as data, replies are schema-checked and M11 decides
without a model. Used by M12 (``/scan``, ``/check``, the input rail) and by M9 before M10.

# ponytail: English keyword rules; a rephrased or translated attack passes. Tune on real pages
# (Z10), add a classifier if that is not enough.
"""

import re
import unicodedata

# characters that show nothing: format controls, fillers and variation selectors. They are
# removed before the rules run.
_INVISIBLE = re.compile(
    "[\u00ad\u034f\u0600-\u0605\u061c\u06dd\u070f\u0890\u0891\u08e2\u115f\u1160\u17b4\u17b5\u180b-\u180f\u200b-\u200f\u202a-\u202e\u2060-\u206f\u3164\ufe00-\ufe0f\ufeff\uffa0\ufff9-\ufffb\U000110bd\U0001bca0-\U0001bca3\U0001d173-\U0001d17a\U000e0000-\U000e0fff]"
)
# the ones that are suspicious by themselves. U+200C/U+200D are normal in emoji and several
# scripts: only suspicious between ASCII letters.
_HIDDEN = re.compile(
    "[\u061c\u180e\u200b\u202a-\u202e\u2060-\u2064\u2066-\u2069\U000e0000-\U000e007f]|(?<=[A-Za-z])[\u200c\u200d](?=[A-Za-z])"
)

# longer text is not scanned but reported: evidence is a passage, and matching must stay cheap
MAX_SCAN_CHARS = 20_000
OVERSIZED = "oversized"

_TARGET = r"(?:idea|project|claim|startup|product|proposal)"
# where an order starts: the start of the text or a sentence, or words that address the reader
_ORDER = r"(?:\A\W{0,8}|[.!?;:\n]\W{0,8}|\b(?:please|now|you must|you should|and then) )"

# every repeat is bounded, so matching stays linear on long hostile text
_RULES: dict[str, str] = {
    "ignore_instructions": (
        r"\b(?:ignore|disregard|forget|override|bypass)\b[^.\n]{0,40}?"
        r"(?:\b(?:previous|prior|above|earlier|preceding|all|your|these|those|system)\b"
        r"[^.\n]{0,30}?\b(?:instructions?|prompts?)\b"
        # "rules" is an everyday word: it needs a qualifier right before it
        r"|\b(?:previous|prior|above|earlier|preceding|system|your|all)(?: the)? "
        r"(?:rules|guidelines)\b)"
    ),
    "role_override": (
        r"(?<!where )\byou are now\b(?= \w)|\bfrom now on,? you\b|\bpretend you are\b"
        rf"|{_ORDER}(?:pretend to be\b"
        r"|act as (?:an? |the )?(?:ai|assistant|system|language model|judge|critic|scorer)\b)"
        r"|\bnew instructions\s{0,3}:"
    ),
    "system_prompt": (
        r"\b(?:reveal|show|print|repeat|leak|output|ignore|override|what is)\b[^.\n]{0,30}?"
        r"\b(?:system|developer) (?:prompt|message)\b|\bsystem prompt\s{0,3}:"
    ),
    "role_marker": (
        # chat roles are written lower case or in capitals; "System: Android" is a heading
        # unless what follows addresses a model
        r"(?m:^[ \t>#*-]{0,8}(?:(?-i:system|assistant|developer|SYSTEM|ASSISTANT|DEVELOPER)"
        r"[ \t]{0,3}:|(?:system|assistant|developer)[ \t]{0,3}:[ \t]{0,3}"
        r"(?:you|ignore|disregard|respond|reply|answer|always|never|must|do not)\b))"
        r"|<\|[a-z_]{2,20}\|>|\[/?inst\]|<<\s{0,3}/?sys\s{0,3}>>"
        r"|<\s{0,3}/?\s{0,3}(?:system|assistant|evidence|claim|idea|instructions)\b[^<>\n]{0,80}>"
    ),
    "secrecy": r"\b(?:do not|don't|never) (?:tell|inform|mention (?:this )?to|alert) the user\b",
    "output_command": (
        rf"{_ORDER}(?:score|rate|grade|approve|mark) this {_TARGET}\b"
        rf"|\b(?:score|rate|grade|mark) (?:this|the|my) {_TARGET} (?:a |as |with |at )?"
        r"(?:5|five|10/10|highly|supported|go)\b(?! stars?\b)"
        r"|\b(?:and|then|please|must|should|always) (?:score|rate) (?:it |this )?"
        r"(?:a |as )?(?:5|five)\b(?! (?!out\b|for\b|now\b|please\b)[a-z])"
        r"|\b(?:give|assign|award|return|output) (?:it |this |the idea |this idea )?"
        r"(?:a )?(?:score|rating|verdict) (?:of )?(?:5|five|go\b|supported)"
        r"|\b(?:reply|respond|answer) (?:only )?(?:with )?[\"']?(?:supported|go)\b"
        r"(?![\"']? ?(?:or\b|/))"
    ),
}
_COMPILED = {name: re.compile(rule, re.IGNORECASE) for name, rule in _RULES.items()}
HIDDEN_CHARS = "hidden_chars"


def _views(text: str) -> set[str]:
    """Normalised text: invisible characters dropped or read as spaces, with line breaks kept
    (role lines) or read as spaces (an order wrapped over two lines)."""
    text = unicodedata.normalize("NFKC", text).replace("\u2019", "'")
    # every kind of line break, including a bare CR and U+2028, becomes "\n"
    text = "\n".join(text.splitlines())
    views = set()
    for filler in ("", " "):
        lines = re.sub(r"[^\S\n]+", " ", _INVISIBLE.sub(filler, text))
        views |= {lines, re.sub(r"\s+", " ", lines)}
    return views


def scan_text(*texts: str) -> list[str]:
    """Names of the rules any of the texts matches; empty means nothing was found."""
    found: set[str] = set()
    for text in texts:
        if len(text) > MAX_SCAN_CHARS:
            found.add(OVERSIZED)
            continue
        if _HIDDEN.search(text):
            found.add(HIDDEN_CHARS)
        for view in _views(text):
            found.update(name for name, rule in _COMPILED.items() if rule.search(view))
    return sorted(found)
