"""Visual signatures ("stamps") for Cachet: model, fonts, rendering, JSON, PDF.

A visual signature is a text or an image stamped onto one or more pages. It
is NOT a cryptographic signature: in beid/azure mode the stamps are applied
into the same incremental writer BEFORE the document is signed; in image mode
they are the whole job.

Design notes (deliberate choices):

- A text is a raster. It is rendered with Pillow into a tightly cropped RGBA
  image at 300 dpi (flat colour, anti-aliased alpha mask) and then goes
  through exactly the same pipeline as an image (one image XObject painted
  at its exact float size, no border). No font is embedded, no shaping
  library needed.
- The text layout is pinned to ``ImageFont.Layout.BASIC`` so sizes do not
  depend on raqm being installed (identical on every OS and in CI).
- Memory is bounded: a text (or a freehand drawing) is measured first and
  refused above ``MAX_TEXT_PIXELS`` before anything is allocated; an image is
  downsampled to ``IMAGE_MAX_DPI`` of its target width before it is embedded.
- ``stamp_content`` is THE single source of pixels + size for both the PDF
  and the GUI preview, so what is previewed is what gets stamped.
- Every failure raises ``StampError`` (a ``ValueError``): messages are
  English, lower-case first word, no trailing period.

This module must stay importable without tkinter, customtkinter, pypdfium2,
the GUI or the core (``sign_pdfs_beid`` is the CLI entry script: importing it
from here would load it twice).
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import math
import os
import re
import sys
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from pyhanko.pdf_utils import generic, images
from pyhanko.sign.fields import enumerate_sig_fields


class StampError(ValueError):
    """Any failure of this module (bad stamp, font, image, JSON, page target)."""


KINDS = ("text", "image")
PAGE_ANCHORS = ("first", "last")
DEFAULT_FONT = "great-vibes"
DEFAULT_COLOR = "#1A2B8C"
DEFAULT_FONT_SIZE = 24.0
DEFAULT_IMAGE_WIDTH_PT = 150.0
TEXT_DPI = 300
MAX_FONT_SIZE = 400.0
MAX_WIDTH_PT = 5000.0
MAX_TEXT_LEN = 2000
MAX_TEXT_PIXELS = 40_000_000            # pixel budget of one rendered text (before cropping)
IMAGE_MAX_DPI = 600                     # image stamps are downsampled to this, for their width_pt
DATE_FORMAT = "%d/%m/%Y"
BUNDLED_FONTS = {                       # id -> (display name, file name); dict order = menu order
    "great-vibes":       ("Great Vibes",       "GreatVibes-Regular.ttf"),
    "dancing-script":    ("Dancing Script",    "DancingScript.ttf"),
    "caveat":            ("Caveat",            "Caveat.ttf"),
    "sacramento":        ("Sacramento",        "Sacramento-Regular.ttf"),
    "lato":              ("Lato",              "Lato-Regular.ttf"),
    "libre-baskerville": ("Libre Baskerville", "LibreBaskerville.ttf"),
}

# Image modes pyHanko embeds as they are; anything else is converted to RGBA.
_NATIVE_IMAGE_MODES = ("RGB", "RGBA", "L", "LA")


# ---------------------------------------------------------------------------
# Fonts and assets
# ---------------------------------------------------------------------------

def _expanduser(path: Path) -> Path:
    """``~`` expanded; a ``~user`` that does not exist is left as written
    (pathlib raises ``RuntimeError`` for it)."""
    try:
        return path.expanduser()
    except RuntimeError:
        return path


def _is_file(path) -> bool:
    """``Path.is_file`` that never raises (over-long names, embedded NULs)."""
    try:
        return Path(path).is_file()
    except (OSError, ValueError):
        return False


def asset_path(name: str) -> Path:
    """Path of a bundled asset: next to this module in a checkout, under
    ``sys._MEIPASS`` in the frozen binaries (GUI and CLI alike)."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / name


def font_choices() -> list[tuple[str, str]]:
    """``[(id, display name), ...]`` of the bundled fonts, in menu order."""
    return [(font_id, name) for font_id, (name, _) in BUNDLED_FONTS.items()]


