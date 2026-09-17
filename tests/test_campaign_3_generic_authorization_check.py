"""Closure campaign 3 (2026-09-17) - the live authorization check stopped
asking *which category* and started asking *the backend*.

The gap, found by reading the ledger's own "genuinely open" list rather than
the code: `provider <category> check <backend>` was gated on a hardcoded
`_CHECKABLE_CATEGORIES = (LLM_CATEGORY, "speech_provider")`. The mechanism
behind it (`check_speech_authorization`) was already vendor-neutral over any
adapter carrying `probe_authorization`, so the tuple was the only thing
standing between a video or image backend and a real check - a fixed list
needing new code per category, which is precisely the shape CON-05 records
this project as refusing everywhere else.

The category no longer decides. `CategoryAdapter.construct` hands back the
same backend `validate` already builds and discards, and the built backend is
asked whether it can prove its own credential. These tests pin the doctrine:
a category that did not exist when this file was written becomes checkable
with **zero** change to `scripts/control_center.py`.

No test here reaches a real vendor.
"""

from __future__ import annotations

import pytest

import scripts.control_center as cc
from src.config.settings import Settings
from src.services.credentials.account import ProviderAccount
from src.services.credentials.authorization import check_vendor_authorization
from src.services.ecosystem import registry_factory
from src.services.ecosystem.contracts import (
    CategoryAdapter,
    EcosystemRegistry,
    GenericCategoryAdapter,
)


class _ProbeWorks:
    name = "inventedvendor"

    def __init__(self, account=None):
        self.account = account

    def probe_authorization(self):
        return 7


class _NoProbe:
    name = "plainvendor"


class TestTheMechanismAsksTheBackendNotTheCategory:
    def test_a_backend_with_a_probe_is_authorized_on_its_own_answer(self):
        outcome = check_vendor_authorization(_ProbeWorks())
        assert outcome.authorized and outcome.reached_vendor
        assert "7 real result(s)" in outcome.reason

    def test_the_unit_is_the_caller_s_word_so_the_reason_reads_honestly(self):
        outcome = check_vendor_authorization(_ProbeWorks(), unit="voice")
        assert "7 real voice(s)" in outcome.reason

    def test_a_backend_without_a_probe_is_refused_never_guessed(self):
        outcome = check_vendor_authorization(_NoProbe())
        assert outcome.authorized is False and outcome.reached_vendor is False
        assert "no live authorization probe" in outcome.reason
        assert "plainvendor" in outcome.reason

    def test_a_probe_that_raises_is_reported_as_never_reached(self):
        class _Raises:
            name = "x"

            def probe_authorization(self):
                raise RuntimeError("401 unauthorized")

        outcome = check_vendor_authorization(_Raises())
        assert outcome.authorized is False and outcome.reached_vendor is False
        assert "unauthorized" in outcome.reason.lower()


class TestConstructHandsBackWhatValidateDiscards:
    def _adapter(self, build=None):
        return GenericCategoryAdapter(
            "invented_category",
            list_backends=lambda: ["inventedvendor"],
            active_key=lambda: None,
            build=build,
        )

    def test_construct_returns_the_real_built_backend(self):
        built = _ProbeWorks()
        adapter = self._adapter(build=lambda key, credential: built)
        assert adapter.construct("inventedvendor") is built

    def test_validate_still_only_reports_and_never_returns_the_object(self):
        adapter = self._adapter(build=lambda key, credential: _ProbeWorks())
        outcome = adapter.validate("inventedvendor")
        assert outcome.ok and "constructed successfully" in outcome.detail

    def test_a_category_with_no_build_path_refuses_to_hand_back_anything(self):
        adapter = self._adapter(build=None)
        with pytest.raises(NotImplementedError):
            adapter.construct("inventedvendor")

    def test_the_base_contract_refuses_by_default_rather_than_returning_none(self):
        """A None would be mistaken for a backend; NotImplementedError cannot."""

        class _Bare(CategoryAdapter):
            @property
            def category(self):
                return "bare"

            def available(self):
                return ()

            def active(self):
                return None

        with pytest.raises(NotImplementedError):
            _Bare().construct("anything")

    def test_construction_errors_propagate_rather_than_being_swallowed(self):
        def _explode(key, credential):
            raise ValueError("no such model")

        with pytest.raises(ValueError):
            self._adapter(build=_explode).construct("inventedvendor")


