"""EU Trusted List trust provider (ETSI TS 119 612) for LTV signing.

Fetches the EU List of Trusted Lists (LOTL), follows the pointer to the
Belgian national trusted list, and extracts the X.509 certificates of trust
service providers qualified for electronic signatures (CA/QC, status
"granted", scoped to eSignatures). Those certificates seed the
``ValidationContext`` trust roots used to gather OCSP/CRL material for
PAdES B-LT / B-LTA signing.

Results are cached as a JSON file under the OS cache dir (``platformdirs``)
with a configurable TTL (default 24 h); ``refresh=True`` forces a re-fetch.
If the list is unreachable and no valid cache exists, ``TrustListError`` is
raised with an actionable message naming the endpoint — callers must NEVER
silently downgrade the signature level on that error.

Design notes (deliberate choices):

- Existing parsers were evaluated before hand-rolling the XML handling, per
  the project spec. pyHanko >= 0.35 ships native EUTL support
  (``pyhanko.sign.validation.qualified.eutl_fetch``) behind the ``[etsi]``
  extra, but that pulls in aiohttp + xsdata + generated TS 119 612 bindings
  (async-only, heavy for the frozen onefile binaries) and its cache layout
  does not match the platformdirs-file + TTL + ``--refresh-trust-list``
  semantics required here. Standalone PyPI EUTL parsers are niche and
  unmaintained. The narrow LOTL -> BE list -> CA/QC-cert extraction below
  only needs lxml + requests + platformdirs, which are already pinned
  dependencies of this project.
- The XML signatures of the LOTL / national list themselves are NOT
  verified (that would require xmlsec + the pivot-LOTL bootstrap process).
  Transport security relies on HTTPS to ec.europa.eu / the national
  operator. Recorded as a known limitation.
- Per TS 119 612 §5.5.9.4, a CA/QC service with no
  AdditionalServiceInformation extension is not restricted to a usage, so
  it is kept; services that declare ASi URIs are kept only if they include
  the ForeSignatures URI.

This module must stay importable without tkinter and is unit-testable with
the network mocked (inject ``fetcher``).
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import requests
from asn1crypto import x509
from lxml import etree

DEFAULT_LOTL_URL = "https://ec.europa.eu/tools/lotl/eu-lotl.xml"
ENV_LOTL_URL = "SIGNAPP_LOTL_URL"
DEFAULT_TERRITORY = "BE"
DEFAULT_TTL = 24 * 3600  # seconds
_HTTP_TIMEOUT = 30  # seconds per request
_CACHE_APP_NAME = "signApp"

_TSL_NS = "http://uri.etsi.org/02231/v2#"
_ADDTYPES_NS = "http://uri.etsi.org/02231/v2/additionaltypes#"
_NS = {"tsl": _TSL_NS, "tslx": _ADDTYPES_NS}
_TSL_XML_MIME = "application/vnd.etsi.tsl+xml"
_SVC_TYPE_CA_QC = "http://uri.etsi.org/TrstSvc/Svctype/CA/QC"
_SVC_STATUS_GRANTED = "http://uri.etsi.org/TrstSvc/TrustedList/Svcstatus/granted"
_ASI_FOR_ESIGNATURES = (
    "http://uri.etsi.org/TrstSvc/TrustedList/SvcInfoExt/ForeSignatures"
)


class TrustListError(RuntimeError):
    """EU trusted list could not be obtained (and no valid cache exists)."""


def resolve_lotl_url(explicit: str | None = None) -> str:
    """LOTL URL precedence: --trust-list-url flag > SIGNAPP_LOTL_URL > default."""
    return explicit or os.environ.get(ENV_LOTL_URL) or DEFAULT_LOTL_URL


def _parser() -> etree.XMLParser:
    # Hardened: the XML comes from the network. No entity expansion, no
    # external fetches during parsing.
    return etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)


def national_list_url(lotl_xml: bytes, territory: str = DEFAULT_TERRITORY) -> str:
    """Extract the XML trusted-list URL for `territory` from the LOTL.

    Pointers also exist in human-readable (PDF) form; only the
    application/vnd.etsi.tsl+xml one is the machine-processable list.
    """
    root = etree.fromstring(lotl_xml, parser=_parser())
    pointers = root.findall(
        ".//tsl:SchemeInformation/tsl:PointersToOtherTSL/tsl:OtherTSLPointer", _NS
    )
    for ptr in pointers:
        terr = ptr.findtext(".//tsl:SchemeTerritory", namespaces=_NS)
        mime = ptr.findtext(".//tslx:MimeType", namespaces=_NS)
        loc = ptr.findtext("tsl:TSLLocation", namespaces=_NS)
        if terr == territory and mime == _TSL_XML_MIME and loc:
            return loc.strip()
    raise TrustListError(
        f"No XML trusted-list pointer for territory {territory!r} in the LOTL."
    )


def qualified_esig_ca_certs(tl_xml: bytes) -> list[bytes]:
    """DER certs of granted CA/QC services qualified for eSignatures.

    Filters the national trusted list to ServiceTypeIdentifier CA/QC with
    ServiceStatus "granted", keeping services whose
    AdditionalServiceInformation includes ForeSignatures (or that declare no
    ASi at all, i.e. unrestricted). Deduplicated, document order preserved.
    """
    root = etree.fromstring(tl_xml, parser=_parser())
    services = root.findall(
        ".//tsl:TrustServiceProvider/tsl:TSPServices/tsl:TSPService"
        "/tsl:ServiceInformation",
        _NS,
    )
    certs: list[bytes] = []
    seen: set[bytes] = set()
    for svc in services:
        if svc.findtext("tsl:ServiceTypeIdentifier", namespaces=_NS) != _SVC_TYPE_CA_QC:
            continue
        if svc.findtext("tsl:ServiceStatus", namespaces=_NS) != _SVC_STATUS_GRANTED:
            continue
        asi = [
            uri.text.strip()
            for uri in svc.findall(
                ".//tsl:AdditionalServiceInformation/tsl:URI", _NS
            )
            if uri.text
        ]
        if asi and _ASI_FOR_ESIGNATURES not in asi:
            continue
        for node in svc.findall(
            ".//tsl:ServiceDigitalIdentity/tsl:DigitalId/tsl:X509Certificate", _NS
        ):
            try:
                der = base64.b64decode("".join((node.text or "").split()))
            except (ValueError, TypeError):
                continue  # malformed entry: skip, the list has redundancy
            if der and der not in seen:
                seen.add(der)
                certs.append(der)
    return certs


def _default_fetcher(url: str) -> bytes:
    resp = requests.get(
        url, timeout=_HTTP_TIMEOUT, headers={"User-Agent": "signApp-trust/1.0"}
    )
    resp.raise_for_status()
    return resp.content


def _cache_file(cache_dir: Path | None) -> Path:
    if cache_dir is None:
        import platformdirs

        cache_dir = Path(platformdirs.user_cache_dir(_CACHE_APP_NAME))
    return Path(cache_dir) / "eu_trust_anchors.json"


def _load_cache(path: Path, url: str, territory: str, ttl: float) -> list[bytes] | None:
    """Return cached DER certs if fresh and matching (url, territory), else None."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("lotl_url") != url or data.get("territory") != territory:
            return None
        if time.time() - float(data["fetched_at"]) > ttl:
            return None
        certs = [base64.b64decode(c) for c in data["certs_der_b64"]]
        return certs or None
    except (OSError, ValueError, KeyError, TypeError):
        return None  # missing/corrupt cache == no cache


