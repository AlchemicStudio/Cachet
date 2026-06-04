"""Azure Key Vault signing for `--mode azure` (personal AES, not QES).

The signed-in user's *own* certificate + non-exportable key live in Azure
Key Vault. Authentication is an interactive Microsoft Entra ID login (one
per batch); the signed-in identity determines which key is used, so the
signature is uniquely linked to that person (sole control → an *advanced*
electronic signature). Only the document **digest** is ever sent to Azure
— the document itself never leaves the machine, and tokens/key material
are never logged.

Design decisions (recorded per the project spec):

- pyHanko integration follows the documented custom-`Signer` pattern
  (cf. the AWS KMS example in pyHanko's advanced guide): subclass
  ``signers.Signer``, set ``signature_mechanism``, implement
  ``async_sign_raw`` to hash locally and delegate the digest signature to
  ``CryptographyClient.sign``.
- **RSA keys sign with PKCS#1 v1.5 (RS256/384/512)**. Key Vault has no
  key-level "PSS policy" attribute to honour, and PKCS#1 v1.5 remains the
  CMS/PAdES interop default; PSS can be added later behind an explicit
  option if an organisation requires it.
- **ECDSA**: Key Vault returns the raw ``r||s`` (JWA) concatenation, but
  CMS requires the DER-encoded ``ECDSA-Sig-Value`` — converted here. The
  curve dictates the digest (ES256 = P-256 + SHA-256, …); a mismatched
  ``--digest`` fails with an actionable message instead of silently
  re-hashing.
- **Key Vault object names** only allow ``[0-9a-zA-Z-]``, so the
  ``--azure-key-name-template`` placeholders are sanitised: ``{upn}`` =
  full UPN with every other character mapped to ``-`` (e.g.
  ``jane.doe@example.org`` → ``jane-doe-example-org``), ``{upn_local}`` =
  the part before ``@``, ``{oid}`` = the Entra object id.
- The access token's claims are decoded locally **without signature
  verification**: we only read *our own* token to learn who signed in;
  authorization is enforced server-side by Key Vault.
- Azure SDK imports are lazy so this module stays importable (and the
  core stays testable) even when the SDK is absent; no tkinter anywhere.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import re
import sys

from asn1crypto import algos, x509
from pyhanko.sign import signers
from pyhanko.sign.general import get_pyca_cryptography_hash
from pyhanko_certvalidator.registry import SimpleCertificateStore

# Data-plane scopes (AAD v2 resource/.default form). The Key Vault SDK
# negotiates its own scope via auth challenges; we request the same one
# explicitly only to obtain the user's identity claims up front.
KEY_VAULT_SCOPE = "https://vault.azure.net/.default"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
GRAPH_ME_URL = "https://graph.microsoft.com/v1.0/me"
LOGIN_ENDPOINT = "login.microsoftonline.com"

AUTH_METHODS = ("interactive", "device-code", "default")
DEFAULT_KEY_TEMPLATE = "sig-{upn}"


class AzureSigningError(RuntimeError):
    """Azure-mode signing cannot proceed (auth, key resolution, Key Vault…)."""


# --------------------------------------------------------------------------
# Authentication & user resolution (R2)
# --------------------------------------------------------------------------


def build_credential(method: str):
    """Build the Entra ID credential for the chosen auth method.

    interactive  → system-browser login (GUI default);
    device-code  → code displayed on stderr, entered on another device
                   (headless CLI default);
    default      → DefaultAzureCredential — breaks the per-user model
                   (may pick a service principal / managed identity);
                   intended for testing/CI only.
    """
    if method not in AUTH_METHODS:
        raise AzureSigningError(
            f"Unknown --azure-auth method: {method!r} "
            f"(expected {'|'.join(AUTH_METHODS)})."
        )
    try:
        import azure.identity as _id
    except ImportError as exc:  # pragma: no cover - packaging guard
        raise AzureSigningError(
            "The Azure SDK is not installed: pip install azure-identity "
            "azure-keyvault-keys azure-keyvault-certificates"
        ) from exc
    if method == "interactive":
        return _id.InteractiveBrowserCredential()
    if method == "device-code":
        return _id.DeviceCodeCredential()
    return _id.DefaultAzureCredential()


_credential_cache: dict = {}


def get_cached_credential(method: str, *, fresh: bool = False):
    """Process-wide credential cache (R6/R10): the GUI sign-in action and
    every subsequent batch share ONE credential object, whose token cache
    is managed in-memory by the Azure SDK (silent refresh on expiry) — so
    a user logs in once, not once per batch or per document."""
    if fresh or method not in _credential_cache:
        _credential_cache[method] = build_credential(method)
    return _credential_cache[method]


def _jwt_claims(token: str) -> dict:
    """Decode the payload of a JWT locally (NO signature verification —
    we only read our own token to learn who we are; Key Vault enforces
    authorization server-side). Never log the token itself."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)  # restore base64url padding
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError) as exc:
        raise AzureSigningError(
            "Could not parse the access token returned by Entra ID."
        ) from exc


