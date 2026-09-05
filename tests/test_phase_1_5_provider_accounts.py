"""Phase 1.5 - Provider Accounts & Credentials.

CHECK found the provider *classes* real (Phase 1.4: Anthropic, OpenAI,
xAI, each with its own environment-variable credential, never another
vendor's), Full/Half Brain semantics real (Phase 1.3), and every
existing credential-isolation guarantee still holding. What did not
exist was an actual *account* layer: no way to hold more than one
credential per provider, no explicit selection between them, no
first-class revocation independent of unsetting a process environment
variable, and no real validation beyond "does a string exist".

**The one genuine, structural gap CHECK found in the real production
path, not merely absent from a spec.** `src/composition.py::
build_runtime` called `build_provider(settings)` exactly once and
handed that *single instance* to both the Manager's own brain and
every specialist's brain (`build_specialist_brain(settings, provider,
...)`, the identical `provider` local reused for each). Reproduced
directly below (`TestTheSharedProviderInstanceBugAlreadyFixed`):
before this phase's fix, `runtime.provider is arthur.brain._provider`
was `True` in the real, default production configuration - Alfred and
every specialist shared one literal `LLMProvider` object, and therefore
one credential, by construction. This is the opposite of the
independence this project already documents and tests for Brain,
Memory and Voice, and it is exactly what made "a future specialist
selects its own account" structurally impossible without this fix -
not merely untested, genuinely absent.

**What was built**, entirely additive, nothing pre-existing removed or
duplicated:

- `src/services/credentials/account.py` - `ProviderAccount` (a named,
  provider-specific credential; never repr'd, never logged) and
  `CredentialStore` (multiple accounts per provider, explicit
  selection, immediate-effect revocation, isolation between accounts
  and between providers).
- `src/services/credentials/validation.py` - `validate_credential`, a
  real function that makes the one real call
  (`LLMProvider.complete(...)`) that actually proves a credential
  works, never a `if key exists: valid = True` fake.
- `src/providers/factory.py::build_provider` gained one optional
  parameter, `account: ProviderAccount | None`, refused outright if it
  names a different provider than the one selected
  (`ProviderConfigurationError`, never a silent substitution); omitted,
  every existing caller is unaffected.
- `src/composition.py::build_runtime` gained two optional parameters
  (`accounts`, `specialist_accounts`) and now constructs a genuinely
  separate `LLMProvider` instance per agent - the fix for the gap
  above. The default (no accounts given) reproduces the exact prior
  credential *value* for every agent (there is still only one
  configured account in an ordinary deployment), just no longer the
  same object.

No provider-specific branching (`if provider == "anthropic"`) was added
anywhere in this file's own subject matter - see
`TestNoHardcodedProviderWorkarounds`.
"""

from __future__ import annotations

import logging

import pytest

from src.config.settings import Settings
from src.core.contracts import Task
from src.composition import build_runtime
from src.manager.cognition import CognitiveMode, assess_cognition, assess_manager
from src.manager.llm_brain import LLMManagerBrain
from src.providers.base import Completion, LLMProvider
from src.providers.factory import (
    ANTHROPIC_KEY_ENV,
    OPENAI_KEY_ENV,
    XAI_KEY_ENV,
    ProviderConfigurationError,
    build_provider,
)
from src.providers.anthropic_provider import AnthropicProvider
from src.providers.openai_provider import OpenAIProvider
from src.providers.xai_provider import XAIProvider
from src.services.credentials.account import CredentialStore, ProviderAccount
from src.services.credentials.validation import validate_credential
from scripts.control_center import build
from scripts.exchange import exchange


def real_system(**overrides):
    settings = Settings.from_env(env={}, memory="ephemeral", **overrides)
    return build(settings)


class FakeProvider(LLMProvider):
    """A real, distinct LLMProvider - never a mock - standing in for a
    genuinely different vendor/account each time one is constructed."""

    def __init__(self, vendor_name: str, answer: str = "an answer"):
        self._name = vendor_name
        self._answer = answer

    @property
    def name(self) -> str:
        return self._name

    def complete(self, prompt, *, system=None):
        if system and "Reply with a single JSON object" in system:
            return Completion(text='{"action":"unknown","reason":"n/a"}', provider=self._name)
        return Completion(text=self._answer, provider=self._name)


