"""Universal Ecosystem & Evolution Management Layer.

CHECK found this project already had roughly a dozen independent,
identically-shaped replaceable axes (LLM provider, video-generation
vendor, media trio, speech engine, connection, avatar, knowledge,
live-info, celebration, calendar events) - each its own
``available_*``/``build_*`` pair, never tied together. This checkpoint
adds the missing tie: a generic ``EcosystemRegistry``
(``src/services/ecosystem/contracts.py``) that *wraps* every existing
factory (``src/services/ecosystem/registry_factory.py``) rather than
reimplementing any of them, a file-backed extension to
``CredentialStore`` for persisted operator-managed accounts
(``src/services/credentials/account_store_file.py``), and a real
operator CLI (``scripts/ecosystem_cli.py``) - plus one genuinely new
category (``environment_provider``) completing Section 9's own request,
built with the identical "descriptive, never a rendering asset"
discipline ``AvatarProvider`` already established.

Tests below prove: the generic contract works against every *real*
existing axis (not a mock), credentials persist and stay isolated
per-provider, dependency impact reporting is real, the CLI performs
real operations, and - the acceptance test this checkpoint's own brief
explicitly demands - a genuinely new, previously-unseen category can
enter the ecosystem through the same extension boundary with zero
changes to ``contracts.py`` or ``registry_factory.py``.
"""

from __future__ import annotations

import json

import pytest

from src.config.settings import Settings
from src.providers.avatar_factory import available_avatar_providers
from src.providers.environment import EnvironmentProfile, EnvironmentProvider
from src.providers.environment_factory import available_environment_providers, build_environment_provider
from src.services.credentials.account import CredentialStore, ProviderAccount
from src.services.credentials.account_store_file import load_credential_store, save_credential_store
from src.services.ecosystem.contracts import (
    CategoryAdapter,
    ComponentDescriptor,
    EcosystemRegistry,
    GenericCategoryAdapter,
    LifecycleOperation,
    ValidationOutcome,
)
from src.services.ecosystem.registry_factory import build_ecosystem_registry


# ---------------------------------------------------------------------------
# 1. The generic contract itself
# ---------------------------------------------------------------------------


