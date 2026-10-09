# KSI Local Studio 2.0.0 — Apple Silicon

The accepted offline package targets macOS 14+ on Apple Silicon with 16 GB
memory. Intel release work is retired. Models are included on the segmented
DMG; runtime models, jobs and caches use the internal system disk only.

All 14 strict automatic distribution cases, actual packaged engine/model
checks and independent offline DMG installation passed. The final code-cleanup
suite passed 693 tests with zero skips. Git source/history, retained build
artifacts and actual application/media privacy checks have no unresolved
findings within their recorded scopes. No user tests or human scores are claimed.

The application is ad-hoc signed, not Apple-notarized. First launch may require
explicit approval in macOS Privacy & Security. Historical human/notarized and
two-architecture APIs remain unchanged and do not define this ARM-only release.

The distribution includes original notices and corresponding sources. The
distribution SPDX inventory conservatively includes source dependencies; not
every listed source package is claimed to be compiled into the binary. Where
upstream provides an SPDX license declaration rather than a separate root
license file, original declarations, standard terms, existing attribution and
complete source are preserved. No copyright attribution is fabricated. This
technical delivery review is not a legal warranty.

[Version 2.0.0 is published](https://github.com/taliyigit2-prog/Ksi/releases/tag/v2.0.0).
The [installer ZIP](https://github.com/taliyigit2-prog/Ksi/releases/download/v2.0.0/KSI-Local-Studio-2.0.0-Install.zip)
downloads 52 smaller native DMG parts with individual size/SHA-256 verification.
All 60 installation, source and verification assets matched their complete
server-side digests before publication. The unchanged accepted application also
passed a fresh full privacy audit, strict embedded-media verification and real
installer verification after native DMG resegmentation.
Post-publication checks also passed: eight complete installer/source/metadata
downloads, a matching 1 MiB range from every one of the 52 media parts, all 398
files in the Git source archive matched to the accepted commit, and the actual
published helper downloaded one missing 228 MB part, verified the full media
and opened the DMG. The other 51 accepted parts were seeded locally; this is
not a claim of a fresh 13.9 GB public download. No account token or overridden
home directory was used, and no existing user files were overwritten.
The accepted product source is
`874c8e6585037ca2fbf6bddb11edd55187fa9dd7`; later release-tool and documentation
commits do not change the sealed application.

## Historical 2.0.0.dev0 candidate notes

The following describes the earlier baseline, not current installation support
or the approved ARM-only release criteria.

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
