#!/usr/bin/env python3
"""Batch-sign PDFs with the Belgian electronic identity card (eID) via pyHanko.

Prerequisites:
    1. Belgian eID middleware installed (https://eid.belgium.be) -> provides
       the PKCS#11 library (libbeidpkcs11.so / .dylib / beidpkcs11.dll).
    2. Card reader + card inserted on THIS machine.
    3. pip install "pyHanko[pkcs11,image-support]" pyhanko-beid-plugin

Usage:
    python sign_pdfs_beid.py ./entree ./signes
    python sign_pdfs_beid.py ./entree ./signes --lib /usr/lib/libbeidpkcs11.so
    python sign_pdfs_beid.py doc1.pdf doc2.pdf ./signes --pades-level b-t

Important:
    - We use the SIGNATURE (non-repudiation) certificate, legally equivalent
      to a handwritten signature. Only authorize trusted code to use it.
    - The card generally requires the PIN for EACH signature
      (CKA_ALWAYS_AUTHENTICATE on the non-repudiation key), so plan for one
      PIN entry per document.
    - The national register number is embedded in the certificate and is
      therefore readable in every signature produced. Mind PDF distribution.
"""

from __future__ import annotations

# Single source of truth for the application version. Bump it on `develop`;
# merging develop -> main triggers the release workflow, which tags
# v{__version__} and publishes the binaries (see .github/workflows/release.yml
# and BUILD.md "Release process").
__version__ = "1.3.0"

import argparse
import dataclasses
import io
import os
import platform
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path

import requests
import pkcs11
from pkcs11 import Attribute, ObjectClass
from pkcs11.exceptions import PKCS11Error
from asn1crypto import x509
from PIL import Image

from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.pdf_utils import images
from pyhanko.pdf_utils.layout import (
    AxisAlignment,
    BoxConstraints,
    InnerScaling,
    Margins,
    SimpleBoxLayoutRule,
)
from pyhanko.sign import PdfSignatureMetadata, signers, timestamps
from pyhanko.sign.fields import SigFieldSpec, SigSeedSubFilter
from pyhanko.sign.validation import validate_pdf_signature
from pyhanko.sign.validation.dss import DocumentSecurityStore
from pyhanko.sign.validation.errors import NoDSSFoundError
from pyhanko.stamp import StaticStampStyle, TextStampStyle
from pyhanko_certvalidator import ValidationContext

# Since pyHanko >= 0.22, Belgian eID support lives in the separate plugin.
# On a very old install (< 0.22), replace with:
#   from pyhanko.sign.beid import BEIDSigner
# from pyhanko_beid import BEIDSigner
# We no longer use open_beid_session(): it rigidly requires a token with the
# label "BELPIC", which fails depending on the reader/middleware. See
# open_eid_session() below.
from pyhanko_beid.beid import BEIDSigner

def default_pkcs11_lib() -> str:
    """Likely path of the eID PKCS#11 lib depending on the OS."""
    system = platform.system()
    if system == "Windows":
        return r"C:\Windows\System32\beidpkcs11.dll"
    if system == "Darwin":
        return "/usr/local/lib/libbeidpkcs11.dylib"
    # Linux: the location varies depending on the distribution.
    for candidate in (
        "/usr/lib/libbeidpkcs11.so",
        "/usr/local/lib/libbeidpkcs11.so",
        "/usr/lib/x86_64-linux-gnu/libbeidpkcs11.so",
    ):
        if Path(candidate).exists():
            return candidate
    return "/usr/lib/libbeidpkcs11.so"


def open_eid_session(lib_path: str):
    """Open a PKCS#11 session on the eID card, with clear diagnostics.

    Replaces ``pyhanko_beid.open_beid_session()`` which rigidly requires a
    token with the label "BELPIC". Depending on the reader or middleware
    version, the token may carry another label (or none), hence the misleading
    error "No token matching criteria TokenCriteria(label='BELPIC') found" —
    an error that ALSO occurs, identically, when no card can be read.

    Strategy: we pick the "BELPIC" token if it exists, otherwise the first
    token present; and we emit an actionable message if no reader or no card
    is detected. The returned session (``token.open()``) is identical to the
    one pyHanko uses internally.
    """
    try:
        lib = pkcs11.lib(lib_path)
    except Exception as exc:  # noqa: BLE001 - we want a readable message
        raise SystemExit(
            f"Cannot load the PKCS#11 library \"{lib_path}\": {exc}"
        )

    slots = lib.get_slots(token_present=False)
    if not slots:
        raise SystemExit(
            "No card reader detected.\n"
            "  - Plug in the eID reader.\n"
            "  - Check that the pcscd service is running: "
            "sudo systemctl status pcscd"
        )

    # Slots that actually contain a token (= a readable card).
    tokens = []
    for slot in slots:
        try:
            tokens.append(slot.get_token())
        except PKCS11Error:
            continue  # reader present, but no readable card in this slot

    if not tokens:
        readers = "\n".join(f"      - {s.slot_description.strip()}" for s in slots)
        raise SystemExit(
            "Reader detected, but no card could be read:\n"
            f"{readers}\n"
            "  Check that:\n"
            "    - the Belgian eID card is inserted correctly and fully;\n"
            "    - it is indeed an eID card (not another smart card);\n"
            "    - the eID middleware is installed and the pcscd service is running.\n"
            "  Re-insert the card then run the command again."
        )

    chosen = next(
        (t for t in tokens if (t.label or "").strip().upper() == "BELPIC"),
        None,
    )
    if chosen is None:
        chosen = tokens[0]
        print(
            f"  Note: no \"BELPIC\" token found; using the token "
            f"present (label={chosen.label!r}).",
            file=sys.stderr,
        )

    print(f"  Card detected: label={chosen.label!r}, serial={chosen.serial!r}")
    return chosen.open()


# --- Visible signature appearance (vignette at bottom-right) ---------------
_STAMP_W = 210        # default vignette width (PDF points, 1 pt = 1/72")
_STAMP_H = 62         # default vignette height (3 lines of text)
_STAMP_MARGIN = 24    # margin from the page's lower-right corner
_PHOTO_BAND_FRAC = 0.2  # share of the width reserved for the photo (0.2×210 = 42, unchanged)

# Freely placed vignette (beid mode with a chosen position): the exact size is
# not known in advance, so we draw a 3:1 (landscape) box one-fifth as wide as
# the page (height = width / 3).
_VIGNETTE_W_FRAC = 1 / 5
_VIGNETTE_ASPECT = 3.0


def vignette_size_pt(page_w: float) -> tuple[float, float]:
    """Size (width, height) in points of the freely placed vignette:
    width = page_w/5, height = width/3 (3:1 landscape ratio)."""
    w = page_w * _VIGNETTE_W_FRAC
    return (w, w / _VIGNETTE_ASPECT)


@dataclasses.dataclass
class CardIdentity:
    """Data read from the card for the signature vignette."""

    name: str                      # e.g. "Sébastien Denooz"
    photo: Image.Image | None      # eID portrait (or None if unreadable)


def read_card_identity(session) -> CardIdentity:
    """Read the name (signature certificate) and the photo (``PHOTO_FILE``
    object) from the eID card via the already-open PKCS#11 session.

    Neither read requires the PIN; they can therefore be done once, before the
    signing loop.
    """
    first = last = common = None
    try:
        for obj in session.get_objects({Attribute.CLASS: ObjectClass.CERTIFICATE}):
            if obj[Attribute.LABEL] == "Signature":
                subject = x509.Certificate.load(
                    bytes(obj[Attribute.VALUE])
                ).subject.native
                first = subject.get("given_name")
                last = subject.get("surname")
                common = subject.get("common_name")
                break
    except PKCS11Error:
        pass

    if first and last:
        name = f"{first} {last}"
    elif common:
        name = common.split(" (")[0]  # strip the "(Signature)" suffix
    else:
        name = "the cardholder"

    photo = None
    try:
        for obj in session.get_objects(
            {Attribute.CLASS: ObjectClass.DATA, Attribute.LABEL: "PHOTO_FILE"}
        ):
            data = bytes(obj[Attribute.VALUE])
            if data[:2] == b"\xff\xd8":  # JPEG header
                photo = Image.open(io.BytesIO(data))
                photo.load()  # decode now: the session may then be closed
            break
    except (PKCS11Error, OSError):
        photo = None

    return CardIdentity(name=name, photo=photo)


