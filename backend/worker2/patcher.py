import json
import os
import subprocess
import tempfile

# Only these three are accepted for export_ratio — deliberately locked down.
RATIO_SPECS = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
}

# Every headshot clip's own [start, end) window gets this much extra time
# past the raw timestamp before any gap-mode duration is applied on top,
# so the full headshot moment (the follow-through right after the kill) is
# always visible instead of getting cut off exactly at the stamp. This is
# purely a trim-boundary concern — it never affects where an effect lands
# (that still uses the raw, unmodified stamp).
TRIM_LEAD_SECONDS = 1.0

# Before any patch footage is taken from a leftover ("natural gap") region,
# this many seconds are trimmed off BOTH of its edges. Both edges of a
# leftover gap border a neighboring headshot's own clip (the start-edge is
# the end of the previous headshot's window, the end-edge is the start of
# the next one's) — patching from right at either edge means the borrowed
# footage is really just that neighbor's immediate lead-in/lead-out, which
# reads as a duplicate/stutter of that other headshot. A gap too small to
# survive this buffer on both sides is skipped entirely rather than shrunk.
LEFTOVER_EDGE_BUFFER_SECONDS = 1.0


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
        "-avoid_negative_ts", "make_zero",
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


def _compute_primary_windows(timestamps, gap, duration):
    """Each headshot's own [start, end) clip window, clipped to video bounds.
    end = timestamp + TRIM_LEAD_SECONDS + gap: the +1s guarantees the full
    headshot moment is visible even before any gap-mode duration is added
    on top."""
    return [
        (max(0.0, t), min(duration, t + TRIM_LEAD_SECONDS + gap))
        for t in timestamps
    ]


def _compute_leftover_gaps(primary_windows, duration):
    """
    Regions of the video NOT covered by any headshot's own primary window —
    i.e. the video's natural gaps. Returned sorted biggest-first (fixed
    order — this ordering is not re-sorted later as gaps get consumed).
    """
    sorted_windows = sorted(primary_windows, key=lambda w: w[0])
    leftover = []
    cursor = 0.0
    for start, end in sorted_windows:
        if start > cursor:
            leftover.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration:
        leftover.append((cursor, duration))
    leftover.sort(key=lambda w: (w[1] - w[0]), reverse=True)
    return leftover


def _build_patch_pool(leftover_gaps):
    """
    Turn raw leftover gaps into the actual patchable pool: each gap gets
    LEFTOVER_EDGE_BUFFER_SECONDS trimmed off both edges (once, up front —
    the buffer is never re-applied later as a gap gets partially consumed).
    Gaps too small to survive the buffer on both sides are dropped
    entirely. Order is preserved (biggest original gap first) since this
    fixed order is what the descending cycle walks.
    Returns a list of mutable [start, end] pairs.
    """
    pool = []
    for g_start, g_end in leftover_gaps:
        usable_start = g_start + LEFTOVER_EDGE_BUFFER_SECONDS
        usable_end = g_end - LEFTOVER_EDGE_BUFFER_SECONDS
        if usable_end - usable_start > 0.01:
            pool.append([usable_start, usable_end])
    return pool


