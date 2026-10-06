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

Models have separate licenses. rembg's commercial BRIA model and cloud
backend are not selected by KSI. Native FFmpeg binaries have build-dependent
LGPL/GPL obligations; nonfree builds must not be distributed.