def read_cert_identity(cert, display_name: str | None = None) -> CardIdentity:
    """Identity for the vignette from a certificate subject (azure mode).

    Mirrors ``read_card_identity()``'s shape with ``photo=None`` (the
    text-only vignette layout already supports that). Name preference:
    explicit ``display_name`` (e.g. Microsoft Graph) > given_name + surname
    > common name.
    """

    def _first(value):
        return value[0] if isinstance(value, list) else value

    name = display_name
    if not name:
        subject = cert.subject.native
        given, surname = _first(subject.get("given_name")), _first(subject.get("surname"))
        if given and surname:
            name = f"{given} {surname}"
        else:
            name = _first(subject.get("common_name"))
    return CardIdentity(name=str(name or "Unknown signer"), photo=None)


def load_trust_anchor_certs(path) -> list:
    """Load trust-anchor certificates from a PEM/DER file or a directory.

    Used for the internal-CA chain of azure mode (--azure-trust-anchors).
    A directory is scanned for *.pem/*.crt/*.cer/*.der files; PEM files may
    hold several certificates. Raises ValueError when nothing loads.
    """
    from asn1crypto import pem

    p = Path(path)
    if p.is_dir():
        files = sorted(
            f for f in p.iterdir()
            if f.suffix.lower() in (".pem", ".crt", ".cer", ".der")
        )
    else:
        files = [p]
    certs: list = []
    for f in files:
        data = f.read_bytes()
        if pem.detect(data):
            for kind, _, der in pem.unarmor(data, multiple=True):
                if kind == "CERTIFICATE":
                    certs.append(x509.Certificate.load(der))
        else:
            certs.append(x509.Certificate.load(data))
    if not certs:
        raise ValueError(f"No certificate found in {path}")
    return certs


def _page_mediabox(writer, page_index: int) -> list[float]:
    """MediaBox of page `page_index` (0-based, -1 = last), with inheritance
    from the page tree."""
    page_ref, _ = writer.find_page_for_modification(page_index)
    node = page_ref.get_object()
    for _ in range(50):
        if "/MediaBox" in node:
            mb = node.raw_get("/MediaBox").get_object()
            return [
                float(v.get_object() if hasattr(v, "get_object") else v) for v in mb
            ]
        parent = node.get("/Parent")
        if parent is None:
            break
        node = parent.get_object()
    return [0.0, 0.0, 595.276, 841.89]  # A4 default


def _default_vignette_box(writer, page_index: int = -1) -> tuple[float, float, float, float]:
    """Vignette rectangle, anchored bottom-right of page ``page_index``
    (0-based, -1 = last page — the historical default)."""
    mb = _page_mediabox(writer, page_index)
    x1 = mb[2] - _STAMP_MARGIN
    x0 = max(mb[0] + 4, x1 - _STAMP_W)
    y0 = mb[1] + _STAMP_MARGIN
    y1 = min(y0 + _STAMP_H, mb[3] - 4)
    return (x0, y0, x1, y1)


def build_stamp_style(
    identity: CardIdentity, box_w: float = _STAMP_W, box_h: float = _STAMP_H
) -> TextStampStyle:
    """Build the appearance: photo on the left, "Signed by ..." on the right.

    Margins are PROPORTIONAL to the box width (box_w): the photo band equals
    ``_PHOTO_BAND_FRAC × box_w`` (i.e. 42 pt for the default 210 pt box —
    unchanged), which lets the same vignette fit in a smaller box (beid mode
    with a free position) without overflowing.

    A NEW instance is created per document: the background image is bound to
    the writer at render time, so we avoid reusing the same object across PDFs.
    """
    # Vignette in 3 lines: "Signed by:" / name / "at <date>".
    # The name is escaped (%%) in case it contains a %, while %(ts)s is
    # replaced by pyHanko with the current date (format below) at signing time.
    safe_name = identity.name.replace("%", "%%")
    text = f"Signed by:\n{safe_name}\nat %(ts)s"
    text_style = dataclasses.replace(TextStampStyle().text_box_style, font_size=10)
    band = _PHOTO_BAND_FRAC * box_w   # photo band, on the left

    if identity.photo is not None:
        return TextStampStyle(
            stamp_text=text,
            timestamp_format="%d/%m/%Y",
            background=images.PdfImage(identity.photo),
            background_opacity=1.0,
            background_layout=SimpleBoxLayoutRule(
                x_align=AxisAlignment.ALIGN_MIN,
                y_align=AxisAlignment.ALIGN_MID,
                margins=Margins(left=4, right=box_w - band, top=4, bottom=4),
                inner_content_scaling=InnerScaling.SHRINK_TO_FIT,
            ),
            inner_content_layout=SimpleBoxLayoutRule(
                x_align=AxisAlignment.ALIGN_MIN,
                y_align=AxisAlignment.ALIGN_MID,
                margins=Margins(left=band + 6, right=6, top=4, bottom=4),
                inner_content_scaling=InnerScaling.SHRINK_TO_FIT,
            ),
            text_box_style=text_style,
            border_width=0,
        )

    # No readable photo: fall back to text only, centered.
    return TextStampStyle(
        stamp_text=text,
        timestamp_format="%d/%m/%Y",
        text_box_style=text_style,
        inner_content_layout=SimpleBoxLayoutRule(
            x_align=AxisAlignment.ALIGN_MID,
            y_align=AxisAlignment.ALIGN_MID,
            margins=Margins(left=6, right=6, top=4, bottom=4),
            inner_content_scaling=InnerScaling.SHRINK_TO_FIT,
        ),
        border_width=0,
    )


def collect_pdfs(inputs: list[str]) -> list[Path]:
    """Resolve the input arguments into a list of PDF files."""
    pdfs: list[Path] = []
    for raw in inputs:
        path = Path(raw)
        if path.is_dir():
            pdfs.extend(sorted(path.glob("*.pdf")))
        elif path.suffix.lower() == ".pdf" and path.is_file():
            pdfs.append(path)
        else:
            print(f"  Ignored (not a PDF): {path}", file=sys.stderr)
    return pdfs


# --------------------------------------------------------------------------
# PAdES baseline levels (ETSI EN 319 142-1)
# --------------------------------------------------------------------------

PADES_LEVELS = ("b-b", "b-t", "b-lt", "b-lta")
DIGEST_CHOICES = ("sha256", "sha384", "sha512")
# Free public RFC 3161 TSA. Technically valid for B-T/B-LTA but NOT a
# *qualified* timestamp; point --timestamp-url at a qualified TSA for
# eIDAS-grade long-term preservation (see README).
DEFAULT_TSA_URL = "http://timestamp.digicert.com"
ENV_TSA_URL = "CACHET_TSA_URL"

# azure mode (Entra ID + Key Vault, personal AES). Flag > env > default.
AZURE_AUTH_METHODS = ("interactive", "device-code", "default")
ENV_AZURE_VAULT_URL = "CACHET_AZURE_VAULT_URL"
ENV_AZURE_KEY_NAME = "CACHET_AZURE_KEY_NAME"
ENV_AZURE_KEY_TEMPLATE = "CACHET_AZURE_KEY_NAME_TEMPLATE"
ENV_AZURE_CERT_NAME = "CACHET_AZURE_CERT_NAME"
ENV_AZURE_AUTH = "CACHET_AZURE_AUTH"
ENV_AZURE_TRUST_ANCHORS = "CACHET_AZURE_TRUST_ANCHORS"


def resolve_azure_auth(explicit: str | None = None) -> str:
    """Auth method precedence: --azure-auth > CACHET_AZURE_AUTH >
    device-code (the headless-CLI default; the GUI passes interactive)."""
    return explicit or os.environ.get(ENV_AZURE_AUTH) or "device-code"


def resolve_tsa_url(explicit: str | None = None) -> str:
    """TSA URL precedence: --timestamp-url flag > CACHET_TSA_URL > default."""
    return explicit or os.environ.get(ENV_TSA_URL) or DEFAULT_TSA_URL


def level_needs_timestamp(pades_level: str) -> bool:
    """Levels >= b-t require an RFC 3161 timestamp token."""
    return pades_level in ("b-t", "b-lt", "b-lta")


