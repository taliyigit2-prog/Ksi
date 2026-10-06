# Clean offline desktop build

The architecture-specific builds use pinned public CPython archives and PyPI
wheels. This pipeline never copies an existing personal virtual environment,
Application Support directory, workspace identity, jobs or user media.

The public `config/python-wheels-arm64.json` and
`config/python-wheels-x86_64.json` locks contain exact artifact URLs, sizes and
SHA-256 digests cross-checked with official PyPI metadata. They exclude pip's
local report/environment details. Wheel tags must support CPython 3.12 and
macOS 14 on the stated architecture; universal2/abi3 wheels are allowed.

Build steps, using the project development interpreter with `PYTHONPATH=src`:

1. Fetch the corresponding `python-arm64` or `python-x86_64` entry with
   `scripts/fetch_build_input.py`.
2. Populate an exact private wheelhouse using `scripts/lock_python_wheels.py
   fetch`; missing, extra or corrupt files are rejected.
3. Run `scripts/assemble_clean_runtime.py` on the **native target processor**.
   Pip installs exclusively from that wheelhouse with hashes, without a network
   index, source builds, dependency re-resolution, user configuration or cache.
4. Stage native engines, required model files, license texts and corresponding
   sources against an explicit component specification. A source revision and
   redistribution evidence are required for each engine/model.
5. With a clean committed source tree, run `scripts/assemble_offline_app.py`.
   Internal runtime symlinks are materialized; external/cyclic links fail.
   Bytecode caches are excluded. Native files are ad-hoc signed before their
   runtime integrity hashes are recorded. The outer app is then signed without
   re-signing the hashed inner components.
6. Use the DMG transport builder only for that sealed application. Verify the
   exact final media by mounting, copying and launching it in clean test state.

The Argos integration intentionally installs its pure package/tokenizer wheel
and the explicitly locked SentencePiece/BPE/CTranslate2 dependencies. It does
not import upstream SBD providers, Stanza or spaCy. The native Intel runtime
does not require MLX or Torch for this path. This is a constrained direct-pair
adapter, not a claim to support every upstream Argos execution mode.

The ARM lock includes MLX Whisper's own dependencies. Chatterbox speech remains
an isolated runtime and must be separately locked and staged. Intel speech uses
the separately licensed Piper engine. No engine or model is implicitly fetched
by the installed application.

Build-only CMake/Ninja/Meson wheels have separate locks; they are not added to
the application runtime. `scripts/prepare_native_component.py` fetches the
whisper.cpp release tag only when it resolves to the pinned commit, records and
rechecks every source file, and produces a static CPU CLI without Homebrew or
OpenMP dependencies. Compiler prefix maps exclude local source paths; Git
discovery cannot accidentally label it with the enclosing KSI repository commit.
Official oxipng macOS archives are independently digest-checked and extracted
with their license into clean staging, rejecting links and path escapes.

The manual `Native offline build inputs` GitHub workflow uses native Apple
Silicon and Intel runners. It bootstraps from the same pinned inputs, builds
both dependency prefixes and the CPU CLI, and retains only a narrow engine
artifact with its pinned public source and upstream license headers. It does not run the final product
acceptance suite or publish a release. Native runtime/engine assembly is not
evidence that the final DMG, GUI or model processing works.

The selected native codec/delegate libraries have separate public artifact locks
in `config/native-libraries-*.json`. A build-only pinned micromamba resolver
operates with a temporary home and no user configuration. Offline prefix
installation uses already SHA-256-verified local archives, without re-solving
or contacting a package index. The broad FFmpeg/ImageMagick binary packages
were not selected: their unrelated Ghostscript/GUI/OpenVINO dependencies are
outside this build's raster/media scope. FFmpeg and ImageMagick are built from
their own pinned commits against the selected libraries instead.

These locks deliberately state `redistribution_review_complete: false`.
Declared package license labels do not replace upstream notices, matching
recipe patches or corresponding sources. The final engine/library package
must retain those materials and pass the binary license/privacy gate. The
build-only resolver, package metadata and development prefix are not copied
wholesale into the shipped app.

