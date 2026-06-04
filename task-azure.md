# Mission

Add a third signing mode, **`azure`**, to this repository: sign each PDF with the **authenticated user's own personal certificate held in Azure Key Vault**, via an **interactive Microsoft Entra ID login**, producing a **personal Advanced Electronic Signature (AES)** in PAdES form (reusing the existing `--pades-level`, default `b-lta`). This mode is **additive** — `beid` (eID QES) and `image` must keep working unchanged.

Persist this entire specification to `task-azure.md` at the repo root, then iterate task by task until **every acceptance criterion in §9 is checked**, committing after each green step (Conventional Commits).

---

# 1. First step — reconcile with the ACTUAL current code

The repository has evolved (a previous loop implemented the B-LTA upgrade: `--pades-level {b-b,b-t,b-lt,b-lta}` default `b-lta`, RFC 3161 timestamping, `embed_validation_info`/`use_pades_lta`, a `trust.py` EU-LOTL trust provider, post-signing self-verification, an RRN warning, a pinned digest).

**Before writing anything, use serena to read the current state** of `sign_pdfs_beid.py`, `gui.py`, `test_sign_pdfs_beid.py`, `requirements.txt`, `signApp.spec`, `README.md`, `CLAUDE.md`, `BUILD.md`. Map the real, current shapes of `RunConfig`, `sign_one()`, `process_batch()`, `build_arg_parser()`, `resolve_config()`, `validate_config()`, the verification helper, and the trust/validation-context plumbing. **This spec describes the target; the current code is the source of truth for how to get there.** If anything here contradicts the actual code, adapt and note it in `task-azure.md`.

---

# 2. Context that does not change

- All business logic stays in `sign_pdfs_beid.py` and must keep importing **without tkinter** (regression test `HeadlessImport`).
- `gui.py` is a thin CustomTkinter façade; the worker-thread / `queue` / `after()` invariants must be preserved (no Tk calls off the main thread).
- Tests use stdlib **`unittest`** only. Do not add another framework.
- The existing CI smoke test (image mode, no card) and headless invariants must keep passing.
- Conventional Commits; English comments/CLI text; `from __future__ import annotations`; dataclasses; type hints; pure/testable helpers.

---

# 3. Tooling mandate (do not skip)

- **context7 is authoritative for APIs.** Before coding, fetch current docs for: `pyHanko` (`signers`, `ExternalSigner` / interrupted-signing flow, `PdfSignatureMetadata`, `timestamps`), `pyhanko-certvalidator` (`ValidationContext`, trust roots, fetching), and the Azure SDKs `azure-identity` (`InteractiveBrowserCredential`, `DeviceCodeCredential`, token caching/claims), `azure-keyvault-keys` (`CryptographyClient`, `SignatureAlgorithm`), `azure-keyvault-certificates` (`CertificateClient`). Confirm the **Key Vault data-plane token scope** and the **sign operation** signature — do not invent method names.
- **serena** for navigation and symbol-level edits.
- Use **web research** to confirm anything context7 lacks (e.g. exact Entra scope strings).

---

# 4. Architecture (treat as the fixed design)

**Trust model: internal use, each user signs in their own name → personal AES (not QES).**

- **Identity / sole control.** The user authenticates interactively with their Microsoft account (Entra ID). The signed-in identity determines **which key is used**, so the signature is uniquely linked to that person. One interactive login **per batch** (not per document — unlike the eID PIN).
- **Key residence.** Per-user signing keys/certificates live in **Azure Key Vault** (key non-exportable; only the digest is sent to Azure, never the document). The signature CMS is assembled locally with the user's **public certificate + chain** retrieved from Key Vault. The actual signing of the digest is delegated to Key Vault via the pyHanko external/interrupted-signing mechanism.
- **Per-user key resolution.** After login, read the signed-in user's UPN / object-id from the access token claims (or Microsoft Graph `/me`), then resolve the Key Vault key & certificate names from a **configurable template** (default e.g. `sig-{upn}`), overridable by an explicit name. **Security rule:** by default a user may only use the key resolved from their own token claims; an explicit override name must be gated/logged so one user cannot sign as another.
- **Trust for LTV is mode-dependent.** `beid` keeps using the existing EU-LOTL `trust.py`. **`azure` mode must build its `ValidationContext` from the organization's internal CA chain** (ADCS / Entra-issued), supplied via config — **never** the EU LOTL. Refactor the validation-context construction into a small helper that returns the correct anchors per mode.
- **Visible appearance.** No eID photo in `azure` mode. Reuse the existing text-only vignette (the code already supports `photo=None`): "Signed by: \<name\> / at \<date\>", where \<name\> comes from the certificate subject (CN, or given_name + surname), with optional Microsoft Graph `displayName` as a nicer source. Keep `--page/--x/--y` placement; default bottom-right of the last page, exactly like `beid`.
- **eIDAS honesty.** This is an **AES** (advanced), not a QES. Document that explicitly. For internal documents this is appropriate; for documents meant to be relied upon by external third parties, the certificate would need to come from a publicly recognised issuer — out of scope here.

