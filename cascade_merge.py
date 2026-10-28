#!/usr/bin/env python3
"""The 2018 merge-barrels.py / merge-barrels-advanced.py, working again.

Doesn't tell barrels apart: drags every barrel of a row onto the one above it,
row after row in a fixed pattern that pushes merges up to the top row. Then the
top row gets merged into one barrel, which is converted into Star Fragments
(double tap + "Yes" in the "Convert Barrel?" dialog). Touches go through the
phone helper (see injector/); the screen is only looked at to collect magnet
storms and wait out popups.
"""
from __future__ import annotations

import argparse
import random
import time

from src import actions, board
from src.actions import Timing
from src.bot import Chores, add_common_arguments, handle_storm, run_bot
from src.hud import HudWatcher
from src.phone import Phone
from src.stats import Stats

# Rows whose barrels get dragged onto the row above, in this order (row 0 is the top row)
CASCADE = [1, 2, 1,
           2, 3, 2, 1,
           2, 3, 2, 3, 4, 3, 2, 1]
# Top row: column -> column drags that merge it into one barrel in the first slot
TOP_ROW_MERGES = [[(1, 0), (3, 2)], [(2, 0)]]
HIDDEN_REPORT_SECONDS = 1  # board hidden longer than this (not just a dialog fading out): say so


def side_tap(timing: Timing) -> list[str]:
    """Tap on the board background left of the first column, as the 2018 bot did after every drag."""
    x = round(board.COLUMNS_X[0] * random.uniform(0.25, 0.45))
    y = random.randint(board.FIRST_ROW_TOP, board.FIRST_ROW_TOP + board.ROW_STEP * board.ROWS)
    return actions.tap((x, y), (0, 0), timing)


def merge_row(phone: Phone, row: int, timing: Timing, side_taps: bool) -> None:
    """Drags each barrel of the row onto the barrel above it (2018: mergeToRow)."""
    columns = len(board.COLUMNS_X)
    commands: list[str] = []
    for column in range(columns):
        commands += actions.slot_drag(row * columns + column, (row - 1) * columns + column, timing)
        if side_taps:
            commands += side_tap(timing)
    phone.run(commands)


def merge_top_row(phone: Phone, timing: Timing) -> None:
    """Merges the top row into one barrel in the first slot (2018: mergeFirstRowAndRemove)."""
    for drags in TOP_ROW_MERGES:
        phone.run([command for source, target in drags for command in actions.slot_drag(source, target, timing)])
        settle(timing)


def settle(timing: Timing) -> None:
    """Gives the game time to finish the merges before the next drags."""
    time.sleep(timing['settle'] + random.uniform(0, 0.1))


def wait_for_board(phone: Phone, watcher: HudWatcher, chores: Chores, args: argparse.Namespace) -> None:
    """Returns once the plain board can be seen; collects storms, takes boosts, waits out popups,
    finishes a leftover conversion."""
    hidden_since = None
    last_report = 0.0
    while True:
        frame = phone.grab()
        if board.convert_dialog_open(frame):
            actions.finish_conversion(phone, args.timing)
            continue
        if chores.update(phone, frame):
            continue
        background = board.grid_background(frame)
        if background == 'normal':
            if last_report:
                print('%s board back, merging again' % time.strftime('%H:%M:%S'))
            return
        if background == 'storm':
            handle_storm(phone, watcher, args)
            continue
        now = time.time()
        hidden_since = hidden_since or now
        if now - hidden_since > HIDDEN_REPORT_SECONDS and now - last_report > actions.STATUS_SECONDS:
            print('%s board not visible (popup, event or other screen), waiting' % time.strftime('%H:%M:%S'))
            last_report = now
        time.sleep(actions.HIDDEN_WAIT_SECONDS)


def run(args: argparse.Namespace, phone: Phone, watcher: HudWatcher, chores: Chores, stats: Stats) -> None:
    while True:
        for row in CASCADE:
            wait_for_board(phone, watcher, chores, args)
            merge_row(phone, row, args.timing, args.side_taps)
            stats.add('drags', len(board.COLUMNS_X))
            settle(args.timing)
        wait_for_board(phone, watcher, chores, args)
        merge_top_row(phone, args.timing)
        if not args.no_convert:
            if actions.convert(phone, 0, args.timing):
                stats.add('conversions')
            settle(args.timing)
        stats.add('loops')
        print('%s loop %d done' % (time.strftime('%H:%M:%S'), stats.counts['loops']))
        if args.once:
            break


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--no-convert', action='store_true',
                        help='keep the merged top-left barrel instead of converting it into Star Fragments')
    parser.add_argument('--side-taps', action='store_true',
                        help='tap the board left of the barrels after every drag, as the 2018 bot did')
    parser.add_argument('--once', action='store_true', help='do a single loop and exit')
    add_common_arguments(parser)
    args = parser.parse_args()
    args.timing = actions.FAST_TIMING if args.fast else actions.NORMAL_TIMING
    run_bot(args, lambda phone, watcher, chores, stats: run(args, phone, watcher, chores, stats),
            ['loops', 'drags', 'conversions', 'storms', 'storm magnets', 'boosts', 'boosts declined'])


if __name__ == '__main__':
    main()