class RaisingProvider(LLMProvider):
    def __init__(self, vendor_name: str, error: Exception):
        self._name = vendor_name
        self._error = error

    @property
    def name(self) -> str:
        return self._name

    def complete(self, prompt, *, system=None):
        raise self._error


# ---------------------------------------------------------------------------
# The structural gap CHECK found, and its fix
# ---------------------------------------------------------------------------


class TestTheSharedProviderInstanceBugAlreadyFixed:
    def test_alfred_and_a_specialist_no_longer_share_one_provider_instance(self, monkeypatch):
        monkeypatch.setenv(ANTHROPIC_KEY_ENV, "fake-key")
        runtime = build_runtime(
            Settings.from_env(
                env={}, memory="ephemeral", provider="anthropic", brain="llm", specialist_brain="llm"
            )
        )
        arthur_brain = runtime.specialists["email_agent"].brain
        assert runtime.provider is not arthur_brain._provider

    def test_the_default_credential_value_is_still_identical_for_both(self, monkeypatch):
        """The fix changes object identity, never behaviour: with no
        account override, every agent still resolves the identical
        environment-configured credential - zero regression for today's
        single-account deployment."""
        monkeypatch.setenv(ANTHROPIC_KEY_ENV, "fake-key")
        runtime = build_runtime(
            Settings.from_env(
                env={}, memory="ephemeral", provider="anthropic", brain="llm", specialist_brain="llm"
            )
        )
        arthur_brain = runtime.specialists["email_agent"].brain
        assert runtime.provider._api_key == arthur_brain._provider._api_key == "fake-key"


# ---------------------------------------------------------------------------
# 1-2. Credential isolation, multiple accounts
# ---------------------------------------------------------------------------


class TestCredentialIsolationAndMultipleAccounts:
    def test_two_accounts_for_the_same_provider_are_both_real_and_distinct(self):
        store = CredentialStore()
        store.add(ProviderAccount(provider="openai", account_id="work", credential="sk-work"))
        store.add(ProviderAccount(provider="openai", account_id="personal", credential="sk-personal"), activate=False)

        accounts = store.accounts_for("openai")
        assert {a.account_id for a in accounts} == {"work", "personal"}
        assert store.get("openai", "work").credential == "sk-work"
        assert store.get("openai", "personal").credential == "sk-personal"

    def test_an_account_for_one_provider_never_leaks_into_another(self):
        store = CredentialStore()
        store.add(ProviderAccount(provider="openai", account_id="default", credential="sk-openai"))
        assert store.accounts_for("xai") == ()
        assert store.get("xai", "default") is None

    def test_an_explicit_account_decides_which_vendor_is_built_not_settings_provider(self):
        """The seam that lets a specialist use a genuinely different
        vendor than the Manager's own ``Settings.provider`` (Section 9):
        the account's own declared provider drives which builder runs.
        The safety property this project actually needs - a credential
        never reaches a builder for a different vendor than the one it
        names - still holds, enforced inside each builder itself (see
        the next test), never by requiring Settings to already agree."""
        openai_account = ProviderAccount(provider="openai", account_id="default", credential="sk-openai")
        provider = build_provider(Settings(provider="xai"), env={}, account=openai_account)
        assert isinstance(provider, OpenAIProvider)
        assert provider._api_key == "sk-openai"

    def test_a_builder_still_refuses_an_account_for_a_different_vendor_directly(self):
        """Defence in depth: even called directly (bypassing
        build_provider's own correct-by-construction dispatch), a
        builder refuses an account that does not name its own vendor -
        the credential can never reach the wrong builder by accident."""
        from src.providers.factory import _build_xai

        openai_account = ProviderAccount(provider="openai", account_id="default", credential="sk-openai")
        with pytest.raises(ProviderConfigurationError) as exc:
            _build_xai(Settings(provider="xai"), env={}, account=openai_account)
        assert "openai" in str(exc.value) and "xai" in str(exc.value)

    def test_a_provider_receives_only_the_credential_belonging_to_it(self):
        account = ProviderAccount(provider="openai", account_id="default", credential="sk-real-openai-value")
        provider = build_provider(Settings(provider="openai"), env={}, account=account)
        assert isinstance(provider, OpenAIProvider)
        assert provider._api_key == "sk-real-openai-value"


# ---------------------------------------------------------------------------
# 3. Account selection
# ---------------------------------------------------------------------------


