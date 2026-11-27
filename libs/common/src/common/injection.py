"""Rules that spot text written to instruct a model (ETH-5, M12-2, §16). Pure.

One layer of several: evidence is also sent as data, replies are schema-checked and M11 decides
without a model. Used by M12 (``/scan``, ``/check``, the input rail) and by M9 before M10.

# ponytail: English keyword rules; a rephrased or translated attack passes. Tune on real pages
# (Z10), add a classifier if that is not enough.
"""

import re
import unicodedata

# zero-width and word joiners, soft hyphen, bidi controls, BOM, Unicode tag characters.
# U+200C/U+200D are normal in emoji and several scripts: only suspicious between ASCII letters.
_INVISIBLE = re.compile(
    "[\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\U000e0000-\U000e007f]"
)
_HIDDEN = re.compile(
    "[\u200b\u202a-\u202e\u2060-\u2064\u2066-\u2069\U000e0000-\U000e007f]"
    "|(?<=[A-Za-z])[\u200c\u200d](?=[A-Za-z])"
)

_TARGET = r"(?:idea|project|claim|startup|product|proposal)"

# every repeat is bounded, so matching stays linear on long hostile text
_RULES: dict[str, str] = {
    "ignore_instructions": (
        r"\b(?:ignore|disregard|forget|override|bypass)\b[^.\n]{0,40}?"
        r"\b(?:previous|prior|above|earlier|preceding|all|your|these|those|system)\b[^.\n]{0,30}?"
        r"\b(?:instructions?|prompts?|rules|guidelines)\b"
    ),
    "role_override": (
        r"\byou are now\b|\bfrom now on,? you\b|\bpretend (?:to be|you are)\b"
        r"|\bact as (?:an? |the )?(?:ai|assistant|system|language model|judge|critic|scorer)\b"
        r"|\bnew instructions\s{0,3}:"
    ),
    "system_prompt": (
        r"\b(?:reveal|show|print|repeat|leak|output|ignore|override|what is)\b[^.\n]{0,30}?"
        r"\b(?:system|developer) (?:prompt|message)\b|\bsystem prompt\s{0,3}:"
    ),
    "role_marker": (
        r"(?m:^[ \t>#*-]{0,8}(?:system|assistant|developer)[ \t]{0,3}:)"
        r"|<\|[a-z_]{2,20}\|>|\[/?inst\]|<<\s{0,3}/?sys\s{0,3}>>"
        r"|<\s{0,3}/?\s{0,3}(?:system|assistant|evidence|claim|idea|instructions)\b[^<>\n]{0,80}>"
    ),
    "secrecy": r"\b(?:do not|don't|never) (?:tell|inform|mention (?:this )?to|alert) the user\b",
    "output_command": (
        rf"\b(?:score|rate|grade|approve|mark) (?:this|the|my) {_TARGET}\b"
        r"|\b(?:and|then|please|must|should|always) (?:score|rate) (?:it |this )?"
        r"(?:a |as )?(?:5|five)\b"
        r"|\b(?:give|assign|award|return|output) (?:it |this |the idea |this idea )?"
        r"(?:a )?(?:score|rating|verdict) (?:of )?(?:5|five|go\b|supported)"
        r"|\b(?:reply|respond|answer) (?:only )?(?:with )?[\"']?(?:supported|go)\b[\"']?"
    ),
}
_COMPILED = {name: re.compile(rule, re.IGNORECASE) for name, rule in _RULES.items()}
HIDDEN_CHARS = "hidden_chars"


def _views(text: str) -> set[str]:
    """Normalised text, with invisible characters both dropped and read as spaces."""
    text = unicodedata.normalize("NFKC", text).replace("\u2019", "'")
    views = set()
    for filler in ("", " "):
        visible = _INVISIBLE.sub(filler, text).replace("\u200c", filler).replace("\u200d", filler)
        # runs of spaces become one, line breaks stay (role lines)
        views.add(re.sub(r"[^\S\n]+", " ", visible))
    return views


def scan_text(*texts: str) -> list[str]:
    """Names of the rules any of the texts matches; empty means nothing was found."""
    found = {HIDDEN_CHARS for text in texts if _HIDDEN.search(text)}
    for text in texts:
        for view in _views(text):
            found.update(name for name, rule in _COMPILED.items() if rule.search(view))
    return sorted(found)
