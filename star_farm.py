#!/usr/bin/env python3
"""Star Fragment farming for Scrap Clicker 2: converts every barrel on the board, over and over.

Uses the phone helper and screen reading of simple_merge.py. A barrel is
converted by double tapping it and confirming the "Convert Barrel?" dialog;
the dialog is checked to really be open before "Yes" gets tapped.
"""
import argparse
import random
import signal
import time

import simple_merge as sm

THRESHOLD = 10  # max mean pixel difference for "barrel looks the same as in the last frame"
MAX_MISSES = 3  # dialog not opening this many times in a row: something is in the way, back off
BLIND_BATCH = 8  # --fast: barrels converted without looking at the screen in between

# --fast with a full board (e.g. the 10x spawn boost): keep tapping the point where the
# barrel in row 3, column 2 overlaps the Yes button. Two taps open that barrel's dialog,
# the next one hits Yes, and a new barrel spawns into the freed slot. Measured: ~5
# conversions/s, as fast as the 10x boost spawns barrels
STREAM_POINT = sm.CONVERT_YES
STREAM_JITTER = 3  # px; more spoils the double taps
STREAM_INTERVAL_MS = (78, 82)
STREAM_SECONDS = 6  # then look at the board again (storm, popup...); taps there are harmless meanwhile
STREAM_CHUNK_SECONDS = 0.5  # taps sent at once, so Ctrl+C stops the tapping quickly
# Conversions can't be seen while tapping continuously (looking would slow it down),
# so the stats count them at the rate measured with the 10x boost
STREAM_CONVERSIONS_PER_SECOND = 4.0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--fast', action='store_true',
                        help='convert as fast as possible, for spawn boosts like the 10x ad boost')
    parser.add_argument('--screen-off', action='store_true',
                        help='switch the display panel off while the script runs; it comes back on when it stops')
    args = parser.parse_args()
    timing = sm.FAST_TIMING if args.fast else sm.NORMAL_TIMING

    phone = sm.Phone()
    if phone.touch_device < 0:
        print('touchscreen not found, touches are sent from a virtual device')
    stay_on = sm.switch_screen_off(phone) if args.screen_off else None
    stats = sm.Stats()
    try:
        farm(phone, timing, args.fast, stats)
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)  # a second Ctrl+C must not cut the cleanup short
        try:
            if sm.convert_dialog_open(phone.grab()):
                finish_conversion(phone, timing)
        except (OSError, ValueError):
            pass  # the helper is already gone
        if stay_on is not None:
            sm.switch_screen_on(phone, stay_on)
        phone.close()
        estimated = stats.counts.get('estimated', 0)
        stats.print_summary(['conversions'], approximate=['conversions'] if estimated else [])
        if estimated:
            print('(%d of them during continuous tapping, counted at the measured ~%.0f/s)'
                  % (estimated, STREAM_CONVERSIONS_PER_SECOND))


def finish_conversion(phone, timing):
    phone.run(sm.tap(sm.CONVERT_YES, sm.CONVERT_YES_SPREAD, timing))
    sm.wait_for(phone, lambda frame: sm.grid_background(frame) is not None, sm.CONVERT_DIALOG_TIMEOUT)


def tap_stream(seconds):
    commands, total = [], 0
    while total < seconds * 1000:
        x = STREAM_POINT[0] + random.randint(-STREAM_JITTER, STREAM_JITTER)
        y = STREAM_POINT[1] + random.randint(-STREAM_JITTER, STREAM_JITTER)
        hold = random.randint(12, 20)
        gap = random.randint(*STREAM_INTERVAL_MS) - hold
        commands += ['d %d %d' % (x, y), 's %d' % hold, 'u %d %d' % (x, y), 's %d' % gap]
        total += hold + gap
    return commands


def farm(phone, timing, blind, stats):
    previous = None
    background = 'normal'
    misses = 0
    last_report = time.time()

    while True:
        frame = phone.grab()
        if sm.convert_dialog_open(frame):
            # A conversion still waiting for its Yes (e.g. the end of a tap stream): finish it
            finish_conversion(phone, timing)
            continue
        new_background = sm.grid_background(frame)
        if new_background is None:
            # Usually just the last dialog fading out
            frame = sm.wait_for(phone, lambda frame: sm.grid_background(frame) is not None,
                                sm.CONVERT_DIALOG_TIMEOUT)
            new_background = sm.grid_background(frame)
        if new_background != background:
            if new_background == 'storm':
                print('%s magnet storm, waiting for it to pass' % time.strftime('%H:%M:%S'))
            elif new_background is None:
                print('%s grid not visible (popup, event or other screen), waiting' % time.strftime('%H:%M:%S'))
            background = new_background
            previous = None
        if background != 'normal':
            time.sleep(sm.HIDDEN_WAIT_SECONDS)
            continue

        current = sm.signatures(frame)
        if blind and all(signature is not None for signature in current):
            for _ in range(round(STREAM_SECONDS / STREAM_CHUNK_SECONDS)):
                phone.run(tap_stream(STREAM_CHUNK_SECONDS))
                stats.add('conversions', STREAM_CONVERSIONS_PER_SECOND * STREAM_CHUNK_SECONDS)
                stats.add('estimated', STREAM_CONVERSIONS_PER_SECOND * STREAM_CHUNK_SECONDS)
            ready = []
        elif blind:
            # Taps on a barrel that is still dropping in are harmless, no need to wait for it
            ready = [slot for slot, signature in enumerate(current) if signature is not None]
            if ready:
                sm.convert_blind(phone, random.sample(ready, min(len(ready), BLIND_BATCH)), timing)
                stats.add('conversions', min(len(ready), BLIND_BATCH))
                # Let the last dialog fade out before looking at the board again
                sm.wait_for(phone, lambda frame: sm.grid_background(frame) is not None, sm.CONVERT_DIALOG_TIMEOUT)
                ready = []
        else:
            # Only barrels that look the same as in the previous frame: spawn animations are over
            ready = [slot for slot, signature in enumerate(current)
                     if signature is not None and previous is not None and previous[slot] is not None
                     and sm.difference(signature, previous[slot]) < THRESHOLD]
            previous = current

        for slot in ready:
            if sm.convert(phone, slot, timing):
                stats.add('conversions')
                misses = 0
            else:
                # Something got in the way (storm, popup...): look at the board again
                misses += 1
                previous = None
                break
        if misses >= MAX_MISSES:
            print('%s the "Convert Barrel?" dialog did not open %d times in a row, waiting'
                  % (time.strftime('%H:%M:%S'), misses))
            misses = 0
            time.sleep(2)

        if time.time() - last_report > sm.STATUS_SECONDS:
            print('%s converted %s%d barrels so far' % (time.strftime('%H:%M:%S'),
                                                        '~' if stats.counts.get('estimated') else '',
                                                        stats.counts.get('conversions', 0)))
            last_report = time.time()
        if not ready:
            time.sleep(random.uniform(*timing['pause']))


if __name__ == '__main__':
    main()
