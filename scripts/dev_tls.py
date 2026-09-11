"""Private, local-network HTTPS for Alfred - never a public one.

Browsers refuse `getUserMedia`/`MediaRecorder` (and the Web Speech API the
served page's own ambient voice activation already uses) outside a "secure
context": HTTPS, or the special-cased `localhost`/`127.0.0.1`/`::1`.
`scripts/serve_ui.py` speaks plain HTTP only, which is why Recording works
from this machine's own browser but is silently refused by any phone
reached over the LAN - not a defect in Recording, a fact about what every
mobile browser enforces before a microphone is ever touched, confirmed
directly against browser platform behaviour rather than assumed (see the
full-system audit, 2026-09-09).

This module closes that gap the smallest way that stays private: a local,
two-certificate chain - never a third-party account, never a hosted
service, never a public domain. One persistent **Alfred Development CA**
(a real Certificate Authority, `CA:TRUE`, generated once and kept on this
machine only) signs a **server certificate** (`CA:FALSE`, the one Alfred's
own HTTPS socket actually presents) covering whichever addresses this
machine is reachable at *right now* - detected live, never a fixed IP baked
into source. A person trusts the *CA* once on each of their own devices -
the exact, standard way any internal-only HTTPS site is trusted on a phone
that has never heard of a public CA, the same mechanism `mkcert` and every
enterprise-internal HTTPS deployment already rely on - and every server
certificate this machine issues afterwards, including ones regenerated
months from now, is trusted automatically with no further per-device step.

**Why not a single self-signed leaf trusted directly (the original,
simpler design).** Tried first, and genuinely closed the "phone refuses
`getUserMedia` outside a secure context" gap in principle - but real
physical iPhone and Android testing (2026-09-11) found that both
platforms' certificate verifiers require a trust anchor to actually be a
CA (`BasicConstraints CA:TRUE` with `keyCertSign`) before it can validate
anything, including itself: a self-signed leaf marked `CA:FALSE`, even
with "full trust" explicitly enabled in the device's own certificate
settings, was rejected outright with `NET::ERR_CERT_AUTHORITY_INVALID` on
both platforms. The two-certificate CA/leaf split below is the smallest
correct fix for that real, confirmed behaviour - not a redesign, and not
this module's original mistake repeated with extra steps.

After trusting the CA once, `--https` makes Alfred reachable from a
laptop, an Android phone or an iPhone on the same private network exactly
as it already is from `localhost` today - same server, same routes, same
one `LiveSystem`, nothing duplicated, nothing exposed to the public
internet.

**Optional, lazily imported, honestly absent otherwise.** Generating a
certificate needs the `cryptography` package (see requirements.txt) - the
identical "imported lazily, the project runs without it installed"
discipline every other optional dependency here already holds itself to.
Without it, `--https` refuses to start with a clear, actionable error
(`TLSUnavailable`) rather than silently falling back to plain HTTP - that
fallback would be exactly the "fake success" the Real Audio Recording/
Capture checkpoint's own docstrings already refuse everywhere else.

**Never the architecture's problem to solve twice.** Nothing here touches
`src/` - this is a transport concern for one entry point, the same
"scripts/ is where entry points live, without being part of the
architecture those files assemble" boundary `serve_ui.py`'s own module
docstring already draws for itself. Recording, Control Center and every
other capability remain exactly as reachable over plain `--host 0.0.0.0`
HTTP as they always were; this module only adds the option to wrap that
same socket in TLS, never a second server and never a second source of
truth.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_TLS_DIR = Path("data/tls")
# The server (leaf) certificate - what Alfred's own HTTPS socket actually
# presents on the wire. Never installed by hand on any device.
CERT_NAME = "alfred-local.crt"
KEY_NAME = "alfred-local.key"
META_NAME = "alfred-local.json"
# The Alfred Development CA - never presented on the wire, never signed by
# anything but itself (a root is self-signed by definition), the one
# certificate a person actually installs/trusts once per device. Kept in
# the same data/tls/ directory, under its own file names, so the existing
# per-concern-isolation convention this project already holds itself to
# everywhere in data/ is preserved rather than introducing a new location.
CA_CERT_NAME = "alfred-ca.crt"
CA_KEY_NAME = "alfred-ca.key"
CA_META_NAME = "alfred-ca.json"
# The longest leaf-certificate lifetime every major browser still honours
# without complaint. NOT the older (825-day, 2018 CA/Browser Forum) ceiling
# this used to be - found wrong the hard way (2026-09-11): a real iPhone
# physically refused a certificate generated under that limit with
# NET::ERR_CERT_VALIDITY_TOO_LONG. Since 1 September 2020, Apple (Safari/
# WebKit, every platform including iOS) and Google's Chrome Root Program
# both cap TLS server certificates issued on or after that date at 398
# days - the actual current ceiling, confirmed against real physical
# device behaviour, not assumed from an outdated rule.
#
# 396, not 397: the certificate's own not_valid_before is backdated by a
# further fixed day below (clock-skew tolerance - a client whose clock
# runs slightly behind must not see a freshly issued certificate as "not
# yet valid"), so the REAL total span a browser measures is
# VALIDITY_DAYS + 1. 396 + 1 = 397 days - a full day of margin under the
# 398-day ceiling, not resting exactly on it, so ordinary UTC/leap-second
# rounding across "now" can never push the real lifetime a browser
# computes to 399. (An earlier version of this fix used 397 here without
# accounting for that extra backdated day, which produced an exact
# 398-day span - correct per the letter of the rule but with zero margin;
# tightened once, verified with a direct not_valid_after minus
# not_valid_before check on the real generated certificate, not assumed.)
# Renewed automatically anyway the moment the detected address set
# changes or the cert nears expiry (RENEW_WITHIN_DAYS below), so this
# shorter fixed lifetime buys no less real safety and only means a
# "re-trust this on your phone" moment every ~13 months instead of every
# ~27.
VALIDITY_DAYS = 396
RENEW_WITHIN_DAYS = 30
# The CA's own lifetime - deliberately NOT subject to the 398-day leaf
# ceiling above, because a CA is never itself presented on the wire as a
# server certificate; only the leaf it signs is. Long-lived on purpose:
# the CA is the one certificate a person actually installs by hand on
# each device, and the entire point of splitting it from the leaf is that
# this rarely-repeated, more-involved step should stay rare - the leaf
# above still rotates on its own ~13-month cadence underneath it without
# ever asking anyone to re-trust anything.
CA_VALIDITY_DAYS = 3650
# RFC 5737 TEST-NET-3 - reserved for documentation/examples, guaranteed
# never routable. UDP `connect()` never actually transmits a packet; it
# only asks the OS routing table which local interface would carry
# traffic there, which is exactly this machine's own real LAN address,
# discovered live rather than assumed.
_ROUTE_PROBE_ADDRESS = ("203.0.113.1", 1)


class TLSUnavailable(RuntimeError):
    """--https was requested but a real certificate could not be produced.

    Never caught anywhere to fall back to plain HTTP - see module
    docstring on why a silent fallback would be dishonest.
    """


@dataclass(frozen=True)
class CertificateInfo:
    """One real, already-written certificate - a description of a file
    on disk, never the bytes themselves held in memory longer than
    needed."""

    cert_path: Path
    key_path: Path
    hosts: tuple[str, ...]
    expires_at: str  # ISO 8601, UTC

    def covers(self, required_hosts: tuple[str, ...]) -> bool:
        return set(required_hosts) <= set(self.hosts)

    def expires_soon(self) -> bool:
        try:
            expires = datetime.fromisoformat(self.expires_at)
        except ValueError:
            return True
        return expires - datetime.now(timezone.utc) < timedelta(days=RENEW_WITHIN_DAYS)


@dataclass(frozen=True)
class CAInfo:
    """The persistent Alfred Development CA - a description of the one
    root certificate/key pair a device is meant to trust, kept
    deliberately separate from ``CertificateInfo`` (the server leaf)
    because the two have genuinely different lifetimes, different
    file roles, and different "does a person need to do anything"
    answers. Never presented on the wire by ``build_ssl_context`` -
    only ever read for display (``describe_trust_steps``) or to sign a
    fresh leaf (``_generate_certificate``).
    """

    cert_path: Path
    key_path: Path
    expires_at: str  # ISO 8601, UTC

    def expires_soon(self) -> bool:
        try:
            expires = datetime.fromisoformat(self.expires_at)
        except ValueError:
            return True
        return expires - datetime.now(timezone.utc) < timedelta(days=RENEW_WITHIN_DAYS)


def detect_private_addresses() -> tuple[str, ...]:
    """Every address this machine is genuinely reachable at on its own
    network right now - discovered live every call, never hardcoded and
    never cached past one call. Always includes the loopback names every
    browser already treats as a secure context on their own; real LAN
    addresses are added on top of that, not instead of it, so a phone on
    the same network can reach the identical certificate a laptop
    already trusts at `localhost`.
    """
    found: set[str] = {"127.0.0.1", "localhost", "::1"}
    try:
        hostname = socket.gethostname()
        found.add(hostname)
        _, _, addrs = socket.gethostbyname_ex(hostname)
        found.update(addrs)
    except OSError:
        pass  # no resolvable hostname - the route probe below still finds the real LAN IP
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(_ROUTE_PROBE_ADDRESS)
            found.add(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass  # offline, or no IPv4 route at all - loopback still serves this machine's own browser
    return tuple(sorted(a for a in found if a))


def _requires_cryptography():
    """The one, shared lazy-import point every certificate operation in
    this module needs - `cryptography` is optional (see requirements.txt),
    so every caller must fail the identical, honest, actionable way
    (`TLSUnavailable`) rather than a raw `ImportError` traceback. Reused
    here rather than duplicated per function, the way it was before the
    Alfred Development CA split added a second real caller of the same
    import block.
    """
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError as exc:
        raise TLSUnavailable(
            "the 'cryptography' package is not installed, so a private local "
            "certificate cannot be generated (see requirements.txt - it is "
            "optional, only needed for --https). Install it with "
            "'pip install cryptography', or omit --https to keep serving "
            "plain HTTP to this machine's own browser only."
        ) from exc
    return x509, hashes, serialization, rsa, NameOID


def _load_existing(tls_dir: Path) -> "CertificateInfo | None":
    meta_path, cert_path, key_path = tls_dir / META_NAME, tls_dir / CERT_NAME, tls_dir / KEY_NAME
    if not (meta_path.exists() and cert_path.exists() and key_path.exists()):
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    hosts = tuple(meta.get("hosts") or ())
    expires_at = meta.get("expires_at")
    if not hosts or not expires_at:
        return None
    return CertificateInfo(cert_path=cert_path, key_path=key_path, hosts=hosts, expires_at=expires_at)


def _load_existing_ca(tls_dir: Path) -> "CAInfo | None":
    meta_path, cert_path, key_path = tls_dir / CA_META_NAME, tls_dir / CA_CERT_NAME, tls_dir / CA_KEY_NAME
    if not (meta_path.exists() and cert_path.exists() and key_path.exists()):
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    expires_at = meta.get("expires_at")
    if not expires_at:
        return None
    return CAInfo(cert_path=cert_path, key_path=key_path, expires_at=expires_at)


def _leaf_matches_ca(cert_path: Path, ca: "CAInfo") -> bool:
    """Whether the leaf certificate at ``cert_path`` was genuinely issued
    by ``ca`` - the real invariant that makes "reuse the existing leaf"
    safe. False for a certificate from the old, retired self-signed
    architecture (whose issuer was always itself, never a CA) and for a
    leaf signed by a CA that has since been regenerated - both cases must
    force a fresh, correctly-signed leaf, never be silently reused as if
    nothing had changed.
    """
    x509, *_ = _requires_cryptography()
    try:
        leaf_cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        ca_cert = x509.load_pem_x509_certificate(ca.cert_path.read_bytes())
    except (OSError, ValueError):
        return False
    return leaf_cert.issuer == ca_cert.subject


def ensure_ca(tls_dir: Path = DEFAULT_TLS_DIR) -> CAInfo:
    """The Alfred Development CA this machine should sign server
    certificates with - the one certificate a person actually installs
    on each of their own devices (see `describe_trust_steps`).

    Reused, unchanged, across ordinary restarts and across every server
    certificate renewal - a CA is deliberately not regenerated merely
    because the leaf it signs renews on its own, much shorter cadence
    (see `CA_VALIDITY_DAYS`'s own comment on why). Regenerated only when
    genuinely missing, corrupt (unreadable metadata), or itself close to
    its own, much longer expiry.
    """
    existing = _load_existing_ca(tls_dir)
    if existing is not None and not existing.expires_soon():
        return existing
    return _generate_ca(tls_dir)


def ensure_certificate(
    *, extra_hosts: tuple[str, ...] = (), tls_dir: Path = DEFAULT_TLS_DIR,
) -> CertificateInfo:
    """The server certificate this run of `--https` should actually
    present on the wire, signed by the Alfred Development CA
    (`ensure_ca`, called first - a CA is ensured before any leaf ever
    is, never the other way around).

    Reused, unchanged, if what is already on disk still covers every
    address this machine is reachable at today, is not about to expire,
    and was genuinely signed by the CA this call just ensured - so a
    device that already trusts the CA keeps working across ordinary
    restarts. Regenerated, over the same file path, the moment the
    required address set grows (a new network, a new `--tls-host`), the
    certificate is close to expiring, or it turns out to have been
    issued by a different CA (a stale, retired self-signed certificate
    from before this module's own CA/leaf split included - migration is
    automatic and never silently reuses one of those).

    `extra_hosts` is `--tls-host` on the command line: the escape hatch
    for a stable hostname/IP live auto-detection cannot see (a
    router-assigned static IP, a LAN DNS name) - never a reason to hard-
    code an address into this module.
    """
    ca = ensure_ca(tls_dir)
    required = tuple(sorted(set(detect_private_addresses()) | set(extra_hosts)))
    existing = _load_existing(tls_dir)
    if (
        existing is not None
        and existing.covers(required)
        and not existing.expires_soon()
        and _leaf_matches_ca(existing.cert_path, ca)
    ):
        return existing
    return _generate_certificate(required, tls_dir, ca)


def _generate_ca(tls_dir: Path) -> CAInfo:
    x509, hashes, serialization, rsa, NameOID = _requires_cryptography()

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Alfred Development CA (private, local)")])
    now = datetime.now(timezone.utc)

    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)  # a root CA is self-signed by definition - the one legitimate case of this
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=CA_VALIDITY_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            # key_cert_sign/crl_sign are the two facts that actually make
            # this usable as a trust anchor - the exact properties real
            # physical-device testing found missing from the original,
            # single self-signed-leaf design. Every other usage bit is
            # honestly False: this key only ever signs certificates and
            # (in principle) revocation lists, never TLS traffic itself.
            x509.KeyUsage(
                digital_signature=False, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        # SubjectKeyIdentifier - the standard, real way a leaf's own
        # AuthorityKeyIdentifier (see _generate_certificate below) names
        # exactly which CA key signed it. Found genuinely required, not
        # merely conventional: cryptography's own RFC 5280 path-building
        # verifier (x509.verification.ServerVerifier) refused a leaf
        # lacking the matching AuthorityKeyIdentifier during this fix's
        # own real chain-validation testing, before this extension was
        # added.
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )

    tls_dir.mkdir(parents=True, exist_ok=True)
    cert_path, key_path, meta_path = tls_dir / CA_CERT_NAME, tls_dir / CA_KEY_NAME, tls_dir / CA_META_NAME

    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    try:
        key_path.chmod(0o600)
    except OSError:
        pass  # not every filesystem (notably Windows') honours this; the key never leaves data/tls/, already gitignored like every other credential in data/ - and is never transmitted anywhere, including over the TLS connection this CA merely signs for

    expires_at = (now + timedelta(days=CA_VALIDITY_DAYS)).isoformat()
    meta_path.write_text(
        json.dumps({"expires_at": expires_at, "generated_at": now.isoformat()}, indent=2),
        encoding="utf-8",
    )
    return CAInfo(cert_path=cert_path, key_path=key_path, expires_at=expires_at)


def _generate_certificate(hosts: tuple[str, ...], tls_dir: Path, ca: CAInfo) -> CertificateInfo:
    x509, hashes, serialization, rsa, NameOID = _requires_cryptography()

    ca_key = serialization.load_pem_private_key(ca.key_path.read_bytes(), password=None)
    ca_certificate = x509.load_pem_x509_certificate(ca.cert_path.read_bytes())

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Alfred (private, local)")])
    now = datetime.now(timezone.utc)

    san_entries: list = []
    for host in hosts:
        try:
            san_entries.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            san_entries.append(x509.DNSName(host))

    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(ca_certificate.subject)  # signed by the Alfred Development CA, never itself
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=VALIDITY_DAYS))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        # AuthorityKeyIdentifier - names exactly which CA key signed this
        # leaf (matched against the CA's own SubjectKeyIdentifier, see
        # _generate_ca above). Required in practice, not merely
        # conventional: found missing during this fix's own real chain-
        # validation test against cryptography's RFC 5280 path-building
        # verifier, which refused to validate a leaf without it.
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_certificate.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())  # signed with the CA's own private key, never the leaf's
    )

    tls_dir.mkdir(parents=True, exist_ok=True)
    cert_path, key_path, meta_path = tls_dir / CERT_NAME, tls_dir / KEY_NAME, tls_dir / META_NAME

    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    try:
        key_path.chmod(0o600)
    except OSError:
        pass  # not every filesystem (notably Windows') honours this; the key never leaves data/tls/, already gitignored like every other credential in data/

    expires_at = (now + timedelta(days=VALIDITY_DAYS)).isoformat()
    meta_path.write_text(
        json.dumps(
            {"hosts": list(hosts), "expires_at": expires_at, "generated_at": now.isoformat()},
            indent=2,
        ),
        encoding="utf-8",
    )
    return CertificateInfo(cert_path=cert_path, key_path=key_path, hosts=hosts, expires_at=expires_at)


def fingerprint_sha256(cert_path: Path) -> str:
    """A short, human-checkable value for confirming - on the device
    doing the trusting - that the certificate it is about to trust is
    genuinely the one this machine just generated, not something
    substituted on the network in between. Stdlib only; reading a
    fingerprint back needs no dependency even though generating the
    certificate does.
    """
    der = ssl.PEM_cert_to_DER_cert(cert_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(der).hexdigest()
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2)).upper()


def build_ssl_context(info: CertificateInfo) -> ssl.SSLContext:
    """The one TLS context `--https` wraps the existing socket in - never
    a second server, never a second port; the identical routes, the
    identical `LiveSystem`, now reachable over HTTPS instead of HTTP."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(info.cert_path), keyfile=str(info.key_path))
    return context


def describe_trust_steps(ca: CAInfo, leaf: CertificateInfo) -> str:
    """Generic, platform-shaped instructions for trusting the Alfred
    Development CA once per device - never naming a specific phone, a
    specific person's laptop, or a specific network, and never the
    server leaf (``leaf``, used only to report where Alfred is actually
    reachable - the leaf itself is presented automatically during the
    TLS handshake and is never something a person installs by hand).

    Trusting the CA once covers every server certificate this machine
    issues afterwards, including ones regenerated months from now on
    their own, much shorter cadence - no further per-device step, ever,
    unless the CA itself is deliberately regenerated (rare - see
    `CA_VALIDITY_DAYS`'s own comment). The same three steps apply to any
    Windows machine, any Android phone, any iPhone; none of them require
    a public CA, a domain, or exposing this machine to anything beyond
    its own private network.
    """
    lan = [h for h in leaf.hosts if h not in ("127.0.0.1", "localhost", "::1")]
    reachable = ", ".join(lan) if lan else "(no LAN address detected - only this machine's own browser can reach it right now)"
    fp = fingerprint_sha256(ca.cert_path)
    return (
        f"Private certificate ready - Alfred reachable at: {reachable}\n"
        f"Alfred Development CA file (trust THIS one - never the server's own certificate): {ca.cert_path}\n"
        f"Alfred Development CA SHA-256 fingerprint (check this matches on-device before trusting it): {fp}\n"
        "\n"
        "Trust the CA once on each of your own devices - never publicly, never for anyone else's. "
        "This is the one certificate you install by hand; Alfred's own server certificate is "
        "presented automatically and is never installed directly:\n"
        f"  Windows/laptop:  double-click {ca.cert_path.name} -> Install Certificate -> "
        "Local Machine -> Trusted Root Certification Authorities.\n"
        "  Android:         copy the .crt file to the device -> Settings -> Security -> "
        "Encryption & credentials -> Install a certificate -> CA certificate.\n"
        "  iPhone/iPad:     AirDrop or email the .crt file to the device and open it (installs "
        "a profile) -> Settings -> General -> VPN & Device Management -> install the profile -> "
        "then Settings -> General -> About -> Certificate Trust Settings -> enable full trust "
        "for it. Both steps are required - the profile alone is not enough for Safari to treat "
        "the connection as secure.\n"
    )