---

# 5. Functional requirements

**R1 — New `azure` mode.** Add `azure` to the `--mode` choices `{beid, image, azure}`. Wire it through `RunConfig`, `validate_config`, `resolve_config`, `process_batch`, the CLI banner, and the GUI.

**R2 — Entra ID interactive authentication.** Add `--azure-auth {interactive,device-code,default}`:
   - `interactive` → `InteractiveBrowserCredential` (default for the GUI),
   - `device-code` → `DeviceCodeCredential` (default for the headless CLI),
   - `default` → `DefaultAzureCredential` (for automation; **document that this breaks the per-user model** and is for testing/CI only).
   Acquire the token once, cache it, and resolve the signed-in user's UPN/oid from the token claims (or Graph `/me`).

**R3 — Azure Key Vault signer.** Implement a dedicated module (e.g. `azure_signer.py`) providing a pyHanko `Signer` (subclass / `ExternalSigner` wiring) that:
   - fetches the user's **certificate + chain** via `CertificateClient`,
   - signs the document digest via `CryptographyClient.sign(...)`, selecting the `SignatureAlgorithm` that matches the key type (RSA → RS256, or PS256 if the key policy is PSS; EC P-256 → ES256, etc.) and the chosen `--digest`,
   - exposes `signing_cert` / `cert_registry` so pyHanko builds a correct CMS,
   - is import-safe without tkinter and unit-testable with the Azure SDK mocked.

**R4 — Config & resolution.** Add CLI flags (each with a `SIGNAPP_AZURE_*` env var; flag > env > default):
   - `--azure-vault-url` (required for `azure` mode),
   - `--azure-key-name` (explicit override) and `--azure-key-name-template` (default `sig-{upn}`),
   - `--azure-cert-name` (if the cert name differs from the key name),
   - `--azure-auth` (see R2),
   - `--azure-trust-anchors <path-or-dir>` (PEM of the internal root/intermediate CA chain; required for `b-lt`/`b-lta` in `azure` mode),
   - `--azure-graph` (opt-in: use Graph `/me` displayName for the vignette).
   Reuse the existing `--pades-level`, `--timestamp-url`, `--digest`, `--no-verify`.

**R5 — PAdES levels & B-LTA reuse.** `azure` mode honours `--pades-level` exactly like `beid`: attach the timestamper for ≥ `b-t`; set `embed_validation_info=True` + the **internal-CA** `ValidationContext(allow_fetching=True)` for `b-lt`/`b-lta`; set `use_pades_lta=True` for `b-lta`. The internal CA must publish reachable CRL/OCSP for revocation embedding; if not, fail clearly (R8) — **no silent downgrade**.

**R6 — One-time setup per batch.** In `process_batch`, for `azure` mode: authenticate **once**, resolve the user + key/cert **once**, build the Azure signer, timestamper, and validation context **once**, then loop over documents. Handle token lifetime for long batches (refresh if needed).

**R7 — Generalise the signing path.** Make `sign_one()` (and the identity/vignette helper) **signer-agnostic** so it accepts any pyHanko `Signer` and an identity object, rather than being hard-bound to the eID signer. Add a `read_cert_identity(cert, graph_name=None)` analogous to the existing eID identity reader, producing the same identity shape (name + `photo=None`).

**R8 — Network / offline behaviour.** `azure` mode requires outbound access to `login.microsoftonline.com`, the vault URL, the TSA, and the internal CA's CRL/OCSP endpoints. On any unreachable endpoint, fail with an actionable message **naming the endpoint and the missing capability**; never downgrade the level silently. `b-b`/`image` remain offline-capable.

**R9 — Self-verification.** The existing post-signing verification must run for `azure` mode too, **using the internal-CA validation context**, detect the achieved level/LTV status, surface it in `DocResult.detail` (e.g. `signed (Azure, <upn>) — PAdES-B-LTA, LTV ok`), and mark a mismatch as a failure. `--no-verify` skips.

**R10 — GUI.** Add a third mode radio **"Azure (Microsoft login)"**. When selected: hide the image picker; show the Azure config (vault URL, optional key override, auth method) and a **"Sign in with Microsoft"** action; keep the page+position canvas. Run login + signing on the worker thread (the system-browser auth is fine off the Tk main thread; never touch widgets from the worker). Cache the token to avoid re-login between batches. Thread `azure_*` config into `RunConfig`.

