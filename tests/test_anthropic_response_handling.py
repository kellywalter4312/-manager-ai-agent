"""Tests for what the Claude provider does with responses it did not expect.

The companion file test_anthropic_provider.py covers the happy path: a
well-formed SDK response becoming a Completion. This one covers the other side
of the same boundary - a response whose *shape* is wrong.

That boundary matters because of a trust rule the architecture depends on:
model output is untrusted. A vendor response is untrusted too, and the two
failures it can cause are different in kind:

    corrupt content  -> ProviderResponseError, so it is visible
    odd telemetry    -> normalised to None, so it is harmless

Getting that split backwards is the bug these tests exist to prevent. If a
garbled response returned empty text instead of raising, it would arrive at
LLMManagerBrain as an UNKNOWN decision - indistinguishable from a model that
simply had nothing to say. That is the same class of bug the missing-key check
in the factory was written to kill, one layer further in.

As everywhere else in this suite: a fake client, no key, no network, no SDK.
"""

import inspect
import socket
from types import SimpleNamespace

import pytest

from src.core.contracts import Task
from src.manager.llm_brain import LLMManagerBrain
from src.manager.llm_router import LLMRouter
from src.manager.router import RoutingUnavailable
from src.providers.anthropic_provider import DEFAULT_MODEL, AnthropicProvider
from src.providers.base import Completion, LLMProvider, ProviderResponseError


def block(type_: str, text: str = ""):
    return SimpleNamespace(type=type_, text=text)


