"""Pulling structured opportunity details out of unstructured email.

Source-agnostic by design. Nothing here knows what Wellfound is: it recognises
*shapes* that job emails share regardless of platform - labelled lines
("Salary: ..."), subject patterns ("<role> at <company>"), forwarded blocks,
and quoted replies. A platform is at most a hint read from a header, never a
requirement.

Every extractor returns None when it cannot read a field. That is the whole
point: a missing salary is reported as unknown, never invented.

All functions are pure - text in, values out - so they can be tested directly
with no mailbox, no agent and no profile.
"""

from __future__ import annotations

import re

from src.services.email.message import EmailMessage

# ---------------------------------------------------------------------------
# Source / platform detection
# ---------------------------------------------------------------------------

# Hints only. An unknown sender is fine; source stays None and nothing breaks.
_KNOWN_SOURCES: tuple[tuple[str, str], ...] = (
    ("wellfound", "Wellfound"),
    ("angel.co", "Wellfound"),
    ("upwork", "Upwork"),
    ("fiverr", "Fiverr"),
    ("freelancer", "Freelancer"),
    ("toptal", "Toptal"),
    ("linkedin", "LinkedIn"),
    ("indeed", "Indeed"),
    ("otta", "Otta"),
    ("hired", "Hired"),
    ("glassdoor", "Glassdoor"),
    ("remoteok", "RemoteOK"),
    ("weworkremotely", "We Work Remotely"),
)

_FORWARD_MARKERS = (
    "---------- forwarded message ----------",
    "begin forwarded message",
    "-----original message-----",
)

_QUOTE_LINE = re.compile(r"^\s*>+\s?", re.MULTILINE)


def detect_source(message: EmailMessage) -> str | None:
    """Identify the platform from headers, sender, or body markers.

    Headers first: List-Id and similar are set by the platform itself and
    survive forwarding better than a sender address does.
    """
    haystacks = [
        message.header("list-id"),
        message.header("list-unsubscribe"),
        message.header("x-mailer"),
        message.sender,
        message.subject,
        message.body[:2000],
    ]
    blob = " ".join(h for h in haystacks if h).lower()
    for needle, name in _KNOWN_SOURCES:
        if needle in blob:
            return name
    return None


def is_forwarded(message: EmailMessage) -> bool:
    lowered = message.body.lower()
    return message.subject.lower().startswith(("fwd:", "fw:")) or any(
        marker in lowered for marker in _FORWARD_MARKERS
    )


def readable_text(message: EmailMessage) -> str:
    """Subject plus body, with quote markers stripped.

    Quoted and forwarded content is kept, not discarded: in a forwarded posting
    or a reply thread the actual opportunity usually lives *inside* the quote.
    Only the '>' decoration is removed so patterns still match.
    """
    return f"{message.subject}\n\n{_QUOTE_LINE.sub('', message.body)}"


# ---------------------------------------------------------------------------
# Splitting digests into individual postings
# ---------------------------------------------------------------------------

# "Senior Python Engineer at Northwind Labs" on a line of its own.
_ROLE_AT_COMPANY_LINE = re.compile(
    r"^[ \t]*(?P<role>[\w/+#.\-' ]{3,70}?)\s+(?:at|@|with)\s+(?P<company>[\w&.\-' ]{2,60})[ \t]*$",
    re.MULTILINE,
)

_ROLE_WORDS = (
    "engineer", "developer", "designer", "analyst", "manager", "scientist",
    "architect", "consultant", "specialist", "intern", "programmer", "lead",
)


def split_opportunity_blocks(text: str) -> list[str]:
    """Split a digest into one block per posting.

    A single email from a job board routinely advertises several roles. Without
    this, five opportunities would collapse into one finding and four would be
    invisible.

    A block boundary is a line that looks like a job headline. Fewer than two
    boundaries means it is an ordinary single-opportunity email.
    """
    boundaries = [
        match.start()
        for match in _ROLE_AT_COMPANY_LINE.finditer(text)
        if any(word in match.group("role").lower() for word in _ROLE_WORDS)
    ]
    if len(boundaries) < 2:
        return [text]

    blocks: list[str] = []
    for index, start in enumerate(boundaries):
        end = boundaries[index + 1] if index + 1 < len(boundaries) else len(text)
        block = text[start:end].strip()
        if block:
            blocks.append(block)
    return blocks or [text]


