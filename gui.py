#!/usr/bin/env python3
"""CustomTkinter graphical interface for Cachet.

This module is imported ONLY when running `sign_pdfs_beid.py --gui`: it
depends on tkinter/customtkinter, which are absent in CLI/headless mode. All the
business logic (validation, image insertion, eID signing, placement math) lives
in `sign_pdfs_beid.py` and is tested without tkinter; the GUI is merely a
façade that follows the requested workflow:

    1. Choose the template PDF.      5. Choose the mode (eID | image).
    2. Choose the files.             6. Page + position (both modes;
    3. Choose the output folder.        image: also the image choice).
    4. Validate the files.           7. Run.  8. Per-document summary.

The selection frame (step 6) shows the actual rendering of the template's
current page and resizes with the window; a click there places the signature
(scaled placeholder: image in image mode, 3:1 box of width page/5 in beid
mode).
"""

from __future__ import annotations

import os
import queue
import threading
from pathlib import Path
from tkinter import filedialog, ttk

import customtkinter as ctk
from PIL import Image, ImageTk

import sign_pdfs_beid as core

_FRAME_MAX_W = 360   # max size of the page preview frame (px)
_FRAME_MAX_H = 460


# --------------------------------------------------------------------------
# Step-5 explanation texts (left column: one short hint per input element;
# right column: the mode/level/AES-vs-QES documentation; popup: full glossary)
# --------------------------------------------------------------------------

_HINT_WRAP = 430  # px; CTk labels do not auto-wrap

_HINT_BEID = (
    "Signs with the citizen's eID card: a QUALIFIED signature (QES), legally "
    "equal to a handwritten one. Requires a card reader, the inserted card and "
    "one PIN entry per document. ⚠ Embeds your national register number (RRN) "
    "in every signature — mind how signed PDFs are distributed."
)
_HINT_AZURE = (
    "Signs with your personal certificate held in Azure Key Vault: an ADVANCED "
    "signature (AES). One Microsoft sign-in for the whole batch — no card, no "
    "per-document PIN. Needs network access; only the document fingerprint "
    "(digest) ever leaves this machine."
)
_HINT_IMAGE = (
    "Pastes a picture onto the page. NOT a cryptographic signature: no legal "
    "value, nothing to configure, works fully offline. Use it only when a "
    "document merely needs to look signed."
)
_HINT_LEVEL = (
    "Durability of the signature (eID and Azure modes). Keep the default "
    "b-lta: the file stays verifiable for decades. Levels above b-b need "
    "internet access (timestamp authority, trust lists, OCSP/CRL); choose "
    "b-b only to sign offline. See the documentation panel for details."
)
_HINT_VAULT = (
    "Required. The address of your organisation's Azure Key Vault, where your "
    "personal signing key and certificate are stored (ask your administrator). "
    "Note: the pre-filled https://login.live.com is the Microsoft sign-in "
    "page, NOT a vault — a real Key Vault address looks like "
    "https://<name>.vault.azure.net; replace it with your organisation's "
    "vault before launching."
)
_HINT_KEY = (
    "Optional. Normally the key is derived from YOUR login (sig-<your-upn>), "
    "so you can only sign in your own name. Fill this only to use another "
    "key — the override is clearly flagged in the run output."
)
_HINT_ANCHORS = (
    "PEM/DER file with your organisation's internal CA chain (root + "
    "intermediates). Required for levels b-lt/b-lta and for the post-signing "
    "verification: it anchors the trust of your certificate. The EU trusted "
    "list is NOT used in Azure mode."
)
_HINT_AUTH = (
    "How you sign in to Microsoft. 'interactive' opens your browser "
    "(recommended); 'device-code' shows a code to type on another device "
    "(for terminals); 'default' is for automation/testing only — it can pick "
    "a service account and break the personal-signature model. Signing in "
    "now is optional: otherwise the login simply happens at launch."
)

_DOC_INTRO = (
    "Cachet produces three very different kinds of \"signature\". The summary "
    "below explains the modes, the PAdES durability levels and the AES/QES "
    "legal tiers — open \"Read more\" for the full glossary of every term "
    "and technology used by the app."
)

