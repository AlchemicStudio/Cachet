# Mission

Upgrade the Belgian eID PDF signatures produced by this repository from their current **PAdES-B-B** level to the **maximum preservation level, PAdES-B-LTA**, with full Long-Term Validation (LTV): trusted RFC 3161 timestamping, embedded revocation information (OCSP/CRL), and an archival timestamp chain. Also correct the misleading documentation, add post-signing self-verification, and surface the privacy implication of the national register number.

Persist this entire specification to `specifications.md` at the repo root, then iterate task by task until **every acceptance criterion in §7 is checked**, committing after each green step (Conventional Commits).

---

# 1. Repository context

This is `signApp`, a batch PDF signer for the Belgian eID card.

- All business logic lives in `sign_pdfs_beid.py` (core + CLI). It must keep importing **without tkinter** (there is a regression test for this: `HeadlessImport`).
- `gui.py` is a CustomTkinter façade over the core; it currently exposes a boolean **PAdES** checkbox.
- `test_sign_pdfs_beid.py` is a stdlib **`unittest`** suite (no card, no tkinter). Do **not** introduce another test framework.
- The signing entry points are `sign_one()` (single PDF) and `process_batch()` (batch, shared CLI/GUI), configured by the `RunConfig` dataclass and `build_arg_parser()` / `resolve_config()`.

The current `sign_one()` only sets `field_name` + `subfilter`; it passes **no** `timestamper`, **no** `embed_validation_info`, **no** `use_pades_lta`, **no** `validation_context`. That is the root cause of the B-B ceiling and is what this work fixes.

---

# 2. Tooling mandate (do not skip)

- **context7 MCP is authoritative for APIs.** Before writing any signing code, use context7 to fetch the *current* docs for `pyHanko` (`pyhanko.sign.signers`, `pyhanko.sign.timestamps`, `PdfSignatureMetadata`) and `pyhanko-certvalidator` (`ValidationContext`, trust roots, fetching). Do **not** rely on training memory for method names or parameters — confirm them.
- **serena MCP** for code navigation and symbol-level edits across `sign_pdfs_beid.py`, `gui.py`, and the tests.
- For the EU Trusted List work (§4, R7), use context7 **and** web research to confirm the LOTL format (ETSI TS 119 612) and to evaluate existing parsing libraries **before** hand-rolling XML parsing.
- Follow the existing code conventions: English comments/CLI text, `from __future__ import annotations`, dataclasses, type hints, pure/testable helpers.

---

# 3. Decisions already made (treat as fixed)

1. **Timestamp authority**: default to a **free public RFC 3161 TSA**, fully overridable. Default URL: `http://timestamp.digicert.com`. Overridable via `--timestamp-url <url>` and the `SIGNAPP_TSA_URL` environment variable (flag wins over env, env wins over default).
   - ⚠️ Document clearly that a free TSA yields a *technically valid* B-T/B-LTA but **not a qualified timestamp**. For genuine qualified long-term preservation the operator must point `--timestamp-url` at a **qualified** TSA. State this in `README.md`.
2. **Trust anchors for LTV**: **fetch the EU Trusted List (LOTL) at runtime**, follow the pointer to the Belgian national trusted list, extract the X.509 certificates of trust service providers qualified for electronic signatures, and use them to seed the `ValidationContext` trust roots. Cache the result locally with a sensible TTL; fail with a clear, actionable error if the list cannot be fetched.
3. **CLI surface**: add `--pades-level {b-b,b-t,b-lt,b-lta}`, **default `b-lta`**.

---

# 4. Functional requirements

**R1 — `--pades-level` flag.** Add `--pades-level` with choices `b-b`, `b-t`, `b-lt`, `b-lta`, default `b-lta`. This is now the single knob controlling signature strength in `beid` mode.

**R2 — Deprecate `--pades`; preserve legacy CMS explicitly.** Keep `--pades` as a deprecated alias that emits a deprecation warning and is a no-op (PAdES is now the default). The legacy non-PAdES `adbe.pkcs7.detached` path must remain reachable **only** via an explicit, deprecated `--legacy-cms` flag (mutually exclusive with `--pades-level` ≠ `b-b`). Default behaviour without flags = `b-lta`.