class TestTheRealAccountReachesTheBuilderNotASyntheticOne:
    """The regression this repair could have introduced. An account may pin
    its own ``model``; routing the check through a bare credential string
    would have silently built against the default and checked something other
    than what the account describes."""

    def test_a_real_account_is_passed_through_untouched(self):
        account = ProviderAccount(
            provider="anthropic", account_id="owner", credential="secret", model="pinned-model"
        )
        assert registry_factory._credentialed_account("anthropic", account) is account

    def test_a_bare_credential_is_still_wrapped_for_validate(self):
        made = registry_factory._credentialed_account("anthropic", "secret")
        assert made.credential == "secret"
        assert made.account_id == registry_factory._VALIDATION_ACCOUNT_ID

    def test_no_credential_stays_none(self):
        assert registry_factory._credentialed_account("anthropic", None) is None


class TestAFutureCategoryNeedsNoControlCenterChange:
    """The point of the repair, proven the only way that means anything: a
    category invented here, in this test, checked through the unmodified
    Control Center command."""

    @pytest.fixture
    def stores(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cc, "DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json")
        monkeypatch.setattr(cc, "DEFAULT_CONFIG_PATH", tmp_path / "config.json")
        return tmp_path

    def _centre(self, tmp_path):
        settings = Settings.from_env(
            env={}, memory="ephemeral", profile_path="does-not-exist-USER.md",
            artifacts_dir=str(tmp_path / "artifacts"), knowledge_dir=str(tmp_path / "k"),
            documents_dir=str(tmp_path / "d"),
        )
        runtime, manager, channel, conversation = cc.build(settings)
        return cc.ControlCenter(runtime, manager), channel, conversation

    def _with_invented_category(self, monkeypatch, *, backend):
        real = registry_factory.build_ecosystem_registry

        def _extended(settings) -> EcosystemRegistry:
            registry = real(settings)
            registry.register(
                GenericCategoryAdapter(
                    "invented_category",
                    list_backends=lambda: ["inventedvendor"],
                    active_key=lambda: None,
                    build=lambda key, credential: backend(credential),
                    requires_credential=lambda key: True,
                )
            )
            return registry

        monkeypatch.setattr(cc, "build_ecosystem_registry", _extended)

    def test_an_invented_category_with_a_probe_is_checked_with_no_code_change(
        self, stores, monkeypatch
    ):
        seen = {}

        def _backend(credential):
            seen["account"] = credential
            return _ProbeWorks(credential)

        self._with_invented_category(monkeypatch, backend=_backend)
        centre, channel, conversation = self._centre(stores)
        cc.run_command(
            centre, "provider invented_category add inventedvendor owner fake-key-not-real",
            100, channel, conversation,
        )
        out = cc.run_command(
            centre, "provider invented_category check inventedvendor", 100, channel, conversation
        )
        assert out.startswith("AUTHORIZED: inventedvendor/owner")
        assert "7 real result(s)" in out
        assert "LIVE EXTERNAL VERIFIED" in out
        # the stored account itself reached the builder, not a synthetic one
        assert isinstance(seen["account"], ProviderAccount)
        assert seen["account"].account_id == "owner"

    def test_the_same_invented_category_without_a_probe_is_refused_honestly(
        self, stores, monkeypatch
    ):
        self._with_invented_category(monkeypatch, backend=lambda credential: _NoProbe())
        centre, channel, conversation = self._centre(stores)
        cc.run_command(
            centre, "provider invented_category add inventedvendor owner fake-key-not-real",
            100, channel, conversation,
        )
        out = cc.run_command(
            centre, "provider invented_category check inventedvendor", 100, channel, conversation
        )
        assert out.startswith("NOT AUTHORIZED: inventedvendor/owner")
        assert "no live authorization probe" in out

    def test_it_is_still_a_confirmed_outward_write_for_the_new_category(self):
        needs, described = cc.classify_administrative_write(
            "provider invented_category check inventedvendor owner"
        )
        assert needs
        assert "leaves the machine" in described
