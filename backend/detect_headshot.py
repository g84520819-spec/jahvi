import cv2
import numpy as np
import json
import os
import sys
import time

# =========================================================
# ⚙️ CONFIGURATION - EDIT YOUR FILE HERE
# =========================================================
VIDEO_FILE = "backend/br2edited.mp4"  # <--- Put your video path/filename here
SAVE_JSON = True                 # Saves results to 'timestamps.json'
SHOW_DEBUG_WINDOW = False        # Set to True to see a visual window while processing

# Middle-ground ROI that widened the crop without making it too broad.
# Percent of frame HEIGHT (0.0 = very top, 1.0 = bottom):
ROI_Y_TOP = 0.05
ROI_Y_BOTTOM = 0.45
# Percent of frame WIDTH (0.0 = left edge, 1.0 = right edge), centered:
ROI_X_LEFT = 0.10
ROI_X_RIGHT = 0.90
# =========================================================


class NonAIIconDetector:
    """
    5-Step Non-AI HUD Icon Detector:
    1. Dynamic ROI Slicing (Top 5-50% height, 15-85% width)
    2. Dual-Masking (HSV High Saturation + Canny Edge Sharpness)
    3. Morphological Closing
    4. Contour Geometry Validation
    5. Temporal Debouncing & Backtracking
    """
    def __init__(self, min_area=300, max_area=12000, min_solidity=0.40):
        self.min_area = min_area
        self.max_area = max_area
        self.min_solidity = min_solidity

    def process_roi(self, roi_frame):
        if roi_frame is None or roi_frame.size == 0:
            return False, None

        hsv = cv2.cvtColor(roi_frame, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(roi_frame, cv2.COLOR_BGR2GRAY)

        # Dual-mask: High saturation + Sharp Canny edge
        lower_vivid = np.array([0, 100, 130])
        upper_vivid = np.array([180, 255, 255])
        color_mask = cv2.inRange(hsv, lower_vivid, upper_vivid)
        edges = cv2.Canny(gray, 100, 200)

        combined_mask = cv2.bitwise_and(color_mask, edges)

        # Morphological closing
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
        closed_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_CLOSE, kernel)

        # Geometry validation — collect every valid candidate, then pick the
        # strongest one (largest area) instead of just the first contour found.
        contours, _ = cv2.findContours(closed_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        best = None
        best_area = 0
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if self.min_area <= area <= self.max_area:
                x, y, w, h = cv2.boundingRect(cnt)
                aspect_ratio = float(w) / h

                if 0.5 <= aspect_ratio <= 2.0:
                    hull = cv2.convexHull(cnt)
                    hull_area = cv2.contourArea(hull)
                    if hull_area > 0 and (float(area) / hull_area) >= self.min_solidity:
                        if area > best_area:
                            best_area = area
                            best = (x, y, w, h)

        if best is not None:
            return True, best
        return False, None


def format_time(seconds):
    minutes = int(seconds // 60)
    sec = seconds % 60
    return f"{minutes:02d}:{sec:06.3f}"


def detect_headshots(video_path, *, save_json=False):
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found at '{video_path}'")

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video file '{video_path}'")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / fps

    print("=" * 60)
    print(f"📹 Processing Video: {os.path.basename(video_path)}")
    print(f"⏱️  FPS: {fps:.2f} | Total Frames: {total_frames} | Duration: {format_time(duration)}")
    print("=" * 60)

    detector = NonAIIconDetector()

    # State Machine Variables
    # Keep the original threshold to preserve the stable detector behavior.
    TRIGGER_THRESHOLD = 3
    consecutive_frames = 0
    icon_active = False
    lockout_counter = 0
    clear_screen_counter = 0
    last_bbox_center = None

    # A real icon stays roughly in the same spot while it's visible; noise
    # (stray saturated pixels from gunfire, blood, etc.) tends to jump
    # around frame to frame. If a "detection" appears far from the last
    # one, it's treated as a brand-new candidate rather than a continuation.
    POSITION_TOLERANCE_RATIO = 0.35

    LOCKOUT_MIN_FRAMES = max(5, int(fps * 0.15))
    CLEAR_RESET_FRAMES = 2

    detected_kills = []
    start_time = time.time()

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        current_frame = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
        height, width = frame.shape[:2]

        # Upper-center ROI (where the headshot icon appears)
        y1, y2 = int(height * ROI_Y_TOP), int(height * ROI_Y_BOTTOM)
        x1, x2 = int(width * ROI_X_LEFT), int(width * ROI_X_RIGHT)
        roi = frame[y1:y2, x1:x2]

        is_detected, bbox = detector.process_roi(roi)

        if is_detected:
            bx, by, bw, bh = bbox
            center = (bx + bw / 2.0, by + bh / 2.0)
            roi_h, roi_w = roi.shape[:2]
            tol_x = roi_w * POSITION_TOLERANCE_RATIO
            tol_y = roi_h * POSITION_TOLERANCE_RATIO

            same_spot = True
            if last_bbox_center is not None and not icon_active:
                dx = abs(center[0] - last_bbox_center[0])
                dy = abs(center[1] - last_bbox_center[1])
                same_spot = dx <= tol_x and dy <= tol_y

            consecutive_frames = consecutive_frames + 1 if same_spot else 1
            last_bbox_center = center
            clear_screen_counter = 0

            if consecutive_frames == TRIGGER_THRESHOLD and not icon_active:
                icon_active = True
                lockout_counter = 0

                # Retrospective backtrack to frame 1 of pop-in
                origin_frame = current_frame - TRIGGER_THRESHOLD + 1
                timestamp_sec = origin_frame / fps
                formatted_ts = format_time(timestamp_sec)

                kill_event = {
                    "kill_id": len(detected_kills) + 1,
                    "frame": origin_frame,
                    "timestamp_seconds": round(timestamp_sec, 3),
                    "timestamp_formatted": formatted_ts
                }
                detected_kills.append(kill_event)

                print(f"🎯 [KILL #{kill_event['kill_id']}] Detected at {formatted_ts} "
                      f"(Second: {timestamp_sec:.3f}s | Frame #{origin_frame})")
        else:
            if not icon_active:
                consecutive_frames = 0
                last_bbox_center = None

        # Cooldown / Reset Logic
        if icon_active:
            lockout_counter += 1
            if not is_detected:
                clear_screen_counter += 1

            if lockout_counter >= LOCKOUT_MIN_FRAMES and clear_screen_counter >= CLEAR_RESET_FRAMES:
                icon_active = False
                consecutive_frames = 0
                lockout_counter = 0
                clear_screen_counter = 0

        if SHOW_DEBUG_WINDOW:
            cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 255, 0), 2)
            if is_detected and bbox:
                bx, by, bw, bh = bbox
                cv2.rectangle(frame, (x1 + bx, y1 + by), (x1 + bx + bw, y1 + by + bh), (0, 0, 255), 2)
            cv2.imshow("Debug Mode (Press 'q' to Quit)", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

    cap.release()
    if SHOW_DEBUG_WINDOW:
        cv2.destroyAllWindows()

    elapsed = time.time() - start_time
    print("=" * 60)
    print(f"✅ Finished in {elapsed:.2f}s ({total_frames / elapsed:.1f} FPS processing speed)")
    print(f"📊 Total Kills Found: {len(detected_kills)}")
    print("=" * 60)

    if save_json:
        with open("timestamps.json", "w") as f:
            json.dump(detected_kills, f, indent=4)
        print("📁 Timestamps saved to 'timestamps.json'")
    return detected_kills


def main():
    video_path = sys.argv[1] if len(sys.argv) > 1 else VIDEO_FILE
    try:
        detect_headshots(video_path, save_json=SAVE_JSON)
    except (FileNotFoundError, RuntimeError) as error:
        print(f"❌ Error: {error}")
        sys.exit(1)


if __name__ == "__main__":
    main()
    