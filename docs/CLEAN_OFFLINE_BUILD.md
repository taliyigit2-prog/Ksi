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

Successful assembly records `acceptance_tested: false`. It is not native
functional/quality evidence, a successful clean installation, notarization,
or release approval. Each final architecture still needs automated acceptance
and exact-artifact privacy/license checks before publication.