`scripts/stage_native_engines.py` copies only the selected engine dependency
graph. Recorded vendor build-prefix references are rebound to explicit clean
prefix members, never read from Homebrew or a user installation. Internal
library aliases are materialized into independent files. Load paths become
app-local `@loader_path` references, stale search paths are removed, and the
relocated binaries are ad-hoc signed before final hashes are recorded. Missing
libraries, escaping links and name collisions close this build boundary.
Every final load command is resolved inside the staged graph and every member's
ad-hoc signature is verified. Native library prefixes should be created in a
neutral build directory: prefix replacement may also affect embedded default
configuration strings, not just load commands.

`scripts/collect_native_notices.py` independently verifies the locked archives
and extracts only bounded license/recipe metadata. It preserves source archive
digests and recipe patches for the subsequent corresponding-source review.
Missing notice texts are explicitly reported; collection does not approve
redistribution. The selected fonts and portable fontconfig configuration must
also be staged explicitly rather than relying on a developer's font directory.

The app assembler binds download-tool binary digests to their actual
post-signing files while retaining the pinned versions and original archive
digests. The launcher clears inherited manifest overrides, and packaged OCR
uses a verified component instead of searching a development directory.

Successful assembly records `acceptance_tested: false`. It is not native
functional/quality evidence, a successful clean installation, notarization,
or release approval. Each final architecture still needs automated acceptance
and exact-artifact privacy/license checks before publication.

