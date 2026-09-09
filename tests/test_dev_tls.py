"""Private device-access checkpoint: `scripts/dev_tls.py`.

The full-system audit (2026-09-09) found a real gap: `scripts/serve_ui.py`
speaks plain HTTP only, and every modern mobile browser refuses
`getUserMedia` outside a secure context (HTTPS, or the special-cased
`localhost`). This module closes that gap with a locally-generated,
self-signed certificate covering whichever addresses this machine is
actually reachable at - never a public one, never a fixed address baked
into source.

`tests/conftest.py::_no_network` makes any real `socket.socket.connect`
(and `ssl.SSLContext.wrap_socket`) a loud `AssertionError` for this whole
suite, on purpose - so these tests never let `detect_private_addresses()`
attempt its real UDP route-probe or DNS lookup un-mocked, and never call
`build_ssl_context(...).wrap_socket(...)` (that happens only in
`scripts/serve_ui.py::main()`, never here). Where a test genuinely wants to
prove `detect_private_addresses()`'s own fallback logic, it uses the exact
escape hatch that fixture's own docstring describes: monkeypatching
`socket.socket.connect` locally, to a controlled fake, for that one test.

The actual wire-level proof - a real certificate, a real TLS handshake, a
real headless browser reporting `window.isSecureContext` flip from `false`
to `true` at this machine's own real LAN address once `--https` is used -
is proven separately, by hand, outside pytest, exactly as
`tests/test_live_ui.py`'s own module docstring already establishes for
`route_message`'s wire-level behaviour.
"""

from __future__ import annotations

import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.dev_tls import (
    CertificateInfo,
    TLSUnavailable,
    build_ssl_context,
    detect_private_addresses,
    ensure_certificate,
    fingerprint_sha256,
)


# ---------------------------------------------------------------------------
# detect_private_addresses(): the one function allowed to touch a real
# socket - proven here entirely through the fixture's own documented
# local-monkeypatch escape hatch, never a real connect/lookup.
# ---------------------------------------------------------------------------


class TestDetectPrivateAddressesIsDefensive:
    def test_loopback_names_are_always_present_even_if_every_probe_fails(self, monkeypatch):
        """A machine with no route and no resolvable hostname must still
        get a usable result - this machine's own browser can always
        reach itself at localhost."""
        def failing_connect(self, *a, **k):
            raise OSError("network unreachable")

        def failing_hostbyname(*a, **k):
            raise OSError("no such host")

        monkeypatch.setattr(socket.socket, "connect", failing_connect)
        monkeypatch.setattr(socket, "gethostbyname_ex", failing_hostbyname)
        monkeypatch.setattr(socket, "gethostname", lambda: "unreachable-host")

        result = detect_private_addresses()
        assert {"127.0.0.1", "localhost", "::1"} <= set(result)

    def test_a_real_lan_address_the_probe_finds_is_included(self, monkeypatch):
        """When the routing-table probe succeeds, its discovered address
        is genuinely included - not just the loopback fallback."""
        class FakeSocket:
            def __init__(self, *a, **k):
                pass

            def connect(self, addr):
                pass

            def getsockname(self):
                return ("192.0.2.55", 0)

            def close(self):
                pass

        monkeypatch.setattr(socket, "socket", lambda *a, **k: FakeSocket())
        monkeypatch.setattr(socket, "gethostname", lambda: "test-host")
        monkeypatch.setattr(socket, "gethostbyname_ex", lambda h: (h, [], ["10.0.0.9"]))

        result = detect_private_addresses()
        assert "192.0.2.55" in result
        assert "10.0.0.9" in result
        assert "127.0.0.1" in result  # still present alongside the real one, not replaced by it

    def test_the_result_is_sorted_and_deduplicated(self, monkeypatch):
        class FakeSocket:
            def __init__(self, *a, **k):
                pass

            def connect(self, addr):
                pass

            def getsockname(self):
                return ("127.0.0.1", 0)  # deliberately collides with the always-included constant

            def close(self):
                pass

        monkeypatch.setattr(socket, "socket", lambda *a, **k: FakeSocket())
        monkeypatch.setattr(socket, "gethostname", lambda: "localhost")  # deliberate collision too
        monkeypatch.setattr(socket, "gethostbyname_ex", lambda h: (h, [], ["127.0.0.1"]))

        result = detect_private_addresses()
        assert result == tuple(sorted(set(result)))
        assert len(result) == len(set(result))


# ---------------------------------------------------------------------------
# CertificateInfo: pure logic, no disk, no socket.
# ---------------------------------------------------------------------------


class TestCertificateInfoLogic:
    def _info(self, *, hosts=("127.0.0.1", "localhost"), expires_in_days=100):
        expires_at = (datetime.now(timezone.utc) + timedelta(days=expires_in_days)).isoformat()
        return CertificateInfo(
            cert_path=Path("unused.crt"), key_path=Path("unused.key"),
            hosts=hosts, expires_at=expires_at,
        )

    def test_covers_is_a_real_subset_check(self):
        info = self._info(hosts=("127.0.0.1", "localhost", "192.168.1.5"))
        assert info.covers(("127.0.0.1", "localhost"))
        assert not info.covers(("127.0.0.1", "10.0.0.9"))  # a genuinely new address must fail this

    def test_expires_soon_is_true_within_the_renewal_window(self):
        assert self._info(expires_in_days=5).expires_soon()
        assert not self._info(expires_in_days=100).expires_soon()

    def test_a_malformed_expiry_string_is_treated_as_already_expiring(self):
        """Honest degradation, not a crash - the identical discipline
        every other real-system-state parser in this project already
        holds itself to: a value that cannot be trusted is treated as
        the least permissive case, not silently accepted."""
        info = CertificateInfo(cert_path=Path("x"), key_path=Path("y"), hosts=(), expires_at="not-a-date")
        assert info.expires_soon()