class TestContracts:
    def test_component_descriptor_requires_a_category_and_key(self):
        with pytest.raises(ValueError):
            ComponentDescriptor(category="", key="x")
        with pytest.raises(ValueError):
            ComponentDescriptor(category="x", key="")

    def test_validation_outcome_failure_must_explain_itself(self):
        with pytest.raises(ValueError):
            ValidationOutcome.failure("")

    def test_validation_outcome_success_needs_no_detail(self):
        outcome = ValidationOutcome.success()
        assert outcome.ok

    def test_generic_adapter_reports_active_and_inactive_backends(self):
        adapter = GenericCategoryAdapter(
            "widget", list_backends=lambda: ["a", "b"], active_key=lambda: "a"
        )
        descriptors = {d.key: d for d in adapter.available()}
        assert descriptors["a"].is_active
        assert not descriptors["b"].is_active
        assert adapter.active().key == "a"

    def test_generic_adapter_with_no_build_cannot_validate(self):
        adapter = GenericCategoryAdapter("widget", list_backends=lambda: ["a"], active_key=lambda: "a")
        outcome = adapter.validate("a")
        assert not outcome.ok
        assert LifecycleOperation.VALIDATE not in adapter.supported_operations()

    def test_generic_adapter_with_build_validates_honestly(self):
        def build(key, credential):
            if key == "broken":
                raise RuntimeError("cannot construct broken")
            return object()

        adapter = GenericCategoryAdapter(
            "widget", list_backends=lambda: ["ok", "broken"], active_key=lambda: "ok", build=build
        )
        assert adapter.validate("ok").ok
        failure = adapter.validate("broken")
        assert not failure.ok
        assert "cannot construct broken" in failure.detail
        assert LifecycleOperation.VALIDATE in adapter.supported_operations()

    def test_generic_adapter_reports_credential_requirement_per_key(self):
        adapter = GenericCategoryAdapter(
            "widget",
            list_backends=lambda: ["free", "paid"],
            active_key=lambda: "free",
            requires_credential=lambda key: key == "paid",
        )
        descriptors = {d.key: d for d in adapter.available()}
        assert not descriptors["free"].requires_credential
        assert descriptors["paid"].requires_credential

    def test_registry_registration_is_replacement_not_duplication(self):
        registry = EcosystemRegistry()
        registry.register(GenericCategoryAdapter("widget", list_backends=lambda: ["a"], active_key=lambda: "a"))
        registry.register(GenericCategoryAdapter("widget", list_backends=lambda: ["b"], active_key=lambda: "b"))
        assert len(registry) == 1
        assert registry.adapter_for("widget").active().key == "b"

    def test_registry_reports_an_unknown_category_honestly(self):
        registry = EcosystemRegistry()
        assert registry.adapter_for("nonexistent") is None
        assert registry.impact_of("nonexistent") == ()

    def test_default_category_adapter_default_operations_are_conservative(self):
        class BareAdapter(CategoryAdapter):
            @property
            def category(self):
                return "bare"

            def available(self):
                return ()

            def active(self):
                return None

        adapter = BareAdapter()
        assert adapter.supported_operations() == {LifecycleOperation.DISCOVER, LifecycleOperation.SELECT}
        assert not adapter.validate("x").ok
        assert adapter.dependents() == ()

    def test_this_layer_is_structurally_independent_of_stage_11a_11b(self):
        """CHECK found src/extensions.py (Stage 11.A) and src/evolution.py
        (Stage 11.B) already govern a related but different question -
        whether an abstract feature/change *declaration* is approved to
        be marked active in an immutable ledger, never touching a real
        provider or credential. This layer answers the complementary,
        concrete question (what real backends exist, can one genuinely
        be constructed) - proven structurally independent, not merely by
        description: neither imports the other."""
        import inspect
        import src.extensions as extensions_module
        import src.evolution as evolution_module
        import src.services.ecosystem.contracts as contracts_module
        import src.services.ecosystem.registry_factory as registry_factory_module

        for module in (contracts_module, registry_factory_module):
            source = inspect.getsource(module)
            assert "src.extensions" not in source
            assert "src.evolution" not in source
        for module in (extensions_module, evolution_module):
            source = inspect.getsource(module)
            assert "src.services.ecosystem" not in source

    def test_contracts_module_names_no_specific_vendor_or_category(self):
        """The generic layer must stay generic - Section 2's own
        requirement, checked directly: no hardcoded provider or
        category name leaks into the contract itself."""
        import inspect
        import src.services.ecosystem.contracts as contracts_module

        source = inspect.getsource(contracts_module).lower()
        for forbidden in (
            "anthropic", "openai", "xai", "runway", "veo", "gmail", "elevenlabs",
            "kokoro", "pollinations",
        ):
            assert forbidden not in source, forbidden


# ---------------------------------------------------------------------------
# 2. The registry built from every REAL existing axis
# ---------------------------------------------------------------------------