Piper has its own `python-piper-wheels-*.json` locks, resolved from the official
PyPI metadata and never installed into the primary app interpreter. Assemble
the isolated prefix with `assemble_clean_runtime.py --lock`, then declare its
real interpreter as the verified `piper-python` component. The backend runs
`-I -m piper` in a separate process; there is no installed-app resolver or model
download. The matching GPL source revision is pinned in `native-sources.json`.
The exact source of embedded eSpeak and other wheel license notices remains a
required corresponding-source review item, not a completed gate. See the
[upstream CLI](https://github.com/OHF-Voice/piper1-gpl/blob/v1.8.0/docs/CLI.md).

`stage_portable_font.py` copies only the digest-checked DejaVu font member and
its collected notice. Declare `fonts.conf` and the font under the same sealed
directory with support identifiers `fontconfig-config` and `subtitle-font`.
FFmpeg/Magick worker environments use those verified paths and do not inherit
a developer's fontconfig search-root override.

Some official model archives do not publish a SHA-256 digest. The build-only
`review_model_input.py` is restricted to three explicit upstream artifacts and
performs a bounded first retrieval into a private review directory. U2NetP is
also checked against upstream rembg's legacy MD5. The resulting public model
lock states which SHA-256 values were observed, not independently advertised;
Argos index IPFS CIDs are not claimed to have been verified. Subsequent fetches
use only the fixed SHA-256/size lock. This never approves model redistribution.

Argos archives lack standalone license files. The project's
[model license declaration](https://github.com/argosopentech/argos-translate/issues/76#issuecomment-815704991)
states its model-training work is covered by MIT/CC0. Preserve the original
model README and corpus citations together with the chosen MIT notice. Do not
label this as a new license grant for training corpora or a legal determination
about training-data rights; corpus data is not included in this package.

The settings storage card exposes an explicit workspace-location choice even
when a previously selected SSD is unavailable. It never moves/deletes previous
files: it creates a new named KSI folder or adopts a valid existing marker.
Unrelated populated folders and missing `/Volumes` paths are rejected. External
locations retain actual disk UUID identity. Corrupt selection files fail closed
instead of silently creating an internal fallback.

Workspace selection holds a cross-client exclusive transaction lock. CLI work,
shared tool execution, model operations and workspace resolution hold readers;
owned engine children inherit the reader descriptor so an orphan cannot release
the protection early. Active processing blocks explicit location changes. The
physical-copy relocation helper still preserves its source; settings do not
expose it as a completed job-database/path migration feature.

The primary GUI lock now includes only PySide6-Essentials and Shiboken, matching
the app's Core/Gui/Widgets/PrintSupport imports. Unused Addons/demo/web modules
are not copied into the app. This reduces payload scope without adding a new
UI framework or losing the existing PDF printer path. Qt license notices and
matching LGPL source remain required; smaller scope is not a license exemption.

Ollama packages are pinned as complete manifests, config and all layers, not
one guessed GGUF. The current Qwen3.5:4b manifest differs from the historical
baseline and requires its projector in addition to its new model layer.
Acceptance must be rerun; the change is not inherited quality evidence.
Native model/readiness inventory comes from the sealed architecture catalog.
Intel preflight recognizes the GGML file/Piper layout, and signed-package UI
checks use the current processor, retaining the old ARM/manual gate separately.

Model HTTP requests explicitly bypass inherited proxies and refuse redirects;
responses are bounded. Turkish Chatterbox workers enforce the local-only socket
policy and skip only the unused upstream Chinese-converter initialization.
Turkish tokenization/weights are unchanged. Demo/training/other-language extras
are outside the selected isolated inference scope. Public weights come from
the actual ResembleAI/chatterbox repository, not the logical family label.
No user reference voice, old environment or personal cache is bundled.

All isolated runtime scripts have their build-prefix shebangs normalized before
signing and hashing. Piper's isolated interpreter receives explicit `-B`, since
`-I` ignores the bytecode environment flag; first use must not mutate a signed
runtime. Large DMGs are created as native segments directly to avoid retaining
both a full compressed image and duplicate segments. Actual mount/copy/launch
verification of this transport remains mandatory.

Packaged Chatterbox weights are checked before any PyTorch deserialization,
including checkpoint-only resumes. All five required upstream files must match
the sealed payload; checkpoint identity includes their combined hashes so a
model revision cannot reuse audio from another revision. The isolated ARM speech
runtime has its own official PyPI lock, independent of the GUI's NumPy/Torch.
The Intel Whisper GGML input is pinned to the official LFS SHA-256 and revision;
neither this pin nor successful input downloads constitute inference acceptance.

Idle Core clients revalidate the saved selection, marker and external volume
inside their reader transaction before resuming work. A workspace changed by
another client cannot silently keep receiving writes through an old Core object.
Corresponding-source downloads preserve recipe SHA-256 pins even when upstream
has no trustworthy Content-Length, using a hard download bound instead. LGPL
components and combined license expressions require source records too; source
collection alone does not approve a binary release.

On-demand Ollama has an owned supervisor holding the active reader/model/job
descriptors. The parent keeps a liveness pipe open; normal close or parent death
triggers termination of only that supervisor's server process group, including
runners. The sealed app refuses an already-ready foreign server instead of
silently sending text to a different model store. Development-only daemon reuse
remains available outside a sealed bundle.

Upstream PyPI Chatterbox 0.1.7 does not expose the pinned Git commit's V3 API.
The source-bound runtime builder preserves its fresh wheel prefix, creates a
separate prefix using the exact verified Git package files, and records the
override plus a regenerated wheel RECORD. It does not borrow a patched personal
environment. That commit uses `grapheme_mtl_merged_expanded_v1.json`; the other
upstream `mtl_tokenizer.json` is not interchangeable. Both model API and actual
speech output require final acceptance on the source-bound prefix.

Argos staging keeps the five direct inference files and upstream README citations;
unused Stanza tokenizer weights are excluded. Archive paths, links, duplicates,
encryption, expansion limits and language direction are checked before extraction.
The download/runtime tools are separately pinned to official release digests.
The final signed tool manifest binds the actual architecture's Deno archive and
Python version and drops historical certificate/GPG claims that have not been
reestablished for that package. Native OCR targets macOS 14 or newer explicitly.

The pinned Deno release archives match their official hashes, but upstream macOS
signature verification failed on both extracted binaries in this build. Original
archives remain intact; an explicit `--adhoc-sign` build step creates a separate
strictly verified ad-hoc copy and records the failed upstream signature status.
This is not an Apple notarization or valid-upstream-certificate claim. Functional
download-tool acceptance and final package audits are still required.

Settings can display each model's complete verified offline notices in an inert
read-only text dialog. Catalog notices are bound to sealed license identifiers;
no arbitrary path or network page is opened. Empty licenses are rejected, and
Gemma families require the original terms layer, current official terms snapshot,
prohibited-use snapshot, required Notice and KSI model-specific terms. These are
not personal acceptance records or a legal-review certification.
