"""What all the bots share: common options, connecting, storms and the stats at the end."""
from __future__ import annotations

import argparse
import signal
import time
from collections.abc import Callable, Sequence
from types import FrameType

from src import actions, board, boost, hud, phone as phones, popup, storm, upgrade
from src.board import Frame
from src.hud import HudWatcher
from src.phone import Phone
from src.stats import Stats

STORM_CHECK_SECONDS = 1  # --no-storm: how often to check whether a magnet storm is over


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    """Options all the bots share."""
    parser.add_argument('--fast', action='store_true',
                        help='as fast as possible, for spawn boosts like the 10x ad boost')
    parser.add_argument('--boost', action='store_true',
                        help='take the 10x barrels boost whenever the crown top right offers it (decline others) '
                             'and close the ad once it has granted the reward')
    parser.add_argument('--upgrade', action='store_true',
                        help='buy the "Better Barrels" upgrade whenever there is enough scrap for it')
    parser.add_argument('--no-storm', action='store_true',
                        help='during magnet storms just wait instead of collecting the magnets')
    parser.add_argument('--screen-off', action='store_true',
                        help='switch the display panel off while the bot runs (the game keeps running '
                             'and can still be seen by the bot); it comes back on when the bot stops')


class Chores:
    """What the bots do besides their own job: boosts, upgrades and closing popups."""

    def __init__(self, booster: boost.Booster, upgrader: upgrade.Upgrader, popups: popup.PopupCloser,
                 watcher: HudWatcher) -> None:
        self.booster = booster
        self.upgrader = upgrader
        self.popups = popups
        self.watcher = watcher

    def update(self, phone: Phone, frame: Frame) -> bool:
        """True when it touched the game (the frame is outdated then)."""
        if board.level_up_banner(frame):
            self.watcher.banner_seen()
        return self.booster.update(phone, frame) or self.upgrader.update(phone, frame, self.watcher) \
            or self.popups.update(phone, frame)


def stop_once(signal_number: int, frame: FrameType | None) -> None:
    """SIGINT handler: the first Ctrl+C stops the bot, any further ones are ignored so they can't cut the cleanup short."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    raise KeyboardInterrupt


def run_bot(args: argparse.Namespace, play: Callable[[Phone, HudWatcher, Chores, Stats], None],
            counted: Sequence[str], approximate: Sequence[str] = (),
            cleanup: Callable[[Phone], None] | None = None) -> Stats:
    """Connects to the phone, runs play(phone, watcher, chores, stats) until Ctrl+C and cleans up.

    cleanup(phone) runs before the phone gets released; the stats listed in counted
    are printed at the end (those never counted are left out, except the first)
    and the run is saved into runs/.
    """
    phone = phones.connect()
    stay_on = phones.switch_screen_off(phone) if args.screen_off and not getattr(args, 'dry_run', False) else None
    stats = Stats()
    watcher = hud.HudWatcher(stats)
    watcher.start()
    timing = actions.FAST_TIMING if args.fast else actions.NORMAL_TIMING
    dry_run = getattr(args, 'dry_run', False)
    chores = Chores(boost.Booster(args.boost and not dry_run, stats, timing),
                    upgrade.Upgrader(args.upgrade and not dry_run, stats, timing),
                    popup.PopupCloser(not dry_run, stats, timing), watcher)
    signal.signal(signal.SIGINT, stop_once)
    try:
        play(phone, watcher, chores, stats)
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if cleanup:
            try:
                cleanup(phone)
            except (OSError, ValueError):
                pass  # the helper is already gone
        if stay_on is not None:
            phones.switch_screen_on(phone, stay_on)
        phone.close()
        stats.finish(list(counted) + ['upgrades', 'scrapyard taps', 'brick upgrades', 'player levels',
                                      'level-up magnets'], approximate)
    return stats


def handle_storm(phone: Phone, watcher: HudWatcher, args: argparse.Namespace) -> None:
    """Collects the magnets of a storm that just started (or waits it out with --no-storm)."""
    if args.no_storm:
        time.sleep(STORM_CHECK_SECONDS)
        return
    print('%s magnet storm, collecting magnets' % time.strftime('%H:%M:%S'))
    seconds = storm.collect(phone, watcher)
    print('%s storm over after %.0fs' % (time.strftime('%H:%M:%S'), seconds))