def level_needs_ltv(pades_level: str) -> bool:
    """Levels >= b-lt embed revocation info (OCSP/CRL) into the DSS."""
    return pades_level in ("b-lt", "b-lta")


def signature_level_label(pades_level: str, legacy_cms: bool = False) -> str:
    """Human-readable label of the signature strength, for summaries."""
    if legacy_cms:
        return "legacy CMS (adbe.pkcs7.detached)"
    return f"PAdES-{pades_level.upper()}"


def signature_meta_kwargs(
    pades_level: str,
    digest: str = "sha256",
    *,
    legacy_cms: bool = False,
    validation_context=None,
) -> dict:
    """Map a PAdES level to ``PdfSignatureMetadata`` keyword arguments (pure).

    b-b   = basic PAdES signature;
    b-t   = + trusted timestamp (the timestamper itself is attached to the
            ``PdfSigner``, not to the metadata);
    b-lt  = + revocation info (OCSP/CRL) embedded into the DSS at signing
            time, which needs a fetching ``ValidationContext``;
    b-lta = + archival DocumentTimeStamp chain.

    ``legacy_cms`` selects the historical non-PAdES adbe.pkcs7.detached
    subfilter instead (kept reachable only through the deprecated
    --legacy-cms flag).
    """
    if legacy_cms:
        return {
            "md_algorithm": digest,
            "subfilter": SigSeedSubFilter.ADOBE_PKCS7_DETACHED,
        }
    if pades_level not in PADES_LEVELS:
        raise ValueError(
            f"Unknown PAdES level: {pades_level!r} (expected {'|'.join(PADES_LEVELS)})."
        )
    kwargs: dict = {"md_algorithm": digest, "subfilter": SigSeedSubFilter.PADES}
    if level_needs_ltv(pades_level):
        kwargs["embed_validation_info"] = True
        kwargs["validation_context"] = validation_context
    if pades_level == "b-lta":
        kwargs["use_pades_lta"] = True
    return kwargs


def sign_one(
    signer: signers.Signer,
    src: Path,
    dst: Path,
    field_name: str,
    pades_level: str,
    identity: CardIdentity,
    page_index: int | None = None,
    pos: tuple[float, float] | None = None,
    *,
    digest: str = "sha256",
    legacy_cms: bool = False,
    timestamper: timestamps.TimeStamper | None = None,
    validation_context: ValidationContext | None = None,
) -> None:
    """Sign a PDF (incremental signature) with any pyHanko ``Signer`` and
    stamp the visible vignette onto it ("Signed by ..." + optional photo).

    ``pades_level`` selects the PAdES baseline level (see
    ``signature_meta_kwargs``); ``timestamper`` must be provided for levels
    >= b-t and ``validation_context`` for b-lt/b-lta (both are built once per
    batch by ``build_signing_material``).

    By default (``pos`` None) the vignette is anchored bottom-right of page
    ``page_index`` — the LAST page when that is None too (historical behavior).
    If ``pos`` is provided, it is placed on page ``page_index`` (0-based),
    lower-left corner at ``pos`` points, in a 3:1 box one-fifth as wide as the
    page (cf. ``vignette_size_pt``).
    """
    meta = PdfSignatureMetadata(
        field_name=field_name,
        **signature_meta_kwargs(
            pades_level,
            digest,
            legacy_cms=legacy_cms,
            validation_context=validation_context,
        ),
    )
    with src.open("rb") as inf:
        # strict=False: accept PDFs with "hybrid" cross-reference sections
        # (classic xref table + xref stream in the same file), produced by
        # some tools for backward compatibility. In strict mode, pyHanko
        # refuses to sign them
        # ("hybrid cross-reference sections while hybrid xrefs are disabled").
        writer = IncrementalPdfFileWriter(inf, strict=False)
        if pos is None:
            on_page = page_index if page_index is not None else -1
            box = _default_vignette_box(writer, on_page)
            style = build_stamp_style(identity)
        else:
            on_page = page_index if page_index is not None else -1
            mb = _page_mediabox(writer, on_page)
            vw, vh = vignette_size_pt(mb[2] - mb[0])
            x0, y0 = mb[0] + pos[0], mb[1] + pos[1]
            box = (x0, y0, x0 + vw, y0 + vh)
            style = build_stamp_style(identity, vw, vh)
        field_spec = SigFieldSpec(sig_field_name=field_name, on_page=on_page, box=box)
        pdf_signer = signers.PdfSigner(
            meta,
            signer=signer,
            timestamper=timestamper,
            stamp_style=style,
            new_field_spec=field_spec,
        )
        out = pdf_signer.sign_pdf(writer)
    dst.write_bytes(out.getbuffer())


# =========================================================================
#  Validation against a template
# =========================================================================

# Page anchors: with --page first|last (or the GUI's step-4 selector) the
# signature page is resolved PER DOCUMENT (index 0 / -1). This is what lets a
# batch contain documents whose page count differs from the template: the
# anchor page must still match the template's anchor page exactly, so the
# chosen (x, y) is guaranteed to fit the page that carries the signature.
PAGE_ANCHORS = ("first", "last")


def anchor_page_index(page_anchor: str) -> int:
    """0-based page index of a page anchor: "first" -> 0, "last" -> -1."""
    return 0 if page_anchor == "first" else -1


def _iter_pages(node, inherited_mb=None):
    """Walk the page tree in order and yield (page, mediabox), propagating the
    /MediaBox inherited from a parent node."""
    node = node.get_object()
    mb = node.raw_get("/MediaBox").get_object() if "/MediaBox" in node else inherited_mb
    if "/Kids" in node:
        for kid in node["/Kids"]:
            yield from _iter_pages(kid, mb)
    else:
        yield node, mb


def _mediabox_wh(mb) -> tuple[float, float]:
    if mb is None:
        return (595.276, 841.89)  # A4 default
    vals = [float(v.get_object() if hasattr(v, "get_object") else v) for v in mb]
    return (vals[2] - vals[0], vals[3] - vals[1])


def page_dimensions(pdf_path) -> list[tuple[float, float]]:
    """Dimensions (width, height) in points of each page, in order."""
    with open(pdf_path, "rb") as f:
        reader = PdfFileReader(f, strict=False)
        return [_mediabox_wh(mb) for _, mb in _iter_pages(reader.root["/Pages"])]


@dataclasses.dataclass
class ValidationResult:
    """Result of validating a file against the template."""

    path: Path
    ok: bool
    reason: str = ""


def validate_against_template(
    template_dims: list[tuple[float, float]],
    pdf_path,
    *,
    page_anchor: str | None = None,
) -> ValidationResult:
    """Check that a PDF has the SAME page count AND per-page dimensions
    EXACTLY identical to the template (no tolerance).

    With ``page_anchor`` ("first"/"last"), a file whose page count DIFFERS
    from the template is accepted iff its anchor page has exactly the
    template's anchor-page dimensions — that is the page the signature lands
    on, so the chosen position is guaranteed to fit it. Files with the
    template's page count keep the full strict check.
    """
    path = Path(pdf_path)
    try:
        dims = page_dimensions(path)
    except Exception as exc:  # noqa: BLE001
        return ValidationResult(path, False, f"unreadable ({exc})")
    if len(dims) != len(template_dims):
        count_msg = f"{len(dims)} page(s), the template has {len(template_dims)}"
        if page_anchor is None or not dims or not template_dims:
            return ValidationResult(path, False, count_msg)
        idx = anchor_page_index(page_anchor)
        d, t = dims[idx], template_dims[idx]
        if d != t:
            return ValidationResult(
                path,
                False,
                f"{count_msg}; {page_anchor} page {d[0]:.2f}×{d[1]:.2f} pt "
                f"≠ template {t[0]:.2f}×{t[1]:.2f} pt",
            )
        return ValidationResult(
            path, True, f"{count_msg} — signed on the {page_anchor} page"
        )
    for i, (d, t) in enumerate(zip(dims, template_dims), start=1):
        if d != t:
            return ValidationResult(
                path,
                False,
                f"page {i}: {d[0]:.2f}×{d[1]:.2f} pt ≠ template {t[0]:.2f}×{t[1]:.2f} pt",
            )
    return ValidationResult(path, True, "")


def validate_files(
    template_path, pdf_paths, *, page_anchor: str | None = None
) -> list[ValidationResult]:
    """Validate a list of PDFs against the template (read once)."""
    template_dims = page_dimensions(template_path)
    return [
        validate_against_template(template_dims, p, page_anchor=page_anchor)
        for p in pdf_paths
    ]


