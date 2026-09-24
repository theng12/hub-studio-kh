"""An unrecognised provider must not be classified as a cloud model.

``cloud_guard.LOCAL_PROVIDER_VALUES`` was an allowlist of the strings a studio may report in
``provider``. Anything it had not been taught — ``local-mlx``, ``apple``, a new engine's own
name — was treated as an external vendor, and ``monitor._assemble_aggregate_catalog`` drops
every row it calls cloud. One new studio spelling its provider differently therefore had its
whole modality vanish from every picker with no message anywhere: the 0.6.57 symptom arriving
by a different road.

The rule asserted here is not membership of a list. It is that an unknown provider is local,
that the hub says so out loud instead of guessing, and that the vendor names it does know are
still refused.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from components.hub.backend import cloud_guard
from components.hub.backend.monitor import StudioMonitor


def local_entry(**fields) -> dict:
    entry = {"repo": "org/local-model", "cache": {"state": "cached"}, "is_cloud": False}
    entry.update(fields)
    return entry


class UnknownProviderTests(unittest.TestCase):
    def test_a_provider_the_hub_has_never_seen_is_local(self) -> None:
        """The next studio's own spelling of "runs on this Mac" must not be a cloud signal."""
        for provider in ("local-mlx", "apple", "coreml", "ane", "mlx", "on-device-mlx",
                         "brand-new-engine", "local_llm", "studio-hub"):
            with self.subTest(provider=provider):
                self.assertIsNone(
                    cloud_guard.cloud_reason(
                        "org/local-model", modality="chat", entries=[local_entry(provider=provider)],
                    ),
                    f"provider {provider!r} was classified as cloud; a studio that spells its "
                    f"local provider differently would lose every model it publishes",
                )

    def test_every_local_spelling_that_already_shipped_still_reads_local(self) -> None:
        for provider in sorted(cloud_guard.LOCAL_PROVIDER_VALUES):
            with self.subTest(provider=provider):
                self.assertIsNone(
                    cloud_guard.cloud_reason("org/local-model", entries=[local_entry(provider=provider)])
                )

    def test_a_studio_that_reports_nothing_still_reads_local(self) -> None:
        self.assertIsNone(cloud_guard.cloud_reason("org/local-model", entries=[local_entry()]))
        self.assertIsNone(cloud_guard.cloud_reason("org/local-model", entries=[{}]))

    def test_a_provider_naming_a_hosted_vendor_is_still_cloud(self) -> None:
        """The rule must not become "believe every provider string"."""
        for provider in ("cloudflare", "fal", "fal-ai", "kie", "replicate", "gemini",
                         "together", "nebius", "pollinations", "huggingface", "cloud"):
            with self.subTest(provider=provider):
                self.assertIsNotNone(
                    cloud_guard.cloud_reason("org/model", entries=[local_entry(provider=provider)]),
                    f"provider {provider!r} is a hosted vendor and must stay refused",
                )

    def test_an_explicit_cloud_provider_field_is_still_cloud(self) -> None:
        self.assertIsNotNone(
            cloud_guard.cloud_reason("org/model", entries=[local_entry(cloud_provider="somewhere")])
        )

    def test_the_other_cloud_signals_are_unchanged(self) -> None:
        self.assertIsNotNone(cloud_guard.cloud_reason("org/model", entries=[{"is_cloud": True}]))
        self.assertIsNotNone(cloud_guard.cloud_reason("org/model", entries=[{"kind": "cloud"}]))
        self.assertIsNotNone(
            cloud_guard.cloud_reason("org/model", entries=[{"cache": {"state": "cloud"}}])
        )
        self.assertIsNotNone(cloud_guard.cloud_reason("provider:foo:bar"))
        self.assertIsNotNone(cloud_guard.cloud_reason("cloudflare/flux-1-schnell"))
        self.assertIsNotNone(cloud_guard.cloud_reason("org/model", cost_tier="paid"))

    def test_render_stays_exempt_whatever_it_reports(self) -> None:
        self.assertIsNone(
            cloud_guard.cloud_reason(
                "episode-assembly-v1", modality="render",
                entries=[{"is_cloud": True, "provider": "cloudflare"}],
            )
        )

    def test_an_unrecognised_provider_is_recorded_rather_than_guessed_about(self) -> None:
        """Silence is what made this invisible: the hub must be able to say what it saw."""
        with self.assertLogs("studiohub.cloud_guard", level="INFO") as captured:
            cloud_guard.cloud_reason(
                "org/model", entries=[local_entry(provider="a-brand-new-provider")]
            )
        self.assertTrue(
            any("a-brand-new-provider" in line for line in captured.output),
            f"the unrecognised provider was not named in the log: {captured.output}",
        )


class _FakeMonitor:
    """The two monitor members the aggregate assembler reads: registry and observations."""

    def __init__(self, registry: list[dict]):
        self.registry = registry

    def catalog_observation(self, studio_id, *, transcription=False):
        return {"observed_at": 0.0, "age_seconds": 0.0, "stale": False, "last_error": None}


class AggregateCatalogTests(unittest.TestCase):
    def _catalog_for(self, studio: dict, rows: list[dict]) -> dict:
        return StudioMonitor._assemble_aggregate_catalog(
            _FakeMonitor([studio]), [{"models": rows}], [],
        )

    def test_a_row_with_a_new_provider_survives_the_aggregate_catalog(self) -> None:
        studio = {"id": "chat", "modality": "chat", "machine": "local"}
        assembled = self._catalog_for(
            studio, [local_entry(repo="org/new-chat-model", provider="local-mlx")],
        )
        self.assertEqual(
            [row["repo"] for row in assembled["models"]], ["org/new-chat-model"],
            "a model whose studio spells its local provider differently was filtered out of "
            "the aggregate catalog, so it would never reach a picker",
        )
        self.assertEqual(assembled["per_studio"]["chat"]["retired_cloud_models"], 0)

    def test_a_cloud_row_is_still_dropped_and_counted(self) -> None:
        studio = {"id": "chat", "modality": "chat", "machine": "local"}
        assembled = self._catalog_for(
            studio, [local_entry(repo="cloudflare/flux-1-schnell", is_cloud=True)],
        )
        self.assertEqual(assembled["models"], [])
        self.assertEqual(assembled["per_studio"]["chat"]["retired_cloud_models"], 1)


if __name__ == "__main__":
    unittest.main()
