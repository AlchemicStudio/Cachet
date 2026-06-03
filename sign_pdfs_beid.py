#!/usr/bin/env python3
"""Signe en lot des PDF avec la carte d'identité belge (eID) via pyHanko.

Prérequis :
    1. Middleware eID belge installé (https://eid.belgium.be) -> fournit
       la bibliothèque PKCS#11 (libbeidpkcs11.so / .dylib / beidpkcs11.dll).
    2. Lecteur de carte + carte insérée sur CETTE machine.
    3. pip install "pyHanko[pkcs11,image-support]" pyhanko-beid-plugin

Usage :
    python sign_pdfs_beid.py ./entree ./signes
    python sign_pdfs_beid.py ./entree ./signes --lib /usr/lib/libbeidpkcs11.so
    python sign_pdfs_beid.py doc1.pdf doc2.pdf ./signes --pades

Important :
    - On utilise le certificat de SIGNATURE (non-répudiation), juridiquement
      équivalent à une signature manuscrite. À n'autoriser qu'à du code de confiance.
    - La carte exige généralement le PIN pour CHAQUE signature
      (CKA_ALWAYS_AUTHENTICATE sur la clé de non-répudiation) : prévois donc
      une saisie de PIN par document.
    - Le numéro de registre national est inscrit dans le certificat et donc
      lisible dans chaque signature produite. Attention à la diffusion des PDF.
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
from pathlib import Path

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
from pyhanko.sign import PdfSignatureMetadata, signers
from pyhanko.sign.fields import SigFieldSpec, SigSeedSubFilter
from pyhanko.stamp import StaticStampStyle, TextStampStyle

# Depuis pyHanko >= 0.22, le support eID belge vit dans le plugin séparé.
# Sur une très vieille install (< 0.22), remplacer par :
#   from pyhanko.sign.beid import BEIDSigner
# from pyhanko_beid import BEIDSigner
# On n'utilise plus open_beid_session() : il exige rigidement un token
# au label « BELPIC », ce qui échoue selon le lecteur/middleware. Voir
# open_eid_session() plus bas.
from pyhanko_beid.beid import BEIDSigner

def default_pkcs11_lib() -> str:
    """Chemin probable de la lib PKCS#11 eID selon l'OS."""
    system = platform.system()
    if system == "Windows":
        return r"C:\Windows\System32\beidpkcs11.dll"
    if system == "Darwin":
        return "/usr/local/lib/libbeidpkcs11.dylib"
    # Linux : l'emplacement varie selon la distribution.
    for candidate in (
        "/usr/lib/libbeidpkcs11.so",
        "/usr/local/lib/libbeidpkcs11.so",
        "/usr/lib/x86_64-linux-gnu/libbeidpkcs11.so",
    ):
        if Path(candidate).exists():
            return candidate
    return "/usr/lib/libbeidpkcs11.so"


