"""Touch gestures and game actions: drags, taps, converting barrels, recovering the game."""
from __future__ import annotations

import random
import subprocess
import time
from collections.abc import Callable, Iterable
from typing import TypedDict

from src import board
from src.board import Frame, Point, Size
from src.phone import GAME_PACKAGE, Phone, game_in_front

MAX_BEND = 0.15  # sideways bend of the drag path, as a fraction of its length



class Timing(TypedDict):
    """Timing of drags and rounds: random (min, max) ranges in ms unless noted."""
    swipe_ms: tuple[float, float]
    hold_ms: tuple[int, int]
    drop_ms: tuple[int, int]
    gap_ms: tuple[int, int]
    steps: tuple[int, int]
    pause: tuple[float, float]  # seconds
    settle: float  # seconds
    tap_ms: tuple[int, int]
    double_tap_gap_ms: tuple[int, int]
    yes_delay_ms: tuple[int, int]
    after_yes_ms: tuple[int, int]


# --fast is meant for spawn boosts (e.g. the 10x ad boost)
NORMAL_TIMING: Timing = {
    'swipe_ms': (150, 300),  # duration of one drag
    'hold_ms': (30, 90),  # finger down before moving
    'drop_ms': (20, 50),  # finger resting on the target before it lifts
    'gap_ms': (30, 120),  # between drags
    'steps': (8, 14),  # moves along the curve
    'pause': (0.1, 0.4),  # seconds between rounds
    'settle': 0.25,  # seconds the game gets to show the result of the drags
    'tap_ms': (40, 80),  # finger down during a tap
    'double_tap_gap_ms': (70, 110),
    'yes_delay_ms': (90, 130),  # double tap -> Yes, for converting without looking (star_farm.py --fast)
    'after_yes_ms': (40, 80),  # Yes -> next double tap, likewise
}
FAST_TIMING: Timing = {
    # Measured with the 10x boost (merges/s, drags the game missed): 12-22 ms drags 6.8/s, 24%;
    # 25-40 ms 6.6/s, 6%; 30-50 ms 5.9/s, 2%; 40-80 ms 5.0/s, 1%. Most misses were long drags
    # upwards; since the drags ease out (see drag_commands()) none of 836 missed. The game follows
    # only one dragged barrel at a time, so drags can't overlap (multi-touch)
    'swipe_ms': (30, 50),
    'hold_ms': (12, 25),
    'drop_ms': (20, 35),
    'gap_ms': (3, 10),
    'steps': (4, 6),
    'pause': (0.0, 0.05),
    # Barrels still animating are skipped anyway (they differ from the previous frame)
    'settle': 0.04,
    # Measured with the 10x spawn boost: Yes sooner than ~70 ms after the double tap
    # starts to get lost, the rest can be this short (~240 ms per barrel)
    'tap_ms': (12, 25),
    'double_tap_gap_ms': (35, 55),
    'yes_delay_ms': (75, 100),
    'after_yes_ms': (10, 30),
}

HIDDEN_WAIT_SECONDS = 0.5
STATUS_SECONDS = 15  # how often to say what's going on while waiting
CONVERT_DIALOG_TIMEOUT = 0.8  # seconds to wait for the dialog to open / close
CONVERT_DIALOG_FADE_SECONDS = 0.15


def drag_commands(start: Point, end: Point, timing: Timing) -> list[str]:
    """Injector commands for a drag along a quadratic Bezier curve."""
    (x1, y1), (x2, y2) = start, end
    length = max(1.0, ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5)
    # Control point: the midpoint pushed sideways (perpendicular to the drag)
    bend = random.uniform(-MAX_BEND, MAX_BEND) * length
    control_x = (x1 + x2) / 2 - (y2 - y1) / length * bend
    control_y = (y1 + y2) / 2 + (x2 - x1) / length * bend

    duration_ms = random.uniform(*timing['swipe_ms'])
    steps = random.randint(*timing['steps'])
    commands = ['d %d %d' % start, 's %d' % random.randint(*timing['hold_ms'])]
    for step in range(1, steps + 1):
        # Ease out: fast at first, slowing down into the target. The game draws the dragged barrel
        # trailing the finger and drops it where the barrel is, so a drag that ends at full speed
        # (long fast drags upwards) drops it short of the target, between the rows
        t = 1 - (1 - step / steps) ** 2
        x = (1 - t) ** 2 * x1 + 2 * (1 - t) * t * control_x + t ** 2 * x2
        y = (1 - t) ** 2 * y1 + 2 * (1 - t) * t * control_y + t ** 2 * y2
        commands += ['m %d %d' % (x, y), 's %d' % (duration_ms / steps)]
    commands += ['s %d' % random.randint(*timing['drop_ms']), 'u %d %d' % end]
    return commands


