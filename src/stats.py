"""Counts what a script did, prints a summary when it stops and saves the run into runs/ (see runs.py)."""
from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict

if TYPE_CHECKING:
    from src.hud import Reading

RUNS_DIR = os.environ.get('SCRAP_RUNS') \
    or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'runs')
CURRENCIES = ('scrap', 'magnets')
# Number names, each 1000x the one before, as the game's "Normal" number format shows them
# (seen in the game up to UV; the ones after it follow the same pattern and are a guess)
SUFFIXES = ['K', 'M', 'B', 'T', 'Qa', 'Qi', 'Sx', 'Sp', 'Oc', 'No', 'Dc', 'UD', 'DD', 'TD', 'QaD', 'QiD',
            'SxD', 'SpD', 'OD', 'ND', 'V', 'UV', 'DV', 'TV', 'QaV', 'QiV', 'SxV', 'SpV', 'OV', 'NV']
PLAIN_BELOW = 1e9  # numbers below this are written out in full: 70,862

# [seconds since the start, scrap, magnets, counts so far, player level] (no player level in older runs)
TimelineEntry = list[Any]
Event = list[Any]  # [seconds since the start, kind, {details}]


class Run(TypedDict):
    """A run as saved into runs/ (JSON). Runs saved by older versions miss the NotRequired parts."""
    script: str
    arguments: list[str]
    start: str  # '2026-10-28 12:53:11'
    seconds: float
    counted: list[str]  # the counts in the order to show them
    approximate: list[str]  # counts that are estimates
    counts: dict[str, float]
    currencies: dict[str, list[float]]  # name -> [first, last]
    levels: NotRequired[dict[str, list[int]]]  # upgrade -> [first, last]
    magnets_per_level: NotRequired[float | None]
    timeline: list[TimelineEntry]
    events: NotRequired[list[Event]]
    path: NotRequired[str]  # where it was loaded from (runs.py)


def format_number(value: float | None, scientific: bool = False) -> str:
    """70862 -> "70,862"; 1.366e74 -> "136.6 TV" like the game's Normal format (or "1.366e74")."""
    if value is None:
        return '?'
    if abs(value) < PLAIN_BELOW:
        return '{:,.0f}'.format(value) if abs(value) >= 1 or value == 0 else '%.2g' % value
    exponent = int(math.floor(math.log10(abs(value))))
    group = exponent // 3
    if scientific or group > len(SUFFIXES):
        return ('%.4g' % value).replace('e+', 'e')
    return '%.4g %s' % (value / 1000.0 ** group, SUFFIXES[group - 1])


def format_change(first: float | None, last: float | None, scientific: bool = False) -> str:
    """How a currency changed: "+60,862 (x7.1)" or "+136.6 TV (x81,068)"."""
    if first is None or last is None:
        return '?'
    change = last - first
    text = ('+' if change >= 0 else '-') + format_number(abs(change), scientific)
    if first > 0 and last / first >= 1.1:
        ratio = last / first
        text += ' (x%s)' % ('%.2g' % ratio if ratio < 10 else format_number(ratio, scientific))
    return text