def open_eid_session(lib_path: str):
    """Ouvre une session PKCS#11 sur la carte eID, avec un diagnostic clair.

    Remplace ``pyhanko_beid.open_beid_session()`` qui exige rigidement un
    token au label « BELPIC ». Selon le lecteur ou la version du middleware,
    le token peut porter un autre label (ou aucun), d'où l'erreur trompeuse
    « No token matching criteria TokenCriteria(label='BELPIC') found » —
    erreur qui survient AUSSI, à l'identique, quand aucune carte n'est lue.

    Stratégie : on choisit le token « BELPIC » s'il existe, sinon le premier
    token présent ; et on émet un message actionnable si aucun lecteur ou
    aucune carte n'est détecté. La session retournée (``token.open()``) est
    identique à celle qu'utilise pyHanko en interne.
    """
    try:
        lib = pkcs11.lib(lib_path)
    except Exception as exc:  # noqa: BLE001 - on veut un message lisible
        raise SystemExit(
            f"Impossible de charger la bibliothèque PKCS#11 « {lib_path} » : {exc}"
        )

    slots = lib.get_slots(token_present=False)
    if not slots:
        raise SystemExit(
            "Aucun lecteur de carte détecté.\n"
            "  - Branche le lecteur eID.\n"
            "  - Vérifie que le service pcscd tourne : "
            "sudo systemctl status pcscd"
        )

    # Slots qui contiennent réellement un token (= une carte lisible).
    tokens = []
    for slot in slots:
        try:
            tokens.append(slot.get_token())
        except PKCS11Error:
            continue  # lecteur présent, mais pas de carte lisible dans ce slot

    if not tokens:
        readers = "\n".join(f"      - {s.slot_description.strip()}" for s in slots)
        raise SystemExit(
            "Lecteur détecté, mais aucune carte n'a pu être lue :\n"
            f"{readers}\n"
            "  Vérifie que :\n"
            "    - la carte eID belge est insérée correctement et à fond ;\n"
            "    - c'est bien une carte eID (pas une autre carte à puce) ;\n"
            "    - le middleware eID est installé et le service pcscd tourne.\n"
            "  Réinsère la carte puis relance la commande."
        )

    chosen = next(
        (t for t in tokens if (t.label or "").strip().upper() == "BELPIC"),
        None,
    )
    if chosen is None:
        chosen = tokens[0]
        print(
            f"  Note : aucun token « BELPIC » trouvé ; utilisation du token "
            f"présent (label={chosen.label!r}).",
            file=sys.stderr,
        )

    print(f"  Carte détectée : label={chosen.label!r}, serial={chosen.serial!r}")
    return chosen.open()


# --- Apparence de la signature visible (vignette en bas à droite) ---------
_STAMP_W = 210        # largeur de la vignette par défaut (points PDF, 1 pt = 1/72")
_STAMP_H = 62         # hauteur de la vignette par défaut (3 lignes de texte)
_STAMP_MARGIN = 24    # marge depuis le coin inférieur droit de la page
_PHOTO_BAND_FRAC = 0.2  # part de la largeur réservée à la photo (0.2×210 = 42, inchangé)

# Vignette placée librement (mode beid avec position choisie) : la taille exacte
# n'est pas connue à l'avance, on dessine donc un cadre 3:1 (paysage) large d'1/5
# de la page (hauteur = largeur / 3).
_VIGNETTE_W_FRAC = 1 / 5
_VIGNETTE_ASPECT = 3.0


def vignette_size_pt(page_w: float) -> tuple[float, float]:
    """Taille (largeur, hauteur) en points de la vignette placée librement :
    largeur = page_w/5, hauteur = largeur/3 (ratio 3:1 paysage)."""
    w = page_w * _VIGNETTE_W_FRAC
    return (w, w / _VIGNETTE_ASPECT)


@dataclasses.dataclass
class CardIdentity:
    """Données lues sur la carte pour la vignette de signature."""

    name: str                      # ex. « Sébastien Denooz »
    photo: Image.Image | None      # portrait eID (ou None si illisible)


def read_card_identity(session) -> CardIdentity:
    """Lit le nom (certificat de signature) et la photo (objet ``PHOTO_FILE``)
    sur la carte eID via la session PKCS#11 déjà ouverte.

    Ces deux lectures ne nécessitent PAS le PIN ; elles peuvent donc se faire
    une seule fois, avant la boucle de signature.
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
        name = common.split(" (")[0]  # retire le suffixe « (Signature) »
    else:
        name = "le titulaire de la carte"

    photo = None
    try:
        for obj in session.get_objects(
            {Attribute.CLASS: ObjectClass.DATA, Attribute.LABEL: "PHOTO_FILE"}
        ):
            data = bytes(obj[Attribute.VALUE])
            if data[:2] == b"\xff\xd8":  # en-tête JPEG
                photo = Image.open(io.BytesIO(data))
                photo.load()  # décode maintenant : la session pourra se fermer
            break
    except (PKCS11Error, OSError):
        photo = None

    return CardIdentity(name=name, photo=photo)


def _page_mediabox(writer, page_index: int) -> list[float]:
    """MediaBox de la page `page_index` (0-based, -1 = dernière), avec héritage
    depuis l'arbre de pages."""
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
    return [0.0, 0.0, 595.276, 841.89]  # A4 par défaut


