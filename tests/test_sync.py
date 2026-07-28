import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import Mock

from bdpan.models import FileEntry, SyncMapping
from bdpan.sync import SyncManager


class SyncPreviewSafetyTests(unittest.TestCase):
    def _mapping(self, local_path: str, strategy: str) -> SyncMapping:
        return SyncMapping(1, 1, "/7大投行/高盛", local_path, False, 0, strategy)

    def _entry(self) -> FileEntry:
        return FileEntry(
            None, 1, 1, "report.pdf", "/7大投行/高盛/report.pdf",
            False, 10, "md5", 0, None,
        )

    def _entry_with_version(self, modified_time: int, md5: str = "md5") -> FileEntry:
        return FileEntry(
            None, 1, 1, "report.pdf", "/7大投行/高盛/report.pdf",
            False, 10, md5, modified_time, None,
        )

    def test_copy_new_preview_never_reports_local_deletions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "local-only.pdf").touch()
            preview = SyncManager(Mock()).preview_changes(
                self._mapping(tmp, "copy_new"), [self._entry()]
            )

        self.assertEqual(preview["delete"], [])
        self.assertEqual(preview["add"], ["report.pdf"])

    def test_missing_remote_path_is_reported_and_sync_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = SyncManager(Mock())
            mapping = self._mapping(tmp, "mirror")
            entries = [self._entry()]
            mapping.remote_path = "/不存在"

            preview = manager.preview_changes(mapping, entries)
            self.assertTrue(preview["skip"])
            self.assertEqual(preview["delete"], [])
            with self.assertRaisesRegex(ValueError, "未匹配"):
                manager.sync_mapping(mapping, entries)

    def test_mirror_only_deletes_managed_zero_byte_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real_file = Path(tmp, "real.pdf")
            real_file.write_bytes(b"real user data")
            placeholder = Path(tmp, "old-placeholder.pdf")
            placeholder.touch()
            Path(tmp, "old-placeholder.pdf.bdpan").write_text("{}")

            preview = SyncManager(Mock()).preview_changes(
                self._mapping(tmp, "mirror"), [self._entry()]
            )

        self.assertEqual(preview["delete"], ["old-placeholder.pdf"])
        self.assertNotIn("real.pdf", preview["delete"])
        self.assertNotIn("old-placeholder.pdf.bdpan", preview["delete"])

    def test_sync_replaces_zero_byte_pdf_and_removes_legacy_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp, "report.pdf")
            pdf.touch()
            sidecar = Path(tmp, "report.pdf.bdpan")
            sidecar.write_text("{}")

            def downloader(_fs_id: int, destination: str) -> int:
                with open(destination, "wb") as output:
                    output.write(b"real-data!")
                return 10

            db = Mock()
            manager = SyncManager(db, downloader)
            result = manager.sync_mapping(
                self._mapping(tmp, "copy_new"),
                [self._entry()],
            )

            self.assertEqual(pdf.read_bytes(), b"real-data!")
            self.assertEqual(json.loads(sidecar.read_text())["remote"]["fs_id"], 1)
            self.assertEqual(result.files_updated, ["report.pdf"])

    def test_sync_updates_same_size_file_when_remote_version_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp, "report.pdf")
            pdf.write_bytes(b"old-data!!")
            sidecar = Path(tmp, "report.pdf.bdpan")
            sidecar.write_text(json.dumps({
                "version": 1,
                "remote": {
                    "fs_id": 1,
                    "size": 10,
                    "md5": "old-md5",
                    "modified_time": 100,
                },
            }))

            def downloader(_fs_id: int, destination: str) -> int:
                with open(destination, "wb") as output:
                    output.write(b"new-data!!")
                return 10

            db = Mock()
            manager = SyncManager(db, downloader)
            result = manager.sync_mapping(
                self._mapping(tmp, "copy_new"),
                [self._entry_with_version(200, "new-md5")],
            )

            self.assertEqual(pdf.read_bytes(), b"new-data!!")
            self.assertEqual(result.files_updated, ["report.pdf"])
            meta = json.loads(sidecar.read_text())
            self.assertEqual(meta["remote"]["modified_time"], 200)
            self.assertEqual(meta["remote"]["md5"], "new-md5")

    def test_sync_skips_same_remote_version_after_metadata_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp, "report.pdf")
            pdf.write_bytes(b"real-data!")
            Path(tmp, "report.pdf.bdpan").write_text(json.dumps({
                "version": 1,
                "remote": {
                    "fs_id": 1,
                    "size": 10,
                    "md5": "md5",
                    "modified_time": 200,
                },
            }))

            downloader = Mock()
            manager = SyncManager(Mock(), downloader)
            result = manager.sync_mapping(
                self._mapping(tmp, "copy_new"),
                [self._entry_with_version(200)],
            )

            downloader.assert_not_called()
            self.assertEqual(result.files_updated, [])


class WindowsLongPathTests(unittest.TestCase):
    @unittest.skipUnless(__import__("os").name == "nt", "仅适用于 Windows")
    def test_real_download_supports_paths_over_260_chars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            name = "a" * 230 + ".pdf"
            local_file = Path(tmp, name)
            entry = FileEntry(
                None, 1, 1, name, "/高盛/" + name,
                False, 10, "md5", 0, None,
            )

            def downloader(_fs_id: int, destination: str) -> int:
                with open(destination, "wb") as output:
                    output.write(b"real-data!")
                return 10

            manager = SyncManager(Mock(), downloader)

            self.assertGreater(len(str(local_file)), 260)
            manager._download_entry(local_file, entry)

            self.assertTrue(manager._io_path(local_file).is_file())
            self.assertEqual(manager._io_path(local_file).read_bytes(), b"real-data!")
            self.assertIn(name, manager._collect_local_files(Path(tmp)))
            manager._io_path(local_file).unlink()
