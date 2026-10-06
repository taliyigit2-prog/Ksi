from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from ksi_local.image_editing import (
    CLASSIC_MODEL,
    PLATFORM_PROFILES,
    EditSession,
    platform_profile,
    validate_model_budget,
)
from ksi_local.cli import build_parser


def product_image(path: Path) -> None:
    image = Image.new("RGB", (100, 100), "white")
    for x in range(25, 75):
        for y in range(20, 80):
            image.putpixel((x, y), (220, 30, 40))
    image.save(path)


def product_mask(path: Path) -> None:
    image = Image.new("L", (100, 100), 0)
    for x in range(25, 75):
        for y in range(20, 80):
            image.putpixel((x, y), 255)
    image.save(path)


class Phase33Tests(unittest.TestCase):
    def test_cli_requires_explicit_workflow_and_platform_choice(self) -> None:
        args = build_parser().parse_args(
            [
                "image-edit-start",
                "/tmp/source.png",
                "/tmp/session",
                "--workflow",
                "product",
                "--platform",
                "shopify-product-square",
            ]
        )
        self.assertEqual(args.workflow, "product")
        self.assertEqual(args.platform, "shopify-product-square")

    def test_product_and_general_workflows_are_separate_with_no_default_platform(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            product_image(source)
            with self.assertRaises(ValueError):
                EditSession.create(source, root / "missing-platform", workflow="product")
            general = EditSession.create(source, root / "general", workflow="general")
            self.assertTrue(general.manifest.exists())
            with self.assertRaises(ValueError):
                EditSession.create(
                    source, root / "wrong-general", workflow="general", platform="etsy-listing-square"
                )

    def test_platform_profiles_have_verified_ratio_safe_area_and_file_limits(self) -> None:
        self.assertEqual(platform_profile("etsy-listing-square").width, 2000)
        shopify = platform_profile("shopify-product-square")
        self.assertEqual((shopify.width, shopify.height), (2048, 2048))
        self.assertGreater(shopify.safe_margin_percent, 0)
        self.assertEqual(shopify.max_file_bytes, 20 * 1024**2)
        self.assertTrue(all(profile.official_source.startswith("https://") for profile in PLATFORM_PROFILES.values()))

    def test_small_preview_is_mandatory_and_unsafe_model_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            product_image(source)
            session = EditSession.create(source, root / "session", workflow="general")
            with self.assertRaises(ValueError):
                validate_model_budget("large-remote-model", 12 * 1024**3)
            preview = session.preview("biraz aydınlat", seed=42, model=CLASSIC_MODEL)
            with Image.open(preview) as image:
                self.assertLessEqual(max(image.size), 512)
            with self.assertRaises(PermissionError):
                session.commit(approve_preview=False)
            output = session.commit(approve_preview=True)
            self.assertTrue(output.exists())

    def test_product_mask_preserves_product_while_background_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, mask = root / "source.png", root / "mask.png"
            product_image(source)
            product_mask(mask)
            session = EditSession.create(
                source,
                root / "product-session",
                workflow="product",
                platform="shopify-product-square",
            )
            session.preview("arka plan #00ff00", protect_mask=mask, seed=7)
            output = session.commit(approve_preview=True)
            with Image.open(output) as image:
                self.assertEqual(image.size, (2048, 2048))
                self.assertEqual(image.getpixel((0, 0))[:3], (0, 255, 0))
                self.assertGreater(image.getpixel((1024, 1024))[0], 180)

    def test_repeated_edit_undo_seed_model_and_comparison_are_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            product_image(source)
            session = EditSession.create(source, root / "session", workflow="general")
            session.preview("aydınlat", seed=101)
            first = session.commit(approve_preview=True)
            session.preview("kontrast artır", seed=202)
            second = session.commit(approve_preview=True)
            manifest = json.loads(session.manifest.read_text())
            self.assertEqual([item["seed"] for item in manifest["revisions"]], [101, 202])
            self.assertTrue(all(item["model"] == CLASSIC_MODEL for item in manifest["revisions"]))
            self.assertEqual(session.undo(), first)
            self.assertTrue(second.exists())
            session.preview("aydınlat", seed=303)
            branch = session.commit(approve_preview=True)
            self.assertEqual(branch.name, "revision-003.png")
            self.assertTrue(second.exists())
            comparison = session.comparison(root / "comparison.png")
            self.assertTrue(comparison.exists())


if __name__ == "__main__":
    unittest.main()
