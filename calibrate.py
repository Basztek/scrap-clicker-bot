#!/usr/bin/env python3
"""Measures the barrel grid on the phone's screen and writes calibration.json.

All bots load that file (see src/board.py) and adapt their 1220x2712
coordinates to the phone. Open the game on the board (not
during a magnet storm) before running this. The grid is found from the board
background; check the saved preview image, and if the grid is wrong, click
the barrel centers yourself (m at the prompt, or --manual).
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from collections.abc import Sequence
from typing import Any

import cv2
import numpy as np

# Measure against the plain 1220x2712 values, not against an earlier calibration
os.environ['SCRAP_CALIBRATION'] = ''
from src import board, hud  # noqa: E402
from src.board import Color, Frame  # noqa: E402
from src.phone import screenshot  # noqa: E402

Calibration = dict[str, Any]  # as saved in calibration.json, see board.apply_calibration()

REFERENCE_ROW_STEP = board.ROW_STEP
# Center of a barrel / empty-slot silhouette below its row's top, measured on the 1220x2712 screen
BLOB_CENTER_Y = 158
BACKGROUND_TOLERANCE = 40  # summed BGR difference that still counts as board background
MIN_BRIGHTNESS = 100  # mean background brightness; darker = magnet storm, no good for calibrating
MAX_STEP_DIFFERENCE = 0.15  # columns / rows may be this much (fraction of the step) unevenly spaced
PREVIEW_HEIGHT = 900  # px, window for clicking the barrels


def board_background(frame: Frame) -> Color:
    """Most common color in the middle of the screen: the board between the barrels."""
    height, width = frame.shape[:2]
    middle = frame[height * 3 // 10:height * 7 // 10, width // 10:width * 9 // 10].reshape(-1, 3)
    bins = (middle // 8).astype(int)
    keys = bins[:, 0] * 1024 + bins[:, 1] * 32 + bins[:, 2]
    return np.asarray(middle[keys == np.bincount(keys).argmax()].mean(axis=0), dtype=np.float64)


def split_at_gaps(values: Sequence[float], count: int) -> list[list[float]]:
    """Splits the values into count groups at the largest gaps between them."""
    ordered = sorted(values)
    cuts = sorted(int(cut) + 1 for cut in np.argsort(np.diff(ordered))[len(ordered) - count:])
    return [ordered[start:end] for start, end in zip([0] + cuts, cuts + [len(ordered)])]


def evenly_spaced_centers(values: Sequence[float], count: int, what: str) -> list[float]:
    """Median of each of the count groups; raises when they don't form an even grid."""
    if len(values) < count:
        raise ValueError('found only %d barrels/slots, need at least one per %s' % (len(values), what))
    groups = split_at_gaps(values, count)
    centers = [float(np.median(group)) for group in groups]
    steps = np.diff(centers)
    if (np.abs(steps - steps.mean()) > steps.mean() * MAX_STEP_DIFFERENCE).any() \
            or any(max(group) - min(group) > steps.mean() * MAX_STEP_DIFFERENCE * 2 for group in groups):
        raise ValueError('the %ss are not evenly spaced: %s' % (what, [round(center) for center in centers]))
    return centers


