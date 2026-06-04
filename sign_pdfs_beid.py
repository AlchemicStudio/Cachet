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


def _last_page_mediabox(writer) -> list[float]:
    """MediaBox of the last page (with inheritance from the page tree)."""
    return _page_mediabox(writer, -1)


def _last_page_box(writer) -> tuple[float, float, float, float]:
    """Vignette rectangle, anchored bottom-right of the last page."""
    mb = _last_page_mediabox(writer)
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
ENV_TSA_URL = "SIGNAPP_TSA_URL"


def resolve_tsa_url(explicit: str | None = None) -> str:
    """TSA URL precedence: --timestamp-url flag > SIGNAPP_TSA_URL > default."""
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
    signer: BEIDSigner,
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
    """Sign a PDF (incremental signature) and stamp the visible vignette onto
    it (photo + "Signed by ...").

    ``pades_level`` selects the PAdES baseline level (see
    ``signature_meta_kwargs``); ``timestamper`` must be provided for levels
    >= b-t and ``validation_context`` for b-lt/b-lta (both are built once per
    batch by ``build_signing_material``).

    By default (``pos`` None) the vignette is anchored bottom-right of the LAST
    page (historical behavior). If ``pos`` is provided, it is placed on page
    ``page_index`` (0-based), lower-left corner at ``pos`` points, in a 3:1 box
    one-fifth as wide as the page (cf. ``vignette_size_pt``).
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
            on_page, box = -1, _last_page_box(writer)
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
    template_dims: list[tuple[float, float]], pdf_path
) -> ValidationResult:
    """Check that a PDF has the SAME page count AND per-page dimensions
    EXACTLY identical to the template (no tolerance)."""
    path = Path(pdf_path)
    try:
        dims = page_dimensions(path)
    except Exception as exc:  # noqa: BLE001
        return ValidationResult(path, False, f"unreadable ({exc})")
    if len(dims) != len(template_dims):
        return ValidationResult(
            path, False, f"{len(dims)} page(s), the template has {len(template_dims)}"
        )
    for i, (d, t) in enumerate(zip(dims, template_dims), start=1):
        if d != t:
            return ValidationResult(
                path,
                False,
                f"page {i}: {d[0]:.2f}×{d[1]:.2f} pt ≠ template {t[0]:.2f}×{t[1]:.2f} pt",
            )
    return ValidationResult(path, True, "")


def validate_files(template_path, pdf_paths) -> list[ValidationResult]:
    """Validate a list of PDFs against the template (read once)."""
    template_dims = page_dimensions(template_path)
    return [validate_against_template(template_dims, p) for p in pdf_paths]


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
    timestamp_url: str | None = None   # None -> SIGNAPP_TSA_URL env -> DigiCert
    trust_list_url: str | None = None  # None -> SIGNAPP_LOTL_URL env -> EU LOTL
    digest: str = "sha256"             # sha256 | sha384 | sha512
    verify: bool = True                # post-signing self-verification (>= b-t)
    legacy_cms: bool = False           # deprecated adbe.pkcs7.detached path
    refresh_trust_list: bool = False   # bypass the trusted-list cache


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
    if cfg.mode not in ("beid", "image"):
        raise ValueError(f"Unknown mode: {cfg.mode!r} (expected beid|image).")
    if cfg.mode == "beid":
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
    """Resolve the per-batch signing material for cfg's level (beid mode).

    Levels >= b-t get an RFC 3161 ``HTTPTimeStamper`` (URL precedence:
    flag > SIGNAPP_TSA_URL > DigiCert default). Levels b-lt/b-lta also get a
    fetching ``ValidationContext`` whose trust roots are seeded with the EU
    trusted-list anchors, so OCSP/CRL material can be gathered and embedded.

    The anchors are passed as ``extra_trust_roots`` (i.e. *in addition to*
    the system store) deliberately: when embedding validation info, pyHanko
    validates the TSA's certificate chain against this same context
    (``timestamper.validation_paths``), and the default free TSA does not
    chain to the EU-trusted-list anchors. A trust_roots-only context would
    therefore make every b-lt/b-lta signature fail.

    Raises ``trust.TrustListError`` (actionable, names the endpoint) when the
    trusted list is needed but unavailable — the level is NEVER silently
    downgraded.
    """
    material = SigningMaterial()
    if cfg.mode != "beid" or cfg.legacy_cms:
        return material
    if level_needs_timestamp(cfg.pades_level):
        material.tsa_url = resolve_tsa_url(cfg.timestamp_url)
        material.timestamper = timestamps.HTTPTimeStamper(url=material.tsa_url)
    if level_needs_ltv(cfg.pades_level):
        import trust  # lazy: only LTV levels need the trusted-list machinery

        material.trust_anchors = trust.get_trust_anchors(
            cfg.trust_list_url, refresh=cfg.refresh_trust_list
        )
        material.validation_context = ValidationContext(
            extra_trust_roots=material.trust_anchors,
            allow_fetching=True,
        )
    return material


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
    material = SigningMaterial()
    if cfg.mode == "beid":
        session = open_eid_session(cfg.lib or default_pkcs11_lib())
        signer = BEIDSigner(session)
        identity = read_card_identity(session)
        # May raise trust.TrustListError (clear + actionable): levels above
        # b-b require their network material — NEVER downgrade silently.
        material = build_signing_material(cfg)

    Path(cfg.output).mkdir(parents=True, exist_ok=True)

    # Optional position (None = unspecified). beid mode: if provided, vignette
    # placed freely; otherwise default vignette (bottom-right, last page).
    placement = cfg.x is not None and cfg.y is not None
    page = cfg.page or 1
    x = cfg.x if cfg.x is not None else 0.0
    y = cfg.y if cfg.y is not None else 0.0

    label = signature_level_label(cfg.pades_level, cfg.legacy_cms)
    for src in cfg.inputs:
        src = Path(src)
        if template_dims is not None:
            verdict = validate_against_template(template_dims, src)
            if not verdict.ok:
                res = DocResult(src, None, False, f"rejected — {verdict.reason}")
                results.append(res)
                if on_progress:
                    on_progress(res)
                continue
        dst = unique_output_path(cfg.output, src.stem)  # never overwrites
        try:
            if cfg.mode == "beid":
                sign_kwargs = dict(
                    digest=cfg.digest,
                    legacy_cms=cfg.legacy_cms,
                    timestamper=material.timestamper,
                    validation_context=material.validation_context,
                )
                if placement:
                    sign_one(signer, src, dst, f"{cfg.field}1", cfg.pades_level,
                             identity, page_index=page - 1, pos=(x, y),
                             **sign_kwargs)
                    detail = (f"signed (eID) — vignette page {page} "
                              f"@ ({x:.0f}, {y:.0f}) — {label}")
                else:
                    sign_one(signer, src, dst, f"{cfg.field}1", cfg.pades_level,
                             identity, **sign_kwargs)
                    detail = f"signed (eID) — vignette — {label}"
            else:
                insert_image_one(src, dst, cfg.image_path, page - 1, x, y)
                detail = f"image inserted — page {page} @ ({x:.0f}, {y:.0f})"
            res = DocResult(src, dst, True, detail)
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


def print_summary(results: list[DocResult], out_dir) -> None:
    """Print a summary table of the batch."""
    print("\n=== Summary ===")
    width = max((len(r.path.name) for r in results), default=8)
    for r in results:
        flag = "OK   " if r.ok else "FAIL "
        print(f"  [{flag}] {r.path.name:<{width}}  {r.detail}")
    ok = sum(1 for r in results if r.ok)
    print(f"\n{ok}/{len(results)} document(s) processed successfully. Output: {out_dir}")


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
        "--template", default=None, help="Template PDF to validate the input files."
    )
    parser.add_argument(
        "--input", nargs="+", default=None, help="PDF file(s)/folder(s) to process."
    )
    parser.add_argument("--output", default=None, help="Output folder.")
    parser.add_argument(
        "--mode",
        choices=("beid", "image"),
        default="beid",
        help="Mode: beid (eID card + vignette) or image (image insertion).",
    )
    parser.add_argument(
        "--image-path", dest="image_path", default=None,
        help="Image to insert (required in --mode image).",
    )
    parser.add_argument(
        "--page", type=int, default=None,
        help="Target page (1-based). Image: insertion page. beid: vignette page.",
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
             "anchors for b-lt/b-lta. Precedence: this flag > SIGNAPP_LOTL_URL "
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
        help="Skip the post-signing self-verification (levels >= b-t).",
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

    cfg = RunConfig(
        inputs=collect_pdfs([str(p) for p in raw_inputs]),
        output=Path(output) if output else None,
        mode=args.mode,
        template=Path(args.template) if args.template else None,
        pades_level=pades_level,
        field=args.field,
        lib=args.lib,
        image_path=Path(args.image_path) if args.image_path else None,
        page=args.page,
        x=args.x,
        y=args.y,
        timestamp_url=getattr(args, "timestamp_url", None),
        trust_list_url=getattr(args, "trust_list_url", None),
        digest=getattr(args, "digest", "sha256"),
        verify=getattr(args, "verify", True),
        legacy_cms=legacy_cms,
        refresh_trust_list=getattr(args, "refresh_trust_list", False),
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
        placed = cfg.x is not None and cfg.y is not None
        where = (f"vignette page {cfg.page or 1} @ ({cfg.x:.0f}, {cfg.y:.0f})"
                 if placed else "vignette bottom-right, last page")
        print(f"Mode: eID — {where}. PKCS#11 lib: {lib_path}")
        label = signature_level_label(cfg.pades_level, cfg.legacy_cms)
        print(f"Signature level: {label} — digest {cfg.digest}")
        if not cfg.legacy_cms and level_needs_timestamp(cfg.pades_level):
            print(f"RFC 3161 TSA: {resolve_tsa_url(cfg.timestamp_url)}")
        if not cfg.legacy_cms and level_needs_ltv(cfg.pades_level):
            from trust import resolve_lotl_url

            print(f"LTV trust anchors: EU trusted list ({resolve_lotl_url(cfg.trust_list_url)})")
        print(f"{len(cfg.inputs)} PDF(s). The PIN will be requested for each document.")
    else:
        print(
            f"Mode: image — {cfg.image_path} "
            f"(page {cfg.page or 1}, x={cfg.x or 0:.0f}, y={cfg.y or 0:.0f})."
        )
        print(f"{len(cfg.inputs)} PDF(s).")
    if cfg.template:
        print(f"Validation against the template: {cfg.template}")
    print()

    def progress(r: DocResult) -> None:
        print(f"  [{'OK' if r.ok else 'FAIL'}] {r.path.name} — {r.detail}")

    results = process_batch(cfg, on_progress=progress)
    print_summary(results, cfg.output)
    return 0 if results and all(r.ok for r in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
