import os
import subprocess
import tempfile

import librosa
import numpy as np
from scipy.signal import butter, sosfilt

# Extend these if you need to support more formats.
#this function finds beat in a music
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".wma", ".opus"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv"}


def _extract_audio_if_needed(input_path):
    """
    If input_path is a video file, pull just its audio track out via ffmpeg
    into a temp mp3 and return (temp_path, temp_path) so the caller knows to
    clean it up afterward.

    If input_path is already an audio file (e.g. mp3), it's returned
    unchanged with no extraction step and no temp file to clean up.
    """
    ext = os.path.splitext(input_path)[1].lower()

    if ext in AUDIO_EXTENSIONS:
        return input_path, None

    if ext in VIDEO_EXTENSIONS:
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".mp3")
        os.close(tmp_fd)
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-vn",                 # drop video stream entirely
            "-acodec", "libmp3lame",
            "-q:a", "2",           # good quality, small enough/fast
            tmp_path,
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            os.remove(tmp_path)
            raise RuntimeError(
                f"ffmpeg audio extraction failed for {input_path}: "
                f"{result.stderr.decode(errors='ignore')}"
            )
        return tmp_path, tmp_path

    # Unknown extension: hand it to librosa as-is and let it fail loudly
    # if it genuinely can't read the file, rather than silently guessing.
    return input_path, None


def _bandpass_rms(audio, sr, low, high, hop_length):
    """RMS loudness of just one frequency band."""
    sos = butter(4, [low, high], btype="bandpass", fs=sr, output="sos")
    filtered = sosfilt(sos, audio)
    return librosa.feature.rms(y=filtered, hop_length=hop_length)[0]


def _normalize(values):
    v_min, v_max = values.min(), values.max()
    return (values - v_min) / (v_max - v_min + 1e-9)


