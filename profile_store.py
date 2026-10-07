"""User profile and signature image store for the Cachet GUI.

What is persisted, in the user folder, so nothing has to be reconfigured at
the next start:

- ``<config dir>/profile.json`` — the signature library (content + last
  placement of each visual signature) and the wizard settings (language,
  mode, PAdES level, output folder, azure vault URL / auth method /
  trust-anchors path, beID vignette placement);
- ``<data dir>/signatures/`` — a copy of every imported or drawn image, under
  a content-addressed name, so a moved original does not break a signature.

NEVER persisted: PIN, tokens, any credential, the azure key name, the
signed-in UPN, the template, the input files. Everything is stored
UNENCRYPTED.

Design notes (deliberate choices):

- Directories: explicit argument > ``CACHET_CONFIG_DIR`` / ``CACHET_DATA_DIR``
  > ``platformdirs``. Tests must always pass explicit temp dirs (or set the
  env vars) — never touch the real user profile.
- ``load()`` never raises: a missing file gives defaults, a corrupt or
  other-version file is moved aside as ``profile.json.bak`` (so a later save
  cannot destroy it silently), a bad entry is skipped and the others kept.
  A profile that could neither be read nor moved aside stays in place, and
  ``save()`` then refuses to overwrite it until a later ``load()`` succeeds.
- Writes are atomic (temp file in the same directory + ``os.replace``).
- The only code that deletes anything is ``remove_signature``, for the one
  stored image of the one signature being deleted, and only when
  ``is_store_file`` says the file belongs to the store. There is no sweep of
  unreferenced images: the data dir may serve another profile, and a
  ``.bak`` still references its images.

This module must stay importable without tkinter. It is used by the GUI only:
the core/CLI never reads the profile (headless runs stay deterministic).
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import math
import os
import re
import tempfile
import uuid
from pathlib import Path

import stamps as stamplib

PROFILE_VERSION = 1
APP_NAME = "Cachet"
PROFILE_FILENAME = "profile.json"
SIGNATURES_DIRNAME = "signatures"
ENV_CONFIG_DIR = "CACHET_CONFIG_DIR"
ENV_DATA_DIR = "CACHET_DATA_DIR"

MAX_LABEL_LEN = 80
_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff")
# Names import_image / store_image produce: 32 hex digits of the sha256 + extension.
_STORE_NAME_RE = re.compile(r"[0-9a-f]{32}\.[a-z0-9]+")
_ID_RE = re.compile(r"[0-9a-f]{12}")
# validate_stamp messages meaning "the content is fine but its file is not
# there (any more)": such an entry is kept, the GUI shows it as unusable.
_MISSING_FILE_PREFIXES = (
    "image not found", "unknown font", "bundled font file missing", "cannot load font")
_SETTING_KEYS = (
    "language", "mode", "pades_level", "output_dir",
    "azure_vault_url", "azure_auth", "azure_trust_anchors")


# ---------------------------------------------------------------------------
# Directories and ids
# ---------------------------------------------------------------------------

def _resolve_dir(explicit, env_name: str, kind: str) -> Path:
    if explicit is not None:
        return Path(explicit)
    env = os.environ.get(env_name)
    if env:
        return Path(env)
    import platformdirs  # lazy: only the default location needs it

    return Path(getattr(platformdirs, f"user_{kind}_dir")(APP_NAME, appauthor=False))


def resolve_config_dir(explicit=None) -> Path:
    """Directory of ``profile.json``: ``explicit`` > ``CACHET_CONFIG_DIR`` >
    the platform's user config dir. Never creates it."""
    return _resolve_dir(explicit, ENV_CONFIG_DIR, "config")


def resolve_data_dir(explicit=None) -> Path:
    """Directory holding ``signatures/``: ``explicit`` > ``CACHET_DATA_DIR`` >
    the platform's user data dir. Never creates it."""
    return _resolve_dir(explicit, ENV_DATA_DIR, "data")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def is_valid_id(value) -> bool:
    """12 lower-case hex digits — so the reserved GUI element id
    ``"vignette"`` can never be a signature id."""
    return isinstance(value, str) and _ID_RE.fullmatch(value) is not None


