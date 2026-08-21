"""Checks that a configuration means what whoever wrote it thought it meant.

Settings validates each value on its own: a log level is a real level, a model
name is not blank. What it deliberately does not do - see the closing note in
settings.py - is decide whether the names refer to things that exist, because
that would couple configuration to everything it configures.

This module handles the case in between: values that are individually valid and
jointly incoherent. There are two, and both were silent until now.

    MANAGER_PROVIDER=anthropic          a provider nothing consumes
    (router and brain both left off)    -> built, then discarded

    MANAGER_ROUTER=llm                  a consumer with no model behind it
    MANAGER_PROVIDER=null               -> declines every task, forever

Neither crashes. Both produce a system that runs, answers, and looks correct
while doing something other than what was asked - the first never calls the
model it was configured with, the second falls back to deterministic routing on
every single task. That is the failure mode this project already refuses
elsewhere: a misconfiguration must not be able to disguise itself as ordinary
operation.

Two deliberate constraints on where this lives:

- **Not in the factories.** build_provider builds a provider; whether anything
  will use it is not its business, and putting the check there would make
  constructing a provider in isolation - which every test does - illegal.
- **Not in Settings.** It would have to reason about routers and brains, which
  is the coupling settings.py explicitly avoids. This module knows only the
  configured *names*, as strings, and imports neither package.

It is called from the composition roots (main.py, scripts/heartbeat.py), which
is the one place where the whole configuration is known and nothing has been
built yet.
"""

from __future__ import annotations

from src.config.settings import Settings

# The name meaning "no model". Compared as a string on purpose: importing the
# provider registry to ask would rebuild the coupling this module exists to
# avoid.
NO_PROVIDER = "null"

# The component names that require a model to be useful.
LLM_COMPONENT = "llm"


class ConfigurationError(RuntimeError):
    """Raised when settings are individually valid but jointly incoherent.

    A sibling of ProviderConfigurationError, and separate from it on purpose:
    that one means "this provider cannot be built", this one means "this
    combination will not do what you asked". Both are startup problems, so the
    composition roots handle them the same way - a readable line and a non-zero
    exit, never a traceback.
    """


def check_configuration(settings: Settings) -> None:
    """Raise ConfigurationError if the configured components cannot work together.

    Silent for every valid configuration, including the default. The default -
    provider=null, router=capability, brain=null - is coherent by design: no
    model is configured and nothing asks for one.
    """
    provider = settings.provider
    consumers = [
        name
        for name, value in (
            ("MANAGER_ROUTER", settings.router),
            ("MANAGER_BRAIN", settings.brain),
            # A specialist brain consumes the provider exactly as the other two
            # do. Leaving it out would make MANAGER_PROVIDER=anthropic with
            # MANAGER_SPECIALIST_BRAIN=llm fail as "nothing uses it" - refusing
            # to start a configuration that is in fact correct.
            ("MANAGER_SPECIALIST_BRAIN", settings.specialist_brain),
        )
        if value == LLM_COMPONENT
    ]

    if provider != NO_PROVIDER and not consumers:
        raise ConfigurationError(
            f"MANAGER_PROVIDER={provider} is configured, but nothing uses it: "
            f"MANAGER_ROUTER={settings.router}, MANAGER_BRAIN={settings.brain} and "
            f"MANAGER_SPECIALIST_BRAIN={settings.specialist_brain} are all "
            "deterministic, so the provider would be built and then discarded, "
            "and no model would ever be called. Set MANAGER_ROUTER=llm to route "
            "with the model, MANAGER_BRAIN=llm to give the Manager a reasoning "
            "capability, MANAGER_SPECIALIST_BRAIN=llm to give the specialists "
            f"theirs, or MANAGER_PROVIDER={NO_PROVIDER} to run deterministically "
            "on purpose."
        )

    if provider == NO_PROVIDER and consumers:
        selected = " and ".join(f"{name}={LLM_COMPONENT}" for name in consumers)
        raise ConfigurationError(
            f"{selected} is configured, but MANAGER_PROVIDER={NO_PROVIDER}, so "
            "there is no model behind it. It would decline on every task and "
            "the deterministic fallback would answer instead - indistinguishable "
            "from routing that was never configured at all. Set "
            "MANAGER_PROVIDER=anthropic (and an API key), or leave the "
            "deterministic default in place."
        )

    # Note what is never mentioned above: the API key. Whether one exists is
    # build_provider's question, and its value must not reach a message that
    # is printed to a terminal and pasted into a bug report.