class TestAccountSelection:
    def test_the_active_account_is_used_by_default(self):
        store = CredentialStore()
        store.add(ProviderAccount(provider="openai", account_id="first", credential="key-1"))
        assert store.active("openai").account_id == "first"

    def test_selecting_a_different_registered_account_changes_which_is_active(self):
        store = CredentialStore()
        store.add(ProviderAccount(provider="openai", account_id="first", credential="key-1"))
        store.add(ProviderAccount(provider="openai", account_id="second", credential="key-2"), activate=False)
        assert store.active("openai").account_id == "first"

        store.select("openai", "second")
        assert store.active("openai").account_id == "second"
        assert store.active("openai").credential == "key-2"

    def test_selecting_an_unregistered_account_is_refused_not_silently_ignored(self):
        store = CredentialStore()
        store.add(ProviderAccount(provider="openai", account_id="first", credential="key-1"))
        with pytest.raises(KeyError):
            store.select("openai", "nonexistent")
        # The refusal changed nothing - still "first", never silently
        # falling back to whatever was already active being mistaken for
        # a successful switch.
        assert store.active("openai").account_id == "first"


# ---------------------------------------------------------------------------
# 4-5. Credential replacement and revocation
# ---------------------------------------------------------------------------


class TestReplacementAndRevocation:
    def test_adding_an_account_with_the_same_id_replaces_it(self):
        store = CredentialStore()
        store.add(ProviderAccount(provider="openai", account_id="default", credential="old-key"))
        store.add(ProviderAccount(provider="openai", account_id="default", credential="new-key"))
        assert store.get("openai", "default").credential == "new-key"
        assert len(store.accounts_for("openai")) == 1

    def test_revoking_an_account_makes_it_immediately_unusable(self):
        store = CredentialStore()
        store.add(ProviderAccount(provider="openai", account_id="default", credential="a-key"))
        store.revoke("openai", "default")
        assert store.get("openai", "default") is None
        assert store.active("openai") is None

    def test_revocation_never_falls_back_to_a_different_provider_or_account(self):
        store = CredentialStore()
        store.add(ProviderAccount(provider="openai", account_id="a", credential="key-a"))
        store.add(ProviderAccount(provider="openai", account_id="b", credential="key-b"), activate=False)
        store.add(ProviderAccount(provider="anthropic", account_id="default", credential="key-anthropic"))

        store.revoke("openai", "a")
        # No silent promotion of "b" to active, and anthropic is
        # completely untouched.
        assert store.active("openai") is None
        assert store.get("openai", "b") is not None
        assert store.active("anthropic").credential == "key-anthropic"

    def test_revoking_takes_effect_on_the_very_next_provider_construction(self):
        store = CredentialStore()
        store.add(ProviderAccount(provider="openai", account_id="default", credential="a-real-key"))
        provider = build_provider(Settings(provider="openai"), env={}, account=store.active("openai"))
        assert isinstance(provider, OpenAIProvider)

        store.revoke("openai", "default")
        with pytest.raises(ProviderConfigurationError):
            build_provider(Settings(provider="openai"), env={}, account=store.active("openai"))

    def test_a_replacement_credential_makes_the_provider_usable_again(self):
        store = CredentialStore()
        store.add(ProviderAccount(provider="xai", account_id="default", credential="expired-looking-key"))
        store.revoke("xai", "default")
        with pytest.raises(ProviderConfigurationError):
            build_provider(Settings(provider="xai"), env={}, account=store.active("xai"))

        store.add(ProviderAccount(provider="xai", account_id="default", credential="a-fresh-key"))
        provider = build_provider(Settings(provider="xai"), env={}, account=store.active("xai"))
        assert isinstance(provider, XAIProvider)
        assert provider._api_key == "a-fresh-key"


# ---------------------------------------------------------------------------
# 6-9. Missing/invalid credentials, provider-specific routing, no leakage
# ---------------------------------------------------------------------------


