#!/usr/bin/env python3
"""Simple Scrap Clicker 2 merger: drags together barrels that look the same.

Looks at the phone's screen through a small helper running on the phone
(see injector/), cuts a sample out of each of the 20 grid slots, groups slots
whose samples look alike and drags them onto each other through the same
helper. Drags start and end at random points on the barrels,
follow a slight curve and have random timing. Calibrated for a 1220x2712 screen.
"""
import argparse
import ctypes
import os
import random
import select
import signal
import subprocess
import sys
import time

import cv2
import numpy as np

SCREEN_SIZE = (1220, 2712)
FRAME_SIZE = (305, 678)  # screen is grabbed at quarter size, plenty for telling barrels apart

# Grid geometry in screen pixels
COLUMNS_X = [189, 473, 755, 1039]
FIRST_ROW_TOP = 441
ROW_STEP = 383
ROWS = 5

# Part of the barrel body that gets sampled, relative to the slot's column
# center / row top. Skips the "+1.397 OD" label at the top of the slot.
SAMPLE_TOP = 120
SAMPLE_BOTTOM = 270
SAMPLE_HALF_WIDTH = 55
TOUCH_OFFSET_Y = 190

# Max random offset (x, y) from the barrel center where a drag starts / ends
START_SPREAD = (40, 60)
END_SPREAD = (20, 30)
MAX_BEND = 0.15  # sideways bend of the drag path, as a fraction of its length

# Background between the barrels: normal, and darkened during a magnet storm (no merging then)
BACKGROUNDS_BGR = {'normal': np.array([173, 208, 171]), 'storm': np.array([52, 62, 50])}
GAP_X = [331, 614, 897]  # background between columns, used to check the grid is visible
GAP_MAX_COVERED = 5  # gap points that may be covered (falling storm magnets) with the grid still visible
HIDDEN_WAIT_SECONDS = 0.5
STORM_CHECK_SECONDS = 1  # how often to check whether a magnet storm (no merging possible) is over

EMPTY_MAX_STD = 6  # empty slot = flat gray silhouette or plain background
SIGNATURE_SIZE = (6, 8)  # (width, height) of the downscaled sample
BLOCK_SECONDS = 60

# Timing of drags and rounds. --fast is meant for spawn boosts (e.g. the 10x ad boost)
NORMAL_TIMING = {
    'swipe_ms': (150, 300),  # duration of one drag
    'hold_ms': (30, 90),  # finger down before moving
    'gap_ms': (30, 120),  # between drags
    'steps': (8, 14),  # moves along the curve
    'pause': (0.1, 0.4),  # seconds between rounds
    'settle': 0.25,  # seconds the game gets to show the result of the drags
    'tap_ms': (40, 80),  # finger down during a tap
    'double_tap_gap_ms': (70, 110),
    'yes_delay_ms': (90, 130),  # double tap -> Yes, for converting without looking (star_farm.py --fast)
    'after_yes_ms': (40, 80),  # Yes -> next double tap, likewise
}
FAST_TIMING = {
    'swipe_ms': (40, 80),
    'hold_ms': (15, 40),
    'gap_ms': (15, 50),
    'steps': (5, 8),
    'pause': (0.0, 0.05),
    'settle': 0.12,
    # Measured with the 10x spawn boost: Yes sooner than ~70 ms after the double tap
    # starts to get lost, the rest can be this short (~240 ms per barrel)
    'tap_ms': (12, 25),
    'double_tap_gap_ms': (35, 55),
    'yes_delay_ms': (75, 100),
    'after_yes_ms': (10, 30),
}
REMEMBERED_TYPES = 50
STATUS_SECONDS = 15  # how often to say what's going on when there is nothing to merge

GAME_PACKAGE = 'com.scrap.clicker.android'

