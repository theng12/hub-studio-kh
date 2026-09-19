"""The ready-made voice catalog must be reachable.

FastAPI matches routes in declaration order. `GET /api/voices/seed-catalog` was declared *after*
`GET /api/voices/{voice_id}`, so the parameterised route swallowed it: a request for the catalog was
served by the single-voice handler with `voice_id="seed-catalog"`, which answered "voice not found".
The app's ready-made voices were therefore always empty, and nothing said why.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.voice.app.backend import main as voice  # noqa: E402


def _paths():
    return [getattr(route, "path", "") for route in voice.app.routes]


class SeedCatalogRouteTests(unittest.TestCase):
    def test_the_literal_path_is_registered_before_the_parameterised_one(self):
        paths = _paths()
        self.assertIn("/api/voices/seed-catalog", paths)
        self.assertIn("/api/voices/{voice_id}", paths)
        self.assertLess(paths.index("/api/voices/seed-catalog"),
                        paths.index("/api/voices/{voice_id}"),
                        "declared later, the literal path can never be reached")

    def test_the_route_resolves_to_the_catalog_handler(self):
        """The real question: which handler answers that URL?"""
        from fastapi.routing import APIRoute
        for route in voice.app.routes:
            if not isinstance(route, APIRoute) or "GET" not in route.methods:
                continue
            match = route.matches({"type": "http", "method": "GET", "path": "/api/voices/seed-catalog",
                                   "headers": [], "query_string": b"", "root_path": "", "scheme": "http",
                                   "server": ("127.0.0.1", 80), "client": ("127.0.0.1", 1)})
            if match[0].name == "FULL":
                self.assertEqual(route.name, "get_seed_catalog",
                                 f"/api/voices/seed-catalog is answered by {route.name}")
                return
        self.fail("no route matched /api/voices/seed-catalog")

    def test_a_single_voice_still_resolves_to_the_voice_handler(self):
        from fastapi.routing import APIRoute
        for route in voice.app.routes:
            if not isinstance(route, APIRoute) or "GET" not in route.methods:
                continue
            match = route.matches({"type": "http", "method": "GET", "path": "/api/voices/0fb9c76a924f",
                                   "headers": [], "query_string": b"", "root_path": "", "scheme": "http",
                                   "server": ("127.0.0.1", 80), "client": ("127.0.0.1", 1)})
            if match[0].name == "FULL":
                self.assertEqual(route.name, "get_voice")
                return
        self.fail("no route matched a single voice")

    def test_the_catalog_answers_with_entries(self):
        result = voice.get_seed_catalog()
        self.assertIn("entries", result)
        self.assertIsInstance(result["entries"], list)


if __name__ == "__main__":
    unittest.main()