def _find_headshot_moments_from_audio(
    audio_path,
    gap_seconds=0.5,
    top_percent=0.15,
    snap_to_beat=True,
    beat_snap_tolerance=0.12,
):
    """
    Core detector. Assumes audio_path already points at an audio file
    (no video handling here — see find_headshot_moments() for that).

    Find strong, well-spaced "impact" moments in a track — loud, bassy,
    percussive hits suitable for syncing effects/cuts to.

    Generalizes across genres by:
      1. Separating percussive content from harmonic/melodic content
         (vocals, pads, sustained synths no longer pollute the score).
      2. Still measuring raw bass-band (40-150Hz) energy on the full mix,
         so 808/kick/sub hits stay dominant for bass-driven genres
         like phonk/trap/rage without needing genre-specific tuning.
      3. Using adaptive (not global) peak-picking, so quiet verses and
         loud choruses are each scored on their own local dynamics
         instead of one fixed threshold for the whole song.
      4. Optionally snapping picked moments to the nearest detected beat,
         which matters most for genres with a clear, steady beat grid
         (phonk, trap, EDM) and is harmless/skipped when there isn't one
         close enough (loose, rubato, or beat-less passages).
    """
    audio_samples, sample_rate = librosa.load(audio_path, sr=None)
    hop_length = 512

    # ---------- Split into percussive vs harmonic/melodic content ----------
    _harmonic, percussive = librosa.effects.hpss(audio_samples)

    # ---------- 1. RMS of the percussive layer: general "hit" loudness ----------
    rms = librosa.feature.rms(y=percussive, hop_length=hop_length)[0]

    # ---------- 2. Bass-band energy (40-150Hz) on the full mix ----------
    # Full mix (not just percussive) so sustained 808 slides still register.
    bass_energy = _bandpass_rms(audio_samples, sample_rate, 40, 150, hop_length)

    # ---------- 3. Onset strength on the percussive layer ----------
    onset_strength = librosa.onset.onset_strength(
        y=percussive, sr=sample_rate, hop_length=hop_length
    )

    # ---------- 4. Tempo/beat grid (for optional snapping) ----------
    _tempo, beat_frames = librosa.beat.beat_track(
        y=percussive, sr=sample_rate, hop_length=hop_length
    )
    beat_times = librosa.frames_to_time(beat_frames, sr=sample_rate, hop_length=hop_length)

    shortest_length = min(len(rms), len(bass_energy), len(onset_strength))
    rms = rms[:shortest_length]
    bass_energy = bass_energy[:shortest_length]
    onset_strength = onset_strength[:shortest_length]

    rms_norm = _normalize(rms)
    bass_norm = _normalize(bass_energy)
    onset_norm = _normalize(onset_strength)

    # Bass weighted highest — dominant for phonk/trap/rage, still contributes
    # (just less decisively) for genres where bass isn't the main driver.
    combined_score = (0.45 * bass_norm) + (0.3 * rms_norm) + (0.25 * onset_norm)

    times = librosa.frames_to_time(np.arange(shortest_length), sr=sample_rate, hop_length=hop_length)

    # ---------- Adaptive peak-picking ----------
    # Windows sized in frames from gap_seconds; delta/wait keep picks sparse
    # and locally-relative rather than globally thresholded.
    frames_per_sec = sample_rate / hop_length
    window = max(1, int(round(gap_seconds * frames_per_sec)))
    wait = window

    peak_frames = librosa.util.peak_pick(
        combined_score,
        pre_max=window,
        post_max=window,
        pre_avg=window * 2,
        post_avg=window * 2,
        delta=0.05,
        wait=wait,
    )

    if len(peak_frames) == 0:
        return []

    peak_scores = combined_score[peak_frames]
    peak_times = times[peak_frames]

    # Keep only the strongest top_percent of the *picked peaks* (not all frames).
    if 0 < top_percent < 1 and len(peak_frames) > 1:
        threshold = np.percentile(peak_scores, 100 - (top_percent * 100))
        keep = peak_scores >= threshold
        peak_times = peak_times[keep]
        peak_scores = peak_scores[keep]

    # ---------- Optional: snap each moment to the nearest beat ----------
    if snap_to_beat and len(beat_times) > 0:
        snapped = []
        for t in peak_times:
            idx = np.searchsorted(beat_times, t)
            candidates = [beat_times[i] for i in (idx - 1, idx) if 0 <= i < len(beat_times)]
            if candidates:
                nearest = min(candidates, key=lambda b: abs(b - t))
                if abs(nearest - t) <= beat_snap_tolerance:
                    t = nearest
            snapped.append(t)
        peak_times = np.array(snapped)

    # Re-enforce min spacing after snapping (snapping can pull two picks together)
    order = np.argsort(peak_times)
    peak_times = peak_times[order]
    peak_scores = peak_scores[order]

    headshot_moments = []
    last_picked_time = -gap_seconds
    for t, s in zip(peak_times, peak_scores):
        if t - last_picked_time >= gap_seconds:
            headshot_moments.append({"time": round(float(t), 2), "score": round(float(s), 3)})
            last_picked_time = t

    return headshot_moments


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

    # Rank the beat grid by musical impact instead of returning every beat,
    # while retaining the beat tracker's timing for the selected moments.
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

    # Avoid clustered selections when several nearby beats are equally strong.
    order = np.argsort(candidate_times)
    min_gap_seconds = 0.5
    selected_times = []
    for index in order:
        time = float(candidate_times[index])
        if not selected_times or time - selected_times[-1] >= min_gap_seconds:
            selected_times.append(time)

    return [round(time, 3) for time in selected_times if np.isfinite(time) and time >= 0]


def find_headshot_moments(
    audio_or_video_path,
    gap_seconds=0.5,
    top_percent=0.15,
    snap_to_beat=True,
    beat_snap_tolerance=0.12,
):
    """
    Public entry point — accepts either an audio file (mp3, wav, etc.) or
    a video file (mp4, mov, etc.).

    - Video: audio is extracted to a temp mp3 via ffmpeg first, processed,
      then the temp file is deleted.
    - Audio: used as-is, no extraction step, nothing to clean up.
    """
    audio_path, temp_path = _extract_audio_if_needed(audio_or_video_path)
    try:
        return _find_headshot_moments_from_audio(
            audio_path,
            gap_seconds=gap_seconds,
            top_percent=top_percent,
            snap_to_beat=snap_to_beat,
            beat_snap_tolerance=beat_snap_tolerance,
        )
    finally:
        if temp_path is not None and os.path.exists(temp_path):
            os.remove(temp_path)


# Example usage
if __name__ == "__main__":
    moments = find_headshot_moments("song.mp3", gap_seconds=1.0)   # audio -> used directly
    # moments = find_headshot_moments("clip.mp4", gap_seconds=1.0) # video -> audio extracted first
    print(moments)


##end of librosa