# --delete-overflow: when the board is full and nothing can be merged for this
# many checks in a row, every barrel gets converted (into Star Fragments).
# Converting = double tap on the barrel, then "Yes" in the "Convert Barrel?" dialog
OVERFLOW_CHECKS = 10
OVERFLOW_MIN_SECONDS = 3  # and for at least this long (checks are quick with --fast)
CONVERT_DIALOG_BODY = (610, 1464)  # plain gray part of the dialog
# Where the Yes button overlaps the barrel in row 3, column 2: without the dialog a
# tap there is just a harmless single tap on that barrel
# (Higher up, taps hit the barrel's "+1.38 ND" label instead of the barrel.)
CONVERT_YES = (730, 1728)
CONVERT_YES_SPREAD = (25, 12)
CONVERT_YES_CHECK = (690, 1700)  # plain green part of the Yes button (the label is white)
CONVERT_NO_CHECK = (210, 1700)  # plain red part of the No button
CONVERT_DIALOG_TIMEOUT = 0.8  # seconds to wait for the dialog to open / close
CONVERT_DIALOG_FADE_SECONDS = 0.15
OFFLINE_POPUP_OK = (610, 1770)  # "Ok" of the "Offline Production" popup shown after resuming

INJECTOR_DEX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'injector', 'injector.dex')
INJECTOR_ON_PHONE = '/data/local/tmp/injector.dex'
PR_SET_PDEATHSIG = 1
HELPER_START_SECONDS = 15


def die_with_parent():
    # Linux: end this child (and with it the process on the phone) when the bot
    # dies in any way, so nothing keeps running on the phone
    ctypes.CDLL(None).prctl(PR_SET_PDEATHSIG, signal.SIGTERM)


class Phone:
    """Touch injector and screen grabber running on the phone, see injector/Injector.java."""

    def __init__(self):
        if not os.path.exists(INJECTOR_DEX):
            subprocess.run([os.path.join(os.path.dirname(INJECTOR_DEX), 'build.sh')], check=True)
        subprocess.run(['adb', 'push', INJECTOR_DEX, INJECTOR_ON_PHONE], stdout=subprocess.DEVNULL, check=True)
        self.process = subprocess.Popen(
            # Ignoring SIGHUP lets the helper notice a lost connection as end of input
            # and clean up (screen back on, settings restored) instead of being killed
            ['adb', 'shell', "trap '' HUP; CLASSPATH=%s exec app_process / Injector" % INJECTOR_ON_PHONE],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            preexec_fn=die_with_parent if sys.platform.startswith('linux') else None
        )
        if not select.select([self.process.stdout], [], [], HELPER_START_SECONDS)[0]:
            self.process.kill()
            raise RuntimeError('helper on the phone did not start within %ds (is the phone connected '
                               'and unlocked? try again)' % HELPER_START_SECONDS)
        ready = self.process.stdout.readline().split()
        if not ready or ready[0] != b'ready':
            raise RuntimeError('helper on the phone did not start')
        self.touch_device = int(ready[1])

    def run(self, commands):
        """Sends the commands and waits until all of them were injected."""
        self.process.stdin.write(('\n'.join(commands + ['sync']) + '\n').encode())
        self.process.stdin.flush()
        self.process.stdout.readline()

    def grab(self):
        """Returns the current screen, scaled up to SCREEN_SIZE."""
        self.process.stdin.write(b'frame %d %d\n' % FRAME_SIZE)
        self.process.stdin.flush()
        width, height, stride = map(int, self.process.stdout.readline().split()[1:])
        if not width:
            raise RuntimeError('no picture from the phone')
        data = self.process.stdout.read(stride * height)
        rgba = np.frombuffer(data, np.uint8).reshape(height, stride // 4, 4)[:, :width]
        return cv2.resize(cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR), SCREEN_SIZE, interpolation=cv2.INTER_LINEAR)

    def close(self):
        try:
            self.process.stdin.close()
        except OSError:
            pass
        self.process.wait()