# ---------------------------------------------------------------------------
# Placement rules (pure)
# ---------------------------------------------------------------------------

def page_placement_applies(page, placed_on, template_dims, anchor=None) -> bool:
    """Is a placement saved for the 1-based ``page`` in force on this
    template? Only when that page exists, is the anchor page (if an anchor
    applies) and has EXACTLY the ``(w, h)`` the element was placed on."""
    if page is None or placed_on is None or not template_dims:
        return False
    n = len(template_dims)
    idx = page - 1
    if not 0 <= idx < n:
        return False
    if anchor is not None and idx != (0 if anchor == "first" else n - 1):
        return False
    return tuple(placed_on) == tuple(template_dims[idx])


def stamp_placement_applies(stamp, placed_on, template_dims, anchor=None) -> bool:
    """Full rule for a ``Stamp``: numbered page, first/last anchor or every
    page (then any page of the template may match, or the anchor page when
    the batch is forced onto one)."""
    if stamp.x is None or stamp.y is None or placed_on is None or not template_dims:
        return False
    n = len(template_dims)
    dims = [tuple(d) for d in template_dims]
    po = tuple(placed_on)
    if stamp.all_pages:
        if anchor is not None:                       # forced onto the anchor page
            return po == dims[0 if anchor == "first" else n - 1]
        return po in dims
    if stamp.page_anchor is not None:
        page = 1 if stamp.page_anchor == "first" else n
    else:
        page = stamp.page
    return page_placement_applies(page, placed_on, template_dims, anchor)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Placement:                 # vignette placement
    page: int
    x: float
    y: float
    placed_on: tuple[float, float]


@dataclasses.dataclass
class Settings:                  # None = "never set by the user" -> env/defaults apply
    language: str | None = None
    mode: str | None = None
    pades_level: str | None = None
    output_dir: str | None = None
    azure_vault_url: str | None = None
    azure_auth: str | None = None
    azure_trust_anchors: str | None = None
    vignette: Placement | None = None


@dataclasses.dataclass
class Signature:                 # one library entry
    id: str
    label: str
    stamp: stamplib.Stamp        # content + last placement (page target, x, y)
    enabled: bool = True
    placed_on: tuple[float, float] | None = None   # (w, h) of the page it was placed on


@dataclasses.dataclass
class Profile:
    settings: Settings = dataclasses.field(default_factory=Settings)
    signatures: list[Signature] = dataclasses.field(default_factory=list)


# ---------------------------------------------------------------------------
# Tolerant parsing helpers (never raise)
# ---------------------------------------------------------------------------