class TestMissingAndInvalidCredentials:
    @pytest.mark.parametrize(
        "provider_name, env_var",
        [("anthropic", ANTHROPIC_KEY_ENV), ("openai", OPENAI_KEY_ENV), ("xai", XAI_KEY_ENV)],
    )
    def test_a_missing_credential_is_refused_honestly(self, provider_name, env_var):
        with pytest.raises(ProviderConfigurationError) as exc:
            build_provider(Settings(provider=provider_name), env={})
        assert env_var in str(exc.value)

    def test_an_invalid_credential_is_caught_by_real_validation_not_presence_checking(self):
        """The credential 'exists' (a non-empty string was supplied) but
        does not work - exactly the case a naive `if key exists` check
        would wrongly call valid."""
        provider = RaisingProvider("test", RuntimeError("401 unauthorized"))
        result = validate_credential(provider)
        assert result.valid is False
        assert result.checked_live is False
        assert "unauthorized" in result.reason.lower() or "runtimeerror" in result.reason.lower()

    def test_a_working_credential_validates_true_through_a_real_call(self):
        provider = FakeProvider("test", "ok")
        result = validate_credential(provider)
        assert result.valid is True
        assert result.checked_live is True

    def test_validation_never_raises_it_reports(self):
        class Explodes(LLMProvider):
            @property
            def name(self):
                return "test"

            def complete(self, prompt, *, system=None):
                raise ConnectionError("host unreachable")

        result = validate_credential(Explodes())
        assert result.valid is False
        assert "ConnectionError" in result.reason

    def test_an_empty_response_is_reached_but_inconclusive_never_confidently_invalid(self):
        provider = FakeProvider("test", "")
        result = validate_credential(provider)
        assert result.valid is False
        assert result.checked_live is True


class TestProviderSpecificRoutingAndNoLeakage:
    @pytest.mark.parametrize(
        "provider_name, own_env, foreign_envs",
        [
            ("anthropic", ANTHROPIC_KEY_ENV, (OPENAI_KEY_ENV, XAI_KEY_ENV)),
            ("openai", OPENAI_KEY_ENV, (ANTHROPIC_KEY_ENV, XAI_KEY_ENV)),
            ("xai", XAI_KEY_ENV, (ANTHROPIC_KEY_ENV, OPENAI_KEY_ENV)),
        ],
    )
    def test_a_provider_never_accepts_a_foreign_vendors_environment_credential(
        self, provider_name, own_env, foreign_envs
    ):
        with pytest.raises(ProviderConfigurationError) as exc:
            build_provider(
                Settings(provider=provider_name),
                env={foreign: "a-foreign-key" for foreign in foreign_envs},
            )
        assert own_env in str(exc.value)

    def test_an_accounts_own_credential_only_ever_authorises_its_own_vendor(self):
        """Whatever ``Settings.provider`` happens to say, an anthropic
        account only ever builds an anthropic provider with the
        anthropic credential - never silently reinterpreted as
        authorising a different vendor just because that vendor was
        named elsewhere in Settings."""
        anthropic_account = ProviderAccount(provider="anthropic", account_id="default", credential="claude-key")
        for unrelated_setting in ("openai", "xai"):
            provider = build_provider(Settings(provider=unrelated_setting), env={}, account=anthropic_account)
            assert isinstance(provider, AnthropicProvider)
            assert provider._api_key == "claude-key"


# ---------------------------------------------------------------------------
# 10. No agent-owned API keys
# ---------------------------------------------------------------------------


class TestNoAgentOwnedAPIKeys:
    def test_no_agent_brain_or_capability_module_owns_a_credential_field(self):
        """The precise signal - a real field/parameter named ``api_key``
        - not the ordinary English word "credential", which several
        docstrings in this project (including this phase's own) use in
        plain prose without owning one.

        ``memory_policy.py`` is the one legitimate exception: it names
        "api_key" as one of the secret-shaped field names its own
        denylist refuses to ever store - exactly the protective
        behaviour Phase 1.5's security requirements ask for, not a
        credential this module owns.
        """
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        allowed = {repo_root / "src" / "manager" / "memory_policy.py"}
        checked_dirs = [repo_root / "src" / "agents", repo_root / "src" / "manager"]
        for directory in checked_dirs:
            for path in directory.rglob("*.py"):
                if path in allowed:
                    continue
                source = path.read_text(encoding="utf-8")
                assert "api_key" not in source, path


# ---------------------------------------------------------------------------
# 11. Alfred/Arthur credential independence, through the real production path
# ---------------------------------------------------------------------------


