"""Tests for azure mode (Entra ID + Key Vault) — Azure SDK fully mocked.

The fake CryptographyClient REALLY signs the received digest with a local
test key (prehashed), so the produced CMS is genuine and validates through
pyHanko end-to-end; what is mocked is only the Azure transport. The real
Entra login + Key Vault path stays a manual acceptance test (BUILD.md).
"""

from __future__ import annotations

import base64
import datetime
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import azure_signer
import sign_pdfs_beid as core
from test_sign_pdfs_beid import make_pdf

# ---------------------------------------------------------------------------
# Local key/cert helpers (cryptography) — no Azure, no network
# ---------------------------------------------------------------------------


def _name(cn, given=None, surname=None):
    from cryptography import x509 as cx509
    from cryptography.x509.oid import NameOID

    attrs = [cx509.NameAttribute(NameOID.COMMON_NAME, cn)]
    if given:
        attrs.append(cx509.NameAttribute(NameOID.GIVEN_NAME, given))
    if surname:
        attrs.append(cx509.NameAttribute(NameOID.SURNAME, surname))
    return cx509.Name(attrs)


def make_self_signed(key, cn, given=None, surname=None):
    """Self-signed cert (DER) for `key`, with digital-signature key usage."""
    from cryptography import x509 as cx509
    from cryptography.hazmat.primitives import hashes, serialization

    start = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
    name = _name(cn, given, surname)
    cert = (
        cx509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(cx509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(start + datetime.timedelta(days=3650))
        .add_extension(
            cx509.BasicConstraints(ca=True, path_length=None), critical=True
        )
        .add_extension(
            cx509.KeyUsage(
                digital_signature=True, content_commitment=True,
                key_encipherment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=True, crl_sign=True,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.DER)


def _fake_jwt(**claims) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=")
    return "eyJhbGciOiJub25lIn0." + body.decode() + ".sig"


class _StubCredential:
    """get_token returns a canned JWT; never touches the network."""

    def __init__(self, claims=None, raise_exc=None):
        self.claims = claims or {}
        self.raise_exc = raise_exc
        self.scopes: list[str] = []

    def get_token(self, *scopes, **kw):
        self.scopes.extend(scopes)
        if self.raise_exc:
            raise self.raise_exc
        return SimpleNamespace(token=_fake_jwt(**self.claims), expires_on=0)


class _FakeCryptoClient:
    """Mimics CryptographyClient.sign — REALLY signs the digest locally.

    For EC, the DER signature is converted to raw r||s, exactly like Azure
    Key Vault returns it (JWA), so the production r||s -> DER conversion in
    AzureKeyVaultSigner is exercised for real.
    """

    def __init__(self, key, *, ec_coord_bytes=None):
        self._key = key
        self._ec = ec_coord_bytes
        self.calls: list[tuple[str, bytes]] = []

    def sign(self, algorithm, digest):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec, padding
        from cryptography.hazmat.primitives.asymmetric.utils import (
            Prehashed,
            decode_dss_signature,
        )

        self.calls.append((str(algorithm), bytes(digest)))
        prehashed = Prehashed(hashes.SHA256())
        if self._ec is None:
            sig = self._key.sign(digest, padding.PKCS1v15(), prehashed)
        else:
            der = self._key.sign(digest, ec.ECDSA(prehashed))
            r, s = decode_dss_signature(der)
            sig = r.to_bytes(self._ec, "big") + s.to_bytes(self._ec, "big")
        return SimpleNamespace(signature=sig, algorithm=algorithm)


class TmpCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def p(self, name):
        return self.tmp / name


# ---------------------------------------------------------------------------
# Auth & user resolution
# ---------------------------------------------------------------------------


class TokenClaims(unittest.TestCase):
    def test_upn_claim_preferred(self):
        user = azure_signer.acquire_user(_StubCredential({"upn": "a@b.eu", "oid": "123"}))
        self.assertEqual((user.upn, user.oid), ("a@b.eu", "123"))

    def test_preferred_username_fallback(self):
        user = azure_signer.acquire_user(
            _StubCredential({"preferred_username": "c@d.eu"}))
        self.assertEqual(user.upn, "c@d.eu")

    def test_requests_key_vault_scope(self):
        cred = _StubCredential({"upn": "a@b.eu"})
        azure_signer.acquire_user(cred)
        self.assertEqual(cred.scopes, [azure_signer.KEY_VAULT_SCOPE])

    def test_missing_upn_is_actionable(self):
        with self.assertRaises(azure_signer.AzureSigningError) as ctx:
            azure_signer.acquire_user(_StubCredential({"appid": "sp"}))
        self.assertIn("UPN", str(ctx.exception))

    def test_credential_failure_names_login_endpoint(self):
        from azure.identity import CredentialUnavailableError

        cred = _StubCredential(raise_exc=CredentialUnavailableError("no broker"))
        with self.assertRaises(azure_signer.AzureSigningError) as ctx:
            azure_signer.acquire_user(cred)
        self.assertIn("login.microsoftonline.com", str(ctx.exception))


class KeyNameResolution(unittest.TestCase):
    def _user(self, upn="Jane.Doe@Example.org", oid="ab-12"):
        return azure_signer.AzureUser(upn=upn, oid=oid)

    def test_default_template_sanitises_upn(self):
        key, cert, overridden = azure_signer.resolve_key_names(self._user())
        self.assertEqual(key, "sig-jane-doe-example-org")
        self.assertEqual(cert, key)
        self.assertFalse(overridden)

    def test_custom_template_local_part(self):
        key, _, _ = azure_signer.resolve_key_names(
            self._user(), key_name_template="esig-{upn_local}")
        self.assertEqual(key, "esig-jane-doe")

    def test_explicit_override_is_flagged(self):
        key, cert, overridden = azure_signer.resolve_key_names(
            self._user(), key_name="shared-key", cert_name="shared-cert")
        self.assertEqual((key, cert), ("shared-key", "shared-cert"))
        self.assertTrue(overridden)  # caller must surface this (R4 rule)

    def test_unknown_placeholder_rejected(self):
        with self.assertRaises(azure_signer.AzureSigningError):
            azure_signer.resolve_key_names(
                self._user(), key_name_template="sig-{email}")

    def test_invalid_resolved_name_rejected(self):
        with self.assertRaises(azure_signer.AzureSigningError):
            azure_signer.resolve_key_names(self._user(upn="@@@", oid=None))


# ---------------------------------------------------------------------------
# Algorithm mapping (pure)
# ---------------------------------------------------------------------------


class AlgorithmMapping(unittest.TestCase):
    def test_rsa_digest_mapping(self):
        for digest, alg in (("sha256", "RS256"), ("sha384", "RS384"),
                            ("sha512", "RS512")):
            kv_alg, mech, _, ec = azure_signer.algorithm_for_key("RSA", None, digest)
            self.assertEqual((kv_alg, ec), (alg, None))
            self.assertEqual(mech["algorithm"].native, f"{digest}_rsa")

    def test_rsa_hsm_treated_as_rsa(self):
        kv_alg, *_ = azure_signer.algorithm_for_key("RSA-HSM", None, "sha256")
        self.assertEqual(kv_alg, "RS256")

    def test_ec_p256(self):
        kv_alg, mech, size, coord = azure_signer.algorithm_for_key(
            "EC", "P-256", "sha256")
        self.assertEqual((kv_alg, coord), ("ES256", 32))
        self.assertEqual(mech["algorithm"].native, "sha256_ecdsa")
        self.assertGreaterEqual(size, 2 * 32 + 8)

    def test_ec_digest_mismatch_actionable(self):
        with self.assertRaises(azure_signer.AzureSigningError) as ctx:
            azure_signer.algorithm_for_key("EC", "P-256", "sha384")
        self.assertIn("sha256", str(ctx.exception))

    def test_unsupported_type_rejected(self):
        with self.assertRaises(azure_signer.AzureSigningError):
            azure_signer.algorithm_for_key("oct", None, "sha256")


# ---------------------------------------------------------------------------
# Identity from certificate (R7)
# ---------------------------------------------------------------------------


class CertIdentity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from asn1crypto import x509 as ax509
        from cryptography.hazmat.primitives.asymmetric import ec

        key = ec.generate_private_key(ec.SECP256R1())
        cls.cn_cert = ax509.Certificate.load(make_self_signed(key, "Jane Doe"))
        cls.gnsn_cert = ax509.Certificate.load(
            make_self_signed(key, "DOE Jane (Signature)", given="Jane",
                             surname="Doe"))

    def test_given_name_surname_preferred(self):
        ident = core.read_cert_identity(self.gnsn_cert)
        self.assertEqual(ident.name, "Jane Doe")
        self.assertIsNone(ident.photo)

    def test_common_name_fallback(self):
        self.assertEqual(core.read_cert_identity(self.cn_cert).name, "Jane Doe")

    def test_display_name_wins(self):
        ident = core.read_cert_identity(self.cn_cert, display_name="Jane D.")
        self.assertEqual(ident.name, "Jane D.")


class AnchorLoading(TmpCase):
    @classmethod
    def setUpClass(cls):
        from cryptography.hazmat.primitives.asymmetric import ec

        cls.der1 = make_self_signed(ec.generate_private_key(ec.SECP256R1()), "CA 1")
        cls.der2 = make_self_signed(ec.generate_private_key(ec.SECP256R1()), "CA 2")

    @staticmethod
    def _pem(der):
        body = base64.encodebytes(der).decode()
        return f"-----BEGIN CERTIFICATE-----\n{body}-----END CERTIFICATE-----\n"

    def test_multi_cert_pem_file(self):
        f = self.p("chain.pem")
        f.write_text(self._pem(self.der1) + self._pem(self.der2))
        certs = core.load_trust_anchor_certs(f)
        self.assertEqual(len(certs), 2)

    def test_directory_of_certs(self):
        (self.tmp / "a.pem").write_text(self._pem(self.der1))
        (self.tmp / "b.der").write_bytes(self.der2)
        certs = core.load_trust_anchor_certs(self.tmp)
        self.assertEqual(len(certs), 2)

    def test_empty_rejected(self):
        f = self.p("empty.pem")
        f.write_text("")
        with self.assertRaises(ValueError):
            core.load_trust_anchor_certs(f)


# ---------------------------------------------------------------------------
# The signer, end-to-end through pyHanko (offline; transport mocked)
# ---------------------------------------------------------------------------


class AzureSignerEndToEnd(TmpCase):
    def _build(self, *, ec_key=False):
        from cryptography.hazmat.primitives.asymmetric import ec, rsa

        if ec_key:
            key = ec.generate_private_key(ec.SECP256R1())
            kv_key = SimpleNamespace(key_type="EC",
                                     key=SimpleNamespace(crv="P-256"))
            fake_crypto = _FakeCryptoClient(key, ec_coord_bytes=32)
        else:
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            kv_key = SimpleNamespace(key_type="RSA", key=SimpleNamespace())
            fake_crypto = _FakeCryptoClient(key)
        der = make_self_signed(key, "Jane Doe", given="Jane", surname="Doe")
        key_client = SimpleNamespace(get_key=lambda name: kv_key)
        cert_client = SimpleNamespace(
            get_certificate=lambda name: SimpleNamespace(cer=der))
        signer = azure_signer.build_azure_signer(
            "https://vault.example", credential=None,
            key_name="sig-jane", cert_name="sig-jane", digest="sha256",
            key_client=key_client, cert_client=cert_client,
            crypto_client_factory=lambda key: fake_crypto,
        )
        return signer, fake_crypto

    def _sign_pdf(self, signer) -> Path:
        from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
        from pyhanko.sign import signers as psigners

        src = make_pdf(self.p("doc.pdf"), [(595, 842)])
        dst = self.p("signed.pdf")
        meta = core.PdfSignatureMetadata(
            field_name="Sig1", **core.signature_meta_kwargs("b-b", "sha256"))
        with src.open("rb") as inf:
            w = IncrementalPdfFileWriter(inf, strict=False)
            with dst.open("wb") as outf:
                psigners.sign_pdf(w, meta, signer=signer, output=outf)
        return dst

    def test_rsa_signs_digest_only_and_validates(self):
        signer, fake = self._build()
        dst = self._sign_pdf(signer)
        # exactly one Key Vault sign call: RS256 over a 32-byte digest —
        # proof that only the digest (not the document) goes to Azure.
        (alg, digest), = fake.calls
        self.assertEqual(alg, "RS256")
        self.assertEqual(len(digest), 32)
        self.assertLess(len(digest), dst.stat().st_size)
        # the produced CMS is genuine: pyHanko validates it
        detail = core.verify_signed_pdf(dst, "b-b",
                                        trust_anchors=[signer.signing_cert])
        self.assertEqual(detail, "PAdES-B-B")

    def test_ec_raw_rs_converted_to_der_and_validates(self):
        signer, fake = self._build(ec_key=True)
        dst = self._sign_pdf(signer)
        (alg, digest), = fake.calls
        self.assertEqual(alg, "ES256")
        self.assertEqual(len(digest), 32)
        detail = core.verify_signed_pdf(dst, "b-b",
                                        trust_anchors=[signer.signing_cert])
        self.assertEqual(detail, "PAdES-B-B")

    def test_vault_error_is_actionable(self):
        key_client = SimpleNamespace(
            get_key=mock.Mock(side_effect=OSError("connection refused")))
        with self.assertRaises(azure_signer.AzureSigningError) as ctx:
            azure_signer.build_azure_signer(
                "https://vault.example", credential=None,
                key_name="sig-x", cert_name="sig-x", digest="sha256",
                key_client=key_client,
                cert_client=SimpleNamespace(get_certificate=lambda n: None),
                crypto_client_factory=lambda key: None,
            )
        msg = str(ctx.exception)
        self.assertIn("https://vault.example", msg)
        self.assertIn("sig-x", msg)


# ---------------------------------------------------------------------------
# CLI parsing / validation / batch wiring
# ---------------------------------------------------------------------------


class AzureFlagParsing(TmpCase):
    def setUp(self):
        super().setUp()
        self.a = make_pdf(self.p("a.pdf"), [(595, 842)])
        self.anchors = self.p("ca.pem")
        from cryptography.hazmat.primitives.asymmetric import ec

        self.anchors.write_text(AnchorLoading._pem(
            make_self_signed(ec.generate_private_key(ec.SECP256R1()), "Root")))

    def parse(self, *extra):
        argv = ["--input", str(self.a), "--output", str(self.tmp),
                "--mode", "azure", *extra]
        return core.resolve_config(core.build_arg_parser().parse_args(argv))

    def test_minimal_azure_config(self):
        cfg = self.parse("--azure-vault-url", "https://v.example",
                         "--azure-trust-anchors", str(self.anchors))
        self.assertEqual(cfg.mode, "azure")
        self.assertEqual(cfg.azure_vault_url, "https://v.example")
        self.assertEqual(cfg.azure_auth, "device-code")  # CLI default
        self.assertEqual(cfg.pades_level, "b-lta")       # shared default
        self.assertFalse(cfg.azure_use_graph)

    def test_vault_url_required(self):
        with self.assertRaises(ValueError) as ctx:
            self.parse()
        self.assertIn("--azure-vault-url", str(ctx.exception))

    def test_anchors_required_for_lta(self):
        with self.assertRaises(ValueError) as ctx:
            self.parse("--azure-vault-url", "https://v.example")
        self.assertIn("--azure-trust-anchors", str(ctx.exception))

    def test_b_b_needs_no_anchors(self):
        cfg = self.parse("--azure-vault-url", "https://v.example",
                         "--pades-level", "b-b")
        self.assertIsNone(cfg.azure_trust_anchors)

    def test_b_t_no_verify_needs_no_anchors(self):
        cfg = self.parse("--azure-vault-url", "https://v.example",
                         "--pades-level", "b-t", "--no-verify")
        self.assertEqual(cfg.pades_level, "b-t")

    def test_legacy_cms_rejected_in_azure(self):
        import warnings as _w
        with self.assertRaises(ValueError), _w.catch_warnings():
            _w.simplefilter("ignore")
            self.parse("--azure-vault-url", "https://v.example",
                       "--legacy-cms")

    def test_bad_template_rejected_early(self):
        with self.assertRaises(ValueError):
            self.parse("--azure-vault-url", "https://v.example",
                       "--azure-trust-anchors", str(self.anchors),
                       "--azure-key-name-template", "sig-{nope}")

    def test_env_precedence(self):
        env = {
            core.ENV_AZURE_VAULT_URL: "https://env.example",
            core.ENV_AZURE_AUTH: "interactive",
            core.ENV_AZURE_KEY_NAME: "env-key",
        }
        with mock.patch.dict("os.environ", env):
            cfg = self.parse("--azure-trust-anchors", str(self.anchors))
            self.assertEqual(cfg.azure_vault_url, "https://env.example")
            self.assertEqual(cfg.azure_auth, "interactive")
            self.assertEqual(cfg.azure_key_name, "env-key")
            # flag still wins over env
            cfg = self.parse("--azure-vault-url", "https://flag.example",
                             "--azure-trust-anchors", str(self.anchors))
            self.assertEqual(cfg.azure_vault_url, "https://flag.example")

    def test_image_and_beid_modes_unaffected(self):
        argv = ["--input", str(self.a), "--output", str(self.tmp)]
        cfg = core.resolve_config(core.build_arg_parser().parse_args(argv))
        self.assertEqual(cfg.mode, "beid")
        self.assertIsNone(cfg.azure_vault_url)


class BatchAzureWiring(TmpCase):
    """process_batch azure path: setup ONCE per batch, Azure SDK stubbed."""

    def _run(self, cfg, claims=None):
        from asn1crypto import x509 as ax509
        from cryptography.hazmat.primitives.asymmetric import ec

        cert = ax509.Certificate.load(make_self_signed(
            ec.generate_private_key(ec.SECP256R1()), "Jane Doe"))
        calls = {"sign": [], "acquire": 0, "build_signer": 0}

        def fake_acquire(credential):
            calls["acquire"] += 1
            return azure_signer.AzureUser(upn="jane@example.org", oid="1")

        def fake_build_signer(*a, **k):
            calls["build_signer"] += 1
            return SimpleNamespace(signing_cert=cert)

        saved = (core.sign_one, core.build_signing_material, core.verify_signed_pdf)
        core.sign_one = lambda *a, **k: calls["sign"].append((a, k))
        core.build_signing_material = lambda cfg: core.SigningMaterial()
        core.verify_signed_pdf = lambda *a, **k: "PAdES-B-LTA, LTV ok"
        try:
            with mock.patch.object(azure_signer, "get_cached_credential",
                                   return_value=_StubCredential(claims or {})), \
                 mock.patch.object(azure_signer, "acquire_user", fake_acquire), \
                 mock.patch.object(azure_signer, "build_azure_signer",
                                   fake_build_signer):
                results = core.process_batch(cfg)
        finally:
            (core.sign_one, core.build_signing_material,
             core.verify_signed_pdf) = saved
        return calls, results

    def test_one_login_one_signer_for_whole_batch(self):
        srcs = [make_pdf(self.p(f"d{i}.pdf"), [(595, 842)]) for i in range(3)]
        cfg = core.RunConfig(inputs=srcs, output=self.p("out"), mode="azure",
                             azure_vault_url="https://v.example")
        calls, results = self._run(cfg)
        self.assertEqual(calls["acquire"], 1)        # one login per batch
        self.assertEqual(calls["build_signer"], 1)   # one signer per batch
        self.assertEqual(len(calls["sign"]), 3)
        self.assertTrue(all(r.ok for r in results))
        self.assertIn("Azure, jane@example.org", results[0].detail)
        self.assertIn("PAdES-B-LTA, LTV ok", results[0].detail)

    def test_default_vignette_and_placement_pass_through(self):
        src = make_pdf(self.p("d.pdf"), [(595, 842), (595, 842)])
        cfg = core.RunConfig(inputs=[src], output=self.p("out"), mode="azure",
                             azure_vault_url="https://v.example",
                             page=2, x=10, y=20)
        calls, results = self._run(cfg)
        (_, kw) = calls["sign"][0]
        self.assertEqual(kw.get("page_index"), 1)
        self.assertEqual(kw.get("pos"), (10.0, 20.0))
        self.assertIn("page 2", results[0].detail)


class CredentialCanary(unittest.TestCase):
    """azure path fails cleanly with actionable errors — no real login."""

    def test_unconfigured_vault_is_a_config_error(self):
        cfg = core.RunConfig(inputs=[Path("x.pdf")], output=Path("o"),
                             mode="azure")
        with self.assertRaises(ValueError) as ctx:
            core.validate_config(cfg)
        self.assertIn("--azure-vault-url", str(ctx.exception))

    def test_unknown_auth_method_rejected(self):
        with self.assertRaises(azure_signer.AzureSigningError):
            azure_signer.build_credential("magic")

    def test_credential_construction_without_login(self):
        # Constructing the credential performs NO network I/O / no prompt.
        cred = azure_signer.build_credential("device-code")
        self.assertIsNotNone(cred)


if __name__ == "__main__":
    unittest.main()
