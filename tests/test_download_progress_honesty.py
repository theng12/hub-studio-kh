"""A download must not be shown as "0 bytes / 0%" when nobody knows its progress.

The Downloads row drew a percentage bar for every active download. For a Hub-owned model download
there is no percentage to draw: the Hub runs the downloader as a child process and reports only the
run's state, and the app filled the gap with a hardcoded zero - so the bar sat at zero for the whole
download, which reads as "stuck", and the size read "0 bytes" for a 12 GB model. The row now shows a
spinner and the model's own size instead. Worker-reported downloads still have real byte counts and
still show their bar.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEWS = (ROOT / "native" / "StudioViews.swift").read_text()
API = (ROOT / "native" / "StudioAPI.swift").read_text()


class DownloadProgressHonestyTests(unittest.TestCase):
    def test_the_zero_bar_is_gone_from_the_download_row(self):
        self.assertNotIn(
            'if active(download.state) { ProgressView(value: min(100, max(0, download.percent)), total: 100) }',
            VIEWS,
            "an unconditional bar draws a permanent zero when no percentage is known")

    def test_the_bar_is_drawn_only_when_there_are_real_numbers(self):
        self.assertIn("download.percent > 0 || download.totalBytes > 0", VIEWS,
                      "a worker-reported download still has a true percentage")

    def test_the_unknown_case_says_what_is_known(self):
        self.assertIn("sizeCaption(for: download)", VIEWS)
        self.assertIn("Downloading in the background", VIEWS)
        # The size comes from the model, which is the figure the Models page shows - not invented.
        caption = VIEWS[VIEWS.index("private func sizeCaption(for download: NativeDownload) -> String {"):]
        caption = caption[:caption.index("\n    }")]
        self.assertIn("workspace.models.first(where: { $0.repo == download.repo })?.sizeGB", caption)

    def test_the_hub_really_has_no_byte_counts_to_show(self):
        """Why the app cannot show a percentage: the run record the Hub returns has none.

        If this ever changes, the row should start showing the real figure rather than the caption.
        """
        source = (ROOT / "components/hub/model_downloads.py").read_text()
        record = source[source.index('run = {"id": run_id, "package_id": package_id'):]
        record = record[:record.index("}")]
        for field in ("bytes", "percent", "progress", "total"):
            self.assertNotIn(field, record,
                             "the Hub now reports progress; show it in the row instead")


if __name__ == "__main__":
    unittest.main()