# ---------------------------------------------------------------------------
# Field extractors
# ---------------------------------------------------------------------------

_SUBJECT_NOISE = re.compile(
    r"^\s*(re|fwd|fw|opportunity|job alert|new job|job)\s*[:\-]\s*", re.IGNORECASE
)

# Platforms brand their subject lines: "<role> at <company> - Wellfound".
# Left in place, the brand is swallowed into the company name.
_PLATFORM_NAMES = tuple(name for _, name in _KNOWN_SOURCES) + ("LinkedIn Jobs", "Indeed.com")

# A leading label a platform prepends to the real subject, e.g.
# "New role for you: Backend Engineer at Acme". Stripped only when the prefix
# is short and contains no role word of its own, so a genuine title such as
# "Senior Engineer: Platform Team" survives intact.
_LEADING_LABEL = re.compile(r"^\s*(?P<prefix>[^:\n]{1,40}):\s+(?P<rest>\S.*)$")

# Lines that are never a job title, used by the first-line fallback.
_GREETING = re.compile(r"^\s*(hi|hello|hey|dear|good (morning|afternoon|evening))\b", re.IGNORECASE)
_MOSTLY_SYMBOLS = re.compile(r"^[\W\d_]+$")

# The first-line fallback only fires for a line that actually reads like a
# posting - a role word, or an explicit demand. Without this it will happily
# title a newsletter ("This week in software"), which is inventing information
# rather than extracting it.
_DEMAND_WORDS = (
    "needed", "wanted", "required", "hiring", "seeking", "looking for",
    "sought", "vacancy", "opening", "urgent",
)


# Whole-word matching. Substring matching here let "International" match the
# role word "intern", which is how a newsletter masthead line was accepted as
# a job title on the real Gmail run.
_POSTING_CUE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in _ROLE_WORDS + _DEMAND_WORDS) + r")\b",
    re.IGNORECASE,
)

# "Deputy International Editor: Adam Pasick" - a masthead byline. The colon
# separates a role from a PERSON, not from an employer, so reading the line as
# a job title invents an opportunity out of a newsletter credit.
_PERSON_BYLINE = re.compile(
    r"^[^:]{3,60}:\s*(?P<person>[A-Z][\w'’\-]+(?:\s+[A-Z][\w'’\-]+){1,2})\s*$"
)

# Words that mark the right-hand side as an organisation or a team rather than
# a person, so "Senior Engineer: Platform Team" is still read as a title.
_ORG_HINTS = (
    "team", "ltd", "limited", "inc", "llc", "gmbh", "labs", "systems",
    "technologies", "group", "studio", "agency", "solutions", "consulting",
    "media", "digital", "partners", "ventures", "capital", "works",
)


def _is_person_byline(line: str) -> bool:
    """Whether a line is '<role>: <person name>' rather than a job title."""
    match = _PERSON_BYLINE.match(line.strip())
    if not match:
        return False
    person = match.group("person").lower()
    if any(hint in person for hint in _ORG_HINTS):
        return False
    return not _POSTING_CUE.search(person)


def _reads_like_a_posting(line: str) -> bool:
    """Whether a line reads like a job headline rather than prose.

    A headline is short and unpunctuated; a sentence that happens to mention
    hiring ("...a note on how one startup is hiring.") is not a job title, and
    treating it as one would be inventing information.
    """
    if len(line.split()) > 12 or line.rstrip().endswith((".", "!", "?")):
        return False
    if _is_person_byline(line):
        return False
    return bool(_POSTING_CUE.search(line))


