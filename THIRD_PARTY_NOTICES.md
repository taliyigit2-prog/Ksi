# Third-party notices

This inventory distinguishes code bundled with a release from optional tools
downloaded and executed separately. The final public release must regenerate
this file and its SPDX SBOM from the exact package contents.

| Component | Role | Integration | Upstream |
|---|---|---|---|
| yt-dlp | Media acquisition | Verified external executable | https://github.com/yt-dlp/yt-dlp |
| FFmpeg / ffprobe | Media processing | Verified external executables | https://github.com/FFmpeg/FFmpeg |
| Deno | JavaScript runtime for extractors | Verified external executable | https://github.com/denoland/deno |
| Ollama | Local model runtime | External service managed on demand | https://github.com/ollama/ollama |
| MLX Whisper | Speech recognition | Optional Python environment | https://github.com/ml-explore/mlx-examples |
| PySide6 | Native Qt user interface | Optional Python dependency | https://doc.qt.io/qtforpython-6/ |
| Lingua | Offline text-language detection | Python dependency | https://github.com/pemistahl/lingua-py |
| pypdf | PDF parsing | Python dependency | https://github.com/py-pdf/pypdf |
| python-docx | DOCX processing | Python dependency | https://github.com/python-openxml/python-docx |
| NumPy | Image array processing | Python dependency | https://github.com/numpy/numpy |
| Pillow | Image decoding and processing | Python dependency | https://github.com/python-pillow/Pillow |
| Chatterbox | Multilingual speech synthesis | Optional isolated environment | https://github.com/resemble-ai/chatterbox |

This table is informational and does not replace the license text supplied by
each component. Versions, hashes, notices and transitive dependencies are
validated during release preparation.

Architecture-specific Python wheel versions and official artifact checksums are
recorded in `config/python-wheels-arm64.json` and
`config/python-wheels-x86_64.json`. `NOASSERTION` in a lock means the package's
PyPI license-expression field was absent; it does not grant redistribution
permission. Actual wheel license texts, native transitive library notices and
the final binary SBOM remain mandatory before release. See
[clean build documentation](docs/CLEAN_OFFLINE_BUILD.md).

Standalone CPython 3.12.15 includes native and statically incorporated libraries
whose original grants are not all present in the install-only download. The
exact original full distributions and their 21 selected original legal/metadata
members per architecture are pinned in `config/python-runtime-notices.json`.
Clean builds preserve those bytes, including CPython, Tcl, OpenSSL, libffi,
SQLite, ncurses and other original notices. The original metadata's absent
alternative zlib-ng reference remains recorded, not replaced by an invented
grant. Its affected Darwin extensions declare the system z library. Actual
signed-binary/source binding and the final distribution SBOM remain separate.

The build-only full Python archive reader can reuse the exact reviewed native
Zstandard 1.5.7 library from the media component inventory. It adds no new
downloaded decoder or installed-app dependency and retains the library's
existing original notices. Expansion is streamed and bounded.

The isolated ARM Turkish speech environment is locked separately in
`config/python-chatterbox-wheels-arm64.json` (Chatterbox 0.1.7 and its exact
public transitive artifacts). It is rebuilt from official wheels, not a user
environment. Piper's ARM/Intel locks are separate too. All these locks are input
inventories: missing license-expression fields still require actual upstream
license texts, and the final binary SPDX inventory must include their complete
contents. The primary GUI uses PySide6-Essentials plus Shiboken rather than the
unused PySide6-Addons distribution.

The selected upstream Chatterbox V3 Git source additionally requires OmegaConf
2.3.0. Its official wheel is locked with the speech environment. OmegaConf's
ANTLR Python 4.9.3 dependency has no official wheel: the build preserves its
exact official PyPI source, original package metadata and the original BSD
notice from the matching ANTLR Git commit. The explicit source overlay runs no
setup script and is not described as an official wheel. Both the Chatterbox Git
override and ANTLR source override are hash-bound and rechecked before staging.
This does not certify the final binary dependency inventory or model quality.

Only the isolated speech lock retains setuptools 80.9.0 for the original Perth
watermark package's `pkg_resources` API. The main GUI/build-tool environments
are unaffected. Perth's watermark is not replaced by a dummy implementation
or silently disabled to work around a failed import.

Matching Qt/PySide 6.11.2 sources have a separate official-checksum inventory in
`config/qt-corresponding-sources.json`. The selected wheel also contains QML,
SVG, tool, image-format and timeline runtime modules even though KSI's own GUI
uses Widgets; their source and license obligations are not ignored. Source
collection does not certify license completeness or final binary compliance.

The distribution-notice builder resolves missing wheel texts only against the
matching source versions in `config/python-notice-sources.json` and the Qt
inventory. Main and isolated speech package inventories are independently bound
to their exact wheel hashes. App assembly rejects omitted or changed notices
and corresponding-source members. These inventories explicitly do not promote
text completeness into redistribution approval or product acceptance.

Original Ollama 0.24.0, Deno 2.9.6 and yt-dlp 2026.08.19 source commits,
archive digests and selected original notices are recorded in
`config/native-sources.json` and `config/tool-source-notices.json`. The latter
also records unresolved binary dependency closure; root project licenses do
not replace transitive notices. Architecture-specific Ollama Go dependency
inventories are derived from the actual native executable build information
and checked against the original commit-bound `go.sum`. Collection is not a
final binary-license approval. See the
[source notice decision](docs/decisions/2026-10-07-original-tool-source-notices.md).
# Desktop engine additions (implementation candidates)

The following adapters do not copy complete third-party applications. Exact
locked binary versions, transitive libraries and model-weight permissions must
be included in the final distribution SBOM before shipping:

- oxipng: MIT; https://github.com/oxipng/oxipng
- Argos Translate: MIT; https://github.com/argosopentech/argos-translate
- rembg: MIT; https://github.com/danielgatis/rembg
- ONNX Runtime: MIT; https://github.com/microsoft/onnxruntime
- ImageMagick: ImageMagick License; https://github.com/ImageMagick/ImageMagick
- whisper.cpp: MIT; https://github.com/ggml-org/whisper.cpp
- Piper: GPL-3.0; https://github.com/OHF-Voice/piper1-gpl

Piper is invoked as an independent executable for CPU speech; its implementation
is not copied into Apache-2.0 KSI modules. Any distribution of the engine must
include its license, corresponding source and notices for compiled dependencies
(including eSpeak). The build sealer rejects a copyleft engine without an explicit
corresponding-source artifact. This is not by itself a complete license audit.

Models have separate licenses. rembg's commercial BRIA model and cloud
backend are not selected by KSI. Native FFmpeg binaries have build-dependent
LGPL/GPL obligations; nonfree builds must not be distributed.
