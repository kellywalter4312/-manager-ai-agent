"""Private device-access checkpoint: `scripts/dev_tls.py`.

The full-system audit (2026-09-09) found a real gap: `scripts/serve_ui.py`
speaks plain HTTP only, and every modern mobile browser refuses
`getUserMedia` outside a secure context (HTTPS, or the special-cased
`localhost`). This module closes that gap with a locally-generated
Alfred Development CA signing a server (leaf) certificate covering
whichever addresses this machine is actually reachable at - never a
public one, never a fixed address baked into source.

**Real physical-device testing (2026-09-11) found the original design -
one self-signed leaf, trusted directly - genuinely insufficient**: both
iOS Safari and Android Chrome refused it with
`(NET::)ERR_CERT_AUTHORITY_INVALID` even after the device's own "full
trust" step was completed, because a `CA:FALSE` certificate cannot serve
as a trust anchor regardless of trust settings. The CA/leaf split below
(``CAInfo``/``ensure_ca`` alongside the original ``CertificateInfo``/
``ensure_certificate``) is the confirmed fix; several tests below verify
the exact properties (BasicConstraints, KeyUsage, issuer/subject,
chain validation) that made the difference.

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

pytest.importorskip("cryptography", reason="cryptography package not installed")

from scripts.dev_tls import (
    CAInfo,
    CertificateInfo,
    TLSUnavailable,
    build_ssl_context,
    describe_trust_steps,
    detect_private_addresses,
    ensure_ca,
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


class TestAlfredDevelopmentCA:
    """The persistent local CA - the piece real physical iPhone/Android
    testing found genuinely missing from the original single-self-
    signed-leaf design (2026-09-11)."""

    @pytest.fixture(autouse=True)
    def _fixed_detection(self, monkeypatch):
        monkeypatch.setattr(
            "scripts.dev_tls.detect_private_addresses",
            lambda: ("127.0.0.1", "localhost", "::1"),
        )

    def test_ensure_ca_generates_a_real_ca_certificate_and_key_on_disk(self, tmp_path):
        ca = ensure_ca(tls_dir=tmp_path)
        assert ca.cert_path.exists()
        assert ca.key_path.exists()
        assert ca.cert_path.read_bytes().startswith(b"-----BEGIN CERTIFICATE-----")
        assert ca.key_path.read_bytes().startswith(b"-----BEGIN PRIVATE KEY-----")
        assert ca.cert_path.name == "alfred-ca.crt"
        assert ca.key_path.name == "alfred-ca.key"

    def test_the_ca_certificate_is_genuinely_a_ca(self):
        """BasicConstraints CA:TRUE, path_length=0 - the exact property
        a self-signed leaf (CA:FALSE) never had, and the exact reason a
        real iPhone and a real Android device both refused it even with
        full trust enabled."""
        from cryptography import x509

        with __import__("tempfile").TemporaryDirectory() as tmp:
            ca = ensure_ca(tls_dir=Path(tmp))
            cert = x509.load_pem_x509_certificate(ca.cert_path.read_bytes())
            constraints = cert.extensions.get_extension_for_class(x509.BasicConstraints)
            assert constraints.critical is True
            assert constraints.value.ca is True
            assert constraints.value.path_length == 0

    def test_the_ca_certificate_has_key_cert_sign_and_crl_sign(self):
        """The two KeyUsage bits that actually make a certificate usable
        as a trust anchor for signing other certificates - critical, and
        every other usage bit honestly False (this key never itself
        signs TLS traffic)."""
        from cryptography import x509

        with __import__("tempfile").TemporaryDirectory() as tmp:
            ca = ensure_ca(tls_dir=Path(tmp))
            cert = x509.load_pem_x509_certificate(ca.cert_path.read_bytes())
            usage = cert.extensions.get_extension_for_class(x509.KeyUsage)
            assert usage.critical is True
            assert usage.value.key_cert_sign is True
            assert usage.value.crl_sign is True
            assert usage.value.digital_signature is False

    def test_the_ca_is_self_signed_a_root_genuinely_is(self):
        from cryptography import x509

        with __import__("tempfile").TemporaryDirectory() as tmp:
            ca = ensure_ca(tls_dir=Path(tmp))
            cert = x509.load_pem_x509_certificate(ca.cert_path.read_bytes())
            assert cert.issuer == cert.subject

    def test_the_ca_persists_across_repeated_calls(self, tmp_path):
        first = ensure_ca(tls_dir=tmp_path)
        second = ensure_ca(tls_dir=tmp_path)
        assert second.expires_at == first.expires_at  # genuinely the same CA, not regenerated

    def test_leaf_renewal_does_not_silently_regenerate_the_ca(self, tmp_path):
        """The whole point of the split: the CA is the rarely-repeated,
        more-involved device-trust step; the leaf underneath it must be
        free to rotate on its own, much shorter cadence without ever
        touching the CA."""
        ca_before = ensure_ca(tls_dir=tmp_path)
        ensure_certificate(tls_dir=tmp_path)  # first leaf
        ensure_certificate(tls_dir=tmp_path, extra_hosts=("192.168.1.77",))  # forces leaf regeneration
        ca_after = ensure_ca(tls_dir=tmp_path)
        assert ca_after.expires_at == ca_before.expires_at

    def test_a_missing_ca_metadata_file_is_treated_as_no_existing_ca(self, tmp_path):
        ensure_ca(tls_dir=tmp_path)
        (tmp_path / "alfred-ca.json").unlink()
        ca = ensure_ca(tls_dir=tmp_path)
        assert ca.cert_path.exists()


class TestLeafIsSignedByTheCANeverItself:
    @pytest.fixture(autouse=True)
    def _fixed_detection(self, monkeypatch):
        monkeypatch.setattr(
            "scripts.dev_tls.detect_private_addresses",
            lambda: ("127.0.0.1", "localhost", "::1"),
        )

    def test_the_leaf_certificate_remains_not_a_ca(self, tmp_path):
        from cryptography import x509

        leaf = ensure_certificate(tls_dir=tmp_path)
        cert = x509.load_pem_x509_certificate(leaf.cert_path.read_bytes())
        constraints = cert.extensions.get_extension_for_class(x509.BasicConstraints)
        assert constraints.value.ca is False

    def test_the_leaf_issuer_is_the_ca_subject_not_the_leafs_own(self, tmp_path):
        """The exact fact that was wrong before this fix: issuer used to
        equal the leaf's own subject (self-signed). It must now equal
        the CA's subject - genuinely signed BY the CA, never itself."""
        from cryptography import x509

        ca = ensure_ca(tls_dir=tmp_path)
        leaf = ensure_certificate(tls_dir=tmp_path)
        ca_cert = x509.load_pem_x509_certificate(ca.cert_path.read_bytes())
        leaf_cert = x509.load_pem_x509_certificate(leaf.cert_path.read_bytes())
        assert leaf_cert.issuer == ca_cert.subject
        assert leaf_cert.issuer != leaf_cert.subject  # genuinely not self-signed

    def test_the_leaf_retains_every_required_san(self, tmp_path):
        from cryptography import x509

        leaf = ensure_certificate(
            tls_dir=tmp_path, extra_hosts=("172.20.10.2", "DESKTOP-87OAP7B"),
        )
        cert = x509.load_pem_x509_certificate(leaf.cert_path.read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        names = {str(entry.value) for entry in san}
        for required in ("127.0.0.1", "::1", "172.20.10.2", "DESKTOP-87OAP7B", "localhost"):
            assert any(required in name for name in names), f"missing SAN: {required}"

    def test_the_leaf_retains_browser_compatible_validity(self, tmp_path):
        from cryptography import x509

        leaf = ensure_certificate(tls_dir=tmp_path)
        cert = x509.load_pem_x509_certificate(leaf.cert_path.read_bytes())
        span = cert.not_valid_after_utc - cert.not_valid_before_utc
        assert span.days <= 398  # the real Apple/Chrome Root Program ceiling, confirmed 2026-09-11

    def test_the_server_certificate_and_private_key_are_a_genuine_pair(self, tmp_path):
        """The private key on disk must actually correspond to the
        public key inside the certificate - build_ssl_context's own
        load_cert_chain would otherwise fail at real server startup."""
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        leaf = ensure_certificate(tls_dir=tmp_path)
        cert = x509.load_pem_x509_certificate(leaf.cert_path.read_bytes())
        key = serialization.load_pem_private_key(leaf.key_path.read_bytes(), password=None)
        assert isinstance(key, rsa.RSAPrivateKey)
        assert key.public_key().public_numbers() == cert.public_key().public_numbers()


class TestChainValidation:
    """The single most important proof in this file: a client that
    trusts ONLY the generated CA - never the leaf directly, never
    anything else - genuinely validates the generated leaf, using
    cryptography's own real RFC 5280 path-building verifier (the same
    kind of engine a real browser uses), never a hand-rolled comparison.
    This is exactly the check that would have caught the original
    design's real failure before physical devices ever did.
    """

    @pytest.fixture(autouse=True)
    def _fixed_detection(self, monkeypatch):
        monkeypatch.setattr(
            "scripts.dev_tls.detect_private_addresses",
            lambda: ("127.0.0.1", "localhost", "::1"),
        )

    def test_a_client_trusting_only_the_ca_validates_the_real_leaf(self, tmp_path):
        import ipaddress

        from cryptography import x509
        from cryptography.x509.verification import PolicyBuilder, Store

        ca = ensure_ca(tls_dir=tmp_path)
        leaf = ensure_certificate(tls_dir=tmp_path)
        ca_cert = x509.load_pem_x509_certificate(ca.cert_path.read_bytes())
        leaf_cert = x509.load_pem_x509_certificate(leaf.cert_path.read_bytes())

        store = Store([ca_cert])
        verifier = PolicyBuilder().store(store).build_server_verifier(
            x509.verification.IPAddress(ipaddress.ip_address("127.0.0.1"))
        )
        chain = verifier.verify(leaf_cert, [])
        assert chain[0] == leaf_cert
        assert chain[-1] == ca_cert

    def test_a_client_trusting_only_the_ca_refuses_an_unrelated_certificate(self, tmp_path):
        """The negative case: chain validation must genuinely mean
        something - a certificate this CA never signed must be
        refused, never accepted merely because SOME CA is trusted."""
        import ipaddress

        from cryptography import x509
        from cryptography.x509.verification import PolicyBuilder, Store, VerificationError

        ca = ensure_ca(tls_dir=tmp_path)
        other_ca = ensure_ca(tls_dir=tmp_path / "other")  # a genuinely different CA
        unrelated_leaf = ensure_certificate(tls_dir=tmp_path / "other")

        ca_cert = x509.load_pem_x509_certificate(ca.cert_path.read_bytes())
        unrelated_leaf_cert = x509.load_pem_x509_certificate(unrelated_leaf.cert_path.read_bytes())

        store = Store([ca_cert])
        verifier = PolicyBuilder().store(store).build_server_verifier(
            x509.verification.IPAddress(ipaddress.ip_address("127.0.0.1"))
        )
        with pytest.raises(VerificationError):
            verifier.verify(unrelated_leaf_cert, [])

    def test_the_old_self_signed_leaf_shape_would_genuinely_fail_this_check(self, tmp_path):
        """Direct evidence the new architecture fixes a real defect, not
        an assumed one: a leaf trusted as its own issuer (the original
        design) fails the identical real verifier the new CA-signed leaf
        passes above."""
        import ipaddress

        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.x509.verification import PolicyBuilder, Store, VerificationError
        from datetime import datetime, timedelta, timezone

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Old-style self-signed")])
        now = datetime.now(timezone.utc)
        old_style = (
            x509.CertificateBuilder()
            .subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=396))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256())
        )
        store = Store([old_style])  # trusting the leaf directly, exactly as the old design asked a device to
        verifier = PolicyBuilder().store(store).build_server_verifier(
            x509.verification.IPAddress(ipaddress.ip_address("127.0.0.1"))
        )
        with pytest.raises(VerificationError):
            verifier.verify(old_style, [])


