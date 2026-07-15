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
"""

from __future__ import annotations

import os

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
            "batch), or stamp a simple image with no legal value. A template "
            "guarantees that every document receives its signature at exactly "
            "the same spot.\n\nThis assistant guides you through eight steps: "
            "choose a template, the documents and an output folder, validate "
            "the batch, pick and place the signature, then sign and review the "
            "report."
        ),
        "fr": (
            "Cachet signe tout un lot de documents PDF en une seule fois. "
            "Signez avec votre carte eID belge (signature qualifiée, "
            "juridiquement équivalente à une signature manuscrite), avec votre "
            "certificat personnel dans Azure Key Vault (signature avancée — "
            "une seule connexion Microsoft pour tout le lot), ou apposez une "
            "simple image sans valeur juridique. Un modèle garantit que chaque "
            "document reçoit sa signature exactement au même endroit.\n\nCet "
            "assistant vous guide en huit étapes : choix du modèle, des "
            "documents et du dossier de sortie, validation du lot, choix et "
            "positionnement de la signature, puis signature et rapport final."
        ),
        "nl": (
            "Cachet ondertekent een hele reeks PDF-documenten in één keer. "
            "Onderteken met uw Belgische eID-kaart (een gekwalificeerde "
            "handtekening, juridisch gelijkwaardig aan een handgeschreven "
            "handtekening), met uw persoonlijke certificaat in Azure Key Vault "
            "(een geavanceerde handtekening — één Microsoft-aanmelding voor de "
            "hele reeks), of plaats een eenvoudige afbeelding zonder juridische "
            "waarde. Een sjabloon garandeert dat elk document zijn handtekening "
            "op exact dezelfde plek krijgt.\n\nDeze assistent begeleidt u in "
            "acht stappen: kies een sjabloon, de documenten en een uitvoermap, "
            "valideer de reeks, kies en plaats de handtekening, onderteken en "
            "bekijk het rapport."
        ),
        "de": (
            "Cachet signiert einen ganzen Stapel PDF-Dokumente in einem "
            "Durchgang. Signieren Sie mit Ihrer belgischen eID-Karte (eine "
            "qualifizierte Signatur, rechtlich der handschriftlichen "
            "Unterschrift gleichgestellt), mit Ihrem persönlichen Zertifikat "
            "in Azure Key Vault (eine fortgeschrittene Signatur — eine einzige "
            "Microsoft-Anmeldung für den ganzen Stapel), oder stempeln Sie ein "
            "einfaches Bild ohne Rechtswert auf. Eine Vorlage stellt sicher, "
            "dass jedes Dokument seine Signatur an genau derselben Stelle "
            "erhält.\n\nDieser Assistent führt Sie durch acht Schritte: "
            "Vorlage, Dokumente und Ausgabeordner wählen, Stapel prüfen, "
            "Signatur wählen und platzieren, dann signieren und den Bericht "
            "prüfen."
        ),
        "es": (
            "Cachet firma un lote completo de documentos PDF de una sola vez. "
            "Firme con su tarjeta eID belga (firma cualificada, legalmente "
            "equivalente a la manuscrita), con su certificado personal en "
            "Azure Key Vault (firma avanzada — un solo inicio de sesión de "
            "Microsoft para todo el lote), o estampe una simple imagen sin "
            "valor legal. Una plantilla garantiza que cada documento reciba su "
            "firma exactamente en el mismo lugar.\n\nEste asistente le guía en "
            "ocho pasos: elegir la plantilla, los documentos y la carpeta de "
            "salida, validar el lote, elegir y colocar la firma y, por último, "
            "firmar y revisar el informe."
        ),
        "pt": (
            "O Cachet assina um lote inteiro de documentos PDF de uma só vez. "
            "Assine com o seu cartão eID belga (assinatura qualificada, "
            "juridicamente equivalente à manuscrita), com o seu certificado "
            "pessoal no Azure Key Vault (assinatura avançada — um único início "
            "de sessão Microsoft para todo o lote), ou aplique uma simples "
            "imagem sem valor legal. Um modelo garante que cada documento "
            "recebe a assinatura exatamente no mesmo sítio.\n\nEste assistente "
            "guia-o em oito passos: escolher o modelo, os documentos e a pasta "
            "de saída, validar o lote, escolher e posicionar a assinatura e, "
            "por fim, assinar e rever o relatório."
        ),
    },
    "landing.language": {
        "en": "Language",
        "fr": "Langue",
        "nl": "Taal",
        "de": "Sprache",
        "es": "Idioma",
        "pt": "Idioma",
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
            "This discards everything entered so far — selected files, "
            "settings and results — and returns to the welcome screen."
        ),
        "fr": (
            "Cela efface tout ce qui a été saisi jusqu'ici — fichiers "
            "sélectionnés, réglages et résultats — et revient à l'écran "
            "d'accueil."
        ),
        "nl": (
            "Hiermee wordt alles gewist wat u tot nu toe hebt ingevoerd — "
            "geselecteerde bestanden, instellingen en resultaten — en keert u "
            "terug naar het welkomstscherm."
        ),
        "de": (
            "Dadurch werden alle bisherigen Eingaben verworfen — ausgewählte "
            "Dateien, Einstellungen und Ergebnisse — und Sie kehren zum "
            "Startbildschirm zurück."
        ),
        "es": (
            "Esto descarta todo lo introducido hasta ahora — archivos "
            "seleccionados, ajustes y resultados — y vuelve a la pantalla de "
            "bienvenida."
        ),
        "pt": (
            "Isto elimina tudo o que foi introduzido até agora — ficheiros "
            "selecionados, definições e resultados — e regressa ao ecrã "
            "inicial."
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
            "compared against it. Choose the blank PDF your documents are "
            "based on. Its pages also serve as the preview when you place the "
            "signature later. Only files whose page count and page sizes "
            "match the template exactly will be signed."
        ),
        "fr": (
            "Le modèle est le document de référence : chaque fichier à signer "
            "lui est comparé. Choisissez le PDF vierge dont vos documents sont "
            "issus. Ses pages servent aussi d'aperçu au moment de positionner "
            "la signature. Seuls les fichiers dont le nombre de pages et les "
            "dimensions correspondent exactement au modèle seront signés."
        ),
        "nl": (
            "Het sjabloon is het referentiedocument: elk te ondertekenen "
            "bestand wordt ermee vergeleken. Kies de lege PDF waarop uw "
            "documenten zijn gebaseerd. De pagina's dienen ook als voorbeeld "
            "bij het plaatsen van de handtekening. Alleen bestanden waarvan "
            "het aantal pagina's en de paginagrootte exact met het sjabloon "
            "overeenkomen, worden ondertekend."
        ),
        "de": (
            "Die Vorlage ist das Referenzdokument: Jede zu signierende Datei "
            "wird mit ihr verglichen. Wählen Sie das leere PDF, auf dem Ihre "
            "Dokumente basieren. Seine Seiten dienen später auch als Vorschau "
            "beim Platzieren der Signatur. Nur Dateien, deren Seitenzahl und "
            "Seitengrößen exakt mit der Vorlage übereinstimmen, werden "
            "signiert."
        ),
        "es": (
            "La plantilla es el documento de referencia: cada archivo a "
            "firmar se compara con ella. Elija el PDF en blanco del que "
            "parten sus documentos. Sus páginas sirven además de vista previa "
            "al colocar la firma. Solo se firmarán los archivos cuyo número "
            "de páginas y dimensiones coincidan exactamente con la plantilla."
        ),
        "pt": (
            "O modelo é o documento de referência: cada ficheiro a assinar é "
            "comparado com ele. Escolha o PDF em branco na origem dos seus "
            "documentos. As suas páginas servem também de pré-visualização ao "
            "posicionar a assinatura. Só serão assinados os ficheiros cujo "
            "número de páginas e dimensões correspondam exatamente ao modelo."
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
            "signature, position and settings are applied to the whole batch. "
            "Documents that do not match the template are filtered out at the "
            "next step."
        ),
        "fr": (
            "Choisissez tous les documents PDF à signer dans ce lot. La même "
            "signature, la même position et les mêmes réglages s'appliquent à "
            "tout le lot. Les documents qui ne correspondent pas au modèle "
            "seront écartés à l'étape suivante."
        ),
        "nl": (
            "Kies alle PDF-documenten die u in deze reeks wilt ondertekenen. "
            "Dezelfde handtekening, positie en instellingen gelden voor de "
            "hele reeks. Documenten die niet met het sjabloon overeenkomen, "
            "worden in de volgende stap uitgefilterd."
        ),
        "de": (
            "Wählen Sie alle PDF-Dokumente, die in diesem Durchgang signiert "
            "werden sollen. Dieselbe Signatur, Position und dieselben "
            "Einstellungen gelten für den gesamten Stapel. Dokumente, die "
            "nicht zur Vorlage passen, werden im nächsten Schritt aussortiert."
        ),
        "es": (
            "Elija todos los documentos PDF que desea firmar en este lote. La "
            "misma firma, posición y ajustes se aplican a todo el lote. Los "
            "documentos que no coincidan con la plantilla se descartarán en "
            "el paso siguiente."
        ),
        "pt": (
            "Escolha todos os documentos PDF a assinar neste lote. A mesma "
            "assinatura, posição e definições aplicam-se a todo o lote. Os "
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
            "Each document is compared with the template: it must have the "
            "same number of pages and exactly the same page sizes. Documents "
            "that differ are rejected and will not be signed. Go back to "
            "adjust the template or the document list if needed — at least "
            "one valid document is required to continue. If some documents "
            "have a different page count, a selector lets you sign each file "
            "on its own first or last page — that page must still match the "
            "template exactly."
        ),
        "fr": (
            "Chaque document est comparé au modèle : il doit avoir le même "
            "nombre de pages et exactement les mêmes dimensions de pages. Les "
            "documents différents sont rejetés et ne seront pas signés. "
            "Revenez en arrière pour ajuster le modèle ou la liste des "
            "documents si nécessaire — au moins un document valide est requis "
            "pour continuer. Si certains documents n'ont pas le même nombre "
            "de pages, un sélecteur permet de signer chaque fichier sur sa "
            "première ou sa dernière page — cette page doit toujours "
            "correspondre exactement au modèle."
        ),
        "nl": (
            "Elk document wordt met het sjabloon vergeleken: het moet "
            "hetzelfde aantal pagina's en exact dezelfde paginagrootten "
            "hebben. Afwijkende documenten worden geweigerd en niet "
            "ondertekend. Ga zo nodig terug om het sjabloon of de "
            "documentlijst aan te passen — er is minstens één geldig document "
            "nodig om verder te gaan. Als sommige documenten een ander aantal "
            "pagina's hebben, kunt u met een keuzelijst elk bestand op zijn "
            "eerste of laatste pagina ondertekenen — die pagina moet nog "
            "steeds exact met het sjabloon overeenkomen."
        ),
        "de": (
            "Jedes Dokument wird mit der Vorlage verglichen: Es muss dieselbe "
            "Seitenzahl und exakt dieselben Seitengrößen haben. Abweichende "
            "Dokumente werden abgelehnt und nicht signiert. Gehen Sie bei "
            "Bedarf zurück, um die Vorlage oder die Dokumentliste anzupassen "
            "— mindestens ein gültiges Dokument ist erforderlich, um "
            "fortzufahren. Haben einige Dokumente eine andere Seitenzahl, "
            "lässt ein Auswahlfeld jede Datei auf ihrer ersten oder letzten "
            "Seite signieren — diese Seite muss weiterhin exakt der Vorlage "
            "entsprechen."
        ),
        "es": (
            "Cada documento se compara con la plantilla: debe tener el mismo "
            "número de páginas y exactamente las mismas dimensiones. Los "
            "documentos diferentes se rechazan y no se firmarán. Vuelva atrás "
            "para ajustar la plantilla o la lista de documentos si es "
            "necesario — se necesita al menos un documento válido para "
            "continuar. Si algunos documentos tienen un número de páginas "
            "distinto, un selector permite firmar cada archivo en su primera "
            "o última página — esa página debe seguir coincidiendo "
            "exactamente con la plantilla."
        ),
        "pt": (
            "Cada documento é comparado com o modelo: deve ter o mesmo número "
            "de páginas e exatamente as mesmas dimensões. Os documentos "
            "diferentes são rejeitados e não serão assinados. Volte atrás "
            "para ajustar o modelo ou a lista de documentos, se necessário — "
            "é preciso pelo menos um documento válido para continuar. Se "
            "alguns documentos tiverem um número de páginas diferente, um "
            "seletor permite assinar cada ficheiro na sua primeira ou última "
            "página — essa página deve continuar a corresponder exatamente "
            "ao modelo."
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
            "eID (QES) — a qualified signature with your Belgian identity "
            "card, legally equivalent to a handwritten one. Needs a card "
            "reader and one PIN entry per document; your national register "
            "number is embedded in every signature.\n\n"
            "Azure (AES) — an advanced signature with your personal "
            "certificate in Azure Key Vault. One Microsoft sign-in for the "
            "whole batch, no card; only the document fingerprint ever leaves "
            "this machine.\n\n"
            "Image — pastes a picture. NOT a cryptographic signature, no "
            "legal value; works fully offline.\n\n"
            "The PAdES level sets durability. Keep b-lta: the signature stays "
            "verifiable for decades (needs network). Choose b-b only to sign "
            "offline. The full glossary is available in English under “Full "
            "documentation”."
        ),
        "fr": (
            "eID (QES) — signature qualifiée avec votre carte d'identité "
            "belge, juridiquement équivalente à une signature manuscrite. "
            "Nécessite un lecteur de carte et un code PIN par document ; "
            "votre numéro de registre national est intégré dans chaque "
            "signature.\n\n"
            "Azure (AES) — signature avancée avec votre certificat personnel "
            "dans Azure Key Vault. Une seule connexion Microsoft pour tout le "
            "lot, sans carte ; seule l'empreinte du document quitte cette "
            "machine.\n\n"
            "Image — colle une image. Ce N'EST PAS une signature "
            "cryptographique, sans valeur juridique ; fonctionne entièrement "
            "hors ligne.\n\n"
            "Le niveau PAdES règle la durabilité. Gardez b-lta : la signature "
            "reste vérifiable pendant des décennies (réseau requis). "
            "Choisissez b-b uniquement pour signer hors ligne. Le glossaire "
            "complet est disponible en anglais sous « Documentation "
            "complète »."
        ),
        "nl": (
            "eID (QES) — een gekwalificeerde handtekening met uw Belgische "
            "identiteitskaart, juridisch gelijkwaardig aan een handgeschreven "
            "handtekening. Vereist een kaartlezer en één pincode per "
            "document; uw rijksregisternummer wordt in elke handtekening "
            "opgenomen.\n\n"
            "Azure (AES) — een geavanceerde handtekening met uw persoonlijke "
            "certificaat in Azure Key Vault. Eén Microsoft-aanmelding voor de "
            "hele reeks, zonder kaart; alleen de vingerafdruk van het "
            "document verlaat deze computer.\n\n"
            "Afbeelding — plakt een afbeelding. GEEN cryptografische "
            "handtekening, zonder juridische waarde; werkt volledig "
            "offline.\n\n"
            "Het PAdES-niveau bepaalt de duurzaamheid. Behoud b-lta: de "
            "handtekening blijft tientallen jaren verifieerbaar (netwerk "
            "vereist). Kies b-b alleen om offline te ondertekenen. De "
            "volledige woordenlijst is in het Engels beschikbaar onder "
            "“Volledige documentatie”."
        ),
        "de": (
            "eID (QES) — eine qualifizierte Signatur mit Ihrer belgischen "
            "Identitätskarte, rechtlich der handschriftlichen Unterschrift "
            "gleichgestellt. Benötigt einen Kartenleser und eine PIN-Eingabe "
            "pro Dokument; Ihre nationale Registernummer wird in jede "
            "Signatur eingebettet.\n\n"
            "Azure (AES) — eine fortgeschrittene Signatur mit Ihrem "
            "persönlichen Zertifikat in Azure Key Vault. Eine einzige "
            "Microsoft-Anmeldung für den ganzen Stapel, ohne Karte; nur der "
            "Fingerabdruck des Dokuments verlässt diesen Rechner.\n\n"
            "Bild — fügt ein Bild ein. KEINE kryptografische Signatur, ohne "
            "Rechtswert; funktioniert vollständig offline.\n\n"
            "Das PAdES-Niveau bestimmt die Haltbarkeit. Behalten Sie b-lta: "
            "Die Signatur bleibt über Jahrzehnte prüfbar (Netzwerk "
            "erforderlich). Wählen Sie b-b nur, um offline zu signieren. Das "
            "vollständige Glossar ist auf Englisch unter „Vollständige "
            "Dokumentation“ verfügbar."
        ),
        "es": (
            "eID (QES) — firma cualificada con su tarjeta de identidad "
            "belga, legalmente equivalente a la manuscrita. Requiere un "
            "lector de tarjetas y un PIN por documento; su número de registro "
            "nacional se incrusta en cada firma.\n\n"
            "Azure (AES) — firma avanzada con su certificado personal en "
            "Azure Key Vault. Un solo inicio de sesión de Microsoft para todo "
            "el lote, sin tarjeta; solo la huella del documento sale de este "
            "equipo.\n\n"
            "Imagen — pega una imagen. NO es una firma criptográfica, sin "
            "valor legal; funciona totalmente sin conexión.\n\n"
            "El nivel PAdES define la durabilidad. Mantenga b-lta: la firma "
            "seguirá siendo verificable durante décadas (requiere red). Elija "
            "b-b solo para firmar sin conexión. El glosario completo está "
            "disponible en inglés en «Documentación completa»."
        ),
        "pt": (
            "eID (QES) — assinatura qualificada com o seu cartão de "
            "identidade belga, juridicamente equivalente à manuscrita. Requer "
            "um leitor de cartões e um PIN por documento; o seu número de "
            "registo nacional é incorporado em cada assinatura.\n\n"
            "Azure (AES) — assinatura avançada com o seu certificado pessoal "
            "no Azure Key Vault. Um único início de sessão Microsoft para "
            "todo o lote, sem cartão; apenas a impressão digital do documento "
            "sai deste computador.\n\n"
            "Imagem — cola uma imagem. NÃO é uma assinatura criptográfica, "
            "sem valor legal; funciona totalmente offline.\n\n"
            "O nível PAdES define a durabilidade. Mantenha b-lta: a "
            "assinatura permanece verificável durante décadas (requer rede). "
            "Escolha b-b apenas para assinar offline. O glossário completo "
            "está disponível em inglês em «Documentação completa»."
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
            "Click on the page preview to set the lower-left corner of the "
            "signature. In eID and Azure modes you may skip this: the "
            "vignette then goes bottom-right on the last page. The target "
            "page field selects the page that actually gets signed — adjust "
            "it if a document must be signed on a different page than the "
            "previewed template page. Documents without that page will fail "
            "with a clear message. While the first/last-page choice from the "
            "validation step applies, the preview is locked on that page and "
            "the target-page field is disabled."
        ),
        "fr": (
            "Cliquez sur l'aperçu de la page pour définir le coin inférieur "
            "gauche de la signature. En modes eID et Azure, vous pouvez "
            "ignorer cette étape : la vignette ira alors en bas à droite de "
            "la dernière page. Le champ « page cible » désigne la page "
            "réellement signée — ajustez-le si un document doit être signé "
            "sur une autre page que celle du modèle affiché. Les documents "
            "sans cette page échoueront avec un message clair. Tant que le "
            "choix première/dernière page de l'étape de validation "
            "s'applique, l'aperçu est verrouillé sur cette page et le champ "
            "« page cible » est désactivé."
        ),
        "nl": (
            "Klik op het paginavoorbeeld om de linkerbenedenhoek van de "
            "handtekening te bepalen. In de modi eID en Azure mag u dit "
            "overslaan: het vignet komt dan rechtsonder op de laatste pagina. "
            "Het veld 'doelpagina' bepaalt welke pagina daadwerkelijk wordt "
            "ondertekend — pas het aan als een document op een andere pagina "
            "moet worden ondertekend dan de getoonde sjabloonpagina. "
            "Documenten zonder die pagina zullen mislukken met een duidelijke "
            "melding. Zolang de keuze eerste/laatste pagina uit de "
            "validatiestap geldt, is het voorbeeld op die pagina vergrendeld "
            "en is het veld 'doelpagina' uitgeschakeld."
        ),
        "de": (
            "Klicken Sie auf die Seitenvorschau, um die linke untere Ecke der "
            "Signatur festzulegen. In den Modi eID und Azure können Sie dies "
            "überspringen: Die Vignette kommt dann unten rechts auf die "
            "letzte Seite. Das Feld „Zielseite“ bestimmt die tatsächlich "
            "signierte Seite — passen Sie es an, wenn ein Dokument auf einer "
            "anderen Seite signiert werden soll als der angezeigten "
            "Vorlagenseite. Dokumente ohne diese Seite schlagen mit einer "
            "klaren Meldung fehl. Solange die Wahl erste/letzte Seite aus dem "
            "Prüfschritt gilt, ist die Vorschau auf dieser Seite gesperrt und "
            "das Feld „Zielseite“ deaktiviert."
        ),
        "es": (
            "Haga clic en la vista previa para fijar la esquina inferior "
            "izquierda de la firma. En los modos eID y Azure puede omitirlo: "
            "la viñeta irá entonces abajo a la derecha en la última página. "
            "El campo «página de destino» selecciona la página que realmente "
            "se firma — ajústelo si un documento debe firmarse en una página "
            "distinta de la mostrada. Los documentos sin esa página fallarán "
            "con un mensaje claro. Mientras se aplique la elección de "
            "primera/última página del paso de validación, la vista previa "
            "queda bloqueada en esa página y el campo «página de destino» "
            "está desactivado."
        ),
        "pt": (
            "Clique na pré-visualização para definir o canto inferior "
            "esquerdo da assinatura. Nos modos eID e Azure pode saltar este "
            "passo: a vinheta ficará em baixo à direita na última página. O "
            "campo «página de destino» seleciona a página realmente assinada "
            "— ajuste-o se um documento tiver de ser assinado numa página "
            "diferente da mostrada. Os documentos sem essa página falharão "
            "com uma mensagem clara. Enquanto a escolha primeira/última "
            "página do passo de validação se aplicar, a pré-visualização fica "
            "bloqueada nessa página e o campo «página de destino» fica "
            "desativado."
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
            "Check the summary, then start the batch. eID mode asks for your "
            "PIN once per document; Azure mode may open a Microsoft sign-in "
            "window if you have not signed in yet. Levels above b-b contact "
            "the timestamp authority and revocation services, so network "
            "access is required. Keep the window open while signing is in "
            "progress."
        ),
        "fr": (
            "Vérifiez le récapitulatif, puis lancez le lot. Le mode eID "
            "demande votre PIN une fois par document ; le mode Azure peut "
            "ouvrir une fenêtre de connexion Microsoft si vous n'êtes pas "
            "encore connecté. Au-delà de b-b, l'autorité d'horodatage et les "
            "services de révocation sont contactés : un accès réseau est "
            "requis. Gardez la fenêtre ouverte pendant la signature."
        ),
        "nl": (
            "Controleer het overzicht en start de reeks. De eID-modus vraagt "
            "uw pincode één keer per document; de Azure-modus kan een "
            "Microsoft-aanmeldvenster openen als u nog niet bent aangemeld. "
            "Boven b-b worden de tijdstempelautoriteit en de "
            "intrekkingsdiensten gecontacteerd: netwerktoegang is vereist. "
            "Houd het venster open zolang het ondertekenen bezig is."
        ),
        "de": (
            "Prüfen Sie die Übersicht und starten Sie den Stapel. Der "
            "eID-Modus fragt Ihre PIN einmal pro Dokument ab; der Azure-Modus "
            "kann ein Microsoft-Anmeldefenster öffnen, falls Sie noch nicht "
            "angemeldet sind. Oberhalb von b-b werden Zeitstempel- und "
            "Sperrdienste kontaktiert: Netzwerkzugang ist erforderlich. "
            "Lassen Sie das Fenster geöffnet, solange signiert wird."
        ),
        "es": (
            "Compruebe el resumen y lance el lote. El modo eID pide su PIN "
            "una vez por documento; el modo Azure puede abrir una ventana de "
            "inicio de sesión de Microsoft si aún no ha iniciado sesión. Por "
            "encima de b-b se contacta con la autoridad de sellado de tiempo "
            "y los servicios de revocación: se necesita acceso a la red. "
            "Mantenga la ventana abierta mientras se firma."
        ),
        "pt": (
            "Verifique o resumo e inicie o lote. O modo eID pede o PIN uma "
            "vez por documento; o modo Azure pode abrir uma janela de início "
            "de sessão Microsoft se ainda não tiver iniciado sessão. Acima de "
            "b-b são contactados a autoridade de selos temporais e os "
            "serviços de revogação: é necessário acesso à rede. Mantenha a "
            "janela aberta enquanto a assinatura decorre."
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
            "Every document of the batch is listed with its outcome. "
            "Successful lines show the achieved signature level and, where "
            "applicable, the long-term-validation status. Failed lines "
            "explain the reason; those documents were not signed. Finish "
            "returns to the welcome screen."
        ),
        "fr": (
            "Chaque document du lot est listé avec son résultat. Les lignes "
            "réussies indiquent le niveau de signature atteint et, le cas "
            "échéant, l'état de la validation à long terme. Les lignes en "
            "échec expliquent la raison ; ces documents n'ont pas été signés. "
            "« Terminer » revient à l'écran d'accueil."
        ),
        "nl": (
            "Elk document van de reeks staat vermeld met zijn resultaat. "
            "Geslaagde regels tonen het bereikte handtekeningniveau en, "
            "indien van toepassing, de status van de langetermijnvalidatie. "
            "Mislukte regels geven de reden; die documenten zijn niet "
            "ondertekend. 'Voltooien' keert terug naar het welkomstscherm."
        ),
        "de": (
            "Jedes Dokument des Stapels wird mit seinem Ergebnis aufgeführt. "
            "Erfolgreiche Zeilen zeigen das erreichte Signaturniveau und "
            "gegebenenfalls den Status der Langzeitvalidierung. "
            "Fehlgeschlagene Zeilen nennen den Grund; diese Dokumente wurden "
            "nicht signiert. „Fertigstellen“ kehrt zum Startbildschirm "
            "zurück."
        ),
        "es": (
            "Cada documento del lote aparece con su resultado. Las líneas "
            "correctas muestran el nivel de firma alcanzado y, en su caso, el "
            "estado de la validación a largo plazo. Las líneas con error "
            "explican el motivo; esos documentos no se firmaron. «Finalizar» "
            "vuelve a la pantalla de bienvenida."
        ),
        "pt": (
            "Cada documento do lote é listado com o seu resultado. As linhas "
            "com êxito mostram o nível de assinatura alcançado e, quando "
            "aplicável, o estado da validação de longo prazo. As linhas "
            "falhadas explicam o motivo; esses documentos não foram "
            "assinados. «Concluir» regressa ao ecrã inicial."
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
        "en": "Image stamp — no cryptographic signature",
        "fr": "Image — aucune signature cryptographique",
        "nl": "Afbeelding — geen cryptografische handtekening",
        "de": "Bildstempel — keine kryptografische Signatur",
        "es": "Imagen — sin firma criptográfica",
        "pt": "Imagem — sem assinatura criptográfica",
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
        "en": "Visual stamp only, no legal value; works fully offline.",
        "fr": "Simple tampon visuel, sans valeur juridique ; fonctionne entièrement hors ligne.",
        "nl": "Alleen een visuele stempel, zonder juridische waarde; werkt volledig offline.",
        "de": "Nur ein sichtbarer Stempel ohne Rechtswert; funktioniert vollständig offline.",
        "es": "Solo un sello visual, sin valor legal; funciona totalmente sin conexión.",
        "pt": "Apenas um carimbo visual, sem valor legal; funciona totalmente offline.",
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
        "en": "Full documentation (English)…",
        "fr": "Documentation complète (en anglais)…",
        "nl": "Volledige documentatie (Engels)…",
        "de": "Vollständige Dokumentation (Englisch)…",
        "es": "Documentación completa (en inglés)…",
        "pt": "Documentação completa (em inglês)…",
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
    "place.image_choose": {
        "en": "Choose image…",
        "fr": "Choisir l'image…",
        "nl": "Afbeelding kiezen…",
        "de": "Bild wählen…",
        "es": "Elegir imagen…",
        "pt": "Escolher imagem…",
    },
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
    "place.image_missing": {
        "en": "Choose an image, then click on the preview to place it.",
        "fr": "Choisissez une image, puis cliquez sur l'aperçu pour la positionner.",
        "nl": "Kies een afbeelding en klik daarna op het voorbeeld om ze te plaatsen.",
        "de": "Wählen Sie ein Bild und klicken Sie dann auf die Vorschau, um es zu platzieren.",
        "es": "Elija una imagen y luego haga clic en la vista previa para colocarla.",
        "pt": "Escolha uma imagem e depois clique na pré-visualização para a posicionar.",
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
        "en": "Placement: {place}",
        "fr": "Positionnement : {place}",
        "nl": "Plaatsing: {place}",
        "de": "Platzierung: {place}",
        "es": "Colocación: {place}",
        "pt": "Posicionamento: {place}",
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