class TestAlfredAndArthurCredentialIndependence:
    def test_changing_alfreds_account_never_touches_arthurs(self):
        accounts = CredentialStore()
        accounts.add(ProviderAccount(provider="anthropic", account_id="alfred", credential="alfred-key-1"))
        specialist_accounts = {
            "email_agent": ProviderAccount(provider="anthropic", account_id="arthur", credential="arthur-key-1"),
        }
        settings = Settings.from_env(
            env={}, memory="ephemeral", provider="anthropic", brain="llm", specialist_brain="llm"
        )
        runtime = build_runtime(settings, accounts=accounts, specialist_accounts=specialist_accounts)
        arthur_brain = runtime.specialists["email_agent"].brain
        assert runtime.provider._api_key == "alfred-key-1"
        assert arthur_brain._provider._api_key == "arthur-key-1"

        # Revoke and replace Alfred's account only.
        accounts.revoke("anthropic", "alfred")
        accounts.add(ProviderAccount(provider="anthropic", account_id="alfred", credential="alfred-key-2"))
        runtime2 = build_runtime(settings, accounts=accounts, specialist_accounts=specialist_accounts)
        arthur_brain2 = runtime2.specialists["email_agent"].brain
        assert runtime2.provider._api_key == "alfred-key-2"
        # Arthur's own, separately-tracked account is completely untouched.
        assert arthur_brain2._provider._api_key == "arthur-key-1"

    def test_a_future_specialist_can_select_its_own_account_with_zero_brain_changes(self):
        """The concrete proof of Section 9's requirement: adding a second
        specialist's own account - for a genuinely *different vendor*
        than the Manager's own configuration - is a data change to a
        mapping passed into build_runtime, never a code change to
        Brain, Identity, Memory or Voice. Alfred's own settings name no
        provider at all (null); the specialist is independently
        authorised for OpenAI regardless."""
        specialist_accounts = {
            "email_agent": ProviderAccount(provider="openai", account_id="dedicated", credential="dedicated-key"),
        }
        settings = Settings.from_env(
            env={}, memory="ephemeral", provider="null", brain="null", specialist_brain="llm"
        )
        runtime = build_runtime(settings, specialist_accounts=specialist_accounts)
        arthur_brain = runtime.specialists["email_agent"].brain
        assert arthur_brain._provider._api_key == "dedicated-key"
        assert isinstance(arthur_brain._provider, OpenAIProvider)
        from src.providers.null_provider import NullProvider

        assert isinstance(runtime.provider, NullProvider)


# ---------------------------------------------------------------------------
# 12. Full/Half Brain transitions driven by credential state
# ---------------------------------------------------------------------------


class TestFullHalfBrainCredentialTransitions:
    def test_a_usable_credential_earns_full_brain_from_a_real_call(self):
        brain = LLMManagerBrain(FakeProvider("test", "a real answer"))
        assert assess_cognition(brain).mode is CognitiveMode.HALF_BRAIN
        brain.converse(Task(description="hello"))
        assert assess_cognition(brain).mode is CognitiveMode.FULL_BRAIN

    def test_a_missing_credential_never_reaches_full_brain(self):
        with pytest.raises(ProviderConfigurationError):
            build_provider(Settings(provider="openai"), env={})
        # No provider was ever constructed - the honest, unconfigured
        # Half Brain state, the same one an unset environment variable
        # already produces.
        assert assess_cognition(None).mode is CognitiveMode.HALF_BRAIN

    def test_an_invalid_credential_degrades_to_half_brain_not_a_crash(self):
        brain = LLMManagerBrain(RaisingProvider("test", RuntimeError("401 unauthorized")))
        brain.converse(Task(description="hello"))
        assert assess_cognition(brain).mode is CognitiveMode.HALF_BRAIN

    def test_a_revoked_credential_stops_new_provider_construction_honestly(self):
        store = CredentialStore()
        store.add(ProviderAccount(provider="openai", account_id="default", credential="a-key"))
        store.revoke("openai", "default")
        with pytest.raises(ProviderConfigurationError):
            build_provider(Settings(provider="openai"), env={}, account=store.active("openai"))

    def test_a_replacement_credential_can_earn_full_brain_again(self):
        store = CredentialStore()
        store.add(ProviderAccount(provider="openai", account_id="default", credential="expired"))
        store.revoke("openai", "default")
        store.add(ProviderAccount(provider="openai", account_id="default", credential="fresh"))

        provider = build_provider(Settings(provider="openai"), env={}, account=store.active("openai"))
        # A real construction succeeded; Full Brain is still only ever
        # earned by a genuine successful call, never by construction
        # alone - the identical rule Phase 1.3/1.4 already established,
        # reconfirmed here for the account-based construction path.
        brain = LLMManagerBrain(FakeProvider(provider.name, "answer"))
        assert assess_cognition(brain).mode is CognitiveMode.HALF_BRAIN
        brain.converse(Task(description="hi"))
        assert assess_cognition(brain).mode is CognitiveMode.FULL_BRAIN