**R11 — Security & privacy.** Never log tokens, secrets, or key material; only the digest leaves the machine. Enforce the per-user key rule (R4). Add a short startup note in `azure` mode that the signature carries the user's personal certificate identity. (No RRN concern here — that is eID-specific.)

**R12 — Documentation.** Update `README.md`, `CLAUDE.md`, `BUILD.md`: the new mode and all flags; the **AES (not QES)** level and its suitability for internal use; the network requirements; the internal-CA trust requirement and how to provide the chain; the per-user Key Vault provisioning prerequisite; and the eID-vs-Azure trade-off (legal weight vs automation/ergonomics).

---

# 6. Plumbing changes required

- **`RunConfig`**: `mode` now `{beid, image, azure}`; add `azure_vault_url`, `azure_key_name`, `azure_key_name_template` (default `sig-{upn}`), `azure_cert_name`, `azure_auth` (default resolved per CLI/GUI), `azure_trust_anchors`, `azure_use_graph` — all with safe defaults / `None`.
- **`validate_config`**: for `azure` mode require `azure_vault_url`; require `azure_trust_anchors` when `pades_level` ∈ {`b-lt`,`b-lta`}; validate the key-name template / override; keep existing checks.
- **`process_batch`**: branch into the `azure` setup-once path (R6); reuse the generalised `sign_one` (R7).
- **`sign_one` + identity helper**: generalise per R7.
- **Validation-context helper**: refactor so the trust source is selected by mode (`beid` → EU-LOTL `trust.py`; `azure` → internal anchors).
- **`gui.py`**: third mode + Azure panel + sign-in action (R10).
- **`requirements.txt`**: pin `azure-identity`, `azure-keyvault-keys`, `azure-keyvault-certificates` (and confirm transitive `msal`, `azure-core`). If Graph is used, add the minimal client or call the REST endpoint directly.
- **`signApp.spec`**: `azure` mode is **CLI-usable**, so add the Azure packages to the **common** collection (not GUI-only): `collect_all` for `azure`, `azure.identity`, `azure.keyvault.*`, `msal`, plus `copy_metadata` as needed; add any native/hidden imports. **Both** binaries must still build; do **not** exclude azure from the CLI binary.

---

# 7. Testing (stdlib `unittest`, lean — speed over coverage, but no faked crypto)

- Pure: `--mode azure` parsing and all `--azure-*` flags/env precedence; `validate_config` for `azure` (vault required; trust anchors required at b-lt/b-lta); key-name template resolution from a **mocked** token claim (`sig-{upn}`); mode dispatch in `process_batch`.
- `azure_signer.py` with the **Azure SDK fully mocked**: assert it calls `CryptographyClient.sign` with the expected digest and `SignatureAlgorithm`, retrieves the cert/chain, and that the produced CMS has the expected structural shape. Mock all network.
- `read_cert_identity`: name extraction from a synthetic certificate subject (CN and GN+SN cases); `photo is None`.
- Update any existing test affected by the `RunConfig`/`sign_one` generalisation so the suite stays green.
- A **credential canary**: assert the `azure` code path imports and **fails cleanly with an actionable message when no credentials/vault are configured** — without performing a real login.