def detect(frame: Frame, background: Color) -> Calibration:
    """Finds the 4x5 grid: every slot holds a barrel or a gray silhouette on the plain board."""
    height, width = frame.shape[:2]
    is_background = (np.abs(frame.astype(int) - background).sum(axis=2) < BACKGROUND_TOLERANCE).astype(np.uint8)
    _, labels, stats, _ = cv2.connectedComponentsWithStats(is_background, connectivity=8)
    board_label = 1 + stats[1:, cv2.CC_STAT_AREA].argmax()
    left, top, board_width, board_height = stats[board_label][:4]

    things = np.zeros_like(is_background)
    things[top:top + board_height, left:left + board_width] = \
        labels[top:top + board_height, left:left + board_width] != board_label
    # Removes the thin "+9.089 OD" labels above the barrels
    kernel = max(3, width // 60)
    opened = cv2.morphologyEx(things, cv2.MORPH_OPEN, np.ones((kernel, kernel), np.uint8))
    count, _, blobs, _ = cv2.connectedComponentsWithStats(opened, connectivity=8)
    centers: list[tuple[float, float]] = []
    for x, y, blob_width, blob_height, area in blobs[1:count]:
        # Skip small things and the side menu tab at the screen edge
        if area < (board_width / 12) ** 2 or x <= left + 2 or x + blob_width >= left + board_width - 2:
            continue
        centers.append((x + blob_width / 2, y + blob_height / 2))
    if not centers:
        raise ValueError('no barrels found on the board')

    columns_x = evenly_spaced_centers([x for x, _ in centers], len(board.COLUMNS_X), 'column')
    rows_y = evenly_spaced_centers([y for _, y in centers], board.ROWS, 'row')
    # Straight line through the row centers
    row_step, first_row_center = np.polyfit(range(board.ROWS), rows_y, 1)
    return calibration(frame, background, [round(x) for x in columns_x], float(first_row_center), float(row_step))


def calibration(frame: Frame, background: Color, columns_x: list[int], first_row_center: float,
                row_step: float) -> Calibration:
    return {
        'screen_size': [frame.shape[1], frame.shape[0]],
        'columns_x': columns_x,
        'first_row_top': round(first_row_center - BLOB_CENTER_Y * row_step / REFERENCE_ROW_STEP),
        'row_step': round(row_step),
        'background_bgr': [round(value) for value in background],
    }


def pick_corners(frame: Frame) -> list[tuple[float, float]] | None:
    """Lets the user click the centers of the top-left and the bottom-right barrel; None when cancelled."""
    scale = min(1.0, PREVIEW_HEIGHT / frame.shape[0])
    shown = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    clicks: list[tuple[float, float]] = []

    def on_mouse(event: int, x: int, y: int, flags: int, param: Any) -> None:
        if event == cv2.EVENT_LBUTTONDOWN and len(clicks) < 2:
            clicks.append((x / scale, y / scale))

    title = 'Click the center of the top-left barrel, then the bottom-right one (Esc cancels)'
    cv2.namedWindow(title)
    cv2.setMouseCallback(title, on_mouse)
    while len(clicks) < 2:
        image = shown.copy()
        for x, y in clicks:
            cv2.circle(image, (round(x * scale), round(y * scale)), 6, (0, 0, 255), -1)
        cv2.imshow(title, image)
        if cv2.waitKey(30) == 27:
            break
    cv2.destroyWindow(title)
    cv2.waitKey(1)
    return clicks if len(clicks) == 2 else None


def manual(frame: Frame, background: Color) -> Calibration | None:
    corners = pick_corners(frame)
    if corners is None:
        return None
    (left, top), (right, bottom) = corners
    columns = len(board.COLUMNS_X)
    columns_x = [round(left + (right - left) * column / (columns - 1)) for column in range(columns)]
    return calibration(frame, background, columns_x, top, (bottom - top) / (board.ROWS - 1))


def check(frame: Frame, candidate: Calibration, path: str) -> bool:
    """Applies the candidate, saves a preview of what the bots will look at and prints how it went."""
    importlib.reload(board)  # back to the 1220x2712 values, which apply_calibration() starts from
    board.apply_calibration(candidate)
    image = frame.copy()
    thickness = max(2, frame.shape[1] // 300)
    current = board.signatures(frame)
    for slot, signature in enumerate(current):
        row, column = divmod(slot, len(board.COLUMNS_X))
        x, top = board.COLUMNS_X[column], board.FIRST_ROW_TOP + board.ROW_STEP * row
        color = (128, 128, 128) if signature is None else (0, 0, 255)
        cv2.rectangle(image, (x - board.SAMPLE_HALF_WIDTH, top + board.SAMPLE_TOP),
                      (x + board.SAMPLE_HALF_WIDTH, top + board.SAMPLE_BOTTOM), color, thickness)
        cv2.circle(image, board.slot_center(slot), thickness * 3, (255, 0, 255), -1)
        for gap_x in board.GAP_X:
            gap = (gap_x, top + board.TOUCH_OFFSET_Y)
            matches = np.abs(frame[gap[1], gap[0]].astype(int) - board.BACKGROUNDS_BGR['normal']).sum() < 30
            cv2.circle(image, gap, thickness * 3, (0, 200, 0) if matches else (0, 0, 255), thickness)
    for name, point in [('Yes', board.CONVERT_YES), ('No', board.CONVERT_NO_CHECK)]:
        cv2.drawMarker(image, point, (255, 0, 0), cv2.MARKER_CROSS, thickness * 12, thickness)
        cv2.putText(image, name, (point[0] + thickness * 8, point[1]), cv2.FONT_HERSHEY_SIMPLEX,
                    thickness / 2, (255, 0, 0), thickness)
    for box in (board.SCRAP_BOX, board.MAGNETS_BOX, board.STORM_BOX, board.CROWN_BOX):
        cv2.rectangle(image, box[:2], box[2:], (255, 0, 0), thickness)
    cv2.imwrite(path, image)

    print(json.dumps(candidate))
    reading = hud.read(frame)
    print('texts at the top (blue boxes): scrap %r, magnets %s, next storm in %s s, level %s; boost square: %s'
          % (reading.scrap_text, reading.magnets, reading.next_storm, reading.level,
             board.crown_state(frame) or 'none'))
    if not board.hud_visible(frame):
        print('the texts at the top look covered (their boxes should be dark green)')
    print('board seen as: %s' % (board.grid_background(frame) or 'NOT FOUND (the gap points miss the background)'))
    print('slots: %d with a barrel, %d empty' % (sum(s is not None for s in current), sum(s is None for s in current)))
    print('preview: %s (red/gray boxes = sampled part of each slot, should sit on the barrel bodies;'
          ' green circles = background between the columns; blue = where the "Convert Barrel?" buttons'
          ' are expected, blue boxes = texts read with OCR and the boost crown)' % os.path.abspath(path))
    return board.grid_background(frame) is not None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--image', help='calibrate from this screenshot instead of the phone')
    parser.add_argument('--manual', action='store_true', help='click the barrels instead of detecting the grid')
    parser.add_argument('--yes', action='store_true', help='save without asking (when the check passes)')
    parser.add_argument('--preview', default='calibration.png', help='where to save the preview image')
    parser.add_argument('--output', default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                         'calibration.json'))
    args = parser.parse_args()

    frame: Frame | None = cv2.imread(args.image) if args.image else screenshot()
    if frame is None:
        sys.exit('could not read %s' % args.image)
    background = board_background(frame)
    if background.mean() < MIN_BRIGHTNESS:
        sys.exit('the board looks dark: a magnet storm or a popup? Calibrate on the plain board')

    candidate: Calibration | None = None
    if not args.manual:
        try:
            candidate = detect(frame, background)
        except ValueError as error:
            print('grid not detected: %s' % error)
    while True:
        if candidate is None:
            print('click the barrel centers in the window')
            candidate = manual(frame, background)
            if candidate is None:
                sys.exit('cancelled, nothing saved')
        passed = check(frame, candidate, args.preview)
        if args.yes:
            answer = 'y' if passed else 'n'
        else:
            answer = input('save to %s? [y]es / [m]anual / [n]o: ' % args.output).strip().lower()[:1]
        if answer == 'y':
            with open(args.output, 'w') as file:
                json.dump(candidate, file, indent=2)
                file.write('\n')
            print('saved %s' % args.output)
            return
        if answer != 'm':
            sys.exit('nothing saved')
        candidate = None


if __name__ == '__main__':
    main()