# =========================================================================
#  "image" mode: inserting a signature image
# =========================================================================
_IMG_TARGET_W_PT = 150.0  # default insertion width (points, ratio preserved)


def image_size_pt(image_path) -> tuple[float, float]:
    """Size (width, height) in points of the inserted image: fixed width
    (_IMG_TARGET_W_PT), height derived from the ratio."""
    with Image.open(image_path) as im:
        w_px, h_px = im.size
    return (_IMG_TARGET_W_PT, _IMG_TARGET_W_PT * h_px / w_px)


def insert_image_one(src, dst, image_path, page_index: int, x: float, y: float) -> None:
    """Insert the image on page `page_index` (0-based, -1 = last) with its
    lower-left corner at (x, y) points from the page's lower-left corner.
    Incremental update — no card required."""
    img = Image.open(image_path)
    img.load()
    w_pt, h_pt = image_size_pt(image_path)
    n_pages = len(page_dimensions(src))  # clear message if the page is out of range
    if not (-n_pages <= page_index < n_pages):
        shown = page_index + 1 if page_index >= 0 else page_index
        raise ValueError(f"page {shown} out of range (the document has {n_pages} page(s))")
    with open(src, "rb") as inf:
        writer = IncrementalPdfFileWriter(inf, strict=False)
        mb = _page_mediabox(writer, page_index)  # (x, y) relative to the page corner
        style = StaticStampStyle(
            background=images.PdfImage(img),
            background_opacity=1.0,
            background_layout=SimpleBoxLayoutRule(
                x_align=AxisAlignment.ALIGN_MID,
                y_align=AxisAlignment.ALIGN_MID,
                margins=Margins(),
                inner_content_scaling=InnerScaling.STRETCH_TO_FIT,
            ),
            border_width=0,   # no black border around the inserted image
        )
        stamp = style.create_stamp(writer, BoxConstraints(width=w_pt, height=h_pt), {})
        stamp.apply(page_index, int(round(mb[0] + x)), int(round(mb[1] + y)))
        out = io.BytesIO()
        writer.write(out)
    Path(dst).write_bytes(out.getbuffer())


# =========================================================================
#  Placement math (shared with the GUI, testable without tkinter)
# =========================================================================

def fit_frame(page_w, page_h, max_w, max_h) -> tuple[float, float]:
    """Dimensions (w, h) of a frame preserving the page ratio and fitting
    within (max_w, max_h)."""
    scale = min(max_w / page_w, max_h / page_h)
    return (page_w * scale, page_h * scale)


def frame_click_to_pdf_xy(
    page_w, page_h, frame_w, frame_h, click_x, click_y
) -> tuple[float, float]:
    """Convert a click in the frame (top-left origin, like tkinter) into a PDF
    point (bottom-left origin), in points."""
    pdf_x = click_x / frame_w * page_w
    pdf_y = (frame_h - click_y) / frame_h * page_h
    return (pdf_x, pdf_y)


def pdf_rect_to_frame_rect(
    page_w, page_h, frame_w, frame_h, x, y, img_w, img_h
) -> tuple[float, float, float, float]:
    """Rectangle (left, top, width, height) in frame pixels to draw, to scale,
    an image of size (img_w, img_h) pt whose PDF lower-left corner is (x, y)."""
    sx, sy = frame_w / page_w, frame_h / page_h
    return (x * sx, frame_h - (y + img_h) * sy, img_w * sx, img_h * sy)


def render_page_image(pdf_path, page_index: int, px_width: int = 900):
    """Bitmap render of page `page_index` (0-based) as a Pillow image
    (~px_width wide, ratio preserved), or ``None`` if rendering fails.

    Tries **pypdfium2** first (PDFium engine bundled INSIDE the package: no
    external dependency, works as-is in the PyInstaller executable on
    Windows/Linux/macOS), then falls back to **pdftoppm** (poppler) if it is
    installed on the machine. Used as the background of the selection frame;
    the calling code falls back to a white frame if ``None``.
    """
    img = _render_with_pdfium(pdf_path, page_index, px_width)
    if img is not None:
        return img
    return _render_with_pdftoppm(pdf_path, page_index, px_width)


def _render_with_pdfium(pdf_path, page_index: int, px_width: int):
    """Render via pypdfium2 (bundled PDFium). ``None`` if unavailable/failed.

    The import is LAZY: the core stays importable without pypdfium2 (e.g. the
    headless CLI binary, where the package is deliberately excluded)."""
    try:
        import pypdfium2 as pdfium
    except Exception:  # noqa: BLE001 - package missing
        return None
    pdf = None
    try:
        pdf = pdfium.PdfDocument(str(pdf_path))
        if not (0 <= page_index < len(pdf)):
            return None
        page = pdf[page_index]
        w_pt, _ = page.get_size()                 # size in points (1 pt = 1/72")
        scale = (px_width / w_pt) if w_pt else 1.0  # scale=1.0 -> 72 dpi (px = pt)
        img = page.render(scale=scale).to_pil().convert("RGB")
        img.load()
        return img
    except Exception:  # noqa: BLE001 - render impossible -> poppler fallback
        return None
    finally:
        if pdf is not None:
            try:
                pdf.close()
            except Exception:  # noqa: BLE001
                pass