@dataclasses.dataclass
class AzureUser:
    """Identity of the signed-in user, read from the token claims."""

    upn: str
    oid: str | None = None
    name: str | None = None


def acquire_user(credential) -> AzureUser:
    """Sign in (once per batch) and resolve who the user is.

    Raises an actionable AzureSigningError naming the endpoint when the
    login cannot proceed (offline, cancelled, unconsented app, …).
    """
    try:
        from azure.core.exceptions import ClientAuthenticationError
    except ImportError:  # pragma: no cover - packaging guard
        ClientAuthenticationError = Exception  # noqa: N806
    try:
        token = credential.get_token(KEY_VAULT_SCOPE)
    except ClientAuthenticationError as exc:
        raise AzureSigningError(
            f"Microsoft Entra ID sign-in failed ({LOGIN_ENDPOINT}): {exc} "
            "— check network access and that your account is allowed to "
            "use Azure Key Vault."
        ) from exc
    except Exception as exc:  # CredentialUnavailableError et al.
        raise AzureSigningError(
            f"No usable Entra ID credential ({LOGIN_ENDPOINT}): {exc}"
        ) from exc
    claims = _jwt_claims(token.token)
    upn = claims.get("upn") or claims.get("preferred_username")
    if not upn:
        raise AzureSigningError(
            "The signed-in identity has no UPN claim (service principal or "
            "managed identity?). azure mode signs in the USER's name: use "
            "--azure-auth interactive|device-code with a user account, or "
            "pass an explicit --azure-key-name."
        )
    return AzureUser(upn=upn, oid=claims.get("oid"), name=claims.get("name"))


# --------------------------------------------------------------------------
# Per-user key/cert name resolution (R4)
# --------------------------------------------------------------------------


def _sanitize(label: str) -> str:
    """Map an arbitrary label onto the Key Vault name charset [0-9a-zA-Z-]."""
    return re.sub(r"-{2,}", "-", re.sub(r"[^0-9a-zA-Z-]", "-", label)).strip("-")


def resolve_key_names(
    user: AzureUser,
    *,
    key_name: str | None = None,
    key_name_template: str | None = None,
    cert_name: str | None = None,
) -> tuple[str, str, bool]:
    """Resolve (key_name, cert_name, overridden) for the signed-in user.

    Security rule: by default the key name is DERIVED from the user's own
    token claims via the template, so a user can only reach their own key.
    An explicit ``key_name`` bypasses that derivation; it is allowed (an
    admin may legitimately rename things) but flagged to the caller, which
    must surface it (banner + per-document detail) so signing as someone
    else is visible, and Key Vault access policies remain the hard gate.
    """
    if key_name:
        resolved, overridden = key_name, True
    else:
        template = key_name_template or DEFAULT_KEY_TEMPLATE
        subs = {
            "upn": _sanitize(user.upn.lower()),
            "upn_local": _sanitize(user.upn.split("@", 1)[0].lower()),
            "oid": _sanitize(user.oid or ""),
        }
        if not subs["upn"]:
            raise AzureSigningError(
                f"Cannot derive a Key Vault name from UPN {user.upn!r}."
            )
        try:
            resolved = template.format(**subs)
        except (KeyError, IndexError) as exc:
            raise AzureSigningError(
                f"Bad --azure-key-name-template {template!r}: unknown "
                f"placeholder {exc}. Available: {{upn}}, {{upn_local}}, {{oid}}."
            ) from exc
        overridden = False
    if not re.fullmatch(r"[0-9a-zA-Z-]{1,127}", resolved):
        raise AzureSigningError(
            f"Resolved Key Vault key name {resolved!r} is not a valid Key "
            "Vault object name ([0-9a-zA-Z-], 1-127 chars)."
        )
    return resolved, (cert_name or resolved), overridden