**R3 — Trusted timestamping (B-T and above).** For levels `b-t`, `b-lt`, `b-lta`, attach a `pyhanko.sign.timestamps.HTTPTimeStamper` built from the resolved TSA URL (see §3.1) to the `PdfSigner`. No timestamp for `b-b`.

**R4 — Embedded revocation / LTV (B-LT and above).** For `b-lt` and `b-lta`, set `embed_validation_info=True` and pass a `ValidationContext(trust_roots=<EU-TL anchors>, allow_fetching=True)` so OCSP/CRL are gathered and embedded into the DSS at signing time.

**R5 — Archival timestamp chain (B-LTA).** For `b-lta`, additionally set `use_pades_lta=True`.

**R6 — Pinned digest.** Add `--digest` (default `sha256`, also allow `sha384`, `sha512`) and pass it explicitly as `md_algorithm` instead of relying on the library default.

**R7 — EU Trusted List trust provider (new module).** Create a dedicated module (e.g. `trust.py`) that:
   - fetches the EU List of Trusted Lists from `https://ec.europa.eu/tools/lotl/eu-lotl.xml` (make the URL overridable via `--trust-list-url` / `SIGNAPP_LOTL_URL`),
   - resolves the Belgian national trusted list, extracts the qualified-for-eSignature CA certificates,
   - returns them as a list of trust anchors for the `ValidationContext`,
   - caches to a local file under the OS cache dir (use `platformdirs`, already a dependency) with a configurable TTL (default 24h) and a `--refresh-trust-list` flag to force a refresh,
   - raises a clear error if the list is unreachable and no valid cache exists.
   This module must be import-safe without tkinter and unit-testable with the network mocked.

**R8 — Post-signing self-verification.** After writing each signed PDF (when level ≥ `b-t`), re-open it and validate it with pyHanko's validation API, **detect the achieved level / LTV status**, and assert it matches the requested `--pades-level`. Surface the detected level in `DocResult.detail` (e.g. `signed (eID) — PAdES-B-LTA, LTV ok`). Add `--no-verify` to skip. A verification mismatch must mark the document as failed, not silently pass.

**R9 — RRN privacy warning.** In `beid` mode, print an explicit one-line warning at startup that the **national register number (RRN) is embedded in every signed PDF** and to mind distribution; repeat a short note in the final summary. (The cert/identity reading already exists in `read_card_identity()`.)

**R10 — Network / offline behaviour.** Levels `b-t`/`b-lt`/`b-lta` require network (TSA, OCSP/CRL, LOTL). On unreachable endpoints, fail with an actionable message naming the endpoint — **never silently downgrade** to a weaker level. `b-b` and `image` mode must remain fully usable offline.

**R11 — Documentation truth.** Remove every occurrence of the false claim **“PAdES signature (long-term archiving)”** for the old `--pades` flag (it appears in `README.md`, in `build_arg_parser()` help text, and in `BUILD.md`). Replace with accurate descriptions of each level. Document the new flags, the network requirement, the free-vs-qualified TSA caveat, and the RRN exposure.

---

# 5. Plumbing changes required