def _render_with_pdftoppm(pdf_path, page_index: int, px_width: int):
    """Render via `pdftoppm` (poppler) if it is present on the machine. ``None``
    otherwise. Historical fallback when pypdfium2 is unavailable."""
    page_no = page_index + 1
    with tempfile.TemporaryDirectory() as td:
        prefix = os.path.join(td, "page")
        try:
            subprocess.run(
                ["pdftoppm", "-png", "-f", str(page_no), "-l", str(page_no),
                 "-scale-to-x", str(int(px_width)), "-scale-to-y", "-1",
                 str(pdf_path), prefix],
                check=True, capture_output=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        pngs = sorted(Path(td).glob("page*.png"))
        if not pngs:
            return None
        img = Image.open(pngs[0])
        img.load()
        return img


def open_in_file_manager(path, *, system: str | None = None,
                         popen=subprocess.Popen, startfile=None) -> None:
    """Show ``path`` (the output folder) in the desktop's file manager:
    Explorer on Windows (``os.startfile``), Finder on macOS (``open``),
    ``xdg-open`` elsewhere. A missing folder raises ``FileNotFoundError``
    before anything is launched (the Unix launchers exit asynchronously, so
    their own failures are not observable), and a missing launcher / an
    ``os.startfile`` refusal propagate — the caller reports them.
    ``system``/``popen``/``startfile`` are injectable for tests (no desktop
    is touched there)."""
    system = system or platform.system()
    target = str(path)
    if not Path(target).is_dir():
        raise FileNotFoundError(f"Folder not found: {target}")
    if system == "Windows":
        (startfile or os.startfile)(target)  # noqa: S606 - opens a folder, no shell
        return
    cmd = ["open", target] if system == "Darwin" else ["xdg-open", target]
    popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def unique_output_path(out_dir, stem: str, suffix: str = "_signe") -> Path:
    """Free output path: ``{stem}{suffix}.pdf`` then, on collision,
    ``{stem}{suffix} - 1.pdf``, ``- 2``, … until a non-existent name. NEVER
    overwrites an existing file."""
    out_dir = Path(out_dir)
    base = f"{stem}{suffix}"
    candidate = out_dir / f"{base}.pdf"
    i = 1
    while candidate.exists():
        candidate = out_dir / f"{base} - {i}.pdf"
        i += 1
    return candidate


# =========================================================================
#  Run configuration + batch processing (shared CLI / GUI)
# =========================================================================

@dataclasses.dataclass
class RunConfig:
    """Parameters of a run, identical for the CLI and the GUI."""

    inputs: list[Path]
    output: Path
    mode: str = "beid"               # "beid" (eID card + vignette) | "image"
    template: Path | None = None
    pades_level: str = "b-lta"       # b-b | b-t | b-lt | b-lta (beid mode)
    field: str = "Signature"
    lib: str | None = None
    image_path: Path | None = None
    # Page (1-based) + position (points, bottom-left corner). None = unspecified:
    # in image mode we fall back to page 1 / (0, 0); in beid mode, the absence of
    # a position triggers the default vignette (bottom-right, last page).
    page: int | None = None
    x: float | None = None
    y: float | None = None
    # "first"/"last": resolve the signature page PER DOCUMENT (index 0 / -1)
    # instead of a fixed 1-based page, and relax template validation to accept
    # page-count mismatches (the anchor page must still match the template's).
    # Mutually exclusive with `page`.
    page_anchor: str | None = None
    timestamp_url: str | None = None   # None -> CACHET_TSA_URL env -> DigiCert
    trust_list_url: str | None = None  # None -> CACHET_LOTL_URL env -> EU LOTL
    digest: str = "sha256"             # sha256 | sha384 | sha512
    verify: bool = True                # post-signing self-verification (>= b-t)
    legacy_cms: bool = False           # deprecated adbe.pkcs7.detached path
    refresh_trust_list: bool = False   # bypass the trusted-list cache
    # azure mode (Entra ID + Key Vault). Trust anchors = INTERNAL CA chain,
    # never the EU LOTL (that one is beid-only).
    azure_vault_url: str | None = None
    azure_key_name: str | None = None           # explicit override (flagged)
    azure_key_name_template: str | None = None  # None -> "sig-{upn}"
    azure_cert_name: str | None = None          # None -> same as key name
    azure_auth: str | None = None               # None -> device-code (CLI)
    azure_trust_anchors: Path | None = None     # PEM/DER file or directory
    azure_use_graph: bool = False               # Graph /me displayName opt-in


@dataclasses.dataclass
class DocResult:
    """Outcome of processing a document."""

    path: Path
    output: Path | None
    ok: bool
    detail: str


def validate_config(cfg: RunConfig) -> None:
    """Check the consistency of a RunConfig; raises ValueError otherwise."""
    if not cfg.inputs:
        raise ValueError("No input PDF.")
    if cfg.output is None:
        raise ValueError("Missing output folder (--output).")
    if cfg.mode not in ("beid", "image", "azure"):
        raise ValueError(f"Unknown mode: {cfg.mode!r} (expected beid|image|azure).")
    if cfg.page_anchor is not None and cfg.page_anchor not in PAGE_ANCHORS:
        raise ValueError(
            f"Unknown page anchor: {cfg.page_anchor!r} "
            f"(expected {'|'.join(PAGE_ANCHORS)})."
        )
    if cfg.page_anchor and cfg.page is not None:
        raise ValueError(
            "Give either a page number or first/last as the target page, not both."
        )
    if cfg.mode in ("beid", "azure"):
        if cfg.pades_level not in PADES_LEVELS:
            raise ValueError(
                f"Unknown PAdES level: {cfg.pades_level!r} "
                f"(expected {'|'.join(PADES_LEVELS)})."
            )
        if cfg.digest not in DIGEST_CHOICES:
            raise ValueError(
                f"Unknown digest: {cfg.digest!r} (expected {'|'.join(DIGEST_CHOICES)})."
            )
        if cfg.legacy_cms and cfg.pades_level != "b-b":
            raise ValueError(
                "legacy_cms is incompatible with PAdES levels above b-b."
            )
    if cfg.mode == "azure":
        if cfg.legacy_cms:
            raise ValueError("--legacy-cms only applies to beid mode.")
        if not cfg.azure_vault_url:
            raise ValueError(
                "azure mode needs the Key Vault URL: pass --azure-vault-url "
                f"or set {ENV_AZURE_VAULT_URL}."
            )
        if cfg.azure_auth and cfg.azure_auth not in AZURE_AUTH_METHODS:
            raise ValueError(
                f"Unknown --azure-auth: {cfg.azure_auth!r} "
                f"(expected {'|'.join(AZURE_AUTH_METHODS)})."
            )
        if cfg.azure_key_name_template:
            try:  # fail early on bad placeholders, before any login
                cfg.azure_key_name_template.format(upn="u", upn_local="u", oid="o")
            except (KeyError, IndexError) as exc:
                raise ValueError(
                    f"Bad --azure-key-name-template "
                    f"{cfg.azure_key_name_template!r}: unknown placeholder "
                    f"{exc} (available: {{upn}}, {{upn_local}}, {{oid}})."
                ) from None
        # Internal-CA anchors: the spec requires them for b-lt/b-lta; they
        # are equally needed to TRUST the user's chain during the >= b-t
        # self-verification, so require them there too rather than letting
        # every run fail at verification time (recorded design choice).
        needs_anchors = level_needs_ltv(cfg.pades_level) or (
            cfg.verify and level_needs_timestamp(cfg.pades_level)
        )
        if needs_anchors and not cfg.azure_trust_anchors:
            raise ValueError(
                "azure mode needs the internal CA chain for "
                f"{cfg.pades_level} (LTV/verification): pass "
                f"--azure-trust-anchors or set {ENV_AZURE_TRUST_ANCHORS} "
                "(PEM/DER file or directory). The EU trusted list is NOT "
                "used in azure mode."
            )
        if cfg.azure_trust_anchors and not Path(cfg.azure_trust_anchors).exists():
            raise ValueError(
                f"Trust anchors not found: {cfg.azure_trust_anchors}"
            )
    if cfg.mode == "image":
        if not cfg.image_path:
            raise ValueError("--image-path is required in image mode.")
        if not Path(cfg.image_path).exists():
            raise ValueError(f"Image not found: {cfg.image_path}")
        if cfg.page is not None and cfg.page < 1:
            raise ValueError("--page must be >= 1.")
    if cfg.template and not Path(cfg.template).exists():
        raise ValueError(f"Template not found: {cfg.template}")


@dataclasses.dataclass
class SigningMaterial:
    """Network-bound signing collaborators, resolved once per batch."""

    timestamper: timestamps.TimeStamper | None = None
    validation_context: ValidationContext | None = None
    trust_anchors: list | None = None
    tsa_url: str | None = None


def build_signing_material(cfg: RunConfig) -> SigningMaterial:
    """Resolve the per-batch signing material for cfg's level (beid/azure).

    Levels >= b-t get an RFC 3161 ``HTTPTimeStamper`` (URL precedence:
    flag > CACHET_TSA_URL > DigiCert default). Levels b-lt/b-lta also get a
    fetching ``ValidationContext`` whose trust roots are seeded with the
    mode's anchors, so OCSP/CRL material can be gathered and embedded.

    The trust source is MODE-DEPENDENT: beid uses the EU trusted list
    (trust.py, LOTL); azure uses the organisation's internal CA chain from
    --azure-trust-anchors — never the EU LOTL.

    Either way the anchors are passed as ``extra_trust_roots`` (i.e. *in
    addition to* the system store) deliberately: when embedding validation
    info, pyHanko validates the TSA's certificate chain against this same
    context (``timestamper.validation_paths``), and the default free TSA
    chains to a public root, not to the mode's anchors. A trust_roots-only
    context would therefore make every b-lt/b-lta signature fail.

    Raises ``trust.TrustListError`` / ``ValueError`` (actionable, names the
    endpoint or file) when anchors are needed but unavailable — the level
    is NEVER silently downgraded.
    """
    material = SigningMaterial()
    if cfg.mode not in ("beid", "azure") or cfg.legacy_cms:
        return material
    if level_needs_timestamp(cfg.pades_level):
        material.tsa_url = resolve_tsa_url(cfg.timestamp_url)
        material.timestamper = timestamps.HTTPTimeStamper(url=material.tsa_url)
    # Anchors are needed to *sign* at b-lt/b-lta (revinfo gathering) and to
    # *verify* at any level >= b-t (the signer chain must be trusted, R8/R9).
    need_anchors = level_needs_ltv(cfg.pades_level) or (
        cfg.verify and level_needs_timestamp(cfg.pades_level)
    )
    if need_anchors:
        if cfg.mode == "azure":
            material.trust_anchors = load_trust_anchor_certs(cfg.azure_trust_anchors)
        else:
            import trust  # lazy: only network-bound levels need the trusted list

            material.trust_anchors = trust.get_trust_anchors(
                cfg.trust_list_url, refresh=cfg.refresh_trust_list
            )
    if level_needs_ltv(cfg.pades_level):
        material.validation_context = ValidationContext(
            extra_trust_roots=material.trust_anchors,
            allow_fetching=True,
        )
    return material


class SelfVerificationError(RuntimeError):
    """Post-signing self-verification failed (level mismatch or invalid sig)."""


def verify_signed_pdf(
    path: Path,
    expected_level: str,
    *,
    trust_anchors: list | None = None,
) -> str:
    """Re-open a signed PDF, validate it, and detect the achieved PAdES level.

    Returns a detail label such as ``"PAdES-B-LTA, LTV ok"``; raises
    ``SelfVerificationError`` when the signature does not validate or the
    achieved level is below ``expected_level`` (R8 — a mismatch must fail the
    document, never pass silently).

    Level detection is structural, on pyHanko's validation output:
    b-t = validated RFC 3161 signature timestamp present; b-lt = + DSS with
    revocation material (OCSP/CRL); b-lta = + document timestamp chain.
    The signature itself is validated with ``validate_pdf_signature`` against
    the EU-trusted-list anchors (plus the system store, mirroring signing).
    Full historical AdES-LTA re-validation is deliberately NOT run here: it
    would re-require revinfo for the (free) TSA chain at every step and can
    false-negative on perfectly good documents; the manual acceptance test
    in BUILD.md covers it with `pyhanko sign validate --pretty-print`.
    """
    with path.open("rb") as f:
        reader = PdfFileReader(f, strict=False)
        regular = reader.embedded_regular_signatures
        if not regular:
            raise SelfVerificationError("no signature found in the output file")
        emb = regular[-1]  # ours is the last one added
        subfilter = str(emb.sig_object.get("/SubFilter"))
        if subfilter != "/ETSI.CAdES.detached":
            raise SelfVerificationError(
                f"not a PAdES signature (SubFilter {subfilter})"
            )
        vc = ValidationContext(
            extra_trust_roots=trust_anchors or [], allow_fetching=True
        )
        status = validate_pdf_signature(
            emb, signer_validation_context=vc, ts_validation_context=vc
        )
        if not status.bottom_line:
            raise SelfVerificationError(
                f"signature failed validation: {status.summary()}"
            )
        ts = status.timestamp_validity
        has_timestamp = ts is not None and ts.intact and ts.valid
        try:
            dss = DocumentSecurityStore.read_dss(reader)
            has_revinfo = bool(dss.ocsps) or bool(dss.crls)
        except NoDSSFoundError:
            has_revinfo = False
        has_doc_timestamp = bool(reader.embedded_timestamp_signatures)

        detected = "b-b"
        if has_timestamp:
            detected = "b-t"
            if has_revinfo:
                detected = "b-lt"
                if has_doc_timestamp:
                    detected = "b-lta"
        if PADES_LEVELS.index(detected) < PADES_LEVELS.index(expected_level):
            raise SelfVerificationError(
                f"requested PAdES-{expected_level.upper()} but the document "
                f"only achieves PAdES-{detected.upper()}"
            )
        label = f"PAdES-{detected.upper()}"
        if level_needs_ltv(detected):
            label += ", LTV ok"
        return label


def process_batch(cfg: RunConfig, *, on_progress=None) -> list[DocResult]:
    """Validate (if a template is provided) then process each file according to
    the mode. Returns one DocResult per input file. `on_progress` is called
    after each document (useful for the GUI).

    In beid mode the PKCS#11 session, the RFC 3161 timestamper and the
    LTV ValidationContext are built ONCE here and reused for every document.
    """
    results: list[DocResult] = []
    template_dims = page_dimensions(cfg.template) if cfg.template else None

    signer = identity = None
    azure_user = None
    material = SigningMaterial()
    if cfg.mode == "beid":
        session = open_eid_session(cfg.lib or default_pkcs11_lib())
        signer = BEIDSigner(session)
        identity = read_card_identity(session)
        # May raise trust.TrustListError (clear + actionable): levels above
        # b-b require their network material — NEVER downgrade silently.
        material = build_signing_material(cfg)
    elif cfg.mode == "azure":
        # Setup ONCE per batch (R6): one interactive Entra login, one
        # key/cert resolution, one signer + timestamper + context. The SDK
        # clients refresh tokens through the cached credential as needed,
        # so long batches survive token expiry without re-prompting.
        import azure_signer as _az

        credential = _az.get_cached_credential(resolve_azure_auth(cfg.azure_auth))
        azure_user = _az.acquire_user(credential)
        key_name, cert_name, overridden = _az.resolve_key_names(
            azure_user,
            key_name=cfg.azure_key_name,
            key_name_template=cfg.azure_key_name_template,
            cert_name=cfg.azure_cert_name,
        )
        if overridden:
            # Security rule (R4/R11): an explicit key override may sign with
            # a key NOT derived from the signed-in user — make it visible.
            print(
                f"WARNING: --azure-key-name overrides the per-user key "
                f"derivation: signing with {key_name!r} as requested, "
                f"signed in as {azure_user.upn}. Key Vault access policy "
                "remains the authorization gate.",
                file=sys.stderr,
            )
        material = build_signing_material(cfg)
        signer = _az.build_azure_signer(
            cfg.azure_vault_url, credential, key_name, cert_name, cfg.digest,
            other_certs=tuple(material.trust_anchors or ()),
        )
        display = (_az.fetch_graph_display_name(credential)
                   if cfg.azure_use_graph else None)
        identity = read_cert_identity(signer.signing_cert, display_name=display)

    Path(cfg.output).mkdir(parents=True, exist_ok=True)

    # Optional position (None = unspecified). beid mode: if provided, vignette
    # placed freely; otherwise default vignette (bottom-right corner).
    # A page anchor resolves the page PER DOCUMENT (first -> 0, last -> -1),
    # which is what makes page-count mismatches against the template workable.
    placement = cfg.x is not None and cfg.y is not None
    if cfg.page_anchor:
        page_index = anchor_page_index(cfg.page_anchor)
        page_label = f"{cfg.page_anchor} page"
    else:
        page_index = (cfg.page or 1) - 1
        page_label = f"page {cfg.page or 1}"
    x = cfg.x if cfg.x is not None else 0.0
    y = cfg.y if cfg.y is not None else 0.0

    label = signature_level_label(cfg.pades_level, cfg.legacy_cms)
    mode_tag = "eID" if cfg.mode == "beid" else (
        f"Azure, {azure_user.upn}" if azure_user else "Azure"
    )
    for src in cfg.inputs:
        src = Path(src)
        if template_dims is not None:
            verdict = validate_against_template(
                template_dims, src, page_anchor=cfg.page_anchor
            )
            if not verdict.ok:
                res = DocResult(src, None, False, f"rejected — {verdict.reason}")
                results.append(res)
                if on_progress:
                    on_progress(res)
                continue
        dst = unique_output_path(cfg.output, src.stem)  # never overwrites
        try:
            if cfg.mode in ("beid", "azure"):
                sign_kwargs = dict(
                    digest=cfg.digest,
                    legacy_cms=cfg.legacy_cms,
                    timestamper=material.timestamper,
                    validation_context=material.validation_context,
                )
                if placement:
                    sign_one(signer, src, dst, f"{cfg.field}1", cfg.pades_level,
                             identity, page_index=page_index, pos=(x, y),
                             **sign_kwargs)
                    prefix = (f"signed ({mode_tag}) — vignette {page_label} "
                              f"@ ({x:.0f}, {y:.0f})")
                else:
                    # No position: default bottom-right vignette. An anchor
                    # moves it to the first/last page; None keeps the
                    # historical last-page default.
                    sign_one(signer, src, dst, f"{cfg.field}1", cfg.pades_level,
                             identity,
                             page_index=page_index if cfg.page_anchor else None,
                             **sign_kwargs)
                    prefix = f"signed ({mode_tag}) — vignette"
                    if cfg.page_anchor:
                        prefix += f" ({page_label}, bottom-right)"
                # R8: re-open + validate, report the *achieved* level. A
                # mismatch raises SelfVerificationError -> document failed.
                if (cfg.verify and not cfg.legacy_cms
                        and level_needs_timestamp(cfg.pades_level)):
                    achieved = verify_signed_pdf(
                        dst, cfg.pades_level,
                        trust_anchors=material.trust_anchors,
                    )
                    detail = f"{prefix} — {achieved}"
                else:
                    detail = f"{prefix} — {label}"
            else:
                insert_image_one(src, dst, cfg.image_path, page_index, x, y)
                detail = f"image inserted — {page_label} @ ({x:.0f}, {y:.0f})"
            res = DocResult(src, dst, True, detail)
        except SelfVerificationError as exc:
            res = DocResult(
                src, None, False,
                f"failed — self-verification: {exc} (signed file kept: {dst.name})",
            )
        except (requests.RequestException, timestamps.TimestampRequestError) as exc:
            # R10: name the endpoint, never downgrade the level silently.
            endpoints = material.tsa_url or "network endpoint"
            res = DocResult(
                src, None, False,
                f"failed — network error while signing at level {label}: {exc}. "
                f"Check the TSA ({endpoints}) and OCSP/CRL reachability, or "
                "use --pades-level b-b for offline signing.",
            )
        except Exception as exc:  # noqa: BLE001 - continue the batch
            res = DocResult(src, None, False, f"failed — {exc}")
        results.append(res)
        if on_progress:
            on_progress(res)
    return results


def print_summary(results: list[DocResult], out_dir, *, rrn_note: bool = False) -> None:
    """Print a summary table of the batch."""
    print("\n=== Summary ===")
    width = max((len(r.path.name) for r in results), default=8)
    for r in results:
        flag = "OK   " if r.ok else "FAIL "
        print(f"  [{flag}] {r.path.name:<{width}}  {r.detail}")
    ok = sum(1 for r in results if r.ok)
    print(f"\n{ok}/{len(results)} document(s) processed successfully. Output: {out_dir}")
    if rrn_note:
        # R9: repeat the privacy implication in the final summary.
        print("Note: eID signatures embed the signer's national register "
              "number (RRN); mind how the signed PDFs are distributed.")


def page_arg(value: str):
    """argparse type for --page: a 1-based page number, or "first"/"last"."""
    v = value.strip().lower()
    if v in PAGE_ANCHORS:
        return v
    try:
        return int(v)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected a page number, 'first' or 'last' (got {value!r})"
        ) from None


