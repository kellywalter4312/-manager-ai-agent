"""Application configuration.

One frozen object holding every tunable value, built once at startup and passed
down. The alternative - modules reading ``os.environ`` wherever they happen to
need something - scatters configuration across the codebase, makes tests fight
over global state, and hides what the application actually depends on.

Precedence, highest first:

    1. an explicit argument passed by the caller
    2. an environment variable
    3. the declared default

That ordering is what lets a test override one value without touching the
process environment, and lets deployment override without touching code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from typing import Mapping

# Prefixed so the orchestrator cannot collide with unrelated tools that also
# read LOG_LEVEL or PROVIDER from the environment.
ENV_PREFIX = "MANAGER_"

# The level names Python's logging module understands.
VALID_LOG_LEVELS = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"})

_TRUE = frozenset({"true", "yes", "on", "1"})
_FALSE = frozenset({"false", "no", "off", "0", ""})


def _as_bool(value: str, name: str) -> bool:
    """Parse a boolean setting strictly.

    An unrecognised value raises rather than defaulting: silently reading
    "maybe" as True would be a way to turn on autonomy by accident.
    """
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"{name} must be true or false, got {value!r}")


def _as_positive_int(value: str, name: str) -> int:
    try:
        number = int(str(value).strip())
    except ValueError:
        raise ValueError(f"{name} must be a whole number, got {value!r}") from None
    if number <= 0:
        raise ValueError(f"{name} must be positive, got {number}")
    return number


@dataclass(frozen=True)
class Settings:
    """Validated application settings.

    Frozen for the same reason Task is: configuration read at startup should
    not change under a running system. If a value is wrong, the program should
    fail loudly at startup rather than behave strangely an hour later.
    """

    log_level: str = "INFO"
    provider: str = "null"
    router: str = "capability"
    # Reasoning is off by default, matching the router's conservative default.
    # "llm" is still safe: the brain receives whatever build_provider returned,
    # which is the null provider unless a provider was deliberately configured.
    brain: str = "null"
    # The specialists' own reasoning, configured separately from the Manager's.
    # Separate because they are separate capabilities: a deployment may want the
    # orchestrator to deliberate while its specialists stay deterministic, or
    # the reverse. One switch for both would make that impossible to express.
    specialist_brain: str = "null"
    model: str = "claude-opus-5"
    mailbox: str = "memory"
    profile_path: str = "USER.md"
    identity_path: str = "IDENTITY.md"
    soul_path: str = "SOUL.md"

    # Heartbeat. Disabled by default: autonomy is opt-in, never a side effect
    # of upgrading. Declared as str because from_env() reads environment
    # variables, which are always strings; __post_init__ coerces them - a raw
    # "false" would otherwise be truthy, which is exactly the kind of silent
    # failure a safety switch must not have.
    heartbeat_enabled: str = "false"
    heartbeat_interval_minutes: str = "60"
    heartbeat_fetch_limit: str = "25"
    state_path: str = "data/state/processed.json"
    reports_dir: str = "data/reports"

    # Paths to credential files - NOT the credentials themselves. Both live
    # under data/, which is gitignored. Storing a path here means a secret
    # never enters configuration, logs, or a crash report.
    gmail_credentials_path: str = "data/credentials.json"
    gmail_token_path: str = "data/token.json"

    # A secret. `repr=False` keeps it out of the auto-generated __repr__, so a
    # Settings object printed in a traceback or debugger cannot leak the key.
    # describe() masks it separately for the same reason.
    api_key: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        level = self.log_level.strip().upper()
        if level not in VALID_LOG_LEVELS:
            raise ValueError(
                f"Invalid log_level {self.log_level!r}. "
                f"Expected one of: {', '.join(sorted(VALID_LOG_LEVELS))}"
            )
        object.__setattr__(self, "log_level", level)

        provider = self.provider.strip().lower()
        if not provider:
            raise ValueError("Settings.provider must not be empty")
        object.__setattr__(self, "provider", provider)

        router = self.router.strip().lower()
        if not router:
            raise ValueError("Settings.router must not be empty")
        object.__setattr__(self, "router", router)

        brain = self.brain.strip().lower()
        if not brain:
            raise ValueError("Settings.brain must not be empty")
        object.__setattr__(self, "brain", brain)

        specialist_brain = self.specialist_brain.strip().lower()
        if not specialist_brain:
            raise ValueError("Settings.specialist_brain must not be empty")
        object.__setattr__(self, "specialist_brain", specialist_brain)

        mailbox = self.mailbox.strip().lower()
        if not mailbox:
            raise ValueError("Settings.mailbox must not be empty")
        object.__setattr__(self, "mailbox", mailbox)

        model = self.model.strip()
        if not model:
            raise ValueError("Settings.model must not be empty")
        # Not lowercased: model identifiers are opaque strings owned by the
        # provider, so normalizing their case could invent an invalid name.
        object.__setattr__(self, "model", model)

        # An empty or whitespace-only key is treated as "not supplied" rather
        # than as a key, so a blank environment variable cannot be mistaken for
        # a credential and produce a confusing auth failure later.
        api_key = (self.api_key or "").strip()
        object.__setattr__(self, "api_key", api_key or None)

        # Note what is deliberately NOT validated here: whether `provider` and
        # `router` name things that actually exist. Settings would have to
        # import those packages to know, coupling configuration to the things
        # it configures. Resolving names is each factory's job.

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, **overrides: str | None) -> "Settings":
        """Build Settings from environment variables, with optional overrides.

        ``env`` is injectable so tests can pass a plain dict instead of mutating
        ``os.environ`` - global mutable state is a common source of tests that
        pass alone and fail together.
        """
        source = os.environ if env is None else env

        values: dict[str, str] = {}
        for field in fields(cls):
            env_value = source.get(f"{ENV_PREFIX}{field.name.upper()}")
            if env_value is not None:
                values[field.name] = env_value

        # Explicit arguments win over the environment. `None` means "not
        # specified", so it never overrides anything.
        values.update({k: v for k, v in overrides.items() if v is not None})

        return cls(**values)

    # ------------------------------------------------------------------
    # Typed accessors
    #
    # Environment variables are strings, so the raw fields are strings and the
    # coercion happens here, once, with validation. Reading `settings.
    # heartbeat_enabled` directly would be a bug waiting to happen: the string
    # "false" is truthy.
    # ------------------------------------------------------------------

    @property
    def heartbeat_is_enabled(self) -> bool:
        return _as_bool(self.heartbeat_enabled, "MANAGER_HEARTBEAT_ENABLED")

    @property
    def heartbeat_interval(self) -> int:
        return _as_positive_int(
            self.heartbeat_interval_minutes, "MANAGER_HEARTBEAT_INTERVAL_MINUTES"
        )

    @property
    def heartbeat_limit(self) -> int:
        return _as_positive_int(
            self.heartbeat_fetch_limit, "MANAGER_HEARTBEAT_FETCH_LIMIT"
        )

    @property
    def has_api_key(self) -> bool:
        """Whether a key was supplied, without exposing its value."""
        return self.api_key is not None

    def describe(self) -> str:
        """A short one-line summary, safe to log.

        The API key is reported as present or absent and never printed.
        Configuration routinely ends up in log files and crash reports, so a
        secret must never be rendered here - only the fact that one was set.
        """
        return (
            f"log_level={self.log_level} provider={self.provider} "
            f"router={self.router} brain={self.brain} "
            f"specialist_brain={self.specialist_brain} model={self.model} "
            f"mailbox={self.mailbox} "
            f"api_key={'set' if self.has_api_key else 'unset'}"
        )
