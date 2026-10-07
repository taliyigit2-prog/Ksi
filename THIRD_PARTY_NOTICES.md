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

The isolated ARM Turkish speech environment is locked separately in
`config/python-chatterbox-wheels-arm64.json` (Chatterbox 0.1.7 and its exact
public transitive artifacts). It is rebuilt from official wheels, not a user
environment. Piper's ARM/Intel locks are separate too. All these locks are input
inventories: missing license-expression fields still require actual upstream
license texts, and the final binary SPDX inventory must include their complete
contents. The primary GUI uses PySide6-Essentials plus Shiboken rather than the
unused PySide6-Addons distribution.

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
