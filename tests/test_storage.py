from __future__ import annotations

import unittest

from ksi_local.storage import GIB, VolumeInfo, estimate_video_storage, validate_selected_workspace


def volume(**overrides: object) -> VolumeInfo:
    values: dict[str, object] = {
        "mount_point": "/Volumes/VideoSSD",
        "name": "VideoSSD",
        "volume_uuid": "volume-123",
        "filesystem": "apfs",
        "internal": False,
        "writable": True,
        "total_bytes": 128 * GIB,
        "free_bytes": 88 * GIB,
        "workspace_id": "workspace-123",
    }
    values.update(overrides)
    return VolumeInfo(**values)  # type: ignore[arg-type]


class WorkspaceValidationTests(unittest.TestCase):
    def test_accepts_exact_external_identity(self) -> None:
        valid, errors = validate_selected_workspace(
            volume(),
            expected_volume_uuid="volume-123",
            expected_workspace_id="workspace-123",
        )
        self.assertTrue(valid)
        self.assertEqual(errors, ())

    def test_rejects_same_name_with_different_uuid(self) -> None:
        valid, errors = validate_selected_workspace(
            volume(volume_uuid="other-volume"),
            expected_volume_uuid="volume-123",
            expected_workspace_id="workspace-123",
        )
        self.assertFalse(valid)
        self.assertTrue(any("UUID" in error for error in errors))

    def test_rejects_internal_readonly_or_wrong_workspace(self) -> None:
        invalid = volume(internal=True, writable=False, workspace_id="other-workspace")
        valid, errors = validate_selected_workspace(
            invalid,
            expected_volume_uuid="volume-123",
            expected_workspace_id="workspace-123",
        )
        self.assertFalse(valid)
        self.assertEqual(len(errors), 3)

    def test_rejects_tiny_utility_partition_as_workspace(self) -> None:
        tiny = volume(total_bytes=350 * 1024**2, free_bytes=340 * 1024**2)
        self.assertFalse(tiny.suitable_external_workspace)


class StorageBudgetTests(unittest.TestCase):
    def test_current_ssd_can_fit_each_provided_video_and_models(self) -> None:
        for duration in (3123, 981.762):
            with self.subTest(duration=duration):
                budget = estimate_video_storage(duration, free_bytes=88 * GIB)
                self.assertTrue(budget.fits)

    def test_small_free_space_fails_reserve_policy(self) -> None:
        budget = estimate_video_storage(3600, free_bytes=25 * GIB, missing_model_bytes=0)
        self.assertFalse(budget.fits)

    def test_duration_over_three_hours_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            estimate_video_storage(3 * 60 * 60 + 1, free_bytes=100 * GIB)


if __name__ == "__main__":
    unittest.main()