def _save_cache(path: Path, url: str, territory: str, certs: list[bytes]) -> None:
    payload = {
        "fetched_at": time.time(),
        "lotl_url": url,
        "territory": territory,
        "certs_der_b64": [base64.b64encode(c).decode("ascii") for c in certs],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replace so a crash mid-write never leaves a corrupt cache.
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get_trust_anchors(
    url: str | None = None,
    *,
    territory: str = DEFAULT_TERRITORY,
    ttl: float = DEFAULT_TTL,
    refresh: bool = False,
    cache_dir: Path | None = None,
    fetcher=None,
) -> list[x509.Certificate]:
    """Trust anchors (asn1crypto certs) for the territory's qualified eSig CAs.

    Resolution: fresh cache (unless ``refresh``) -> network (LOTL -> national
    list) -> cache fallback (if the fetch fails but a fresh cache exists,
    e.g. ``refresh`` while offline) -> TrustListError. The error names the
    endpoint and is actionable; callers must propagate it rather than
    downgrade the signature level.
    """
    url = resolve_lotl_url(url)
    fetcher = fetcher or _default_fetcher
    cache_path = _cache_file(cache_dir)

    if not refresh:
        cached = _load_cache(cache_path, url, territory, ttl)
        if cached is not None:
            return [x509.Certificate.load(der) for der in cached]

    try:
        lotl_xml = fetcher(url)
        tl_url = national_list_url(lotl_xml, territory)
        tl_xml = fetcher(tl_url)
        ders = qualified_esig_ca_certs(tl_xml)
        if not ders:
            raise TrustListError(
                f"Trusted list for {territory} ({tl_url}) contains no granted "
                "CA/QC certificate for eSignatures — refusing to continue."
            )
        _save_cache(cache_path, url, territory, ders)
        return [x509.Certificate.load(der) for der in ders]
    except (requests.RequestException, etree.XMLSyntaxError, OSError) as exc:
        # Forced refresh while offline may still be served by a fresh cache.
        cached = _load_cache(cache_path, url, territory, ttl)
        if cached is not None:
            print(
                f"warning: EU trusted list refresh failed ({exc}); "
                f"using cached anchors from {cache_path}",
                file=sys.stderr,
            )
            return [x509.Certificate.load(der) for der in cached]
        raise TrustListError(
            f"EU trusted list unreachable: {url} ({exc}). "
            "LTV signing (b-lt/b-lta) needs the EU LOTL at least once to seed "
            "the trust anchors; connect to the network, or pass "
            "--trust-list-url / set SIGNAPP_LOTL_URL to a reachable mirror. "
            "No valid local cache exists. The signature level is NOT "
            "downgraded automatically."
        ) from exc
