from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ksi_local.collection_jobs import (
    CollectionItemStatus,
    CollectionState,
    build_collection_probe_plan,
    plan_collection,
)
from ksi_local.storage import GIB


def entry(video_id: str, duration: int = 60, **extra: object) -> dict[str, object]:
    return {
        "id": video_id,
        "title": f"Video {video_id}",
        "duration": duration,
        "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
        **extra,
    }


class Phase30Tests(unittest.TestCase):
    def test_collection_probe_is_metadata_only_and_has_no_default_cap(self) -> None:
        plan = build_collection_probe_plan("https://www.youtube.com/playlist?list=PLsafe")
        self.assertIn("--yes-playlist", plan.argv)
        self.assertIn("--skip-download", plan.argv)
        self.assertNotIn("--playlist-end", plan.argv)
        self.assertNotIn("--cookies-from-browser", plan.argv)

        normalized = plan_collection(
            {
                "entries": [
                    entry(
                        "safe-id",
                        webpage_url="https://youtube.com/watch?v=safe-id&token=SECRET",
                    )
                ]
            },
            source_url="https://www.youtube.com/playlist?list=PLsafe&token=SECRET",
            free_bytes=100 * GIB,
        )
        self.assertEqual(
            normalized.source_url, "https://www.youtube.com/playlist?list=PLsafe"
        )
        self.assertNotIn("SECRET", json.dumps(normalized.to_dict()))

    def test_all_or_selected_items_receive_aggregate_preflight(self) -> None:
        payload = {"entries": [entry(f"video-{index}", 90) for index in range(30)]}
        all_items = plan_collection(
            payload,
            source_url="https://www.youtube.com/playlist?list=PLsafe",
            free_bytes=100 * GIB,
        )
        selected = plan_collection(
            payload,
            source_url="https://www.youtube.com/playlist?list=PLsafe",
            free_bytes=100 * GIB,
            selected_ids=("video-2", "video-8"),
        )
        self.assertEqual(all_items.selected_count, 30)
        self.assertEqual(selected.selected_count, 2)
        self.assertEqual(selected.total_duration_seconds, 180)
        self.assertGreater(selected.required_bytes, 20 * GIB)

    def test_queue_is_sequential_resumable_and_failure_does_not_stop_next(self) -> None:
        plan = plan_collection(
            {"entries": [entry("first"), entry("second")]},
            source_url="https://youtube.com/@safe/videos",
            free_bytes=100 * GIB,
        )
        with tempfile.TemporaryDirectory() as directory:
            state = CollectionState(Path(directory) / "collection.json", plan)
            first = state.claim_next()
            self.assertEqual(first.video_id, "first")
            self.assertIsNone(state.claim_next())
            failed = state.fail(
                "first",
                "password=hunter2 /Volumes/Private SSD/customer/source.mp4",
            )
            self.assertNotIn("hunter2", failed.error or "")
            self.assertNotIn("Private SSD", failed.error or "")
            self.assertNotIn("customer/source.mp4", failed.error or "")
            second = state.claim_next()
            self.assertEqual(second.video_id, "second")
            reloaded = CollectionState.load(state.path)
            self.assertEqual(reloaded.recover_interrupted(), 1)
            self.assertEqual(reloaded.claim_next().video_id, "second")

    def test_update_deduplicates_and_access_failures_are_isolated(self) -> None:
        payload = {
            "entries": [
                entry("done-id"),
                entry("private-id", availability="private"),
                entry("short-id", webpage_url="https://youtube.com/shorts/short-id"),
                entry("replay-id", live_status="was_live"),
            ]
        }
        plan = plan_collection(
            payload,
            source_url="https://youtube.com/@safe/videos",
            free_bytes=100 * GIB,
            completed_ids=("done-id",),
        )
        statuses = {item.video_id: item.status for item in plan.items}
        self.assertEqual(statuses["done-id"], CollectionItemStatus.SKIPPED)
        self.assertEqual(statuses["private-id"], CollectionItemStatus.FAILED)
        self.assertEqual(statuses["short-id"], CollectionItemStatus.PENDING)
        self.assertEqual(statuses["replay-id"], CollectionItemStatus.PENDING)

    def test_active_live_needs_consent_stop_limit_and_disk_reserve(self) -> None:
        payload = {"entries": [entry("live-id", 0, live_status="is_live")]}
        declined = plan_collection(
            payload,
            source_url="https://youtube.com/@safe/live",
            free_bytes=100 * GIB,
        )
        self.assertEqual(declined.items[0].status, CollectionItemStatus.SKIPPED)
        with self.assertRaises(ValueError):
            plan_collection(
                payload,
                source_url="https://youtube.com/@safe/live",
                free_bytes=100 * GIB,
                allow_active_live=True,
            )
        approved = plan_collection(
            payload,
            source_url="https://youtube.com/@safe/live",
            free_bytes=1 * GIB,
            allow_active_live=True,
            live_recording_limit_seconds=3600,
        )
        self.assertFalse(approved.fits)
        self.assertEqual(approved.total_duration_seconds, 3600)


if __name__ == "__main__":
    unittest.main()