def describe_placement(cfg: RunConfig) -> str:
    """Human summary of where the vignette will go (CLI banner)."""
    page_phrase = (f"{cfg.page_anchor} page" if cfg.page_anchor
                   else f"page {cfg.page or 1}")
    if cfg.x is not None and cfg.y is not None:
        return f"vignette {page_phrase} @ ({cfg.x:.0f}, {cfg.y:.0f})"
    return f"vignette bottom-right, {cfg.page_anchor or 'last'} page"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Batch-sign (Belgian eID card) or stamp an image onto PDFs."
    )
    # Positionals kept for backward compatibility: "inputs… output".
    parser.add_argument(
        "inputs",
        nargs="*",
        help="(Legacy style) inputs… then output folder. Prefer --input/--output.",
    )
    parser.add_argument(
        "--gui", action="store_true", help="Launch the graphical interface (CustomTkinter)."
    )
    parser.add_argument(
        "--version", action="version", version=f"Cachet {__version__}"
    )
    parser.add_argument(
        "--template", default=None, help="Template PDF to validate the input files."
    )
    parser.add_argument(
        "--input", nargs="+", default=None, help="PDF file(s)/folder(s) to process."
    )
    parser.add_argument("--output", default=None, help="Output folder.")
    parser.add_argument(
        "--mode",
        choices=("beid", "image", "azure"),
        default="beid",
        help="Mode: beid (eID card + vignette), image (image insertion), or "
             "azure (your personal certificate in Azure Key Vault via a "
             "Microsoft Entra ID login — an advanced (AES), not qualified, "
             "signature).",
    )
    parser.add_argument(
        "--image-path", dest="image_path", default=None,
        help="Image to insert (required in --mode image).",
    )
    parser.add_argument(
        "--page", type=page_arg, default=None, metavar="N|first|last",
        help="Target page: a 1-based number, or 'first'/'last' (resolved per "
             "document). Image: insertion page. beid: vignette page. With "
             "--template, first/last also accepts files whose page count "
             "differs from the template (their first/last page must still "
             "match the template's).",
    )
    parser.add_argument(
        "--x", type=float, default=None,
        help="X position (points, from the page's lower-left corner). "
             "In beid mode, --x/--y place the vignette (otherwise: bottom-right).",
    )
    parser.add_argument(
        "--y", type=float, default=None,
        help="Y position (points, from the page's lower-left corner).",
    )
    parser.add_argument("--lib", default=None, help="Path to the eID PKCS#11 lib.")
    parser.add_argument(
        "--field", default="Signature", help="Base name of the signature field (beid mode)."
    )
    parser.add_argument(
        "--pades-level", dest="pades_level", choices=PADES_LEVELS, default=None,
        help="PAdES baseline level (beid mode): b-b = basic signature (offline); "
             "b-t = + trusted RFC 3161 timestamp; b-lt = + embedded revocation "
             "info (LTV); b-lta = + archival timestamp chain. Default: b-lta. "
             "Levels above b-b need network access (TSA; plus the EU trusted "
             "list and OCSP/CRL endpoints for b-lt/b-lta).",
    )
    parser.add_argument(
        "--pades", action="store_true",
        help="(deprecated, no-op) PAdES is now the default; use --pades-level.",
    )
    parser.add_argument(
        "--legacy-cms", dest="legacy_cms", action="store_true",
        help="(deprecated) Sign with the legacy non-PAdES CMS subfilter "
             "(adbe.pkcs7.detached): no timestamp, no LTV. Incompatible with "
             "--pades-level above b-b.",
    )
    parser.add_argument(
        "--timestamp-url", dest="timestamp_url", default=None,
        help="RFC 3161 TSA URL for levels >= b-t. Precedence: this flag > "
             f"{ENV_TSA_URL} env var > default {DEFAULT_TSA_URL}. The default "
             "free TSA yields technically valid timestamps but NOT qualified "
             "ones; point this at a qualified TSA for eIDAS-grade "
             "long-term preservation.",
    )
    parser.add_argument(
        "--trust-list-url", dest="trust_list_url", default=None,
        help="EU List of Trusted Lists (LOTL) URL seeding the LTV trust "
             "anchors for b-lt/b-lta. Precedence: this flag > CACHET_LOTL_URL "
             "env var > https://ec.europa.eu/tools/lotl/eu-lotl.xml.",
    )
    parser.add_argument(
        "--refresh-trust-list", dest="refresh_trust_list", action="store_true",
        help="Force re-download of the EU trusted list, ignoring the local cache.",
    )
    parser.add_argument(
        "--digest", choices=DIGEST_CHOICES, default="sha256",
        help="Signature digest algorithm (default: sha256).",
    )
    parser.add_argument(
        "--no-verify", dest="verify", action="store_false",
        help="Skip the post-signing self-verification (levels >= b-t). "
             "Also skips the EU trusted-list fetch at level b-t.",
    )
    azure = parser.add_argument_group(
        "azure mode", "Sign with your personal certificate in Azure Key "
        "Vault (interactive Microsoft Entra ID login, one per batch). "
        f"Each flag falls back to its CACHET_AZURE_* environment variable."
    )
    azure.add_argument(
        "--azure-vault-url", dest="azure_vault_url", default=None,
        help=f"Key Vault URL (required in azure mode; env {ENV_AZURE_VAULT_URL}).",
    )
    azure.add_argument(
        "--azure-key-name", dest="azure_key_name", default=None,
        help="Explicit Key Vault key name. OVERRIDES the per-user derivation "
             f"and is flagged in the output (env {ENV_AZURE_KEY_NAME}).",
    )
    azure.add_argument(
        "--azure-key-name-template", dest="azure_key_name_template", default=None,
        help="Template deriving the key name from the signed-in user; "
             "placeholders {upn}, {upn_local}, {oid} (sanitised for Key "
             f"Vault names). Default sig-{{upn}} (env {ENV_AZURE_KEY_TEMPLATE}).",
    )
    azure.add_argument(
        "--azure-cert-name", dest="azure_cert_name", default=None,
        help="Certificate name if it differs from the key name "
             f"(env {ENV_AZURE_CERT_NAME}).",
    )
    azure.add_argument(
        "--azure-auth", dest="azure_auth", choices=AZURE_AUTH_METHODS,
        default=None,
        help="Entra ID auth: interactive (system browser; GUI default), "
             "device-code (headless CLI default), default "
             "(DefaultAzureCredential — testing/CI only: it may select a "
             "service principal and BREAKS the per-user model). "
             f"Env {ENV_AZURE_AUTH}.",
    )
    azure.add_argument(
        "--azure-trust-anchors", dest="azure_trust_anchors", default=None,
        help="PEM/DER file or directory with the INTERNAL CA chain "
             "(root + intermediates) used as LTV trust anchors in azure "
             "mode — never the EU trusted list. Required for b-lt/b-lta "
             f"(and for self-verification at b-t). Env {ENV_AZURE_TRUST_ANCHORS}.",
    )
    azure.add_argument(
        "--azure-graph", dest="azure_use_graph", action="store_true",
        help="Use the Microsoft Graph /me displayName for the vignette "
             "instead of the certificate subject (opt-in).",
    )
    return parser