def _finite(value) -> float | None:
    """``value`` as a finite float, else None. A bool is never a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:                            # an integer too large for a float
        return None
    return number if math.isfinite(number) else None


def _finite_pair(value) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    w, h = _finite(value[0]), _finite(value[1])
    return None if w is None or h is None else (w, h)


def _parse_vignette(data) -> Placement | None:
    if not isinstance(data, dict):
        return None
    page = data.get("page")
    x, y = _finite(data.get("x")), _finite(data.get("y"))
    placed_on = _finite_pair(data.get("placed_on"))
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        return None
    if x is None or y is None or placed_on is None:
        return None
    return Placement(page=page, x=x, y=y, placed_on=placed_on)


def _parse_settings(data) -> Settings:
    if not isinstance(data, dict):
        return Settings()
    settings = Settings()
    for key in _SETTING_KEYS:
        if isinstance(data.get(key), str):
            setattr(settings, key, data[key])
    settings.vignette = _parse_vignette(data.get("vignette"))
    return settings


def _resolved(path) -> Path | None:
    try:
        return Path(path).resolve()
    except (OSError, RuntimeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` through a temp file in the same directory +
    ``os.replace``: a crash mid-write never leaves a truncated file. The file
    is created 0600 on POSIX (``mkstemp``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class ProfileStore:
    """Reads and writes one user profile and its image store."""

    def __init__(self, config_dir=None, data_dir=None):
        # Resolved ONCE (the env is read here). Nothing is created on disk.
        self.config_dir = resolve_config_dir(config_dir)
        self.data_dir = resolve_data_dir(data_dir)
        # True while the profile on disk was neither read nor moved aside:
        # saving the (empty) in-memory profile would destroy it.
        self._keep_file = False

    @property
    def profile_path(self) -> Path:
        return self.config_dir / PROFILE_FILENAME

    @property
    def signatures_dir(self) -> Path:
        return self.data_dir / SIGNATURES_DIRNAME

    # -- profile ------------------------------------------------------------

    def load(self) -> Profile:
        """Tolerant load: never raises. See the module docstring."""
        self._keep_file = False
        # utf-8-sig: a BOM added by an editor (or PowerShell 5) is not corruption.
        try:
            raw = self.profile_path.read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            return Profile()
        except OSError:
            self._keep_file = True                   # unreadable: left alone
            return Profile()
        except ValueError:                           # UnicodeDecodeError
            return self._set_aside()
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError):         # bad JSON / pathological nesting
            return self._set_aside()
        if not isinstance(data, dict):
            return self._set_aside()
        version = data.get("version")
        if isinstance(version, bool) or not isinstance(version, int):
            return self._set_aside()
        if version != PROFILE_VERSION:
            return self._set_aside()                 # e.g. written by a newer Cachet

        profile = Profile(settings=_parse_settings(data.get("settings")))
        entries = data.get("signatures")
        used_ids: set[str] = set()
        for entry in entries if isinstance(entries, list) else []:
            sig = self._parse_signature(entry, used_ids)
            if sig is not None:
                used_ids.add(sig.id)
                profile.signatures.append(sig)
        return profile

    def _set_aside(self) -> Profile:
        """Move an unusable profile to ``profile.json.bak`` and start afresh."""
        try:
            os.replace(self.profile_path, self.profile_path.with_name(PROFILE_FILENAME + ".bak"))
        except OSError:
            self._keep_file = True                   # still in place: not to be overwritten
        return Profile()

    def _parse_signature(self, entry, used_ids: set[str]) -> Signature | None:
        """One library entry, or None when it has to be skipped."""
        if not isinstance(entry, dict):
            return None
        try:
            stamp = stamplib.Stamp.from_dict(entry, base_dir=self.signatures_dir)
        except stamplib.StampError:
            return None
        try:
            stamplib.validate_stamp(stamp, placed=False)
        except stamplib.StampError as exc:
            # Malformed content is dropped; a missing image/font file is kept
            # (shown as unusable, the user can delete it).
            if not str(exc).startswith(_MISSING_FILE_PREFIXES):
                return None

        placed_on = None
        try:
            stamplib.validate_placement(stamp)
            placed_on = _finite_pair(entry.get("placed_on"))
        except stamplib.StampError:
            # Unplaced, or an incomplete/invalid placement: cleared. The one
            # exception is an unplaced signature that remembers "every page".
            remembers_all_pages = (
                stamp.all_pages and stamp.page is None and stamp.page_anchor is None
                and stamp.x is None and stamp.y is None)
            if not remembers_all_pages:
                stamp = dataclasses.replace(
                    stamp, page=None, page_anchor=None, all_pages=False, x=None, y=None)

        sig_id = entry.get("id")
        if not is_valid_id(sig_id) or sig_id in used_ids:
            sig_id = new_id()
            while sig_id in used_ids:
                sig_id = new_id()
        label = entry.get("label")
        if not isinstance(label, str) or not label.strip():
            label = stamp.content_label()
            if stamp.kind == "text":
                label = label[1:-1]                  # without the quotes
        enabled = entry.get("enabled")
        return Signature(
            id=sig_id, label=label[:MAX_LABEL_LEN], stamp=stamp,
            enabled=enabled if isinstance(enabled, bool) else True,
            placed_on=placed_on)

    def _payload(self, profile: Profile) -> dict:
        settings: dict = {}
        for key in _SETTING_KEYS:
            value = getattr(profile.settings, key)
            if value is not None:
                settings[key] = value
        v = profile.settings.vignette
        if v is not None:
            settings["vignette"] = {
                "page": v.page, "x": v.x, "y": v.y, "placed_on": list(v.placed_on)}
        signatures = []
        for sig in profile.signatures:
            entry = {"id": sig.id, "label": sig.label, "enabled": sig.enabled}
            entry.update(sig.stamp.to_dict(base_dir=self.signatures_dir))
            if sig.placed_on is not None:
                entry["placed_on"] = list(sig.placed_on)
            signatures.append(entry)
        return {"version": PROFILE_VERSION, "settings": settings, "signatures": signatures}

    def save(self, profile: Profile) -> None:
        """Atomic write of ``profile.json`` (UTF-8, no BOM). Raises ``OSError``
        — also when the last ``load()`` had to leave an unusable file in place."""
        if self._keep_file:
            raise OSError(f"{self.profile_path} could not be read and is left untouched")
        text = json.dumps(self._payload(profile), ensure_ascii=False, indent=2)
        # A lone surrogate (a folder name that is not valid UTF-8, a "\ud800"
        # typed into the file) cannot be encoded: backslashreplace writes it
        # as \udXXX, which is precisely its JSON escape — the file stays valid
        # UTF-8, loads back identically, and save never raises a ValueError.
        _atomic_write(self.profile_path, text.encode("utf-8", errors="backslashreplace"))

    # -- image store --------------------------------------------------------

    def _store_bytes(self, data: bytes, ext: str) -> Path:
        """Content-addressed write: the same bytes always land in the same
        file, which is left untouched when it already exists."""
        target = self.signatures_dir / f"{hashlib.sha256(data).hexdigest()[:32]}{ext}"
        if not target.exists():
            _atomic_write(target, data)
        return target

    def import_image(self, source) -> Path:
        """Copy an image file into the store and return the stored path.
        ``StampError`` if it is not a readable image (nothing is written),
        ``OSError`` if the copy cannot be written."""
        source = Path(source)
        stamplib.load_image(source)                  # validate first
        ext = source.suffix.lower()
        return self._store_bytes(
            source.read_bytes(), ext if ext in _IMAGE_EXTENSIONS else ".img")

    def store_image(self, image) -> Path:
        """Store a PIL image (a drawn signature) as a PNG; returns its path."""
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return self._store_bytes(buf.getvalue(), ".png")

    def is_store_file(self, path) -> bool:
        """THE predicate for "this file belongs to the image store": not a
        symlink, a DIRECT child of ``signatures_dir`` by RESOLVED paths
        (pathlib's ``is_relative_to`` is lexical: ``dir/../../x`` would count
        as inside), and a content-addressed name."""
        try:
            path = Path(path)
            if path.is_symlink():
                return False
            resolved = path.resolve()
            return (resolved.parent == self.signatures_dir.resolve()
                    and _STORE_NAME_RE.fullmatch(resolved.name) is not None)
        except (OSError, RuntimeError, ValueError):
            return False

    def remove_signature(self, profile: Profile, sig_id: str) -> None:
        """Remove a library entry (no-op if absent) and, when it was an image
        living in the store that no REMAINING signature references, its file.
        Anything that is not a store file is never unlinked. Does NOT save."""
        for index, sig in enumerate(profile.signatures):
            if sig.id == sig_id:
                break
        else:
            return
        del profile.signatures[index]
        path = sig.stamp.image_path
        if sig.stamp.kind != "image" or path is None or not self.is_store_file(path):
            return
        target = _resolved(path)
        if target is None:
            return
        for other in profile.signatures:
            other_path = other.stamp.image_path
            if (other.stamp.kind == "image" and other_path is not None
                    and _resolved(other_path) == target):
                return                               # still in use
        try:
            target.unlink()
        except OSError:
            pass