def _last_page_mediabox(writer) -> list[float]:
    """MediaBox de la dernière page (avec héritage du tree de pages)."""
    return _page_mediabox(writer, -1)


def _last_page_box(writer) -> tuple[float, float, float, float]:
    """Rectangle de la vignette, calé en bas à droite de la dernière page."""
    mb = _last_page_mediabox(writer)
    x1 = mb[2] - _STAMP_MARGIN
    x0 = max(mb[0] + 4, x1 - _STAMP_W)
    y0 = mb[1] + _STAMP_MARGIN
    y1 = min(y0 + _STAMP_H, mb[3] - 4)
    return (x0, y0, x1, y1)


def build_stamp_style(
    identity: CardIdentity, box_w: float = _STAMP_W, box_h: float = _STAMP_H
) -> TextStampStyle:
    """Construit l'apparence : photo à gauche, « Signed by ... » à droite.

    Les marges sont PROPORTIONNELLES à la largeur du cadre (box_w) : la bande
    photo vaut ``_PHOTO_BAND_FRAC × box_w`` (soit 42 pt pour le cadre par défaut
    de 210 pt — inchangé), ce qui permet à la même vignette de tenir dans un
    cadre plus petit (mode beid avec position libre) sans déborder.

    Une NOUVELLE instance est créée par document : l'image de fond est liée au
    writer lors du rendu, on évite donc de réutiliser le même objet entre PDF.
    """
    # Vignette en 3 lignes : « Signed by: » / nom / « at <date> ».
    # Le nom est échappé (%%) au cas où il contiendrait un %, tandis que
    # %(ts)s est remplacé par pyHanko par la date courante (format ci-dessous)
    # au moment de la signature.
    safe_name = identity.name.replace("%", "%%")
    text = f"Signed by:\n{safe_name}\nat %(ts)s"
    text_style = dataclasses.replace(TextStampStyle().text_box_style, font_size=10)
    band = _PHOTO_BAND_FRAC * box_w   # bande photo, à gauche

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

    # Pas de photo lisible : on se rabat sur un texte seul, centré.
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
    """Résout les arguments d'entrée en une liste de fichiers PDF."""
    pdfs: list[Path] = []
    for raw in inputs:
        path = Path(raw)
        if path.is_dir():
            pdfs.extend(sorted(path.glob("*.pdf")))
        elif path.suffix.lower() == ".pdf" and path.is_file():
            pdfs.append(path)
        else:
            print(f"  Ignoré (pas un PDF) : {path}", file=sys.stderr)
    return pdfs


def sign_one(
    signer: BEIDSigner,
    src: Path,
    dst: Path,
    field_name: str,
    use_pades: bool,
    identity: CardIdentity,
    page_index: int | None = None,
    pos: tuple[float, float] | None = None,
) -> None:
    """Signe un PDF (signature incrémentale) et y appose la vignette visible
    (photo + « Signed by ... »).

    Par défaut (``pos`` None) la vignette est calée en bas à droite de la
    DERNIÈRE page (comportement historique). Si ``pos`` est fourni, elle est
    placée sur la page ``page_index`` (0-based), coin inférieur gauche à ``pos``
    points, dans un cadre 3:1 large d'1/5 de la page (cf. ``vignette_size_pt``).
    """
    meta = PdfSignatureMetadata(
        field_name=field_name,
        subfilter=(
            SigSeedSubFilter.PADES if use_pades else SigSeedSubFilter.ADOBE_PKCS7_DETACHED
        ),
    )
    with src.open("rb") as inf:
        # strict=False : accepte les PDF à références croisées « hybrides »
        # (table xref classique + flux xref dans le même fichier), produits
        # par certains outils pour rétro-compatibilité. En mode strict,
        # pyHanko refuse de les signer
        # (« hybrid cross-reference sections while hybrid xrefs are disabled »).
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
            meta, signer=signer, stamp_style=style, new_field_spec=field_spec
        )
        out = pdf_signer.sign_pdf(writer)
    dst.write_bytes(out.getbuffer())


# =========================================================================
#  Validation par rapport à un modèle (« template »)
# =========================================================================