class TestMigrationFromTheOldSelfSignedArchitecture:
    """A leaf left over from before this checkpoint (self-signed,
    issuer == its own subject) must never be silently reused just
    because it still covers the right hosts and has not expired -
    ensure_certificate must detect it was not signed by the current CA
    and regenerate a genuinely CA-signed replacement."""

    @pytest.fixture(autouse=True)
    def _fixed_detection(self, monkeypatch):
        monkeypatch.setattr(
            "scripts.dev_tls.detect_private_addresses",
            lambda: ("127.0.0.1", "localhost", "::1"),
        )

    def test_a_stale_self_signed_leaf_is_replaced_not_reused(self, tmp_path, monkeypatch):
        import json as _json

        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from datetime import datetime, timedelta, timezone

        tmp_path.mkdir(parents=True, exist_ok=True)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Alfred (private, local)")])
        now = datetime.now(timezone.utc)
        old_style = (
            x509.CertificateBuilder()
            .subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=396))
            .add_extension(
                x509.SubjectAlternativeName([
                    x509.IPAddress(__import__("ipaddress").ip_address("127.0.0.1")),
                    x509.DNSName("localhost"),
                ]),
                critical=False,
            )
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256())
        )
        (tmp_path / "alfred-local.crt").write_bytes(old_style.public_bytes(serialization.Encoding.PEM))
        (tmp_path / "alfred-local.key").write_bytes(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        far_future = (now + timedelta(days=300)).isoformat()  # covers hosts, not near expiry - would be reused under the old logic
        (tmp_path / "alfred-local.json").write_text(
            _json.dumps({"hosts": ["127.0.0.1", "localhost"], "expires_at": far_future, "generated_at": now.isoformat()}),
            encoding="utf-8",
        )

        old_fingerprint = fingerprint_sha256(tmp_path / "alfred-local.crt")
        migrated = ensure_certificate(tls_dir=tmp_path)
        new_fingerprint = fingerprint_sha256(migrated.cert_path)

        assert new_fingerprint != old_fingerprint  # genuinely a new, different certificate
        ca = ensure_ca(tls_dir=tmp_path)
        assert _leaf_signed_by_ca(migrated.cert_path, ca.cert_path)


def _leaf_signed_by_ca(leaf_cert_path: Path, ca_cert_path: Path) -> bool:
    from cryptography import x509

    leaf_cert = x509.load_pem_x509_certificate(leaf_cert_path.read_bytes())
    ca_cert = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())
    return leaf_cert.issuer == ca_cert.subject


class TestDescribeTrustStepsPointsAtTheCA:
    @pytest.fixture(autouse=True)
    def _fixed_detection(self, monkeypatch):
        monkeypatch.setattr(
            "scripts.dev_tls.detect_private_addresses",
            lambda: ("127.0.0.1", "localhost", "::1"),
        )

    def test_instructions_reference_the_ca_file_and_fingerprint_not_the_leaf(self, tmp_path):
        ca = ensure_ca(tls_dir=tmp_path)
        leaf = ensure_certificate(tls_dir=tmp_path, extra_hosts=("192.168.50.10",))
        text = describe_trust_steps(ca, leaf)

        assert str(ca.cert_path) in text
        assert str(leaf.cert_path) not in text  # the leaf's own file path is never what a person installs
        assert fingerprint_sha256(ca.cert_path) in text
        assert fingerprint_sha256(leaf.cert_path) not in text
        # The leaf's own hosts still inform "reachable at", unchanged behaviour.
        assert "192.168.50.10" in text


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
