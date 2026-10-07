#!/usr/bin/env python3
"""Localization catalog for the Cachet GUI.

Import-safe WITHOUT tkinter (plain dicts + helpers), so it is unit-testable
headlessly like the core. The GUI is the only consumer; the CLI stays in
English (its output is technical/log-like and documented that way).

Usage:
    from i18n import tr, set_language, system_language
    set_language(system_language())
    label = tr("landing.start")
    text  = tr("val.summary", ok=3, total=4)

Every key carries all six languages (EN default/fallback, FR, NL, DE, ES,
PT); ``test_i18n.py`` enforces catalog completeness and that the ``{...}``
placeholders of every translation match the English reference. Texts only
use braces for placeholders — ``tr`` formats when kwargs are passed.

Texts may carry a light ``**bold**`` markup (help panel, documentation);
``split_markup`` turns it into segments the GUI renders through text tags.
The long-form documentation of the "Full documentation" popup lives in
``i18n_docs.py`` (``DOCS_CATALOG``) and is merged into ``CATALOG`` below, so
the same invariants and ``tr()`` apply to it.
"""

from __future__ import annotations

import os

from i18n_docs import DOCS_CATALOG

#: Supported language codes, in menu order.
LANGUAGES = ("en", "fr", "nl", "de", "es", "pt")

#: Native display name of each language (for the landing-page selector).
LANGUAGE_NAMES = {
    "en": "English",
    "fr": "Français",
    "nl": "Nederlands",
    "de": "Deutsch",
    "es": "Español",
    "pt": "Português",
}

DEFAULT_LANGUAGE = "en"

_current = DEFAULT_LANGUAGE


def get_language() -> str:
    """Code of the language currently in use."""
    return _current


def set_language(code: str) -> None:
    """Switch the active language; raises ValueError on unsupported codes."""
    global _current
    if code not in LANGUAGES:
        raise ValueError(f"Unsupported language: {code!r} (expected {'|'.join(LANGUAGES)})")
    _current = code


def detect_language(raw: str | None) -> str:
    """Map a locale-ish string ("fr_BE.UTF-8", "nl", "de_DE@euro") to a
    supported language code, defaulting to English. Pure (unit-tested)."""
    if not raw:
        return DEFAULT_LANGUAGE
    code = raw.strip().lower()[:2]
    return code if code in LANGUAGES else DEFAULT_LANGUAGE


def system_language() -> str:
    """Best-effort system language from the usual environment variables
    (LC_ALL > LC_MESSAGES > LANG), falling back to locale.getlocale()."""
    raw = (
        os.environ.get("LC_ALL")
        or os.environ.get("LC_MESSAGES")
        or os.environ.get("LANG")
    )
    if not raw:
        try:
            import locale

            raw = locale.getlocale()[0]
        except Exception:  # noqa: BLE001 - never fail app start over locale
            raw = None
    return detect_language(raw)


def tr(key: str, **fmt) -> str:
    """Translation of ``key`` in the active language (English fallback);
    unknown keys return the key itself so a miss is visible, never fatal."""
    entry = CATALOG.get(key)
    if entry is None:
        return key
    text = entry.get(_current) or entry[DEFAULT_LANGUAGE]
    return text.format(**fmt) if fmt else text


def split_markup(text: str) -> list[tuple[str, bool]]:
    """Split a catalog text on its light ``**bold**`` markup into
    ``(segment, is_bold)`` pairs, in order (empty segments dropped). An
    unpaired trailing ``**`` is kept literally rather than swallowed. Pure,
    tkinter-free — the GUI maps ``is_bold`` onto a text tag."""
    parts = text.split("**")
    if len(parts) % 2 == 0:                 # odd marker count: last one is literal
        parts[-2:] = [parts[-2] + "**" + parts[-1]]
    return [(seg, i % 2 == 1) for i, seg in enumerate(parts) if seg]


#: Sections of the "Full documentation" popup, in display order (keys of
#: ``i18n_docs.DOCS_CATALOG``); the clickable sources block follows them.
DOC_SECTIONS = ("docs.modes", "docs.levels", "docs.tiers", "docs.glossary",
                "docs.glance")

#: Sources listed at the end of the documentation: (title key, URL). The
#: URLs are language-independent; only the titles are translated.
DOC_SOURCES = (
    ("docs.src.eidas", "https://eur-lex.europa.eu/eli/reg/2014/910/oj"),
    ("docs.src.pades",
     "https://www.etsi.org/deliver/etsi_en/319100_319199/31914201/01.01.01_60/en_31914201v010101p.pdf"),
    ("docs.src.tsl",
     "https://www.etsi.org/deliver/etsi_ts/119600_119699/119612/02.02.01_60/ts_119612v020201p.pdf"),
    ("docs.src.tlbrowser", "https://eidas.ec.europa.eu/efda/tl-browser/"),
    ("docs.src.rfc3161", "https://www.rfc-editor.org/rfc/rfc3161"),
    ("docs.src.digicert",
     "https://knowledge.digicert.com/general-information/rfc3161-compliant-time-stamp-authority-server"),
    ("docs.src.cms", "https://www.rfc-editor.org/rfc/rfc5652"),
    ("docs.src.ocsp", "https://www.rfc-editor.org/rfc/rfc6960"),
    ("docs.src.crl", "https://www.rfc-editor.org/rfc/rfc5280"),
    ("docs.src.beid", "https://eid.belgium.be/en"),
    ("docs.src.pkcs11",
     "https://docs.oasis-open.org/pkcs11/pkcs11-base/v2.40/pkcs11-base-v2.40.html"),
    ("docs.src.keyvault", "https://learn.microsoft.com/azure/key-vault/keys/about-keys"),
    ("docs.src.entra", "https://learn.microsoft.com/entra/fundamentals/whatis"),
    ("docs.src.pyhanko", "https://pyhanko.readthedocs.io/"),
)


# =========================================================================
#  Catalog — key -> {language -> text}. Languages always in the order
#  en, fr, nl, de, es, pt. Braces are reserved for placeholders.
# =========================================================================

