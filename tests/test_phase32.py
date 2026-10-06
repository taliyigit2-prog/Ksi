from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
import tomllib
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication

from ksi_local.image_tools import (
    batch_resize,
    create_quality_preview,
    inspect_image,
    lossless_transform,
    plan_resize,
    remove_uniform_background,
    resize_image,
)
from ksi_local.cli import build_parser
from ksi_local.gui import ImageToolsDialog, _IMAGE_DIALOG_TEXT
from ksi_local.i18n import SUPPORTED_UI_LANGUAGES


def make_image(path: Path, size: tuple[int, int] = (120, 80), *, exif: bool = False) -> None:
    image = Image.new("RGB", size, "white")
    for x in range(35, 85):
        for y in range(20, 65):
            image.putpixel((x, y), (20, 80, 180))
    options = {"exif": Image.Exif()} if exif else {}
    if exif:
        options["exif"][0x010E] = "private camera note"
    image.save(path, **options)


class Phase32Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_drag_drop_dialog_is_localized_for_every_ui_language(self) -> None:
        self.assertEqual(set(_IMAGE_DIALOG_TEXT), set(SUPPORTED_UI_LANGUAGES))
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.png"
            make_image(source)
            for language in SUPPORTED_UI_LANGUAGES:
                dialog = ImageToolsDialog(source, language)
                self.assertEqual(dialog.windowTitle(), _IMAGE_DIALOG_TEXT[language][0])
                self.assertTrue(dialog.keep_aspect.isChecked())
                dialog.close()

    def test_cli_exposes_local_image_tools(self) -> None:
        parser = build_parser()
        inspected = parser.parse_args(["image-inspect", "/tmp/source.png"])
        resized = parser.parse_args(
            ["image-resize", "/tmp/source.png", "/tmp/out.png", "--percent", "50"]
        )
        self.assertEqual(inspected.command, "image-inspect")
        self.assertEqual(resized.percent, 50)

    def test_image_runtime_dependencies_are_declared(self) -> None:
        project = tomllib.loads(
            (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
        )
        dependencies = "\n".join(project["project"]["dependencies"]).casefold()
        self.assertIn("pillow", dependencies)
        self.assertIn("numpy", dependencies)

    def test_pixel_percent_aspect_and_preview_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.jpg"
            make_image(source)
            percent = plan_resize(source, percent=50, output_format="PNG")
            locked = plan_resize(source, width=60, output_format="PNG")
            self.assertEqual((percent.width, percent.height), (60, 40))
            self.assertEqual((locked.width, locked.height), (60, 40))
            preview = create_quality_preview(source, Path(directory) / "preview.png")
            with Image.open(preview) as image:
                self.assertLessEqual(max(image.size), 512)

    def test_resize_creates_new_file_strips_metadata_and_preserves_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.jpg"
            output = Path(directory) / "result.png"
            make_image(source, exif=True)
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            result = resize_image(plan_resize(source, width=60, output_format="PNG"), output)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
            self.assertNotEqual(result, source)
            with Image.open(result) as image:
                self.assertEqual(image.size, (60, 40))
                self.assertNotIn("exif", image.info)

    def test_lossless_policy_overwrite_and_upscale_consent_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.png"
            make_image(source)
            with self.assertRaises(ValueError):
                plan_resize(source, width=60, output_format="JPEG", lossless=True)
            with self.assertRaises(ValueError):
                plan_resize(source, width=180, output_format="PNG")
            pilot = plan_resize(
                source, width=180, output_format="PNG", super_resolution_pilot=True
            )
            self.assertTrue(pilot.super_resolution_pilot)
            with self.assertRaises(ValueError):
                resize_image(pilot, source)
            symlink = Path(directory) / "link.png"
            symlink.symlink_to(source)
            with self.assertRaises(ValueError):
                inspect_image(symlink)
            dangling_target = Path(directory) / "elsewhere.png"
            dangling_output = Path(directory) / "output.png"
            dangling_output.symlink_to(dangling_target)
            with self.assertRaises(ValueError):
                resize_image(plan_resize(source, width=60), dangling_output)
            self.assertFalse(dangling_target.exists())

    def test_disk_preflight_blocks_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.png"
            make_image(source)
            plan = plan_resize(source, width=60, output_format="PNG", free_bytes=1)
            self.assertFalse(plan.fits)
            with self.assertRaises(OSError):
                resize_image(plan, Path(directory) / "blocked.png")
            self.assertFalse((Path(directory) / "blocked.png").exists())

    def test_background_removal_outputs_transparency_and_optional_shadow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "product.png"
            make_image(source)
            output = remove_uniform_background(
                source,
                Path(directory) / "cutout.png",
                tolerance=15,
                feather=10,
                product_shadow=True,
            )
            with Image.open(output) as image:
                self.assertEqual(image.mode, "RGBA")
                self.assertEqual(image.getpixel((0, 0))[3], 0)
                self.assertGreater(image.getpixel((50, 40))[3], 200)

    def test_batch_is_sequential_and_collision_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = [root / "a.png", root / "b.png"]
            for source in sources:
                make_image(source)
            plans = [
                (plan_resize(source, width=60, output_format="PNG"), root / f"out-{index}.png")
                for index, source in enumerate(sources)
            ]
            outputs = batch_resize(plans)
            self.assertEqual(len(outputs), 2)
            with self.assertRaises(FileExistsError):
                batch_resize(plans)

    def test_lossless_rotation_changes_dimensions_without_touching_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            make_image(source)
            before = source.read_bytes()
            output = lossless_transform(
                source, root / "rotated.webp", operation="rotate-90"
            )
            with Image.open(output) as image:
                self.assertEqual(image.size, (80, 120))
            self.assertEqual(source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
