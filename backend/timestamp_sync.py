import json
import math
import os
import subprocess
import tempfile
from pathlib import Path


def _run_ffmpeg(command, error_message):
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or error_message)


def _video_duration(video_path):
    command = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", str(video_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Could not read video duration")
    duration = float(json.loads(result.stdout)["format"]["duration"])
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError("Video duration is invalid")
    return duration


def _valid_timestamps(values, duration):
    try:
        cleaned = sorted({round(float(value), 3) for value in values})
    except (TypeError, ValueError):
        raise ValueError("Headshot timestamps must be numeric")
    if not cleaned or any(value < 0 or value >= duration for value in cleaned):
        raise ValueError("Headshot timestamps must be within the video duration")
    if any(not math.isfinite(value) for value in cleaned):
        raise ValueError("Headshot timestamps must be finite")
    return cleaned


def _pair_timestamps(headshots, beats):
    """Pair ordered headshots with beats, cycling footage for extra beats."""
    if not beats:
        raise ValueError("No usable beats were detected")
    if not headshots:
        raise ValueError("At least one headshot timestamp is required")

    repeated = []
    while len(repeated) < len(beats):
        repeated.extend(headshots)
    return list(zip(repeated, beats))


def _has_audio_stream(video_path):
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=index", "-of", "csv=p=0", str(video_path),
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def _write_clip(video_path, start, end, output_path):
    """Extract a raw clip from video_path[start:end] at normal 1x speed.

    No atempo/setpts retiming — the clip plays back exactly as recorded.
    """
    clip_duration = end - start
    if clip_duration <= 0.01:
        return False
    has_audio = _has_audio_stream(video_path)
    command = [
        "ffmpeg", "-y",
        "-ss", f"{start:.3f}", "-t", f"{clip_duration:.3f}",
        "-i", str(video_path),
    ]
    if has_audio:
        command.extend(["-map", "0:v:0", "-map", "0:a:0", "-c:a", "aac", "-ar", "48000", "-ac", "2"])
    else:
        command.extend(["-map", "0:v:0", "-an"])
    command.extend(["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(output_path)])
    _run_ffmpeg(command, "Could not extract video clip")
    return True


def _concat_segments(segment_paths, output_path, work_dir):
    list_path = Path(work_dir) / "concat.txt"
    list_path.write_text("".join(f"file '{path}'\n" for path in segment_paths), encoding="utf-8")
    _run_ffmpeg(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
         "-c", "copy", "-movflags", "+faststart", str(output_path)],
        "Could not concatenate synchronized video segments",
    )


HEADSHOT_TRAIL_SECONDS = 1.0


def _segment_window(headshot, target_duration, duration):
    """A target_duration-long window of source footage ending
    HEADSHOT_TRAIL_SECONDS after `headshot` (so the headshot frame lands
    just before the beat cut instead of exactly on it — otherwise the
    follow-through right after the headshot gets chopped off). The window
    length itself (target_duration, driven by beat spacing) is unchanged;
    only where inside the source footage it's taken from shifts by +1s.

    - EXCESS (more footage available before the headshot than needed):
      trims down to just the last `target_duration` seconds before the
      shifted end point.
    - DEFICIT (not enough footage before the headshot): pulls the missing
      seconds from the tail of the source video, wrapping around as many
      times as necessary, and stitches it in front.
    """
    end = min(duration, headshot + HEADSHOT_TRAIL_SECONDS)
    start = end - target_duration
    if start >= 0:
        return [(start, end)]

    windows = []
    if end > 0:
        windows.append((0.0, end))
    remaining = -start
    cursor = duration
    while remaining > 0.01:
        take = min(cursor, remaining)
        windows.insert(0, (cursor - take, cursor))
        remaining -= take
        cursor = duration if (cursor - take) <= 0.01 else cursor - take
    return windows


def _forward_windows(start, target_duration, duration):
    """A target_duration-long window of source footage starting at `start`
    and moving forward, wrapping to the beginning of the source as many
    times as necessary to fill the requested duration."""
    start = start % duration
    remaining = target_duration
    cursor = start
    windows = []
    while remaining > 0.01:
        available = duration - cursor
        take = min(available, remaining)
        if take > 0.01:
            windows.append((cursor, cursor + take))
        remaining -= take
        cursor = 0.0
    return windows


def _write_windowed_segment(video_path, windows, output_path, work_dir, name):
    if not windows:
        return False
    if len(windows) == 1:
        start, end = windows[0]
        return _write_clip(video_path, start, end, output_path)
    # Multiple pieces (wrap-around) — cut each at 1x speed and stitch together.
    piece_dir = Path(work_dir) / f"{name}_pieces"
    piece_dir.mkdir()
    try:
        piece_paths = []
        for index, (start, end) in enumerate(windows):
            piece_path = piece_dir / f"piece_{index:04d}.mp4"
            if _write_clip(video_path, start, end, piece_path):
                piece_paths.append(piece_path)
        if not piece_paths:
            return False
        _concat_segments(piece_paths, output_path, piece_dir)
        return True
    finally:
        for path in piece_dir.glob("*"):
            path.unlink(missing_ok=True)
        piece_dir.rmdir()


def _normalize_duration(video_path, target_duration, output_path):
    current_duration = _video_duration(video_path)
    padding = max(0.0, target_duration - current_duration)
    video_filter = f"tpad=stop_mode=clone:stop_duration={padding:.6f},trim=duration={target_duration:.6f},setpts=PTS-STARTPTS"
    filter_graph = f"[0:v]{video_filter}[v]"
    if _has_audio_stream(video_path):
        filter_graph += f";[0:a]apad,atrim=duration={target_duration:.6f},asetpts=PTS-STARTPTS[a]"
        command = [
            "ffmpeg", "-y", "-i", str(video_path), "-filter_complex", filter_graph,
            "-map", "[v]", "-map", "[a]", "-c:a", "aac",
        ]
    else:
        command = [
            "ffmpeg", "-y", "-i", str(video_path), "-filter_complex", filter_graph,
            "-map", "[v]", "-an",
        ]
    command.extend(["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(output_path)])
    _run_ffmpeg(command, "Could not normalize synchronized video duration")


def synchronize_video(video_path, headshot_timestamps, beat_timestamps, output_path, target_duration=None):
    """Retimes video so selected headshots land on beats within the target
    duration. Every clip plays at normal 1x speed: gaps that are too long
    get cut down, gaps that are too short get filled with extra footage
    pulled from the source (wrapping around as needed) — never sped up or
    slowed down.
    """
    duration = _video_duration(video_path)
    if target_duration is None:
        target_duration = duration
    if not math.isfinite(target_duration) or target_duration <= 0:
        raise ValueError("Target duration must be positive and finite")
    headshots = _valid_timestamps(headshot_timestamps, duration)
    try:
        beats = sorted({round(float(value), 3) for value in beat_timestamps})
    except (TypeError, ValueError):
        raise ValueError("Beat timestamps must be numeric")
    if any(not math.isfinite(value) or value < 0 for value in beats):
        raise ValueError("Beat timestamps must be finite and non-negative")
    pairs = _pair_timestamps(headshots, beats)
    work_dir = tempfile.mkdtemp(prefix="jahvi_sync_")
    segments = []

    try:
        output_cursor = 0.0
        last_headshot = 0.0
        for index, (headshot, beat) in enumerate(pairs):
            segment_target_duration = beat - output_cursor
            if segment_target_duration <= 0.01:
                continue

            segment_path = Path(work_dir) / f"segment_{index:04d}.mp4"
            windows = _segment_window(headshot, segment_target_duration, duration)
            if _write_windowed_segment(video_path, windows, segment_path, work_dir, f"segment_{index:04d}"):
                segments.append(segment_path)

            output_cursor = beat
            last_headshot = headshot

        if output_cursor < target_duration:
            tail_target_duration = target_duration - output_cursor
            tail_path = Path(work_dir) / "tail.mp4"
            windows = _forward_windows(last_headshot, tail_target_duration, duration)
            if _write_windowed_segment(video_path, windows, tail_path, work_dir, "tail"):
                segments.append(tail_path)

        if not segments:
            raise RuntimeError("No synchronized video segments were produced")
        concatenated_path = Path(work_dir) / "concatenated.mp4"
        _concat_segments(segments, concatenated_path, work_dir)
        _normalize_duration(concatenated_path, target_duration, output_path)
    finally:
        for path in Path(work_dir).glob("*"):
            path.unlink(missing_ok=True)
        os.rmdir(work_dir)
