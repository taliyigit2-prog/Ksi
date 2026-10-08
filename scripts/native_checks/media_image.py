"""Actual-package media/image/OCR diagnostics, without engine mocks."""
import json
import sys
import threading
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from PIL.PngImagePlugin import PngInfo

from ksi_local.atomic_files import atomic_write_json, atomic_write_text
from ksi_local.bundle_runtime import bundle_root, digest_file, host_architecture, tool_path
from ksi_local.engine_runner import OperationCancelled, run_engine
from ksi_local.image_engines import convert_advanced_image, optimize_png, pixel_digest
from ksi_local.media_tools import MediaRequest, process_media
from ksi_local.native_processor import is_rosetta_translated

target = Path(sys.argv[1]).resolve()
if target.exists() or target.is_symlink():
    raise FileExistsError("Native diagnostic target must be a new isolated directory")
target.mkdir(mode=0o700)
resources = bundle_root()
if resources is None:
    raise RuntimeError("Actual packaged interpreter required")
import ksi_local
if not Path(ksi_local.__file__).resolve().is_relative_to(resources / "runtime/src"):
    raise RuntimeError("Diagnostic must import exact packaged application source")
source = target / "reference.mp4"
run_engine([str(tool_path("ffmpeg")), "-hide_banner", "-nostdin", "-loglevel", "error",
    "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24:duration=3",
    "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264",
    "-crf", "8", "-g", "24", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)], timeout=60)
source_hash = digest_file(source)
checks = {}
converted = process_media(MediaRequest((str(source),), str(target / "converted.webm")))
checks["media-convert"] = converted.duration_seconds > 2.5
compressed = process_media(MediaRequest((str(source),), str(target / "compressed.mp4"), profile="small"))
checks["media-compress"] = compressed.output_bytes < compressed.source_bytes
cut = process_media(MediaRequest((str(source),), str(target / "cut.mp4"), operation="trim", start=1, end=2, lossless=True))
checks["media-lossless-cut"] = cut.stream_copy and 0.5 < cut.duration_seconds < 1.6
subtitle = target / "reference.srt"
atomic_write_text(subtitle, "1\n00:00:00,000 --> 00:00:02,000\nLOCAL STUDIO TEST\n")
burned = process_media(MediaRequest((str(source),), str(target / "subtitled.mp4"), operation="burn_subtitle", subtitle=str(subtitle)))
checks["media-subtitle"] = burned.duration_seconds > 2.5
cancel = threading.Event()
cancel.set()
try:
    process_media(MediaRequest((str(source),), str(target / "cancelled.mp4")), cancel=cancel)
except OperationCancelled:
    checks["media-cancel-no-output"] = not (target / "cancelled.mp4").exists()
else:
    checks["media-cancel-no-output"] = False
image = Image.new("RGB", (1000, 300), "white")
font = ImageFont.truetype(str(resources / "engines/fonts/DejaVuSans.ttf"), 64)
ImageDraw.Draw(image).text((35, 90), "LOCAL STUDIO 2026", font=font, fill="black")
original = target / "reference.png"
metadata = PngInfo()
metadata.add_text("private-marker", "Synthetic metadata-removal reference")
image.save(original, pnginfo=metadata)
optimized = target / "optimized.png"
optimize_png(original, optimized, strip_metadata=True)
with Image.open(optimized) as result:
    checks["image-lossless-private-metadata"] = pixel_digest(original) == pixel_digest(optimized) and "private-marker" not in result.info
webp = target / "converted.webp"
convert_advanced_image(original, webp, width=500, strip_metadata=True)
with Image.open(webp) as result:
    checks["image-native-convert"] = result.width == 500 and "private-marker" not in result.info
pdf = target / "reference.pdf"
image.save(pdf, "PDF", resolution=144)
ocr = run_engine([str(tool_path("ocr-helper")), "ocr", "--input", str(pdf), "--page-index", "0", "--max-dimension", "2000"], timeout=60)
ocr_result = json.loads(ocr)
recognized = " ".join(line["text"] for line in ocr_result["lines"])
checks["ocr-reference"] = "LOCAL STUDIO 2026" in recognized
checks["source-preserved"] = digest_file(source) == source_hash
report = dict(schema_version=1, scope="Actual native packaged media/image/OCR diagnostics; not complete release acceptance", source_commit=json.loads((resources / "build-provenance.json").read_text())["source_commit"], architecture=host_architecture(), rosetta_translated=is_rosetta_translated(), checks=checks)
atomic_write_json(target / "diagnostic.local.json", report)
if not all(checks.values()):
    raise RuntimeError("Native engine diagnostic checks failed: " + ", ".join(name for name, value in checks.items() if not value))
print(json.dumps({"checks": len(checks), "native_engine_diagnostics": "passed"}))
