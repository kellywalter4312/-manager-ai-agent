"""Demo: the Manager registers agents, diagnoses, delegates and assesses.

Run with:  python main.py

Note that this file, not the Manager, decides which agents exist. Wiring
concrete implementations together at the entry point (the "composition root")
keeps that decision in one visible place and keeps the Manager reusable.

The EmailAgent below is a real agent doing real work; beta_agent is still a
stand-in. Both are registered the same way, and the Manager cannot tell them
apart - which is the whole point of the BaseAgent contract.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from src.agents.echo_agent import EchoAgent
from src.agents.email.analyzers import JobOpportunityAnalyzer
from src.agents.brain_factory import build_specialist_brain, domain_for
from src.agents.email_agent import EmailAgent
from src.config.coherence import ConfigurationError, check_configuration
from src.config.settings import Settings
from src.core.contracts import Task
from src.manager.brain_factory import build_brain
from src.manager.cognition import assess_manager
from src.manager.manager import ManagerAgent
from src.manager.manager_skills import ManagerSkills
from src.manager.router_factory import build_router
from src.providers.factory import ProviderConfigurationError, build_provider
from src.registry.agent_registry import AgentRegistry
from src.services.identity.identity import load_identity
from src.services.email.mailbox_factory import build_mailbox
from src.services.profile.loader import load_profile
from src.services.profile.profile import ProfileError
from src.utils.logging import configure_logging


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """CLI options. Kept out of main() so main() stays callable with no
    arguments - several tests drive it directly, and argparse reading
    sys.argv under pytest would parse pytest's own flags."""
    parser = argparse.ArgumentParser(description="Run the Manager over a few demo tasks.")
    parser.add_argument(
        "--deliberate",
        action="store_true",
        help=(
            "also ask the Manager's brain what it makes of each task. "
            "Advisory only: the advice is printed, never executed."
        ),
    )
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> None:
    """``argv`` of None means *no arguments*, not "read sys.argv".

    The __main__ block below passes sys.argv[1:] explicitly. Defaulting to
    None-means-empty keeps main() safe to call from a test without argparse
    reaching for the process command line.
    """
    args = parse_args(argv or [])

    # Configuration is read once, here, and everything downstream receives it.
    # Set MANAGER_LOG_LEVEL=DEBUG to see every phase of the DATA loop.
    settings = Settings.from_env()

    # The application decides where log records go, and it decides once, here.
    # Nothing under src/ makes this choice for you.
    configure_logging(settings.log_level)

    # The provider goes to the router, never to the Manager. This is the only
    # place the two meet, and it is why the Manager stays provider-agnostic.
    # Set MANAGER_ROUTER=llm to put an LLMRouter in front of the capability
    # router; with the default null provider it will decline and the capability
    # router will answer, so behaviour is unchanged until a real model exists.
    # The provider goes to the router and the brain, never to the Manager. Both
    # receive a finished collaborator; neither the Manager nor anything it
    # imports learns that a provider exists.
    # Checked before anything is built: a provider nothing consumes, or an LLM
    # component with no model behind it, are both configurations that run
    # perfectly while doing something other than what was asked.
    try:
        check_configuration(settings)
        provider = build_provider(settings)
    except (ConfigurationError, ProviderConfigurationError) as exc:
        # A misconfiguration, not a failure. Reported as a readable line and a
        # non-zero exit rather than a traceback, and never quietly downgraded
        # to the null provider: silently running without the model someone
        # asked for is worse than refusing to start.
        print(f"configuration error: {exc}")
        raise SystemExit(2) from None

    router = build_router(settings, provider)

    # Set MANAGER_BRAIN=llm to give the Manager a reasoning capability. With the
    # default null provider it answers an honest "no view" and makes no network
    # call, so real reasoning needs MANAGER_PROVIDER as well - two deliberate
    # settings, not one.
    brain = build_brain(settings, provider)

    # The Manager's own capabilities are registered like any specialist, so the
    # existing router decides whether a mission is coordination work it should
    # handle itself or domain work belonging to a specialist.
    registry = AgentRegistry()
    identity = load_identity(settings.identity_path)
    manager = ManagerAgent(
        registry=registry,
        router=router,
        brain=brain,
        own_skills=ManagerSkills(identity, roster=registry.all_agents),
    )

    # The mailbox is chosen by configuration and injected into the agent, so
    # switching between the sample inbox and a real Gmail account is an
    # environment variable - not a code change. The default needs no
    # credentials; MANAGER_MAILBOX=gmail requires a one-time authorization.
    mailbox = build_mailbox(settings)

    # The profile is what turns detection into judgement. Without it the agent
    # still finds job emails, it just cannot say whether they suit you - so a
    # missing USER.md degrades to detection rather than stopping the program.
    try:
        profile = load_profile(settings.profile_path)
    except ProfileError as exc:
        print(f"note     : {exc}")
        profile = None

    # The specialist's own brain, built from its own setting and given its own
    # domain. It is not the Manager's brain and never shares one: two calls,
    # two objects, two areas of responsibility. Its memory is untouched by this
    # - EmailAgent still constructs its own store, as it always has.
    email_brain = build_specialist_brain(settings, provider, domain_for("email_agent"))
    manager.register_agent(
        EmailAgent(
            mailbox,
            analyzers=[JobOpportunityAnalyzer(profile=profile)],
            brain=email_brain,
        )
    )

    # Still a stand-in - nothing schedules calendars yet.
    manager.register_agent(EchoAgent(name="beta_agent", capabilities={"schedule", "calendar"}))

    tasks = [
        Task(description="Scan my inbox for job opportunities"),
        Task(description="Read my inbox and summarise the messages"),
        Task(description="Send an email to my team"),  # out of scope by design
        Task(description="Check my calendar for tomorrow"),
        Task(description="Book me a flight to Lagos"),  # no agent can do this
    ]

    print(f"settings : {settings.describe()}")
    key_note = "API key set" if settings.has_api_key else "no API key configured"
    print(f"provider : {provider.name} ({key_note})")
    print(f"router   : {type(router).__name__}")
    print(f"brain    : {type(brain).__name__}")
    print(f"email br.: {type(email_brain).__name__}")
    print(f"mailbox  : {settings.mailbox} ({type(mailbox).__name__})")

    # Which cognitive capabilities this run actually has. Reported rather than
    # enforced: both modes are valid operating states, and the Manager runs the
    # same either way. Half Brain still routes, delegates, assesses and recalls
    # - what it lacks is the model's judgement, so the line says so plainly
    # instead of leaving it to be inferred from the provider name above.
    cognition = assess_manager(manager)
    print(f"cognition: {cognition.describe()}")
    if cognition.missing:
        print(f"           without: {', '.join(cognition.missing)}")
    print("-" * 60)

    if args.deliberate:
        # Deliberation is off by default, and that is the safety boundary made
        # visible: without the flag this run is byte-for-byte what it was
        # before. Note what is deliberately NOT here - nothing acts on the
        # advice. The brain advises, the Manager reports, a human decides.
        print("deliberation is ON: advice is printed, never executed")
        print("-" * 60)

    for task in tasks:
        result = manager.handle_task(task)
        print(f"task     : {task.description}")
        print(f"status   : {result.status.value}")
        print(f"agent    : {result.agent_name}")
        print(f"output   : {result.output or '-'}")
        print(f"error    : {result.error or '-'}")

        if args.deliberate:
            # The existing seam, unchanged: deliberate() is separate from
            # handle_task on purpose, so asking for judgement cannot alter the
            # mechanical routing and assessment that already ran above.
            #
            # It never raises - a provider failure, an unreadable answer or no
            # brain at all all arrive as an UNKNOWN decision that says why. So
            # Half Brain prints an honest "NO VIEW" here rather than an error,
            # which is the point: the Manager is not broken without a model.
            advice = manager.deliberate(task)
            print(f"advice   : {advice.action.label}")
            print(f"           {advice.reason}")
            print(
                f"           confidence={advice.confidence:.2f} "
                f"via={advice.source or '-'}"
            )
            if advice.delegate_to:
                print(f"           suggests: {advice.delegate_to}")
            for step in advice.steps:
                print(f"           - {step}")

        print("-" * 60)


if __name__ == "__main__":
    main(sys.argv[1:])
