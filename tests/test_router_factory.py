"""Tests for config-driven router construction, plus the end-to-end proof that
enabling the LLM router changes nothing while the null provider is in use.
"""

import pytest
from conftest import ScriptedProvider

from src.agents.echo_agent import EchoAgent
from src.config.settings import Settings
from src.core.contracts import Task, TaskStatus
from src.manager.fallback_router import FallbackRouter
from src.manager.llm_router import LLMRouter
from src.manager.manager import ManagerAgent
from src.manager.router import CapabilityRouter, Router
from src.manager.router_factory import available_routers, build_router
from src.providers.null_provider import NullProvider


@pytest.fixture
def provider():
    return NullProvider()


class TestFactory:
    def test_capability_is_the_default(self, provider):
        assert isinstance(build_router(Settings(), provider), CapabilityRouter)

    def test_builds_a_fallback_wrapped_llm_router(self, provider):
        router = build_router(Settings(router="llm"), provider)
        assert isinstance(router, FallbackRouter)
        assert isinstance(router.primary, LLMRouter)
        assert isinstance(router.fallback, CapabilityRouter)

    def test_every_built_router_satisfies_the_interface(self, provider):
        for name in available_routers():
            assert isinstance(build_router(Settings(router=name), provider), Router)

    def test_lists_available_routers(self):
        assert set(available_routers()) == {"capability", "llm"}

    def test_rejects_an_unknown_router_with_a_helpful_message(self, provider):
        with pytest.raises(ValueError) as exc:
            build_router(Settings(router="telepathy"), provider)
        assert "telepathy" in str(exc.value)
        assert "capability" in str(exc.value)


class TestSettingsIntegration:
    def test_router_is_read_from_the_environment(self):
        assert Settings.from_env(env={"MANAGER_ROUTER": "LLM"}).router == "llm"

    def test_router_defaults_to_capability(self):
        assert Settings.from_env(env={}).router == "capability"

    def test_describe_includes_the_router(self):
        assert "router=capability" in Settings().describe()


class TestBehaviourIsUnchangedWithoutAModel:
    """The regression proof: MANAGER_ROUTER=llm is safe to enable today."""

    @staticmethod
    def _manager(router_name):
        settings = Settings(router=router_name)
        manager = ManagerAgent(router=build_router(settings, NullProvider()))
        manager.register_agent(EchoAgent(name="alpha_agent", capabilities={"email", "inbox"}))
        manager.register_agent(EchoAgent(name="beta_agent", capabilities={"calendar"}))
        return manager

    @pytest.mark.parametrize(
        "description,expected_agent,expected_status",
        [
            ("Read my inbox and summarise the unread email", "alpha_agent", TaskStatus.COMPLETED),
            ("Check my calendar for tomorrow", "beta_agent", TaskStatus.COMPLETED),
            ("Book me a flight to Lagos", None, TaskStatus.NO_AGENT_FOUND),
        ],
    )
    def test_llm_routing_matches_capability_routing(
        self, description, expected_agent, expected_status
    ):
        capability = self._manager("capability").handle_task(Task(description=description))
        llm = self._manager("llm").handle_task(Task(description=description))

        assert capability.status is llm.status is expected_status
        assert capability.agent_name == llm.agent_name == expected_agent


class TestTheModelCanOverrideRouting:
    """And the proof it is not merely decorative: a model that answers wins."""

    def test_a_model_answer_beats_capability_matching(self):
        router = FallbackRouter(
            primary=LLMRouter(ScriptedProvider("beta_agent")),
            fallback=CapabilityRouter(),
        )
        manager = ManagerAgent(router=router)
        manager.register_agent(EchoAgent(name="alpha_agent", capabilities={"email"}))
        manager.register_agent(EchoAgent(name="beta_agent", capabilities={"calendar"}))

        # Capability matching would pick alpha_agent for this wording.
        result = manager.handle_task(Task(description="send an email"))

        assert result.status is TaskStatus.COMPLETED
        assert result.agent_name == "beta_agent"

    def test_the_manager_still_never_imports_a_provider(self):
        import src.manager.manager as manager_module

        source = open(manager_module.__file__, encoding="utf-8").read()
        assert "provider" not in source.lower()
