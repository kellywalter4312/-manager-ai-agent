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

This module closes that gap the smallest way that stays private: a
self-signed certificate, generated locally, covering whichever addresses
this machine is actually reachable at *right now* - detected live, never a
fixed IP baked into source, never a public domain, never a third-party
account or hosted service. A person trusts the certificate once on each of
their own devices - the exact, standard way any internal-only HTTPS site is
trusted on a phone that has never heard of a public CA, the same mechanism
`mkcert` and every enterprise-internal HTTPS deployment already rely on.
After that, `--https` makes Alfred reachable from a laptop, an Android
phone or an iPhone on the same private network exactly as it already is
from `localhost` today - same server, same routes, same one `LiveSystem`,
nothing duplicated, nothing exposed to the public internet.

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
CERT_NAME = "alfred-local.crt"
KEY_NAME = "alfred-local.key"
META_NAME = "alfred-local.json"
# The longest leaf-certificate lifetime every major browser still honours
# without complaint (~825 days, the 2018 CA/Browser Forum ceiling many
# still enforce even for a locally-trusted cert nobody is racing to
# revoke). Renewed automatically anyway the moment the detected address
# set changes, so a longer fixed lifetime buys no real safety and only
# means fewer "re-trust this on your phone" moments.
VALIDITY_DAYS = 825
RENEW_WITHIN_DAYS = 30
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


def ensure_certificate(
    *, extra_hosts: tuple[str, ...] = (), tls_dir: Path = DEFAULT_TLS_DIR,
) -> CertificateInfo:
    """The certificate this run of `--https` should actually serve.

    Reused, unchanged, if what is already on disk still covers every
    address this machine is reachable at today and is not about to
    expire - so a device that already trusted it once keeps working
    across ordinary restarts. Regenerated, over the same file path, the
    moment the required address set grows (a new network, a new
    `--tls-host`) or the certificate is close to expiring - never
    silently reused past either of those.

    `extra_hosts` is `--tls-host` on the command line: the escape hatch
    for a stable hostname/IP live auto-detection cannot see (a
    router-assigned static IP, a LAN DNS name) - never a reason to hard-
    code an address into this module.
    """
    required = tuple(sorted(set(detect_private_addresses()) | set(extra_hosts)))
    existing = _load_existing(tls_dir)
    if existing is not None and existing.covers(required) and not existing.expires_soon():
        return existing
    return _generate_certificate(required, tls_dir)


def _generate_certificate(hosts: tuple[str, ...], tls_dir: Path) -> CertificateInfo:
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
        .issuer_name(name)  # self-signed: issuer is the subject, by definition
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=VALIDITY_DAYS))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
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


def describe_trust_steps(info: CertificateInfo) -> str:
    """Generic, platform-shaped instructions for trusting this one
    certificate once per device - never naming a specific phone, a
    specific person's laptop, or a specific network. The same three
    steps apply to any Windows machine, any Android phone, any iPhone;
    none of them require a public CA, a domain, or exposing this machine
    to anything beyond its own private network.
    """
    lan = [h for h in info.hosts if h not in ("127.0.0.1", "localhost", "::1")]
    reachable = ", ".join(lan) if lan else "(no LAN address detected - only this machine's own browser can reach it right now)"
    fp = fingerprint_sha256(info.cert_path)
    return (
        f"Private certificate ready - reachable at: {reachable}\n"
        f"File: {info.cert_path}\n"
        f"SHA-256 fingerprint (check this matches on-device before trusting it): {fp}\n"
        "\n"
        "Trust it once on each of your own devices - never publicly, never for anyone else's:\n"
        f"  Windows/laptop:  double-click {info.cert_path.name} -> Install Certificate -> "
        "Local Machine -> Trusted Root Certification Authorities.\n"
        "  Android:         copy the .crt file to the device -> Settings -> Security -> "
        "Encryption & credentials -> Install a certificate -> CA certificate.\n"
        "  iPhone/iPad:     AirDrop or email the .crt file to the device and open it (installs "
        "a profile) -> Settings -> General -> VPN & Device Management -> install the profile -> "
        "then Settings -> General -> About -> Certificate Trust Settings -> enable full trust "
        "for it. Both steps are required - the profile alone is not enough for Safari to treat "
        "the connection as secure.\n"
    )