def _iter_pages(node, inherited_mb=None):
    """Parcourt l'arbre de pages dans l'ordre et yielde (page, mediabox), en
    propageant le /MediaBox hérité d'un nœud parent."""
    node = node.get_object()
    mb = node.raw_get("/MediaBox").get_object() if "/MediaBox" in node else inherited_mb
    if "/Kids" in node:
        for kid in node["/Kids"]:
            yield from _iter_pages(kid, mb)
    else:
        yield node, mb


def _mediabox_wh(mb) -> tuple[float, float]:
    if mb is None:
        return (595.276, 841.89)  # A4 par défaut
    vals = [float(v.get_object() if hasattr(v, "get_object") else v) for v in mb]
    return (vals[2] - vals[0], vals[3] - vals[1])


def page_dimensions(pdf_path) -> list[tuple[float, float]]:
    """Dimensions (largeur, hauteur) en points de chaque page, dans l'ordre."""
    with open(pdf_path, "rb") as f:
        reader = PdfFileReader(f, strict=False)
        return [_mediabox_wh(mb) for _, mb in _iter_pages(reader.root["/Pages"])]


@dataclasses.dataclass
class ValidationResult:
    """Résultat de la validation d'un fichier face au modèle."""

    path: Path
    ok: bool
    reason: str = ""


def validate_against_template(
    template_dims: list[tuple[float, float]], pdf_path
) -> ValidationResult:
    """Vérifie qu'un PDF a le MÊME nombre de pages ET des dimensions par page
    EXACTEMENT identiques au modèle (aucune tolérance)."""
    path = Path(pdf_path)
    try:
        dims = page_dimensions(path)
    except Exception as exc:  # noqa: BLE001
        return ValidationResult(path, False, f"illisible ({exc})")
    if len(dims) != len(template_dims):
        return ValidationResult(
            path, False, f"{len(dims)} page(s), le modèle en a {len(template_dims)}"
        )
    for i, (d, t) in enumerate(zip(dims, template_dims), start=1):
        if d != t:
            return ValidationResult(
                path,
                False,
                f"page {i} : {d[0]:.2f}×{d[1]:.2f} pt ≠ modèle {t[0]:.2f}×{t[1]:.2f} pt",
            )
    return ValidationResult(path, True, "")


def validate_files(template_path, pdf_paths) -> list[ValidationResult]:
    """Valide une liste de PDF face au modèle (lu une seule fois)."""
    template_dims = page_dimensions(template_path)
    return [validate_against_template(template_dims, p) for p in pdf_paths]


# =========================================================================
#  Mode « image » : insertion d'une image de signature
# =========================================================================
_IMG_TARGET_W_PT = 150.0  # largeur d'insertion par défaut (points, ratio conservé)


def image_size_pt(image_path) -> tuple[float, float]:
    """Taille (largeur, hauteur) en points de l'image insérée : largeur fixe
    (_IMG_TARGET_W_PT), hauteur déduite du ratio."""
    with Image.open(image_path) as im:
        w_px, h_px = im.size
    return (_IMG_TARGET_W_PT, _IMG_TARGET_W_PT * h_px / w_px)


def insert_image_one(src, dst, image_path, page_index: int, x: float, y: float) -> None:
    """Insère l'image sur la page `page_index` (0-based, -1 = dernière) avec son
    coin inférieur gauche à (x, y) points depuis le coin inférieur gauche de la
    page. Mise à jour incrémentale — aucune carte requise."""
    img = Image.open(image_path)
    img.load()
    w_pt, h_pt = image_size_pt(image_path)
    n_pages = len(page_dimensions(src))  # message clair si la page est hors limites
    if not (-n_pages <= page_index < n_pages):
        shown = page_index + 1 if page_index >= 0 else page_index
        raise ValueError(f"page {shown} hors limites (le document a {n_pages} page(s))")
    with open(src, "rb") as inf:
        writer = IncrementalPdfFileWriter(inf, strict=False)
        mb = _page_mediabox(writer, page_index)  # (x, y) relatifs au coin page
        style = StaticStampStyle(
            background=images.PdfImage(img),
            background_opacity=1.0,
            background_layout=SimpleBoxLayoutRule(
                x_align=AxisAlignment.ALIGN_MID,
                y_align=AxisAlignment.ALIGN_MID,
                margins=Margins(),
                inner_content_scaling=InnerScaling.STRETCH_TO_FIT,
            ),
            border_width=0,   # pas de bordure noire autour de l'image insérée
        )
        stamp = style.create_stamp(writer, BoxConstraints(width=w_pt, height=h_pt), {})
        stamp.apply(page_index, int(round(mb[0] + x)), int(round(mb[1] + y)))
        out = io.BytesIO()
        writer.write(out)
    Path(dst).write_bytes(out.getbuffer())