CATALOG: dict[str, dict[str, str]] = {
    # ------------------------------------------------------------- window
    "app.title": {
        "en": "Cachet {version} — PDF signing",
        "fr": "Cachet {version} — Signature de PDF",
        "nl": "Cachet {version} — PDF's ondertekenen",
        "de": "Cachet {version} — PDF-Signatur",
        "es": "Cachet {version} — Firma de PDF",
        "pt": "Cachet {version} — Assinatura de PDF",
    },
    # ------------------------------------------------------------ landing
    "landing.heading": {
        "en": "Welcome to Cachet",
        "fr": "Bienvenue dans Cachet",
        "nl": "Welkom bij Cachet",
        "de": "Willkommen bei Cachet",
        "es": "Le damos la bienvenida a Cachet",
        "pt": "Damos-lhe as boas-vindas ao Cachet",
    },
    "landing.intro": {
        "en": (
            "Cachet signs a whole batch of PDF documents in one run. Sign with "
            "your Belgian eID card (a qualified signature, legally equal to a "
            "handwritten one), with your personal certificate in Azure Key Vault "
            "(an advanced signature — one Microsoft sign-in for the whole "
            "batch), or add visual signatures with no legal value — typed "
            "text, images or a drawing, one or several per document. A "
            "template guarantees that every document receives each element at "
            "exactly the same spot. Your signatures and preferences are "
            "remembered for next time.\n\nThis assistant guides you through "
            "eight steps: choose a template, the documents and an output "
            "folder, validate the batch, pick the signature type, place the "
            "elements, then sign and review the report."
        ),
        "fr": (
            "Cachet signe tout un lot de documents PDF en une seule fois. "
            "Signez avec votre carte eID belge (signature qualifiée, "
            "juridiquement équivalente à une signature manuscrite), avec votre "
            "certificat personnel dans Azure Key Vault (signature avancée — "
            "une seule connexion Microsoft pour tout le lot), ou ajoutez des "
            "signatures visuelles sans valeur juridique — texte saisi, images "
            "ou dessin, une ou plusieurs par document. Un modèle garantit que "
            "chaque document reçoit chaque élément exactement au même endroit. "
            "Vos signatures et vos préférences sont mémorisées pour la "
            "prochaine fois.\n\nCet assistant vous guide en huit étapes : "
            "choix du modèle, des documents et du dossier de sortie, "
            "validation du lot, choix du type de signature, positionnement "
            "des éléments, puis signature et rapport final."
        ),
        "nl": (
            "Cachet ondertekent een hele reeks PDF-documenten in één keer. "
            "Onderteken met uw Belgische eID-kaart (een gekwalificeerde "
            "handtekening, juridisch gelijkwaardig aan een handgeschreven "
            "handtekening), met uw persoonlijke certificaat in Azure Key Vault "
            "(een geavanceerde handtekening — één Microsoft-aanmelding voor de "
            "hele reeks), of voeg visuele handtekeningen zonder juridische "
            "waarde toe — getypte tekst, afbeeldingen of een tekening, één of "
            "meerdere per document. Een sjabloon garandeert dat elk document "
            "elk element op exact dezelfde plek krijgt. Uw handtekeningen en "
            "voorkeuren worden onthouden voor de volgende keer.\n\nDeze "
            "assistent begeleidt u in acht stappen: kies een sjabloon, de "
            "documenten en een uitvoermap, valideer de reeks, kies het "
            "handtekeningtype, plaats de elementen, onderteken en bekijk het "
            "rapport."
        ),
        "de": (
            "Cachet signiert einen ganzen Stapel PDF-Dokumente in einem "
            "Durchgang. Signieren Sie mit Ihrer belgischen eID-Karte (eine "
            "qualifizierte Signatur, rechtlich der handschriftlichen "
            "Unterschrift gleichgestellt), mit Ihrem persönlichen Zertifikat "
            "in Azure Key Vault (eine fortgeschrittene Signatur — eine einzige "
            "Microsoft-Anmeldung für den ganzen Stapel), oder fügen Sie "
            "visuelle Signaturen ohne Rechtswert hinzu — getippten Text, "
            "Bilder oder eine Zeichnung, eine oder mehrere pro Dokument. Eine "
            "Vorlage stellt sicher, dass jedes Dokument jedes Element an genau "
            "derselben Stelle erhält. Ihre Signaturen und Einstellungen werden "
            "für das nächste Mal gespeichert.\n\nDieser Assistent führt Sie "
            "durch acht Schritte: Vorlage, Dokumente und Ausgabeordner wählen, "
            "Stapel prüfen, Signaturtyp wählen, Elemente platzieren, dann "
            "signieren und den Bericht prüfen."
        ),
        "es": (
            "Cachet firma un lote completo de documentos PDF de una sola vez. "
            "Firme con su tarjeta eID belga (firma cualificada, legalmente "
            "equivalente a la manuscrita), con su certificado personal en "
            "Azure Key Vault (firma avanzada — un solo inicio de sesión de "
            "Microsoft para todo el lote), o añada firmas visuales sin valor "
            "legal — texto escrito, imágenes o un dibujo, una o varias por "
            "documento. Una plantilla garantiza que cada documento reciba cada "
            "elemento exactamente en el mismo lugar. Sus firmas y preferencias "
            "se recuerdan para la próxima vez.\n\nEste asistente le guía en "
            "ocho pasos: elegir la plantilla, los documentos y la carpeta de "
            "salida, validar el lote, elegir el tipo de firma, colocar los "
            "elementos y, por último, firmar y revisar el informe."
        ),
        "pt": (
            "O Cachet assina um lote inteiro de documentos PDF de uma só vez. "
            "Assine com o seu cartão eID belga (assinatura qualificada, "
            "juridicamente equivalente à manuscrita), com o seu certificado "
            "pessoal no Azure Key Vault (assinatura avançada — um único início "
            "de sessão Microsoft para todo o lote), ou adicione assinaturas "
            "visuais sem valor legal — texto escrito, imagens ou um desenho, "
            "uma ou várias por documento. Um modelo garante que cada documento "
            "recebe cada elemento exatamente no mesmo sítio. As suas "
            "assinaturas e preferências ficam guardadas para a próxima "
            "vez.\n\nEste assistente guia-o em oito passos: escolher o modelo, "
            "os documentos e a pasta de saída, validar o lote, escolher o tipo "
            "de assinatura, posicionar os elementos e, por fim, assinar e "
            "rever o relatório."
        ),
    },
    "landing.language_label": {
        "en": "Language:",
        "fr": "Langue :",
        "nl": "Taal:",
        "de": "Sprache:",
        "es": "Idioma:",
        "pt": "Idioma:",
    },
    "support.button": {
        "en": "♥ Support Cachet",
        "fr": "♥ Soutenir Cachet",
        "nl": "♥ Steun Cachet",
        "de": "♥ Cachet unterstützen",
        "es": "♥ Apoyar Cachet",
        "pt": "♥ Apoiar o Cachet",
    },
    "landing.start": {
        "en": "Start",
        "fr": "Commencer",
        "nl": "Starten",
        "de": "Starten",
        "es": "Comenzar",
        "pt": "Começar",
    },
    # --------------------------------------------------------- navigation
    "nav.next": {
        "en": "Next: {step}  ▸",
        "fr": "Suivant : {step}  ▸",
        "nl": "Volgende: {step}  ▸",
        "de": "Weiter: {step}  ▸",
        "es": "Siguiente: {step}  ▸",
        "pt": "Seguinte: {step}  ▸",
    },
    "nav.previous": {
        "en": "◂  Previous: {step}",
        "fr": "◂  Précédent : {step}",
        "nl": "◂  Vorige: {step}",
        "de": "◂  Zurück: {step}",
        "es": "◂  Anterior: {step}",
        "pt": "◂  Anterior: {step}",
    },
    "nav.previous_plain": {
        "en": "◂  Previous",
        "fr": "◂  Précédent",
        "nl": "◂  Vorige",
        "de": "◂  Zurück",
        "es": "◂  Anterior",
        "pt": "◂  Anterior",
    },
    "nav.finish": {
        "en": "Finish",
        "fr": "Terminer",
        "nl": "Voltooien",
        "de": "Fertigstellen",
        "es": "Finalizar",
        "pt": "Concluir",
    },
    "nav.cancel": {
        "en": "Cancel",
        "fr": "Annuler",
        "nl": "Annuleren",
        "de": "Abbrechen",
        "es": "Cancelar",
        "pt": "Cancelar",
    },
    "step.header": {
        "en": "Step {n} of {total} — {title}",
        "fr": "Étape {n} sur {total} — {title}",
        "nl": "Stap {n} van {total} — {title}",
        "de": "Schritt {n} von {total} — {title}",
        "es": "Paso {n} de {total} — {title}",
        "pt": "Passo {n} de {total} — {title}",
    },
    "help.heading": {
        "en": "Help",
        "fr": "Aide",
        "nl": "Hulp",
        "de": "Hilfe",
        "es": "Ayuda",
        "pt": "Ajuda",
    },
    # ------------------------------------------------------- cancel modal
    "cancel.title": {
        "en": "Cancel and start over?",
        "fr": "Annuler et recommencer ?",
        "nl": "Annuleren en opnieuw beginnen?",
        "de": "Abbrechen und neu beginnen?",
        "es": "¿Cancelar y empezar de nuevo?",
        "pt": "Cancelar e recomeçar?",
    },
    "cancel.body": {
        "en": (
            "This discards the current session — template, selected files, "
            "validation and results — and returns to the welcome screen. Your "
            "saved signatures and preferences are kept."
        ),
        "fr": (
            "Cela efface la session en cours — modèle, fichiers sélectionnés, "
            "validation et résultats — et revient à l'écran d'accueil. Vos "
            "signatures enregistrées et vos préférences sont conservées."
        ),
        "nl": (
            "Hiermee wordt de huidige sessie gewist — sjabloon, geselecteerde "
            "bestanden, validatie en resultaten — en keert u terug naar het "
            "welkomstscherm. Uw opgeslagen handtekeningen en voorkeuren "
            "blijven behouden."
        ),
        "de": (
            "Dadurch wird die aktuelle Sitzung verworfen — Vorlage, "
            "ausgewählte Dateien, Prüfung und Ergebnisse — und Sie kehren zum "
            "Startbildschirm zurück. Ihre gespeicherten Signaturen und "
            "Einstellungen bleiben erhalten."
        ),
        "es": (
            "Esto descarta la sesión actual — plantilla, archivos "
            "seleccionados, validación y resultados — y vuelve a la pantalla "
            "de bienvenida. Sus firmas guardadas y sus preferencias se "
            "conservan."
        ),
        "pt": (
            "Isto elimina a sessão atual — modelo, ficheiros selecionados, "
            "validação e resultados — e regressa ao ecrã inicial. As suas "
            "assinaturas guardadas e as suas preferências são mantidas."
        ),
    },
    "cancel.confirm": {
        "en": "Yes, discard",
        "fr": "Oui, tout effacer",
        "nl": "Ja, wissen",
        "de": "Ja, verwerfen",
        "es": "Sí, descartar",
        "pt": "Sim, eliminar",
    },
    "cancel.keep": {
        "en": "No, continue",
        "fr": "Non, continuer",
        "nl": "Nee, doorgaan",
        "de": "Nein, fortfahren",
        "es": "No, continuar",
        "pt": "Não, continuar",
    },
    # ------------------------------------------------- step names + help
    "step.template.short": {
        "en": "Template", "fr": "Modèle", "nl": "Sjabloon",
        "de": "Vorlage", "es": "Plantilla", "pt": "Modelo",
    },
    "step.template.title": {
        "en": "Select Template",
        "fr": "Choisir le modèle",
        "nl": "Sjabloon kiezen",
        "de": "Vorlage wählen",
        "es": "Seleccionar plantilla",
        "pt": "Escolher o modelo",
    },
    "step.template.help": {
        "en": (
            "The template is the reference document: every file you sign is "
            "compared against it. Choose the blank PDF your documents are based "
            "on. Its pages also serve as the preview when you place the "
            "signature later. Only files whose page count and page sizes match "
            "the template exactly will be signed.\n\n"
            "**Only one PDF to sign?** Select that document itself as the "
            "template: it is then compared with itself and always passes "
            "validation."
        ),
        "fr": (
            "Le modèle est le document de référence : chaque fichier à signer "
            "lui est comparé. Choisissez le PDF vierge dont vos documents sont "
            "issus. Ses pages servent aussi d'aperçu au moment de positionner "
            "la signature. Seuls les fichiers dont le nombre de pages et les "
            "dimensions correspondent exactement au modèle seront signés.\n\n"
            "**Un seul PDF à signer ?** Choisissez ce document comme modèle : "
            "comparé à lui-même, il réussit toujours la validation."
        ),
        "nl": (
            "Het sjabloon is het referentiedocument: elk te ondertekenen "
            "bestand wordt ermee vergeleken. Kies de lege PDF waarop uw "
            "documenten zijn gebaseerd. De pagina's dienen ook als voorbeeld "
            "bij het plaatsen van de handtekening. Alleen bestanden waarvan het "
            "aantal pagina's en de paginagrootte exact met het sjabloon "
            "overeenkomen, worden ondertekend.\n\n"
            "**Slechts één PDF te ondertekenen?** Selecteer dat document zelf "
            "als sjabloon: het wordt dan met zichzelf vergeleken en slaagt "
            "altijd voor de validatie."
        ),
        "de": (
            "Die Vorlage ist das Referenzdokument: Jede zu signierende Datei "
            "wird mit ihr verglichen. Wählen Sie das leere PDF, auf dem Ihre "
            "Dokumente basieren. Seine Seiten dienen später auch als Vorschau "
            "beim Platzieren der Signatur. Nur Dateien, deren Seitenzahl und "
            "Seitengrößen exakt mit der Vorlage übereinstimmen, werden "
            "signiert.\n\n"
            "**Nur ein einziges PDF zu signieren?** Wählen Sie dieses Dokument "
            "selbst als Vorlage: Es wird dann mit sich selbst verglichen und "
            "besteht die Prüfung immer."
        ),
        "es": (
            "La plantilla es el documento de referencia: cada archivo a firmar "
            "se compara con ella. Elija el PDF en blanco del que parten sus "
            "documentos. Sus páginas sirven además de vista previa al colocar "
            "la firma. Solo se firmarán los archivos cuyo número de páginas y "
            "dimensiones coincidan exactamente con la plantilla.\n\n"
            "**¿Solo tiene un PDF que firmar?** Seleccione ese mismo documento "
            "como plantilla: se comparará consigo mismo y superará siempre la "
            "validación."
        ),
        "pt": (
            "O modelo é o documento de referência: cada ficheiro a assinar é "
            "comparado com ele. Escolha o PDF em branco na origem dos seus "
            "documentos. As suas páginas servem também de pré-visualização ao "
            "posicionar a assinatura. Só serão assinados os ficheiros cujo "
            "número de páginas e dimensões correspondam exatamente ao modelo.\n\n"
            "**Só tem um PDF para assinar?** Selecione esse mesmo documento "
            "como modelo: será então comparado consigo próprio e passará sempre "
            "na validação."
        ),
    },
    "step.files.short": {
        "en": "Documents", "fr": "Documents", "nl": "Documenten",
        "de": "Dokumente", "es": "Documentos", "pt": "Documentos",
    },
    "step.files.title": {
        "en": "Select Documents",
        "fr": "Choisir les documents",
        "nl": "Documenten kiezen",
        "de": "Dokumente wählen",
        "es": "Seleccionar documentos",
        "pt": "Escolher os documentos",
    },
    "step.files.help": {
        "en": (
            "Choose every PDF document to sign in this run. The same "
            "signatures, positions and settings are applied to the whole "
            "batch. Documents that do not match the template are filtered out "
            "at the next step."
        ),
        "fr": (
            "Choisissez tous les documents PDF à signer dans ce lot. Les mêmes "
            "signatures, les mêmes positions et les mêmes réglages "
            "s'appliquent à tout le lot. Les documents qui ne correspondent "
            "pas au modèle seront écartés à l'étape suivante."
        ),
        "nl": (
            "Kies alle PDF-documenten die u in deze reeks wilt ondertekenen. "
            "Dezelfde handtekeningen, posities en instellingen gelden voor de "
            "hele reeks. Documenten die niet met het sjabloon overeenkomen, "
            "worden in de volgende stap uitgefilterd."
        ),
        "de": (
            "Wählen Sie alle PDF-Dokumente, die in diesem Durchgang signiert "
            "werden sollen. Dieselben Signaturen, Positionen und "
            "Einstellungen gelten für den gesamten Stapel. Dokumente, die "
            "nicht zur Vorlage passen, werden im nächsten Schritt aussortiert."
        ),
        "es": (
            "Elija todos los documentos PDF que desea firmar en este lote. Las "
            "mismas firmas, posiciones y ajustes se aplican a todo el lote. "
            "Los documentos que no coincidan con la plantilla se descartarán "
            "en el paso siguiente."
        ),
        "pt": (
            "Escolha todos os documentos PDF a assinar neste lote. As mesmas "
            "assinaturas, posições e definições aplicam-se a todo o lote. Os "
            "documentos que não correspondam ao modelo serão excluídos no "
            "passo seguinte."
        ),
    },
    "step.validate.short": {
        "en": "Validation", "fr": "Validation", "nl": "Validatie",
        "de": "Prüfung", "es": "Validación", "pt": "Validação",
    },
    "step.validate.title": {
        "en": "Validate & Summary",
        "fr": "Validation et récapitulatif",
        "nl": "Valideren en overzicht",
        "de": "Prüfung und Übersicht",
        "es": "Validación y resumen",
        "pt": "Validação e resumo",
    },
    "step.validate.help": {
        "en": (
            "Each document is compared with the template: ideally it has the "
            "same number of pages and, above all, exactly the same page sizes. "
            "If some documents do not have the same number of pages, a selector "
            "lets you specify **where** the signature goes: on their first or "
            "their last page. That page must have the **same dimensions** as "
            "the template. Documents with pages of a different size are "
            "rejected and will not be signed. Go back to adjust the template or "
            "the document list if needed. At least one valid document is "
            "required to continue."
        ),
        "fr": (
            "Chaque document est comparé au modèle : il est préférable d'avoir "
            "le même nombre de pages et surtout exactement les mêmes dimensions "
            "de pages. Si certains documents n'ont pas le même nombre de pages, "
            "un sélecteur permet de spécifier **où** placer la signature : sur "
            "sa première ou sa dernière page. Cette page doit avoir les **mêmes "
            "dimensions** que le modèle. Les documents avec des pages de "
            "tailles différentes sont rejetés et ne seront pas signés. Revenez "
            "en arrière pour ajuster le modèle ou la liste des documents si "
            "nécessaire. Au moins un document valide est requis pour continuer."
        ),
        "nl": (
            "Elk document wordt met het sjabloon vergeleken: idealiter heeft "
            "het hetzelfde aantal pagina's en vooral exact dezelfde "
            "paginagrootten. Als sommige documenten niet hetzelfde aantal "
            "pagina's hebben, kunt u met een keuzelijst aangeven **waar** de "
            "handtekening komt: op hun eerste of hun laatste pagina. Die pagina "
            "moet **dezelfde paginagrootte** hebben als het sjabloon. "
            "Documenten met pagina's van een andere grootte worden geweigerd en "
            "niet ondertekend. Ga zo nodig terug om het sjabloon of de "
            "documentlijst aan te passen. Er is minstens één geldig document "
            "nodig om verder te gaan."
        ),
        "de": (
            "Jedes Dokument wird mit der Vorlage verglichen: Idealerweise hat "
            "es dieselbe Seitenzahl und vor allem exakt dieselben Seitengrößen. "
            "Haben einige Dokumente nicht dieselbe Seitenzahl, können Sie über "
            "ein Auswahlfeld festlegen, **wo** die Signatur platziert wird: auf "
            "ihrer ersten oder ihrer letzten Seite. Diese Seite muss **dieselbe "
            "Seitengröße** wie die Vorlage haben. Dokumente mit Seiten "
            "abweichender Größe werden abgelehnt und nicht signiert. Gehen Sie "
            "bei Bedarf zurück, um die Vorlage oder die Dokumentliste "
            "anzupassen. Mindestens ein gültiges Dokument ist erforderlich, um "
            "fortzufahren."
        ),
        "es": (
            "Cada documento se compara con la plantilla: es preferible que "
            "tenga el mismo número de páginas y, sobre todo, exactamente las "
            "mismas dimensiones de página. Si algunos documentos no tienen el "
            "mismo número de páginas, un selector permite especificar **dónde** "
            "colocar la firma: en su primera o en su última página. Esa página "
            "debe tener las **mismas dimensiones** que la plantilla. Los "
            "documentos con páginas de tamaño distinto se rechazan y no se "
            "firmarán. Vuelva atrás para ajustar la plantilla o la lista de "
            "documentos si es necesario. Se necesita al menos un documento "
            "válido para continuar."
        ),
        "pt": (
            "Cada documento é comparado com o modelo: é preferível que tenha o "
            "mesmo número de páginas e, sobretudo, exatamente as mesmas "
            "dimensões de página. Se alguns documentos não tiverem o mesmo "
            "número de páginas, um seletor permite especificar **onde** colocar "
            "a assinatura: na sua primeira ou na sua última página. Essa página "
            "deve ter as **mesmas dimensões** que o modelo. Os documentos com "
            "páginas de dimensões diferentes são rejeitados e não serão "
            "assinados. Volte atrás para ajustar o modelo ou a lista de "
            "documentos, se necessário. É preciso pelo menos um documento "
            "válido para continuar."
        ),
    },
    "step.output.short": {
        "en": "Output", "fr": "Sortie", "nl": "Uitvoer",
        "de": "Ausgabe", "es": "Salida", "pt": "Saída",
    },
    "step.output.title": {
        "en": "Select Output Folder",
        "fr": "Choisir le dossier de sortie",
        "nl": "Uitvoermap kiezen",
        "de": "Ausgabeordner wählen",
        "es": "Seleccionar carpeta de salida",
        "pt": "Escolher a pasta de saída",
    },
    "step.output.help": {
        "en": (
            "Choose the folder where the signed documents will be written. "
            "Each result keeps the original name with the suffix “_signe”. "
            "Existing files are never overwritten: if a name is already "
            "taken, a numbered variant is created instead."
        ),
        "fr": (
            "Choisissez le dossier où les documents signés seront écrits. "
            "Chaque résultat garde le nom d'origine suivi du suffixe "
            "« _signe ». Les fichiers existants ne sont jamais écrasés : si "
            "un nom est déjà pris, une variante numérotée est créée."
        ),
        "nl": (
            "Kies de map waarin de ondertekende documenten worden "
            "weggeschreven. Elk resultaat behoudt de oorspronkelijke naam met "
            "het achtervoegsel “_signe”. Bestaande bestanden worden nooit "
            "overschreven: is een naam al in gebruik, dan wordt een "
            "genummerde variant aangemaakt."
        ),
        "de": (
            "Wählen Sie den Ordner, in den die signierten Dokumente "
            "geschrieben werden. Jedes Ergebnis behält den ursprünglichen "
            "Namen mit dem Suffix „_signe“. Bestehende Dateien werden nie "
            "überschrieben: Ist ein Name bereits vergeben, wird eine "
            "nummerierte Variante erstellt."
        ),
        "es": (
            "Elija la carpeta donde se escribirán los documentos firmados. "
            "Cada resultado conserva el nombre original con el sufijo "
            "«_signe». Los archivos existentes nunca se sobrescriben: si un "
            "nombre ya está ocupado, se crea una variante numerada."
        ),
        "pt": (
            "Escolha a pasta onde os documentos assinados serão gravados. "
            "Cada resultado mantém o nome original com o sufixo «_signe». Os "
            "ficheiros existentes nunca são substituídos: se um nome já "
            "estiver ocupado, é criada uma variante numerada."
        ),
    },
    "step.mode.short": {
        "en": "Signature", "fr": "Signature", "nl": "Handtekening",
        "de": "Signatur", "es": "Firma", "pt": "Assinatura",
    },
    "step.mode.title": {
        "en": "Select Signature Type",
        "fr": "Choisir le type de signature",
        "nl": "Handtekeningtype kiezen",
        "de": "Signaturtyp wählen",
        "es": "Seleccionar tipo de firma",
        "pt": "Escolher o tipo de assinatura",
    },
    "step.mode.help": {
        "en": (
            "eID (QES) — a qualified signature with your Belgian identity card, "
            "legally equivalent to a handwritten one. Needs a card reader and "
            "one PIN entry per document; your national register number is "
            "embedded in every signature.\n\n"
            "Azure (AES) — an advanced signature with your personal certificate "
            "in Azure Key Vault. One Microsoft sign-in for the whole batch, no "
            "card; only the document fingerprint ever leaves this machine.\n\n"
            "Visual signature — stamps typed text, an image or a drawing on "
            "the pages. NOT a cryptographic signature, no legal value; works "
            "fully offline.\n\n"
            "In every mode, visual signatures are added and placed on the "
            "next step. With eID or Azure they are applied first, and the "
            "cryptographic signature then covers them.\n\n"
            "The PAdES level sets durability. Keep b-lta: the signature stays "
            "verifiable for decades (needs network). Choose b-b only to sign "
            "offline. The full documentation — modes, levels, glossary and "
            "links to the source standards — is available under “Full "
            "documentation”."
        ),
        "fr": (
            "eID (QES) — signature qualifiée avec votre carte d'identité belge, "
            "juridiquement équivalente à une signature manuscrite. Nécessite un "
            "lecteur de carte et un code PIN par document ; votre numéro de "
            "registre national est intégré dans chaque signature.\n\n"
            "Azure (AES) — signature avancée avec votre certificat personnel "
            "dans Azure Key Vault. Une seule connexion Microsoft pour tout le "
            "lot, sans carte ; seule l'empreinte du document quitte cette "
            "machine.\n\n"
            "Signature visuelle — appose un texte saisi, une image ou un "
            "dessin sur les pages. Ce N'EST PAS une signature "
            "cryptographique, sans valeur juridique ; fonctionne entièrement "
            "hors ligne.\n\n"
            "Dans tous les modes, les signatures visuelles s'ajoutent et se "
            "positionnent à l'étape suivante. Avec eID ou Azure, elles sont "
            "apposées en premier, puis la signature cryptographique les "
            "couvre.\n\n"
            "Le niveau PAdES règle la durabilité. Gardez b-lta : la signature "
            "reste vérifiable pendant des décennies (réseau requis). Choisissez "
            "b-b uniquement pour signer hors ligne. La documentation complète — "
            "modes, niveaux, glossaire et liens vers les normes de référence — "
            "est disponible sous « Documentation complète »."
        ),
        "nl": (
            "eID (QES) — een gekwalificeerde handtekening met uw Belgische "
            "identiteitskaart, juridisch gelijkwaardig aan een handgeschreven "
            "handtekening. Vereist een kaartlezer en één pincode per document; "
            "uw rijksregisternummer wordt in elke handtekening opgenomen.\n\n"
            "Azure (AES) — een geavanceerde handtekening met uw persoonlijke "
            "certificaat in Azure Key Vault. Eén Microsoft-aanmelding voor de "
            "hele reeks, zonder kaart; alleen de vingerafdruk van het document "
            "verlaat deze computer.\n\n"
            "Visuele handtekening — brengt getypte tekst, een afbeelding of "
            "een tekening aan op de pagina's. GEEN cryptografische "
            "handtekening, zonder juridische waarde; werkt volledig offline.\n\n"
            "In elke modus voegt u visuele handtekeningen toe en plaatst u ze "
            "in de volgende stap. Met eID of Azure worden ze eerst "
            "aangebracht; de cryptografische handtekening omvat ze daarna.\n\n"
            "Het PAdES-niveau bepaalt de duurzaamheid. Behoud b-lta: de "
            "handtekening blijft tientallen jaren verifieerbaar (netwerk "
            "vereist). Kies b-b alleen om offline te ondertekenen. De volledige "
            "documentatie — modi, niveaus, woordenlijst en links naar de "
            "onderliggende normen — is beschikbaar onder 'Volledige "
            "documentatie'."
        ),
        "de": (
            "eID (QES) — eine qualifizierte Signatur mit Ihrer belgischen "
            "Identitätskarte, rechtlich der handschriftlichen Unterschrift "
            "gleichgestellt. Benötigt einen Kartenleser und eine PIN-Eingabe "
            "pro Dokument; Ihre nationale Registernummer wird in jede Signatur "
            "eingebettet.\n\n"
            "Azure (AES) — eine fortgeschrittene Signatur mit Ihrem "
            "persönlichen Zertifikat in Azure Key Vault. Eine einzige "
            "Microsoft-Anmeldung für den ganzen Stapel, ohne Karte; nur der "
            "Fingerabdruck des Dokuments verlässt diesen Rechner.\n\n"
            "Visuelle Signatur — stempelt getippten Text, ein Bild oder eine "
            "Zeichnung auf die Seiten. KEINE kryptografische Signatur, ohne "
            "Rechtswert; funktioniert vollständig offline.\n\n"
            "In jedem Modus werden visuelle Signaturen im nächsten Schritt "
            "hinzugefügt und platziert. Mit eID oder Azure werden sie zuerst "
            "aufgebracht; die kryptografische Signatur deckt sie anschließend "
            "mit ab.\n\n"
            "Das PAdES-Niveau bestimmt die Haltbarkeit. Behalten Sie b-lta: Die "
            "Signatur bleibt über Jahrzehnte prüfbar (Netzwerk erforderlich). "
            "Wählen Sie b-b nur, um offline zu signieren. Die vollständige "
            "Dokumentation — Modi, Niveaus, Glossar und Links zu den zugrunde "
            "liegenden Standards — ist unter „Vollständige Dokumentation“ "
            "verfügbar."
        ),
        "es": (
            "eID (QES) — firma cualificada con su tarjeta de identidad belga, "
            "legalmente equivalente a la manuscrita. Requiere un lector de "
            "tarjetas y un PIN por documento; su número de registro nacional se "
            "incrusta en cada firma.\n\n"
            "Azure (AES) — firma avanzada con su certificado personal en Azure "
            "Key Vault. Un solo inicio de sesión de Microsoft para todo el "
            "lote, sin tarjeta; solo la huella del documento sale de este "
            "equipo.\n\n"
            "Firma visual — estampa un texto escrito, una imagen o un dibujo "
            "en las páginas. NO es una firma criptográfica, sin valor legal; "
            "funciona totalmente sin conexión.\n\n"
            "En todos los modos, las firmas visuales se añaden y se colocan "
            "en el paso siguiente. Con eID o Azure se aplican primero, y la "
            "firma criptográfica las cubre después.\n\n"
            "El nivel PAdES define la durabilidad. Mantenga b-lta: la firma "
            "seguirá siendo verificable durante décadas (requiere red). Elija "
            "b-b solo para firmar sin conexión. La documentación completa — "
            "modos, niveles, glosario y enlaces a las normas de referencia — "
            "está disponible en «Documentación completa»."
        ),
        "pt": (
            "eID (QES) — assinatura qualificada com o seu cartão de identidade "
            "belga, juridicamente equivalente à manuscrita. Requer um leitor de "
            "cartões e um PIN por documento; o seu número de registo nacional é "
            "incorporado em cada assinatura.\n\n"
            "Azure (AES) — assinatura avançada com o seu certificado pessoal no "
            "Azure Key Vault. Um único início de sessão Microsoft para todo o "
            "lote, sem cartão; apenas a impressão digital do documento sai "
            "deste computador.\n\n"
            "Assinatura visual — aplica um texto escrito, uma imagem ou um "
            "desenho nas páginas. NÃO é uma assinatura criptográfica, sem "
            "valor legal; funciona totalmente offline.\n\n"
            "Em todos os modos, as assinaturas visuais são adicionadas e "
            "posicionadas no passo seguinte. Com eID ou Azure, são aplicadas "
            "primeiro, e a assinatura criptográfica abrange-as em seguida.\n\n"
            "O nível PAdES define a durabilidade. Mantenha b-lta: a assinatura "
            "permanece verificável durante décadas (requer rede). Escolha b-b "
            "apenas para assinar offline. A documentação completa — modos, "
            "níveis, glossário e ligações para as normas de referência — está "
            "disponível em «Documentação completa»."
        ),
    },
    "step.place.short": {
        "en": "Placement", "fr": "Position", "nl": "Plaatsing",
        "de": "Platzierung", "es": "Posición", "pt": "Posição",
    },
    "step.place.title": {
        "en": "Configure Signature Placement",
        "fr": "Positionner la signature",
        "nl": "Plaatsing van de handtekening",
        "de": "Signatur platzieren",
        "es": "Configurar la posición de la firma",
        "pt": "Configurar a posição da assinatura",
    },
    "step.place.help": {
        "en": (
            "Build the list of elements stamped on every document, then place "
            "each one.\n\n"
            "**Elements** — with **eID and Azure** the list starts with the "
            "signature vignette (photo, name, date): it goes bottom-right on "
            "the last page unless you place it. Add as many visual signatures "
            "as you need with **Add text**, **Add image** or **Draw**; untick "
            "one to keep it in your list without using it.\n\n"
            "**Placing** — select an element in the list, then click on the "
            "page preview: the click sets its lower-left corner. The preview "
            "shows every element at its real size. **On every page** repeats a "
            "signature on all pages (initials). In a text, {date} and "
            "{filename} are replaced for each document, so the size of that "
            "text can vary slightly.\n\n"
            "**Target page** — the page that receives the selected element. It "
            "follows the preview; type another page number to move the "
            "element.\n\n"
            "**Documents with a different page count** — when some documents "
            "have more or fewer pages than the template, a selector at the top "
            "of this step lets you choose whether they are signed on their "
            "**first** or their **last** page (the same choice as on the "
            "validation step). Every element then goes on that page: the "
            "preview is locked on the corresponding template page, the "
            "target-page field and \"On every page\" are disabled, and "
            "changing the choice re-checks the documents.\n\n"
            "**Saved for next time** — your signatures, their last position "
            "and your preferences are stored **unencrypted** in your user "
            "folder and reloaded at the next start. **Delete** removes a "
            "signature and its stored image. PIN codes and sign-in tokens are "
            "never stored."
        ),
        "fr": (
            "Composez la liste des éléments apposés sur chaque document, puis "
            "placez chacun d'eux.\n\n"
            "**Éléments** — avec **eID et Azure**, la liste commence par la "
            "vignette de signature (photo, nom, date) : elle ira en bas à "
            "droite de la dernière page si vous ne la placez pas. Ajoutez "
            "autant de signatures visuelles que nécessaire avec **Ajouter un "
            "texte**, **Ajouter une image** ou **Dessiner** ; décochez-en une "
            "pour la garder dans votre liste sans l'utiliser.\n\n"
            "**Placement** — sélectionnez un élément dans la liste, puis "
            "cliquez sur l'aperçu de la page : le clic définit son coin "
            "inférieur gauche. L'aperçu montre chaque élément à sa taille "
            "réelle. **Sur toutes les pages** répète une signature sur chacune "
            "des pages (paraphe). Dans un texte, {date} et {filename} sont "
            "remplacés pour chaque document ; la taille de ce texte peut donc "
            "varier légèrement.\n\n"
            "**Page cible** — la page qui reçoit l'élément sélectionné. Elle "
            "suit l'aperçu ; saisissez un autre numéro de page pour déplacer "
            "l'élément.\n\n"
            "**Documents dont le nombre de pages diffère** — lorsque certains "
            "documents comptent plus ou moins de pages que le modèle, un "
            "sélecteur en haut de cette étape permet de choisir s'ils sont "
            "signés sur leur **première** ou leur **dernière** page (le même "
            "choix qu'à l'étape de validation). Tous les éléments vont alors "
            "sur cette page : l'aperçu est verrouillé sur la page "
            "correspondante du modèle, le champ « page cible » et « Sur toutes "
            "les pages » sont désactivés, et tout changement de ce choix "
            "revérifie les documents.\n\n"
            "**Mémorisé pour la prochaine fois** — vos signatures, leur "
            "dernière position et vos préférences sont enregistrées **sans "
            "chiffrement** dans votre dossier utilisateur et rechargées au "
            "prochain démarrage. **Supprimer** retire une signature et son "
            "image stockée. Les codes PIN et les jetons de connexion ne sont "
            "jamais enregistrés."
        ),
        "nl": (
            "Stel de lijst samen van de elementen die op elk document worden "
            "aangebracht en plaats ze daarna één voor één.\n\n"
            "**Elementen** — met **eID en Azure** begint de lijst met het "
            "handtekeningvignet (foto, naam, datum): het komt rechtsonder op "
            "de laatste pagina, tenzij u het zelf plaatst. Voeg zoveel visuele "
            "handtekeningen toe als u nodig hebt met **Tekst toevoegen**, "
            "**Afbeelding toevoegen** of **Tekenen**; vink er een uit om ze in "
            "uw lijst te bewaren zonder ze te gebruiken.\n\n"
            "**Plaatsen** — selecteer een element in de lijst en klik daarna "
            "op het paginavoorbeeld: de klik bepaalt de linkerbenedenhoek "
            "ervan. Het voorbeeld toont elk element op ware grootte. **Op elke "
            "pagina** herhaalt een handtekening op alle pagina's (paraaf). In "
            "een tekst worden {date} en {filename} voor elk document "
            "vervangen; de grootte van die tekst kan daardoor licht "
            "variëren.\n\n"
            "**Doelpagina** — de pagina die het geselecteerde element krijgt. "
            "Dit veld volgt het voorbeeld; typ een ander paginanummer om het "
            "element te verplaatsen.\n\n"
            "**Documenten met een ander aantal pagina's** — wanneer sommige "
            "documenten meer of minder pagina's hebben dan het sjabloon, kunt u "
            "met een keuzelijst bovenaan deze stap kiezen of ze op hun "
            "**eerste** of hun **laatste** pagina worden ondertekend (dezelfde "
            "keuze als in de validatiestap). Elk element komt dan op die "
            "pagina: het voorbeeld is vergrendeld op de overeenkomstige "
            "sjabloonpagina, het veld 'Doelpagina' en 'Op elke pagina' zijn "
            "uitgeschakeld en als u de keuze wijzigt, worden de documenten "
            "opnieuw gecontroleerd.\n\n"
            "**Bewaard voor de volgende keer** — uw handtekeningen, hun "
            "laatste positie en uw voorkeuren worden **onversleuteld** in uw "
            "gebruikersmap opgeslagen en bij de volgende start opnieuw "
            "geladen. **Verwijderen** wist een handtekening en de bijbehorende "
            "opgeslagen afbeelding. Pincodes en aanmeldtokens worden nooit "
            "opgeslagen."
        ),
        "de": (
            "Stellen Sie die Liste der Elemente zusammen, die auf jedes "
            "Dokument gestempelt werden, und platzieren Sie dann jedes "
            "einzelne.\n\n"
            "**Elemente** — bei **eID und Azure** beginnt die Liste mit der "
            "Signaturvignette (Foto, Name, Datum): Sie kommt unten rechts auf "
            "die letzte Seite, sofern Sie sie nicht selbst platzieren. Fügen "
            "Sie mit **Text hinzufügen**, **Bild hinzufügen** oder "
            "**Zeichnen** beliebig viele visuelle Signaturen hinzu; entfernen "
            "Sie das Häkchen, um eine Signatur in Ihrer Liste zu behalten, "
            "ohne sie zu verwenden.\n\n"
            "**Platzieren** — wählen Sie ein Element in der Liste aus und "
            "klicken Sie dann auf die Seitenvorschau: Der Klick legt seine "
            "linke untere Ecke fest. Die Vorschau zeigt jedes Element in "
            "Originalgröße. **Auf jeder Seite** wiederholt eine Signatur auf "
            "allen Seiten (Paraphe). In einem Text werden {date} und "
            "{filename} für jedes Dokument ersetzt; die Größe dieses Textes "
            "kann daher leicht variieren.\n\n"
            "**Zielseite** — die Seite, die das ausgewählte Element erhält. "
            "Sie folgt der Vorschau; geben Sie eine andere Seitenzahl ein, um "
            "das Element zu verschieben.\n\n"
            "**Dokumente mit abweichender Seitenzahl** — haben einige Dokumente "
            "mehr oder weniger Seiten als die Vorlage, können Sie über ein "
            "Auswahlfeld oben in diesem Schritt festlegen, ob sie auf ihrer "
            "**ersten** oder ihrer **letzten** Seite signiert werden (dieselbe "
            "Wahl wie im Prüfschritt). Alle Elemente kommen dann auf diese "
            "Seite: Die Vorschau ist auf der entsprechenden Vorlagenseite "
            "gesperrt, das Feld „Zielseite“ und „Auf jeder Seite“ sind "
            "deaktiviert, und bei einer Änderung der Wahl werden die Dokumente "
            "erneut geprüft.\n\n"
            "**Für das nächste Mal gespeichert** — Ihre Signaturen, deren "
            "letzte Position und Ihre Einstellungen werden **unverschlüsselt** "
            "in Ihrem Benutzerordner gespeichert und beim nächsten Start "
            "wieder geladen. **Löschen** entfernt eine Signatur und ihr "
            "gespeichertes Bild. PINs und Anmeldetoken werden niemals "
            "gespeichert."
        ),
        "es": (
            "Componga la lista de elementos que se estampan en cada documento "
            "y, después, coloque cada uno.\n\n"
            "**Elementos** — con **eID y Azure** la lista empieza por la viñeta "
            "de firma (foto, nombre, fecha): irá abajo a la derecha en la "
            "última página, salvo que usted la coloque. Añada tantas firmas "
            "visuales como necesite con **Añadir texto**, **Añadir imagen** o "
            "**Dibujar**; desmarque una para conservarla en su lista sin "
            "utilizarla.\n\n"
            "**Colocación** — seleccione un elemento de la lista y luego haga "
            "clic en la vista previa de la página: el clic fija su esquina "
            "inferior izquierda. La vista previa muestra cada elemento a su "
            "tamaño real. **En todas las páginas** repite una firma en cada "
            "una de las páginas (rúbrica). En un texto, {date} y {filename} se "
            "sustituyen en cada documento, por lo que el tamaño de ese texto "
            "puede variar ligeramente.\n\n"
            "**Página de destino** — la página que recibe el elemento "
            "seleccionado. Se actualiza con la vista previa; escriba otro "
            "número de página para mover el elemento.\n\n"
            "**Documentos con un número de páginas distinto** — cuando algunos "
            "documentos tienen más o menos páginas que la plantilla, un "
            "selector en la parte superior de este paso le permite elegir si "
            "se firman en su **primera** o en su **última** página (la misma "
            "elección que en el paso de validación). Todos los elementos van "
            "entonces a esa página: la vista previa queda bloqueada en la "
            "página correspondiente de la plantilla, el campo «Página de "
            "destino» y «En todas las páginas» se desactivan, y cambiar la "
            "elección vuelve a comprobar los documentos.\n\n"
            "**Guardado para la próxima vez** — sus firmas, su última posición "
            "y sus preferencias se guardan **sin cifrar** en su carpeta de "
            "usuario y se vuelven a cargar en el siguiente inicio. "
            "**Eliminar** borra una firma y su imagen almacenada. Los códigos "
            "PIN y los tokens de inicio de sesión nunca se guardan."
        ),
        "pt": (
            "Componha a lista dos elementos aplicados em cada documento e, "
            "depois, posicione cada um.\n\n"
            "**Elementos** — com **eID e Azure**, a lista começa pela vinheta "
            "de assinatura (fotografia, nome, data): ficará em baixo à direita "
            "na última página, a menos que a posicione. Adicione as "
            "assinaturas visuais de que precisar com **Adicionar texto**, "
            "**Adicionar imagem** ou **Desenhar**; desmarque uma para a manter "
            "na lista sem a utilizar.\n\n"
            "**Posicionamento** — selecione um elemento na lista e depois "
            "clique na pré-visualização da página: o clique define o seu canto "
            "inferior esquerdo. A pré-visualização mostra cada elemento no "
            "tamanho real. **Em todas as páginas** repete uma assinatura em "
            "cada uma das páginas (rubrica). Num texto, {date} e {filename} "
            "são substituídos em cada documento, pelo que o tamanho desse "
            "texto pode variar ligeiramente.\n\n"
            "**Página de destino** — a página que recebe o elemento "
            "selecionado. Segue a pré-visualização; escreva outro número de "
            "página para mover o elemento.\n\n"
            "**Documentos com um número de páginas diferente** — quando alguns "
            "documentos têm mais ou menos páginas do que o modelo, um seletor "
            "no topo deste passo permite escolher se são assinados na sua "
            "**primeira** ou na sua **última** página (a mesma escolha que no "
            "passo de validação). Todos os elementos ficam então nessa "
            "página: a pré-visualização fica bloqueada na página "
            "correspondente do modelo, o campo «página de destino» e «Em todas "
            "as páginas» ficam desativados, e alterar a escolha volta a "
            "verificar os documentos.\n\n"
            "**Guardado para a próxima vez** — as suas assinaturas, a "
            "respetiva última posição e as suas preferências são guardadas "
            "**sem encriptação** na sua pasta de utilizador e recarregadas no "
            "arranque seguinte. **Eliminar** remove uma assinatura e a "
            "respetiva imagem armazenada. Os códigos PIN e os tokens de início "
            "de sessão nunca são guardados."
        ),
    },
    "step.run.short": {
        "en": "Signing", "fr": "Signature", "nl": "Ondertekenen",
        "de": "Signieren", "es": "Firmar", "pt": "Assinar",
    },
    "step.run.title": {
        "en": "Apply Signatures",
        "fr": "Signer les documents",
        "nl": "Handtekeningen toepassen",
        "de": "Signaturen anwenden",
        "es": "Aplicar las firmas",
        "pt": "Aplicar as assinaturas",
    },
    "step.run.help": {
        "en": (
            "Check the summary, then start the batch. **eID**: insert your "
            "identity card in the reader before pressing Start — your PIN is "
            "asked once per document. **Azure**: a Microsoft sign-in window may "
            "open if you have not signed in yet. Levels above b-b contact the "
            "timestamp authority and revocation services, so network access is "
            "required. Keep the window open while signing is in progress. "
            "Visual signatures are stamped first; the cryptographic signature "
            "(eID or Azure) is applied last and covers them."
        ),
        "fr": (
            "Vérifiez le récapitulatif, puis lancez le lot. **eID** : insérez "
            "votre carte d'identité dans le lecteur avant d'appuyer sur « "
            "Lancer la signature » — votre code PIN est demandé une fois par "
            "document. **Azure** : une fenêtre de connexion Microsoft peut "
            "s'ouvrir si vous n'êtes pas encore connecté. Au-delà de b-b, "
            "l'autorité d'horodatage et les services de révocation sont "
            "contactés : un accès réseau est requis. Gardez la fenêtre ouverte "
            "pendant la signature. Les signatures visuelles sont apposées en "
            "premier ; la signature cryptographique (eID ou Azure) est "
            "appliquée en dernier et les couvre."
        ),
        "nl": (
            "Controleer het overzicht en start de reeks. **eID**: steek uw "
            "identiteitskaart in de kaartlezer voordat u op 'Ondertekenen "
            "starten' drukt — uw pincode wordt één keer per document gevraagd. "
            "**Azure**: er kan een Microsoft-aanmeldvenster verschijnen als u "
            "nog niet bent aangemeld. Boven b-b worden de tijdstempelautoriteit "
            "en de intrekkingsdiensten gecontacteerd: netwerktoegang is "
            "vereist. Houd het venster open zolang het ondertekenen bezig is. "
            "Visuele handtekeningen worden eerst aangebracht; de "
            "cryptografische handtekening (eID of Azure) komt als laatste en "
            "omvat ze."
        ),
        "de": (
            "Prüfen Sie die Übersicht und starten Sie den Stapel. **eID**: "
            "Stecken Sie Ihre Identitätskarte in den Kartenleser, bevor Sie auf "
            "„Signieren starten“ klicken — Ihre PIN wird einmal pro Dokument "
            "abgefragt. **Azure**: Ein Microsoft-Anmeldefenster kann sich "
            "öffnen, falls Sie noch nicht angemeldet sind. Oberhalb von b-b "
            "werden Zeitstempel- und Sperrdienste kontaktiert: Netzwerkzugang "
            "ist erforderlich. Lassen Sie das Fenster geöffnet, solange "
            "signiert wird. Visuelle Signaturen werden zuerst aufgebracht; die "
            "kryptografische Signatur (eID oder Azure) folgt zuletzt und deckt "
            "sie mit ab."
        ),
        "es": (
            "Compruebe el resumen y lance el lote. **eID**: inserte su tarjeta "
            "de identidad en el lector antes de pulsar «Iniciar la firma» — el "
            "PIN se le pedirá una vez por documento. **Azure**: puede abrirse "
            "una ventana de inicio de sesión de Microsoft si aún no ha iniciado "
            "sesión. Por encima de b-b se contacta con la autoridad de sellado "
            "de tiempo y los servicios de revocación: se necesita acceso a la "
            "red. Mantenga la ventana abierta mientras se firma. Las firmas "
            "visuales se estampan primero; la firma criptográfica (eID o "
            "Azure) se aplica al final y las cubre."
        ),
        "pt": (
            "Verifique o resumo e inicie o lote. **eID**: insira o seu cartão "
            "de identidade no leitor antes de premir «Iniciar a assinatura» — o "
            "PIN é pedido uma vez por documento. **Azure**: pode abrir-se uma "
            "janela de início de sessão Microsoft se ainda não tiver iniciado "
            "sessão. Acima de b-b são contactados a autoridade de selos "
            "temporais e os serviços de revogação: é necessário acesso à rede. "
            "Mantenha a janela aberta enquanto a assinatura decorre. As "
            "assinaturas visuais são aplicadas primeiro; a assinatura "
            "criptográfica (eID ou Azure) é aplicada por último e abrange-as."
        ),
    },
    "step.results.short": {
        "en": "Report", "fr": "Rapport", "nl": "Rapport",
        "de": "Bericht", "es": "Informe", "pt": "Relatório",
    },
    "step.results.title": {
        "en": "Results Report",
        "fr": "Rapport des résultats",
        "nl": "Resultatenrapport",
        "de": "Ergebnisbericht",
        "es": "Informe de resultados",
        "pt": "Relatório de resultados",
    },
    "step.results.help": {
        "en": (
            "Every document of the batch is listed with its outcome. Successful "
            "lines show the achieved signature level and, where applicable, the "
            "long-term-validation status. Failed lines explain the reason; "
            "those documents were not signed. **Open output folder** shows the "
            "signed files in your file manager. Finish returns to the welcome "
            "screen."
        ),
        "fr": (
            "Chaque document du lot est listé avec son résultat. Les lignes "
            "réussies indiquent le niveau de signature atteint et, le cas "
            "échéant, l'état de la validation à long terme. Les lignes en échec "
            "expliquent la raison ; ces documents n'ont pas été signés. "
            "**Ouvrir le dossier de sortie** affiche les fichiers signés dans "
            "votre gestionnaire de fichiers. « Terminer » revient à l'écran "
            "d'accueil."
        ),
        "nl": (
            "Elk document van de reeks staat vermeld met zijn resultaat. "
            "Geslaagde regels tonen het bereikte handtekeningniveau en, indien "
            "van toepassing, de status van de langetermijnvalidatie. Mislukte "
            "regels geven de reden; die documenten zijn niet ondertekend. "
            "**Uitvoermap openen** toont de ondertekende bestanden in uw "
            "bestandsbeheerder. 'Voltooien' keert terug naar het "
            "welkomstscherm."
        ),
        "de": (
            "Jedes Dokument des Stapels wird mit seinem Ergebnis aufgeführt. "
            "Erfolgreiche Zeilen zeigen das erreichte Signaturniveau und "
            "gegebenenfalls den Status der Langzeitvalidierung. Fehlgeschlagene "
            "Zeilen nennen den Grund; diese Dokumente wurden nicht signiert. "
            "**Ausgabeordner öffnen** zeigt die signierten Dateien in Ihrem "
            "Dateimanager an. „Fertigstellen“ kehrt zum Startbildschirm zurück."
        ),
        "es": (
            "Cada documento del lote aparece con su resultado. Las líneas "
            "correctas muestran el nivel de firma alcanzado y, en su caso, el "
            "estado de la validación a largo plazo. Las líneas con error "
            "explican el motivo; esos documentos no se firmaron. **Abrir "
            "carpeta de salida** muestra los archivos firmados en su explorador "
            "de archivos. «Finalizar» vuelve a la pantalla de bienvenida."
        ),
        "pt": (
            "Cada documento do lote é listado com o seu resultado. As linhas "
            "com êxito mostram o nível de assinatura alcançado e, quando "
            "aplicável, o estado da validação de longo prazo. As linhas "
            "falhadas explicam o motivo; esses documentos não foram assinados. "
            "**Abrir a pasta de saída** mostra os ficheiros assinados no seu "
            "gestor de ficheiros. «Concluir» regressa ao ecrã inicial."
        ),
    },
    # ------------------------------------------------------------- step 1
    "tpl.choose": {
        "en": "Choose template…",
        "fr": "Choisir le modèle…",
        "nl": "Sjabloon kiezen…",
        "de": "Vorlage wählen…",
        "es": "Elegir plantilla…",
        "pt": "Escolher modelo…",
    },
    "tpl.selected": {
        "en": "{name}  ({pages} pages)",
        "fr": "{name}  ({pages} pages)",
        "nl": "{name}  ({pages} pagina's)",
        "de": "{name}  ({pages} Seiten)",
        "es": "{name}  ({pages} páginas)",
        "pt": "{name}  ({pages} páginas)",
    },
    "tpl.unreadable": {
        "en": "Cannot read this PDF: {error}",
        "fr": "Impossible de lire ce PDF : {error}",
        "nl": "Kan deze PDF niet lezen: {error}",
        "de": "Diese PDF kann nicht gelesen werden: {error}",
        "es": "No se puede leer este PDF: {error}",
        "pt": "Não é possível ler este PDF: {error}",
    },
    "common.none": {
        "en": "(none selected)",
        "fr": "(aucune sélection)",
        "nl": "(niets geselecteerd)",
        "de": "(nichts ausgewählt)",
        "es": "(nada seleccionado)",
        "pt": "(nada selecionado)",
    },
    # ------------------------------------------------------------- step 2
    "files.choose": {
        "en": "Choose PDF files…",
        "fr": "Choisir les fichiers PDF…",
        "nl": "PDF-bestanden kiezen…",
        "de": "PDF-Dateien wählen…",
        "es": "Elegir archivos PDF…",
        "pt": "Escolher ficheiros PDF…",
    },
    "files.count": {
        "en": "{count} file(s) selected",
        "fr": "{count} fichier(s) sélectionné(s)",
        "nl": "{count} bestand(en) geselecteerd",
        "de": "{count} Datei(en) ausgewählt",
        "es": "{count} archivo(s) seleccionado(s)",
        "pt": "{count} ficheiro(s) selecionado(s)",
    },
    # ------------------------------------------------------------- step 3
    "val.revalidate": {
        "en": "Validate again",
        "fr": "Revalider",
        "nl": "Opnieuw valideren",
        "de": "Erneut prüfen",
        "es": "Validar de nuevo",
        "pt": "Validar novamente",
    },
    "val.col_file": {
        "en": "File", "fr": "Fichier", "nl": "Bestand",
        "de": "Datei", "es": "Archivo", "pt": "Ficheiro",
    },
    "val.col_result": {
        "en": "Result", "fr": "Résultat", "nl": "Resultaat",
        "de": "Ergebnis", "es": "Resultado", "pt": "Resultado",
    },
    "val.col_detail": {
        "en": "Detail", "fr": "Détail", "nl": "Detail",
        "de": "Detail", "es": "Detalle", "pt": "Detalhe",
    },
    "val.ok": {
        "en": "✓ OK", "fr": "✓ OK", "nl": "✓ OK",
        "de": "✓ OK", "es": "✓ OK", "pt": "✓ OK",
    },
    "val.rejected": {
        "en": "✗ rejected", "fr": "✗ rejeté", "nl": "✗ geweigerd",
        "de": "✗ abgelehnt", "es": "✗ rechazado", "pt": "✗ rejeitado",
    },
    "val.summary": {
        "en": "{ok}/{total} valid file(s).",
        "fr": "{ok}/{total} fichier(s) valide(s).",
        "nl": "{ok}/{total} geldig(e) bestand(en).",
        "de": "{ok}/{total} gültige Datei(en).",
        "es": "{ok}/{total} archivo(s) válido(s).",
        "pt": "{ok}/{total} ficheiro(s) válido(s).",
    },
    "val.anchor_label": {
        "en": "Some files have a different page count than the template — sign every file on:",
        "fr": "Certains fichiers n'ont pas le même nombre de pages que le modèle — signer chaque fichier sur :",
        "nl": "Sommige bestanden hebben een ander aantal pagina's dan het sjabloon — onderteken elk bestand op:",
        "de": "Einige Dateien haben eine andere Seitenzahl als die Vorlage — jede Datei signieren auf:",
        "es": "Algunos archivos tienen un número de páginas distinto de la plantilla — firmar cada archivo en:",
        "pt": "Alguns ficheiros têm um número de páginas diferente do modelo — assinar cada ficheiro:",
    },
    "anchor.opt_last": {
        "en": "its last page",
        "fr": "sa dernière page",
        "nl": "zijn laatste pagina",
        "de": "ihrer letzten Seite",
        "es": "su última página",
        "pt": "na última página",
    },
    "anchor.opt_first": {
        "en": "its first page",
        "fr": "sa première page",
        "nl": "zijn eerste pagina",
        "de": "ihrer ersten Seite",
        "es": "su primera página",
        "pt": "na primeira página",
    },
    "anchor.last_page": {
        "en": "last page",
        "fr": "dernière page",
        "nl": "laatste pagina",
        "de": "letzte Seite",
        "es": "última página",
        "pt": "última página",
    },
    "anchor.first_page": {
        "en": "first page",
        "fr": "première page",
        "nl": "eerste pagina",
        "de": "erste Seite",
        "es": "primera página",
        "pt": "primeira página",
    },
    "val.anchor_hint": {
        "en": "(the position is picked on that page at the placement step)",
        "fr": "(la position se choisit sur cette page à l'étape de positionnement)",
        "nl": "(de positie kiest u op die pagina in de plaatsingsstap)",
        "de": "(die Position wählen Sie auf dieser Seite im Platzierungsschritt)",
        "es": "(la posición se elige en esa página en el paso de colocación)",
        "pt": "(a posição escolhe-se nessa página no passo de posicionamento)",
    },
    "val.none_valid": {
        "en": "No document matches the template. Go back and adjust the selection.",
        "fr": "Aucun document ne correspond au modèle. Revenez en arrière pour ajuster la sélection.",
        "nl": "Geen enkel document komt met het sjabloon overeen. Ga terug en pas de selectie aan.",
        "de": "Kein Dokument entspricht der Vorlage. Gehen Sie zurück und passen Sie die Auswahl an.",
        "es": "Ningún documento coincide con la plantilla. Vuelva atrás y ajuste la selección.",
        "pt": "Nenhum documento corresponde ao modelo. Volte atrás e ajuste a seleção.",
    },
    # ------------------------------------------------------------- step 4
    "out.choose": {
        "en": "Choose output folder…",
        "fr": "Choisir le dossier de sortie…",
        "nl": "Uitvoermap kiezen…",
        "de": "Ausgabeordner wählen…",
        "es": "Elegir carpeta de salida…",
        "pt": "Escolher pasta de saída…",
    },
    # ------------------------------------------------------------- step 5
    "mode.beid": {
        "en": "eID card — qualified signature (QES)",
        "fr": "Carte eID — signature qualifiée (QES)",
        "nl": "eID-kaart — gekwalificeerde handtekening (QES)",
        "de": "eID-Karte — qualifizierte Signatur (QES)",
        "es": "Tarjeta eID — firma cualificada (QES)",
        "pt": "Cartão eID — assinatura qualificada (QES)",
    },
    "mode.azure": {
        "en": "Azure Key Vault — advanced signature (AES)",
        "fr": "Azure Key Vault — signature avancée (AES)",
        "nl": "Azure Key Vault — geavanceerde handtekening (AES)",
        "de": "Azure Key Vault — fortgeschrittene Signatur (AES)",
        "es": "Azure Key Vault — firma avanzada (AES)",
        "pt": "Azure Key Vault — assinatura avançada (AES)",
    },
    "mode.image": {
        "en": "Visual signature (text / image) — no cryptographic signature",
        "fr": "Signature visuelle (texte / image) — aucune signature cryptographique",
        "nl": "Visuele handtekening (tekst / afbeelding) — geen cryptografische handtekening",
        "de": "Visuelle Signatur (Text / Bild) — keine kryptografische Signatur",
        "es": "Firma visual (texto / imagen) — sin firma criptográfica",
        "pt": "Assinatura visual (texto / imagem) — sem assinatura criptográfica",
    },
    "mode.beid_hint": {
        "en": "Card reader + one PIN per document. Embeds your national register number (RRN) in every signature.",
        "fr": "Lecteur de carte + un PIN par document. Intègre votre numéro de registre national (RRN) dans chaque signature.",
        "nl": "Kaartlezer + één pincode per document. Neemt uw rijksregisternummer (RRN) in elke handtekening op.",
        "de": "Kartenleser + eine PIN pro Dokument. Bettet Ihre nationale Registernummer (RRN) in jede Signatur ein.",
        "es": "Lector de tarjetas + un PIN por documento. Incrusta su número de registro nacional (RRN) en cada firma.",
        "pt": "Leitor de cartões + um PIN por documento. Incorpora o seu número de registo nacional (RRN) em cada assinatura.",
    },
    "mode.azure_hint": {
        "en": "One Microsoft sign-in for the whole batch; needs network. Only the document digest leaves this machine.",
        "fr": "Une seule connexion Microsoft pour tout le lot ; réseau requis. Seule l'empreinte du document quitte cette machine.",
        "nl": "Eén Microsoft-aanmelding voor de hele reeks; netwerk vereist. Alleen de digest van het document verlaat deze computer.",
        "de": "Eine Microsoft-Anmeldung für den ganzen Stapel; Netzwerk erforderlich. Nur der Hashwert des Dokuments verlässt diesen Rechner.",
        "es": "Un solo inicio de sesión de Microsoft para todo el lote; requiere red. Solo la huella del documento sale de este equipo.",
        "pt": "Um único início de sessão Microsoft para todo o lote; requer rede. Apenas o resumo (digest) do documento sai deste computador.",
    },
    "mode.image_hint": {
        "en": (
            "A typed, drawn or image signature stamped on the pages — one or "
            "several per document. No legal value; works fully offline."
        ),
        "fr": (
            "Une signature saisie au clavier, dessinée ou sous forme d'image, "
            "apposée sur les pages — une ou plusieurs par document. Sans "
            "valeur juridique ; fonctionne entièrement hors ligne."
        ),
        "nl": (
            "Een getypte of getekende handtekening of een afbeelding, "
            "aangebracht op de pagina's — één of meerdere per document. Zonder "
            "juridische waarde; werkt volledig offline."
        ),
        "de": (
            "Eine getippte, gezeichnete oder als Bild vorliegende Signatur, "
            "auf die Seiten gestempelt — eine oder mehrere pro Dokument. Ohne "
            "Rechtswert; funktioniert vollständig offline."
        ),
        "es": (
            "Una firma escrita, dibujada o en forma de imagen, estampada en "
            "las páginas — una o varias por documento. Sin valor legal; "
            "funciona totalmente sin conexión."
        ),
        "pt": (
            "Uma assinatura escrita, desenhada ou em imagem, aplicada nas "
            "páginas — uma ou várias por documento. Sem valor legal; funciona "
            "totalmente offline."
        ),
    },
    "mode.stamps_hint": {
        "en": (
            "In every mode you can add visual signatures (text, image, "
            "drawing) on the placement step. With eID or Azure they are "
            "applied before the cryptographic signature."
        ),
        "fr": (
            "Dans tous les modes, vous pouvez ajouter des signatures visuelles "
            "(texte, image, dessin) à l'étape de positionnement. Avec eID ou "
            "Azure, elles sont apposées avant la signature cryptographique."
        ),
        "nl": (
            "In elke modus kunt u in de plaatsingsstap visuele handtekeningen "
            "(tekst, afbeelding, tekening) toevoegen. Met eID of Azure worden "
            "ze vóór de cryptografische handtekening aangebracht."
        ),
        "de": (
            "In jedem Modus können Sie im Platzierungsschritt visuelle "
            "Signaturen (Text, Bild, Zeichnung) hinzufügen. Mit eID oder Azure "
            "werden sie vor der kryptografischen Signatur aufgebracht."
        ),
        "es": (
            "En todos los modos puede añadir firmas visuales (texto, imagen, "
            "dibujo) en el paso de colocación. Con eID o Azure se aplican "
            "antes de la firma criptográfica."
        ),
        "pt": (
            "Em todos os modos pode adicionar assinaturas visuais (texto, "
            "imagem, desenho) no passo de posicionamento. Com eID ou Azure, "
            "são aplicadas antes da assinatura criptográfica."
        ),
    },
    "mode.level": {
        "en": "PAdES level:",
        "fr": "Niveau PAdES :",
        "nl": "PAdES-niveau:",
        "de": "PAdES-Niveau:",
        "es": "Nivel PAdES:",
        "pt": "Nível PAdES:",
    },
    "mode.level_hint": {
        "en": "Durability of the signature (eID and Azure). Keep b-lta — verifiable for decades; levels above b-b need internet.",
        "fr": "Durabilité de la signature (eID et Azure). Gardez b-lta — vérifiable pendant des décennies ; au-delà de b-b, Internet est requis.",
        "nl": "Duurzaamheid van de handtekening (eID en Azure). Behoud b-lta — tientallen jaren verifieerbaar; boven b-b is internet vereist.",
        "de": "Haltbarkeit der Signatur (eID und Azure). Behalten Sie b-lta — über Jahrzehnte prüfbar; oberhalb von b-b ist Internet erforderlich.",
        "es": "Durabilidad de la firma (eID y Azure). Mantenga b-lta — verificable durante décadas; por encima de b-b se necesita Internet.",
        "pt": "Durabilidade da assinatura (eID e Azure). Mantenha b-lta — verificável durante décadas; acima de b-b é necessária Internet.",
    },
    "azure.settings": {
        "en": "Azure settings",
        "fr": "Paramètres Azure",
        "nl": "Azure-instellingen",
        "de": "Azure-Einstellungen",
        "es": "Ajustes de Azure",
        "pt": "Definições do Azure",
    },
    "azure.vault": {
        "en": "Vault URL:",
        "fr": "URL du coffre :",
        "nl": "Vault-URL:",
        "de": "Vault-URL:",
        "es": "URL del almacén:",
        "pt": "URL do cofre:",
    },
    "azure.vault_hint": {
        "en": (
            "Required. Your organisation's Key Vault address (ask your "
            "administrator), e.g. https://name.vault.azure.net. The "
            "pre-filled https://login.live.com is the Microsoft sign-in "
            "page, NOT a vault — replace it."
        ),
        "fr": (
            "Obligatoire. L'adresse du Key Vault de votre organisation "
            "(demandez à votre administrateur), p. ex. "
            "https://nom.vault.azure.net. Le https://login.live.com "
            "prérempli est la page de connexion Microsoft, PAS un coffre — "
            "remplacez-le."
        ),
        "nl": (
            "Verplicht. Het Key Vault-adres van uw organisatie (vraag uw "
            "beheerder), bv. https://naam.vault.azure.net. Het vooraf "
            "ingevulde https://login.live.com is de Microsoft-aanmeldpagina, "
            "GEEN kluis — vervang het."
        ),
        "de": (
            "Erforderlich. Die Key-Vault-Adresse Ihrer Organisation (fragen "
            "Sie Ihre Administration), z. B. https://name.vault.azure.net. "
            "Das vorausgefüllte https://login.live.com ist die "
            "Microsoft-Anmeldeseite, KEIN Vault — ersetzen Sie es."
        ),
        "es": (
            "Obligatorio. La dirección del Key Vault de su organización "
            "(consulte a su administrador), p. ej. "
            "https://nombre.vault.azure.net. El https://login.live.com "
            "precargado es la página de inicio de sesión de Microsoft, NO un "
            "almacén — sustitúyalo."
        ),
        "pt": (
            "Obrigatório. O endereço do Key Vault da sua organização "
            "(pergunte ao administrador), p. ex. "
            "https://nome.vault.azure.net. O https://login.live.com "
            "pré-preenchido é a página de início de sessão Microsoft, NÃO um "
            "cofre — substitua-o."
        ),
    },
    "azure.key": {
        "en": "Key name (override):",
        "fr": "Nom de la clé (dérogation) :",
        "nl": "Sleutelnaam (overschrijven):",
        "de": "Schlüsselname (Überschreibung):",
        "es": "Nombre de la clave (anulación):",
        "pt": "Nome da chave (substituição):",
    },
    "azure.key_hint": {
        "en": (
            "Optional. Normally the key is derived from YOUR login "
            "(sig-<upn>), so you can only sign in your own name. Fill this "
            "only to use another key — the override is flagged in the run "
            "output."
        ),
        "fr": (
            "Facultatif. Normalement la clé est dérivée de VOTRE identifiant "
            "(sig-<upn>) : vous ne signez qu'en votre nom. Ne remplissez ceci "
            "que pour utiliser une autre clé — la dérogation est signalée "
            "dans le rapport."
        ),
        "nl": (
            "Optioneel. Normaal wordt de sleutel afgeleid van UW aanmelding "
            "(sig-<upn>), zodat u alleen in eigen naam kunt ondertekenen. Vul "
            "dit alleen in om een andere sleutel te gebruiken — de afwijking "
            "wordt in de uitvoer gemeld."
        ),
        "de": (
            "Optional. Normalerweise wird der Schlüssel aus IHRER Anmeldung "
            "abgeleitet (sig-<upn>), sodass Sie nur im eigenen Namen "
            "signieren. Nur ausfüllen, um einen anderen Schlüssel zu "
            "verwenden — die Überschreibung wird im Bericht vermerkt."
        ),
        "es": (
            "Opcional. Normalmente la clave se deriva de SU inicio de sesión "
            "(sig-<upn>), de modo que solo firma en su propio nombre. "
            "Rellénelo solo para usar otra clave — la anulación queda "
            "señalada en el informe."
        ),
        "pt": (
            "Opcional. Normalmente a chave deriva do SEU início de sessão "
            "(sig-<upn>), pelo que só assina em seu nome. Preencha apenas "
            "para usar outra chave — a substituição é assinalada no "
            "relatório."
        ),
    },
    "azure.anchors": {
        "en": "Internal CA chain (PEM)…",
        "fr": "Chaîne CA interne (PEM)…",
        "nl": "Interne CA-keten (PEM)…",
        "de": "Interne CA-Kette (PEM)…",
        "es": "Cadena de CA interna (PEM)…",
        "pt": "Cadeia da CA interna (PEM)…",
    },
    "azure.anchors_none": {
        "en": "(none chosen)",
        "fr": "(aucune choisie)",
        "nl": "(geen gekozen)",
        "de": "(keine gewählt)",
        "es": "(ninguna elegida)",
        "pt": "(nenhuma escolhida)",
    },
    "azure.anchors_hint": {
        "en": (
            "PEM/DER file with your organisation's internal CA chain (root + "
            "intermediates). Required for levels b-lt/b-lta and for "
            "post-signing verification. The EU trusted list is NOT used in "
            "Azure mode."
        ),
        "fr": (
            "Fichier PEM/DER contenant la chaîne CA interne de votre "
            "organisation (racine + intermédiaires). Requis pour les niveaux "
            "b-lt/b-lta et pour la vérification après signature. La liste de "
            "confiance de l'UE n'est PAS utilisée en mode Azure."
        ),
        "nl": (
            "PEM/DER-bestand met de interne CA-keten van uw organisatie "
            "(root + tussenliggende). Vereist voor de niveaus b-lt/b-lta en "
            "voor de verificatie na ondertekening. De vertrouwenslijst van de "
            "EU wordt in Azure-modus NIET gebruikt."
        ),
        "de": (
            "PEM/DER-Datei mit der internen CA-Kette Ihrer Organisation "
            "(Root + Zwischenzertifikate). Erforderlich für die Niveaus "
            "b-lt/b-lta und für die Prüfung nach dem Signieren. Die "
            "EU-Vertrauensliste wird im Azure-Modus NICHT verwendet."
        ),
        "es": (
            "Archivo PEM/DER con la cadena de CA interna de su organización "
            "(raíz + intermedias). Necesario para los niveles b-lt/b-lta y "
            "para la verificación posterior a la firma. La lista de confianza "
            "de la UE NO se usa en modo Azure."
        ),
        "pt": (
            "Ficheiro PEM/DER com a cadeia da CA interna da sua organização "
            "(raiz + intermédias). Necessário para os níveis b-lt/b-lta e "
            "para a verificação após a assinatura. A lista de confiança da UE "
            "NÃO é usada no modo Azure."
        ),
    },
    "azure.auth": {
        "en": "Sign-in method:",
        "fr": "Méthode de connexion :",
        "nl": "Aanmeldmethode:",
        "de": "Anmeldemethode:",
        "es": "Método de inicio de sesión:",
        "pt": "Método de início de sessão:",
    },
    "azure.auth_hint": {
        "en": (
            "'interactive' opens your browser (recommended); 'device-code' "
            "shows a code to type on another device; 'default' is for "
            "automation only. Signing in now is optional — otherwise it "
            "happens at launch."
        ),
        "fr": (
            "« interactive » ouvre votre navigateur (recommandé) ; "
            "« device-code » affiche un code à saisir sur un autre appareil ; "
            "« default » est réservé à l'automatisation. Se connecter "
            "maintenant est facultatif — sinon la connexion a lieu au "
            "lancement."
        ),
        "nl": (
            "'interactive' opent uw browser (aanbevolen); 'device-code' "
            "toont een code om op een ander apparaat in te voeren; 'default' "
            "is alleen voor automatisering. Nu aanmelden is optioneel — "
            "anders gebeurt het bij de start."
        ),
        "de": (
            "'interactive' öffnet Ihren Browser (empfohlen); 'device-code' "
            "zeigt einen Code für ein anderes Gerät; 'default' ist nur für "
            "Automatisierung. Die Anmeldung jetzt ist optional — sonst "
            "erfolgt sie beim Start."
        ),
        "es": (
            "'interactive' abre su navegador (recomendado); 'device-code' "
            "muestra un código para escribir en otro dispositivo; 'default' "
            "es solo para automatización. Iniciar sesión ahora es opcional — "
            "si no, ocurre al lanzar."
        ),
        "pt": (
            "'interactive' abre o navegador (recomendado); 'device-code' "
            "mostra um código para introduzir noutro dispositivo; 'default' é "
            "apenas para automatização. Iniciar sessão agora é opcional — "
            "caso contrário, acontece no arranque."
        ),
    },
    "azure.signin": {
        "en": "Sign in with Microsoft",
        "fr": "Se connecter avec Microsoft",
        "nl": "Aanmelden bij Microsoft",
        "de": "Mit Microsoft anmelden",
        "es": "Iniciar sesión con Microsoft",
        "pt": "Iniciar sessão com a Microsoft",
    },
    "azure.signing_in": {
        "en": "signing in…",
        "fr": "connexion…",
        "nl": "aanmelden…",
        "de": "Anmeldung läuft…",
        "es": "iniciando sesión…",
        "pt": "a iniciar sessão…",
    },
    "azure.signed_in": {
        "en": "signed in as {upn}",
        "fr": "connecté : {upn}",
        "nl": "aangemeld als {upn}",
        "de": "angemeldet als {upn}",
        "es": "sesión iniciada como {upn}",
        "pt": "sessão iniciada como {upn}",
    },
    "azure.not_signed_in": {
        "en": "(not signed in)",
        "fr": "(non connecté)",
        "nl": "(niet aangemeld)",
        "de": "(nicht angemeldet)",
        "es": "(sin sesión iniciada)",
        "pt": "(sem sessão iniciada)",
    },
    "azure.signin_failed": {
        "en": "Microsoft sign-in failed: {error}",
        "fr": "Échec de la connexion Microsoft : {error}",
        "nl": "Microsoft-aanmelding mislukt: {error}",
        "de": "Microsoft-Anmeldung fehlgeschlagen: {error}",
        "es": "Error al iniciar sesión con Microsoft: {error}",
        "pt": "Falha no início de sessão Microsoft: {error}",
    },
    "azure.vault_missing": {
        "en": "Azure mode needs the Key Vault URL.",
        "fr": "Le mode Azure requiert l'URL du Key Vault.",
        "nl": "De Azure-modus vereist de Key Vault-URL.",
        "de": "Der Azure-Modus benötigt die Key-Vault-URL.",
        "es": "El modo Azure necesita la URL del Key Vault.",
        "pt": "O modo Azure precisa do URL do Key Vault.",
    },
    "azure.anchors_missing": {
        "en": "Level {level} needs the internal CA chain (trust anchors).",
        "fr": "Le niveau {level} requiert la chaîne CA interne (ancres de confiance).",
        "nl": "Niveau {level} vereist de interne CA-keten (vertrouwensankers).",
        "de": "Niveau {level} benötigt die interne CA-Kette (Vertrauensanker).",
        "es": "El nivel {level} necesita la cadena de CA interna (anclas de confianza).",
        "pt": "O nível {level} precisa da cadeia da CA interna (âncoras de confiança).",
    },
    "docs.more": {
        "en": "Full documentation…",
        "fr": "Documentation complète…",
        "nl": "Volledige documentatie…",
        "de": "Vollständige Dokumentation…",
        "es": "Documentación completa…",
        "pt": "Documentação completa…",
    },
    "docs.title": {
        "en": "Cachet — Documentation",
        "fr": "Cachet — Documentation",
        "nl": "Cachet — Documentatie",
        "de": "Cachet — Dokumentation",
        "es": "Cachet — Documentación",
        "pt": "Cachet — Documentação",
    },
    # ------------------------------------------------------------- step 6
    "place.page": {
        "en": "Target page:",
        "fr": "Page cible :",
        "nl": "Doelpagina:",
        "de": "Zielseite:",
        "es": "Página de destino:",
        "pt": "Página de destino:",
    },
    "place.preview_page": {
        "en": "Preview: page {cur}/{total}",
        "fr": "Aperçu : page {cur}/{total}",
        "nl": "Voorbeeld: pagina {cur}/{total}",
        "de": "Vorschau: Seite {cur}/{total}",
        "es": "Vista previa: página {cur}/{total}",
        "pt": "Pré-visualização: página {cur}/{total}",
    },
    "place.prev": {
        "en": "◀ Previous page",
        "fr": "◀ Page précédente",
        "nl": "◀ Vorige pagina",
        "de": "◀ Vorherige Seite",
        "es": "◀ Página anterior",
        "pt": "◀ Página anterior",
    },
    "place.next": {
        "en": "Next page ▶",
        "fr": "Page suivante ▶",
        "nl": "Volgende pagina ▶",
        "de": "Nächste Seite ▶",
        "es": "Página siguiente ▶",
        "pt": "Página seguinte ▶",
    },
    "place.pos_none": {
        "en": "No position set — click on the page preview.",
        "fr": "Aucune position définie — cliquez sur l'aperçu de la page.",
        "nl": "Geen positie ingesteld — klik op het paginavoorbeeld.",
        "de": "Keine Position festgelegt — klicken Sie auf die Seitenvorschau.",
        "es": "Sin posición definida — haga clic en la vista previa.",
        "pt": "Nenhuma posição definida — clique na pré-visualização.",
    },
    "place.pos_default": {
        "en": "No position set — the vignette goes bottom-right on the last page.",
        "fr": "Aucune position définie — la vignette ira en bas à droite de la dernière page.",
        "nl": "Geen positie ingesteld — het vignet komt rechtsonder op de laatste pagina.",
        "de": "Keine Position festgelegt — die Vignette kommt unten rechts auf die letzte Seite.",
        "es": "Sin posición definida — la viñeta irá abajo a la derecha en la última página.",
        "pt": "Nenhuma posição definida — a vinheta ficará em baixo à direita na última página.",
    },
    "place.pos": {
        "en": "Position: page {page}, ({x}, {y}) pt from the bottom-left corner.",
        "fr": "Position : page {page}, ({x}, {y}) pt depuis le coin inférieur gauche.",
        "nl": "Positie: pagina {page}, ({x}, {y}) pt vanaf de linkerbenedenhoek.",
        "de": "Position: Seite {page}, ({x}, {y}) pt von der linken unteren Ecke.",
        "es": "Posición: página {page}, ({x}, {y}) pt desde la esquina inferior izquierda.",
        "pt": "Posição: página {page}, ({x}, {y}) pt a partir do canto inferior esquerdo.",
    },
    "place.reset": {
        "en": "Reset position",
        "fr": "Réinitialiser la position",
        "nl": "Positie wissen",
        "de": "Position zurücksetzen",
        "es": "Restablecer posición",
        "pt": "Repor posição",
    },
    "place.page_beyond": {
        "en": "⚠ Page {page} is beyond the template ({total} pages): documents without this page will fail.",
        "fr": "⚠ La page {page} dépasse le modèle ({total} pages) : les documents sans cette page échoueront.",
        "nl": "⚠ Pagina {page} valt buiten het sjabloon ({total} pagina's): documenten zonder deze pagina zullen mislukken.",
        "de": "⚠ Seite {page} liegt außerhalb der Vorlage ({total} Seiten): Dokumente ohne diese Seite schlagen fehl.",
        "es": "⚠ La página {page} supera la plantilla ({total} páginas): los documentos sin esa página fallarán.",
        "pt": "⚠ A página {page} excede o modelo ({total} páginas): os documentos sem essa página falharão.",
    },
    "place.page_invalid": {
        "en": "⚠ The target page must be a whole number ≥ 1.",
        "fr": "⚠ La page cible doit être un nombre entier ≥ 1.",
        "nl": "⚠ De doelpagina moet een geheel getal ≥ 1 zijn.",
        "de": "⚠ Die Zielseite muss eine ganze Zahl ≥ 1 sein.",
        "es": "⚠ La página de destino debe ser un número entero ≥ 1.",
        "pt": "⚠ A página de destino deve ser um número inteiro ≥ 1.",
    },
    "place.locked_suffix": {
        "en": " — locked: {anchor}",
        "fr": " — verrouillé : {anchor}",
        "nl": " — vergrendeld: {anchor}",
        "de": " — gesperrt: {anchor}",
        "es": " — bloqueado: {anchor}",
        "pt": " — bloqueado: {anchor}",
    },
    "place.pos_anchor": {
        "en": "Position: {anchor} of each document, ({x}, {y}) pt from the bottom-left corner.",
        "fr": "Position : {anchor} de chaque document, ({x}, {y}) pt depuis le coin inférieur gauche.",
        "nl": "Positie: {anchor} van elk document, ({x}, {y}) pt vanaf de linkerbenedenhoek.",
        "de": "Position: {anchor} jedes Dokuments, ({x}, {y}) pt von der linken unteren Ecke.",
        "es": "Posición: {anchor} de cada documento, ({x}, {y}) pt desde la esquina inferior izquierda.",
        "pt": "Posição: {anchor} de cada documento, ({x}, {y}) pt a partir do canto inferior esquerdo.",
    },
    "place.pos_default_anchor": {
        "en": "No position set — the vignette goes bottom-right on the {anchor} of each document.",
        "fr": "Aucune position définie — la vignette ira en bas à droite de la {anchor} de chaque document.",
        "nl": "Geen positie ingesteld — het vignet komt rechtsonder op de {anchor} van elk document.",
        "de": "Keine Position festgelegt — die Vignette kommt unten rechts auf die {anchor} jedes Dokuments.",
        "es": "Sin posición definida — la viñeta irá abajo a la derecha en la {anchor} de cada documento.",
        "pt": "Nenhuma posição definida — a vinheta ficará em baixo à direita na {anchor} de cada documento.",
    },
    "place.anchor_status": {
        "en": (
            "{ok}/{total} document(s) accepted with this choice — details on "
            "the Validation step."
        ),
        "fr": (
            "{ok}/{total} document(s) accepté(s) avec ce choix — détails à "
            "l'étape de validation."
        ),
        "nl": (
            "{ok}/{total} document(en) aanvaard met deze keuze — details in de "
            "validatiestap."
        ),
        "de": (
            "{ok}/{total} Dokument(e) mit dieser Wahl akzeptiert — Details im "
            "Prüfschritt."
        ),
        "es": (
            "{ok}/{total} documento(s) aceptado(s) con esta elección — detalles "
            "en el paso de validación."
        ),
        "pt": (
            "{ok}/{total} documento(s) aceite(s) com esta escolha — detalhes no "
            "passo de validação."
        ),
    },
    "place.anchor_hint": {
        "en": (
            "Changing this re-checks every document: a file whose page count "
            "differs from the template is accepted only if that page has "
            "exactly the template's page size."
        ),
        "fr": (
            "Modifier ce choix revérifie chaque document : un fichier dont le "
            "nombre de pages diffère du modèle n'est accepté que si la page "
            "choisie a exactement les mêmes dimensions que le modèle."
        ),
        "nl": (
            "Als u dit wijzigt, wordt elk document opnieuw gecontroleerd: een "
            "bestand met een ander aantal pagina's dan het sjabloon wordt "
            "alleen aanvaard als die pagina exact de paginagrootte van het "
            "sjabloon heeft."
        ),
        "de": (
            "Bei einer Änderung wird jedes Dokument erneut geprüft: Eine Datei, "
            "deren Seitenzahl von der Vorlage abweicht, wird nur akzeptiert, "
            "wenn die gewählte Seite exakt die Seitengröße der Vorlage hat."
        ),
        "es": (
            "Cambiar esta opción vuelve a comprobar todos los documentos: un "
            "archivo cuyo número de páginas no coincide con el de la plantilla "
            "solo se acepta si la página elegida (primera o última) tiene "
            "exactamente el tamaño de página de la plantilla."
        ),
        "pt": (
            "Alterar esta escolha volta a verificar todos os documentos: um "
            "ficheiro cujo número de páginas difere do modelo só é aceite se a "
            "página escolhida tiver exatamente as dimensões de página do "
            "modelo."
        ),
    },
    "place.elements_title": {
        "en": "Elements to place",
        "fr": "Éléments à placer",
        "nl": "Te plaatsen elementen",
        "de": "Zu platzierende Elemente",
        "es": "Elementos a colocar",
        "pt": "Elementos a posicionar",
    },
    "place.list_empty": {
        "en": "No signature yet — add one with the buttons above.",
        "fr": "Aucune signature pour l'instant — ajoutez-en une avec les boutons ci-dessus.",
        "nl": "Nog geen handtekening — voeg er een toe met de knoppen hierboven.",
        "de": "Noch keine Signatur — fügen Sie eine über die Schaltflächen oben hinzu.",
        "es": "Aún no hay ninguna firma — añada una con los botones de arriba.",
        "pt": "Ainda não há nenhuma assinatura — adicione uma com os botões acima.",
    },
    "place.el_vignette": {
        "en": "Signature vignette (certificate)",
        "fr": "Vignette de signature (certificat)",
        "nl": "Handtekeningvignet (certificaat)",
        "de": "Signaturvignette (Zertifikat)",
        "es": "Viñeta de firma (certificado)",
        "pt": "Vinheta de assinatura (certificado)",
    },
    "place.el_vignette_short": {
        "en": "Vignette", "fr": "Vignette", "nl": "Vignet",
        "de": "Vignette", "es": "Viñeta", "pt": "Vinheta",
    },
    "place.st_page": {
        "en": "page {page}",
        "fr": "page {page}",
        "nl": "pagina {page}",
        "de": "Seite {page}",
        "es": "página {page}",
        "pt": "página {page}",
    },
    "place.st_all": {
        "en": "every page",
        "fr": "toutes les pages",
        "nl": "elke pagina",
        "de": "jede Seite",
        "es": "todas las páginas",
        "pt": "todas as páginas",
    },
    "place.st_default": {
        "en": "default position",
        "fr": "position par défaut",
        "nl": "standaardpositie",
        "de": "Standardposition",
        "es": "posición predeterminada",
        "pt": "posição predefinida",
    },
    "place.st_unplaced": {
        "en": "not placed",
        "fr": "non placée",
        "nl": "niet geplaatst",
        "de": "nicht platziert",
        "es": "sin colocar",
        "pt": "não posicionada",
    },
    "place.st_error": {
        "en": "⚠ unusable",
        "fr": "⚠ inutilisable",
        "nl": "⚠ onbruikbaar",
        "de": "⚠ nicht verwendbar",
        "es": "⚠ no utilizable",
        "pt": "⚠ inutilizável",
    },
    "place.all_pages": {
        "en": "On every page (initials)",
        "fr": "Sur toutes les pages (paraphe)",
        "nl": "Op elke pagina (paraaf)",
        "de": "Auf jeder Seite (Paraphe)",
        "es": "En todas las páginas (rúbrica)",
        "pt": "Em todas as páginas (rubrica)",
    },
    "place.pos_all": {
        "en": "Position: every page, ({x}, {y}) pt from the bottom-left corner.",
        "fr": "Position : toutes les pages, ({x}, {y}) pt depuis le coin inférieur gauche.",
        "nl": "Positie: elke pagina, ({x}, {y}) pt vanaf de linkerbenedenhoek.",
        "de": "Position: jede Seite, ({x}, {y}) pt von der linken unteren Ecke.",
        "es": "Posición: todas las páginas, ({x}, {y}) pt desde la esquina inferior izquierda.",
        "pt": "Posição: todas as páginas, ({x}, {y}) pt a partir do canto inferior esquerdo.",
    },
    "place.select_hint": {
        "en": "Select an element in the list, then click on the page preview to place it.",
        "fr": "Sélectionnez un élément dans la liste, puis cliquez sur l'aperçu de la page pour le placer.",
        "nl": "Selecteer een element in de lijst en klik daarna op het paginavoorbeeld om het te plaatsen.",
        "de": "Wählen Sie ein Element in der Liste aus und klicken Sie dann auf die Seitenvorschau, um es zu platzieren.",
        "es": "Seleccione un elemento de la lista y luego haga clic en la vista previa de la página para colocarlo.",
        "pt": "Selecione um elemento na lista e depois clique na pré-visualização da página para o posicionar.",
    },
    "place.need_signature": {
        "en": "Add at least one visual signature and keep it ticked.",
        "fr": "Ajoutez au moins une signature visuelle et laissez-la cochée.",
        "nl": "Voeg minstens één visuele handtekening toe en laat ze aangevinkt.",
        "de": "Fügen Sie mindestens eine visuelle Signatur hinzu und lassen Sie das Häkchen gesetzt.",
        "es": "Añada al menos una firma visual y manténgala marcada.",
        "pt": "Adicione pelo menos uma assinatura visual e mantenha-a marcada.",
    },
    "place.unplaced": {
        "en": "“{name}” is not placed yet: select it, then click on the page preview.",
        "fr": "« {name} » n'est pas encore placée : sélectionnez-la, puis cliquez sur l'aperçu de la page.",
        "nl": "'{name}' is nog niet geplaatst: selecteer deze handtekening en klik daarna op het paginavoorbeeld.",
        "de": "„{name}“ ist noch nicht platziert: Wählen Sie sie aus und klicken Sie dann auf die Seitenvorschau.",
        "es": "«{name}» aún no está colocada: selecciónela y luego haga clic en la vista previa de la página.",
        "pt": "«{name}» ainda não está posicionada: selecione-a e depois clique na pré-visualização da página.",
    },
    "place.el_broken": {
        "en": "“{name}” cannot be used: {error}",
        "fr": "« {name} » est inutilisable : {error}",
        "nl": "'{name}' kan niet worden gebruikt: {error}",
        "de": "„{name}“ kann nicht verwendet werden: {error}",
        "es": "«{name}» no se puede utilizar: {error}",
        "pt": "«{name}» não pode ser utilizada: {error}",
    },
    "place.save_failed": {
        "en": "⚠ Your signatures could not be saved: {error}",
        "fr": "⚠ Vos signatures n'ont pas pu être enregistrées : {error}",
        "nl": "⚠ Uw handtekeningen konden niet worden opgeslagen: {error}",
        "de": "⚠ Ihre Signaturen konnten nicht gespeichert werden: {error}",
        "es": "⚠ No se pudieron guardar sus firmas: {error}",
        "pt": "⚠ Não foi possível guardar as suas assinaturas: {error}",
    },
    "place.storage_note": {
        "en": "Signatures and preferences are saved unencrypted in your user folder.",
        "fr": "Les signatures et les préférences sont enregistrées sans chiffrement dans votre dossier utilisateur.",
        "nl": "Handtekeningen en voorkeuren worden onversleuteld opgeslagen in uw gebruikersmap.",
        "de": "Signaturen und Einstellungen werden unverschlüsselt in Ihrem Benutzerordner gespeichert.",
        "es": "Las firmas y las preferencias se guardan sin cifrar en su carpeta de usuario.",
        "pt": "As assinaturas e as preferências são guardadas sem encriptação na sua pasta de utilizador.",
    },
    # ------------------------------------------- step 6: signature editor
    "sig.add_text": {
        "en": "Add text…",
        "fr": "Ajouter un texte…",
        "nl": "Tekst toevoegen…",
        "de": "Text hinzufügen…",
        "es": "Añadir texto…",
        "pt": "Adicionar texto…",
    },
    "sig.add_image": {
        "en": "Add image…",
        "fr": "Ajouter une image…",
        "nl": "Afbeelding toevoegen…",
        "de": "Bild hinzufügen…",
        "es": "Añadir imagen…",
        "pt": "Adicionar imagem…",
    },
    "sig.draw": {
        "en": "Draw…",
        "fr": "Dessiner…",
        "nl": "Tekenen…",
        "de": "Zeichnen…",
        "es": "Dibujar…",
        "pt": "Desenhar…",
    },
    "sig.edit": {
        "en": "Edit…",
        "fr": "Modifier…",
        "nl": "Bewerken…",
        "de": "Bearbeiten…",
        "es": "Editar…",
        "pt": "Editar…",
    },
    "sig.delete": {
        "en": "Delete",
        "fr": "Supprimer",
        "nl": "Verwijderen",
        "de": "Löschen",
        "es": "Eliminar",
        "pt": "Eliminar",
    },
    "sig.delete_title": {
        "en": "Delete this signature?",
        "fr": "Supprimer cette signature ?",
        "nl": "Deze handtekening verwijderen?",
        "de": "Diese Signatur löschen?",
        "es": "¿Eliminar esta firma?",
        "pt": "Eliminar esta assinatura?",
    },
    "sig.delete_body": {
        "en": (
            "“{name}” will be removed from your saved signatures, together "
            "with its stored image. This cannot be undone."
        ),
        "fr": (
            "« {name} » sera retirée de vos signatures enregistrées, ainsi que "
            "son image stockée. Cette action est irréversible."
        ),
        "nl": (
            "'{name}' wordt uit uw opgeslagen handtekeningen verwijderd, samen "
            "met de bijbehorende opgeslagen afbeelding. Dit kan niet ongedaan "
            "worden gemaakt."
        ),
        "de": (
            "„{name}“ wird aus Ihren gespeicherten Signaturen entfernt, "
            "zusammen mit dem zugehörigen gespeicherten Bild. Dies kann nicht "
            "rückgängig gemacht werden."
        ),
        "es": (
            "«{name}» se eliminará de sus firmas guardadas, junto con su "
            "imagen almacenada. Esta acción no se puede deshacer."
        ),
        "pt": (
            "«{name}» será removida das suas assinaturas guardadas, juntamente "
            "com a respetiva imagem armazenada. Esta ação não pode ser "
            "anulada."
        ),
    },
    "sig.delete_confirm": {
        "en": "Delete",
        "fr": "Supprimer",
        "nl": "Verwijderen",
        "de": "Löschen",
        "es": "Eliminar",
        "pt": "Eliminar",
    },
    "sig.delete_keep": {
        "en": "Keep it",
        "fr": "Conserver",
        "nl": "Behouden",
        "de": "Behalten",
        "es": "Conservar",
        "pt": "Manter",
    },
    "sig.text_title_add": {
        "en": "New text signature",
        "fr": "Nouvelle signature texte",
        "nl": "Nieuwe teksthandtekening",
        "de": "Neue Textsignatur",
        "es": "Nueva firma de texto",
        "pt": "Nova assinatura de texto",
    },
    "sig.text_title_edit": {
        "en": "Edit text signature",
        "fr": "Modifier la signature texte",
        "nl": "Teksthandtekening bewerken",
        "de": "Textsignatur bearbeiten",
        "es": "Editar la firma de texto",
        "pt": "Editar a assinatura de texto",
    },
    "sig.name": {
        "en": "Name (optional):",
        "fr": "Nom (facultatif) :",
        "nl": "Naam (optioneel):",
        "de": "Name (optional):",
        "es": "Nombre (opcional):",
        "pt": "Nome (opcional):",
    },
    "sig.text": {
        "en": "Text:",
        "fr": "Texte :",
        "nl": "Tekst:",
        "de": "Text:",
        "es": "Texto:",
        "pt": "Texto:",
    },
    "sig.text_hint": {
        "en": (
            "Several lines are allowed. {date} is replaced by the date of the "
            "run and {filename} by the name of each document."
        ),
        "fr": (
            "Plusieurs lignes sont possibles. {date} est remplacé par la date "
            "de la signature et {filename} par le nom de chaque document."
        ),
        "nl": (
            "Meerdere regels zijn toegestaan. {date} wordt vervangen door de "
            "datum van ondertekening en {filename} door de naam van elk "
            "document."
        ),
        "de": (
            "Mehrere Zeilen sind möglich. {date} wird durch das Datum der "
            "Signierung ersetzt und {filename} durch den Namen des jeweiligen "
            "Dokuments."
        ),
        "es": (
            "Se admiten varias líneas. {date} se sustituye por la fecha de la "
            "firma y {filename} por el nombre de cada documento."
        ),
        "pt": (
            "São permitidas várias linhas. {date} é substituído pela data da "
            "assinatura e {filename} pelo nome de cada documento."
        ),
    },
    "sig.font": {
        "en": "Font:",
        "fr": "Police :",
        "nl": "Lettertype:",
        "de": "Schriftart:",
        "es": "Fuente:",
        "pt": "Tipo de letra:",
    },
    "sig.font_custom": {
        "en": "Other font file…",
        "fr": "Autre fichier de police…",
        "nl": "Ander lettertypebestand…",
        "de": "Andere Schriftdatei…",
        "es": "Otro archivo de fuente…",
        "pt": "Outro ficheiro de tipo de letra…",
    },
    "sig.size": {
        "en": "Size (pt):",
        "fr": "Taille (pt) :",
        "nl": "Grootte (pt):",
        "de": "Größe (pt):",
        "es": "Tamaño (pt):",
        "pt": "Tamanho (pt):",
    },
    "sig.color": {
        "en": "Colour:",
        "fr": "Couleur :",
        "nl": "Kleur:",
        "de": "Farbe:",
        "es": "Color:",
        "pt": "Cor:",
    },
    "sig.color_title": {
        "en": "Signature colour",
        "fr": "Couleur de la signature",
        "nl": "Kleur van de handtekening",
        "de": "Farbe der Signatur",
        "es": "Color de la firma",
        "pt": "Cor da assinatura",
    },
    "sig.preview": {
        "en": "Preview",
        "fr": "Aperçu",
        "nl": "Voorbeeld",
        "de": "Vorschau",
        "es": "Vista previa",
        "pt": "Pré-visualização",
    },
    "sig.preview_size": {
        "en": "Size on the page: {w} × {h} pt",
        "fr": "Taille sur la page : {w} × {h} pt",
        "nl": "Grootte op de pagina: {w} × {h} pt",
        "de": "Größe auf der Seite: {w} × {h} pt",
        "es": "Tamaño en la página: {w} × {h} pt",
        "pt": "Tamanho na página: {w} × {h} pt",
    },
    "sig.save": {
        "en": "Save",
        "fr": "Enregistrer",
        "nl": "Opslaan",
        "de": "Speichern",
        "es": "Guardar",
        "pt": "Guardar",
    },
    "sig.cancel": {
        "en": "Cancel",
        "fr": "Annuler",
        "nl": "Annuleren",
        "de": "Abbrechen",
        "es": "Cancelar",
        "pt": "Cancelar",
    },
    "sig.err_text_empty": {
        "en": "Enter some text.",
        "fr": "Saisissez un texte.",
        "nl": "Voer een tekst in.",
        "de": "Geben Sie einen Text ein.",
        "es": "Introduzca un texto.",
        "pt": "Introduza um texto.",
    },
    "sig.err_size": {
        "en": "The size must be a number greater than 0, up to {max}.",
        "fr": "La taille doit être un nombre supérieur à 0 et au plus égal à {max}.",
        "nl": "De grootte moet een getal groter dan 0 en hoogstens {max} zijn.",
        "de": "Die Größe muss eine Zahl größer als 0 und höchstens {max} sein.",
        "es": "El tamaño debe ser un número mayor que 0 y como máximo {max}.",
        "pt": "O tamanho deve ser um número superior a 0 e no máximo {max}.",
    },
    "sig.err_render": {
        "en": "This signature cannot be rendered: {error}",
        "fr": "Impossible de générer cette signature : {error}",
        "nl": "Deze handtekening kan niet worden weergegeven: {error}",
        "de": "Diese Signatur kann nicht dargestellt werden: {error}",
        "es": "No se puede generar esta firma: {error}",
        "pt": "Não é possível gerar esta assinatura: {error}",
    },
    "sig.draw_title": {
        "en": "Draw a signature",
        "fr": "Dessiner une signature",
        "nl": "Een handtekening tekenen",
        "de": "Signatur zeichnen",
        "es": "Dibujar una firma",
        "pt": "Desenhar uma assinatura",
    },
    "sig.draw_hint": {
        "en": "Draw with the mouse or a stylus in the box below. The background stays transparent.",
        "fr": "Dessinez à la souris ou au stylet dans le cadre ci-dessous. Le fond reste transparent.",
        "nl": "Teken met de muis of een stylus in het vak hieronder. De achtergrond blijft transparant.",
        "de": "Zeichnen Sie mit der Maus oder einem Stift in das Feld unten. Der Hintergrund bleibt transparent.",
        "es": "Dibuje con el ratón o un lápiz óptico en el recuadro inferior. El fondo se mantiene transparente.",
        "pt": "Desenhe com o rato ou uma caneta digital na caixa abaixo. O fundo mantém-se transparente.",
    },
    "sig.draw_clear": {
        "en": "Clear",
        "fr": "Effacer",
        "nl": "Wissen",
        "de": "Leeren",
        "es": "Borrar",
        "pt": "Limpar",
    },
    "sig.err_draw_empty": {
        "en": "Draw something first.",
        "fr": "Dessinez d'abord quelque chose.",
        "nl": "Teken eerst iets.",
        "de": "Zeichnen Sie zuerst etwas.",
        "es": "Dibuje algo primero.",
        "pt": "Desenhe algo primeiro.",
    },
    "sig.err_draw_too_large": {
        "en": "This drawing is too large. Clear it and draw a smaller one.",
        "fr": "Ce dessin est trop grand. Effacez-le et dessinez-en un plus petit.",
        "nl": "Deze tekening is te groot. Wis ze en teken een kleinere.",
        "de": "Diese Zeichnung ist zu groß. Löschen Sie sie und zeichnen Sie eine kleinere.",
        "es": "Este dibujo es demasiado grande. Bórrelo y dibuje uno más pequeño.",
        "pt": "Este desenho é demasiado grande. Apague-o e desenhe um mais pequeno.",
    },
    "sig.drawn_label": {
        "en": "Drawn signature",
        "fr": "Signature dessinée",
        "nl": "Getekende handtekening",
        "de": "Gezeichnete Signatur",
        "es": "Firma dibujada",
        "pt": "Assinatura desenhada",
    },
    "sig.image_title": {
        "en": "Edit image signature",
        "fr": "Modifier la signature image",
        "nl": "Afbeeldingshandtekening bewerken",
        "de": "Bildsignatur bearbeiten",
        "es": "Editar la firma de imagen",
        "pt": "Editar a assinatura de imagem",
    },
    "sig.width": {
        "en": "Width (pt):",
        "fr": "Largeur (pt) :",
        "nl": "Breedte (pt):",
        "de": "Breite (pt):",
        "es": "Ancho (pt):",
        "pt": "Largura (pt):",
    },
    "sig.err_width": {
        "en": "The width must be a number greater than 0, up to {max}.",
        "fr": "La largeur doit être un nombre supérieur à 0 et au plus égal à {max}.",
        "nl": "De breedte moet een getal groter dan 0 en hoogstens {max} zijn.",
        "de": "Die Breite muss eine Zahl größer als 0 und höchstens {max} sein.",
        "es": "El ancho debe ser un número mayor que 0 y como máximo {max}.",
        "pt": "A largura deve ser um número superior a 0 e no máximo {max}.",
    },
    "sig.err_image": {
        "en": "This image cannot be used: {error}",
        "fr": "Cette image est inutilisable : {error}",
        "nl": "Deze afbeelding kan niet worden gebruikt: {error}",
        "de": "Dieses Bild kann nicht verwendet werden: {error}",
        "es": "No se puede utilizar esta imagen: {error}",
        "pt": "Não é possível utilizar esta imagem: {error}",
    },
    "sig.err_store": {
        "en": (
            "The signature could not be stored in your user folder, so it was "
            "not added: {error}"
        ),
        "fr": (
            "La signature n'a pas pu être enregistrée dans votre dossier "
            "utilisateur ; elle n'a donc pas été ajoutée : {error}"
        ),
        "nl": (
            "De handtekening kon niet in uw gebruikersmap worden opgeslagen en "
            "is dus niet toegevoegd: {error}"
        ),
        "de": (
            "Die Signatur konnte nicht in Ihrem Benutzerordner gespeichert "
            "werden und wurde daher nicht hinzugefügt: {error}"
        ),
        "es": (
            "No se pudo guardar la firma en su carpeta de usuario, por lo que "
            "no se ha añadido: {error}"
        ),
        "pt": (
            "Não foi possível guardar a assinatura na sua pasta de utilizador, "
            "pelo que não foi adicionada: {error}"
        ),
    },
    # ------------------------------------------------------------- step 7
    "run.summary_docs": {
        "en": "Documents to sign: {count}",
        "fr": "Documents à signer : {count}",
        "nl": "Te ondertekenen documenten: {count}",
        "de": "Zu signierende Dokumente: {count}",
        "es": "Documentos a firmar: {count}",
        "pt": "Documentos a assinar: {count}",
    },
    "run.summary_mode": {
        "en": "Signature type: {mode}",
        "fr": "Type de signature : {mode}",
        "nl": "Handtekeningtype: {mode}",
        "de": "Signaturtyp: {mode}",
        "es": "Tipo de firma: {mode}",
        "pt": "Tipo de assinatura: {mode}",
    },
    "run.summary_level": {
        "en": "PAdES level: {level}",
        "fr": "Niveau PAdES : {level}",
        "nl": "PAdES-niveau: {level}",
        "de": "PAdES-Niveau: {level}",
        "es": "Nivel PAdES: {level}",
        "pt": "Nível PAdES: {level}",
    },
    "run.summary_output": {
        "en": "Output folder: {output}",
        "fr": "Dossier de sortie : {output}",
        "nl": "Uitvoermap: {output}",
        "de": "Ausgabeordner: {output}",
        "es": "Carpeta de salida: {output}",
        "pt": "Pasta de saída: {output}",
    },
    "run.summary_place": {
        "en": "Signature vignette: {place}",
        "fr": "Vignette de signature : {place}",
        "nl": "Handtekeningvignet: {place}",
        "de": "Signaturvignette: {place}",
        "es": "Viñeta de firma: {place}",
        "pt": "Vinheta de assinatura: {place}",
    },
    "run.summary_stamps": {
        "en": "Visual signatures: {count}",
        "fr": "Signatures visuelles : {count}",
        "nl": "Visuele handtekeningen: {count}",
        "de": "Visuelle Signaturen: {count}",
        "es": "Firmas visuales: {count}",
        "pt": "Assinaturas visuais: {count}",
    },
    "run.stamp_line": {
        "en": "   • {name} — {place}",
        "fr": "   • {name} — {place}",
        "nl": "   • {name} — {place}",
        "de": "   • {name} — {place}",
        "es": "   • {name} — {place}",
        "pt": "   • {name} — {place}",
    },
    "run.place_custom": {
        "en": "page {page} @ ({x}, {y}) pt",
        "fr": "page {page} @ ({x}, {y}) pt",
        "nl": "pagina {page} @ ({x}, {y}) pt",
        "de": "Seite {page} @ ({x}, {y}) pt",
        "es": "página {page} @ ({x}, {y}) pt",
        "pt": "página {page} @ ({x}, {y}) pt",
    },
    "run.place_default": {
        "en": "bottom-right, last page",
        "fr": "en bas à droite, dernière page",
        "nl": "rechtsonder, laatste pagina",
        "de": "unten rechts, letzte Seite",
        "es": "abajo a la derecha, última página",
        "pt": "em baixo à direita, última página",
    },
    "run.place_custom_anchor": {
        "en": "{anchor} of each document @ ({x}, {y}) pt",
        "fr": "{anchor} de chaque document @ ({x}, {y}) pt",
        "nl": "{anchor} van elk document @ ({x}, {y}) pt",
        "de": "{anchor} jedes Dokuments @ ({x}, {y}) pt",
        "es": "{anchor} de cada documento @ ({x}, {y}) pt",
        "pt": "{anchor} de cada documento @ ({x}, {y}) pt",
    },
    "run.place_default_anchor": {
        "en": "bottom-right, {anchor} of each document",
        "fr": "en bas à droite, {anchor} de chaque document",
        "nl": "rechtsonder, {anchor} van elk document",
        "de": "unten rechts, {anchor} jedes Dokuments",
        "es": "abajo a la derecha, {anchor} de cada documento",
        "pt": "em baixo à direita, {anchor} de cada documento",
    },
    "run.place_all": {
        "en": "every page @ ({x}, {y}) pt",
        "fr": "toutes les pages @ ({x}, {y}) pt",
        "nl": "elke pagina @ ({x}, {y}) pt",
        "de": "jede Seite @ ({x}, {y}) pt",
        "es": "todas las páginas @ ({x}, {y}) pt",
        "pt": "todas as páginas @ ({x}, {y}) pt",
    },
    "run.summary_anchor_last": {
        "en": "Files with a different page count are signed on their last page.",
        "fr": "Les fichiers dont le nombre de pages diffère sont signés sur leur dernière page.",
        "nl": "Bestanden met een afwijkend aantal pagina's worden op hun laatste pagina ondertekend.",
        "de": "Dateien mit abweichender Seitenzahl werden auf ihrer letzten Seite signiert.",
        "es": "Los archivos con un número de páginas distinto se firman en su última página.",
        "pt": "Os ficheiros com um número de páginas diferente são assinados na sua última página.",
    },
    "run.summary_anchor_first": {
        "en": "Files with a different page count are signed on their first page.",
        "fr": "Les fichiers dont le nombre de pages diffère sont signés sur leur première page.",
        "nl": "Bestanden met een afwijkend aantal pagina's worden op hun eerste pagina ondertekend.",
        "de": "Dateien mit abweichender Seitenzahl werden auf ihrer ersten Seite signiert.",
        "es": "Los archivos con un número de páginas distinto se firman en su primera página.",
        "pt": "Os ficheiros com um número de páginas diferente são assinados na sua primeira página.",
    },
    "run.start": {
        "en": "Start signing",
        "fr": "Lancer la signature",
        "nl": "Ondertekenen starten",
        "de": "Signieren starten",
        "es": "Iniciar la firma",
        "pt": "Iniciar a assinatura",
    },
    "run.pin_note": {
        "en": "eID: you will be asked for your PIN once per document.",
        "fr": "eID : votre code PIN sera demandé une fois par document.",
        "nl": "eID: uw pincode wordt één keer per document gevraagd.",
        "de": "eID: Ihre PIN wird einmal pro Dokument abgefragt.",
        "es": "eID: se le pedirá el PIN una vez por documento.",
        "pt": "eID: o PIN ser-lhe-á pedido uma vez por documento.",
    },
    "run.azure_note": {
        "en": "Azure: one Microsoft sign-in covers the whole batch.",
        "fr": "Azure : une seule connexion Microsoft couvre tout le lot.",
        "nl": "Azure: één Microsoft-aanmelding volstaat voor de hele reeks.",
        "de": "Azure: eine Microsoft-Anmeldung genügt für den ganzen Stapel.",
        "es": "Azure: un solo inicio de sesión de Microsoft cubre todo el lote.",
        "pt": "Azure: um único início de sessão Microsoft cobre todo o lote.",
    },
    "run.card_title": {
        "en": "Insert your eID card",
        "fr": "Insérez votre carte eID",
        "nl": "Steek uw eID-kaart in de kaartlezer",
        "de": "Stecken Sie Ihre eID-Karte ein",
        "es": "Inserte su tarjeta eID",
        "pt": "Insira o seu cartão eID",
    },
    "run.card_body": {
        "en": (
            "Insert your Belgian identity card in the reader now, before "
            "pressing Start, and leave it in place for the whole batch. Your "
            "PIN will be requested once per document."
        ),
        "fr": (
            "Insérez dès maintenant votre carte d'identité belge dans le "
            "lecteur, avant d'appuyer sur « Lancer la signature », et "
            "laissez-la en place jusqu'à la fin du lot. Votre code PIN sera "
            "demandé une fois par document."
        ),
        "nl": (
            "Steek uw Belgische identiteitskaart nu in de kaartlezer, voordat u "
            "op 'Ondertekenen starten' drukt, en laat de kaart tijdens de hele "
            "reeks in de lezer zitten. Uw pincode wordt één keer per document "
            "gevraagd."
        ),
        "de": (
            "Stecken Sie Ihre belgische Identitätskarte jetzt in den "
            "Kartenleser, bevor Sie auf „Signieren starten“ klicken, und lassen "
            "Sie sie für den ganzen Stapel stecken. Ihre PIN wird einmal pro "
            "Dokument abgefragt."
        ),
        "es": (
            "Inserte ahora su tarjeta de identidad belga en el lector, antes de "
            "pulsar «Iniciar la firma», y no la retire hasta que termine el "
            "lote. El PIN se le pedirá una vez por documento."
        ),
        "pt": (
            "Insira agora o seu cartão de identidade belga no leitor, antes de "
            "premir «Iniciar a assinatura», e deixe-o inserido durante todo o "
            "lote. O PIN ser-lhe-á pedido uma vez por documento."
        ),
    },
    "run.progress": {
        "en": "Processing {done}/{total}: {name}",
        "fr": "Traitement {done}/{total} : {name}",
        "nl": "Verwerken {done}/{total}: {name}",
        "de": "Verarbeite {done}/{total}: {name}",
        "es": "Procesando {done}/{total}: {name}",
        "pt": "A processar {done}/{total}: {name}",
    },
    "run.working": {
        "en": "Processing…",
        "fr": "Traitement en cours…",
        "nl": "Bezig met verwerken…",
        "de": "Verarbeitung läuft…",
        "es": "Procesando…",
        "pt": "A processar…",
    },
    "run.done": {
        "en": "Done: {ok}/{total} document(s) processed.",
        "fr": "Terminé : {ok}/{total} document(s) traité(s).",
        "nl": "Klaar: {ok}/{total} document(en) verwerkt.",
        "de": "Fertig: {ok}/{total} Dokument(e) verarbeitet.",
        "es": "Hecho: {ok}/{total} documento(s) procesado(s).",
        "pt": "Concluído: {ok}/{total} documento(s) processado(s).",
    },
    "run.error": {
        "en": "Error: {error}",
        "fr": "Erreur : {error}",
        "nl": "Fout: {error}",
        "de": "Fehler: {error}",
        "es": "Error: {error}",
        "pt": "Erro: {error}",
    },
    # ------------------------------------------------------------- step 8
    "res.col_doc": {
        "en": "Document", "fr": "Document", "nl": "Document",
        "de": "Dokument", "es": "Documento", "pt": "Documento",
    },
    "res.col_status": {
        "en": "Status", "fr": "Statut", "nl": "Status",
        "de": "Status", "es": "Estado", "pt": "Estado",
    },
    "res.col_detail": {
        "en": "Detail", "fr": "Détail", "nl": "Detail",
        "de": "Detail", "es": "Detalle", "pt": "Detalhe",
    },
    "res.ok": {
        "en": "✓ OK", "fr": "✓ OK", "nl": "✓ OK",
        "de": "✓ OK", "es": "✓ OK", "pt": "✓ OK",
    },
    "res.fail": {
        "en": "✗ failed", "fr": "✗ échec", "nl": "✗ mislukt",
        "de": "✗ fehlgeschlagen", "es": "✗ error", "pt": "✗ falhou",
    },
    "res.all_ok": {
        "en": "All {total} document(s) were processed successfully.",
        "fr": "Les {total} document(s) ont tous été traités avec succès.",
        "nl": "Alle {total} document(en) zijn met succes verwerkt.",
        "de": "Alle {total} Dokument(e) wurden erfolgreich verarbeitet.",
        "es": "Los {total} documento(s) se procesaron correctamente.",
        "pt": "Todos os {total} documento(s) foram processados com êxito.",
    },
    "res.partial": {
        "en": "{ok} of {total} document(s) succeeded — {fail} failed.",
        "fr": "{ok} document(s) sur {total} réussi(s) — {fail} en échec.",
        "nl": "{ok} van {total} document(en) geslaagd — {fail} mislukt.",
        "de": "{ok} von {total} Dokument(en) erfolgreich — {fail} fehlgeschlagen.",
        "es": "{ok} de {total} documento(s) correctos — {fail} con error.",
        "pt": "{ok} de {total} documento(s) com êxito — {fail} falharam.",
    },
    "res.open_folder": {
        "en": "Open output folder",
        "fr": "Ouvrir le dossier de sortie",
        "nl": "Uitvoermap openen",
        "de": "Ausgabeordner öffnen",
        "es": "Abrir carpeta de salida",
        "pt": "Abrir a pasta de saída",
    },
    "res.open_folder_failed": {
        "en": "Could not open the folder: {error}",
        "fr": "Impossible d'ouvrir le dossier : {error}",
        "nl": "Kan de map niet openen: {error}",
        "de": "Der Ordner konnte nicht geöffnet werden: {error}",
        "es": "No se pudo abrir la carpeta: {error}",
        "pt": "Não foi possível abrir a pasta: {error}",
    },
    "res.rrn_note": {
        "en": (
            "Reminder: every eID signature embeds your national register "
            "number (RRN) — mind how you distribute the signed files."
        ),
        "fr": (
            "Rappel : chaque signature eID intègre votre numéro de registre "
            "national (RRN) — attention à la diffusion des fichiers signés."
        ),
        "nl": (
            "Herinnering: elke eID-handtekening bevat uw rijksregisternummer "
            "(RRN) — let op hoe u de ondertekende bestanden verspreidt."
        ),
        "de": (
            "Hinweis: Jede eID-Signatur enthält Ihre nationale "
            "Registernummer (RRN) — achten Sie darauf, wie Sie die signierten "
            "Dateien weitergeben."
        ),
        "es": (
            "Recordatorio: cada firma eID incrusta su número de registro "
            "nacional (RRN) — cuide cómo distribuye los archivos firmados."
        ),
        "pt": (
            "Lembrete: cada assinatura eID incorpora o seu número de registo "
            "nacional (RRN) — tenha cuidado com a distribuição dos ficheiros "
            "assinados."
        ),
    },
}

# Long-form documentation (i18n_docs.py) shares the catalog, tr() and the
# test invariants.
CATALOG.update(DOCS_CATALOG)