_DOC_PANEL = """\
THE THREE MODES

Cachet signs a whole batch of PDFs the same way. Pick the mode that matches the legal weight you need.

• BEID — your Belgian eID card
  What it is: a qualified electronic signature (QES), the highest legal tier, equivalent to a handwritten signature.
  Requires: a card reader, your eID card, and one PIN entry per document. A visible vignette (your photo, name, and date) is added.
  Use it when: a document must be legally binding and provably signed by you in person.
  Note: your national register number (RRN) is embedded in every signature, so share signed files carefully.

• AZURE — your personal certificate in Azure Key Vault
  What it is: an advanced electronic signature (AES). Strong and verifiable, but one tier below QES.
  Requires: one Microsoft login per batch (not per document). Only the document digest leaves your machine; the private key never does. Trust is anchored on your organisation's internal CA.
  Use it when: you need a real cryptographic signature for many files quickly, without your physical card.

• IMAGE — paste a picture only
  What it is: a visual stamp, NOT a cryptographic signature. No legal value.
  Requires: nothing; works fully offline.
  Use it when: you only need a document to look signed, with no legal effect.

RECOMMENDATION: use BEID for legally binding documents, AZURE for large batches, and IMAGE only for appearance.


PAdES LEVELS

PAdES levels (ETSI EN 319 142-1) describe how durable and verifiable your signature is. Each level builds on the one before it. Cachet uses b-lta by default and never silently drops to a lower level if the network fails.

• B-B (basic) — The core signature: it proves who signed and that the document has not changed. Works fully offline. Pick it only for quick internal drafts where long-term proof and a trusted time do not matter.

• B-T (+ timestamp) — Adds a trusted timestamp from a time-stamping authority (TSA), proving WHEN you signed. Needs network access. Pick it when the signing date must be provable but long-term archiving is not required.

• B-LT (+ long-term validation) — Embeds the revocation data (OCSP/CRL) and CA certificates inside the PDF (LTV). The signature stays verifiable even after the certificates expire. Needs network access. Pick it for documents you must keep and check years later.

• B-LTA (+ archival, DEFAULT) — Adds an archival timestamp chain so the proof itself survives for decades (the chain is renewed every few years). Needs network access. This is the default because it gives the strongest, longest-lasting guarantee for official records.


AES VS QES: WHICH SIGNATURE DO I NEED?

Under EU law (eIDAS) there are three levels of electronic signature:

• SES (simple): any electronic mark, even a pasted picture. Easy, but weak proof of who signed. This is what "image" mode produces — no legal value.

• AES (advanced): uniquely tied to one signer and to the exact document; any later change is detectable. This is "azure" mode.

• QES (qualified): an AES made with a certified device and a face-to-face-verified identity. By law it is equal to a handwritten signature. This is "beid" (eID card) mode.

QES — eID CARD (beid)
Pros: the strongest level; legally equal to a handwritten signature; accepted by any third party with no prior agreement.
Cons: needs a card reader and your card; you type your PIN ONCE PER DOCUMENT (slow for big batches); your national register number (RRN) is embedded in every file.

AES — AZURE KEY VAULT (azure)
Pros: ONE login per batch, so fast for many files; no card or reader; no RRN exposure.
Cons: not "qualified"; trust relies on your organisation's internal CA, so outside parties may not recognise it automatically.

WHICH ONE SHOULD I CHOOSE?

• Internal documents, or large batches: use azure (AES). One login signs the whole batch.

• Documents leaving the organisation, or where a handwritten-equivalent signature is required: use beid (QES), accepting one PIN per document.

• No card or reader available: azure is your only cryptographic option.
"""