# =========================================================================
#  Calcul de placement (partagé avec la GUI, testable sans tkinter)
# =========================================================================

def fit_frame(page_w, page_h, max_w, max_h) -> tuple[float, float]:
    """Dimensions (l, h) d'un cadre respectant le ratio de la page et tenant
    dans (max_w, max_h)."""
    scale = min(max_w / page_w, max_h / page_h)
    return (page_w * scale, page_h * scale)


def frame_click_to_pdf_xy(
    page_w, page_h, frame_w, frame_h, click_x, click_y
) -> tuple[float, float]:
    """Convertit un clic dans le cadre (origine haut-gauche, comme tkinter) en
    point PDF (origine bas-gauche), en points."""
    pdf_x = click_x / frame_w * page_w
    pdf_y = (frame_h - click_y) / frame_h * page_h
    return (pdf_x, pdf_y)


def pdf_rect_to_frame_rect(
    page_w, page_h, frame_w, frame_h, x, y, img_w, img_h
) -> tuple[float, float, float, float]:
    """Rectangle (left, top, width, height) en pixels-cadre pour dessiner à
    l'échelle une image de taille (img_w, img_h) pt dont le coin inférieur
    gauche PDF est (x, y)."""
    sx, sy = frame_w / page_w, frame_h / page_h
    return (x * sx, frame_h - (y + img_h) * sy, img_w * sx, img_h * sy)


def render_page_image(pdf_path, page_index: int, px_width: int = 900):
    """Rendu bitmap de la page `page_index` (0-based) via `pdftoppm` (poppler).

    Renvoie une image Pillow (~px_width de large, ratio conservé) ou ``None`` si
    le rendu échoue (poppler absent, page invalide…). Utilisé comme fond du
    cadre de sélection ; le code appelant retombe sur un cadre blanc si None.
    """
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
    """Chemin de sortie libre : ``{stem}{suffix}.pdf`` puis, en cas de collision,
    ``{stem}{suffix} - 1.pdf``, ``- 2``, … jusqu'à un nom inexistant. Ne réécrit
    JAMAIS un fichier déjà présent."""
    out_dir = Path(out_dir)
    base = f"{stem}{suffix}"
    candidate = out_dir / f"{base}.pdf"
    i = 1
    while candidate.exists():
        candidate = out_dir / f"{base} - {i}.pdf"
        i += 1
    return candidate


# =========================================================================
#  Configuration d'exécution + traitement par lot (partagé CLI / GUI)
# =========================================================================

@dataclasses.dataclass
class RunConfig:
    """Paramètres d'une exécution, identiques pour la CLI et la GUI."""

    inputs: list[Path]
    output: Path
    mode: str = "beid"               # "beid" (carte eID + vignette) | "image"
    template: Path | None = None
    pades: bool = False
    field: str = "Signature"
    lib: str | None = None
    image_path: Path | None = None
    # Page (1-based) + position (points, coin bas-gauche). None = non spécifié :
    # en mode image on retombe sur page 1 / (0, 0) ; en mode beid, l'absence de
    # position déclenche la vignette par défaut (bas à droite, dernière page).
    page: int | None = None
    x: float | None = None
    y: float | None = None


@dataclasses.dataclass
class DocResult:
    """Issue du traitement d'un document."""

    path: Path
    output: Path | None
    ok: bool
    detail: str


