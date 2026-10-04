"""The long-form joiner trims each section's near-silent edges, inserts exactly the designed pause,
and level-matches very short sections (0.6.126). Synthetic audio only: tone bursts padded with
-55 dBFS noise, standing in for a model's speech and its near-silent section edges.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from backend import generation

SR = 24000
RNG = np.random.default_rng(7)


def _section(speech_s: float, *, lead_s: float, tail_s: float, amp: float = 0.3,
             noise_amp: float = 0.0018) -> np.ndarray:
    """Tone burst (4 Hz amplitude wobble, 40 ms ramps like a word edge) between -55 dBFS noise."""
    n = round(speech_s * SR)
    t = np.arange(n) / SR
    tone = amp * np.sin(2 * np.pi * 220 * t) * (0.75 + 0.25 * np.sin(2 * np.pi * 4 * t))
    ramp = round(0.04 * SR)
    tone[:ramp] *= np.linspace(0, 1, ramp)
    tone[-ramp:] *= np.linspace(1, 0, ramp)
    noise = lambda s: (RNG.standard_normal(round(s * SR)) * noise_amp).astype(np.float32)
    return np.concatenate([noise(lead_s), tone.astype(np.float32), noise(tail_s)])


def _join(tmp_path: Path, sections: list[np.ndarray], pauses: list[float]) -> np.ndarray:
    paths = []
    for i, audio in enumerate(sections):
        paths.append(tmp_path / f"s{i}.wav")
        sf.write(paths[-1], audio, SR, subtype="FLOAT")
    out = tmp_path / "joined.wav"
    # Levelling is FFmpeg's and has its own tests; here the sections go in at the levels they have.
    generation._levelled_sections = lambda paths, *a, **k: (list(paths), False)
    generation._join_long_form_wavs(paths, out, "test-family", pause_sequence_s=pauses)
    audio, rate = sf.read(out, dtype="float32")
    assert rate == SR
    return audio


def _db(x: np.ndarray) -> np.ndarray:
    w = round(0.01 * SR)
    m = len(x) // w
    return 10 * np.log10(np.mean(x[: m * w].reshape(m, w) ** 2, axis=1) + 1e-12)


def _speech_runs(x: np.ndarray, threshold_db: float = -45.0) -> list[tuple[float, float]]:
    """(start_s, end_s) of each stretch of 10 ms windows above the threshold, gaps under 30 ms merged."""
    on = np.flatnonzero(_db(x) >= threshold_db)
    runs: list[list[int]] = []
    for i in on:
        if runs and i - runs[-1][1] <= 3:
            runs[-1][1] = i
        else:
            runs.append([i, i])
    return [(a * 0.01, (b + 1) * 0.01) for a, b in runs]


@pytest.fixture(autouse=True)
def _restore_levelling():
    original = generation._levelled_sections
    yield
    generation._levelled_sections = original


@pytest.mark.parametrize("pause", [0.12, 0.3, 0.6])
def test_pause_at_join_equals_designed_pause_whatever_the_model_left_at_the_edges(tmp_path, pause):
    # 1.2 s of near-silence after the first section, 0.7 s before the second, 0.5 s after it...
    sections = [_section(2.0, lead_s=0.2, tail_s=1.2), _section(2.0, lead_s=0.7, tail_s=0.5),
                _section(2.0, lead_s=1.1, tail_s=0.0)]
    audio = _join(tmp_path, sections, [pause, pause])

    runs = _speech_runs(audio)
    assert len(runs) == 3
    for (_, end), (start, _) in zip(runs, runs[1:]):
        assert abs((start - end) - pause) <= 0.02


def test_each_joins_own_designed_pause_is_used(tmp_path):
    sections = [_section(2.0, lead_s=0.4, tail_s=0.9) for _ in range(3)]
    audio = _join(tmp_path, sections, [0.3, 0.6])
    runs = _speech_runs(audio)
    gaps = [b[0] - a[1] for a, b in zip(runs, runs[1:])]
    assert abs(gaps[0] - 0.3) <= 0.02 and abs(gaps[1] - 0.6) <= 0.02


def test_leading_and_trailing_silence_of_the_whole_render_is_trimmed_to_a_pad(tmp_path):
    audio = _join(tmp_path, [_section(2.0, lead_s=1.0, tail_s=1.0), _section(2.0, lead_s=1.0, tail_s=1.0)],
                  [0.3])
    runs = _speech_runs(audio)
    assert runs[0][0] <= 0.06 and len(audio) / SR - runs[-1][1] <= 0.1


def test_speech_is_not_trimmed(tmp_path):
    sections = [_section(2.0, lead_s=0.9, tail_s=1.2), _section(1.5, lead_s=0.6, tail_s=0.8)]
    audio = _join(tmp_path, sections, [0.3])
    loud = lambda x: x[np.abs(x) > 0.01]  # only the tone is that loud; the noise tops out near 0.008
    original = np.concatenate([loud(s) for s in sections])
    joined = loud(audio)
    # every speech sample survives, including the 40 ms word-edge ramps, bit for bit
    assert len(joined) == len(original)
    assert np.allclose(joined, original, atol=1e-6)


def test_a_section_with_no_speech_like_run_is_joined_untouched(tmp_path):
    quiet = (RNG.standard_normal(SR) * 0.0018).astype(np.float32)
    audio = _join(tmp_path, [quiet, _section(2.0, lead_s=0.5, tail_s=0.5)], [0.2])
    assert np.allclose(audio[:SR], quiet, atol=1e-6)


def _voiced_db(audio: np.ndarray, start_s: float, end_s: float) -> float:
    seg = audio[round(start_s * SR): round(end_s * SR)]
    d = _db(seg)
    d = d[d >= -45]  # the voiced windows, as the joiner measures them
    return 10 * np.log10(np.mean(10 ** (d / 10)))


def _level_scene(tmp_path, long_amp: float, short_amp: float):
    def sec(speech_s, amp):
        return _section(speech_s, lead_s=0.3, tail_s=0.3, amp=amp, noise_amp=0.0003)
    sections = [sec(6.0, long_amp), sec(6.0, long_amp), sec(1.5, short_amp), sec(6.0, long_amp),
                sec(6.0, long_amp)]
    audio = _join(tmp_path, sections, [0.3] * 4)
    runs = _speech_runs(audio, -60.0)
    assert len(runs) == 5
    return audio, [_voiced_db(audio, *r) for r in runs]


def test_a_short_quiet_section_is_brought_up_to_its_neighbours(tmp_path):
    _, levels = _level_scene(tmp_path, long_amp=0.3, short_amp=0.15)  # 6 dB under the others
    assert abs(levels[2] - levels[1]) <= 1.0 and abs(levels[2] - levels[3]) <= 1.0
    assert abs(levels[0] - levels[1]) <= 0.1  # long sections are not touched


def test_the_gain_is_bounded(tmp_path):
    _, quiet = _level_scene(tmp_path, long_amp=0.8, short_amp=0.1)  # 18 dB under: only +6 dB allowed
    assert 11.5 <= quiet[1] - quiet[2] <= 12.5
    _, loud = _level_scene(tmp_path, long_amp=0.15, short_amp=0.9)  # 15.6 dB over: only -6 dB allowed
    assert 9.0 <= loud[2] - loud[1] <= 10.2


def test_matching_never_clips(tmp_path):
    # a quiet short section with one tall transient AT the -1.5 dBFS ceiling (what `loudnorm` leaves):
    # +6 dB would be 0.84 * 2 = clipping, so the peak is rounded off and the body still comes up.
    spiky = _section(1.5, lead_s=0.3, tail_s=0.3, amp=0.1, noise_amp=0.0003)
    spiky[SR] = 0.841
    sections = [_section(6.0, lead_s=0.3, tail_s=0.3), spiky, _section(6.0, lead_s=0.3, tail_s=0.3)]
    audio = _join(tmp_path, sections, [0.3, 0.3])
    assert np.max(np.abs(audio)) <= 0.8415  # never past the ceiling the levelling pass uses
    runs = _speech_runs(audio, -60.0)
    assert _voiced_db(audio, *runs[1]) - _voiced_db(sections[1], 0, 2.1) >= 4.0  # the gain did apply


def test_all_short_sections_have_no_reference_and_are_left_alone(tmp_path):
    sections = [_section(1.5, lead_s=0.3, tail_s=0.3, amp=a) for a in (0.1, 0.3)]
    audio = _join(tmp_path, sections, [0.3])
    runs = _speech_runs(audio)
    assert abs((_voiced_db(audio, *runs[1]) - _voiced_db(audio, *runs[0])) - 9.5) <= 0.5


def test_sentence_and_paragraph_pauses_follow_the_text():
    text = "The river was quiet. The mill stood still.\n\nMara listened."
    chunks = ["The river was quiet.", "The mill stood still.", "Mara listened."]
    # Owner decision 2026-10-03: one rule for every family (2026-10-04: 1.0 s after a sentence, 1.5 s at a paragraph).
    assert generation._production_join_pauses_s(text, chunks, 1.0) == pytest.approx([1.00, 1.50])
