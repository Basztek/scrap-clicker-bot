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

BACKGROUND_BGR = np.array([173, 208, 171])
GAP_X = [331, 614, 897]  # background between columns, used to check the grid is visible

EMPTY_MAX_STD = 6  # empty slot = flat gray silhouette or plain background
SIGNATURE_SIZE = (6, 8)  # (width, height) of the downscaled sample
BLOCK_SECONDS = 60
SETTLE_SECONDS = 0.25  # time the game gets to show the result of the drags
REMEMBERED_TYPES = 50
STATUS_SECONDS = 15  # how often to say what's going on when there is nothing to merge

GAME_PACKAGE = 'com.scrap.clicker.android'
OFFLINE_POPUP_OK = (610, 1770)  # "Ok" of the "Offline Production" popup shown after resuming

INJECTOR_DEX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'injector', 'injector.dex')
INJECTOR_ON_PHONE = '/data/local/tmp/injector.dex'
PR_SET_PDEATHSIG = 1


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


def slot_center(slot):
    row, column = divmod(slot, len(COLUMNS_X))
    return COLUMNS_X[column], FIRST_ROW_TOP + ROW_STEP * row + TOUCH_OFFSET_Y


def slot_sample(frame, slot):
    row, column = divmod(slot, len(COLUMNS_X))
    x = COLUMNS_X[column]
    top = FIRST_ROW_TOP + ROW_STEP * row
    return frame[top + SAMPLE_TOP:top + SAMPLE_BOTTOM, x - SAMPLE_HALF_WIDTH:x + SAMPLE_HALF_WIDTH]


def grid_visible(frame):
    # When a popup/ad/menu covers the grid the gaps between barrels stop being background
    matches = 0
    for row in range(ROWS):
        y = FIRST_ROW_TOP + ROW_STEP * row + TOUCH_OFFSET_Y
        for x in GAP_X:
            if np.abs(frame[y, x].astype(int) - BACKGROUND_BGR).sum() < 30:
                matches += 1
    return matches >= len(GAP_X) * ROWS - 2


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


def drag_commands(start, end, duration_ms):
    """Injector commands for a drag along a quadratic Bezier curve."""
    (x1, y1), (x2, y2) = start, end
    length = max(1.0, ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5)
    # Control point: the midpoint pushed sideways (perpendicular to the drag)
    bend = random.uniform(-MAX_BEND, MAX_BEND) * length
    control_x = (x1 + x2) / 2 - (y2 - y1) / length * bend
    control_y = (y1 + y2) / 2 + (x2 - x1) / length * bend

    steps = random.randint(8, 14)
    commands = ['d %d %d' % start, 's %d' % random.randint(30, 90)]
    for step in range(1, steps + 1):
        t = step / steps
        x = (1 - t) ** 2 * x1 + 2 * (1 - t) * t * control_x + t ** 2 * x2
        y = (1 - t) ** 2 * y1 + 2 * (1 - t) * t * control_y + t ** 2 * y2
        commands += ['m %d %d' % (x, y), 's %d' % (duration_ms / steps)]
    commands.append('u %d %d' % end)
    return commands


def merge(phone, pairs, swipe_ms):
    pairs = list(pairs)
    random.shuffle(pairs)
    commands = []
    for target, source in pairs:
        commands += drag_commands(random_point(source, START_SPREAD), random_point(target, END_SPREAD),
                                  random.uniform(*swipe_ms))
        commands.append('s %d' % random.randint(30, 120))
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
    parser.add_argument('--swipe-ms', type=time_range, default='150-300',
                        help='random duration range of one drag in ms, e.g. 150-300')
    parser.add_argument('--pause', type=time_range, default='0.1-0.4',
                        help='random range of seconds to wait between rounds')
    parser.add_argument('--debug-image', help='save the annotated frame of each round here')
    parser.add_argument('--screen-off', action='store_true',
                        help='switch the display panel off while the bot runs (the game keeps running '
                             'and can still be seen by the bot); it comes back on when the bot stops')
    args = parser.parse_args()

    phone = Phone()
    if phone.touch_device < 0:
        print('touchscreen not found, touches are sent from a virtual device')

    stay_on = None
    if args.screen_off and not args.dry_run:
        # Android doesn't know the panel is off, so stop it from going to sleep
        # at the screen timeout (which would pause the game) while charging over USB
        stay_on = subprocess.run(['adb', 'shell', 'settings get global stay_on_while_plugged_in'],
                                 capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(['adb', 'shell', 'settings put global stay_on_while_plugged_in 7'], check=True)
        # The helper on the phone restores it even when the bot gets killed
        phone.run(['atexit settings put global stay_on_while_plugged_in %s' % stay_on, 'screen 0'])
        print('display switched off, press Ctrl+C to stop the bot and switch it back on')
    try:
        run(args, phone)
    except KeyboardInterrupt:
        pass
    finally:
        if stay_on is not None:
            try:
                phone.run(['screen 1'])
            except OSError:
                pass  # the helper died with Ctrl+C; it switches the screen back on itself when it exits
            subprocess.run(['adb', 'shell', 'settings put global stay_on_while_plugged_in %s' % stay_on])
        phone.close()


def run(args, phone):
    previous_signatures = signatures(phone.grab())
    last_attempt = {}  # pair -> signatures of both slots when it was tried
    mergeable = []  # signatures of barrel types that merged fine
    unmergeable = []  # (signature, until) of barrel types that did not merge (max level...)
    last_action = 0.0
    last_report = time.time()
    grid_hidden = False

    while True:
        settle = last_action + SETTLE_SECONDS - time.time()
        if settle > 0:
            time.sleep(settle)
        frame = phone.grab()
        if not grid_visible(frame):
            if not grid_hidden or time.time() - last_report > STATUS_SECONDS:
                print('%s grid not visible (popup, event or other screen), waiting'
                      % time.strftime('%H:%M:%S'))
                last_report = time.time()
                if args.debug_image:
                    cv2.imwrite(args.debug_image, frame)
            grid_hidden = True
            previous_signatures = [None] * len(previous_signatures)
            last_attempt = {}
            time.sleep(2)
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
            merge(phone, pairs, args.swipe_ms)
            last_action = time.time()

        if args.once:
            break
        previous_signatures = current
        time.sleep(random.uniform(*args.pause))


if __name__ == '__main__':
    main()