Real Entra login + Key Vault signing + internal-CA LTV is a **manual acceptance test** (sign a PDF as a real user; verify in Adobe Reader / `pyhanko sign validate` that the achieved level and LTV match, and that the signer identity is the user's). Document it in `BUILD.md`; do not gate CI on it. **Never fabricate a passing test for the real signing path.**

---

# 8. Out of scope (note as follow-ups, do not implement)

- The **local Windows certificate store (CNG)** signing variant (would make the tool Windows-only; documented alternative only).
- Service-principal / managed-identity **automated** signing as a production path (breaks the per-user model; allowed only via `--azure-auth default` for testing).
- Any **QES** upgrade (qualified cert + QSCD).
- **Provisioning** the per-user keys/certificates in Key Vault (Azure-admin prerequisite — document it, don't build it).
- Microsoft Graph beyond an optional `displayName` lookup.

---

# 9. Acceptance criteria (the loop drives to all-checked)

- [ ] `--mode azure` exists alongside `beid`/`image`; both existing modes unchanged and still pass their tests.
- [ ] Interactive Entra ID login (`--azure-auth interactive|device-code|default`); token cached; **one login per batch**, no per-document prompt.
- [ ] Signed-in user's UPN/oid resolved from token claims (or Graph); Key Vault key/cert resolved via `--azure-key-name-template` (default `sig-{upn}`) or explicit override, with the per-user safety rule enforced.
- [ ] `azure_signer.py` signs the digest via Key Vault `CryptographyClient` (correct `SignatureAlgorithm` per key type + `--digest`), builds the CMS from the Key Vault certificate + chain; document never sent to Azure.
- [ ] `--pades-level` honoured in `azure` mode (timestamp ≥ b-t; internal-CA `ValidationContext` + `embed_validation_info` for b-lt/b-lta; `use_pades_lta` for b-lta).
- [ ] Validation/trust is **mode-dependent**: `azure` uses `--azure-trust-anchors` (internal CA); `beid` still uses the EU-LOTL `trust.py`.
- [ ] Visible vignette in `azure` mode shows the user's name from the certificate (or Graph), `photo=None`; placement flags work; default bottom-right last page.
- [ ] Post-signing self-verification runs for `azure` (internal-CA context), reports level/LTV in `DocResult.detail`, fails on mismatch; `--no-verify` skips.
- [ ] No silent level downgrade on network failure; failures name the endpoint/capability; `b-b`/`image` remain offline-capable.
- [ ] GUI third mode "Azure (Microsoft login)" with sign-in action and Azure panel; thread-safety invariants preserved.
- [ ] `requirements.txt` + `signApp.spec` updated; **both** binaries build; azure available in the CLI binary.
- [ ] Tokens/keys never logged; only the digest leaves the machine; per-user key rule enforced.
- [ ] Docs (`README`/`CLAUDE`/`BUILD`) updated: new mode/flags, **AES-not-QES**, network + internal-CA trust requirements, per-user Key Vault prerequisite, eID-vs-Azure trade-off.
- [ ] `python -m unittest -v` green; `HeadlessImport` and the image-mode smoke path pass.

---

# 10. Constraints recap

Ship a working **per-user AES** `azure` mode that reuses the existing B-LTA pipeline. Confirm every pyHanko / pyhanko-certvalidator / Azure SDK API against context7 before coding. Keep `beid`, `image`, and the no-tkinter/headless invariants intact. Trust anchors for `azure` are the **internal CA**, never the EU LOTL. One interactive login per batch. Commit per green step. Never fabricate passing tests for the real login/signing path. Respect the out-of-scope list.
---

# Appendix — §1 reconciliation notes (actual code, 2026-06-04)

Verified with serena against the current tree (HEAD 03c9ae3). The spec's
assumptions hold; how each target maps onto the real code:

- **`RunConfig`** (sign_pdfs_beid.py:705): already carries `pades_level`
  (default `"b-lta"`), `timestamp_url`, `trust_list_url`, `digest`, `verify`,
  `legacy_cms`, `refresh_trust_list`. The azure fields are pure additions.
- **`sign_one()`** (line 406): signature
  `(signer, src, dst, field_name, pades_level, identity, page_index, pos, *,
  digest, legacy_cms, timestamper, validation_context)` — it already treats
  the signer as an opaque pyHanko `Signer` and the identity as
  `CardIdentity(name, photo)`; R7 reduces to loosening the `BEIDSigner` type
  hint/docstring and adding `read_cert_identity()` (photo=None works:
  `build_stamp_style` already supports it).
- **`build_signing_material(cfg) -> SigningMaterial`** (line 783): the
  existing once-per-batch seam (timestamper + ValidationContext + anchors;
  tests stub it). The §6 "validation-context helper selected by mode" slots
  here: beid keeps `trust.get_trust_anchors()` (EU LOTL), azure loads
  `--azure-trust-anchors` PEMs instead. EU-TL anchors are currently passed as
  `extra_trust_roots` because pyHanko validates the TSA chain against the
  same context (pdf_signer.py:753) — the SAME consideration applies to the
  internal-CA context in azure mode (the public TSA must stay validatable),
  so internal anchors are ALSO passed as extra_trust_roots, not trust_roots.
- **`verify_signed_pdf(path, expected_level, *, trust_anchors)`** (line 831):
  already mode-agnostic — R9 is just "pass the internal anchors".
- **`process_batch()`** (line 902): per-mode branch + detail-prefix
  construction; azure adds a third branch with setup-once (R6) and the
  `signed (Azure, <upn>) — <level>` prefix.
- **Detail labels**: `signature_level_label()` + the R8 verification suffix
  (`PAdES-B-LTA, LTV ok`) are reused as-is.
- **Tests**: `_Args` (argparse mimic) must gain the azure defaults;
  `BatchBeidWiring` shows the stubbing pattern to clone for azure batch
  wiring; `SelfVerification` already proves the verifier on real signatures.
- **GUI**: step-5 row hosts the mode radios + level selector; the azure
  panel and sign-in action extend that step; worker-thread/queue/after()
  invariants unchanged.
- **Spec/packaging**: signApp.spec collects common engine packages into BOTH
  binaries via `common_*` lists — azure packages go there (CLI keeps azure).
- Deviation note: the loop prompt says to record reconciliation notes "in
  task.md"; the spec's Mission says persist the spec to `task-azure.md`.
  Both are kept: task-azure.md = spec + these notes; the §9 checklist is
  ticked in BOTH files as criteria complete.