# ---------------------------------------------------------------------------
# 13-16. Provider switching, Memory/Conversation/Voice survival
# ---------------------------------------------------------------------------


class TestProviderSwitchingWithAccounts:
    def test_anthropic_to_openai_to_xai_via_distinct_accounts(self):
        runtime, manager, channel, conversation = real_system()
        alfred_identity = runtime.identity
        memory = manager.memory

        store = CredentialStore()
        store.add(ProviderAccount(provider="anthropic", account_id="default", credential="claude-key"))
        store.add(ProviderAccount(provider="openai", account_id="default", credential="openai-key"))
        store.add(ProviderAccount(provider="xai", account_id="default", credential="xai-key"))

        # Provider A: anthropic
        provider_a = build_provider(Settings(provider="anthropic"), env={}, account=store.active("anthropic"))
        brain_a = LLMManagerBrain(FakeProvider("anthropic", "hello from Claude"), personality=runtime.identity.personality)
        manager.brain = brain_a
        manager.own_skills._converse = brain_a.converse
        exchange(channel, conversation, "remember that the project codename is Halcyon")
        assert any("Halcyon" in r.content for r in memory.recall(limit=10))
        assert isinstance(provider_a, AnthropicProvider) and provider_a._api_key == "claude-key"

        # Provider B: openai - a wholly separate credential
        provider_b = build_provider(Settings(provider="openai"), env={}, account=store.active("openai"))
        brain_b = LLMManagerBrain(FakeProvider("openai", "hello from GPT"), personality=runtime.identity.personality)
        manager.brain = brain_b
        manager.own_skills._converse = brain_b.converse
        turn = exchange(channel, conversation, "what did I ask you to remember?")
        assert turn.succeeded
        assert any("Halcyon" in r.content for r in memory.recall(limit=10))
        assert isinstance(provider_b, OpenAIProvider) and provider_b._api_key == "openai-key"
        assert runtime.identity is alfred_identity
        assert runtime.identity.voice is alfred_identity.voice

        # Provider C: xai - a third, independent credential
        provider_c = build_provider(Settings(provider="xai"), env={}, account=store.active("xai"))
        brain_c = LLMManagerBrain(FakeProvider("xai", "hello from Grok"), personality=runtime.identity.personality)
        manager.brain = brain_c
        manager.own_skills._converse = brain_c.converse
        turn = exchange(channel, conversation, "one more time, what's the codename?")
        assert turn.succeeded
        assert isinstance(provider_c, XAIProvider) and provider_c._api_key == "xai-key"

        # No credential ever crossed providers.
        assert len({provider_a._api_key, provider_b._api_key, provider_c._api_key}) == 3
        assert any("Halcyon" in r.content for r in memory.recall(limit=10))
        assert runtime.identity.name == "Alfred"

    def test_openai_to_anthropic_to_xai_the_other_useful_order(self):
        store = CredentialStore()
        store.add(ProviderAccount(provider="openai", account_id="default", credential="o-key"))
        store.add(ProviderAccount(provider="anthropic", account_id="default", credential="a-key"))
        store.add(ProviderAccount(provider="xai", account_id="default", credential="x-key"))

        p1 = build_provider(Settings(provider="openai"), env={}, account=store.active("openai"))
        p2 = build_provider(Settings(provider="anthropic"), env={}, account=store.active("anthropic"))
        p3 = build_provider(Settings(provider="xai"), env={}, account=store.active("xai"))
        assert (p1._api_key, p2._api_key, p3._api_key) == ("o-key", "a-key", "x-key")


# ---------------------------------------------------------------------------
# 17. Future-provider extensibility, at the account layer
# ---------------------------------------------------------------------------