def _strip_platform_suffix(value: str, source: str | None = None) -> str:
    """Remove a trailing platform brand from a title or company name."""
    cleaned = value.strip()
    candidates = list(_PLATFORM_NAMES)
    if source:
        candidates.append(source)

    for _ in range(3):  # a subject may carry more than one trailing brand
        before = cleaned
        for brand in candidates:
            pattern = re.compile(
                rf"[\s]*[-|–—:]\s*{re.escape(brand)}\s*$", re.IGNORECASE
            )
            cleaned = pattern.sub("", cleaned)
        cleaned = cleaned.strip(" -|–—:")
        if cleaned == before:
            break
    return cleaned


def clean_subject(subject: str, source: str | None = None) -> str:
    """Strip platform decoration from a subject line.

    Removes the "Re:/Fwd:/Job alert:" style prefixes, a platform's own leading
    label, and any trailing brand suffix - leaving the part that actually
    describes the role.
    """
    cleaned = _SUBJECT_NOISE.sub("", subject or "").strip()

    label = _LEADING_LABEL.match(cleaned)
    if label:
        prefix = label.group("prefix").lower()
        # Keep the prefix when it looks like part of the role itself.
        if not any(word in prefix for word in _ROLE_WORDS):
            cleaned = label.group("rest").strip()

    return _strip_platform_suffix(cleaned, source)


def _labelled(text: str, *labels: str) -> str | None:
    """Read a 'Label: value' line, tolerating spacing and case."""
    for label in labels:
        pattern = re.compile(
            rf"^[ \t>*\-]*{re.escape(label)}\s*[:\-]\s*(?P<value>.+?)\s*$",
            re.IGNORECASE | re.MULTILINE,
        )
        match = pattern.search(text)
        if match:
            value = match.group("value").strip(" .;")
            if value and value.lower() not in ("n/a", "none", "not specified", "tbd"):
                return value
    return None


def first_line_title(text: str, source: str | None = None) -> str | None:
    """Take the opening headline of a posting block as its title.

    Marketplace listings (Freelancer and similar) lead with the project title
    on its own line and never use an "X at Y" shape or a "Title:" label - so
    without this they extract no title at all and surface as "Untitled
    opportunity", which is what the first real Gmail run produced.

    Guarded so that greetings, labelled fields, URLs, prices and decoration
    are never mistaken for a title.
    """
    for raw in (text or "").splitlines():
        line = _strip_platform_suffix(raw.strip(" *#>-"), source)
        if not line or len(line) > 90:
            continue
        if _GREETING.match(line) or _MOSTLY_SYMBOLS.match(line):
            continue
        if line.startswith(("http://", "https://", "www.")):
            continue
        # "Skills: ..." and friends are fields, not headlines.
        if _LEADING_LABEL.match(line) and len(line.split(":", 1)[0]) <= 25:
            continue
        if len(line.split()) < 2:
            continue
        if not re.search(r"[A-Za-z]{3}", line):
            continue
        if not _reads_like_a_posting(line):
            continue
        return line
    return None


def extract_title_and_company(
    text: str, subject: str, source: str | None = None
) -> tuple[str | None, str | None]:
    """Read role and company from a labelled line, a headline, or the subject.

    ``source`` lets the platform's own brand be stripped from whatever is
    found: a subject like "<role> at <company> - Wellfound" otherwise yields a
    company of "<company> - Wellfound".
    """
    title = _labelled(text, "role", "title", "job title", "position", "project")
    company = _labelled(text, "company", "client", "employer", "organisation", "organization")
    if title and company:
        return _strip_platform_suffix(title, source), _strip_platform_suffix(company, source)

    for candidate in (text, clean_subject(subject, source)):
        match = _ROLE_AT_COMPANY_LINE.search(candidate)
        if match:
            found_role = _strip_platform_suffix(" ".join(match.group("role").split()), source)
            found_company = _strip_platform_suffix(
                " ".join(match.group("company").split()).rstrip(".,"), source
            )
            return (title or found_role or None), (company or found_company or None)

    if not title:
        cleaned = clean_subject(subject, source)
        if cleaned and any(word in cleaned.lower() for word in _ROLE_WORDS):
            title = cleaned

    if not title:
        # Last resort: the block's own opening line.
        title = first_line_title(text, source)

    # A byline reaching this point came from the subject rather than the body,
    # so the same guard applies here: better no title than a person's name.
    if title and _is_person_byline(title):
        return None, company

    return (_strip_platform_suffix(title, source) if title else None), company


