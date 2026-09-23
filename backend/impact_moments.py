import librosa
import numpy as np


def find_headshot_moments(audio_path, gap_seconds=0.5, top_percent=0.15):

    # Load the audio. sr=None keeps its original sample rate.
    audio_samples, sample_rate = librosa.load(audio_path, sr=None)

    # Window size for all our measurements (in samples)
    hop_length = 512  # roughly ~11-12ms per window at typical sample rates

    # ---------- 1. RMS: overall loudness per window ----------
    rms = librosa.feature.rms(y=audio_samples, hop_length=hop_length)[0]

    # ---------- 2. Bass-band energy: loudness of just the low frequencies ----------
    from scipy.signal import butter, sosfilt
    sos = butter(4, [40, 150], btype='bandpass', fs=sample_rate, output='sos')
    bass_only_audio = sosfilt(sos, audio_samples)
    bass_energy = librosa.feature.rms(y=bass_only_audio, hop_length=hop_length)[0]

    # ---------- 3. Onset strength: where new hits begin ----------
    onset_strength = librosa.onset.onset_strength(y=audio_samples, sr=sample_rate, hop_length=hop_length)

    # Make sure all three arrays are the same length before combining
    shortest_length = min(len(rms), len(bass_energy), len(onset_strength))
    rms = rms[:shortest_length]
    bass_energy = bass_energy[:shortest_length]
    onset_strength = onset_strength[:shortest_length]

    # Normalize each curve to 0-1 so they're on the same scale before weighting
    def normalize(values):
        return (values - values.min()) / (values.max() - values.min() + 1e-9)

    rms_norm = normalize(rms)
    bass_norm = normalize(bass_energy)
    onset_norm = normalize(onset_strength)

    # ---------- Combine into one score per window ----------
    combined_score = (0.4 * rms_norm) + (0.4 * bass_norm) + (0.2 * onset_norm)

    # Convert window index -> actual time in seconds
    times = librosa.frames_to_time(np.arange(shortest_length), sr=sample_rate, hop_length=hop_length)

    # ---------- Peak-picking: keep only the strongest, well-spaced moments ----------
    threshold = np.percentile(combined_score, 100 - (top_percent * 100))

    headshot_moments = []
    last_picked_time = -gap_seconds

    for i in range(1, shortest_length - 1):
        is_local_peak = combined_score[i] > combined_score[i - 1] and combined_score[i] > combined_score[i + 1]
        is_above_threshold = combined_score[i] >= threshold
        is_far_enough_from_last_pick = (times[i] - last_picked_time) >= gap_seconds

        if is_local_peak and is_above_threshold and is_far_enough_from_last_pick:
            headshot_moments.append({"time": round(float(times[i]), 2)})
            last_picked_time = times[i]

    return headshot_moments


if __name__ == "__main__":
    moments = find_headshot_moments("song.mp3", gap_seconds=1.0)
    print(moments)
