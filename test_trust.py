"""Unit tests for trust.py (EU LOTL trust provider) — network fully mocked."""

from __future__ import annotations

import base64
import datetime
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

import trust

# ---------------------------------------------------------------------------
# Fixtures: minimal but namespace-correct TS 119 612 documents + real DER certs
# ---------------------------------------------------------------------------

_LOTL_URL = "https://lotl.example/eu-lotl.xml"
_BE_URL = "https://be.example/tsl-be.xml"

_LOTL_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<TrustServiceStatusList xmlns="http://uri.etsi.org/02231/v2#"
    xmlns:tslx="http://uri.etsi.org/02231/v2/additionaltypes#">
  <SchemeInformation>
    <PointersToOtherTSL>
      <OtherTSLPointer>
        <TSLLocation>https://de.example/tsl-de.xml</TSLLocation>
        <AdditionalInformation>
          <OtherInformation><SchemeTerritory>DE</SchemeTerritory></OtherInformation>
          <OtherInformation><tslx:MimeType>application/vnd.etsi.tsl+xml</tslx:MimeType></OtherInformation>
        </AdditionalInformation>
      </OtherTSLPointer>
      <OtherTSLPointer>
        <TSLLocation>https://be.example/tsl-be.pdf</TSLLocation>
        <AdditionalInformation>
          <OtherInformation><SchemeTerritory>BE</SchemeTerritory></OtherInformation>
          <OtherInformation><tslx:MimeType>application/pdf</tslx:MimeType></OtherInformation>
        </AdditionalInformation>
      </OtherTSLPointer>
      <OtherTSLPointer>
        <TSLLocation>{_BE_URL}</TSLLocation>
        <AdditionalInformation>
          <OtherInformation><SchemeTerritory>BE</SchemeTerritory></OtherInformation>
          <OtherInformation><tslx:MimeType>application/vnd.etsi.tsl+xml</tslx:MimeType></OtherInformation>
        </AdditionalInformation>
      </OtherTSLPointer>
    </PointersToOtherTSL>
  </SchemeInformation>