def resolve_font(font: str) -> Path:
    """Font file of a bundled id (case-insensitive) or of a user ``.ttf/.otf``
    path. The result is an existing file."""
    font_id = font.strip().lower()
    if font_id in BUNDLED_FONTS:
        path = asset_path("fonts") / BUNDLED_FONTS[font_id][1]
        if not _is_file(path):
            raise StampError(f"bundled font file missing: {path}")
        return path
    path = _expanduser(Path(font))
    if not _is_file(path):
        raise StampError(
            f"unknown font {font!r} (expected one of {', '.join(BUNDLED_FONTS)}, "
            "or the path of a .ttf/.otf file)")
    return path


# ---------------------------------------------------------------------------
# Text and stroke rendering
# ---------------------------------------------------------------------------

def _normalise_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def resolve_text(text: str, *, filename: str = "", date: datetime.date | None = None) -> str:
    """Normalise the newlines and replace the two placeholders: ``{date}``
    (dd/mm/YYYY) and ``{filename}``. Plain ``str.replace`` — never
    ``str.format`` — so every other brace is left untouched. ``{date}`` goes
    first: a file name that itself contains ``{date}`` is not re-expanded."""
    day = (date or datetime.date.today()).strftime(DATE_FORMAT)
    return _normalise_newlines(text).replace("{date}", day).replace("{filename}", filename)


def _parse_color(color: str) -> tuple[int, int, int]:
    m = re.fullmatch(r"#([0-9A-Fa-f]{6})", color) if isinstance(color, str) else None
    if m is None:
        raise StampError(f"bad color {color!r} (expected #RRGGBB)")
    return tuple(int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4))


def _ink_image(mask: Image.Image, rgb: tuple[int, int, int]) -> Image.Image:
    """RGBA image whose RGB plane is the flat colour and whose alpha is the
    (anti-aliased) ink mask."""
    img = Image.new("RGBA", mask.size, rgb + (0,))
    img.putalpha(mask)
    return img


def text_layout(text: str, font: str, font_size: float) -> tuple:
    """Measure a text WITHOUT rasterising it and enforce the pixel budget.

    Returns ``(font_obj, spacing, (l, t), pad, (w_px, h_px))``. ``text`` must
    already be newline-normalised. Shared by ``render_text_image`` and
    ``validate_stamp``."""
    px = max(1, round(font_size * TEXT_DPI / 72))
    spacing = round(px * 0.2)
    # Measuring is inside the try as well: FreeType opens a damaged font file
    # and only fails (OSError) on the first glyph it has to load.
    try:
        fnt = ImageFont.truetype(str(resolve_font(font)), px, layout_engine=ImageFont.Layout.BASIC)
        # A 1x1 probe: measuring allocates nothing proportional to the text.
        probe = ImageDraw.Draw(Image.new("L", (1, 1)))
        l, t, r, b = probe.multiline_textbbox((0, 0), text, font=fnt, spacing=spacing)
    except OSError as exc:
        raise StampError(f"cannot load font {font!r}: {exc}") from None
    pad = max(2, round(px * 0.25))
    size = (int(r - l) + 2 * pad, int(b - t) + 2 * pad)
    if size[0] * size[1] > MAX_TEXT_PIXELS:              # BEFORE any allocation
        raise StampError("text is too large to render (reduce the font size or the text length)")
    return fnt, spacing, (l, t), pad, size


def render_text_image(text: str, font: str, font_size: float, color: str) -> Image.Image:
    """Render a (multi-line) text into a tightly cropped RGBA image: flat
    colour, anti-aliased alpha. Never allocates more than ``MAX_TEXT_PIXELS``
    mask pixels (the uncropped mask is the only large allocation)."""
    rgb = _parse_color(color)
    text = _normalise_newlines(text)
    fnt, spacing, (l, t), pad, size = text_layout(text, font, font_size)
    mask = Image.new("L", size, 0)
    try:
        ImageDraw.Draw(mask).multiline_text(
            (pad - l, pad - t), text, font=fnt, fill=255, spacing=spacing)
    except OSError as exc:                               # FreeType failing on a glyph
        raise StampError(f"cannot load font {font!r}: {exc}") from None
    bbox = mask.getbbox()
    if bbox is None:
        raise StampError("text is empty")
    return _ink_image(mask.crop(bbox), rgb)


