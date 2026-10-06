# KSI Local Studio 2.0.0.dev0 release candidate notes

This development candidate provides a local-first macOS application for permitted, DRM-free video,
document, article, image and creative workflows. It includes an eight-language interface, resumable
jobs, external-workspace support, local CLI/MCP access, source-preserving exports and privacy-safe
history controls.

## Release status

This build is **not a public release**. Publication remains blocked until the complete human
acceptance matrix passes, a clean-Mac installation is accepted, reviewed cleanup is explicitly
approved, and the final package is Developer ID signed and Apple-notarized. The personal offline
DMG is ad-hoc signed and must not be represented as notarized.

The final personal DMG embeds CPython 3.12.13 for Apple Silicon. Both Python environments use
payload-relative launchers, the runtime portability audit reports zero findings, and an isolated
same-Mac installation into an empty temporary home passed. This does not replace acceptance on a
second physical clean Mac, Developer ID signing, or Apple notarization.

## Final audit corrections

- MCP rejects malformed JSON-RPC shapes and unexpected arguments without crashing.
- Article downloads pin TLS to a validated public IP and validate every redirect before connecting,
  closing redirect SSRF and DNS-rebinding paths.
- Workspace relocation rejects pre-existing staging symlinks; image, acceptance and public-source
  outputs reject dangling symlink targets.
- Download results exclude partial/checkpoint and unrelated files.
- Update versions order numeric prereleases correctly and update/release gates independently verify
  Developer ID and Apple notarization instead of trusting manifest claims.
- Installers hold `app.lock` throughout backup and atomic swap, so a running application cannot be
  replaced.
- Error persistence redacts user and external-volume paths. NumPy and Pillow are declared and
  included in notices/SBOM inputs.
- Apple Vision OCR is a required public build input and its Swift source is included in the clean
  source allow-list.
- The installer verifies all 83,678 payload files and links before an atomic swap, while the final
  regression suite passes 370/370 tests with no skips.

## Safety and privacy

- No subscription, cloud AI account or Codex/ChatGPT dependency is required.
- Network operations are explicit; model processing and user-file workflows remain local.
- Sources and completed outputs are never automatically deleted.
- Models, jobs, cookies, browser profiles, private catalogs, disk identity and acceptance evidence
  are excluded from the public source tree.
- KSI Local Studio does not bypass DRM, CAPTCHA, paywalls or access controls.

See `README.md`, `SECURITY.md`, `THIRD_PARTY_NOTICES.md`, `MODEL_LICENSES.md`,
`SOURCE-MANIFEST.json` and `SBOM.spdx.json` in the prepared public source tree.