def validate_config(cfg: RunConfig) -> None:
    """Vérifie la cohérence d'une RunConfig ; lève ValueError sinon."""
    if not cfg.inputs:
        raise ValueError("Aucun PDF d'entrée.")
    if cfg.output is None:
        raise ValueError("Dossier de sortie manquant (--output).")
    if cfg.mode not in ("beid", "image"):
        raise ValueError(f"Mode inconnu : {cfg.mode!r} (attendu beid|image).")
    if cfg.mode == "image":
        if not cfg.image_path:
            raise ValueError("--image-path est requis en mode image.")
        if not Path(cfg.image_path).exists():
            raise ValueError(f"Image introuvable : {cfg.image_path}")
        if cfg.page is not None and cfg.page < 1:
            raise ValueError("--page doit être >= 1.")
    if cfg.template and not Path(cfg.template).exists():
        raise ValueError(f"Modèle introuvable : {cfg.template}")


def process_batch(cfg: RunConfig, *, on_progress=None) -> list[DocResult]:
    """Valide (si un modèle est fourni) puis traite chaque fichier selon le
    mode. Renvoie un DocResult par fichier d'entrée. `on_progress` est appelé
    après chaque document (utile pour la GUI)."""
    results: list[DocResult] = []
    template_dims = page_dimensions(cfg.template) if cfg.template else None

    signer = identity = None
    if cfg.mode == "beid":
        session = open_eid_session(cfg.lib or default_pkcs11_lib())
        signer = BEIDSigner(session)
        identity = read_card_identity(session)

    Path(cfg.output).mkdir(parents=True, exist_ok=True)

    # Position éventuelle (None = non spécifiée). Mode beid : si fournie, vignette
    # placée librement ; sinon vignette par défaut (bas à droite, dernière page).
    placement = cfg.x is not None and cfg.y is not None
    page = cfg.page or 1
    x = cfg.x if cfg.x is not None else 0.0
    y = cfg.y if cfg.y is not None else 0.0

    for src in cfg.inputs:
        src = Path(src)
        if template_dims is not None:
            verdict = validate_against_template(template_dims, src)
            if not verdict.ok:
                res = DocResult(src, None, False, f"rejeté — {verdict.reason}")
                results.append(res)
                if on_progress:
                    on_progress(res)
                continue
        dst = unique_output_path(cfg.output, src.stem)  # ne réécrit jamais
        try:
            if cfg.mode == "beid":
                if placement:
                    sign_one(signer, src, dst, f"{cfg.field}1", cfg.pades, identity,
                             page_index=page - 1, pos=(x, y))
                    detail = f"signé eID — vignette page {page} @ ({x:.0f}, {y:.0f})"
                else:
                    sign_one(signer, src, dst, f"{cfg.field}1", cfg.pades, identity)
                    detail = "signé eID — vignette"
                detail += " (PAdES)" if cfg.pades else ""
            else:
                insert_image_one(src, dst, cfg.image_path, page - 1, x, y)
                detail = f"image insérée — page {page} @ ({x:.0f}, {y:.0f})"
            res = DocResult(src, dst, True, detail)
        except Exception as exc:  # noqa: BLE001 - on continue le lot
            res = DocResult(src, None, False, f"échec — {exc}")
        results.append(res)
        if on_progress:
            on_progress(res)
    return results