- **`RunConfig`**: replace `pades: bool` with `pades_level: str = "b-lta"`; add `timestamp_url: str | None = None`, `trust_list_url: str | None = None`, `digest: str = "sha256"`, `verify: bool = True`, `legacy_cms: bool = False`, `refresh_trust_list: bool = False`.
- **`sign_one()`**: change the `use_pades: bool` parameter to carry the level + the resolved timestamper + validation context; map level → the correct `PdfSignatureMetadata` parameters per R3–R6. Keep the existing vignette/placement logic untouched.
- **`process_batch()`**: build the timestamper and the `ValidationContext` **once** before the loop (alongside the existing single PKCS#11 session), reuse across documents; thread the level through; run R8 verification per document.
- **`build_arg_parser()` / `resolve_config()`**: wire all new flags; keep the legacy positional form working; update validation in `validate_config()`.
- **`gui.py`**: replace the boolean **PAdES** checkbox with a **level selector** (dropdown / segmented control) defaulting to `b-lta`; thread `pades_level` (and sensible defaults for the rest) into `RunConfig`. Keep the worker-thread / queue / `after()` invariants intact (do not reintroduce Tk calls off the main thread).
- **`requirements.txt` + `signApp.spec`**: if any new runtime dependency is added (e.g. an LOTL parser), pin it in `requirements.txt` **and** add it to the appropriate `collect_all` / hiddenimports in `signApp.spec` so both frozen binaries still build. `lxml` is already bundled.

---

# 6. Testing (stdlib `unittest`, keep it lean)

Prioritise fast, hardware-free, deterministic tests; do **not** fake a real card signature into passing.

- Pure mapping `pades_level → PdfSignatureMetadata params` (factor this into a pure function and test all four levels).
- CLI/`resolve_config` parsing of the new flags, defaults (`b-lta`), `--legacy-cms` exclusivity, deprecated `--pades` warning, env-var precedence.
- `trust.py`: parsing + caching + TTL + refresh, **with the network mocked**; clear error when unreachable and no cache.
- Update every existing test that constructs `RunConfig(... pades=...)` or `_Args(pades=...)` to the new shape so the suite stays green.
- The existing CI smoke test (image mode, no card) and the `HeadlessImport` invariant must keep passing unchanged.

Real eID + TSA + LTV signing remains a **manual acceptance test on real hardware** (sign a PDF; verify in Adobe Reader and via `pyhanko sign validate` that it reports **LTV enabled** and the expected level). Document this in `BUILD.md`; do not gate CI on it.

---

# 7. Acceptance criteria (the loop drives to all-checked)

- [x] `--pades-level {b-b,b-t,b-lt,b-lta}` exists, defaults to `b-lta`.
- [x] `--pades` is a deprecated no-op alias with a warning; `--legacy-cms` reaches the old `adbe.pkcs7.detached` path and is mutually exclusive with PAdES levels > b-b.
- [x] `--timestamp-url` (+ `SIGNAPP_TSA_URL`, default DigiCert free TSA) wired; `HTTPTimeStamper` attached for levels ≥ b-t.
- [x] `embed_validation_info=True` + fetching `ValidationContext` for b-lt/b-lta; `use_pades_lta=True` for b-lta.
- [x] `--digest` pins `md_algorithm` (default sha256).
- [x] `trust.py` fetches the EU LOTL → Belgian list → qualified eSig CA certs, with cache/TTL/refresh and clear offline errors; seeds the `ValidationContext` trust roots.
- [x] Post-signing self-verification detects achieved level/LTV, reports it in `DocResult.detail`, fails on mismatch; `--no-verify` skips.
- [x] RRN privacy warning printed in `beid` mode + summary note.
- [x] No silent level downgrade on network failure; `b-b` and `image` work offline.
- [x] All “long-term archiving” false claims removed; README/CLAUDE.md/BUILD.md updated (flags, network requirement, free-vs-qualified TSA caveat, RRN).
- [x] GUI exposes a level selector defaulting to b-lta; thread-safety invariants preserved.
- [x] `requirements.txt` + `signApp.spec` updated for any new dependency; both binaries still build.
- [x] `python -m unittest -v` is green; `HeadlessImport` and the image-mode smoke test pass.

---

# 8. Out of scope (note as follow-ups, do not implement now)

- A standalone `ltaupdate`-style maintenance command to renew the archival timestamp chain over time (mention it in `BUILD.md` as the required ongoing maintenance for B-LTA).
- Remote / server-side / itsme (QTSP) signing flows.
- Any change to the template-validation or image-insertion logic.

---

# 9. Constraints recap

Speed-oriented but correct: ship working B-LTA. Confirm every pyHanko / pyhanko-certvalidator / LOTL API against context7 before coding. Commit per green step. Never fabricate passing tests for the hardware signing path. Keep `image` mode and headless/no-tkinter invariants intact.