class Stats:
    """Counts what a script did, for the summary printed when it stops."""

    def __init__(self):
        self.start = time.time()
        self.counts = {}

    def add(self, name, amount=1):
        self.counts[name] = self.counts.get(name, 0) + amount

    def print_summary(self, names, approximate=()):
        elapsed = max(time.time() - self.start, 1e-6)
        hours, rest = divmod(int(elapsed), 3600)
        print('\n--- stats after %dh %02dm %02ds ---' % (hours, rest // 60, rest % 60))
        for name in names:
            total = self.counts.get(name, 0)
            mark = '~' if name in approximate else ''
            print('%s: %s%d  (%s%.0f/h, %s%.1f/min, %s%.2f/s)'
                  % (name, mark, round(total), mark, total / elapsed * 3600, mark, total / elapsed * 60,
                     mark, total / elapsed))


def slot_center(slot):
    row, column = divmod(slot, len(COLUMNS_X))
    return COLUMNS_X[column], FIRST_ROW_TOP + ROW_STEP * row + TOUCH_OFFSET_Y


def slot_sample(frame, slot):
    row, column = divmod(slot, len(COLUMNS_X))
    x = COLUMNS_X[column]
    top = FIRST_ROW_TOP + ROW_STEP * row
    return frame[top + SAMPLE_TOP:top + SAMPLE_BOTTOM, x - SAMPLE_HALF_WIDTH:x + SAMPLE_HALF_WIDTH]


def grid_background(frame):
    """Returns 'normal' or 'storm' depending on the board's background, None when the grid is covered.

    When a popup/ad/menu covers the grid the gaps between barrels stop being background.
    """
    gaps = np.array([frame[FIRST_ROW_TOP + ROW_STEP * row + TOUCH_OFFSET_Y, x]
                     for row in range(ROWS) for x in GAP_X], dtype=int)
    for name, color in BACKGROUNDS_BGR.items():
        matches = (np.abs(gaps - color).sum(axis=1) < 30).sum()
        if matches >= len(gaps) - GAP_MAX_COVERED:
            return name
    return None


def grid_visible(frame):
    return grid_background(frame) is not None


def signatures(frame):
    """Returns a downscaled sample per slot, or None for empty slots."""
    result = []
    for slot in range(len(COLUMNS_X) * ROWS):
        sample = slot_sample(frame, slot)
        if sample.std() < EMPTY_MAX_STD:
            result.append(None)
        else:
            result.append(cv2.resize(sample, SIGNATURE_SIZE, interpolation=cv2.INTER_AREA).astype(float))
    return result


def difference(a, b):
    return np.abs(a - b).mean()


def is_known(signature, known, threshold):
    return any(difference(signature, other) < threshold for other in known)


def find_pairs(current, previous, threshold, skip):
    # Only use slots that look the same as in the previous frame, so barrels
    # that are still animating (dropping in, being merged) are skipped
    stable = [
        slot for slot, signature in enumerate(current)
        if signature is not None and previous[slot] is not None
        and difference(signature, previous[slot]) < threshold
        and not is_known(signature, skip, threshold)
    ]

    groups = []
    for slot in stable:
        for group in groups:
            if difference(current[slot], current[group[0]]) < threshold:
                group.append(slot)
                break
        else:
            groups.append([slot])

    pairs = []
    for group in groups:
        for index in range(0, len(group) - 1, 2):
            pairs.append((group[index], group[index + 1]))
    return groups, pairs


def random_point(slot, spread):
    x, y = slot_center(slot)
    return x + random.randint(-spread[0], spread[0]), y + random.randint(-spread[1], spread[1])


def drag_commands(start, end, timing):
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
        t = step / steps
        x = (1 - t) ** 2 * x1 + 2 * (1 - t) * t * control_x + t ** 2 * x2
        y = (1 - t) ** 2 * y1 + 2 * (1 - t) * t * control_y + t ** 2 * y2
        commands += ['m %d %d' % (x, y), 's %d' % (duration_ms / steps)]
    commands.append('u %d %d' % end)
    return commands


def merge(phone, pairs, timing):
    pairs = list(pairs)
    random.shuffle(pairs)
    commands = []
    for target, source in pairs:
        commands += drag_commands(random_point(source, START_SPREAD), random_point(target, END_SPREAD), timing)
        commands.append('s %d' % random.randint(*timing['gap_ms']))
    phone.run(commands)


def still_unmerged(phone, attempts, threshold):
    """Checks a fresh frame a bit later whether all attempted pairs are still unmerged."""
    time.sleep(0.7)
    current = signatures(phone.grab())
    for (target, source), (old_target, old_source) in attempts.items():
        if current[target] is None or current[source] is None \
                or difference(current[target], old_target) >= threshold \
                or difference(current[source], old_source) >= threshold:
            return False
    return True


def game_in_front():
    focus = subprocess.run(['adb', 'shell', 'dumpsys window | grep mCurrentFocus'],
                           capture_output=True, text=True).stdout
    return GAME_PACKAGE in focus


def recover(phone):
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
    if game_in_front() and not grid_visible(phone.grab()):
        x, y = OFFLINE_POPUP_OK
        phone.run(['d %d %d' % (x + random.randint(-40, 40), y + random.randint(-20, 20)),
                   's %d' % random.randint(40, 90), 'u %d %d' % (x, y)])
        time.sleep(1)


def convert_dialog_open(frame):
    def color(point):
        x, y = point
        return frame[y - 10:y + 10, x - 10:x + 10].reshape(-1, 3).mean(axis=0)
    body, yes, no = color(CONVERT_DIALOG_BODY), color(CONVERT_YES_CHECK), color(CONVERT_NO_CHECK)
    return (np.abs(body - 190) < 20).all() and yes[1] > yes[2] + 60 and no[2] > no[1] + 80


def tap(point, spread, timing):
    x, y = point[0] + random.randint(-spread[0], spread[0]), point[1] + random.randint(-spread[1], spread[1])
    return ['d %d %d' % (x, y), 's %d' % random.randint(*timing['tap_ms']), 'u %d %d' % (x, y)]


def wait_for(phone, condition, timeout):
    """Grabs frames until one meets the condition or the timeout passes; returns the last frame."""
    deadline = time.time() + timeout
    frame = phone.grab()
    while not condition(frame) and time.time() < deadline:
        time.sleep(0.03)
        frame = phone.grab()
    return frame


def convert(phone, slot, timing):
    """Converts the barrel in the slot into Star Fragments; False when the dialog did not open."""
    phone.run(double_tap(slot, timing))
    if not convert_dialog_open(wait_for(phone, convert_dialog_open, CONVERT_DIALOG_TIMEOUT)):
        return False
    # The dialog fades in and only takes taps once it is fully there
    time.sleep(CONVERT_DIALOG_FADE_SECONDS)
    for _ in range(3):
        phone.run(tap(CONVERT_YES, CONVERT_YES_SPREAD, timing))
        # Until the dialog has fully faded out, it still covers the board
        if grid_background(wait_for(phone, lambda frame: grid_background(frame) is not None,
                                    CONVERT_DIALOG_TIMEOUT)) is not None:
            return True
    return False


def double_tap(slot, timing):
    point = random_point(slot, (25, 35))
    return tap(point, (4, 4), timing) + ['s %d' % random.randint(*timing['double_tap_gap_ms'])] \
        + tap(point, (4, 4), timing)


def convert_blind(phone, slots, timing):
    """Converts the barrels without looking at the screen in between: much faster than convert().

    Safe because every tap is harmless when things go differently: Yes without
    the dialog is a single tap on a barrel, and a double tap landing on a dialog
    that is still open hits nothing but its No / X.
    """
    commands = []
    for slot in slots:
        commands += double_tap(slot, timing) + ['s %d' % random.randint(*timing['yes_delay_ms'])]
        commands += tap(CONVERT_YES, CONVERT_YES_SPREAD, timing) + ['s %d' % random.randint(*timing['after_yes_ms'])]
    phone.run(commands)


def convert_all(phone, timing, slots=None):
    """Converts every barrel on the board (or in the given slots), checking that the dialog really opened each time.

    Returns how many barrels were converted.
    """
    converted = misses = 0
    for slot in slots if slots is not None else range(len(COLUMNS_X) * ROWS):
        if misses >= 3:
            print('the "Convert Barrel?" dialog did not open, stopped converting')
            break
        if signatures(phone.grab())[slot] is None:
            continue
        if convert(phone, slot, timing):
            converted += 1
            misses = 0
        else:
            misses += 1
    print('%s converted %d barrels' % (time.strftime('%H:%M:%S'), converted))
    return converted


def save_debug_image(frame, current, groups, path):
    image = frame.copy()
    group_of = {slot: index for index, group in enumerate(groups) for slot in group}
    for slot, signature in enumerate(current):
        row, column = divmod(slot, len(COLUMNS_X))
        x = COLUMNS_X[column]
        top = FIRST_ROW_TOP + ROW_STEP * row
        color = (128, 128, 128) if signature is None else (0, 0, 255)
        cv2.rectangle(image, (x - SAMPLE_HALF_WIDTH, top + SAMPLE_TOP),
                      (x + SAMPLE_HALF_WIDTH, top + SAMPLE_BOTTOM), color, 4)
        label = 'empty' if signature is None else 'g%s' % group_of.get(slot, '?')
        cv2.putText(image, '%d:%s' % (slot, label), (x - 90, top + SAMPLE_BOTTOM + 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.3, (0, 0, 0), 4)
    cv2.imwrite(path, image)


def time_range(text):
    """Parses "min-max" (or a single number) into a (min, max) tuple."""
    low, _, high = text.partition('-')
    return float(low), float(high or low)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dry-run', action='store_true', help='only print what would be merged')
    parser.add_argument('--once', action='store_true', help='do a single round and exit')
    parser.add_argument('--threshold', type=float, default=10, help='max mean pixel difference for "same barrel"')
    parser.add_argument('--fast', action='store_true',
                        help='merge as fast as possible, for spawn boosts like the 10x ad boost')
    parser.add_argument('--swipe-ms', type=time_range,
                        help='random duration range of one drag in ms (default 150-300, with --fast 40-80)')
    parser.add_argument('--pause', type=time_range,
                        help='random range of seconds to wait between rounds (default 0.1-0.4, with --fast 0-0.05)')
    parser.add_argument('--delete-overflow', action='store_true',
                        help='when the board is full and nothing can be merged (%d checks in a row), '
                             'convert all barrels into Star Fragments to make room' % OVERFLOW_CHECKS)
    parser.add_argument('--debug-image', help='save the annotated frame of each round here')
    parser.add_argument('--screen-off', action='store_true',
                        help='switch the display panel off while the bot runs (the game keeps running '
                             'and can still be seen by the bot); it comes back on when the bot stops')
    args = parser.parse_args()
    args.timing = dict(FAST_TIMING if args.fast else NORMAL_TIMING)
    if args.swipe_ms:
        args.timing['swipe_ms'] = args.swipe_ms
    if args.pause:
        args.timing['pause'] = args.pause

    phone = Phone()
    if phone.touch_device < 0:
        print('touchscreen not found, touches are sent from a virtual device')

    stay_on = switch_screen_off(phone) if args.screen_off and not args.dry_run else None
    stats = Stats()
    try:
        run(args, phone, stats)
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)  # a second Ctrl+C must not cut the cleanup short
        if stay_on is not None:
            switch_screen_on(phone, stay_on)
        phone.close()
        stats.print_summary(['merges'] + (['conversions'] if 'conversions' in stats.counts else []))


def switch_screen_off(phone):
    """Switches the display panel off; returns the stay-awake setting to restore afterwards."""
    # Android doesn't know the panel is off, so stop it from going to sleep
    # at the screen timeout (which would pause the game) while charging over USB
    stay_on = subprocess.run(['adb', 'shell', 'settings get global stay_on_while_plugged_in'],
                             capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(['adb', 'shell', 'settings put global stay_on_while_plugged_in 7'], check=True)
    # The helper on the phone restores it even when the bot gets killed
    phone.run(['atexit settings put global stay_on_while_plugged_in %s' % stay_on, 'screen 0'])
    print('display switched off, press Ctrl+C to stop the bot and switch it back on')
    return stay_on


def switch_screen_on(phone, stay_on):
    try:
        phone.run(['screen 1'])
    except OSError:
        pass  # the helper died with Ctrl+C; it switches the screen back on itself when it exits
    subprocess.run(['adb', 'shell', 'settings put global stay_on_while_plugged_in %s' % stay_on])


def run(args, phone, stats):
    previous_signatures = signatures(phone.grab())
    last_attempt = {}  # pair -> signatures of both slots when it was tried
    mergeable = []  # signatures of barrel types that merged fine
    unmergeable = []  # (signature, until) of barrel types that did not merge (max level...)
    last_action = 0.0
    last_report = time.time()
    grid_hidden = False
    background = None
    stuck_checks = 0
    stuck_since = 0.0

    while True:
        settle = last_action + args.timing['settle'] - time.time()
        if settle > 0:
            time.sleep(settle)
        frame = phone.grab()
        new_background = grid_background(frame)
        if new_background != background:
            if new_background == 'storm':
                print('%s magnet storm, barrels can\'t be merged, waiting for it to pass' % time.strftime('%H:%M:%S'))
            elif background == 'storm':
                print('%s storm over' % time.strftime('%H:%M:%S'))
                last_report = time.time()
            previous_signatures = [None] * len(previous_signatures)
            last_attempt = {}
            background = new_background
        if background == 'storm':
            time.sleep(STORM_CHECK_SECONDS)
            continue
        if background is None:
            if not grid_hidden or time.time() - last_report > STATUS_SECONDS:
                print('%s grid not visible (popup, event or other screen), waiting'
                      % time.strftime('%H:%M:%S'))
                last_report = time.time()
                if args.debug_image:
                    cv2.imwrite(args.debug_image, frame)
            grid_hidden = True
            time.sleep(HIDDEN_WAIT_SECONDS)
            continue

        grid_hidden = False
        current = signatures(frame)
        now = time.time()
        unmergeable = [(signature, until) for signature, until in unmergeable if until > now]

        # Check how the drags of the previous round went
        failed = []
        for pair, (old_target, old_source) in last_attempt.items():
            if current[pair[0]] is not None and current[pair[1]] is not None \
                    and difference(current[pair[0]], old_target) < args.threshold \
                    and difference(current[pair[1]], old_source) < args.threshold:
                failed.append(old_target)
            elif not is_known(old_target, mergeable, args.threshold):
                mergeable = (mergeable + [old_target])[-REMEMBERED_TYPES:]
        stats.add('merges', -len(failed))  # counted when the drag was sent

        # Barrels that merged before stopped merging: the game may be ignoring drags.
        # Look again a bit later first, the game might just have been slow
        if failed and len(failed) == len(last_attempt) \
                and any(is_known(signature, mergeable, args.threshold) for signature in failed) \
                and still_unmerged(phone, last_attempt, args.threshold):
            print('%s no merge worked, the game ignores drags' % time.strftime('%H:%M:%S'))
            recover(phone)
            last_attempt = {}
            previous_signatures = [None] * len(current)
            last_action = time.time()
            continue
        for signature in failed:
            if not is_known(signature, mergeable, args.threshold):
                print('a barrel type did not merge (max level?), ignoring it for %ds' % BLOCK_SECONDS)
                unmergeable.append((signature, now + BLOCK_SECONDS))

        groups, pairs = find_pairs(current, previous_signatures, args.threshold,
                                   [signature for signature, _ in unmergeable])
        last_attempt = {pair: (current[pair[0]], current[pair[1]]) for pair in pairs}

        # Full board where nothing moves and nothing can be merged
        stable_slots = sum(len(group) for group in groups) + sum(
            1 for slot, signature in enumerate(current) if signature is not None and previous_signatures[slot] is not None
            and difference(signature, previous_signatures[slot]) < args.threshold
            and is_known(signature, [s for s, _ in unmergeable], args.threshold))
        if not pairs and stable_slots == len(current):
            if not stuck_checks:
                stuck_since = now
            stuck_checks += 1
        else:
            stuck_checks = 0
        if args.delete_overflow and not args.dry_run and stuck_checks >= OVERFLOW_CHECKS \
                and now - stuck_since >= OVERFLOW_MIN_SECONDS:
            print('%s board full and nothing can be merged (checked %d times), converting all barrels'
                  % (time.strftime('%H:%M:%S'), stuck_checks))
            stats.add('conversions', convert_all(phone, args.timing))
            stuck_checks = 0
            last_attempt = {}
            previous_signatures = [None] * len(current)
            last_action = time.time()
            continue

        if args.debug_image:
            save_debug_image(frame, current, groups, args.debug_image)
        if pairs or args.dry_run:
            print('%s merging: %s' % (time.strftime('%H:%M:%S'), pairs))
            last_report = now
        elif now - last_report > STATUS_SECONDS:
            print('%s nothing to merge: %d barrels, %d types, %d types skipped as unmergeable'
                  % (time.strftime('%H:%M:%S'), sum(signature is not None for signature in current),
                     len(groups), len(unmergeable)))
            last_report = now

        if pairs and not args.dry_run:
            merge(phone, pairs, args.timing)
            stats.add('merges', len(pairs))
            last_action = time.time()

        if args.once:
            break
        previous_signatures = current
        time.sleep(random.uniform(*args.timing['pause']))


if __name__ == '__main__':
    main()
