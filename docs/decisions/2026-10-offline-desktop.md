# Offline desktop redesign and distribution

Accepted scope, 2026-10-06:

- Keep the Python/PySide6 service boundary and replace the presentation with a
  dark-first sidebar, cards, drop zones and model-oriented settings.
- Support both Apple Silicon and Intel macOS. Platform detection must select
  a real local CPU backend on Intel instead of importing MLX there.
- Required models are part of the offline installation media. A network
  connection must not be needed to initialize or run installed local features.
- Ship explicitly ad-hoc signed distribution with the documented first-open
  macOS warning. Never describe it as Developer ID signed or notarized.
- Preserve original files, private model/voice data and previous installations.
- Run UI interaction, service, real-engine and clean-install verification
  autonomously. Do not manufacture human-quality scores or bypass old gates.
- Commit frequently; audit source and history before every public push.

Selected additions: oxipng, Argos Translate, local rembg/ONNX, constrained
ImageMagick and original FFmpeg media services. Model licenses are separate
from engine licenses. No third-party GPL/AGPL application code is copied into
Apache-2.0 KSI source.

The GitHub asset limit applies to each file, not to the complete release.
Measure the complete offline payload before choosing a transport layout;
never silently substitute first-run model downloads for offline installation.

Architecture-dependent capabilities and their quality evidence must be
explicit. Intel support cannot be inferred from successfully running an
Apple Silicon package through tests on an Apple Silicon host.