def render_strokes_image(strokes, color: str, *, width: float = 3.0, scale: int = 4) -> Image.Image:
    """Rasterise freehand strokes into a tightly cropped transparent RGBA
    image, ``scale`` times the canvas size (supersampling).

    ``strokes``: a list of strokes, each a list of ``(x, y)`` floats in canvas
    pixels. Every point also gets a filled disc of diameter ``width``: round
    caps, and a one-point stroke draws a dot."""
    rgb = _parse_color(color)
    points = [(float(x), float(y)) for stroke in strokes for x, y in stroke]
    if not points:
        raise StampError("nothing was drawn")
    # Origin moved to the strokes' own bounding box (plus the pen width), so
    # points dragged outside the canvas (negative coordinates) are kept.
    x0 = min(x for x, _ in points) - width
    y0 = min(y for _, y in points) - width
    w = (max(x for x, _ in points) + width - x0) * scale
    h = (max(y for _, y in points) + width - y0) * scale
    # Same budget as a text, checked BEFORE the allocation: the mask covers
    # the whole bounding box, and a drag can leave the canvas by a screen.
    if not w * h <= MAX_TEXT_PIXELS:                     # (also true for NaN / inf)
        raise StampError("drawing is too large")
    mask = Image.new("L", (max(1, math.ceil(w)), max(1, math.ceil(h))), 0)
    draw = ImageDraw.Draw(mask)
    pen = max(1, round(width * scale))
    radius = width * scale / 2
    for stroke in strokes:
        scaled = [((float(x) - x0) * scale, (float(y) - y0) * scale) for x, y in stroke]
        if len(scaled) > 1:
            draw.line(scaled, fill=255, width=pen, joint="curve")
        for cx, cy in scaled:
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=255)
    return _ink_image(mask.crop(mask.getbbox()), rgb)


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

def load_image(path) -> Image.Image:
    """Load an image, normalised once: EXIF orientation applied, any mode
    pyHanko cannot embed (palette, CMYK, 1-bit, …) converted to RGBA. The
    file handle is closed on return."""
    try:
        with Image.open(path) as im:
            im.load()
            out = ImageOps.exif_transpose(im)
            if out.mode not in _NATIVE_IMAGE_MODES:
                return out.convert("RGBA")
            return out.copy()
    # Deliberately broad: on a damaged file Pillow's decoders raise far more
    # than OSError / ValueError / DecompressionBombError — SyntaxError (broken
    # PNG chunk, bad EXIF block), NotImplementedError (exotic DDS),
    # MemoryError (absurd declared size). All mean "not a usable image".
    except Exception as exc:
        # The text of these two repeats the path: keep the reason alone.
        if isinstance(exc, UnidentifiedImageError):
            reason = "not a recognised image file"
        else:
            reason = getattr(exc, "strerror", None) or exc   # OSError: its reason
        raise StampError(f"cannot read image {path}: {reason}") from None


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

def _unknown_kind(kind) -> StampError:
    return StampError(f"unknown kind {kind!r} (expected {'|'.join(KINDS)})")


def _unknown_anchor(anchor) -> StampError:
    return StampError(f"unknown page_anchor {anchor!r} (expected {'|'.join(PAGE_ANCHORS)})")


