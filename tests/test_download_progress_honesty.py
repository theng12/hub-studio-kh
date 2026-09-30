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

    def test_the_hub_now_reports_byte_counts_and_the_app_reads_them(self):
        """The tripwire fired, as it was written to: the Hub now reports progress (0.6.114).

        `bytes_done` is measured from the folder the download writes into and `total_bytes` is the
        manifest's own sum, so the row draws a real bar as soon as the first file starts. The
        spinner and the model's size remain only for the moment before there is anything to count.
        """
        source = (ROOT / "components/hub/model_downloads.py").read_text()
        for field in ("bytes_done", "total_bytes", "error_tail"):
            self.assertIn(field, source, "the Hub must publish " + field)
        for field in ("bytesDone", "totalBytes", "errorTail"):
            self.assertIn(f"let {field}: ", API[API.index("private struct HubModelDownload"):][:400],
                          "the app must decode " + field)
        self.assertNotIn('percent: 0, downloadedBytes: 0, totalBytes: 0,\n                    error: run.state',
                         API, "the app must not hardcode a zero for a download the Hub measures")


if __name__ == "__main__":
    unittest.main()
