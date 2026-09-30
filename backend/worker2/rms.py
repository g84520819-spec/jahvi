import librosa
import numpy as np
from scipy.signal import butter, sosfilt


def _bandpass_rms(audio, sample_rate, low, high, hop_length):
    sos = butter(4, [low, high], btype="bandpass", fs=sample_rate, output="sos")
    filtered = sosfilt(sos, audio)
    return librosa.feature.rms(y=filtered, hop_length=hop_length)[0]


def _normalize(values):
    value_min, value_max = values.min(), values.max()
    return (values - value_min) / (value_max - value_min + 1e-9)


def detect_beats(audio_path):
    """Return strong, chronologically ordered beat timestamps detected by Librosa."""
    audio_samples, sample_rate = librosa.load(audio_path, sr=None)
    _harmonic, percussive = librosa.effects.hpss(audio_samples)
    hop_length = 512
    _tempo, beat_frames = librosa.beat.beat_track(
        y=percussive, sr=sample_rate, hop_length=hop_length
    )
    if len(beat_frames) == 0:
        return []

    rms = librosa.feature.rms(y=percussive, hop_length=hop_length)[0]
    bass_energy = _bandpass_rms(audio_samples, sample_rate, 40, 150, hop_length)
    onset_strength = librosa.onset.onset_strength(
        y=percussive, sr=sample_rate, hop_length=hop_length
    )
    shortest_length = min(len(rms), len(bass_energy), len(onset_strength))
    if shortest_length == 0:
        return []

    combined_score = (
        0.45 * _normalize(bass_energy[:shortest_length])
        + 0.3 * _normalize(rms[:shortest_length])
        + 0.25 * _normalize(onset_strength[:shortest_length])
    )
    valid_frames = np.asarray(
        [frame for frame in beat_frames if 0 <= frame < shortest_length],
        dtype=int,
    )
    if len(valid_frames) == 0:
        return []

    beat_scores = combined_score[valid_frames]
    threshold = np.percentile(beat_scores, 85) if len(beat_scores) > 1 else beat_scores[0]
    candidate_frames = valid_frames[beat_scores >= threshold]
    candidate_times = librosa.frames_to_time(
        candidate_frames, sr=sample_rate, hop_length=hop_length
    )

    order = np.argsort(candidate_times)
    min_gap_seconds = 0.5
    selected_times = []
    for index in order:
        beat_time = float(candidate_times[index])
        if not selected_times or beat_time - selected_times[-1] >= min_gap_seconds:
            selected_times.append(beat_time)

    return [
        round(beat_time, 3)
        for beat_time in selected_times
        if np.isfinite(beat_time) and beat_time >= 0
    ]