_FULL_DOC = """\
Cachet signs PDF documents in batches. It can apply three kinds of mark: a
cryptographic signature made with your Belgian eID card (beid), a cryptographic
signature made with your personal certificate held in Azure Key Vault (azure),
or a simple pasted image with no legal value (image). For the two cryptographic
modes it follows the European PAdES standard and, after signing, re-checks each
file and tells you exactly what was achieved. The terms below explain what those
checks and labels mean.


GLOSSARY

ELECTRONIC SIGNATURE
A legally recognised way to sign a document electronically. Unlike a scanned
handwritten signature, a cryptographic electronic signature also proves WHO
signed and that the file has not changed since.

SES / AES / QES
The three eIDAS tiers, weakest to strongest. SES (simple) is just "an
electronic mark". AES (advanced) is uniquely linked to the signer and detects
any later change. QES (qualified) is an AES made with a qualified certificate
and secure device, and is legally equal to a handwritten signature. eID = QES,
Azure = AES, image = none.

eIDAS
The EU regulation that defines electronic signatures, trust services and the
SES/AES/QES tiers, so a signature made in one EU country is recognised across
the others.

PAdES
"PDF Advanced Electronic Signatures": the ETSI standard (EN 319 142-1) for
embedding signatures inside PDF files. Cachet writes PAdES signatures so any
compliant reader (e.g. Adobe) can verify them.

CMS
The low-level container format (Cryptographic Message Syntax) that actually
holds the signature bytes, certificates and timestamps inside the PDF. PAdES is
a PDF-specific profile built on top of CMS.

DIGEST / HASH
A short fixed-length fingerprint computed from the document. Change one byte and
the fingerprint changes completely. The signature is made over this fingerprint,
which is why only the digest, never the full file, is sent to Azure.

CERTIFICATE
An electronic identity card for a cryptographic key: it binds a public key to a
person or service and is itself signed by a Certificate Authority. Yours proves
the signature really came from you.

CA / CERTIFICATE CHAIN
A Certificate Authority (CA) issues certificates. Verifying a signature means
following the chain from your certificate up through one or more CAs to a trusted
root. If the whole chain checks out, the signature is trusted.

eID / NON-REPUDIATION CERTIFICATE
A Belgian eID card carries two certificates; Cachet uses the
"non-repudiation" one, which is reserved for legally binding signatures (as
opposed to the "authentication" certificate used only to log in).

RRN
The Belgian National Register Number. It is embedded in every eID signature.
Anyone who receives a signed PDF can read it, so share signed files carefully.

PKCS#11
The standard software interface Cachet uses to talk to the eID card through the
card-reader middleware. It lets the app use the card's key without the key ever
leaving the card.

PIN
The secret code that unlocks your eID card's signing key. The card never reveals
the key; it only signs when the PIN is correct. In beid mode Cachet asks for it
once PER DOCUMENT.

AZURE KEY VAULT
A Microsoft cloud service that stores your personal certificate and key so the
key cannot be exported. Signing happens inside the vault: only the document
digest is sent there, and the signed result comes back.

MICROSOFT ENTRA ID
Microsoft's identity and login service (formerly Azure Active Directory). In
azure mode you log in once PER BATCH to prove you may use your key in the vault.

UPN
User Principal Name: your sign-in identity in Entra ID, usually in the form
name@organisation. It is how the app knows which vault account is yours.

RFC 3161 TIMESTAMP / TSA
A trusted, dated stamp proving the signature existed at a given moment. It comes
from a Time-Stamping Authority (TSA) over the network and protects the signature
even after the signing certificate later expires.

QUALIFIED vs FREE TIMESTAMP
A free timestamp (Cachet's default, from DigiCert) is technically valid and
widely trusted. A qualified timestamp comes from an eIDAS-qualified TSA and
carries stronger legal weight. Both prove "when"; only the qualified one is
"qualified".

LTV
Long-Term Validation: enough proof is stored inside the PDF that it can still be
verified years later, even after the certificates have expired or the issuing
CA has gone offline.

DSS
The Document Security Store: the area inside the PDF where LTV evidence
(certificates and revocation data) is kept so the file is self-contained.

OCSP
A live online check asking the CA "is this certificate still valid right now, or
was it revoked?". The answer is saved in the DSS for LTV.

CRL
Certificate Revocation List: a published list of certificates the CA has
cancelled. An alternative to OCSP for proving a certificate was still good when
used; also stored for LTV.

EU TRUSTED LIST (LOTL)
The official EU list of trusted qualified providers (the List of Trusted Lists).
eID (QES) trust ultimately traces here. Azure (AES) does NOT: it is trusted via
your organisation's internal CA instead.

INTERNAL CA
Your organisation's own Certificate Authority. In azure mode, trust and LTV are
anchored on this internal CA chain (a PEM file you provide), not on the EU
Trusted List.

VIGNETTE
The small visible stamp Cachet draws on the page in eID mode: the cardholder's
photo, "Signed by:", the name and the date. It is the human-readable face of an
otherwise invisible cryptographic signature.

TEMPLATE VALIDATION
An optional safety check: before signing, every input PDF is compared to a model
("template") and must have the same page count and identical page sizes. This
guarantees the signature lands in the right spot on every file in the batch.

B-LTA RENEWAL
B-LTA (the default level) adds an archival timestamp chain so the evidence stays
provable for decades. "Renewal" means that, every few years, a fresh archive
timestamp must be added before the previous one's protection weakens.


PAdES LEVELS, AT A GLANCE
• B-B: basic signature, fully offline.
• B-T: adds a trusted timestamp (network needed).
• B-LT: adds revocation info and CA certs (LTV).
• B-LTA: adds the archival timestamp chain (default).

Cachet never silently downgrades these levels. If the network is unavailable
and the requested level cannot be reached, it tells you rather than quietly
producing a weaker signature.
"""


