import json
import os
import subprocess
import tempfile

# Only these three are accepted for export_ratio — deliberately locked down.
RATIO_SPECS = {
    "16:9": (768, 698),
    "9:16": (576, 768),
    "1:1": (1080, 1080),
}

# See patcher.py — same +1s trim-lead baseline, kept identical across both
# gap modes so "exact" and "maximum" behave consistently around the stamp.
TRIM_LEAD_SECONDS = 1.0


def _get_video_duration(video_path):
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json", video_path,
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {video_path}: {result.stderr.decode(errors='ignore')}")
    return float(json.loads(result.stdout)["format"]["duration"])


def _cut_segment(video_path, start, end, out_path):
    """Cut [start, end) from video_path into out_path. Returns False if the
    requested range is empty/invalid (nothing was written)."""
    duration = end - start
    if duration <= 0.01:
        return False
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-ss", f"{start:.3f}",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", "-c:a", "aac",
        out_path,
    ]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg cut failed [{start}-{end}] — see ffmpeg output above for details")
    return True


def _concat_clips(clip_paths, out_path, work_dir, tag):
    list_file = os.path.join(work_dir, f"{tag}_concat_list.txt")
    with open(list_file, "w") as f:
        for p in clip_paths:
            f.write(f"file '{p}'\n")
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", out_path]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise RuntimeError("ffmpeg concat failed — see ffmpeg output above for details")


def _apply_export_ratio(input_path, output_path, export_ratio):
    """Center-crop to the target aspect ratio (no stretching/distortion),
    then scale to that ratio's standard resolution."""
    target_w, target_h = RATIO_SPECS[export_ratio]
    vf = (
        f"crop='min(iw,ih*{target_w}/{target_h})':'min(ih,iw*{target_h}/{target_w})',"
        f"scale={target_w}:{target_h}:flags=bicubic,setsar=1"
    )
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", "-c:a", "aac",
        output_path,
    ]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise RuntimeError("ffmpeg export-ratio step failed — see ffmpeg output above for details")


def build_headshot_highlight(video_path, headshot_timestamps, gap, export_ratio, output_path):
    """
    Build one merged highlight video from a list of headshot timestamps.
    No patching: each clip is [timestamp, timestamp+gap].

    - If there's not enough footage left after the timestamp to reach the
      full gap, that clip just comes out shorter than gap. No borrowing.
    - If there's more footage available than gap, only the front part is
      kept ([timestamp, timestamp+gap]) — everything past that is simply
      never included, i.e. the excess is cut off the back of the clip.

    video_path            : source video file
    headshot_timestamps    : list of seconds, e.g. [2, 5.5, 7]
    gap                     : single target clip duration (seconds), applied to every headshot
    export_ratio            : one of "16:9", "9:16", "1:1" — nothing else
    output_path              : where the final merged, ratio-cropped video is written

    Returns {"output_path": ..., "warnings": [...]} — warnings list any
    headshot whose clip came out shorter than the requested gap.
    """
    if export_ratio not in RATIO_SPECS:
        raise ValueError(f"export_ratio must be one of {list(RATIO_SPECS)}, got {export_ratio!r}")

    duration = _get_video_duration(video_path)
    timestamps = sorted(t for t in headshot_timestamps if 0 <= t < duration)
    if not timestamps:
        raise ValueError("No valid headshot timestamps within the video's duration.")

    work_dir = tempfile.mkdtemp(prefix="headshot_highlight_")
    warnings = []
    clip_paths = []

    try:
        total = len(timestamps)
        target_len = TRIM_LEAD_SECONDS + gap
        for i, t in enumerate(timestamps):
            print(f"[{i + 1}/{total}] Headshot at {t}s...")
            start = t
            end = min(duration, t + target_len)  # naturally cuts any excess off the back
            actual_len = end - start

            if actual_len < target_len - 0.01:
                warnings.append(
                    f"Headshot at {t}s: clip only {actual_len:.2f}s long "
                    f"(requested {target_len}s) — not enough footage left in the video, left as-is."
                )

            clip_path = os.path.join(work_dir, f"hs{i}.mp4")
            if _cut_segment(video_path, start, end, clip_path):
                clip_paths.append(clip_path)
            else:
                print(f"    skipped — nothing usable for this headshot")

        if not clip_paths:
            raise RuntimeError("No usable headshot clips were produced from the given timestamps.")

        print("Merging all headshot clips...")
        merged_path = os.path.join(work_dir, "merged.mp4")
        _concat_clips(clip_paths, merged_path, work_dir, tag="merged")

        print(f"Cropping/scaling to {export_ratio}...")
        _apply_export_ratio(merged_path, output_path, export_ratio)
        print(f"Done — saved to {output_path}")

    finally:
        for root, _, files in os.walk(work_dir):
            for f in files:
                try:
                    os.remove(os.path.join(root, f))
                except OSError:
                    pass
        try:
            os.rmdir(work_dir)
        except OSError:
            pass

    return {"output_path": output_path, "warnings": warnings}


def _parse_timestamps(raw):
    """'2,5.5,7' -> [2.0, 5.5, 7.0]"""
    return [float(x.strip()) for x in raw.split(",") if x.strip()]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build a merged headshot highlight video (no patching).")
    parser.add_argument("video", help="Path to the source video file")
    parser.add_argument("timestamps", help="Comma-separated headshot timestamps, e.g. 2,5.5,7")
    parser.add_argument("gap", type=float, help="Clip duration in seconds applied to every headshot")
    parser.add_argument("ratio", choices=list(RATIO_SPECS), help="Export aspect ratio")
    parser.add_argument("output", help="Path to write the final merged video to")
    args = parser.parse_args()

    result = build_headshot_highlight(
        video_path=args.video,
        headshot_timestamps=_parse_timestamps(args.timestamps),
        gap=args.gap,
        export_ratio=args.ratio,
        output_path=args.output,
    )
    print(result)