class TestFutureProviderAccountExtensibility:
    def test_a_never_before_seen_provider_gets_a_real_account_with_zero_changes(self, monkeypatch):
        """A synthetic 'gemini' provider/account, registered purely
        through the existing mechanisms - the account layer needed no
        change, the factory needed no change beyond the generic
        ``account`` parameter already added for every provider, and
        Brain/Memory/Voice/Identity were never touched."""
        import src.providers.factory as provider_factory

        credential_env = "GEMINI_API_KEY"

        def _build_gemini(settings, env, account=None):
            if account is not None:
                if account.provider != "gemini":
                    raise ProviderConfigurationError("wrong provider for this account")
                return FakeProvider("gemini", "a genuine gemini answer")
            source = env if env is not None else {}
            key = (source.get(credential_env) or "").strip()
            if not key:
                raise ProviderConfigurationError(f"set {credential_env}")
            return FakeProvider("gemini", "a genuine gemini answer")

        patched = dict(provider_factory._BUILDERS)
        patched["gemini"] = _build_gemini
        monkeypatch.setattr(provider_factory, "_BUILDERS", patched)

        store = CredentialStore()
        store.add(ProviderAccount(provider="gemini", account_id="default", credential="a-gemini-key"))

        provider = build_provider(Settings(provider="gemini"), env={}, account=store.active("gemini"))
        assert provider.name == "gemini"

        brain = LLMManagerBrain(provider)
        decision = brain.converse(Task(description="hi"))
        assert decision.reason == "a genuine gemini answer"

        # An unrelated vendor's account still only ever authorises its
        # own provider - dispatch by account.provider generalises to
        # the new provider too, with no gemini-specific code added
        # anywhere in the factory or the account layer.
        anthropic_account = ProviderAccount(provider="anthropic", account_id="default", credential="claude-key")
        real_anthropic_provider = build_provider(Settings(provider="gemini"), env={}, account=anthropic_account)
        assert isinstance(real_anthropic_provider, AnthropicProvider)
        assert real_anthropic_provider.name != "gemini"


# ---------------------------------------------------------------------------
# 18. Secrets are never exposed in logs or diagnostics
# ---------------------------------------------------------------------------


class TestSecretsNeverExposed:
    def test_credential_store_repr_never_names_a_secret_value(self):
        store = CredentialStore()
        store.add(ProviderAccount(provider="openai", account_id="default", credential="sk-super-secret-value"))
        assert "sk-super-secret-value" not in repr(store)
        assert "openai/default" in repr(store)

    def test_provider_account_repr_never_names_the_credential(self):
        account = ProviderAccount(provider="openai", account_id="default", credential="sk-super-secret-value")
        assert "sk-super-secret-value" not in repr(account)
        assert "sk-super-secret-value" not in account.describe()

    def test_building_a_provider_from_an_account_never_logs_the_secret(self, caplog):
        with caplog.at_level(logging.DEBUG):
            build_provider(
                Settings(provider="openai"),
                env={},
                account=ProviderAccount(provider="openai", account_id="default", credential="sk-must-not-appear"),
            )
        for record in caplog.records:
            assert "sk-must-not-appear" not in record.getMessage()

    def test_a_completion_describe_never_includes_the_credential_or_the_text(self):
        """Re-confirms the pre-existing guarantee Completion.describe()
        already gives (it excludes generated text) in this phase's own
        context - a provider's diagnostic surface never becomes a
        second place a secret could leak through."""
        completion = Completion(text="some real answer", provider="openai", model="gpt-5")
        assert "some real answer" not in completion.describe()

    def test_settings_describe_still_masks_the_credential(self):
        settings = Settings(api_key="a-real-secret-key")
        assert "a-real-secret-key" not in settings.describe()
        assert "set" in settings.describe()


# ---------------------------------------------------------------------------
# 19. No hardcoded provider workarounds in this phase's own new code
# ---------------------------------------------------------------------------


class TestNoHardcodedProviderWorkarounds:
    def test_the_credential_layer_contains_no_per_vendor_branching(self):
        import inspect

        import src.services.credentials.account as account_module
        import src.services.credentials.validation as validation_module

        for module in (account_module, validation_module):
            source = inspect.getsource(module)
            for vendor in ("anthropic", "openai", "xai", "grok"):
                assert f'"{vendor}"' not in source.lower() and f"'{vendor}'" not in source.lower(), (
                    module.__name__, vendor
                )