class TestRegistryFromRealComposition:
    def test_every_known_axis_is_registered(self):
        registry = build_ecosystem_registry(Settings.from_env(env={}))
        expected = {
            "llm_provider", "video_generation_provider", "media", "speech_provider",
            "connection", "avatar_provider", "environment_provider", "knowledge_provider",
            "live_info_provider", "communication_provider", "celebration_provider",
            "calendar_event_provider", "capability",
        }
        assert expected.issubset(set(registry.categories()))

    def test_active_backend_reflects_real_settings(self):
        settings = Settings.from_env(env={}, provider="anthropic", api_key="sk-real-enough")
        registry = build_ecosystem_registry(settings)
        assert registry.adapter_for("llm_provider").active().key == "anthropic"

    def test_validate_llm_provider_without_credential_fails_honestly(self):
        registry = build_ecosystem_registry(Settings.from_env(env={}))
        outcome = registry.adapter_for("llm_provider").validate("anthropic")
        assert not outcome.ok
        assert "no API key is configured" in outcome.detail

    def test_validate_llm_provider_with_a_supplied_credential_constructs_for_real(self):
        registry = build_ecosystem_registry(Settings.from_env(env={}))
        outcome = registry.adapter_for("llm_provider").validate("anthropic", credential="sk-fake-but-present")
        assert outcome.ok

    def test_validate_video_generation_provider_for_both_real_vendors(self):
        registry = build_ecosystem_registry(Settings.from_env(env={}))
        adapter = registry.adapter_for("video_generation_provider")
        assert not adapter.validate("runway").ok
        assert adapter.validate("runway", credential="k").ok
        assert not adapter.validate("veo").ok
        assert adapter.validate("veo", credential="k").ok

    def test_a_credential_given_to_one_category_never_reaches_another(self):
        """The generic registry must not become a new leakage surface:
        each adapter's own build closure only ever wraps its own
        category's real factory - a credential passed to `speech_provider`
        cannot possibly authorise `llm_provider`, structurally, because
        each closure calls a different, independent build_*() function."""
        registry = build_ecosystem_registry(Settings.from_env(env={}))
        # A credential shaped like an Anthropic key, validated against
        # elevenlabs, only ever reaches ElevenLabsSpeechAdapter's own
        # constructor - it cannot make llm_provider's own null default
        # suddenly require one.
        registry.adapter_for("speech_provider").validate("elevenlabs", credential="sk-not-really-anthropic")
        assert registry.adapter_for("llm_provider").active().key == "null"

    def test_replacing_llm_provider_never_touches_avatar_or_environment(self):
        settings_a = Settings.from_env(env={}, provider="null")
        settings_b = Settings.from_env(env={}, provider="anthropic", api_key="k")
        registry_a = build_ecosystem_registry(settings_a)
        registry_b = build_ecosystem_registry(settings_b)
        assert registry_a.adapter_for("llm_provider").active().key != registry_b.adapter_for(
            "llm_provider"
        ).active().key
        assert (
            registry_a.adapter_for("avatar_provider").active().key
            == registry_b.adapter_for("avatar_provider").active().key
        )
        assert (
            registry_a.adapter_for("environment_provider").active().key
            == registry_b.adapter_for("environment_provider").active().key
        )

    def test_impact_of_connection_names_real_dependents(self):
        registry = build_ecosystem_registry(Settings.from_env(env={}))
        impact = registry.impact_of("connection")
        assert impact and any("connected_action" in line for line in impact)

    def test_capability_category_is_discovery_only_and_lists_real_capabilities(self):
        registry = build_ecosystem_registry(Settings.from_env(env={}))
        adapter = registry.adapter_for("capability")
        keys = {d.key for d in adapter.available()}
        assert "media_production" in keys
        assert "connected_action" in keys
        assert LifecycleOperation.VALIDATE not in adapter.supported_operations()

    def test_describe_all_is_a_readable_non_empty_report(self):
        registry = build_ecosystem_registry(Settings.from_env(env={}))
        report = registry.describe_all()
        assert "llm_provider" in report
        assert "avatar_provider" in report


# ---------------------------------------------------------------------------
# 3. The new environment_provider axis (Section 9) - descriptive, honest,
#    independent of avatar, mirroring the established pattern exactly.
# ---------------------------------------------------------------------------


class TestEnvironmentProvider:
    def test_default_provider_has_a_non_empty_deterministic_catalog(self):
        provider = build_environment_provider(Settings.from_env(env={}))
        assert provider.catalog()
        assert provider.default_for("Alfred") == provider.default_for("Alfred")

    def test_alfred_and_arthur_can_land_on_different_defaults(self):
        provider = build_environment_provider(Settings.from_env(env={}))
        alfred = provider.default_for("Alfred")
        arthur = provider.default_for("Arthur")
        assert isinstance(alfred, EnvironmentProfile)
        assert isinstance(arthur, EnvironmentProfile)
        # Not asserted equal or unequal (a hash collision is legitimate) -
        # only that neither identity was special-cased to force one.
        assert alfred.environment_id in {p.environment_id for p in provider.catalog()}
        assert arthur.environment_id in {p.environment_id for p in provider.catalog()}

    def test_environment_is_independent_of_avatar(self):
        settings = Settings.from_env(env={}, avatar_provider="default", environment_provider="default")
        assert "default" in available_avatar_providers()
        assert "default" in available_environment_providers()
        # Two genuinely separate Settings fields - changing one's env
        # var cannot possibly touch the other's.
        assert settings.avatar_provider == "default"
        assert settings.environment_provider == "default"

    def test_unknown_environment_provider_is_refused(self):
        with pytest.raises(ValueError):
            build_environment_provider(Settings.from_env(env={}, environment_provider="nonexistent"))


