"""Realistic email fixtures and a fixed test profile.

The profile here is deliberately NOT the real USER.md: tests must not depend on
personal data, and must not break when the user edits their own profile.

Every address uses the reserved .example domain and every company is invented.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.services.email.message import EmailMessage
from src.services.profile.profile import Project, SkillTier, UserProfile

BASE = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)

TEST_PROFILE = UserProfile(
    skills={
        "python": SkillTier.STRONG,
        "testing": SkillTier.STRONG,
        "apis": SkillTier.STRONG,
        "javascript": SkillTier.WORKING,
        "node.js": SkillTier.WORKING,
        "html": SkillTier.WORKING,
        "css": SkillTier.WORKING,
        "docker": SkillTier.LEARNING,
        "kubernetes": SkillTier.LEARNING,
    },
    projects=(
        Project("Orchestrator", "multi-agent system", frozenset({"python", "testing", "apis"})),
        Project("Salon Site", "static site", frozenset({"html", "css", "javascript"})),
    ),
    seniority="junior",
    targets=frozenset({"python development", "junior software development", "automation"}),
    hard_filters=("no unpaid work",),
)


def email(
    id_,
    sender,
    subject,
    body,
    minutes=0,
    labels=(),
    headers=None,
) -> EmailMessage:
    return EmailMessage(
        id=id_,
        sender=sender,
        subject=subject,
        body=body,
        received_at=BASE + timedelta(minutes=minutes),
        labels=frozenset(labels),
        headers=headers or {},
    )


# --------------------------------------------------------------------------
# Opportunity emails - varied sources and formats
# --------------------------------------------------------------------------

WELLFOUND_DIGEST = email(
    "wf-1",
    "jobs@wellfound.example",
    "5 new jobs matching your search",
    """Here are this week's matches.

Junior Python Developer at Aurora Systems
Skills: Python, APIs, testing
Seniority: Junior
Salary: £32,000 - £38,000
Location: Remote
Type: Full-time
Apply: https://wellfound.example/jobs/1

Senior Rust Engineer at Ironclad
Skills: Rust, Kubernetes, Terraform
Seniority: Senior
Salary: £95,000
Location: On-site
Apply: https://wellfound.example/jobs/2

Automation Engineer at Brightpath
Skills: Python, JavaScript, Docker
Seniority: Mid
Location: Hybrid
Apply: https://wellfound.example/jobs/3
""",
    minutes=10,
    headers={"List-Id": "<jobs.wellfound.example>", "Precedence": "bulk"},
)

FREELANCE_PLATFORM = email(
    "fl-1",
    "no-reply@upwork.example",
    "New project matching your profile",
    """A new project has been posted.

Title: Build a REST API for a booking system
Client: Meadow Consulting
Required skills: Python, APIs
Nice to have: Docker
Budget: $2,000
Type: Freelance
Deadline: apply by 30 August
Location: Remote
https://upwork.example/jobs/abc
""",
    minutes=20,
)

FORWARDED_POSTING = email(
    "fw-1",
    "sam@friends.example",
    "Fwd: Junior Developer at Larkspur Digital",
    """Thought of you!

---------- Forwarded message ----------
From: careers@larkspur.example

Junior Developer at Larkspur Digital
We are hiring a junior developer to join our platform team.
Required skills: JavaScript, Node.js, APIs
Seniority: Junior
Salary: £30,000
Location: Remote
Apply by 12 September
""",
    minutes=30,
)

RECRUITER_EMAIL = email(
    "rec-1",
    "hannah@talentbridge.example",
    "Opportunity: Python Engineer at Vantage Health",
    """Hi,

I am recruiting for a role and thought you might be interested.

Role: Python Engineer
Company: Vantage Health
Requirements: Python, testing, APIs
Experience: 1-2 years experience
Rate: £250 per day
Type: Contract
Location: Fully remote

Let me know if you would like the full spec.
""",
    minutes=40,
)

QUOTED_THREAD = email(
    "th-1",
    "colleague@work.example",
    "Re: that role I mentioned",
    """Here's the detail you asked for.

> Role: Automation Developer
> Company: Cobalt Labs
> Skills: Python, testing
> Seniority: Junior
> Salary: £34,000
> Location: Remote
""",
    minutes=50,
)

STRETCH_ROLE = email(
    "st-1",
    "careers@zenith.example",
    "Senior Python Engineer at Zenith Data",
    """We are hiring.

Role: Senior Python Engineer
Company: Zenith Data
Required skills: Python, APIs, testing
Seniority: Senior
Experience: 5+ years experience
Salary: £80,000
Location: Remote
""",
    minutes=60,
)

POOR_MATCH = email(
    "pm-1",
    "jobs@ironworks.example",
    "Kubernetes Platform Engineer at Ironworks",
    """We are hiring a platform engineer.

Role: Kubernetes Platform Engineer
Company: Ironworks
Required skills: Rust, Terraform, Go
Seniority: Senior
Salary: £90,000
""",
    minutes=70,
)

UNPAID_ROLE = email(
    "up-1",
    "founder@startupdream.example",
    "Developer at StartupDream",
    """Join our journey!

Role: Developer
Company: StartupDream
Required skills: Python, JavaScript
This is an unpaid position for exposure, with equity later.
Seniority: Junior
""",
    minutes=80,
)

VAGUE_OPPORTUNITY = email(
    "vg-1",
    "recruiting@shadow.example",
    "Exciting opportunity for a developer",
    """We have a great opening for a developer.
Interested candidates should apply. Salary competitive.
""",
    minutes=90,
)

NO_SALARY_NO_DEADLINE = email(
    "ns-1",
    "careers@quietco.example",
    "Python Developer at QuietCo",
    """Role: Python Developer
Company: QuietCo
Required skills: Python, testing
Seniority: Junior
Location: Remote
""",
    minutes=100,
)

# --------------------------------------------------------------------------
# Unrelated emails - must not be reported as opportunities
# --------------------------------------------------------------------------

RECEIPT = email(
    "un-1",
    "no-reply@grocerybox.example",
    "Your weekly delivery is confirmed",
    "Your order arrives Thursday between 9am and 11am.",
    minutes=5,
)

NEWSLETTER = email(
    "un-2",
    "digest@devweekly.example",
    "This week in software",
    "Three articles on database design, plus a note on how one startup is hiring.",
    minutes=6,
)

PERSONAL = email(
    "un-3",
    "sam@friends.example",
    "Coffee on Saturday?",
    "Are you free around 11? There's a new place near the station.",
    minutes=7,
)

SECURITY_ALERT = email(
    "un-4",
    "security@bank.example",
    "New sign-in to your account",
    "We noticed a new sign-in from a new device. If this was you, no action is needed.",
    minutes=8,
)

OPPORTUNITY_EMAILS = (
    WELLFOUND_DIGEST,
    FREELANCE_PLATFORM,
    FORWARDED_POSTING,
    RECRUITER_EMAIL,
    QUOTED_THREAD,
    STRETCH_ROLE,
    POOR_MATCH,
    UNPAID_ROLE,
    NO_SALARY_NO_DEADLINE,
)

UNRELATED_EMAILS = (RECEIPT, NEWSLETTER, PERSONAL, SECURITY_ALERT)

ALL_EMAILS = OPPORTUNITY_EMAILS + UNRELATED_EMAILS + (VAGUE_OPPORTUNITY,)