class CachetApp(ctk.CTk):
    """Main window: the whole workflow in a scrollable view."""

    def __init__(self, args):
        super().__init__()
        self.title(f"Cachet {core.__version__} — PDF signing")
        self.geometry("1180x950")  # two-column step 5 needs the width
        _style = ttk.Style()
        _style.configure("Treeview", rowheight=30, font=("", 11))   # tall rows, full text
        _style.configure("Treeview.Heading", font=("", 11, "bold"))

        # --- state ---
        self.default_lib = getattr(args, "lib", None)
        self.template_path: Path | None = None
        self.template_dims: list[tuple[float, float]] = []
        self.input_paths: list[Path] = []
        self.valid_paths: list[Path] = []
        self.output_dir: Path | None = None
        self.mode_var = ctk.StringVar(value="beid")
        self.pades_level_var = ctk.StringVar(value="b-lta")  # PAdES level
        # azure mode state (CACHET_AZURE_* env vars pre-fill the panel).
        self.azure_vault_var = ctk.StringVar(
            value=os.environ.get(core.ENV_AZURE_VAULT_URL, "https://login.live.com"))
        self.azure_key_var = ctk.StringVar(
            value=os.environ.get(core.ENV_AZURE_KEY_NAME, ""))
        self.azure_auth_var = ctk.StringVar(value="interactive")  # GUI default
        self.azure_anchors_path: Path | None = (
            Path(p) if (p := os.environ.get(core.ENV_AZURE_TRUST_ANCHORS)) else None
        )
        self._azure_login_q: queue.Queue | None = None
        self.image_path: Path | None = None
        self.cur_page = 0                         # 0-based, for the preview
        self.place_page: int | None = None        # 1-based, chosen position
        self.place_x: float | None = None
        self.place_y: float | None = None
        self._canvas_img = None                   # PhotoImage ref of the placeholder
        self._bg_img = None                       # PhotoImage ref of the page background
        self._page_img_cache: dict = {}           # (template, page) -> full-resolution PIL
        self._last_win_size = None

        root = ctk.CTkScrollableFrame(self)
        root.pack(fill="both", expand=True, padx=12, pady=12)
        self._build_steps(root)
        self._refresh_placement_section()
        self.bind("<Configure>", self._on_resize)  # the canvas grows with the window

    # ------------------------------------------------------------------ UI
    def _build_steps(self, root) -> None:
        def header(txt):
            lbl = ctk.CTkLabel(root, text=txt, font=ctk.CTkFont(size=15, weight="bold"))
            lbl.pack(anchor="w", pady=(12, 2))
            return lbl

        # 1. template
        header("1. Template PDF")
        row = ctk.CTkFrame(root, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkButton(row, text="Choose template…", command=self._pick_template).pack(side="left")
        self.template_lbl = ctk.CTkLabel(row, text="(none)"); self.template_lbl.pack(side="left", padx=10)

        # 2. files
        header("2. Files to sign")
        row = ctk.CTkFrame(root, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkButton(row, text="Choose files…", command=self._pick_inputs).pack(side="left")
        self.inputs_lbl = ctk.CTkLabel(row, text="(none)"); self.inputs_lbl.pack(side="left", padx=10)

        # 3. output
        header("3. Output folder")
        row = ctk.CTkFrame(root, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkButton(row, text="Choose folder…", command=self._pick_output).pack(side="left")
        self.output_lbl = ctk.CTkLabel(row, text="(none)"); self.output_lbl.pack(side="left", padx=10)

        # 4. validation
        header("4. Validation (page count + exact dimensions)")
        ctk.CTkButton(root, text="Validate files", command=self._validate).pack(anchor="w")
        self.valid_table = self._make_table(root, ("File", "Result", "Detail"), height=5)

        # 5. mode — split vertically: controls + per-input explanations on the
        # LEFT, the "which signature / which level / AES vs QES" docs on the
        # RIGHT (with a Read-more popup for the full glossary).
        header("5. Signing mode")
        split = ctk.CTkFrame(root, fg_color="transparent")
        split.pack(fill="x", pady=(0, 4))
        split.grid_columnconfigure(0, weight=1, uniform="step5")
        split.grid_columnconfigure(1, weight=1, uniform="step5")

        left = ctk.CTkFrame(split)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        def hint(parent, txt, **pack_kw):
            # Small gray explanation under each control: what it is, why we
            # need it, whether it is required.
            lbl = ctk.CTkLabel(parent, text=txt, justify="left", anchor="w",
                               wraplength=_HINT_WRAP,
                               text_color=("gray25", "gray70"),
                               font=ctk.CTkFont(size=11))
            lbl.pack(fill="x", padx=(28, 8), **pack_kw)
            return lbl

        # Radios stacked VERTICALLY, one explanation each.
        ctk.CTkRadioButton(left, text="eID (card + vignette) — qualified, QES",
                           variable=self.mode_var, value="beid",
                           command=self._refresh_placement_section
                           ).pack(anchor="w", padx=8, pady=(10, 0))
        hint(left, _HINT_BEID, pady=(2, 8))
        ctk.CTkRadioButton(left, text="Azure (Microsoft login) — advanced, AES",
                           variable=self.mode_var, value="azure",
                           command=self._refresh_placement_section
                           ).pack(anchor="w", padx=8)
        hint(left, _HINT_AZURE, pady=(2, 8))
        ctk.CTkRadioButton(left, text="Image insertion — visual stamp only",
                           variable=self.mode_var, value="image",
                           command=self._refresh_placement_section
                           ).pack(anchor="w", padx=8)
        hint(left, _HINT_IMAGE, pady=(2, 8))

        # PAdES level (used by the eID and Azure modes).
        lrow = ctk.CTkFrame(left, fg_color="transparent")
        lrow.pack(fill="x", padx=8)
        ctk.CTkLabel(lrow, text="PAdES level:").pack(side="left")
        ctk.CTkOptionMenu(lrow, variable=self.pades_level_var,
                          values=list(core.PADES_LEVELS), width=110
                          ).pack(side="left", padx=8)
        hint(left, _HINT_LEVEL, pady=(2, 10))

        # Azure panel (shown only in azure mode; see _refresh_placement_section).
        self.azure_section = ctk.CTkFrame(left, fg_color="transparent")
        az = self.azure_section
        ctk.CTkLabel(az, text="Azure settings",
                     font=ctk.CTkFont(size=13, weight="bold")
                     ).pack(anchor="w", padx=8)
        arow1 = ctk.CTkFrame(az, fg_color="transparent")
        arow1.pack(fill="x", padx=8, pady=(4, 0))
        ctk.CTkLabel(arow1, text="Vault URL:", width=150, anchor="w").pack(side="left")
        ctk.CTkEntry(arow1, textvariable=self.azure_vault_var).pack(
            side="left", fill="x", expand=True, padx=(4, 0))
        hint(az, _HINT_VAULT, pady=(2, 6))
        arow2 = ctk.CTkFrame(az, fg_color="transparent")
        arow2.pack(fill="x", padx=8)
        ctk.CTkLabel(arow2, text="Key name (override):", width=150, anchor="w"
                     ).pack(side="left")
        ctk.CTkEntry(arow2, textvariable=self.azure_key_var).pack(
            side="left", fill="x", expand=True, padx=(4, 0))
        hint(az, _HINT_KEY, pady=(2, 6))
        arow3 = ctk.CTkFrame(az, fg_color="transparent")
        arow3.pack(fill="x", padx=8)
        ctk.CTkButton(arow3, text="Internal CA chain (PEM)…", width=180,
                      command=self._pick_azure_anchors).pack(side="left")
        self.azure_anchors_lbl = ctk.CTkLabel(
            arow3, text=str(self.azure_anchors_path or "(none chosen)"))
        self.azure_anchors_lbl.pack(side="left", padx=8)
        hint(az, _HINT_ANCHORS, pady=(2, 6))
        arow4 = ctk.CTkFrame(az, fg_color="transparent")
        arow4.pack(fill="x", padx=8)
        ctk.CTkLabel(arow4, text="Auth method:", width=150, anchor="w"
                     ).pack(side="left")
        ctk.CTkOptionMenu(arow4, variable=self.azure_auth_var,
                          values=list(core.AZURE_AUTH_METHODS), width=140
                          ).pack(side="left", padx=(4, 12))
        self.azure_login_btn = ctk.CTkButton(
            arow4, text="Sign in with Microsoft", command=self._azure_sign_in)
        self.azure_login_btn.pack(side="left")
        self.azure_login_lbl = ctk.CTkLabel(arow4, text="(not signed in)")
        self.azure_login_lbl.pack(side="left", padx=8)
        hint(az, _HINT_AUTH, pady=(2, 8))

        # RIGHT column: documentation panel + Read-more popup.
        right = ctk.CTkFrame(split)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        ctk.CTkLabel(right, text="Which signature should I use?",
                     font=ctk.CTkFont(size=14, weight="bold")
                     ).pack(anchor="w", padx=10, pady=(10, 2))
        ctk.CTkLabel(right, text=_DOC_INTRO, justify="left", anchor="w",
                     wraplength=_HINT_WRAP,
                     font=ctk.CTkFont(size=11)).pack(fill="x", padx=10)
        self.doc_box = ctk.CTkTextbox(right, wrap="word", height=330,
                                      font=ctk.CTkFont(size=12))
        self.doc_box.pack(fill="both", expand=True, padx=10, pady=(6, 2))
        self.doc_box.insert("1.0", _DOC_PANEL)
        self.doc_box.configure(state="disabled")
        ctk.CTkButton(right, text="Read more — full documentation…",
                      command=self._show_docs_popup
                      ).pack(anchor="e", padx=10, pady=(2, 10))

        # 6. page + position (BOTH modes; the image choice appears only in image mode)
        self.image_section = ctk.CTkFrame(root)
        ctk.CTkLabel(self.image_section, text="6. Page + position",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w", pady=(4, 2))
        self.image_row = ctk.CTkFrame(self.image_section, fg_color="transparent")
        self.image_row.pack(fill="x")
        ctk.CTkButton(self.image_row, text="Choose image…", command=self._pick_image).pack(side="left")
        self.image_lbl = ctk.CTkLabel(self.image_row, text="(none)"); self.image_lbl.pack(side="left", padx=10)

        self._nav_row = ctk.CTkFrame(self.image_section, fg_color="transparent")
        self._nav_row.pack(fill="x", pady=4)
        ctk.CTkButton(self._nav_row, text="◀ Previous", width=110,
                      command=lambda: self._turn_page(-1)).pack(side="left")
        self.page_lbl = ctk.CTkLabel(self._nav_row, text="page —/—"); self.page_lbl.pack(side="left", padx=10)
        ctk.CTkButton(self._nav_row, text="Next ▶", width=110,
                      command=lambda: self._turn_page(1)).pack(side="left")
        self.pos_lbl = ctk.CTkLabel(self._nav_row, text="position: (click in the frame)")
        self.pos_lbl.pack(side="left", padx=16)

        # tkinter Canvas: background = rendered page, click = position. Grows with the window.
        import tkinter as tk
        self.canvas = tk.Canvas(self.image_section, width=_FRAME_MAX_W, height=_FRAME_MAX_H,
                                bg="#d9d9d9", highlightthickness=1, highlightbackground="#888")
        self.canvas.pack(pady=6, fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_canvas_click)

        # 7. run  (anchor: the image section is placed JUST before this header)
        self._after_image_anchor = header("7. Run")
        self.launch_btn = ctk.CTkButton(root, text="Run",
                                        command=self._launch, fg_color="#2a7", hover_color="#196")
        self.launch_btn.pack(anchor="w")
        self.status_lbl = ctk.CTkLabel(root, text=""); self.status_lbl.pack(anchor="w", pady=2)

        # 8. summary
        header("8. Summary")
        self.summary_table = self._make_table(root, ("Document", "Signed", "Detail"), height=8)

    def _make_table(self, parent, columns, height):
        table = ttk.Treeview(parent, columns=columns, show="headings", height=height)
        widths = {"File": 220, "Document": 220, "Detail": 380}
        for c in columns:
            table.heading(c, text=c)
            table.column(c, width=widths.get(c, 90), anchor="w")
        table.pack(fill="x", pady=4)
        return table

    @staticmethod
    def _clear(table):
        for item in table.get_children():
            table.delete(item)

    def _refresh_placement_section(self) -> None:
        # Placement section visible in ALL modes (vignette OR image). Placed
        # BEFORE "7. Run" to respect the workflow order.
        self.image_section.pack(fill="both", expand=True, pady=6, before=self._after_image_anchor)
        if self.mode_var.get() == "image":
            self.image_row.pack(fill="x", before=self._nav_row)   # image choice
        else:
            self.image_row.pack_forget()                  # beid/azure: no image
        if self.mode_var.get() == "azure":
            # lives at the end of the step-5 LEFT column -> plain pack works
            self.azure_section.pack(fill="x", pady=(0, 8))
        else:
            self.azure_section.pack_forget()
        self._draw_page()

    # ---------------------------------------------------------- documentation
    def _show_docs_popup(self) -> None:
        """'Read more' window: the full glossary of every term/technology."""
        win = getattr(self, "_docs_win", None)
        if win is not None and win.winfo_exists():
            win.lift()
            win.focus()
            return
        win = ctk.CTkToplevel(self)
        win.title("Cachet — Documentation")
        win.geometry("840x780")
        box = ctk.CTkTextbox(win, wrap="word", font=ctk.CTkFont(size=13))
        box.pack(fill="both", expand=True, padx=12, pady=12)
        box.insert("1.0", _FULL_DOC)
        box.configure(state="disabled")
        self._docs_win = win

    # ------------------------------------------------------------ azure auth
    def _pick_azure_anchors(self) -> None:
        path = filedialog.askopenfilename(
            title="Internal CA chain (root + intermediates)",
            filetypes=[("Certificates", "*.pem *.crt *.cer *.der"),
                       ("All files", "*.*")])
        if path:
            self.azure_anchors_path = Path(path)
            self.azure_anchors_lbl.configure(text=path)

    def _azure_sign_in(self) -> None:
        """Interactive Microsoft login on a WORKER thread (system browser /
        device code), result delivered through a queue + after() — tkinter
        is never touched off the main thread. The credential is cached
        process-wide (azure_signer.get_cached_credential), so the batch
        reuses this login instead of prompting again."""
        self.azure_login_btn.configure(state="disabled")
        self.azure_login_lbl.configure(text="signing in…")
        self._azure_login_q = queue.Queue()
        method = self.azure_auth_var.get()

        def worker(q: queue.Queue) -> None:
            try:
                import azure_signer as az

                user = az.acquire_user(az.get_cached_credential(method))
                q.put(("ok", user.upn))
            except Exception as exc:  # noqa: BLE001 - report, don't crash
                q.put(("err", str(exc) or exc.__class__.__name__))

        threading.Thread(target=worker, args=(self._azure_login_q,),
                         daemon=True).start()
        self.after(100, self._poll_azure_login)

    def _poll_azure_login(self) -> None:
        if self._azure_login_q is None:
            return
        try:
            if not self.winfo_exists():  # window closed mid-login
                return
        except Exception:  # noqa: BLE001
            return
        try:
            kind, payload = self._azure_login_q.get_nowait()
        except queue.Empty:
            self.after(100, self._poll_azure_login)
            return
        self._azure_login_q = None
        self.azure_login_btn.configure(state="normal")
        if kind == "ok":
            self.azure_login_lbl.configure(text=f"signed in as {payload}")
        else:
            self.azure_login_lbl.configure(text="(not signed in)")
            self.status_lbl.configure(text=f"Microsoft sign-in failed: {payload}")

    # -------------------------------------------------------------- actions
    def _pick_template(self) -> None:
        path = filedialog.askopenfilename(title="Template PDF", filetypes=[("PDF", "*.pdf")])
        if not path:
            return
        self.template_path = Path(path)
        self._page_img_cache.clear()              # new template -> new renders
        try:
            self.template_dims = core.page_dimensions(self.template_path)
        except Exception as exc:  # noqa: BLE001
            self.template_dims = []
            self.template_lbl.configure(text=f"unreadable: {exc}")
            return
        self.cur_page = 0
        self.template_lbl.configure(
            text=f"{self.template_path.name}  ({len(self.template_dims)} pages)"
        )
        self._draw_page()

    def _pick_inputs(self) -> None:
        paths = filedialog.askopenfilenames(title="Files to sign", filetypes=[("PDF", "*.pdf")])
        if not paths:
            return
        self.input_paths = [Path(p) for p in paths]
        self.inputs_lbl.configure(text=f"{len(self.input_paths)} file(s)")

    def _pick_output(self) -> None:
        path = filedialog.askdirectory(title="Output folder")
        if not path:
            return
        self.output_dir = Path(path)
        self.output_lbl.configure(text=str(self.output_dir))

    def _pick_image(self) -> None:
        path = filedialog.askopenfilename(
            title="Signature image", filetypes=[("Images", "*.png *.jpg *.jpeg *.gif *.bmp")]
        )
        if not path:
            return
        self.image_path = Path(path)
        self.image_lbl.configure(text=self.image_path.name)
        self._draw_page()

    def _validate(self) -> None:
        self._clear(self.valid_table)
        self.valid_paths = []
        if not self.template_path or not self.input_paths:
            self.status_lbl.configure(text="Choose a template and files first.")
            return
        for r in core.validate_files(self.template_path, self.input_paths):
            self.valid_table.insert("", "end",
                                    values=(r.path.name, "✓ OK" if r.ok else "✗ rejected", r.reason or "—"))
            if r.ok:
                self.valid_paths.append(r.path)
        self.status_lbl.configure(
            text=f"{len(self.valid_paths)}/{len(self.input_paths)} valid file(s)."
        )

    def _turn_page(self, delta: int) -> None:
        if not self.template_dims:
            return
        self.cur_page = max(0, min(len(self.template_dims) - 1, self.cur_page + delta))
        self._draw_page()

    def _canvas_target_size(self) -> tuple[int, int]:
        """Target canvas size, derived from the window (grows/shrinks with it)."""
        w = max(320, self.winfo_width() - 130)
        h = max(260, int(self.winfo_height() * 0.55))
        return w, h

    def _get_page_image(self, page_index):
        """Full-resolution (PIL) image of the template page, cached."""
        if not self.template_path:
            return None
        key = (str(self.template_path), page_index)
        if key not in self._page_img_cache:
            self._page_img_cache[key] = core.render_page_image(
                self.template_path, page_index, px_width=900
            )
        return self._page_img_cache[key]

    def _draw_page(self) -> None:
        if not hasattr(self, "canvas") or not self.template_dims:
            return
        cw, ch = self._canvas_target_size()
        self.canvas.configure(width=cw, height=ch)
        self.canvas.delete("all")
        pw, ph = self.template_dims[self.cur_page]
        fw, fh = core.fit_frame(pw, ph, cw, ch)
        ox, oy = (cw - fw) / 2, (ch - fh) / 2
        pil = self._get_page_image(self.cur_page)   # background = actual page rendering
        if pil is not None:
            self._bg_img = ImageTk.PhotoImage(
                pil.resize((max(1, int(fw)), max(1, int(fh))))
            )
            self.canvas.create_image(ox, oy, anchor="nw", image=self._bg_img)
            self.canvas.create_rectangle(ox, oy, ox + fw, oy + fh, outline="#333")
        else:                                       # render unavailable -> blank frame
            self.canvas.create_rectangle(ox, oy, ox + fw, oy + fh, fill="white", outline="#333")
        self.page_lbl.configure(text=f"page {self.cur_page + 1}/{len(self.template_dims)}")
        self._frame_geom = (fw, fh, ox, oy)
        if self.place_page == self.cur_page + 1 and self.place_x is not None:
            self._draw_placeholder(self.place_x, self.place_y)

    def _on_canvas_click(self, event) -> None:
        if self.mode_var.get() not in ("image", "beid") or not self.template_dims:
            return
        if not hasattr(self, "_frame_geom"):
            return
        fw, fh, ox, oy = self._frame_geom
        cx, cy = event.x - ox, event.y - oy
        if not (0 <= cx <= fw and 0 <= cy <= fh):
            return
        pw, ph = self.template_dims[self.cur_page]
        x, y = core.frame_click_to_pdf_xy(pw, ph, fw, fh, cx, cy)
        self.place_page, self.place_x, self.place_y = self.cur_page + 1, x, y
        self.pos_lbl.configure(text=f"position: page {self.place_page} @ ({x:.0f}, {y:.0f}) pt")
        self._draw_page()

    def _placeholder_size_pt(self) -> tuple[float, float]:
        """Placeholder size (pt): actual image in image mode, otherwise a 3:1
        vignette box of width page/5 of the current page (beid mode)."""
        pw = self.template_dims[self.cur_page][0]
        if self.mode_var.get() == "image" and self.image_path:
            return core.image_size_pt(self.image_path)
        return core.vignette_size_pt(pw)

    def _draw_placeholder(self, x, y) -> None:
        fw, fh, ox, oy = self._frame_geom
        pw, ph = self.template_dims[self.cur_page]
        iw, ih = self._placeholder_size_pt()
        left, top, w, h = core.pdf_rect_to_frame_rect(pw, ph, fw, fh, x, y, iw, ih)
        left, top = left + ox, top + oy
        if self.mode_var.get() == "image" and self.image_path:
            try:
                im = Image.open(self.image_path).convert("RGBA")
                im = im.resize((max(1, int(w)), max(1, int(h))))
                self._canvas_img = ImageTk.PhotoImage(im)
                self.canvas.create_image(left, top, anchor="nw", image=self._canvas_img)
            except Exception:  # noqa: BLE001
                pass
        self.canvas.create_rectangle(left, top, left + w, top + h, outline="#c00", width=2)

    def _on_resize(self, event) -> None:
        # the canvas follows the window size, keeping the page proportions
        if event.widget is not self:
            return
        size = (event.width, event.height)
        if size == self._last_win_size:
            return
        self._last_win_size = size
        if self.template_dims:
            self._draw_page()

    # --------------------------------------------------------------- launch
    def _launch(self) -> None:
        files = self.valid_paths or self.input_paths
        if not files or not self.output_dir:
            self.status_lbl.configure(text="Validated files and an output folder are required.")
            return
        if self.mode_var.get() == "image" and (self.image_path is None or self.place_x is None):
            self.status_lbl.configure(
                text="Image mode: choose an image, then click in the frame to set the position."
            )
            return
        # beid: a click places the vignette; without a click, default vignette
        # (bottom-right, last page). So we pass place_* as-is (None if no
        # click) — process_batch derives the placement from it.
        cfg = core.RunConfig(
            inputs=files,
            output=self.output_dir,
            mode=self.mode_var.get(),
            template=self.template_path,
            pades_level=self.pades_level_var.get(),
            lib=self.default_lib,
            image_path=self.image_path,
            page=self.place_page,
            x=self.place_x,
            y=self.place_y,
            # azure settings (ignored by the other modes). The worker batch
            # reuses the credential cached by "Sign in with Microsoft"; if
            # the user skipped it, the login happens on the worker thread.
            azure_vault_url=self.azure_vault_var.get().strip() or None,
            azure_key_name=self.azure_key_var.get().strip() or None,
            azure_auth=self.azure_auth_var.get(),
            azure_trust_anchors=self.azure_anchors_path,
        )
        try:
            core.validate_config(cfg)
        except ValueError as exc:
            self.status_lbl.configure(text=str(exc))
            return
        self._clear(self.summary_table)
        self.status_lbl.configure(text="Processing…")
        self.launch_btn.configure(state="disabled")   # avoids concurrent batches
        # Tkinter is not thread-safe: the worker writes ONLY to a queue,
        # and the main thread drains it via a periodic after().
        self._result_q = queue.Queue()
        threading.Thread(target=self._run_batch, args=(cfg,), daemon=True).start()
        self.after(100, self._poll_results)

    def _run_batch(self, cfg) -> None:
        # run off the main thread: NO Tk calls here, only the queue.
        try:
            results = core.process_batch(
                cfg, on_progress=lambda r: self._result_q.put(("row", r))
            )
            self._result_q.put(("done", results))
        except (Exception, SystemExit) as exc:  # noqa: BLE001
            # open_eid_session() raises SystemExit ("no reader/card") —
            # SystemExit is NOT an Exception: without this case, the thread would
            # die silently and the GUI would stay stuck on "Processing…".
            self._result_q.put(("error", str(exc) or exc.__class__.__name__))

    def _poll_results(self) -> None:
        # main thread: drain the queue and update the widgets.
        if not self.winfo_exists():       # window closed during processing
            return
        try:
            while True:
                kind, payload = self._result_q.get_nowait()
                if kind == "row":
                    self.summary_table.insert(
                        "", "end",
                        values=(payload.path.name, "✓" if payload.ok else "✗", payload.detail),
                    )
                elif kind == "done":
                    ok = sum(1 for r in payload if r.ok)
                    self.status_lbl.configure(
                        text=f"Done: {ok}/{len(payload)} document(s) processed."
                    )
                    self.launch_btn.configure(state="normal")
                    return
                elif kind == "error":
                    self.status_lbl.configure(text=f"Error: {payload}")
                    self.launch_btn.configure(state="normal")
                    return
        except queue.Empty:
            pass
        self.after(100, self._poll_results)


def launch_gui(args) -> int:
    """Entry point called by `sign_pdfs_beid.py --gui`."""
    ctk.set_appearance_mode("system")
    app = CachetApp(args)
    app.mainloop()
    return 0