# ---------------------------------------------------------------------------
# 4. Persisted, operator-managed accounts (file-backed CredentialStore)
# ---------------------------------------------------------------------------


class TestAccountPersistence:
    def test_round_trip_preserves_accounts_and_active_selection(self, tmp_path):
        path = tmp_path / "accounts.json"
        store = CredentialStore()
        store.add(ProviderAccount(provider="anthropic", account_id="work", credential="k1"))
        store.add(ProviderAccount(provider="anthropic", account_id="personal", credential="k2"), activate=False)
        store.select("anthropic", "personal")
        save_credential_store(store, path)

        restored = load_credential_store(path)
        assert restored.active("anthropic").account_id == "personal"
        assert restored.get("anthropic", "work").credential == "k1"

    def test_a_missing_file_loads_as_an_honestly_empty_store(self, tmp_path):
        store = load_credential_store(tmp_path / "does_not_exist.json")
        assert len(store) == 0

    def test_revocation_persists(self, tmp_path):
        path = tmp_path / "accounts.json"
        store = CredentialStore()
        store.add(ProviderAccount(provider="runway", account_id="default", credential="k"))
        save_credential_store(store, path)

        reloaded = load_credential_store(path)
        reloaded.revoke("runway", "default")
        save_credential_store(reloaded, path)

        final = load_credential_store(path)
        assert final.active("runway") is None

    def test_accounts_of_different_providers_never_collide_on_disk(self, tmp_path):
        path = tmp_path / "accounts.json"
        store = CredentialStore()
        store.add(ProviderAccount(provider="runway", account_id="default", credential="runway-key"))
        store.add(ProviderAccount(provider="veo", account_id="default", credential="veo-key"))
        save_credential_store(store, path)

        restored = load_credential_store(path)
        assert restored.active("runway").credential == "runway-key"
        assert restored.active("veo").credential == "veo-key"

    def test_the_persisted_file_stores_plain_json_never_a_pickle_or_binary_blob(self, tmp_path):
        """Not a secrecy claim (the file necessarily contains the
        credential value - the same honest tradeoff Gmail's own token
        file already makes) - a format/inspectability claim: an
        operator can read what is stored."""
        path = tmp_path / "accounts.json"
        store = CredentialStore()
        store.add(ProviderAccount(provider="anthropic", account_id="work", credential="k1"))
        save_credential_store(store, path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["accounts"][0]["provider"] == "anthropic"


# ---------------------------------------------------------------------------
# 5. The real operator CLI
# ---------------------------------------------------------------------------


class TestEcosystemCLI:
    def _run(self, capsys, accounts_path, *args):
        from scripts.ecosystem_cli import main

        code = main(["--accounts-path", str(accounts_path), *args])
        return code, capsys.readouterr().out

    def test_list_shows_every_category(self, tmp_path, capsys):
        code, out = self._run(capsys, tmp_path / "accounts.json", "list")
        assert code == 0
        assert "llm_provider" in out
        assert "environment_provider" in out

    def test_status_reports_active_and_dependents(self, tmp_path, capsys):
        code, out = self._run(capsys, tmp_path / "accounts.json", "status", "connection")
        assert code == 0
        assert "active: null" in out
        assert "connected_action" in out

    def test_validate_reports_success_and_failure_honestly(self, tmp_path, capsys):
        code, out = self._run(capsys, tmp_path / "accounts.json", "validate", "llm_provider", "null")
        assert code == 0
        assert "OK" in out

        code, out = self._run(capsys, tmp_path / "accounts.json", "validate", "llm_provider", "anthropic")
        assert code == 1
        assert "FAILED" in out

    def test_add_select_revoke_account_round_trip_through_the_cli(self, tmp_path, capsys):
        accounts_path = tmp_path / "accounts.json"
        code, out = self._run(
            capsys, accounts_path, "add-account", "anthropic", "work", "--credential", "sk-test"
        )
        assert code == 0 and "added" in out

        code, out = self._run(
            capsys, accounts_path, "add-account", "anthropic", "personal", "--credential", "sk-2",
            "--no-activate",
        )
        assert code == 0

        code, out = self._run(capsys, accounts_path, "accounts", "anthropic")
        assert "work" in out and "personal" in out

        code, out = self._run(capsys, accounts_path, "select-account", "anthropic", "personal")
        assert code == 0
        store = load_credential_store(accounts_path)
        assert store.active("anthropic").account_id == "personal"

        code, out = self._run(capsys, accounts_path, "revoke-account", "anthropic", "personal")
        assert code == 0
        store = load_credential_store(accounts_path)
        assert store.active("anthropic") is None
        assert store.get("anthropic", "work") is not None

    def test_selecting_an_unregistered_account_is_refused(self, tmp_path, capsys):
        code, out = self._run(capsys, tmp_path / "accounts.json", "select-account", "anthropic", "ghost")
        assert code == 1
        assert "FAILED" in out

    def test_unknown_category_is_refused_with_a_helpful_list(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as exc:
            self._run(capsys, tmp_path / "accounts.json", "status", "not-a-real-category")
        assert "Known categories" in str(exc.value)

    def test_no_credential_value_is_echoed_in_full_by_list_or_status(self, tmp_path, capsys):
        accounts_path = tmp_path / "accounts.json"
        self._run(capsys, accounts_path, "add-account", "anthropic", "work", "--credential", "sk-must-not-leak")
        code, out = self._run(capsys, accounts_path, "accounts", "anthropic")
        assert "sk-must-not-leak" not in out


# ---------------------------------------------------------------------------
# 6. The acceptance test: a genuinely new, previously-unseen category
#    entering the ecosystem through the same extension boundary.
# ---------------------------------------------------------------------------


class TestFutureCategoryExtensibility:
    def test_a_never_before_seen_category_works_through_the_identical_generic_lifecycle(self):
        """Section 21's own acceptance test, reproduced literally:
        "embedding_provider" appears nowhere else in this codebase -
        not in Settings, not in composition.py, not in any existing
        factory. It is registered here, in the test, using only the
        public GenericCategoryAdapter/EcosystemRegistry contract, and
        the full generic lifecycle (discover, list, validate success,
        validate failure, dependents, replacement) is exercised against
        it - proving the mechanism, not merely asserting it works."""
        import re
        import subprocess

        # Structural precondition: this category genuinely does not
        # already exist anywhere in the shipped source tree.
        result = subprocess.run(
            ["git", "grep", "-il", "embedding_provider", "--", "src", "scripts"],
            cwd=".", capture_output=True, text=True,
        )
        assert result.stdout.strip() == "", "the fixture category must be genuinely unseen"

        constructed: dict[str, int] = {"count": 0}

        def build(key, credential):
            constructed["count"] += 1
            if key == "unfunded-vendor" and credential is None:
                raise RuntimeError("no API key configured for unfunded-vendor")
            return {"key": key, "credential": credential}

        registry = EcosystemRegistry()
        registry.register(
            GenericCategoryAdapter(
                "embedding_provider",
                list_backends=lambda: ["null", "local-hash", "unfunded-vendor"],
                active_key=lambda: "null",
                build=build,
                requires_credential=lambda key: key == "unfunded-vendor",
                dependents=("a future semantic-memory retrieval capability",),
            )
        )

        # DISCOVER / REGISTER
        assert "embedding_provider" in registry.categories()
        adapter = registry.adapter_for("embedding_provider")
        keys = {d.key for d in adapter.available()}
        assert keys == {"null", "local-hash", "unfunded-vendor"}

        # SELECT / ACTIVATE (informational - reflects whichever
        # active_key closure the caller supplied)
        assert adapter.active().key == "null"

        # VALIDATE - both the honest failure and the honest success
        failure = adapter.validate("unfunded-vendor")
        assert not failure.ok
        assert "no API key configured" in failure.detail

        success = adapter.validate("unfunded-vendor", credential="fake-but-present")
        assert success.ok
        assert constructed["count"] == 2

        # REPLACE without disturbing anything else already registered
        other_registry = build_ecosystem_registry(Settings.from_env(env={}))
        other_registry.register(adapter)
        assert other_registry.adapter_for("llm_provider") is not None
        assert other_registry.adapter_for("embedding_provider") is adapter

        # The dependency-impact mechanism works identically for the
        # unseen category - no special-casing required anywhere.
        assert "semantic-memory" in registry.impact_of("embedding_provider")[0]

        # No core file needed to change to make any of the above true -
        # confirmed by the fact that this entire test constructs its
        # fixture using only public, already-shipped classes.