def slot_drag(source: int, target: int, timing: Timing) -> list[str]:
    """Drag from a random point on the source barrel onto the target barrel, then a short gap."""
    return drag_commands(board.random_point(source, board.START_SPREAD),
                         board.random_point(target, board.END_SPREAD), timing) \
        + ['s %d' % random.randint(*timing['gap_ms'])]


def tap(point: Point, spread: Size, timing: Timing) -> list[str]:
    x, y = point[0] + random.randint(-spread[0], spread[0]), point[1] + random.randint(-spread[1], spread[1])
    return ['d %d %d' % (x, y), 's %d' % random.randint(*timing['tap_ms']), 'u %d %d' % (x, y)]


def double_tap(slot: int, timing: Timing) -> list[str]:
    point = board.random_point(slot, (25, 35))
    return tap(point, (4, 4), timing) + ['s %d' % random.randint(*timing['double_tap_gap_ms'])] \
        + tap(point, (4, 4), timing)


def wait_for(phone: Phone, condition: Callable[[Frame], bool], timeout: float) -> Frame:
    """Grabs frames until one meets the condition or the timeout passes; returns the last frame."""
    deadline = time.time() + timeout
    frame = phone.grab()
    while not condition(frame) and time.time() < deadline:
        time.sleep(0.03)
        frame = phone.grab()
    return frame


def wait_for_grid(phone: Phone) -> Frame:
    """Waits for a dialog to fade out; returns the last frame."""
    return wait_for(phone, board.grid_visible, CONVERT_DIALOG_TIMEOUT)


def convert(phone: Phone, slot: int, timing: Timing) -> bool:
    """Converts the barrel in the slot into Star Fragments; False when the dialog did not open."""
    phone.run(double_tap(slot, timing))
    if not board.convert_dialog_open(wait_for(phone, board.convert_dialog_open, CONVERT_DIALOG_TIMEOUT)):
        return False
    # The dialog fades in and only takes taps once it is fully there
    time.sleep(CONVERT_DIALOG_FADE_SECONDS)
    for _ in range(3):
        phone.run(tap(board.CONVERT_YES, board.CONVERT_YES_SPREAD, timing))
        # Until the dialog has fully faded out, it still covers the board
        if board.grid_visible(wait_for_grid(phone)):
            return True
    return False


def finish_conversion(phone: Phone, timing: Timing) -> None:
    """Taps Yes of a "Convert Barrel?" dialog that is still open."""
    phone.run(tap(board.CONVERT_YES, board.CONVERT_YES_SPREAD, timing))
    wait_for_grid(phone)


def convert_blind(phone: Phone, slots: Iterable[int], timing: Timing) -> None:
    """Converts the barrels without looking at the screen in between: much faster than convert().

    Safe because every tap is harmless when things go differently: Yes without
    the dialog is a single tap on a barrel, and a double tap landing on a dialog
    that is still open hits nothing but its No / X.
    """
    commands = []
    for slot in slots:
        commands += double_tap(slot, timing) + ['s %d' % random.randint(*timing['yes_delay_ms'])]
        commands += tap(board.CONVERT_YES, board.CONVERT_YES_SPREAD, timing) \
            + ['s %d' % random.randint(*timing['after_yes_ms'])]
    phone.run(commands)


def convert_all(phone: Phone, timing: Timing, slots: Iterable[int] | None = None) -> int:
    """Converts every barrel on the board (or in the given slots), checking that the dialog really opened each time.

    Returns how many barrels were converted.
    """
    converted = misses = 0
    for slot in slots if slots is not None else range(board.slot_count()):
        if misses >= 3:
            print('the "Convert Barrel?" dialog did not open, stopped converting')
            break
        if board.signatures(phone.grab())[slot] is None:
            continue
        if convert(phone, slot, timing):
            converted += 1
            misses = 0
        else:
            misses += 1
    print('%s converted %d barrels' % (time.strftime('%H:%M:%S'), converted))
    return converted


def recover(phone: Phone) -> None:
    """Pauses and resumes the game through the recent apps screen.

    After a while the game sometimes starts ignoring drags (taps still work);
    pausing and resuming it fixes that. Unlike going home and relaunching it,
    this never restarts the game (MIUI sometimes killed it on the way, and the
    fresh start could then crash).
    """
    subprocess.run(['adb', 'shell', 'input keyevent APP_SWITCH; sleep 1.2; input keyevent APP_SWITCH'], check=True)
    time.sleep(2)
    if not game_in_front():
        # Bring the existing game task back, as the launcher icon would
        subprocess.run(['adb', 'shell', 'monkey -p %s -c android.intent.category.LAUNCHER 1' % GAME_PACKAGE],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
    if game_in_front() and not board.grid_visible(phone.grab()):
        x, y = board.OFFLINE_POPUP_OK
        phone.run(['d %d %d' % (x + random.randint(-40, 40), y + random.randint(-20, 20)),
                   's %d' % random.randint(40, 90), 'u %d %d' % (x, y)])
        time.sleep(1)
