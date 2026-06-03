# signApp

Signature **par lot** de fichiers PDF avec la **carte d'identité électronique
belge (eID)**, ou apposition d'une **image** de signature — avec validation des
entrées par rapport à un modèle, en **ligne de commande** comme en **interface
graphique** (CustomTkinter).

> ⚖️ Le mode eID utilise le certificat de **non-répudiation** de la carte,
> juridiquement équivalent à une signature manuscrite. Le **numéro de registre
> national** est inscrit dans chaque signature produite — attention à la
> diffusion des PDF signés.

---

## Deux modes de signature

| Mode | Carte requise | Nature | Rendu |
|---|---|---|---|
| **`beid`** | oui (lecteur + carte + PIN par document) | signature **cryptographique** eID (pyHanko via le middleware PKCS#11) | **vignette** visible : photo du titulaire + « Signed by: » / nom / date |
| **`image`** | non | **tampon d'image** (ce n'est *pas* une signature cryptographique) | l'image fournie, posée à une position choisie |

Dans les deux modes, la même vignette/image + page + position est appliquée à
**tous** les documents du lot — la validation par modèle garantit que les
fichiers sont géométriquement identiques.

## Validation par modèle (`--template`)

Si un PDF modèle est fourni, chaque entrée est acceptée **uniquement** si elle a
le **même nombre de pages** ET des **dimensions par page exactement identiques**
(égalité stricte, sans tolérance). Les fichiers rejetés ne sont jamais signés ;
le motif du rejet est affiché (récapitulatif CLI / tableau GUI).

## Sortie

Les fichiers sont écrits `{nom}_signe.pdf` dans le dossier de sortie et **ne
sont jamais écrasés** : en cas de collision, ` - 1`, ` - 2`, … sont ajoutés.

---

## Prérequis (runtime)

- **Mode `beid`** : le **middleware eID belge** installé
  (<https://eid.belgium.be>), qui fournit la bibliothèque PKCS#11
  (`libbeidpkcs11.so` / `beidpkcs11.dll` / `…dylib`), un **lecteur + carte eID
  insérée**, et le service **PC/SC** (`pcscd`) en marche. Le PIN est demandé
  pour **chaque** document.
- **Mode `image`** : rien de particulier — tampon PDF pur.
- **Interface graphique** : `customtkinter` + un Python avec `tkinter` et un
  affichage. L'aperçu de page (étape 6) est rendu par **pypdfium2** (moteur
  PDFium **embarqué**) — aucune dépendance externe à installer. (poppler /
  `pdftoppm` n'est plus qu'un repli optionnel s'il est déjà présent.)

## Installation (depuis les sources)

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
# GUI sous Ubuntu/Debian, si tkinter manque :  sudo apt install python3-tk
```

---

## Utilisation — ligne de commande

```bash
# Signature eID (vignette en bas à droite de la dernière page) :
./venv/bin/python sign_pdfs_beid.py --input ../pdfs --output ../signes --mode beid --pades

# Tampon d'image (sans carte), validé contre un modèle :
./venv/bin/python sign_pdfs_beid.py --mode image \
  --template ../pdfs/MODELE.pdf --input ../pdfs --output ../signes \
  --image-path signature.png --page 1 --x 360 --y 150

# Interface graphique :
./venv/bin/python sign_pdfs_beid.py --gui
```

### Options

| Drapeau | Signification |
|---|---|
| `--gui` | lance l'interface graphique ; sinon exécution en mode console. |
| `--input <chemins…>` | fichiers et/ou dossiers à traiter (les dossiers sont parcourus pour `*.pdf`). |
| `--output <dossier>` | dossier de sortie (`{nom}_signe.pdf`, jamais écrasé). |
| `--template <pdf>` | PDF modèle ; si fourni, les entrées sont validées contre lui. |
| `--mode beid\|image` | mode de signature (défaut `beid`). |
| `--image-path <img>` | image à apposer (**requis** en `--mode image`). |
| `--page <N>` | page cible, **1-based**. Image : page d'insertion. beID : page de la vignette. |
| `--x <pt> --y <pt>` | coin inférieur gauche, en points depuis le bas-gauche de la page. beID : **omettre les deux ⇒ bas-droite de la dernière page**. |
| `--pades` | signature **PAdES** (archivage long terme). |
| `--lib <chemin>` | chemin vers la bibliothèque PKCS#11 eID (sinon valeur par défaut selon l'OS). |
| `--field <nom>` | nom de base du champ de signature (mode beid). |

> Compatibilité ascendante : l'ancienne forme positionnelle `entrées… dossier_sortie`
> reste acceptée si `--input`/`--output` sont absents.

## Utilisation — interface graphique

Un assistant vertical déroule le flux : **1.** modèle → **2.** fichiers →
**3.** dossier de sortie → **4.** validation (tableau pass/échec) → **5.** mode
(eID/image + PAdES) → **6.** page + position (aperçu réel de la page, clic pour
placer) → **7.** lancer → **8.** récapitulatif par document.

---

## Exécutables autonomes (Linux & Windows)

Le projet se compile en **deux binaires autonomes** par OS (un GUI fenêtré
`signApp`, un CLI console `signApp-cli`) — aucun Python requis sur la machine
cible. Voir **[BUILD.md](BUILD.md)** pour toutes les routes (natif Linux,
Windows natif, Wine, et CI GitHub Actions).

```bash
./build_linux.sh        # Linux  -> dist/signApp , dist/signApp-cli
build_windows.bat       # Windows -> dist\signApp.exe , dist\signApp-cli.exe
```

Le middleware eID reste une **dépendance runtime** (mode beid) et n'est jamais
embarqué ; l'aperçu de page, lui, fonctionne sans rien installer (PDFium
embarqué via pypdfium2).

## Tests

Suite `unittest` headless (sans carte ni tkinter) :

```bash
./venv/bin/python -m unittest -v
```

## Structure du projet

| Fichier | Rôle |
|---|---|
| `sign_pdfs_beid.py` | cœur + point d'entrée CLI (logique métier, importable sans tkinter). |
| `gui.py` | interface CustomTkinter (façade au-dessus du cœur). |
| `gui_main.py` | point d'entrée du binaire fenêtré (ouvre la GUI). |
| `test_sign_pdfs_beid.py` | suite de tests `unittest`. |
| `signApp.spec` | recette PyInstaller (deux binaires). |
| `build_*.sh` / `build_windows.bat` | scripts de build. |
| `.github/workflows/build.yml` | CI : binaires Windows + Linux en artefacts. |
| `BUILD.md` | guide d'empaquetage détaillé. |
