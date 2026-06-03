# Construire les exécutables autonomes (Linux & Windows)

`signApp` est empaqueté avec **PyInstaller** en **deux binaires autonomes**, à
partir d'un seul fichier de recette `signApp.spec` :

| Binaire | Type | Rôle | Double-clic |
|---|---|---|---|
| **`signApp`** (`.exe` sous Windows) | fenêtré (`console=False`) | ouvre l'**interface graphique** CustomTkinter | ➜ lance la GUI |
| **`signApp-cli`** (`.exe` sous Windows) | console (`console=True`) | **signature/tampon en lot** depuis un terminal | ➜ affiche l'aide |

Le binaire console est volontairement **headless** (sans Tk) donc plus léger ; le
binaire fenêtré embarque Tk + CustomTkinter + tout le moteur.

> ℹ️ **PyInstaller ne fait pas de cross-compilation.** Un `.exe` Windows doit être
> produit *sur* Windows (machine réelle, CI Windows, ou Wine). Le binaire Linux se
> compile nativement sous Linux. Les trois routes Windows sont décrites plus bas.

---

## 1. Linux (natif)

```bash
./build_linux.sh
# -> dist/signApp        (GUI)
# -> dist/signApp-cli    (CLI)
```

Le binaire fenêtré exige `tkinter`/`_tkinter` dans le Python du venv. Sur
Ubuntu/Debian si nécessaire : `sudo apt install python3-tk` (ou `python3.14-tk`).

**Compatibilité glibc** : compilez sur la **plus ancienne** distribution à
supporter. PyInstaller embarque Python et les libs Python mais lie dynamiquement
la glibc système ; un binaire compilé sur une glibc récente échouera sur une
cible plus ancienne (`GLIBC_2.xx not found`).

---

## 2. Windows — sur une vraie machine Windows (le plus fiable)

Prérequis : **Python 64 bits 3.12 / 3.13 / 3.14** installé avec l'option
*« tcl/tk and IDLE »* cochée, `py`/`python` dans le `PATH`.

```bat
build_windows.bat
:: -> dist\signApp.exe        (GUI)
:: -> dist\signApp-cli.exe    (CLI)
```

C'est la route recommandée pour un livrable de production : elle produit un `.exe`
natif testable immédiatement (y compris le mode eID si un lecteur + carte sont
présents).

---

## 3. Windows — via GitHub Actions (CI, reproductible)

Le workflow `.github/workflows/build.yml` compile **Windows + Linux** sur les
runners GitHub à chaque `push`/tag, lance un smoke-test (mode image, sans carte)
et publie les binaires en artefacts.

```bash
# une seule fois : pousser le dépôt sur GitHub
git remote add origin git@github.com:<vous>/<repo>.git
git push -u origin main
```

Puis : onglet **Actions** → run → **Artifacts** → `signApp-windows-latest` /
`signApp-ubuntu-latest`. Déclenchable aussi manuellement (*workflow_dispatch*).

La version Python du CI est `3.13` (variable `PYTHON_VERSION` en tête du
workflow ; `3.14` fonctionne aussi).

---

## 4. Windows — via Wine, depuis Linux (best effort)

> ⚠️ Officiellement **non supporté** par PyInstaller. À réserver à un `.exe` de
> dépannage : la **GUI ne s'affiche souvent pas** correctement *sous Wine*
> (bugs Tcl/Tk + GDI propres à Wine, absents sur un vrai Windows), et le **mode
> eID n'est pas testable** sous Wine (ni lecteur, ni carte, ni `beidpkcs11.dll`).

```bash
sudo apt install wine        # unique étape root, à lancer vous-même
./build_windows_wine.sh      # télécharge Python Windows, installe les deps, build
# -> dist/signApp.exe, dist/signApp-cli.exe
```

Le script crée un prefix Wine 64 bits (`~/.wine-signapp`), installe Python 3.12
Windows (toutes les *wheels* `win_amd64` épinglées existent en cp312), `pip
install --only-binary=:all:` (un wheel manquant échoue franchement plutôt que de
tenter une compilation impossible sous Wine), puis lance PyInstaller.

**Le résultat doit être validé sur un vrai Windows** avant diffusion.

---

## Dépendances *runtime* (NON embarquées — à installer sur la machine cible)

