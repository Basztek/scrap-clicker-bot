#!/usr/bin/env python3
"""Star Fragment farming for Scrap Clicker 2: converts every barrel on the board, over and over.

A barrel is converted by double tapping it and confirming the "Convert Barrel?"
dialog; the dialog is checked to really be open before "Yes" gets tapped.
During magnet storms it collects the magnets instead.
"""
from __future__ import annotations

import argparse
import random
import time

from src import actions, board
from src.actions import Timing
from src.board import Background, Signature
from src.bot import Chores, add_common_arguments, handle_storm, run_bot
from src.hud import HudWatcher
from src.phone import Phone
from src.stats import Stats

THRESHOLD = 10  # max mean pixel difference for "barrel looks the same as in the last frame"
MAX_MISSES = 3  # dialog not opening this many times in a row: something is in the way, back off
BLIND_BATCH = 8  # --fast: barrels converted without looking at the screen in between

# --fast with a full board (e.g. the 10x spawn boost): keep tapping the point where the
# barrel in row 3, column 2 overlaps the Yes button. Two taps open that barrel's dialog,
# the next one hits Yes, and a new barrel spawns into the freed slot. Measured: ~5
# conversions/s, as fast as the 10x boost spawns barrels
STREAM_JITTER = 3  # px; more spoils the double taps
STREAM_INTERVAL_MS = (78, 82)
STREAM_SECONDS = 6  # then look at the board again (storm, popup...); taps there are harmless meanwhile
STREAM_CHUNK_SECONDS = 0.5  # taps sent at once, so Ctrl+C stops the tapping quickly
STORM_MARGIN_SECONDS = 1  # stop tapping this long before the next storm (from the timer at the top)
# Conversions can't be seen while tapping continuously (looking would slow it down),
# so the stats count them at the rate measured with the 10x boost
STREAM_CONVERSIONS_PER_SECOND = 4.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_arguments(parser)
    args = parser.parse_args()
    timing = actions.FAST_TIMING if args.fast else actions.NORMAL_TIMING

    def cleanup(phone: Phone) -> None:
        if board.convert_dialog_open(phone.grab()):
            actions.finish_conversion(phone, timing)

    stats = run_bot(args, lambda phone, watcher, chores, stats: farm(phone, watcher, chores, timing, args, stats),
                    ['conversions', 'storms', 'storm magnets', 'boosts', 'boosts declined'], cleanup=cleanup,
                    approximate=['conversions'] if args.fast else ())
    estimated = stats.counts.get('estimated', 0)
    if estimated:
        print('(%d of the conversions during continuous tapping, counted at the measured ~%.0f/s)'
              % (estimated, STREAM_CONVERSIONS_PER_SECOND))


def tap_stream(seconds: float) -> list[str]:
    commands: list[str] = []
    total = 0
    point = board.CONVERT_YES
    while total < seconds * 1000:
        x = point[0] + random.randint(-STREAM_JITTER, STREAM_JITTER)
        y = point[1] + random.randint(-STREAM_JITTER, STREAM_JITTER)
        hold = random.randint(12, 20)
        gap = random.randint(*STREAM_INTERVAL_MS) - hold
        commands += ['d %d %d' % (x, y), 's %d' % hold, 'u %d %d' % (x, y), 's %d' % gap]
        total += hold + gap
    return commands


def stream_seconds(watcher: HudWatcher) -> float:
    """How long to tap without looking: STREAM_SECONDS, less when a storm is about to start."""
    to_storm = watcher.seconds_to_storm()
    if to_storm is None or to_storm < -STREAM_SECONDS:  # unknown, or the timer is stale
        return STREAM_SECONDS
    return max(0.0, min(STREAM_SECONDS, to_storm - STORM_MARGIN_SECONDS))


def farm(phone: Phone, watcher: HudWatcher, chores: Chores, timing: Timing, args: argparse.Namespace,
         stats: Stats) -> None:
    blind = args.fast
    previous: list[Signature | None] | None = None
    background: Background | None = 'normal'
    misses = 0
    last_report = time.time()

    while True:
        frame = phone.grab()
        if board.convert_dialog_open(frame):
            # A conversion still waiting for its Yes (e.g. the end of a tap stream): finish it
            actions.finish_conversion(phone, timing)
            continue
        if chores.update(phone, frame):
            previous = None
            continue
        new_background = board.grid_background(frame)
        if new_background is None:
            # Usually just the last dialog fading out
            frame = actions.wait_for_grid(phone)
            new_background = board.grid_background(frame)
        if new_background == 'storm':
            handle_storm(phone, watcher, args)
            previous = None
            continue
        if new_background != background:
            if new_background is None:
                print('%s grid not visible (popup, event or other screen), waiting' % time.strftime('%H:%M:%S'))
            background = new_background
            previous = None
        if background is None:
            time.sleep(actions.HIDDEN_WAIT_SECONDS)
            continue

        current = board.signatures(frame)
        seconds = stream_seconds(watcher) if blind else 0
        if blind and not seconds:
            # The storm is about to start: wait for it instead of starting a batch
            time.sleep(0.2)
            continue
        if blind and all(signature is not None for signature in current):
            for _ in range(max(1, round(seconds / STREAM_CHUNK_SECONDS))):
                phone.run(tap_stream(STREAM_CHUNK_SECONDS))
                stats.add('conversions', STREAM_CONVERSIONS_PER_SECOND * STREAM_CHUNK_SECONDS)
                stats.add('estimated', STREAM_CONVERSIONS_PER_SECOND * STREAM_CHUNK_SECONDS)
            ready = []
        elif blind:
            # Taps on a barrel that is still dropping in are harmless, no need to wait for it
            ready = [slot for slot, signature in enumerate(current) if signature is not None]
            if ready:
                actions.convert_blind(phone, random.sample(ready, min(len(ready), BLIND_BATCH)), timing)
                stats.add('conversions', min(len(ready), BLIND_BATCH))
                # Let the last dialog fade out before looking at the board again
                actions.wait_for_grid(phone)
                ready = []
        else:
            # Only barrels that look the same as in the previous frame: spawn animations are over
            ready = [slot for slot, (signature, before) in enumerate(zip(current, previous or [None] * len(current)))
                     if signature is not None and before is not None
                     and board.difference(signature, before) < THRESHOLD]
            previous = current

        for slot in ready:
            if actions.convert(phone, slot, timing):
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

        if time.time() - last_report > actions.STATUS_SECONDS:
            print('%s converted %s%d barrels so far' % (time.strftime('%H:%M:%S'),
                                                        '~' if stats.counts.get('estimated') else '',
                                                        stats.counts.get('conversions', 0)))
            last_report = time.time()
        if not ready:
            time.sleep(random.uniform(*timing['pause']))


if __name__ == '__main__':
    main()
