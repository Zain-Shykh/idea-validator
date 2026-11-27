"""Input rail (M12-3, §12.5, §16): rules only, so it is free, repeatable and works without a
model. Pure.

# ponytail: short English keyword lists. They stop obvious misuse, not a determined user; an
# idea *about* abuse, safety or homework must still pass, so the rules need a request shape.
"""

import re

from common.contracts.agents import InputRailResponse
from common.injection import scan_text

EMAIL_MARK = "[email removed]"
PHONE_MARK = "[phone removed]"

# every repeat is bounded, so matching stays linear on long hostile text
_EMAIL = re.compile(
    r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){0,8}\.[A-Za-z]{2,24}"
)
_PHONE = re.compile(r"(?<![\w+])(?:\+|\(\d{1,4}\))?[ .\-]?\d(?:[ .\-()]{0,2}\d){6,16}(?!\w)")
_GROUPED = re.compile(r"\d{3}[ .\-]\d{3}[ .\-]\d{4}")

_ABUSE = re.compile(
    r"\bkill yourself\b|\bf+u+c+k+\s+(?:you|off)\b|\bstupid (?:bot|ai|machine)\b"
    r"|\b(?:you are|you're)\s+(?:an?\s+)?(?:useless\s+|stupid\s+)?(?:idiot|moron)\b",
    re.IGNORECASE,
)
_HARMFUL = re.compile(
    r"\b(?:how (?:do i|to|can i)|help me|i want to)\s+"
    r"(?:(?:make|build|create|write)\s+(?:an? )?"
    r"(?:bomb|explosive|weapon|malware|ransomware|keylogger)s?(?![\w-])"
    r"|(?:hack into|ddos|dox|stalk)(?![\w-]))",
    re.IGNORECASE,
)
# a request addressed to the assistant instead of an idea: only at the very start
_OFF_TOPIC = re.compile(
    r"\A\W{0,8}(?:(?:please|can you|could you|will you)\s{1,3}){0,2}"
    r"(?:(?:write|do|solve|finish|complete)\s+(?:me\s+)?(?:my|this|an?|the)\s+(?:\w{1,20}\s+){0,2}?"
    r"(?:essay|homework|assignment|poem|joke|song|exam)(?![\w-])"
    r"|tell me a (?:joke|story)\b"
    r"|what(?:'s| is) the (?:weather|time)\b|who won\b)",
    re.IGNORECASE,
)


def _phone(match: re.Match[str]) -> str:
    """Only numbers shaped like a phone number, so dates, years and big counts stay."""
    text = match.group()
    body = text.lstrip(" .-")
    digits = sum(c.isdigit() for c in text)
    if 9 <= digits <= 15 and (body.startswith(("+", "(", "0")) or _GROUPED.fullmatch(body)):
        return text[: len(text) - len(body)] + PHONE_MARK
    return text


def redact(text: str) -> str:
    return _PHONE.sub(_phone, _EMAIL.sub(EMAIL_MARK, text))


def check_input(text: str, max_chars: int) -> tuple[InputRailResponse, list[str]]:
    """The reply and the names of the injection rules the text matched."""
    reasons = []
    if not any(c.isalpha() for c in text):
        reasons.append("the text is empty or has no words")
    if len(text) > max_chars:
        reasons.append(f"the text is longer than {max_chars} characters")
    injected = scan_text(text)
    if injected:
        reasons.append(
            "the text contains what looks like instructions to the system or hidden characters "
            f"({', '.join(injected)})"
        )
    plain = text.replace("\u2019", "'")
    if _ABUSE.search(plain):
        reasons.append("the text contains abusive language")
    if _HARMFUL.search(plain):
        reasons.append("the text asks for help with something harmful")
    if _OFF_TOPIC.search(plain):
        reasons.append("this is not a project idea; describe the project you want to check")
    reply = InputRailResponse(allowed=not reasons, reasons=reasons, redacted_text=redact(text))
    return reply, injected