Ces composants sont chargés dynamiquement et **ne peuvent pas** être empaquetés :

- **Middleware eID belge** — fournit la bibliothèque PKCS#11 chargée par chemin
  (`pkcs11.lib(...)`), **mode `beid` uniquement** :
  - Windows : `C:\Windows\System32\beidpkcs11.dll`
  - Linux : `/usr/lib/…/libbeidpkcs11.so` (+ `pcscd` lancé)
  - macOS : `/usr/local/lib/libbeidpkcs11.dylib`
  - À installer depuis <https://eid.belgium.be>. Surchargez le chemin via `--lib`.
  - Nécessite aussi un **lecteur + carte eID insérée** + le service PC/SC.
L'**aperçu de page** de la GUI (étape 6) est rendu par **pypdfium2** (moteur
PDFium **embarqué** dans l'exécutable) : rien à installer, sur aucun OS.
`poppler` (`pdftoppm`) n'est plus qu'un **repli optionnel** utilisé seulement
s'il est déjà présent sur la machine (cf. `core.render_page_image`).

Le **mode image** ne dépend d'aucun de ces composants : il est pleinement
fonctionnel et testable sans matériel.

---

## Personnalisation

- **Icône** : déposez `signApp.ico` (Windows) ou `signApp.icns` (macOS) à côté de
  `signApp.spec` — il sera repris automatiquement. Sous Linux l'icône est ignorée
  par PyInstaller (fournissez plutôt un fichier `.desktop` avec `Icon=`).
- **onefile → onedir** : par défaut chaque binaire est *onefile* (un seul
  fichier, démarrage un peu plus lent car décompressé dans un dossier temporaire).
  Pour un démarrage plus rapide (dossier `exe + _internal/`), passez
  `exclude_binaries=True` dans chaque `EXE(...)` et ajoutez un `COLLECT(...)` —
  voir la doc PyInstaller.

---

## Dépannage

| Symptôme | Cause | Correctif |
|---|---|---|
| GUI : `Can't find a usable init.tcl` / fenêtre vide | données Tcl/Tk non collectées | rebuild avec un Python ayant un `tkinter` complet ; le hook `_tkinter` les collecte dans `_tcl_data`/`_tk_data` |
| GUI : `FileNotFoundError` sur un thème `.json` | assets customtkinter manquants | s'assurer que `pyinstaller-hooks-contrib` est installé (il l'est) ; le spec fait aussi `collect_all('customtkinter')` |
| `ModuleNotFoundError: pkcs11._pkcs11` | extension native non embarquée | déjà géré (`collect_dynamic_libs('pkcs11')` + hiddenimport) ; vérifier le log de build |
| `ZoneInfoNotFoundError` à la signature **sur Windows** | base zoneinfo absente | `tzdata` (installé via `requirements-build.txt` sur Windows ; collecté par le spec) |
| Erreur OpenSSL d'`oscrypto` | parsing de version sur certains OpenSSL | non déclenché par les modes image/eID de cette app (le trust-list Linux lit des PEM) ; sinon `pip install` un oscrypto corrigé ou pyhanko-certvalidator ≥ 0.41 |
| `mode beid` « échoue » sur machine vierge | middleware eID/lecteur/carte absents | **attendu** : installer le middleware eID (voir ci-dessus) ; ce n'est pas un bug d'empaquetage |
| Antivirus / SmartScreen bloque le `.exe` | binaires onefile non signés souvent signalés | `upx=False` (déjà le cas) ; idéalement **signer (Authenticode)** le `.exe` sur Windows ; au besoin passer en *onedir* |
| `GLIBC_2.xx not found` (Linux) | compilé sur une glibc trop récente | recompiler sur la plus ancienne distribution cible |

---

## Ce qui ne peut PAS être testé sans matériel

Le **mode eID** (signature cryptographique via la carte) exige un lecteur + une
carte eID + le middleware + une saisie de PIN par document : il ne peut être
validé que sur une vraie machine équipée. L'empaquetage est vérifié jusqu'au
chargement de la bibliothèque PKCS#11 ; la signature réelle doit faire l'objet
d'un **test d'acceptation sur Windows réel** (signer un PDF, vérifier la
signature dans Adobe Reader / pyHanko) avant diffusion.
