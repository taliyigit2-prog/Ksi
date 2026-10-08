# KSI Local Studio architecture

KSI Local Studio is a local-first modular monolith.

## Accepted desktop revision (in implementation)

The 2026-10 revision targets Apple Silicon and Intel macOS with required models
on offline installation media. It retains PySide6 and the service boundary.
`ui` contains native Halite-referenced design tokens, sidebar, cards, tool forms
and a threaded signal bridge. `media_tools`, `image_engines`, `local_ai_worker`
and `cpu_transcription` contain original local engine adapters. Heavy ONNX and
Argos imports only occur inside the dedicated local-only worker.

`bundle_runtime` validates architecture, paths, sizes and SHA-256 hashes without
network access. Packaged tools never fall back to Homebrew. First-run internal
workspace selection is persisted before model copying, so an interrupted copy
can resume. Active workspaces are internal-only. Previously selected external
configuration is archived privately; it cannot block a new internal setup.
In-process model install caching is invalidated by manifest/file size/mtime
changes; hashes are verified on initial use and again when an AI worker loads
the selected model.

SQLite schema 6 adds `media` and `image` job kinds. Tool requests stay in private
job directories; persisted output paths cannot redirect publication outside the
job's outputs. Existing review/dubbing actions are unavailable for tool jobs.
Native processes have deadlines, cancellation and bounded/redacted diagnostics.
Cancellation and absolute deadlines remain active even if a still-running motor
closes its output streams; output EOF is never treated as process completion.
Both default and explicitly injected job-database paths are validated as absolute
internal-disk locations before directory creation or SQLite initialization.

The strict earlier human/notarized release gates below remain historical APIs.
The user-approved new distribution permits clearly labeled ad-hoc signing and
requires autonomous acceptance instead of invented human scores. New binary
release evidence is not yet available; do not interpret source checkpoints as
successful cross-architecture, model-quality or installation verification.

The offline producer rejects stale OCR MIT metadata when binding the project's
Apache-2.0 grant, and rejects source-only Unlicense metadata for the GPLv3+
PyInstaller downloader. Its original aggregate notice and corresponding source
must be retained; label consistency alone does not approve redistribution.

Source-notice collection excludes empty files, which remain in the original
source archive. Legacy build-input placeholders may be reclassified as support
only when their empty bytes match an exact digest-bound original archive member,
nonempty original sibling grants remain intact, and no component or model uses
that placeholder as its required license. The sealer still rejects empty grants.

Synthetic sealing fixtures use the actual test processor rather than assuming
ARM. MLX unit doubles explicitly select the MLX branch; separate CPU adapter
tests validate native command construction, result parsing and model integrity.
The native Intel source suite supplies the verified OCR development helper and
the CPU speech queue fixture. These source tests are not real-model inference.

`autonomous_release` is the approved ad-hoc boundary. Every required automatic
case is bound to the source commit, processor and exact offline manifest; named
objective checks and zero unresolved privacy/license findings are mandatory.
`distribution_integrity` mounts media read-only and compares a complete bundle
inventory including the launcher and outer code seal with the accepted app.
Matching checksums on self-described DMG files are not sufficient. Both native
architectures must use the same final version; development versions remain gated.

The outer app code seal and strict deep verification each have a bounded
15-minute budget because the seal covers gigabytes of offline model weights.
Individual native files retain their shorter signing budget. Timeouts and
verification errors remain build failures; no unsigned fallback is allowed.

Clean builds restore standalone Python's original legal texts from its exact
public full distribution, separately pinned to the install-only runtime input.
macOS libarchive reads the archive without a Homebrew dependency. Every selected
member has its own size/digest pin. The original Darwin metadata's missing
alternative zlib-ng reference remains visible: its four affected extensions
declare the OS-provided z library, not a shipped static zlib-ng. This notice
restoration is not actual signed-binary attestation or complete legal approval.

The old personal environment-copy installers are retired. The portable bundle
launcher resolves its Python, tools and models relative to the application, checks
the processor, sanitizes interpreter/plugin injection variables and does not use
the predecessor's runtime. Source assembly never replaces an installed app.

```text
GUI ─┐
CLI ─┼─> KSI Core ─> jobs, storage, security and resource governor
MCP ─┘               ├─ download providers
                     ├─ transcription / translation / speech
                     ├─ document and web processing
                     └─ image, audio and video engines
```

The current package contains the existing video and document pipeline. New
providers and engines must depend on core interfaces rather than importing GUI
objects. The MCP server will be a thin local adapter, not the application core.

The GUI presentation boundary is `ksi_local.i18n`: English is the complete
reference catalog, every supported locale must have the same keys and formatter
placeholders, and locale modules are static package data. Selecting an interface
language never changes the source/content language and never starts a network
translation service. Private UI preferences store only bounded settings such as
the locale and last export directory.

`ksi_local.job_store` and `ksi_local.privacy` form the persistence boundary for
job history. Source references and log messages are sanitized before storage;
GUI search, clipboard and open actions consume the sanitized representation and
revalidate output paths against the active workspace.

`ksi_local.workspace_management` keeps application placement separate from the
jobs/models/cache workspace selection. Machine identity remains outside source
control. Workspace relocation is staged, SHA-256 verified, atomically promoted,
and non-destructive to the source. The 2026-10-07 approved revision removes
external runtime storage. Legacy data remains untouched; historical paths must
be migrated with a database backup rather than simply renaming folders.

`ksi_local.providers` is the GUI-independent source boundary. It exposes common
inspect, authorize, download, progress, stop and resume capabilities while the
pinned `yt-dlp` implementation remains in `ksi_local.downloader`. The public
registry is deliberately limited to the platforms covered by automated policy
tests; DRM bypass is not a provider capability.