def print_summary(results: list[DocResult], out_dir) -> None:
    """Affiche un tableau récapitulatif du lot."""
    print("\n=== Récapitulatif ===")
    width = max((len(r.path.name) for r in results), default=8)
    for r in results:
        flag = "OK   " if r.ok else "ÉCHEC"
        print(f"  [{flag}] {r.path.name:<{width}}  {r.detail}")
    ok = sum(1 for r in results if r.ok)
    print(f"\n{ok}/{len(results)} document(s) traité(s) avec succès. Sortie : {out_dir}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Signe (carte eID belge) ou tamponne une image, en lot, sur des PDF."
    )
    # Positionnels conservés pour la rétro-compatibilité : « entrées… sortie ».
    parser.add_argument(
        "inputs",
        nargs="*",
        help="(Ancien style) entrées… puis dossier de sortie. Préférer --input/--output.",
    )
    parser.add_argument(
        "--gui", action="store_true", help="Lance l'interface graphique (CustomTkinter)."
    )
    parser.add_argument(
        "--template", default=None, help="PDF modèle pour valider les fichiers d'entrée."
    )
    parser.add_argument(
        "--input", nargs="+", default=None, help="Fichier(s)/dossier(s) PDF à traiter."
    )
    parser.add_argument("--output", default=None, help="Dossier de sortie.")
    parser.add_argument(
        "--mode",
        choices=("beid", "image"),
        default="beid",
        help="Mode : beid (carte eID + vignette) ou image (insertion d'image).",
    )
    parser.add_argument(
        "--image-path", dest="image_path", default=None,
        help="Image à insérer (requis en --mode image).",
    )
    parser.add_argument(
        "--page", type=int, default=None,
        help="Page cible (1-based). Image : page d'insertion. beid : page de la vignette.",
    )
    parser.add_argument(
        "--x", type=float, default=None,
        help="Position X (points, depuis le coin inférieur gauche de la page). "
             "En mode beid, --x/--y placent la vignette (sinon : bas à droite).",
    )
    parser.add_argument(
        "--y", type=float, default=None,
        help="Position Y (points, depuis le coin inférieur gauche de la page).",
    )
    parser.add_argument("--lib", default=None, help="Chemin vers la lib PKCS#11 eID.")
    parser.add_argument(
        "--field", default="Signature", help="Nom de base du champ de signature (mode beid)."
    )
    parser.add_argument(
        "--pades", action="store_true", help="Signature PAdES (archivage long terme)."
    )
    return parser


def resolve_config(args) -> RunConfig:
    """Construit une RunConfig depuis les arguments argparse, en acceptant à la
    fois les nouveaux drapeaux (--input/--output) et l'ancien style positionnel."""
    raw_inputs = list(args.input) if args.input else []
    output = args.output
    if not raw_inputs and args.inputs:  # rétro-compat : positionnels
        if output is not None:
            raw_inputs = list(args.inputs)
        else:
            *raw_inputs, output = args.inputs

    cfg = RunConfig(
        inputs=collect_pdfs([str(p) for p in raw_inputs]),
        output=Path(output) if output else None,
        mode=args.mode,
        template=Path(args.template) if args.template else None,
        pades=args.pades,
        field=args.field,
        lib=args.lib,
        image_path=Path(args.image_path) if args.image_path else None,
        page=args.page,
        x=args.x,
        y=args.y,
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
                f"Impossible de charger la GUI : {exc}\n"
                "Installe CustomTkinter et le support Tk :\n"
                "  pip install customtkinter\n"
                "  sudo apt install python3-tk   (ou python3.14-tk)",
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
                f"Bibliothèque PKCS#11 introuvable : {lib_path}\n"
                "Installe le middleware eID ou précise le chemin avec --lib.",
                file=sys.stderr,
            )
            return 1
        placed = cfg.x is not None and cfg.y is not None
        where = (f"vignette page {cfg.page or 1} @ ({cfg.x:.0f}, {cfg.y:.0f})"
                 if placed else "vignette bas-droite, dernière page")
        print(f"Mode : eID — {where}. Lib PKCS#11 : {lib_path}")
        print(f"{len(cfg.inputs)} PDF. Le PIN sera demandé pour chaque document.")
    else:
        print(
            f"Mode : image — {cfg.image_path} "
            f"(page {cfg.page or 1}, x={cfg.x or 0:.0f}, y={cfg.y or 0:.0f})."
        )
        print(f"{len(cfg.inputs)} PDF.")
    if cfg.template:
        print(f"Validation contre le modèle : {cfg.template}")
    print()

    def progress(r: DocResult) -> None:
        print(f"  [{'OK' if r.ok else 'ÉCHEC'}] {r.path.name} — {r.detail}")

    results = process_batch(cfg, on_progress=progress)
    print_summary(results, cfg.output)
    return 0 if results and all(r.ok for r in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