class _PatchCycle:
    """
    Persistent, wrapping cursor over the buffered patch pool. Walks the
    pool in its fixed (biggest-first) order, consuming from whichever gap
    the cursor currently sits on, advancing to the next gap once the
    current one is exhausted, and wrapping back to the start once the end
    is reached — so repeated deficits across many headshots spread out
    across different leftover footage instead of always re-taking from the
    same one gap.
    """

    def __init__(self, pool):
        self.pool = pool
        self.cursor = 0

    def take(self, needed):
        segments = []
        remaining = needed

        if not self.pool:
            return segments, remaining

        # Safety guard against spinning forever if every gap is exhausted:
        # one full pass over the pool with zero progress means there's
        # genuinely nothing left to give.
        stalled_passes = 0
        while remaining > 0.01 and stalled_passes <= len(self.pool):
            gap = self.pool[self.cursor]
            gap_size = gap[1] - gap[0]

            if gap_size <= 0.01:
                self.cursor = (self.cursor + 1) % len(self.pool)
                stalled_passes += 1
                continue

            take = min(gap_size, remaining)
            segments.append((gap[0], gap[0] + take))
            gap[0] += take
            remaining -= take
            stalled_passes = 0

            if gap[1] - gap[0] <= 0.01:
                self.cursor = (self.cursor + 1) % len(self.pool)

        return segments, max(0.0, remaining)


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

    video_path           : source video file
    headshot_timestamps   : list of seconds, e.g. [2, 5.5, 7]
    gap                    : gap-mode duration (seconds) applied on top of
                              the +1s trim lead, e.g. 5
    export_ratio           : one of "16:9", "9:16", "1:1" — nothing else
    output_path             : where the final merged, ratio-cropped video is written

    For each headshot: cuts [timestamp, timestamp + TRIM_LEAD_SECONDS + gap].
    If the video runs out before reaching that full length, the missing
    seconds are patched in from the video's unused ("natural gap") regions,
    cycling through them biggest-first and wrapping around rather than
    always re-taking from the single biggest one — and each gap has a 1s
    buffer trimmed off both edges before anything is taken from it, so
    patched footage never bleeds into a neighboring headshot's own clip.

    Returns {"output_path": ..., "warnings": [...]}. Warnings list any
    headshot where even cycling through every leftover region in the video
    still couldn't fully cover the requested length.
    """
    if export_ratio not in RATIO_SPECS:
        raise ValueError(f"export_ratio must be one of {list(RATIO_SPECS)}, got {export_ratio!r}")

    duration = _get_video_duration(video_path)
    timestamps = sorted(t for t in headshot_timestamps if 0 <= t < duration)
    if not timestamps:
        raise ValueError("No valid headshot timestamps within the video's duration.")

    primary_windows = _compute_primary_windows(timestamps, gap, duration)
    leftover_gaps = _compute_leftover_gaps(primary_windows, duration)
    patch_pool = _build_patch_pool(leftover_gaps)
    patch_cycle = _PatchCycle(patch_pool)

    work_dir = tempfile.mkdtemp(prefix="headshot_highlight_")
    warnings = []
    clip_paths = []

    try:
        total = len(timestamps)
        target_len = TRIM_LEAD_SECONDS + gap
        for i, (t, (start, end)) in enumerate(zip(timestamps, primary_windows)):
            print(f"[{i + 1}/{total}] Headshot at {t}s...")
            segment_specs = [(start, end)]
            deficit = target_len - (end - start)

            if deficit > 0.01:
                patch_segments, short_by = patch_cycle.take(deficit)
                segment_specs.extend(patch_segments)
                print(f"    patching {deficit - short_by:.2f}s from elsewhere in the video")
                if short_by > 0.01:
                    warnings.append(
                        f"Headshot at {t}s: only filled {target_len - short_by:.2f}s of the "
                        f"requested {target_len}s — not enough unused footage left in the video."
                    )

            piece_paths = []
            for j, (s, e) in enumerate(segment_specs):
                piece_path = os.path.join(work_dir, f"hs{i}_part{j}.mp4")
                if _cut_segment(video_path, s, e, piece_path):
                    piece_paths.append(piece_path)

            if not piece_paths:
                print(f"    skipped — nothing usable for this headshot")
                continue

            if len(piece_paths) == 1:
                clip_paths.append(piece_paths[0])
            else:
                clip_path = os.path.join(work_dir, f"hs{i}_full.mp4")
                _concat_clips(piece_paths, clip_path, work_dir, tag=f"hs{i}")
                clip_paths.append(clip_path)

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

    parser = argparse.ArgumentParser(description="Build a merged headshot highlight video.")
    parser.add_argument("video", help="Path to the source video file")
    parser.add_argument("timestamps", help="Comma-separated headshot timestamps, e.g. 2,5.5,7")
    parser.add_argument("gap", type=float, help="Gap-mode duration in seconds applied on top of the +1s trim lead")
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