`ksi_local.translation_targets` separates the upstream 55-locale candidate
matrix from KSI's smaller quality-verified target set. `ksi_local.translation`
accepts explicit source and target codes, while `ksi_local.multilingual_document`
publishes language-coded document packages without removing Turkish legacy names.

`ksi_local.manual_acceptance` owns the private, resumable Phase 38 human-quality ledger.
It stores no source URL, account session, file contents or user path, and requires every
applicable human score to be at least 4/5 before a blocking case can pass. The upstream
55-locale model matrix remains non-blocking research and cannot promote a product language.

`ksi_local.reliability_audit` is a model-free Phase 39 gate for cross-module policy decisions
and safe temporary-directory fault probes. It never operates on user jobs or completed output.

The desktop shell separates New Job, Processing, History, System and Help into keyboard-accessible
tabs. Startup workspace resolution is shown in an in-window loading page; health information is
rendered in the persistent System tab instead of an unsolicited delayed dialog. Interface language
and System/Light/Dark theme are independent persisted preferences. Explicit light and dark palettes
use high-contrast text and selection colors, while System follows the macOS palette.

`ksi_local.article_reader` is the network/content boundary for blog and news
pages. It validates DNS targets and redirects, bounds HTML, treats extracted text
as untrusted data, enforces optional robots policy, rejects access-restricted
pages, and keeps original content separate from translated Markdown/PDF outputs.

`ksi_local.collection_jobs` converts one uncapped YouTube channel/playlist probe
into a disk-budgeted, selectable queue. Its atomic checkpoint admits at most one
running item, resumes interrupted items, preserves completed item IDs for update
deduplication, and isolates access failures. Active live capture is excluded unless
the caller supplies explicit consent and a bounded stop duration.

`ksi_local.catalog` loads a small offline public index whose schema requires official
provenance and verified redistribution terms. Personal bookmarks are a separate
mode-0600 `Application Support/private-catalog.local.json`; they are never promoted
to the bundled catalog, automatically downloaded, or represented as legally approved.

`ksi_local.image_tools` is the dependency-light raster boundary. It inspects image
dimensions before decoding work, enforces a 100-megapixel/1.5-GiB working-set ceiling,
performs one image at a time, strips metadata by default and atomically publishes only
new PNG/WebP/JPEG files. The GUI drop field opens the same core resize/background-removal
functions; CLI and GUI do not maintain separate implementations.

`ksi_local.image_editing` separates general edits from explicit e-commerce profiles.
Every full-resolution revision is gated by a hash-bound small preview and records its
seed/model. Protection masks composite original product/logo/text pixels back over the
edit. Undo only moves a manifest pointer; it never deletes an earlier revision. The
initial pilot deliberately permits only the zero-model classical local backend.

`ksi_local.creative_lab` is a quarantine boundary for optional creative pilots. Every
subfeature is independently allow-listed and defaults off. The baseline performs only
bounded classical image/audio/video-plan/SVG work; candidate generative models remain
disabled until license, unified-memory, runtime and quality evidence are all measured.
Character identity files are fictional-only and SVG input is parsed with active and
external content rejection.

`ksi_local.core_service` is the client-neutral application service used by GUI, JSON
CLI and `ksi_local.mcp_server`. The MCP process is a thin newline-delimited JSON-RPC
stdio adapter: it owns no alternate state machine or exporter. Local paths must remain
under explicit roots, network access defaults off, state-changing tools carry explicit
confirmation, and all returned errors pass through the shared privacy redactor.

Canonical identifiers are defined in `ksi_local.project_metadata`. The private
predecessor can be referenced only by `ksi_local.migration` and the migration
guide.

`ksi_local.release_prep` never publishes the personal working tree. It copies an
explicit source allow-list into a new directory, sanitizes machine-bound configuration,
generates a source SPDX SBOM and SHA-256 manifest, then runs privacy, secret, symlink and
binary gates before atomically exposing the tree. It does not initialize Git or contact
GitHub.
The source SBOM takes its product version from the exact staged, bounded ordinary
`pyproject.toml`, not a hard-coded development version or the builder's imports.
Missing, linked, oversized, malformed or foreign project metadata closes that gate.

`ksi_local.update_manager` validates a local release manifest without installing it.
The offline installer verifies every payload file before mutation and takes a verified
rollback snapshot of application source, configuration and SQLite state. Models and user
media remain outside the package. Public packages require Developer ID signing and Apple
notarization; those properties are checked against `codesign`, the stapled ticket and
Gatekeeper rather than trusted from manifest booleans. Personal ad-hoc builds are an explicit,
separately labelled path.

`ksi_local.runtime_portability` is a fail-closed package boundary. An installer may be labelled
offline-complete only when both Python runtimes have no machine-external symlink, Homebrew/local
library dependency or machine-bound `pyvenv.cfg`. The payload manifest covers every ordinary file
and symbolic-link target and rejects unlisted payload members. A development runtime that depends
on Homebrew remains usable on its source Mac but is not a clean-Mac distribution artifact.
## Final release gate

`final_release` is the historical non-destructive Phase 40 boundary. Its revised
cleanup preview inventories only regenerable development caches and obsolete distribution artifacts;
it never deletes or moves them. Its protection list explicitly keeps project source, tests,
documentation, local environments, workspace identity, user jobs, completed outputs, private
catalogs, acceptance evidence and accepted models.

The release gate combines human acceptance, clean-install evidence, reviewed cleanup, public-tree
and full-history audit results, package checksum, a deterministic current-source fingerprint,
Developer ID signing and Apple notarization. Missing evidence closes the gate. The command does not
publish, notarize, install or clean; external release actions remain explicit.