def format_duration(seconds: float) -> str:
    hours, rest = divmod(int(seconds), 3600)
    return '%dh %02dm %02ds' % (hours, rest // 60, rest % 60)


def boost_line(boosted: float, seconds: float) -> str:
    """How long a boost ran: "1h 52m 03s (83% of the time)"."""
    return '%s (%.0f%% of the time)' % (format_duration(boosted), boosted / max(seconds, 1e-6) * 100)


def format_rates(total: float, seconds: float) -> str:
    seconds = max(seconds, 1e-6)
    per_hour = total / seconds * 3600
    return '%s/h, %s/min, %.2f/s' % (format_number(round(per_hour)), '%.1f' % (per_hour / 60), per_hour / 3600)


class Stats:
    def __init__(self) -> None:
        self.start = time.time()
        self.counts: dict[str, float] = {}
        # Currency -> (first, last) value read from the screen
        self.currencies: dict[str, tuple[float, float]] = {}
        self.readings: dict[str, int] = {}  # currency -> how many readings were taken
        # Currency (or 'player level') -> a reading that jumped too far, until the next one confirms it
        self.unconfirmed: dict[str, float] = {}
        # Upgrade -> (first, last) level read in the upgrades menu
        self.levels: dict[str, tuple[int, int]] = {}
        self.player: int | None = None  # player level last read
        self.magnets_per_level: list[float] = []  # level up magnets / level reached, of the banners read
        self.timeline: list[TimelineEntry] = []  # at every reading of the screen
        self.events: list[Event] = []  # storms, boosts, upgrades, level ups...
        self.lock = threading.Lock()  # add() and hud() run in the bot and in the HudWatcher thread

    def add(self, name: str, amount: float = 1) -> None:
        with self.lock:
            self.counts[name] = self.counts.get(name, 0) + amount

    def hud(self, reading: Reading) -> None:
        values: list[float | None] = []
        for name in CURRENCIES:
            values.append(self.currency(name, getattr(reading, name)))
        self.player_level(reading.level)
        with self.lock:
            self.timeline.append([round(reading.time - self.start, 1)] + values + [dict(self.counts), self.player])

    def currency(self, name: str, value: float | None) -> float | None:
        """Takes a value read from the screen; returns it, or None when it looks misread."""
        if value is None:
            return None
        first, last = self.currencies.get(name, (value, None))
        # A lost exponent / suffix changes it a thousandfold at least. Such a jump is only
        # believed once the next reading confirms it; if the old value was read just once, that
        # one was the misread (e.g. the very first reading) and gets replaced
        if last and not 1e-3 < value / last < 1e3:
            unconfirmed = self.unconfirmed.get(name)
            self.unconfirmed[name] = value
            if not unconfirmed or not 1e-3 < value / unconfirmed < 1e3:
                return None
            if self.readings.get(name, 0) <= 1:
                first = unconfirmed
        self.unconfirmed.pop(name, None)
        self.readings[name] = self.readings.get(name, 0) + 1
        self.currencies[name] = (first, value)
        return value

    def event(self, kind: str, **details: Any) -> None:
        """Notes something that happened, for runs.py details."""
        with self.lock:
            self.events.append([round(time.time() - self.start, 1), kind, details])

    def player_level(self, level: int | None) -> None:
        """Counts the player levels gained ('player levels').

        The level only goes up, a bit at a time; it drops when spent (brick upgrade, see
        player_level_reset()). Any other change is believed once the next reading confirms it.
        """
        if level is None:
            return
        if self.player is not None and level != self.player and not 0 < level - self.player <= 10:
            unconfirmed = self.unconfirmed.get('player level')
            self.unconfirmed['player level'] = level
            if level != unconfirmed:
                return
        elif self.player is not None and level > self.player:
            self.add('player levels', level - self.player)
            # The levels reached, for estimating the magnets of level ups whose banner wasn't read
            self.add('levels reached', sum(range(self.player + 1, level + 1)))
        self.unconfirmed.pop('player level', None)
        self.player = level

    def level_up(self, magnets: int) -> None:
        """A level up banner was read: "You got 156 Magnets!".

        The magnets are 5 per level reached, plus the bonus of the "More Magnets" upgrade
        (+2% per upgrade level), rounded down; that factor (5.8 with +16%) is learned here from
        the level read before (the banner covers the level itself).
        """
        self.add('level ups seen')
        self.add('level-up magnets', magnets)
        self.event('level up', magnets=magnets, level=self.player + 1 if self.player is not None else None)
        if self.player is not None:
            # + 0.5: the game rounds down, by half a magnet on average
            self.magnets_per_level.append((magnets + 0.5) / (self.player + 1))

    def player_level_reset(self) -> None:
        """The player level was reset to 1 (spent on a brick upgrade)."""
        self.player = 1

    def level(self, name: str, value: int) -> None:
        first = self.levels.get(name, (value, value))[0]
        self.levels[name] = (first, value)

    def run(self, counted: Sequence[str], approximate: Sequence[str] = ()) -> Run:
        """Everything about the run, as saved into runs/."""
        end = time.time()
        with self.lock:
            counts = dict(self.counts)
            timeline = list(self.timeline)
            events = list(self.events)
        return {
            'script': os.path.basename(sys.argv[0]),
            'arguments': sys.argv[1:],
            'start': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.start)),
            'seconds': round(end - self.start, 1),
            # The counts in the order to show them, those never counted left out (except the first)
            'counted': [name for name in counted if name in counts or name == counted[0]],
            'approximate': [name for name in approximate if name in counts],
            'counts': counts,
            'currencies': {name: list(values) for name, values in self.currencies.items()},
            'levels': {name: list(values) for name, values in self.levels.items()},
            # Median: when banners come quickly, the level read before one can be outdated
            'magnets_per_level': sorted(self.magnets_per_level)[len(self.magnets_per_level) // 2]
            if self.magnets_per_level else None,
            'timeline': timeline,
            'events': events,
        }

    def finish(self, counted: Sequence[str], approximate: Sequence[str] = ()) -> Run:
        """Prints the summary and saves the run; returns the run."""
        run = self.run(counted, approximate)
        print('\n' + '\n'.join(summary(run)))
        try:
            path = save(run)
            relative = os.path.relpath(path)
            print('saved as %s (see runs.py)' % (path if relative.startswith('..') else relative))
        except OSError as error:
            print('could not save the run: %s' % error)
        return run


def save(run: Run) -> str:
    os.makedirs(RUNS_DIR, exist_ok=True)
    name = '%s_%s.json' % (run['start'].replace(' ', '_').replace(':', '-'), os.path.splitext(run['script'])[0])
    path = os.path.join(RUNS_DIR, name)
    with open(path, 'w') as file:
        json.dump(run, file)
    return path


def summary(run: Run, scientific: bool = False) -> list[str]:
    """The summary of a run as lines of text."""
    seconds = run['seconds']
    lines = ['--- stats after %s ---' % format_duration(seconds)]
    for name in run['counted']:
        total = run['counts'].get(name, 0)
        mark = '~' if name in run['approximate'] else ''
        lines.append('%s: %s%s  (%s%s)' % (name, mark, format_number(round(total)), mark,
                                           format_rates(total, seconds)))
    for name, (first, last) in run['currencies'].items():
        lines.append('%s: %s -> %s  %s, %s/h' % (
            name, format_number(first, scientific), format_number(last, scientific),
            format_change(first, last, scientific),
            format_number((last - first) / max(seconds, 1e-6) * 3600, scientific)))
    for name, (first, last) in run.get('levels', {}).items():
        lines.append('%s: level %d -> %d  (%+d, %.1f/h)' % (name, first, last, last - first,
                                                             (last - first) / max(seconds, 1e-6) * 3600))
    seen, level_ups = run['counts'].get('level ups seen', 0), run['counts'].get('player levels', 0)
    if seen < level_ups:
        note = '(level-up magnets: read on %d of %d level ups' % (seen, level_ups)
        per_level = run.get('magnets_per_level')
        if per_level and 'levels reached' in run['counts']:
            # Rounded down by the game, half a magnet each on average
            note += ', all of them gave ~%s' % format_number(per_level * run['counts']['levels reached']
                                                              - level_ups / 2)
        lines.append(note + ')')
    if 'boost seconds' in run['counts']:
        lines.append('boost active: %s' % boost_line(run['counts']['boost seconds'], seconds))
    return lines
