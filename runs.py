#!/usr/bin/env python3
"""Shows the runs the bots saved into runs/ (one JSON file per run, written when a bot stops).

  runs.py                 list the runs, numbered
  runs.py show [RUN]      one run (by its number, default the latest): its summary and how it went over time
  runs.py details [RUN]   everything about one run: speeds, growth, magnet sources, levels, storms,
                          boosts, charts over time and the log of what happened
  runs.py total           all runs added up (--script to pick one bot)

Big numbers are written like the game's Normal number format (136.6 TV); --scientific
writes them like its Scientific format (1.366e74).
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import math
import os
import sys
import time
from collections.abc import Sequence
from typing import Any

from src import stats
from src.stats import Run, TimelineEntry

PERIODS = [(3 * 3600, 3600), (3600, 1800), (0, 600)]  # run length -> length of the periods in "show"
SPARK = '▁▂▃▄▅▆▇█'
CHART_WIDTH = 60


def load_runs(script: str | None = None) -> list[Run]:
    runs: list[Run] = []
    for path in sorted(glob.glob(os.path.join(stats.RUNS_DIR, '*.json'))):
        try:
            with open(path) as file:
                run: Run = json.load(file)
        except (OSError, ValueError) as error:
            print('skipping %s: %s' % (path, error), file=sys.stderr)
            continue
        if script is None or os.path.splitext(run['script'])[0] == os.path.splitext(script)[0]:
            run['path'] = path
            runs.append(run)
    return runs


def table(rows: Sequence[Sequence[str]], left: int = 1) -> list[str]:
    """Lines with the columns of rows (lists of texts) lined up; the first row is the header.

    The first left columns are aligned left, the others (numbers) right.
    """
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    lines = ['  '.join(text.ljust(width) if column < left else text.rjust(width)
                       for column, (text, width) in enumerate(zip(row, widths))).rstrip() for row in rows]
    return lines[:1] + ['-' * len(max(lines, key=len))] + lines[1:]


def currency_change(run: Run, name: str, args: argparse.Namespace) -> str:
    first, last = run['currencies'].get(name, (None, None))
    return stats.format_change(first, last, args.scientific) if first is not None else '-'


def list_runs(runs: Sequence[Run], args: argparse.Namespace) -> None:
    rows = [['#', 'started', 'script', 'length', 'main count', 'boost', 'upgrades', 'magnets', 'scrap']]
    for number, run in enumerate(runs, 1):
        main = run['counted'][0] if run['counted'] else None
        total = run['counts'].get(main, 0) if main else 0
        rows.append([str(number), run['start'][:16], os.path.splitext(run['script'])[0],
                     stats.format_duration(run['seconds']),
                     '%s %s (%s/h)' % (stats.format_number(round(total)), main,
                                       stats.format_number(round(total / max(run['seconds'], 1) * 3600)))
                     if main else '-',
                     '%.0f%%' % (run['counts']['boost seconds'] / max(run['seconds'], 1) * 100)
                     if 'boost seconds' in run['counts'] else '-',
                     ', '.join('%+d' % (last - first) for first, last in run.get('levels', {}).values()) or '-',
                     currency_change(run, 'magnets', args), currency_change(run, 'scrap', args)])
    print('\n'.join(table(rows, left=3)))


def value_at(timeline: Sequence[TimelineEntry], index: int, end: float) -> Any:
    """Last value of the column (1 scrap, 2 magnets) read before the time end; None if none was."""
    values = [entry[index] for entry in timeline if entry[0] < end and entry[index] is not None]
    return values[-1] if values else None


def show(run: Run, args: argparse.Namespace) -> None:
    print('%s  %s %s' % (run['start'], run['script'], ' '.join(run['arguments'])))
    print('\n'.join(stats.summary(run, args.scientific)))
    storms = run['counts'].get('storms', 0)
    if storms and 'storm magnets' in run['counts']:
        print('magnets per storm: %s' % stats.format_number(round(run['counts']['storm magnets'] / storms)))
    over_time(run, args)


def over_time(run: Run, args: argparse.Namespace) -> None:
    timeline = run['timeline']
    if not timeline:
        return
    period = args.every * 60 if args.every else next(length for over, length in PERIODS if run['seconds'] >= over)
    names = run['counted']
    boosted = 'boost seconds' in run['counts']
    rows = [['time'] + names + (['boost'] if boosted else []) + ['magnets', 'scrap']]
    start = 0.0
    while start < run['seconds']:
        end = start + period
        if run['seconds'] - end < period / 4:  # a short rest goes into the last line
            end = run['seconds'] + 1
        counts_before: dict[str, float] = next((entry[3] for entry in reversed(timeline) if entry[0] < start), {})
        counts_after: dict[str, float] = next((entry[3] for entry in reversed(timeline) if entry[0] < end), {})
        row = ['%s-%s' % (stats.format_duration(start)[:-4], stats.format_duration(min(end, run['seconds']))[:-4])]
        row += [stats.format_number(round(counts_after.get(name, 0) - counts_before.get(name, 0))) for name in names]
        if boosted:
            boost = counts_after.get('boost seconds', 0) - counts_before.get('boost seconds', 0)
            row.append('%.0f%%' % (boost / (min(end, run['seconds']) - start) * 100))
        for index in (2, 1):
            before = value_at(timeline, index, start) if start else next(
                (entry[index] for entry in timeline if entry[index] is not None), None)
            after = value_at(timeline, index, end)
            row.append(stats.format_change(before, after, args.scientific) if before is not None else '?')
        rows.append(row)
        start = end
    print('\nevery %d minutes:' % (period // 60))
    print('\n'.join(table(rows)))


def section(title: str) -> None:
    print('\n== %s ==' % title)


def clock(run: Run, seconds: float) -> str:
    """Time of day, seconds into the run."""
    start = time.mktime(time.strptime(run['start'], '%Y-%m-%d %H:%M:%S'))
    return time.strftime('%H:%M:%S', time.localtime(start + seconds))


def per_minute(count: float, seconds: float) -> str:
    return '%.1f/min' % (count / seconds * 60) if seconds > 0 else '-'


def sparkline(values: Sequence[float | None]) -> str:
    """A chart in one line of text; None values show as a space."""
    known = [value for value in values if value is not None]
    if not known:
        return ''
    low, high = min(known), max(known)
    return ''.join(' ' if value is None else SPARK[0 if high == low else
                                                  round((value - low) / (high - low) * (len(SPARK) - 1))]
                   for value in values)


def charts(run: Run) -> None:
    """Merges per minute, magnets, player level and scrap over the run, CHART_WIDTH columns."""
    timeline, main = run['timeline'], run['counted'][0] if run['counted'] else None
    if len(timeline) < 2:
        return
    width = min(CHART_WIDTH, len(timeline) - 1)  # a reading every ~10 s
    step = run['seconds'] / width
    rate, magnets, level, scrap, boost = [], [], [], [], []
    for column in range(width):
        start, end = column * step, (column + 1) * step
        inside = [entry for entry in timeline if start <= entry[0] < end]
        before = [entry for entry in timeline if entry[0] < start]
        if inside and (before or len(inside) > 1):
            first, last = (before[-1] if before else inside[0]), inside[-1]
            seconds = last[0] - first[0]
            rate.append((last[3].get(main, 0) - first[3].get(main, 0)) / seconds * 60 if seconds > 0 else None)
            boosted = last[3].get('boost seconds', 0) - first[3].get('boost seconds', 0)
            boost.append(min(100, boosted / seconds * 100) if seconds > 0 else None)
        else:
            rate.append(None)
            boost.append(None)
        magnets.append(next((entry[2] for entry in reversed(inside) if entry[2] is not None), None))
        level.append(next((entry[4] for entry in reversed(inside) if len(entry) > 4 and entry[4] is not None), None))
        # Scrap grows exponentially: its logarithm shows the growth rate
        scrap.append(next((math.log10(entry[1]) for entry in reversed(inside) if entry[1]), None))
    section('over time (%s per column, low to high)' % stats.format_duration(step)[3:])
    rows = [('%s/min' % main if main else 'per min', rate), ('boost on %', boost), ('magnets', magnets),
            ('player level', level), ('scrap (log)', scrap)]
    width = max(len(name) for name, _ in rows)
    for name, values in rows:
        known = [value for value in values if value is not None]
        if known and any(known):
            print('%s  %s  %s .. %s' % (name.ljust(width), sparkline(values),
                                       stats.format_number(min(known)) if name != 'scrap (log)' else 'e%.1f' % min(known),
                                       stats.format_number(max(known)) if name != 'scrap (log)' else 'e%.1f' % max(known)))


def details(run: Run, args: argparse.Namespace) -> None:
    counts, seconds, events = run['counts'], run['seconds'], run.get('events', [])
    main = run['counted'][0] if run['counted'] else None
    section('run')
    print('file:      %s' % os.path.relpath(run['path']))
    print('script:    %s %s' % (run['script'], ' '.join(run['arguments'])))
    print('time:      %s -> %s (%s)' % (run['start'], clock(run, seconds), stats.format_duration(seconds)))
    if not events:
        print('(saved before events were logged: storms, boosts, upgrades and level ups are not listed one by one)')

    section('counts')
    # ('levels reached' only feeds the level-up magnets estimate)
    shown = [name for name in run['counted']] + sorted(name for name in counts
                                                       if name not in run['counted'] and name != 'levels reached')
    width = max(len(name) for name in shown)
    for name in shown:
        value = counts.get(name, 0)
        if name == 'boost seconds':
            text = stats.format_duration(value)
        else:
            text = stats.format_number(round(value)) + '  (' + stats.format_rates(value, seconds) + ')'
        print('%s  %s' % (name.ljust(width), text))

    if main and run['timeline']:
        section('speed')
        failed = counts.get('failed merges', 0)
        if main == 'merges' and counts.get('merges') and '--debug' in run['arguments']:  # (failures only counted then)
            print('drags that merged: %.1f%% (%s failed)' % (counts['merges'] / (counts['merges'] + failed) * 100,
                                                            stats.format_number(failed)))
        # Between consecutive readings: with the boost running all the time, or not at all
        timeline = run['timeline']
        totals = {'boost on': [0.0, 0.0], 'boost off': [0.0, 0.0]}  # [done, seconds]
        fastest = 0.0
        for index, (before, after) in enumerate(zip(timeline, timeline[1:])):
            dt = after[0] - before[0]
            done = after[3].get(main, 0) - before[3].get(main, 0)
            boosted = after[3].get('boost seconds', 0) - before[3].get('boost seconds', 0)
            if dt <= 0:
                continue
            if boosted >= 0.8 * dt:
                totals['boost on'][0] += done
                totals['boost on'][1] += dt
            elif boosted == 0:
                totals['boost off'][0] += done
                totals['boost off'][1] += dt
            # The fastest minute: from this reading to the first one at least 60 s later
            later = next((entry for entry in timeline[index + 1:] if entry[0] - before[0] >= 60), None)
            if later:
                fastest = max(fastest, (later[3].get(main, 0) - before[3].get(main, 0)) / (later[0] - before[0]) * 60)
        print('%s overall: %s' % (main, per_minute(counts.get(main, 0), seconds)))
        for name, (done, dt) in totals.items():
            if dt >= 60:
                print('%s with %s: %s (over %s)' % (main, name, per_minute(done, dt), stats.format_duration(dt)))
        if fastest:
            print('fastest minute: %.1f %s' % (fastest, main))

    section('currencies')
    for name, (first, last) in run['currencies'].items():
        print('%s: %s -> %s  %s' % (name, stats.format_number(first, args.scientific),
                                    stats.format_number(last, args.scientific),
                                    stats.format_change(first, last, args.scientific)))
    if 'scrap' in run['currencies']:
        first, last = run['currencies']['scrap']
        if first and last and last > first:
            growth = math.log(last / first) / seconds  # per second
            print('scrap grew x%s per hour, doubling every %.1f s'
                  % (stats.format_number(math.exp(min(growth * 3600, 700))), math.log(2) / growth))
    if 'magnets' in run['currencies']:
        first, last = run['currencies']['magnets']
        gained = last - first
        storm, level_ups = counts.get('storm magnets', 0), counts.get('level-up magnets', 0)
        per_level = run.get('magnets_per_level')
        estimated = bool(per_level and counts.get('levels reached')
                         and counts.get('level ups seen', 0) < counts.get('player levels', 0))
        if per_level and estimated:
            level_ups = max(level_ups, per_level * counts['levels reached'] - counts['player levels'] / 2)
        print('magnets gained: %s' % stats.format_number(gained))
        for name, value in (('from storms', storm), ('from level ups' + (' (~, some estimated)' if estimated else ''), level_ups),
                            ('other (merging, minus anything spent)', gained - storm - level_ups)):
            print('  %s: %s%s' % (name, stats.format_number(round(value)),
                                  ' (%.0f%%)' % (value / gained * 100) if gained > 0 else ''))

    section('levels')
    levels = counts.get('player levels', 0)
    if levels:
        reached = [entry[4] for entry in run['timeline'] if len(entry) > 4 and entry[4] is not None]
        print('player levels gained: %d (%s, one every %.0f s)%s'
              % (levels, per_minute(levels, seconds), seconds / levels,
                 ', highest level %d' % max(reached) if reached else ''))
    per_level = run.get('magnets_per_level')
    if per_level:
        print('level-up magnets: %.2f per level reached (5 + the "More Magnets" bonus: +%.0f%%)'
              % (per_level, (per_level / 5 - 1) * 100))
    for name, (first, last) in run.get('levels', {}).items():
        print('%s: level %d -> %d (%+d)' % (name, first, last, last - first))
    for kind, label in (('better barrels', 'Better Barrels'), ('bricks', 'bricks')):
        for at, _, info in (event for event in events if event[1] == kind):
            if kind == 'bricks':
                print('  %s bricks -> level %s: %s (player level %s spent)'
                      % (clock(run, at), info.get('level'), info.get('production'), info.get('player_level')))
            else:
                print('  %s Better Barrels bought %dx -> %s, next one %s'
                      % (clock(run, at), info['bought'], info.get('level'),
                         stats.format_number(info['next_price'], args.scientific) if info.get('next_price') else '?'))

    storms = [event for event in events if event[1] == 'storm']
    if counts.get('storms'):
        section('storms')
        print('%d storms, %s magnets each on average'
              % (counts['storms'], stats.format_number(round(counts.get('storm magnets', 0) / counts['storms']))))
        for at, _, info in storms:
            print('  %s  %+d magnets in %d s' % (clock(run, at), info['magnets'], info['seconds']))

    offers = [event[2] for event in events if event[1] == 'boost offer']
    if offers or counts.get('boost seconds'):
        section('boosts')
        if counts.get('boost seconds'):
            print('boost active: %s' % stats.boost_line(counts['boost seconds'], seconds))
        for name, number in collections.Counter(offer['name'] for offer in offers).most_common():
            print('  offered %dx: %s%s' % (number, name, ' (taken)' if any(offer['taken'] and offer['name'] == name
                                                                          for offer in offers) else ''))
        for at, _, info in (event for event in events if event[1] == 'boost ended'):
            print('  %s boost ended after %s' % (clock(run, at), stats.format_duration(info['seconds'])))

    charts(run)
    over_time(run, args)

    if events:
        section('log')
        for at, kind, info in events:
            print('%s  %s%s' % (clock(run, at), kind,
                                '  ' + ', '.join('%s %s' % (key.replace('_', ' '), log_value(value, args))
                                                 for key, value in info.items() if value is not None) if info else ''))


def log_value(value: Any, args: argparse.Namespace) -> Any:
    if isinstance(value, bool):
        return 'yes' if value else 'no'
    if isinstance(value, (int, float)):
        return stats.format_number(value, args.scientific)
    return value


def total(runs: Sequence[Run], args: argparse.Namespace) -> None:
    seconds = sum(run['seconds'] for run in runs)
    print('%d runs, %s in all' % (len(runs), stats.format_duration(seconds)))
    names = []
    for run in runs:
        names += [name for name in run['counted'] if name not in names]
    for name in names:
        count = sum(run['counts'].get(name, 0) for run in runs)
        # Rates over the runs that could count it (star_farm.py has no merges...)
        counting = sum(run['seconds'] for run in runs if name in run['counted'])
        print('%s: %s  (%s)' % (name, stats.format_number(round(count)), stats.format_rates(count, counting)))
    levels: dict[str, int] = {}
    for run in runs:
        for name, (first, last) in run.get('levels', {}).items():
            levels[name] = levels.get(name, 0) + last - first
    for name, gained in levels.items():
        print('%s: %+d levels  (%.1f/h)' % (name, gained, gained / max(seconds, 1e-6) * 3600))
    if any('boost seconds' in run['counts'] for run in runs):
        print('boost active: %s' % stats.boost_line(
            sum(run['counts'].get('boost seconds', 0) for run in runs), seconds))
    for name in stats.CURRENCIES:
        changes = [(run['currencies'][name], run['seconds']) for run in runs if name in run['currencies']]
        if changes:
            amount = math.fsum(last - first for (first, last), _ in changes)
            counting = math.fsum(run_seconds for _, run_seconds in changes)
            print('%s gained: %s  (%s/h)' % (name, stats.format_number(amount, args.scientific),
                                            stats.format_number(amount / counting * 3600, args.scientific)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', nargs='?', default='list', choices=['list', 'show', 'details', 'total'])
    parser.add_argument('run', nargs='?', type=int, help='show: number of the run (from the list), default the latest')
    parser.add_argument('--script', help='only the runs of this script (e.g. simple_merge)')
    parser.add_argument('--every', type=int, help='show: minutes per line of the over-time table')
    parser.add_argument('--scientific', action='store_true', help='big numbers as 1.366e74 instead of 136.6 TV')
    args = parser.parse_args()

    runs = load_runs(args.script)
    if not runs:
        sys.exit('no runs saved in %s yet (the bots save one when they stop)' % stats.RUNS_DIR)
    if args.command == 'list':
        list_runs(runs, args)
    elif args.command in ('show', 'details'):
        if args.run is not None and not 1 <= args.run <= len(runs):
            sys.exit('there are runs 1-%d' % len(runs))
        (show if args.command == 'show' else details)(runs[args.run - 1 if args.run else -1], args)
    else:
        total(runs, args)


if __name__ == '__main__':
    main()
