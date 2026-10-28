#!/usr/bin/env python3
"""Simple Scrap Clicker 2 merger: drags together barrels that look the same.

Looks at the phone's screen through a small helper running on the phone
(see injector/), cuts a sample out of each of the 20 grid slots, groups slots
whose samples look alike and drags them onto each other through the same
helper. Drags start and end at random points on the barrels,
follow a slight curve and have random timing. During magnet storms it
collects the magnets instead.
"""
from __future__ import annotations

import argparse
import random
import time
from collections.abc import Collection, Iterable, Mapping, Sequence

import cv2

from src import actions, board
from src.actions import Timing
from src.board import Frame, Signature
from src.bot import Chores, add_common_arguments, handle_storm, run_bot
from src.hud import HudWatcher
from src.phone import Phone
from src.stats import Stats

Pair = tuple[int, int]  # (target slot, source slot)

BLOCK_SECONDS = 60
REMEMBERED_TYPES = 50
# A merge shows ~0.1 s after the finger lifts and its animation is over after ~0.3 s.
# Until then both slots of a dragged pair are left alone: right after the drag the target
# still looks like before (and would get paired again, then reject the barrel dropped on it)
MERGE_BUSY_SECONDS = 0.35
MERGE_TIMEOUT = 0.5  # nothing changed this long after the drag: the merge failed
# This many pairs failing at once with none merging (or pairs of a type that merged before):
# the game probably ignores input. Recovered at most every RECOVER_MIN_SECONDS, since drags
# also fail now and then for real (max level, a drag the game missed)
IGNORED_MIN_FAILED = 3
RECOVER_MIN_SECONDS = 60

# --delete-overflow: when the board is full and nothing can be merged for this
# many checks in a row, every barrel gets converted (into Star Fragments).
OVERFLOW_CHECKS = 10
OVERFLOW_MIN_SECONDS = 3  # and for at least this long (checks are quick with --fast)


def is_known(signature: Signature, known: Iterable[Signature], threshold: float) -> bool:
    return any(board.difference(signature, other) < threshold for other in known)


def find_pairs(current: Sequence[Signature | None], previous: Sequence[Signature | None], threshold: float,
               skip: Sequence[Signature], busy: Collection[int] = ()) -> tuple[list[list[int]], list[Pair]]:
    # Only use slots that look the same as in the previous frame, so barrels
    # that are still animating (dropping in, being merged) are skipped
    stable: list[tuple[int, Signature]] = []
    for slot, signature in enumerate(current):
        before = previous[slot]
        if slot not in busy and signature is not None and before is not None \
                and board.difference(signature, before) < threshold and not is_known(signature, skip, threshold):
            stable.append((slot, signature))

    groups: list[list[int]] = []
    looks: list[Signature] = []  # of each group: its first barrel
    for slot, signature in stable:
        for group, look in zip(groups, looks):
            if board.difference(signature, look) < threshold:
                group.append(slot)
                break
        else:
            groups.append([slot])
            looks.append(signature)

    pairs: list[Pair] = []
    for group in groups:
        for index in range(0, len(group) - 1, 2):
            pairs.append((group[index], group[index + 1]))
    return groups, pairs


def merge(phone: Phone, pairs: Iterable[Pair], timing: Timing) -> list[Pair]:
    """Drags the pairs in a random order; returns that order."""
    shuffled = list(pairs)
    random.shuffle(shuffled)
    commands: list[str] = []
    for target, source in shuffled:
        commands += actions.slot_drag(source, target, timing)
    phone.run(commands)
    return shuffled


def still_unmerged(phone: Phone, attempts: Mapping[Pair, tuple[Signature, Signature]], threshold: float) -> bool:
    """Checks a fresh frame a bit later whether all attempted pairs are still unmerged."""
    time.sleep(0.7)
    current = board.signatures(phone.grab())
    for (target, source), (old_target, old_source) in attempts.items():
        target_now, source_now = current[target], current[source]
        if target_now is None or source_now is None \
                or board.difference(target_now, old_target) >= threshold \
                or board.difference(source_now, old_source) >= threshold:
            return False
    return True


class DragDebug:
    """--debug: logs every failed merge with what might explain it (runs.py details shows the log).

    That's the drag's place in its batch, its distance and direction, and how long the target
    had looked the same before the drag.
    """

    def __init__(self) -> None:
        self.changed_at = [time.time()] * board.slot_count()  # slot -> when it last looked different
        self.drags: dict[Pair, tuple[int, int, float]] = {}  # pair -> (place in the batch, batch size, still)

    def frame(self, current: Sequence[Signature | None], previous: Sequence[Signature | None], threshold: float,
              now: float) -> None:
        for slot, (look, before) in enumerate(zip(current, previous)):
            if look is None or before is None or board.difference(look, before) >= threshold:
                self.changed_at[slot] = now

    def dragged(self, order: Sequence[Pair], started: float) -> None:
        self.drags.update((pair, (place, len(order), started - self.changed_at[pair[0]]))
                          for place, pair in enumerate(order))

    def done(self, pair: Pair, failed: bool, stats: Stats) -> None:
        place, size, still = self.drags.pop(pair, (0, 0, 0.0))
        if not failed:
            return
        stats.add('failed merges')
        (target_x, target_y), (source_x, source_y) = board.slot_center(pair[0]), board.slot_center(pair[1])
        stats.event('merge failed', target=pair[0], source=pair[1], drag=place + 1, of=size,
                    distance=round(((target_x - source_x) ** 2 + (target_y - source_y) ** 2) ** 0.5),
                    upward=target_y < source_y, target_still_ms=round(still * 1000))