# ---------------------------------------------------------------------------
# ensure_certificate(): real files on a real tmp_path, hosts supplied
# explicitly via extra_hosts + a monkeypatched detect_private_addresses so
# no test depends on this machine's own real network shape.
# ---------------------------------------------------------------------------


class TestCertificateGenerationAndReuse:
    @pytest.fixture(autouse=True)
    def _fixed_detection(self, monkeypatch):
        """Every test in this class runs against a fixed, fake address
        set - never this machine's own real network, and never a real
        socket call."""
        monkeypatch.setattr(
            "scripts.dev_tls.detect_private_addresses",
            lambda: ("127.0.0.1", "localhost", "::1"),
        )

    def test_generates_a_real_certificate_and_key_on_disk(self, tmp_path):
        info = ensure_certificate(tls_dir=tmp_path)
        assert info.cert_path.exists()
        assert info.key_path.exists()
        assert info.cert_path.read_bytes().startswith(b"-----BEGIN CERTIFICATE-----")
        assert info.key_path.read_bytes().startswith(b"-----BEGIN PRIVATE KEY-----")
        assert set(("127.0.0.1", "localhost", "::1")) <= set(info.hosts)

    def test_reuses_an_existing_certificate_that_already_covers_what_is_needed(self, tmp_path):
        first = ensure_certificate(tls_dir=tmp_path)
        second = ensure_certificate(tls_dir=tmp_path)
        assert second.expires_at == first.expires_at  # genuinely the same certificate, not regenerated

    def test_regenerates_when_an_extra_host_is_newly_required(self, tmp_path):
        first = ensure_certificate(tls_dir=tmp_path)
        second = ensure_certificate(tls_dir=tmp_path, extra_hosts=("192.168.1.50",))
        assert "192.168.1.50" in second.hosts
        assert second.expires_at != first.expires_at  # a real, new certificate was written

    def test_a_missing_metadata_file_is_treated_as_no_existing_certificate(self, tmp_path):
        """A cert/key present with no readable metadata must not be
        blindly trusted and reused - the honest response is to generate
        a fresh, known-good one."""
        ensure_certificate(tls_dir=tmp_path)
        (tmp_path / "alfred-local.json").unlink()
        # Must not raise, must produce a real, usable certificate again.
        info = ensure_certificate(tls_dir=tmp_path)
        assert info.cert_path.exists()

    def test_the_private_key_lives_only_under_the_given_tls_dir(self, tmp_path):
        info = ensure_certificate(tls_dir=tmp_path)
        assert info.key_path.parent == tmp_path


class TestFingerprintAndSslContext:
    @pytest.fixture(autouse=True)
    def _fixed_detection(self, monkeypatch):
        monkeypatch.setattr(
            "scripts.dev_tls.detect_private_addresses", lambda: ("127.0.0.1", "localhost"),
        )

    def test_fingerprint_is_a_real_sha256_of_the_actual_certificate_bytes(self, tmp_path):
        info = ensure_certificate(tls_dir=tmp_path)
        fp = fingerprint_sha256(info.cert_path)
        # 32 bytes of SHA-256, rendered as 32 colon-separated uppercase hex pairs.
        parts = fp.split(":")
        assert len(parts) == 32
        assert all(len(p) == 2 and all(c in "0123456789ABCDEF" for c in p) for p in parts)

        # A different certificate must genuinely fingerprint differently -
        # this is not a fixed, decorative string.
        other = ensure_certificate(tls_dir=tmp_path, extra_hosts=("203.0.113.9",))
        assert fingerprint_sha256(other.cert_path) != fp

    def test_build_ssl_context_loads_the_real_certificate_without_error(self, tmp_path):
        """Only constructs the context and loads the chain - never calls
        `.wrap_socket()`, which `tests/conftest.py::_no_network` blocks
        on purpose for this whole suite. The real wrap/serve path is
        verified live, outside pytest (see module docstring)."""
        import ssl

        info = ensure_certificate(tls_dir=tmp_path)
        context = build_ssl_context(info)
        assert isinstance(context, ssl.SSLContext)


class TestTLSUnavailableWhenCryptographyIsMissing:
    def test_a_missing_cryptography_package_fails_honestly_not_silently(self, tmp_path, monkeypatch):
        """No fallback to plain HTTP, no fabricated certificate - the
        identical honest-refusal shape `check_configuration` already
        uses in `scripts/serve_ui.py::main()`. Simulated by making the
        import itself fail, the standard `sys.modules[...] = None`
        technique - never actually uninstalling the real dependency."""
        monkeypatch.setitem(sys.modules, "cryptography", None)
        monkeypatch.setattr(
            "scripts.dev_tls.detect_private_addresses", lambda: ("127.0.0.1",),
        )
        with pytest.raises(TLSUnavailable, match="cryptography"):
            ensure_certificate(tls_dir=tmp_path)