def fetch_graph_display_name(credential) -> str | None:
    """Optional nicety (--azure-graph): displayName from Microsoft Graph /me.

    Soft-fails to None — the vignette then falls back to the certificate
    subject. Never raises for cosmetic data.
    """
    try:
        import requests

        token = credential.get_token(GRAPH_SCOPE)
        resp = requests.get(
            GRAPH_ME_URL,
            headers={"Authorization": f"Bearer {token.token}"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("displayName") or None
    except Exception as exc:  # noqa: BLE001 - cosmetic lookup only
        print(f"warning: Microsoft Graph displayName lookup failed: {exc}",
              file=sys.stderr)
        return None


# --------------------------------------------------------------------------
# The Key Vault signer (R3)
# --------------------------------------------------------------------------

# (key type, digest) -> Key Vault SignatureAlgorithm name. ECDSA pairs the
# curve with its mandatory digest; requesting another digest is an error.
_RSA_ALGS = {"sha256": "RS256", "sha384": "RS384", "sha512": "RS512"}
_EC_ALGS = {
    ("P-256", "sha256"): "ES256",
    ("P-384", "sha384"): "ES384",
    ("P-521", "sha512"): "ES512",
}
_EC_COORD_BYTES = {"P-256": 32, "P-384": 48, "P-521": 66}


def algorithm_for_key(key_type: str, curve: str | None, digest: str):
    """Map (Key Vault key type, curve, --digest) to the signing parameters.

    Returns (kv_algorithm_name, signature_mechanism, estimate_size, ec_coords)
    where ec_coords is None for RSA and the per-coordinate byte length for EC.
    Pure; raises AzureSigningError with an actionable message on mismatch.
    """
    kty = (key_type or "").upper().replace("-HSM", "")
    if kty == "RSA":
        alg = _RSA_ALGS.get(digest)
        if not alg:
            raise AzureSigningError(f"Unsupported digest for RSA: {digest!r}.")
        mech = algos.SignedDigestAlgorithm({"algorithm": f"{digest}_rsa"})
        # Placeholder size: a 4096-bit modulus covers the common cases for
        # dry runs; the actual signature replaces it byte-exactly.
        return alg, mech, 512, None
    if kty == "EC":
        alg = _EC_ALGS.get((curve or "", digest))
        if not alg:
            wanted = {c: d for (c, d) in _EC_ALGS if c == (curve or "")}
            hint = (
                f"curve {curve} requires --digest "
                f"{next(iter(d for (c, d) in _EC_ALGS if c == curve), '?')}"
                if any(c == curve for (c, d) in _EC_ALGS)
                else f"unsupported curve {curve!r}"
            )
            raise AzureSigningError(
                f"EC key/digest mismatch: {hint} (got --digest {digest})."
            )
        coord = _EC_COORD_BYTES[curve]
        mech = algos.SignedDigestAlgorithm({"algorithm": f"{digest}_ecdsa"})
        # DER ECDSA-Sig-Value worst case: SEQUENCE + 2 INTEGERs w/ padding.
        return alg, mech, 2 * coord + 9, coord
    raise AzureSigningError(
        f"Unsupported Key Vault key type for signing: {key_type!r} "
        "(expected RSA / RSA-HSM / EC / EC-HSM)."
    )


class AzureKeyVaultSigner(signers.Signer):
    """pyHanko Signer delegating the digest signature to Azure Key Vault.

    The CMS is assembled locally from the user's certificate (+ optional
    chain); ``async_sign_raw`` hashes the signed attributes locally and
    sends ONLY that digest to ``CryptographyClient.sign``.
    """

    def __init__(
        self,
        signing_cert: x509.Certificate,
        crypto_client,
        kv_algorithm,
        signature_mechanism: algos.SignedDigestAlgorithm,
        *,
        other_certs: tuple = (),
        estimate_size: int = 512,
        ec_coord_bytes: int | None = None,
    ):
        registry = SimpleCertificateStore()
        registry.register_multiple(other_certs)
        self._crypto_client = crypto_client
        self._kv_algorithm = kv_algorithm
        self._estimate_size = estimate_size
        self._ec_coord_bytes = ec_coord_bytes
        super().__init__(
            signing_cert=signing_cert,
            cert_registry=registry,
            signature_mechanism=signature_mechanism,
        )

    async def async_sign_raw(
        self, data: bytes, digest_algorithm: str, dry_run=False
    ) -> bytes:
        if dry_run:
            return bytes(self._estimate_size)
        # Hash locally — only the digest leaves the machine.
        from cryptography.hazmat.primitives import hashes

        md = hashes.Hash(get_pyca_cryptography_hash(digest_algorithm))
        md.update(data)
        digest = md.finalize()
        try:
            result = self._crypto_client.sign(self._kv_algorithm, digest)
        except Exception as exc:
            raise AzureSigningError(
                f"Azure Key Vault sign operation failed: {exc} — check that "
                "the vault is reachable and that your account holds the "
                "'sign' permission on the key."
            ) from exc
        signature = result.signature
        if self._ec_coord_bytes is not None:
            # Key Vault returns ECDSA as raw r||s (JWA); CMS needs DER.
            from cryptography.hazmat.primitives.asymmetric.utils import (
                encode_dss_signature,
            )

            half = len(signature) // 2
            signature = encode_dss_signature(
                int.from_bytes(signature[:half], "big"),
                int.from_bytes(signature[half:], "big"),
            )
        return signature


def build_azure_signer(
    vault_url: str,
    credential,
    key_name: str,
    cert_name: str,
    digest: str,
    *,
    other_certs: tuple = (),
    key_client=None,
    cert_client=None,
    crypto_client_factory=None,
) -> AzureKeyVaultSigner:
    """Resolve the Key Vault key + certificate and build the signer (R3/R6).

    Called once per batch. The injectable ``*_client`` parameters exist for
    unit tests (Azure SDK fully mocked); production builds them from the
    vault URL + credential.
    """
    if key_client is None or cert_client is None or crypto_client_factory is None:
        try:
            from azure.keyvault.certificates import CertificateClient
            from azure.keyvault.keys import KeyClient
            from azure.keyvault.keys.crypto import CryptographyClient
        except ImportError as exc:  # pragma: no cover - packaging guard
            raise AzureSigningError(
                "The Azure SDK is not installed: pip install azure-identity "
                "azure-keyvault-keys azure-keyvault-certificates"
            ) from exc
        key_client = key_client or KeyClient(vault_url, credential)
        cert_client = cert_client or CertificateClient(vault_url, credential)
        crypto_client_factory = crypto_client_factory or (
            lambda key: CryptographyClient(key, credential)
        )
    try:
        key = key_client.get_key(key_name)
    except AzureSigningError:
        raise
    except Exception as exc:
        raise AzureSigningError(
            f"Cannot fetch Key Vault key {key_name!r} from {vault_url}: {exc} "
            "— check the vault URL, your access policy, and that your "
            "personal signing key was provisioned (see README)."
        ) from exc
    try:
        cert_bundle = cert_client.get_certificate(cert_name)
        signing_cert = x509.Certificate.load(bytes(cert_bundle.cer))
    except AzureSigningError:
        raise
    except Exception as exc:
        raise AzureSigningError(
            f"Cannot fetch Key Vault certificate {cert_name!r} from "
            f"{vault_url}: {exc}"
        ) from exc
    curve = getattr(key.key, "crv", None)
    kv_alg, mechanism, estimate, ec_coord = algorithm_for_key(
        str(key.key_type), str(curve) if curve else None, digest
    )
    return AzureKeyVaultSigner(
        signing_cert=signing_cert,
        crypto_client=crypto_client_factory(key),
        kv_algorithm=kv_alg,
        signature_mechanism=mechanism,
        other_certs=tuple(other_certs),
        estimate_size=estimate,
        ec_coord_bytes=ec_coord,
    )