def save_debug_image(frame: Frame, current: Sequence[Signature | None], groups: Sequence[Sequence[int]],
                     path: str) -> None:
    image = frame.copy()
    group_of = {slot: index for index, group in enumerate(groups) for slot in group}
    for slot, signature in enumerate(current):
        row, column = divmod(slot, len(board.COLUMNS_X))
        x = board.COLUMNS_X[column]
        top = board.FIRST_ROW_TOP + board.ROW_STEP * row
        color = (128, 128, 128) if signature is None else (0, 0, 255)
        cv2.rectangle(image, (x - board.SAMPLE_HALF_WIDTH, top + board.SAMPLE_TOP),
                      (x + board.SAMPLE_HALF_WIDTH, top + board.SAMPLE_BOTTOM), color, 4)
        label = 'empty' if signature is None else 'g%s' % group_of.get(slot, '?')
        cv2.putText(image, '%d:%s' % (slot, label), (x - 90, top + board.SAMPLE_BOTTOM + 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.3, (0, 0, 0), 4)
    cv2.imwrite(path, image)


def time_range(text: str) -> tuple[float, float]:
    """Parses "min-max" (or a single number) into a (min, max) tuple."""
    low, _, high = text.partition('-')
    return float(low), float(high or low)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dry-run', action='store_true', help='only print what would be merged')
    parser.add_argument('--once', action='store_true', help='do a single round and exit')
    parser.add_argument('--threshold', type=float, default=10, help='max mean pixel difference for "same barrel"')
    parser.add_argument('--swipe-ms', type=time_range,
                        help='random duration range of one drag in ms (default 150-300, with --fast 40-80)')
    parser.add_argument('--pause', type=time_range,
                        help='random range of seconds to wait between rounds (default 0.1-0.4, with --fast 0-0.05)')
    parser.add_argument('--delete-overflow', action='store_true',
                        help='when the board is full and nothing can be merged (%d checks in a row), '
                             'convert all barrels into Star Fragments to make room' % OVERFLOW_CHECKS)
    parser.add_argument('--debug-image', help='save the annotated frame of each round here')
    parser.add_argument('--debug', action='store_true',
                        help='log every failed merge with its details (see runs.py details)')
    add_common_arguments(parser)
    args = parser.parse_args()
    args.timing = (actions.FAST_TIMING if args.fast else actions.NORMAL_TIMING).copy()
    if args.swipe_ms:
        args.timing['swipe_ms'] = args.swipe_ms
    if args.pause:
        args.timing['pause'] = args.pause

    run_bot(args, lambda phone, watcher, chores, stats: run(args, phone, watcher, chores, stats),
            ['merges', 'conversions', 'storms', 'storm magnets', 'boosts', 'boosts declined'])


def run(args: argparse.Namespace, phone: Phone, watcher: HudWatcher, chores: Chores, stats: Stats) -> None:
    previous_signatures = board.signatures(phone.grab())
    # Dragged pair -> (signatures of both slots before, time of the drag), until it shows
    pending: dict[Pair, tuple[Signature, Signature, float]] = {}
    busy_until: dict[int, float] = {}  # slot -> time until which it is left alone after a drag
    debug = DragDebug() if args.debug else None
    mergeable: list[Signature] = []  # signatures of barrel types that merged fine
    unmergeable: list[tuple[Signature, float]] = []  # (signature, until) of types that did not merge (max level...)
    last_action = 0.0
    last_report = time.time()
    grid_hidden = False
    stuck_checks = 0
    stuck_since = 0.0
    last_recover = 0.0

    while True:
        settle = last_action + args.timing['settle'] - time.time()
        if settle > 0:
            time.sleep(settle)
        frame = phone.grab()
        if chores.update(phone, frame):
            previous_signatures = [None] * len(previous_signatures)
            pending, busy_until = {}, {}
            last_action = time.time()
            continue
        background = board.grid_background(frame)
        if background == 'storm':
            if not args.dry_run:
                handle_storm(phone, watcher, args)
            previous_signatures = [None] * len(previous_signatures)
            pending, busy_until = {}, {}
            last_report = time.time()
            continue
        if background is None:
            if not grid_hidden or time.time() - last_report > actions.STATUS_SECONDS:
                print('%s grid not visible (popup, event or other screen), waiting'
                      % time.strftime('%H:%M:%S'))
                last_report = time.time()
                if args.debug_image:
                    cv2.imwrite(args.debug_image, frame)
            grid_hidden = True
            previous_signatures = [None] * len(previous_signatures)
            pending, busy_until = {}, {}
            time.sleep(actions.HIDDEN_WAIT_SECONDS)
            continue

        grid_hidden = False
        current = board.signatures(frame)
        now = time.time()
        if debug:
            debug.frame(current, previous_signatures, args.threshold, now)
        unmergeable = [(signature, until) for signature, until in unmergeable if until > now]

        # Check how earlier drags went
        failed: dict[Pair, tuple[Signature, Signature]] = {}
        merged = 0
        for pair, (old_target, old_source, dragged_at) in list(pending.items()):
            target_now, source_now = current[pair[0]], current[pair[1]]
            # The target turning into the next level shows a merge. The source slot doesn't:
            # it looks empty during the drag and while a rejected barrel flies back
            target_changed = target_now is not None and board.difference(target_now, old_target) >= args.threshold
            if not target_changed and now - dragged_at < MERGE_TIMEOUT:
                continue  # not showing yet
            del pending[pair]
            # (A merge whose result looks like the old target still leaves the source empty / refilled)
            unchanged = not target_changed and source_now is not None \
                and board.difference(source_now, old_source) < args.threshold
            if debug:
                debug.done(pair, unchanged, stats)
            if unchanged:
                failed[pair] = (old_target, old_source)
            else:
                merged += 1
                if not is_known(old_target, mergeable, args.threshold):
                    mergeable = (mergeable + [old_target])[-REMEMBERED_TYPES:]
        stats.add('merges', -len(failed))  # counted when the drag was sent

        # Barrels that merged before stopped merging: the game may be ignoring drags.
        # Look again a bit later first, the game might just have been slow
        if failed and not merged and not pending and now - last_recover > RECOVER_MIN_SECONDS \
                and (any(is_known(old_target, mergeable, args.threshold) for old_target, _ in failed.values())
                     or len(failed) >= IGNORED_MIN_FAILED) \
                and still_unmerged(phone, failed, args.threshold):
            print('%s no merge worked, the game ignores drags' % time.strftime('%H:%M:%S'))
            actions.recover(phone)
            stats.event('recover', reason='drags ignored')
            last_recover = time.time()
            pending, busy_until = {}, {}
            previous_signatures = [None] * len(current)
            last_action = time.time()
            continue
        for signature, _ in failed.values():
            if not is_known(signature, mergeable, args.threshold):
                print('a barrel type did not merge (max level?), ignoring it for %ds' % BLOCK_SECONDS)
                stats.event('unmergeable')
                unmergeable.append((signature, now + BLOCK_SECONDS))

        busy = {slot for pair in pending for slot in pair} \
            | {slot for slot, until in busy_until.items() if until > now}
        groups, pairs = find_pairs(current, previous_signatures, args.threshold,
                                   [signature for signature, _ in unmergeable], busy)

        # Full board where nothing moves and nothing can be merged
        stable_slots = sum(len(group) for group in groups)
        for look, before in zip(current, previous_signatures):
            if look is not None and before is not None and board.difference(look, before) < args.threshold \
                    and is_known(look, [skipped for skipped, _ in unmergeable], args.threshold):
                stable_slots += 1
        if not pairs and not busy and stable_slots == len(current):
            if not stuck_checks:
                stuck_since = now
            stuck_checks += 1
        else:
            stuck_checks = 0
        if args.delete_overflow and not args.dry_run and stuck_checks >= OVERFLOW_CHECKS \
                and now - stuck_since >= OVERFLOW_MIN_SECONDS:
            print('%s board full and nothing can be merged (checked %d times), converting all barrels'
                  % (time.strftime('%H:%M:%S'), stuck_checks))
            converted = actions.convert_all(phone, args.timing)
            stats.add('conversions', converted)
            stats.event('board full', converted=converted)
            stuck_checks = 0
            pending, busy_until = {}, {}
            previous_signatures = [None] * len(current)
            last_action = time.time()
            continue

        if args.debug_image:
            save_debug_image(frame, current, groups, args.debug_image)
        if pairs or args.dry_run:
            print('%s merging: %s' % (time.strftime('%H:%M:%S'), pairs))
            last_report = now
        elif now - last_report > actions.STATUS_SECONDS:
            print('%s nothing to merge: %d barrels, %d types, %d types skipped as unmergeable'
                  % (time.strftime('%H:%M:%S'), sum(signature is not None for signature in current),
                     len(groups), len(unmergeable)))
            last_report = now

        if pairs and not args.dry_run:
            started = time.time()
            order = merge(phone, pairs, args.timing)
            stats.add('merges', len(pairs))
            last_action = time.time()
            if debug:
                debug.dragged(order, started)
            for target, source in pairs:
                target_look, source_look = current[target], current[source]
                if target_look is not None and source_look is not None:  # (always: find_pairs() skips empty slots)
                    pending[(target, source)] = (target_look, source_look, last_action)
                busy_until[target] = busy_until[source] = last_action + MERGE_BUSY_SECONDS

        if args.once:
            break
        previous_signatures = current
        time.sleep(random.uniform(*args.timing['pause']))


if __name__ == '__main__':
    main()
