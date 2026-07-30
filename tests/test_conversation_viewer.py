from __future__ import annotations

import functools
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import conversation_viewer as viewer


class ConversationDeletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.archive_directory = Path(self.temporary_directory.name)
        self._write_fixture()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _write_json(self, name: str, document: object) -> None:
        (self.archive_directory / name).write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_fixture(self) -> None:
        self._write_json(
            "Codex对话记录_2026-06.json",
            {
                "schema_version": 1,
                "month": "2026-06",
                "updated_at": "2026-06-30T12:00:00+08:00",
                "record_count": 2,
                "records": [
                    {
                        "record_id": "target:turn-1",
                        "session_id": "target",
                        "conversation_title": "需要删除的对话",
                        "user_prompt": "问题一",
                    },
                    {"record_id": "keep:turn-1", "session_id": "keep"},
                ],
            },
        )
        self._write_json(
            "Codex对话记录_2026-07.json",
            {
                "schema_version": 1,
                "month": "2026-07",
                "updated_at": "2026-07-30T12:00:00+08:00",
                "record_count": 2,
                "records": [
                    {
                        "record_id": "target:turn-2",
                        "session_id": "target",
                        "conversation_title": "需要删除的对话",
                        "user_prompt": "问题二",
                    },
                    {"record_id": "other:turn-1", "session_id": "other"},
                ],
            },
        )
        self._write_json(
            "Codex对话索引.json",
            {
                "schema_version": 1,
                "updated_at": "2026-07-30T12:00:00+08:00",
                "sessions": {
                    "target": {"conversation_title": "需要删除的对话"},
                    "keep": {"conversation_title": "保留的对话"},
                },
            },
        )

    def test_delete_moves_cross_month_session_to_trash(self) -> None:
        result = viewer.delete_archived_conversation(self.archive_directory, "target")

        self.assertEqual(result["deleted_record_count"], 2)
        self.assertEqual(result["affected_file_count"], 2)
        self.assertEqual(result["remaining_record_count"], 2)

        for month in ("2026-06", "2026-07"):
            document = json.loads(
                (self.archive_directory / f"Codex对话记录_{month}.json").read_text(encoding="utf-8")
            )
            self.assertEqual(document["record_count"], 1)
            self.assertFalse(any(record.get("session_id") == "target" for record in document["records"]))

        index = json.loads((self.archive_directory / "Codex对话索引.json").read_text(encoding="utf-8"))
        self.assertNotIn("target", index["sessions"])
        self.assertIn("keep", index["sessions"])

        trash_files = list((self.archive_directory / ".trash").glob("*.json"))
        self.assertEqual(len(trash_files), 1)
        trash = json.loads(trash_files[0].read_text(encoding="utf-8"))
        self.assertEqual(trash["status"], "completed")
        self.assertEqual(trash["session_id"], "target")
        self.assertEqual(trash["deleted_record_count"], 2)
        self.assertEqual(
            {item["source_file"] for item in trash["records"]},
            {"Codex对话记录_2026-06.json", "Codex对话记录_2026-07.json"},
        )
        self.assertEqual(trash["index_entry"]["conversation_title"], "需要删除的对话")
        self.assertFalse(any((self.archive_directory / ".locks").glob("*.lock")))

    def test_restore_returns_records_to_original_months_and_index(self) -> None:
        deletion = viewer.delete_archived_conversation(self.archive_directory, "target")
        restored = viewer.restore_archived_conversation(self.archive_directory, deletion["deletion_id"])

        self.assertEqual(restored["restored_record_count"], 2)
        self.assertEqual(restored["skipped_existing_count"], 0)
        self.assertEqual(restored["remaining_trash_count"], 0)
        for month in ("2026-06", "2026-07"):
            document = json.loads(
                (self.archive_directory / f"Codex对话记录_{month}.json").read_text(encoding="utf-8")
            )
            self.assertEqual(document["record_count"], 2)
            self.assertEqual(sum(record.get("session_id") == "target" for record in document["records"]), 1)

        index = json.loads((self.archive_directory / "Codex对话索引.json").read_text(encoding="utf-8"))
        self.assertEqual(index["sessions"]["target"]["conversation_title"], "需要删除的对话")
        trash_path = self.archive_directory / deletion["trash_file"]
        trash = json.loads(trash_path.read_text(encoding="utf-8"))
        self.assertEqual(trash["status"], "restored")
        self.assertEqual(trash["restored_record_count"], 2)
        self.assertEqual(viewer.read_trash_items(self.archive_directory)[0], [])

    def test_restore_skips_record_ids_that_already_exist(self) -> None:
        deletion = viewer.delete_archived_conversation(self.archive_directory, "target")
        june_path = self.archive_directory / "Codex对话记录_2026-06.json"
        june_document = json.loads(june_path.read_text(encoding="utf-8"))
        june_document["records"].append(
            {
                "record_id": "target:turn-1",
                "session_id": "target",
                "conversation_title": "后来重新写入的版本",
            }
        )
        june_document["record_count"] = len(june_document["records"])
        self._write_json(june_path.name, june_document)

        restored = viewer.restore_archived_conversation(self.archive_directory, deletion["deletion_id"])

        self.assertEqual(restored["restored_record_count"], 1)
        self.assertEqual(restored["skipped_existing_count"], 1)
        june_after = json.loads(june_path.read_text(encoding="utf-8"))
        target_turns = [record for record in june_after["records"] if record.get("record_id") == "target:turn-1"]
        self.assertEqual(len(target_turns), 1)
        self.assertEqual(target_turns[0]["conversation_title"], "后来重新写入的版本")

    def test_purge_permanently_removes_only_the_selected_trash_json(self) -> None:
        deletion = viewer.delete_archived_conversation(self.archive_directory, "target")
        trash_path = self.archive_directory / deletion["trash_file"]
        monthly_snapshot = {
            path.name: path.read_text(encoding="utf-8")
            for path in self.archive_directory.glob("Codex对话记录_*.json")
        }

        purged = viewer.purge_trash_item(self.archive_directory, deletion["deletion_id"])

        self.assertEqual(purged["deleted_record_count"], 2)
        self.assertEqual(purged["remaining_trash_count"], 0)
        self.assertFalse(trash_path.exists())
        self.assertEqual(
            monthly_snapshot,
            {
                path.name: path.read_text(encoding="utf-8")
                for path in self.archive_directory.glob("Codex对话记录_*.json")
            },
        )
        with self.assertRaises(viewer.ArchiveMutationError) as missing_item:
            viewer.purge_trash_item(self.archive_directory, deletion["deletion_id"])
        self.assertEqual(missing_item.exception.status_code, 404)

    def test_delete_api_requires_token_and_updates_archive(self) -> None:
        handler = functools.partial(viewer.ViewerRequestHandler, directory=str(viewer.APP_DIRECTORY))
        server = viewer.ViewerHTTPServer(("127.0.0.1", 0), handler)
        server.configured_archive_directory = self.archive_directory
        server.mutation_token = "test-mutation-token"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"

        try:
            with urlopen(f"{base_url}/api/configured-archive", timeout=5) as response:
                configured_payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(configured_payload["api_version"], viewer.VIEWER_API_VERSION)
            self.assertTrue(configured_payload["capabilities"]["restore"])
            self.assertTrue(configured_payload["capabilities"]["purge"])
            self.assertTrue(configured_payload["can_delete"])
            self.assertEqual(configured_payload["mutation_token"], "test-mutation-token")

            invalid_request = Request(
                f"{base_url}/api/delete-conversation",
                data=json.dumps({"session_id": "target"}).encode("utf-8"),
                headers={"Content-Type": "application/json", "X-Viewer-Token": "wrong-token"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as invalid_response:
                urlopen(invalid_request, timeout=5)
            self.assertEqual(invalid_response.exception.code, 403)

            valid_request = Request(
                f"{base_url}/api/delete-conversation",
                data=json.dumps({"session_id": "target"}).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-Viewer-Token": "test-mutation-token",
                    "Origin": base_url,
                },
                method="POST",
            )
            with urlopen(valid_request, timeout=5) as response:
                deletion_payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(deletion_payload["deleted_record_count"], 2)
            self.assertTrue((self.archive_directory / deletion_payload["trash_file"]).is_file())

            trash_request = Request(
                f"{base_url}/api/trash",
                headers={"X-Viewer-Token": "test-mutation-token"},
            )
            with urlopen(trash_request, timeout=5) as response:
                trash_payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(trash_payload["count"], 1)
            self.assertEqual(trash_payload["items"][0]["deletion_id"], deletion_payload["deletion_id"])

            restore_request = Request(
                f"{base_url}/api/restore-conversation",
                data=json.dumps({"deletion_id": deletion_payload["deletion_id"]}).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-Viewer-Token": "test-mutation-token",
                    "Origin": base_url,
                },
                method="POST",
            )
            with urlopen(restore_request, timeout=5) as response:
                restore_payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(restore_payload["restored_record_count"], 2)
            self.assertEqual(restore_payload["remaining_trash_count"], 0)

            keep_deletion = viewer.delete_archived_conversation(self.archive_directory, "keep")
            purge_request = Request(
                f"{base_url}/api/purge-trash",
                data=json.dumps({"deletion_id": keep_deletion["deletion_id"]}).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-Viewer-Token": "test-mutation-token",
                    "Origin": base_url,
                },
                method="POST",
            )
            with urlopen(purge_request, timeout=5) as response:
                purge_payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(purge_payload["deleted_record_count"], 1)
            self.assertFalse((self.archive_directory / keep_deletion["trash_file"]).exists())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_viewer_server_refuses_a_second_instance_on_the_same_port(self) -> None:
        handler = functools.partial(viewer.ViewerRequestHandler, directory=str(viewer.APP_DIRECTORY))
        first_server = viewer.ViewerHTTPServer(("127.0.0.1", 0), handler)
        port = first_server.server_address[1]
        try:
            with self.assertRaises(OSError):
                second_server = viewer.ViewerHTTPServer(("127.0.0.1", port), handler)
                second_server.server_close()
        finally:
            first_server.server_close()

    def test_concurrent_deletions_are_serialized_by_archive_locks(self) -> None:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda session_id: viewer.delete_archived_conversation(self.archive_directory, session_id),
                    ("target", "keep"),
                )
            )

        self.assertEqual(sorted(result["deleted_record_count"] for result in results), [1, 2])
        remaining_session_ids: set[str] = set()
        for monthly_path in self.archive_directory.glob("Codex对话记录_*.json"):
            document = json.loads(monthly_path.read_text(encoding="utf-8"))
            remaining_session_ids.update(record["session_id"] for record in document["records"])
        self.assertEqual(remaining_session_ids, {"other"})
        self.assertEqual(len(list((self.archive_directory / ".trash").glob("*.json"))), 2)
        self.assertFalse(any((self.archive_directory / ".locks").glob("*.lock")))


if __name__ == "__main__":
    unittest.main()