def extract_skills(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return (required, preferred) skills from labelled lines.

    Only labelled lists are trusted. Harvesting every technology word from the
    prose would invent requirements the posting never stated - and those would
    then be reported as gaps in the user's profile.
    """
    required = _labelled(
        text, "required skills", "requirements", "must have", "skills required",
        "you have", "we require", "essential", "tech stack", "stack", "skills",
    )
    preferred = _labelled(
        text, "preferred skills", "nice to have", "bonus", "desirable",
        "preferred", "plus", "advantageous",
    )
    return _split_skills(required), _split_skills(preferred)


def _split_skills(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    parts = re.split(r"[,/;•|]| and ", value)
    skills = []
    for part in parts:
        cleaned = part.strip(" .;:-*").lower()
        # Drop prose fragments; skill names are short.
        if cleaned and 1 < len(cleaned) <= 30 and len(cleaned.split()) <= 3:
            skills.append(cleaned)
    return tuple(dict.fromkeys(skills))


def extract_seniority(text: str) -> str | None:
    labelled = _labelled(text, "seniority", "level", "experience level")
    if labelled:
        return labelled
    match = re.search(
        r"\b(intern|graduate|junior|entry[- ]level|mid[- ]level|senior|lead|principal)\b",
        text,
        re.IGNORECASE,
    )
    return match.group(1).lower() if match else None


def extract_experience(text: str) -> str | None:
    labelled = _labelled(text, "experience", "experience required", "years of experience")
    if labelled:
        return labelled
    match = re.search(r"(\d+\+?\s*(?:-\s*\d+\s*)?years?[\w \-]*experience)", text, re.IGNORECASE)
    return match.group(1).strip() if match else None


def extract_salary(text: str) -> str | None:
    labelled = _labelled(text, "salary", "rate", "compensation", "pay", "budget", "day rate")
    if labelled:
        return labelled
    match = re.search(
        r"([£$€]\s?\d[\d,.]*\s*(?:k)?(?:\s*(?:-|to)\s*[£$€]?\s?\d[\d,.]*\s*(?:k)?)?"
        r"(?:\s*(?:per|/)\s*(?:hour|hr|day|year|annum|month))?)",
        text,
        re.IGNORECASE,
    )
    return match.group(1).strip() if match else None


def extract_deadline(text: str) -> str | None:
    labelled = _labelled(text, "deadline", "closing date", "apply by", "closes", "applications close")
    if labelled:
        return labelled
    match = re.search(
        r"((?:apply by|closes on|closing)\s+[\w ,]{3,30})", text, re.IGNORECASE
    )
    return match.group(1).strip() if match else None


def extract_location(text: str) -> str | None:
    labelled = _labelled(text, "location", "based in", "office", "where")
    if labelled:
        return labelled
    match = re.search(r"\b(fully remote|remote[- ]first|remote|hybrid|on[- ]site)\b", text, re.IGNORECASE)
    return match.group(1).lower() if match else None


def extract_employment_type(text: str) -> str | None:
    labelled = _labelled(text, "type", "employment type", "contract type", "job type")
    if labelled:
        return labelled
    match = re.search(
        r"\b(full[- ]time|part[- ]time|freelance|contract|permanent|internship|temporary)\b",
        text,
        re.IGNORECASE,
    )
    return match.group(1).lower() if match else None


def extract_apply_link(text: str) -> str | None:
    match = re.search(r"https?://[^\s>)\]]+", text)
    return match.group(0).rstrip(".,);") if match else None


def extract_description(text: str, limit: int = 400) -> str:
    """A trimmed excerpt used for red-flag checks and context."""
    collapsed = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    return collapsed[:limit]