@dataclasses.dataclass(frozen=True)
class Stamp:
    """One visual signature: its content and (once placed) where it goes.

    The field order is part of the contract."""
    kind: str = "image"                       # "text" | "image"
    text: str = ""
    font: str = DEFAULT_FONT                  # bundled id or path
    color: str = DEFAULT_COLOR                # "#RRGGBB"
    font_size: float = DEFAULT_FONT_SIZE      # pt (text)
    image_path: Path | None = None            # (image)
    width_pt: float = DEFAULT_IMAGE_WIDTH_PT  # pt (image)
    page: int | None = None                   # 1-based          } exactly one of the three
    page_anchor: str | None = None            # "first" | "last" } once the stamp is placed
    all_pages: bool = False                   #                  }
    x: float | None = None
    y: float | None = None

    def _target_count(self) -> int:
        return (self.page is not None) + (self.page_anchor is not None) + bool(self.all_pages)

    @property
    def is_placed(self) -> bool:
        """A position and exactly one page target."""
        return self.x is not None and self.y is not None and self._target_count() == 1

    def page_indexes(self, n_pages: int) -> list[int]:
        """0-based indexes of the pages this stamp lands on, for a document
        of ``n_pages`` pages."""
        if n_pages < 1:
            raise StampError("the document has no page")
        if self.all_pages:
            return list(range(n_pages))
        if self.page_anchor is not None:
            if self.page_anchor not in PAGE_ANCHORS:
                raise _unknown_anchor(self.page_anchor)
            return [0 if self.page_anchor == "first" else n_pages - 1]
        if self.page is None:
            raise StampError("the signature has no page target")
        if not 1 <= self.page <= n_pages:
            raise StampError(
                f"page {self.page} out of range (the document has {n_pages} page(s))")
        return [self.page - 1]

    def target_label(self) -> str:
        if self.all_pages:
            return "all pages"
        if self.page_anchor is not None:
            return f"{self.page_anchor} page"
        if self.page is not None:
            return f"page {self.page}"
        return "unplaced"

    def describe(self) -> str:
        """One-line summary, e.g. ``text last page @ (360, 120)``."""
        if self.x is None or self.y is None:
            return f"{self.kind} unplaced"
        return f"{self.kind} {self.target_label()} @ ({self.x:.0f}, {self.y:.0f})"

    def content_label(self) -> str:
        """Short label of the content: the first non-empty line of a text (in
        double quotes, truncated to 30 characters), or the image file name."""
        if self.kind == "text":
            first = next((ln.strip() for ln in self.text.splitlines() if ln.strip()), "")
            if len(first) > 30:
                first = first[:30] + "…"
            return f'"{first}"'
        return Path(self.image_path).name if self.image_path else "?"

    def to_dict(self, *, base_dir=None) -> dict:
        """JSON form (the ``--signatures`` schema). Only the keys of the kind
        and the placement keys that are set are emitted. An image that is a
        DIRECT child of ``base_dir`` is written as its bare file name, any
        other one as an absolute path."""
        out: dict = {"kind": self.kind}
        if self.kind == "text":
            out.update(text=self.text, font=self.font, color=self.color,
                       font_size=self.font_size)
        else:
            if self.image_path is not None:
                out["image_path"] = self._image_path_for(base_dir)
            out["width_pt"] = self.width_pt
        if self.page is not None:
            out["page"] = self.page
        if self.page_anchor is not None:
            out["page_anchor"] = self.page_anchor
        if self.all_pages:
            out["all_pages"] = True
        if self.x is not None:
            out["x"] = self.x
        if self.y is not None:
            out["y"] = self.y
        return out

    def _image_path_for(self, base_dir) -> str:
        if base_dir is not None:
            # RESOLVED paths only: pathlib's relative_to/is_relative_to are
            # lexical, so base_dir/../../x would count as "inside" base_dir.
            # ValueError: a NUL or a lone surrogate in the path (hand-edited
            # profile) — such a file is nowhere, so it is not in base_dir.
            try:
                resolved = Path(self.image_path).resolve()
                if resolved.parent == Path(base_dir).resolve():
                    return resolved.name
            except (OSError, RuntimeError, ValueError):
                pass
        return os.path.abspath(self.image_path)

    @classmethod
    def from_dict(cls, data, *, base_dir=None) -> "Stamp":
        """Type-check and build a stamp from its JSON form. Does NOT call
        ``validate_stamp``. Unknown keys are ignored, missing keys take the
        defaults. A relative ``image_path`` (or font path) is joined to
        ``base_dir`` as written — the result may point outside ``base_dir``
        and nothing here treats it as owned by that directory."""
        if not isinstance(data, dict):
            raise StampError("expected an object")
        if "kind" not in data:
            raise StampError("missing 'kind'")

        def string(key):
            value = data[key]
            if not isinstance(value, str):
                raise StampError(f"'{key}' must be a string")
            return value

        def number(key):
            value = data[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise StampError(f"'{key}' must be a number")
            try:
                return float(value)
            except OverflowError:                # an integer too large for a float
                raise StampError(f"'{key}' must be a number") from None

        def under_base(path: Path) -> Path:
            path = _expanduser(path)
            if base_dir is not None and not path.is_absolute():
                return Path(base_dir) / path
            return path

        fields: dict = {"kind": string("kind")}
        for key in ("text", "color", "page_anchor"):
            if key in data:
                fields[key] = string(key)
        if "font" in data:
            font = string("font")
            # A bundled id stays an id; so does any font when there is no
            # base_dir (resolve_font expands and checks the path later). A
            # bare word that is no file there stays as typed too: it is a
            # mistyped id, to be reported as such, not as a path.
            if (base_dir is not None and font.strip()
                    and font.strip().lower() not in BUNDLED_FONTS):
                joined = under_base(Path(font))
                bare_word = Path(font).name == font and not Path(font).suffix
                if not bare_word or _is_file(joined):
                    font = str(joined)
            fields["font"] = font
        if "image_path" in data:
            fields["image_path"] = under_base(Path(string("image_path")))
        for key in ("font_size", "width_pt", "x", "y"):
            if key in data:
                fields[key] = number(key)
        if "page" in data:
            page = data["page"]
            if isinstance(page, bool) or not isinstance(page, int):
                raise StampError("'page' must be an integer")
            fields["page"] = page
        if "all_pages" in data:
            if not isinstance(data["all_pages"], bool):
                raise StampError("'all_pages' must be true or false")
            fields["all_pages"] = data["all_pages"]
        return cls(**fields)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _in_range(value, maximum: float) -> bool:
    return math.isfinite(value) and 0 < value <= maximum


def _validate_content(stamp: Stamp) -> None:
    if stamp.kind not in KINDS:
        raise _unknown_kind(stamp.kind)
    if stamp.kind == "text":
        if not stamp.text.strip():
            raise StampError("text is empty")
        if len(stamp.text) > MAX_TEXT_LEN:
            raise StampError(f"text is too long (max {MAX_TEXT_LEN} characters)")
        _parse_color(stamp.color)
        if not _in_range(stamp.font_size, MAX_FONT_SIZE):
            raise StampError(
                f"font_size must be greater than 0 and at most {MAX_FONT_SIZE:g}")
        # Probe only (font + pixel budget): nothing is rasterised here.
        text_layout(_normalise_newlines(stamp.text), stamp.font, stamp.font_size)
    else:
        if stamp.image_path is None:
            raise StampError("image_path is required")
        if not _is_file(stamp.image_path):
            raise StampError(f"image not found: {stamp.image_path}")
        if not _in_range(stamp.width_pt, MAX_WIDTH_PT):
            raise StampError(
                f"width_pt must be greater than 0 and at most {MAX_WIDTH_PT:g}")


def validate_placement(stamp: Stamp) -> None:
    """Placement rules alone (page target + position), whatever the content.

    Lets a caller judge a saved placement even when the content is
    temporarily unusable (image or font file missing)."""
    if stamp._target_count() != 1:
        raise StampError("give exactly one page target: page, page_anchor or all_pages")
    if stamp.page is not None and stamp.page < 1:
        raise StampError("page must be an integer >= 1")
    if stamp.page_anchor is not None and stamp.page_anchor not in PAGE_ANCHORS:
        raise _unknown_anchor(stamp.page_anchor)
    if stamp.x is None or stamp.y is None:
        raise StampError("x and y are required")
    if not (math.isfinite(stamp.x) and math.isfinite(stamp.y)):
        raise StampError("x and y must be finite numbers")


def validate_stamp(stamp: Stamp, *, placed: bool = True) -> None:
    """Raise ``StampError`` on the first broken rule: content first (kind,
    text/colour/size/font/pixel budget or image/width), then — unless
    ``placed=False`` (a library entry not placed yet) — the placement."""
    _validate_content(stamp)
    if placed:
        validate_placement(stamp)


# ---------------------------------------------------------------------------
# Content (pixels + size) and JSON file
# ---------------------------------------------------------------------------

def content_key(stamp: Stamp, *, filename: str = "", date: datetime.date | None = None) -> tuple:
    """Hashable identity of a stamp's rendered content (placement excluded)."""
    if stamp.kind == "text":
        return ("text", resolve_text(stamp.text, filename=filename, date=date),
                stamp.font, float(stamp.font_size), stamp.color.upper())
    return ("image", str(stamp.image_path), float(stamp.width_pt))


def stamp_content(stamp: Stamp, *, filename: str = "", date: datetime.date | None = None,
                  cache: dict | None = None) -> tuple[Image.Image, tuple[float, float]]:
    """Pixels and size in points of a stamp — THE single source for both the
    PDF and the GUI preview.

    ``cache`` (a plain dict owned by the caller) is read and written under
    ``content_key(...)``, except that a text using ``{filename}`` is never
    written to it: one entry per document would grow without bound over a
    batch."""
    if stamp.kind not in KINDS:
        raise _unknown_kind(stamp.kind)
    if stamp.kind == "image" and stamp.image_path is None:
        raise StampError("image_path is required")
    key = content_key(stamp, filename=filename, date=date)
    if cache is not None and key in cache:
        return cache[key]
    if stamp.kind == "text":
        img = render_text_image(key[1], stamp.font, stamp.font_size, stamp.color)
        size = (img.width * 72 / TEXT_DPI, img.height * 72 / TEXT_DPI)
        cacheable = "{filename}" not in stamp.text
    else:
        img = load_image(stamp.image_path)
        # Size from the aspect of the full-resolution image, then cap the
        # embedded bitmap: a 12 MP photo stamped 150 pt wide is 1250 px.
        size = (float(stamp.width_pt), stamp.width_pt * img.height / img.width)
        max_w = max(1, math.ceil(stamp.width_pt * IMAGE_MAX_DPI / 72))
        if img.width > max_w:
            img = img.resize(
                (max_w, max(1, round(img.height * max_w / img.width))), Image.LANCZOS)
        cacheable = True
    if cache is not None and cacheable:
        cache[key] = (img, size)
    return img, size


def load_stamps_file(path) -> list[Stamp]:
    """Read a ``--signatures`` file: a UTF-8 JSON list of visual signatures
    (same schema as the profile's entries). Entries with ``"enabled": false``
    are skipped; every other one is validated (placement included)."""
    # Encoding pinned (never the platform default); utf-8-sig also swallows
    # the BOM that PowerShell 5 writes.
    try:
        raw = Path(path).read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:                           # a ValueError, not an OSError
        raise StampError(f"{path}: not valid UTF-8") from None
    except OSError as exc:
        raise StampError(f"cannot read signatures file {path}: {exc}") from None
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as exc:          # deep nesting -> RecursionError
        raise StampError(f"invalid JSON in {path}: {exc}") from None
    if not isinstance(data, list):
        raise StampError(f"{path}: expected a JSON list of signatures")
    name = Path(path).name
    base_dir = Path(path).resolve().parent
    stamp_list: list[Stamp] = []
    for i, entry in enumerate(data, 1):
        try:
            if isinstance(entry, dict) and "enabled" in entry:
                if not isinstance(entry["enabled"], bool):
                    raise StampError("'enabled' must be true or false")
                if not entry["enabled"]:
                    continue
            stamp = Stamp.from_dict(entry, base_dir=base_dir)
            validate_stamp(stamp)
        except StampError as exc:
            raise StampError(f"{name}: signature #{i}: {exc}") from None
        stamp_list.append(stamp)
    return stamp_list


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def page_count(writer) -> int:
    """Number of pages of the document behind ``writer`` (does not dirty it)."""
    return int(writer.root["/Pages"]["/Count"])


def page_mediabox(writer, page_index: int) -> list[float]:
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
    return [0.0, 0.0, 595.276, 841.89]


def _pdf_number(value: float) -> bytes:
    """A number for a content stream: fixed notation (``%g`` may emit an
    exponent, which PDF does not allow), without trailing zeros."""
    return (b"%.4f" % value).rstrip(b"0").rstrip(b".")


def existing_signature_count(writer) -> int:
    """Signatures and document timestamps the INPUT already carries
    (0 = unsigned). Reads the previous revision only: it neither parses the
    CMS nor dirties the writer."""
    return sum(1 for _ in enumerate_sig_fields(writer.prev, filled_status=True))


def apply_stamps(writer, stamp_list, *, filename: str = "",
                 date: datetime.date | None = None, cache: dict | None = None) -> None:
    """Stamp every element of ``stamp_list`` into ``writer`` (and nothing
    else: the caller writes or signs it).

    Two passes: pages and content are resolved for ALL stamps before the
    first write, so a bad stamp never leaves a half-stamped writer for the
    caller to sign. A low-level primitive: it stamps whatever it is given,
    including an already signed document (see ``existing_signature_count``)."""
    day = date or datetime.date.today()
    n = page_count(writer)
    resolved = []
    for stamp in stamp_list:
        if not stamp.is_placed:
            raise StampError("the signature is not placed")
        pages = stamp.page_indexes(n)                    # range errors BEFORE touching the writer
        img, size = stamp_content(stamp, filename=filename, date=day, cache=cache)
        resolved.append((stamp, pages, img, size))

    def add_content(page_index: int, data: bytes, **kwargs) -> None:
        writer.add_stream_to_page(
            page_index, writer.add_object(generic.StreamObject(stream_data=data)), **kwargs)

    owned: set[int] = set()                              # pages whose /Contents is theirs alone
    for stamp, pages, img, (w, h) in resolved:
        # ONE image XObject per stamp, painted on N pages (alpha -> /SMask by
        # pyHanko). The image is painted directly, not through a pyHanko
        # stamp: an image fills the unit square, so the matrix carries the
        # exact float size, whereas a stamp box is truncated to whole points
        # (and cannot be under 1 pt).
        image_ref = images.pil_image(img, writer)
        for p in pages:
            page_ref, _ = writer.find_page_for_modification(p)
            page_obj = page_ref.get_object()
            if "/Contents" not in page_obj:              # blank page: /Contents is optional
                page_obj["/Contents"] = writer.add_object(generic.StreamObject(stream_data=b""))
            elif p not in owned:
                contents = page_obj.raw_get("/Contents")
                if (isinstance(contents, generic.IndirectObject)
                        and isinstance(contents.get_object(), generic.ArrayObject)):
                    # pyHanko appends to an indirect array in place, and
                    # another page may point at the same one: give this page
                    # its own copy, or the stamp would land on that page too.
                    page_obj["/Contents"] = generic.ArrayObject(contents.get_object())
            owned.add(p)
            # Always: a /Contents or /Resources that pyHanko sets on the page
            # is not written unless the page itself is marked (the stamp
            # would be invisible).
            writer.mark_update(page_ref)
            mb = page_mediabox(writer, p)                # per-page origin
            # A fresh name per page: several pages may share one /Resources.
            name = "/Stamp" + uuid.uuid4().hex
            # The existing content is wrapped in q/Q so that a transformation
            # it leaves behind cannot move or scale the stamp.
            add_content(p, b"q", prepend=True)
            add_content(p, b"Q")
            add_content(
                p,
                b"q %s 0 0 %s %s %s cm %s Do Q" % (
                    _pdf_number(w), _pdf_number(h),
                    _pdf_number(mb[0] + stamp.x), _pdf_number(mb[1] + stamp.y),
                    name.encode("ascii")),
                resources=generic.DictionaryObject({
                    generic.pdf_name("/XObject"): generic.DictionaryObject(
                        {generic.pdf_name(name): image_ref})}))