def resolve_config(args) -> RunConfig:
    """Build a RunConfig from the argparse arguments, accepting both the new
    flags (--input/--output) and the legacy positional style."""
    raw_inputs = list(args.input) if args.input else []
    output = args.output
    if not raw_inputs and args.inputs:  # backward compat: positionals
        if output is not None:
            raw_inputs = list(args.inputs)
        else:
            *raw_inputs, output = args.inputs

    # FutureWarning (not DeprecationWarning) so end users see it without -W.
    if getattr(args, "pades", False):
        warnings.warn(
            "--pades is deprecated and has no effect: PAdES is now the "
            "default (b-lta). Use --pades-level to choose the level.",
            FutureWarning, stacklevel=2,
        )
    legacy_cms = bool(getattr(args, "legacy_cms", False))
    explicit_level = getattr(args, "pades_level", None)
    if legacy_cms:
        warnings.warn(
            "--legacy-cms is deprecated; it produces a non-PAdES "
            "adbe.pkcs7.detached signature with no timestamp and no LTV.",
            FutureWarning, stacklevel=2,
        )
        # Mutually exclusive with PAdES levels above b-b; alone it implies
        # the b-b strength tier (no timestamp / LTV material is built).
        if explicit_level not in (None, "b-b"):
            raise ValueError(
                f"--legacy-cms cannot be combined with --pades-level "
                f"{explicit_level} (only b-b)."
            )
        pades_level = "b-b"
    else:
        pades_level = explicit_level or "b-lta"

    # --page accepts a 1-based number OR "first"/"last" (page_arg); a string
    # becomes the per-document page anchor, a number the fixed page.
    page = getattr(args, "page", None)
    page_anchor = None
    if isinstance(page, str):
        page_anchor, page = page, None

    cfg = RunConfig(
        inputs=collect_pdfs([str(p) for p in raw_inputs]),
        output=Path(output) if output else None,
        mode=args.mode,
        template=Path(args.template) if args.template else None,
        pades_level=pades_level,
        field=args.field,
        lib=args.lib,
        image_path=Path(args.image_path) if args.image_path else None,
        page=page,
        page_anchor=page_anchor,
        x=args.x,
        y=args.y,
        timestamp_url=getattr(args, "timestamp_url", None),
        trust_list_url=getattr(args, "trust_list_url", None),
        digest=getattr(args, "digest", "sha256"),
        verify=getattr(args, "verify", True),
        legacy_cms=legacy_cms,
        refresh_trust_list=getattr(args, "refresh_trust_list", False),
        # azure settings: flag > CACHET_AZURE_* env > default-at-use.
        azure_vault_url=(getattr(args, "azure_vault_url", None)
                         or os.environ.get(ENV_AZURE_VAULT_URL)),
        azure_key_name=(getattr(args, "azure_key_name", None)
                        or os.environ.get(ENV_AZURE_KEY_NAME)),
        azure_key_name_template=(getattr(args, "azure_key_name_template", None)
                                 or os.environ.get(ENV_AZURE_KEY_TEMPLATE)),
        azure_cert_name=(getattr(args, "azure_cert_name", None)
                         or os.environ.get(ENV_AZURE_CERT_NAME)),
        azure_auth=resolve_azure_auth(getattr(args, "azure_auth", None)),
        azure_trust_anchors=(
            Path(p) if (p := (getattr(args, "azure_trust_anchors", None)
                              or os.environ.get(ENV_AZURE_TRUST_ANCHORS)))
            else None
        ),
        azure_use_graph=getattr(args, "azure_use_graph", False),
    )
    validate_config(cfg)
    return cfg


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.gui:
        try:
            from gui import launch_gui
        except Exception as exc:  # noqa: BLE001
            print(
                f"Cannot load the GUI: {exc}\n"
                "Install CustomTkinter and Tk support:\n"
                "  pip install customtkinter\n"
                "  sudo apt install python3-tk   (or python3.14-tk)",
                file=sys.stderr,
            )
            return 1
        return launch_gui(args)

    try:
        cfg = resolve_config(args)
    except ValueError as exc:
        parser.error(str(exc))

    if cfg.mode == "beid":
        lib_path = cfg.lib or default_pkcs11_lib()
        if not Path(lib_path).exists():
            print(
                f"PKCS#11 library not found: {lib_path}\n"
                "Install the eID middleware or specify the path with --lib.",
                file=sys.stderr,
            )
            return 1
        print(f"Mode: eID — {describe_placement(cfg)}. PKCS#11 lib: {lib_path}")
        # R9: the RRN is in the signing certificate, hence in every signature.
        print(
            "WARNING: each eID signature embeds the signer's national "
            "register number (RRN) — mind how the signed PDFs are distributed.",
            file=sys.stderr,
        )
        label = signature_level_label(cfg.pades_level, cfg.legacy_cms)
        print(f"Signature level: {label} — digest {cfg.digest}")
        if not cfg.legacy_cms and level_needs_timestamp(cfg.pades_level):
            print(f"RFC 3161 TSA: {resolve_tsa_url(cfg.timestamp_url)}")
        if not cfg.legacy_cms and level_needs_ltv(cfg.pades_level):
            from trust import resolve_lotl_url

            print(f"LTV trust anchors: EU trusted list ({resolve_lotl_url(cfg.trust_list_url)})")
        print(f"{len(cfg.inputs)} PDF(s). The PIN will be requested for each document.")
    elif cfg.mode == "azure":
        print(f"Mode: Azure Key Vault — {describe_placement(cfg)}. "
              f"Vault: {cfg.azure_vault_url}")
        # R11: AES, not QES — and the signature carries the user's identity.
        print(
            "Note: signs with YOUR personal Key Vault certificate — an "
            "advanced electronic signature (AES), not a qualified one (QES); "
            "the signature carries your name/identity.",
        )
        print(f"Signature level: {signature_level_label(cfg.pades_level)} "
              f"— digest {cfg.digest}")
        if level_needs_timestamp(cfg.pades_level):
            print(f"RFC 3161 TSA: {resolve_tsa_url(cfg.timestamp_url)}")
        if cfg.azure_trust_anchors:
            print(f"LTV trust anchors: internal CA ({cfg.azure_trust_anchors})")
        print(f"{len(cfg.inputs)} PDF(s). One Microsoft sign-in for the whole "
              f"batch ({resolve_azure_auth(cfg.azure_auth)}).")
    else:
        page_phrase = (f"{cfg.page_anchor} page" if cfg.page_anchor
                       else f"page {cfg.page or 1}")
        print(
            f"Mode: image — {cfg.image_path} "
            f"({page_phrase}, x={cfg.x or 0:.0f}, y={cfg.y or 0:.0f})."
        )
        print(f"{len(cfg.inputs)} PDF(s).")
    if cfg.template:
        print(f"Validation against the template: {cfg.template}")
        if cfg.page_anchor:
            print(f"  Files whose page count differs from the template are "
                  f"accepted and signed on their {cfg.page_anchor} page.")
    print()

    def progress(r: DocResult) -> None:
        print(f"  [{'OK' if r.ok else 'FAIL'}] {r.path.name} — {r.detail}")

    try:
        results = process_batch(cfg, on_progress=progress)
    except RuntimeError as exc:
        # Batch-level setup failures (trust.TrustListError, azure
        # AzureSigningError, …): actionable message, no traceback, and the
        # level is NEVER silently downgraded.
        print(f"\nFATAL: {exc}", file=sys.stderr)
        return 1
    print_summary(results, cfg.output, rrn_note=(cfg.mode == "beid"))
    return 0 if results and all(r.ok for r in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
