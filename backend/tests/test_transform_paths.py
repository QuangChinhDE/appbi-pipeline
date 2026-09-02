"""Path validation and object storage.

The file API takes a path from the request body, so this is the boundary that
stops `models/../../../root/.ssh/authorized_keys` from becoming a write. It is
checked once, centrally, because a check repeated at every call site is a check
that will eventually be forgotten at one of them.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from app.core.errors import ValidationError
from app.transforms.runtime.workspace import (
    find_project_root, validate_path, validate_directory,
)
from app.transforms.storage import (
    LocalObjectStore, ObjectNotFound, content_key, digest_of,
)


class PathValidation(unittest.TestCase):
    def test_accepts_ordinary_project_paths(self):
        for path in (
            "dbt_project.yml",
            "models/staging/stg_orders.sql",
            "models/marts/_marts.yml",
            "macros/cents_to_dollars.sql",
            "seeds/country_codes.csv",
            "analyses/revenue debug.sql",
            ".gitignore",
        ):
            with self.subTest(path=path):
                self.assertEqual(validate_path(path).value, path)

    def test_rejects_traversal(self):
        for path in (
            "../etc/passwd",
            "models/../../../root/.ssh/authorized_keys",
            "models/./../../secrets",
            "a/../../b",
        ):
            with self.subTest(path=path):
                with self.assertRaises(ValidationError):
                    validate_path(path)

    def test_rejects_absolute_and_drive_paths(self):
        for path in ("/etc/passwd", "C:\\Windows\\System32\\config",
                     "//server/share/file"):
            with self.subTest(path=path):
                with self.assertRaises(ValidationError):
                    validate_path(path)

    def test_normalises_backslashes_and_duplicate_slashes(self):
        self.assertEqual(
            validate_path("models\\staging\\stg.sql").value,
            "models/staging/stg.sql",
        )
        self.assertEqual(validate_path("models//staging//x.sql").value,
                         "models/staging/x.sql")

    def test_rejects_null_bytes(self):
        with self.assertRaises(ValidationError):
            validate_path("models/a\x00.sql")

    def test_rejects_dbt_output_directories(self):
        # Storing dbt's own output in a revision would make every parse dirty
        # the project and race with the runtime.
        for path in ("target/manifest.json", "dbt_packages/dbt_utils/x.sql",
                     "logs/dbt.log", ".git/config"):
            with self.subTest(path=path):
                with self.assertRaises(ValidationError) as caught:
                    validate_path(path)
                self.assertEqual(caught.exception.code, "TRANSFORM_PATH_RESERVED")

    def test_rejects_trailing_space_or_dot(self):
        # Windows silently strips these, so `a.sql ` and `a.sql` would be two
        # rows in storage and one file on the runtime's disk.
        with self.assertRaises(ValidationError):
            validate_path("models/a.sql ")
        with self.assertRaises(ValidationError):
            validate_path("models/a.")

    def test_rejects_excessive_depth(self):
        with self.assertRaises(ValidationError):
            validate_path("/".join(["a"] * 20) + "/x.sql")

    def test_empty_directory_is_the_root(self):
        self.assertEqual(validate_directory(""), "")
        self.assertEqual(validate_directory("/"), "")
        self.assertEqual(validate_directory("models/staging"), "models/staging")

    def test_text_detection_drives_editor_versus_download(self):
        self.assertTrue(validate_path("models/a.sql").is_text)
        self.assertTrue(validate_path("seeds/a.csv").is_text)
        self.assertTrue(validate_path(".gitignore").is_text)
        self.assertFalse(validate_path("assets/logo.png").is_text)


class ProjectRootDetection(unittest.TestCase):
    def test_finds_root_at_top_level(self):
        self.assertEqual(
            find_project_root(["dbt_project.yml", "models/a.sql"]), "",
        )

    def test_finds_root_in_a_subdirectory(self):
        self.assertEqual(
            find_project_root(["README.md", "transform/dbt_project.yml",
                               "transform/models/a.sql"]),
            "transform",
        )

    def test_prefers_the_shallowest_project(self):
        self.assertEqual(
            find_project_root([
                "dbt/dbt_project.yml",
                "dbt/deeply/nested/dbt_project.yml",
            ]),
            "dbt",
        )

    def test_ignores_a_vendored_project_inside_dbt_packages(self):
        # A package ships its own dbt_project.yml; it must never win over the
        # repository's real one.
        self.assertEqual(
            find_project_root([
                "dbt_packages/dbt_utils/dbt_project.yml",
                "analytics/dbt_project.yml",
            ]),
            "analytics",
        )

    def test_returns_none_when_there_is_no_project(self):
        self.assertIsNone(find_project_root(["README.md", "src/main.py"]))


class ContentAddressing(unittest.TestCase):
    def test_same_content_gives_the_same_key(self):
        self.assertEqual(content_key(b"select 1"), content_key(b"select 1"))
        self.assertNotEqual(content_key(b"select 1"), content_key(b"select 2"))

    def test_key_is_sharded_and_carries_the_digest(self):
        key = content_key(b"x")
        digest = digest_of(b"x")
        self.assertTrue(key.startswith("blobs/"))
        self.assertTrue(key.endswith(digest))
        self.assertEqual(key.count("/"), 3)


class LocalStore(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = LocalObjectStore(self.directory.name)

    async def asyncTearDown(self):
        self.directory.cleanup()

    async def test_round_trip(self):
        key = await self.store.put_content(b"select 1 as id")
        self.assertEqual(await self.store.get(key), b"select 1 as id")
        self.assertTrue(await self.store.exists(key))

    async def test_missing_key_raises_rather_than_returning_empty(self):
        # An empty .sql is legal; a missing one is not. Conflating them would
        # turn a lost blob into a model that silently compiles to nothing.
        with self.assertRaises(ObjectNotFound):
            await self.store.get(content_key(b"never stored"))

    async def test_writing_the_same_content_twice_is_idempotent(self):
        first = await self.store.put_content(b"same")
        second = await self.store.put_content(b"same")
        self.assertEqual(first, second)

    async def test_deduplication_across_a_project(self):
        # The property that makes a revision per save affordable: saving one
        # file in a 400-file project stores one blob and reuses the rest.
        keys = await self.store.put_many([b"a", b"b", b"a", b"b", b"c"])
        self.assertEqual(len(set(keys)), 3)

    async def test_traversal_key_is_refused(self):
        with self.assertRaises(ValidationError):
            await self.store.put("../escape", b"x")
        with self.assertRaises(ValidationError):
            await self.store.get("/etc/passwd")

    async def test_delete_is_idempotent(self):
        key = await self.store.put_content(b"gone")
        await self.store.delete(key)
        await self.store.delete(key)
        self.assertFalse(await self.store.exists(key))

    async def test_blob_lands_where_the_key_says(self):
        key = await self.store.put_content(b"located")
        self.assertTrue((Path(self.directory.name) / key).is_file())

    async def test_concurrent_writes_of_the_same_blob(self):
        results = await asyncio.gather(
            *(self.store.put_content(b"racing") for _ in range(20))
        )
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(await self.store.get(results[0]), b"racing")


if __name__ == "__main__":
    unittest.main()