</TrustServiceStatusList>
""".encode()

_CA_QC = "http://uri.etsi.org/TrstSvc/Svctype/CA/QC"
_QTST = "http://uri.etsi.org/TrstSvc/Svctype/TSA/QTST"
_GRANTED = "http://uri.etsi.org/TrstSvc/TrustedList/Svcstatus/granted"
_WITHDRAWN = "http://uri.etsi.org/TrstSvc/TrustedList/Svcstatus/withdrawn"
_FOR_ESIG = "http://uri.etsi.org/TrstSvc/TrustedList/SvcInfoExt/ForeSignatures"
_FOR_ESEALS = "http://uri.etsi.org/TrstSvc/TrustedList/SvcInfoExt/ForeSeals"


def make_cert_der(cn: str) -> bytes:
    """Self-signed EC cert (fast) so trust-list entries are loadable DER."""
    from cryptography import x509 as cx509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = cx509.Name([cx509.NameAttribute(NameOID.COMMON_NAME, cn)])
    start = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
    cert = (
        cx509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(cx509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(start + datetime.timedelta(days=3650))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.DER)


_CERT_A = make_cert_der("Anchor A")
_CERT_B = make_cert_der("Anchor B")
_CERT_C = make_cert_der("Anchor C")


def _service(stype: str, status: str, der: bytes, asi: list[str] | None) -> str:
    ext = ""
    if asi is not None:
        uris = "".join(f"<URI>{u}</URI>" for u in asi)
        ext = (
            "<ServiceInformationExtensions><Extension Critical=\"false\">"
            f"<AdditionalServiceInformation>{uris}</AdditionalServiceInformation>"
            "</Extension></ServiceInformationExtensions>"
        )
    b64 = base64.b64encode(der).decode("ascii")
    return (
        "<TSPService><ServiceInformation>"
        f"<ServiceTypeIdentifier>{stype}</ServiceTypeIdentifier>"
        "<ServiceName><Name xml:lang=\"en\">svc</Name></ServiceName>"
        "<ServiceDigitalIdentity><DigitalId>"
        f"<X509Certificate>{b64}</X509Certificate>"
        "</DigitalId></ServiceDigitalIdentity>"
        f"<ServiceStatus>{status}</ServiceStatus>"
        f"{ext}"
        "</ServiceInformation></TSPService>"
    )


def _be_tl(services: list[str]) -> bytes:
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<TrustServiceStatusList xmlns=\"http://uri.etsi.org/02231/v2#\">"
        "<TrustServiceProviderList><TrustServiceProvider><TSPServices>"
        + "".join(services)
        + "</TSPServices></TrustServiceProvider></TrustServiceProviderList>"
        "</TrustServiceStatusList>"
    ).encode()


_BE_TL_XML = _be_tl(
    [
        _service(_CA_QC, _GRANTED, _CERT_A, [_FOR_ESIG]),       # kept
        _service(_CA_QC, _WITHDRAWN, _CERT_B, [_FOR_ESIG]),     # status: dropped
        _service(_CA_QC, _GRANTED, _CERT_B, [_FOR_ESEALS]),     # seals only: dropped
        _service(_QTST, _GRANTED, _CERT_B, [_FOR_ESIG]),        # type: dropped
        _service(_CA_QC, _GRANTED, _CERT_C, None),              # no ASi: kept
        _service(_CA_QC, _GRANTED, _CERT_A, [_FOR_ESIG]),       # duplicate: deduped
    ]
)


class _Fetcher:
    """Mock network: maps url -> bytes, records calls, optionally fails."""

    def __init__(self, mapping=None, fail=False):
        self.mapping = mapping or {_LOTL_URL: _LOTL_XML, _BE_URL: _BE_TL_XML}
        self.fail = fail
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        if self.fail:
            raise requests.ConnectionError(f"mock offline: {url}")
        return self.mapping[url]


class TrustParsing(unittest.TestCase):
    def test_lotl_pointer_resolution_filters_mime_and_territory(self):
        self.assertEqual(trust.national_list_url(_LOTL_XML, "BE"), _BE_URL)

    def test_lotl_missing_territory_raises(self):
        with self.assertRaises(trust.TrustListError):
            trust.national_list_url(_LOTL_XML, "FR")

    def test_cert_extraction_filters_and_dedup(self):
        ders = trust.qualified_esig_ca_certs(_BE_TL_XML)
        self.assertEqual(ders, [_CERT_A, _CERT_C])


class TrustFetchAndCache(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache_dir = Path(self._tmp.name)

    def _get(self, **kw):
        kw.setdefault("url", _LOTL_URL)
        kw.setdefault("cache_dir", self.cache_dir)
        return trust.get_trust_anchors(**kw)

    def test_fetch_returns_certificates_and_hits_both_urls(self):
        fetcher = _Fetcher()
        anchors = self._get(fetcher=fetcher)
        self.assertEqual(fetcher.calls, [_LOTL_URL, _BE_URL])
        self.assertEqual(len(anchors), 2)
        self.assertEqual(anchors[0].subject.native["common_name"], "Anchor A")

    def test_cache_hit_within_ttl_skips_network(self):
        fetcher = _Fetcher()
        self._get(fetcher=fetcher)
        self._get(fetcher=fetcher)
        self.assertEqual(len(fetcher.calls), 2)  # only the first call fetched

    def test_ttl_expiry_refetches(self):
        fetcher = _Fetcher()
        self._get(fetcher=fetcher)
        cache = self.cache_dir / "eu_trust_anchors.json"
        data = json.loads(cache.read_text())
        data["fetched_at"] -= trust.DEFAULT_TTL + 60  # age the cache past TTL
        cache.write_text(json.dumps(data))
        self._get(fetcher=fetcher)
        self.assertEqual(len(fetcher.calls), 4)

    def test_refresh_forces_fetch_despite_fresh_cache(self):
        fetcher = _Fetcher()
        self._get(fetcher=fetcher)
        self._get(fetcher=fetcher, refresh=True)
        self.assertEqual(len(fetcher.calls), 4)

    def test_offline_without_cache_raises_actionable_error(self):
        with self.assertRaises(trust.TrustListError) as ctx:
            self._get(fetcher=_Fetcher(fail=True))
        self.assertIn(_LOTL_URL, str(ctx.exception))
        self.assertIn("--trust-list-url", str(ctx.exception))

    def test_offline_refresh_falls_back_to_fresh_cache(self):
        self._get(fetcher=_Fetcher())  # prime the cache
        with mock.patch("sys.stderr"):  # silence the fallback warning
            anchors = self._get(fetcher=_Fetcher(fail=True), refresh=True)
        self.assertEqual(len(anchors), 2)

    def test_malformed_xml_without_cache_raises(self):
        bad = _Fetcher(mapping={_LOTL_URL: b"this is not xml"})
        with self.assertRaises(trust.TrustListError):
            self._get(fetcher=bad)

    def test_empty_anchor_set_is_an_error(self):
        empty = _Fetcher(mapping={_LOTL_URL: _LOTL_XML, _BE_URL: _be_tl([])})
        with self.assertRaises(trust.TrustListError):
            self._get(fetcher=empty)


class TrustUrlResolution(unittest.TestCase):
    def test_explicit_beats_env_beats_default(self):
        with mock.patch.dict("os.environ", {trust.ENV_LOTL_URL: "https://env.example/l.xml"}):
            self.assertEqual(trust.resolve_lotl_url("https://flag.example/l.xml"),
                             "https://flag.example/l.xml")
            self.assertEqual(trust.resolve_lotl_url(None), "https://env.example/l.xml")
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop(trust.ENV_LOTL_URL, None)
            self.assertEqual(trust.resolve_lotl_url(None), trust.DEFAULT_LOTL_URL)


if __name__ == "__main__":
    unittest.main()