def message(**overrides):
    """A well-formed response, with named parts swapped out per test."""
    fields = dict(
        content=[block("text", "ok")],
        stop_reason="end_turn",
        model="claude-opus-5",
        usage=SimpleNamespace(input_tokens=120, output_tokens=8),
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


# "Not supplied" has to be distinguishable from an explicit None, because a
# client that returns None *is* one of the malformed cases under test here.
UNSET = object()


class FakeClient:
    """Returns a canned response and records what it was asked."""

    def __init__(self, response=UNSET, error=None):
        self._response = message() if response is UNSET else response
        self._error = error
        self.requests: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._response


def provider_returning(response):
    return AnthropicProvider(client=FakeClient(response))


# ----------------------------------------------------------------------
# The error type is additive
# ----------------------------------------------------------------------


class TestTheErrorType:
    def test_it_is_a_runtime_error(self):
        """So it travels the boundary catch that already exists in the brains
        and routers, instead of needing new handling at every call site."""
        assert issubclass(ProviderResponseError, RuntimeError)

    def test_the_provider_interface_signature_is_unchanged(self):
        """Adding the error did not alter what an implementer must write."""
        signature = inspect.signature(LLMProvider.complete)
        assert list(signature.parameters) == ["self", "prompt", "system"]
        assert signature.parameters["system"].kind is inspect.Parameter.KEYWORD_ONLY

    def test_the_completion_structure_is_unchanged(self):
        completion = Completion(
            text="hi", provider="anthropic", model="m", input_tokens=1, output_tokens=2
        )
        assert (completion.text, completion.provider, completion.model) == ("hi", "anthropic", "m")

    def test_a_minimal_third_party_provider_still_satisfies_the_contract(self):
        class MinimalProvider(LLMProvider):
            @property
            def name(self):
                return "minimal"

            def complete(self, prompt, *, system=None):
                return Completion(text="hi", provider=self.name)

        assert MinimalProvider().complete("x").text == "hi"


# ----------------------------------------------------------------------
# Corrupt content is reported, never silently emptied
# ----------------------------------------------------------------------


class TestCorruptContentRaises:
    def test_content_that_is_a_bare_string(self):
        """The shape that would otherwise iterate character by character, match
        no block, and return "" - garbage wearing the costume of a model with
        nothing to say."""
        with pytest.raises(ProviderResponseError):
            provider_returning(message(content="alpha_agent")).complete("hi")

    def test_content_that_is_a_mapping_rather_than_a_list(self):
        with pytest.raises(ProviderResponseError):
            provider_returning(message(content={"type": "text"})).complete("hi")

    def test_content_that_is_a_number(self):
        with pytest.raises(ProviderResponseError):
            provider_returning(message(content=7)).complete("hi")

    def test_a_text_block_carrying_no_text_attribute(self):
        """Previously an AttributeError from inside the vendor's own object."""
        with pytest.raises(ProviderResponseError):
            provider_returning(message(content=[SimpleNamespace(type="text")])).complete("hi")

    def test_a_text_block_whose_text_is_none(self):
        with pytest.raises(ProviderResponseError):
            provider_returning(message(content=[block("text", None)])).complete("hi")

    def test_a_text_block_whose_text_is_a_number(self):
        with pytest.raises(ProviderResponseError):
            provider_returning(message(content=[block("text", 42)])).complete("hi")

    def test_a_client_that_returns_nothing_at_all(self):
        with pytest.raises(ProviderResponseError):
            provider_returning(None).complete("hi")

    def test_the_message_never_quotes_the_response_body(self):
        """Errors reach logs; a partial completion must not ride along."""
        with pytest.raises(ProviderResponseError) as exc:
            provider_returning(message(content="SECRET_MODEL_OUTPUT")).complete("hi")
        assert "SECRET_MODEL_OUTPUT" not in str(exc.value)

    def test_no_raw_sdk_exception_escapes_for_any_malformed_shape(self):
        """The whole point: one typed error, not a grab-bag of TypeError and
        AttributeError leaking another library's internals into the Manager."""
        malformed = [
            message(content="a string"),
            message(content=7),
            message(content=[SimpleNamespace(type="text")]),
            message(content=[block("text", None)]),
            None,
        ]
        for response in malformed:
            with pytest.raises(ProviderResponseError):
                provider_returning(response).complete("hi")


# ----------------------------------------------------------------------
# Absent content is emptiness, not corruption
# ----------------------------------------------------------------------


class TestAbsentContentIsEmptyNotAnError:
    def test_content_set_to_none(self):
        assert provider_returning(message(content=None)).complete("hi").is_empty

    def test_a_response_with_no_content_attribute(self):
        response = SimpleNamespace(stop_reason="end_turn")
        assert provider_returning(response).complete("hi").is_empty

    def test_an_empty_block_list(self):
        assert provider_returning(message(content=[])).complete("hi").is_empty

    def test_only_non_text_blocks(self):
        response = message(content=[block("thinking", "hmm"), block("tool_use")])
        assert provider_returning(response).complete("hi").is_empty

    def test_an_empty_completion_still_names_its_provider(self):
        completion = provider_returning(message(content=None)).complete("hi")
        assert completion.provider == "anthropic"


# ----------------------------------------------------------------------
# Blocks may be objects or plain dicts
# ----------------------------------------------------------------------


class TestBlockShapes:
    def test_dict_shaped_blocks_are_read(self):
        """A re-serialised response carries dicts, not SDK objects. This used to
        extract nothing at all and report success."""
        response = message(content=[{"type": "text", "text": "alpha_agent"}])
        assert provider_returning(response).complete("hi").text == "alpha_agent"

    def test_dict_and_object_blocks_mix(self):
        response = message(content=[{"type": "text", "text": "alpha"}, block("text", "_agent")])
        assert provider_returning(response).complete("hi").text == "alpha_agent"

    def test_dict_shaped_non_text_blocks_are_ignored(self):
        response = message(content=[{"type": "thinking", "thinking": "hmm"}, block("text", "b")])
        assert provider_returning(response).complete("hi").text == "b"

    def test_blocks_that_are_neither_are_skipped(self):
        response = message(content=[7, None, block("text", "ok")])
        assert provider_returning(response).complete("hi").text == "ok"

    def test_a_tuple_of_blocks_is_accepted(self):
        response = message(content=(block("text", "a"), block("text", "b")))
        assert provider_returning(response).complete("hi").text == "ab"


# ----------------------------------------------------------------------
# Odd telemetry is normalised, never fatal
# ----------------------------------------------------------------------


class TestUsageMetadataDegradesQuietly:
    def test_a_negative_count_becomes_unknown(self):
        """Completion rejects negative counts. Normalising here stops a cosmetic
        number from failing a call that actually returned an answer."""
        response = message(usage=SimpleNamespace(input_tokens=-5, output_tokens=8))
        completion = provider_returning(response).complete("hi")
        assert completion.text == "ok"
        assert completion.input_tokens is None
        assert completion.output_tokens == 8

    def test_counts_that_are_strings_become_unknown(self):
        response = message(usage=SimpleNamespace(input_tokens="120", output_tokens="8"))
        completion = provider_returning(response).complete("hi")
        assert completion.text == "ok"
        assert completion.input_tokens is None

    def test_booleans_are_not_counted_as_numbers(self):
        """bool subclasses int, so True would otherwise be a token count of 1."""
        response = message(usage=SimpleNamespace(input_tokens=True, output_tokens=False))
        completion = provider_returning(response).complete("hi")
        assert completion.input_tokens is None
        assert completion.output_tokens is None

    def test_a_missing_usage_object_is_not_fatal(self):
        completion = provider_returning(message(usage=None)).complete("hi")
        assert completion.text == "ok"
        assert completion.input_tokens is None

    def test_a_response_with_no_usage_attribute_is_not_fatal(self):
        response = SimpleNamespace(content=[block("text", "ok")], stop_reason="end_turn")
        assert provider_returning(response).complete("hi").text == "ok"

    def test_zero_is_a_real_count_not_a_missing_one(self):
        response = message(usage=SimpleNamespace(input_tokens=0, output_tokens=0))
        completion = provider_returning(response).complete("hi")
        assert completion.input_tokens == 0
        assert completion.output_tokens == 0


class TestTheReportedModel:
    def test_the_model_that_answered_is_preferred(self):
        response = message(model="claude-opus-5-20260101")
        assert provider_returning(response).complete("hi").model == "claude-opus-5-20260101"

    def test_a_missing_model_falls_back_to_the_configured_one(self):
        assert provider_returning(message(model=None)).complete("hi").model == DEFAULT_MODEL

    def test_a_non_string_model_falls_back_rather_than_propagating(self):
        assert provider_returning(message(model=123)).complete("hi").model == DEFAULT_MODEL

    def test_a_blank_model_falls_back(self):
        assert provider_returning(message(model="   ")).complete("hi").model == DEFAULT_MODEL


# ----------------------------------------------------------------------
# Transport failures still propagate unchanged
# ----------------------------------------------------------------------


class TestTransportErrors:
    @pytest.mark.parametrize(
        "error",
        [
            ConnectionError("connection reset"),
            TimeoutError("request timed out"),
            RuntimeError("overloaded_error"),
            OSError("network unreachable"),
        ],
    )
    def test_client_errors_reach_the_caller(self, error):
        provider = AnthropicProvider(client=FakeClient(error=error))
        with pytest.raises(type(error)):
            provider.complete("hi")

    def test_a_failure_is_not_converted_into_an_empty_answer(self):
        """Degrading is the caller's decision, not the provider's. Returning an
        empty Completion here would hide an outage as a quiet non-answer."""
        provider = AnthropicProvider(client=FakeClient(error=ConnectionError("down")))
        with pytest.raises(ConnectionError):
            provider.complete("hi")

    def test_the_provider_is_reusable_after_a_failure(self):
        client = FakeClient(error=ConnectionError("down"))
        provider = AnthropicProvider(client=client)
        with pytest.raises(ConnectionError):
            provider.complete("hi")

        client._error = None
        assert provider.complete("hi").text == "ok"


# ----------------------------------------------------------------------
# The callers degrade rather than crash
# ----------------------------------------------------------------------


class TestTheCallersDegrade:
    """Retargeted alongside the loud-fallback milestone.

    These two router tests previously asserted that a malformed response made
    LLMRouter return None. It did - and that put a garbled vendor response in
    the same bucket as a model answering "no suitable agent", which is exactly
    the conflation the routing layer now refuses to make. A response that
    cannot be parsed is a fault, so it surfaces as RoutingUnavailable.

    The property they were written to protect is unchanged and still asserted
    below: unusable model output can never select an agent.
    """

    def test_the_router_reports_a_malformed_response_as_a_fault(self):
        from src.agents.echo_agent import EchoAgent

        provider = provider_returning(message(content="garbled"))
        agents = [EchoAgent(name="alpha_agent", capabilities={"email"})]
        with pytest.raises(RoutingUnavailable):
            LLMRouter(provider).route(Task(description="send an email"), agents)

    def test_the_manager_brain_reaches_no_view_on_a_malformed_response(self):
        provider = provider_returning(message(content=[block("text", None)]))
        decision = LLMManagerBrain(provider).deliberate(Task(description="anything"))
        assert not decision.is_decided

    def test_a_malformed_response_never_becomes_a_routing_choice(self):
        """The trust boundary in one line: unusable model output must not be
        able to select an agent."""
        from src.agents.echo_agent import EchoAgent

        agents = [EchoAgent(name="alpha_agent", capabilities={"email"})]
        for bad in (message(content="alpha_agent"), message(content=7), None):
            router = LLMRouter(provider_returning(bad))
            with pytest.raises(RoutingUnavailable):
                router.route(Task(description="send an email"), agents)

    def test_a_malformed_response_cannot_drive_the_choice_behind_a_fallback(self):
        """The same guarantee where it actually matters in production.

        `content="alpha_agent"` is the adversarial shape: the raw bytes spell a
        real agent name. Routing must still not be attributable to the model -
        the decision that comes back is the deterministic one, and it says so.
        """
        from src.agents.echo_agent import EchoAgent

        from src.manager.fallback_router import FallbackRouter
        from src.manager.router import CapabilityRouter

        agents = [EchoAgent(name="alpha_agent", capabilities={"email"})]
        router = FallbackRouter(
            primary=LLMRouter(provider_returning(message(content="alpha_agent"))),
            fallback=CapabilityRouter(),
        )
        decision = router.route(Task(description="send an email"), agents)

        assert "llm unavailable" in decision.reason
        assert "selected by" not in decision.reason
        assert router.last_degradation.error_type == "ProviderResponseError"


# ----------------------------------------------------------------------
# Still offline
# ----------------------------------------------------------------------


class TestNothingReachedTheNetwork:
    def test_no_socket_is_opened_for_any_case_in_this_file(self, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("network access attempted")

        monkeypatch.setattr(socket, "socket", explode)
        monkeypatch.setattr(socket, "create_connection", explode)

        assert provider_returning(message()).complete("hi").text == "ok"
        with pytest.raises(ProviderResponseError):
            provider_returning(message(content=7)).complete("hi")

    def test_no_vendor_sdk_is_imported_by_these_tests(self):
        import sys

        provider_returning(message()).complete("hi")
        assert "anthropic" not in sys.modules

    def test_no_api_key_is_needed_anywhere_in_this_file(self):
        """Every provider here was built with no credential of any kind."""
        assert AnthropicProvider(client=FakeClient())._api_key is None
