"""Upgrades: buys "Better Barrels" (barrels spawn 1 tier higher) whenever there is enough
scrap, and upgrades the brick production whenever the player level is high enough.

The Better Barrels price is read from the "Upgrades" menu (the arrow at the bottom,
first tab) and remembered; the menu is opened again once the scrap read from the top of
the screen (see HudWatcher) reaches it. There the upgrade is bought as long as its
button is green, then the new price is read and the menu closed.

The scrapyard (3rd tab of the "Prestige" menu, golden barrel at the bottom) levels up
every 10 taps on it; each tap costs scrap, its price is shown by the "i" button (read once,
then after every level). It's only opened once the scrap read at the top pays for all the
taps left to the next level, which are then tapped at once.

A brick upgrade costs the player level (it's reset to 1) and needs a certain level,
one more each time. That is read from the dialog the "Upgrade" button of the
"Prestige" menu (golden barrel at the bottom, 4th tab) opens; the upgrade is only
confirmed when the player level read at the top reaches it and the button shows the
red "available" badge.
"""
from __future__ import annotations

import random
import re
import time

from src import actions, board, hud, stats
from src.actions import Timing
from src.board import Frame
from src.hud import HudWatcher, Reading
from src.phone import Phone, screenshot

MENU_TIMEOUT = 2  # seconds for the menu / tab to show after the tap
AFTER_BUY_SECONDS = 0.35  # the button shows the next price (and turns gray when unaffordable) by then
MAX_BUYS = 50  # per opening of the menu, in case the button check goes wrong
RETRY_SECONDS = 300  # the price couldn't be read: try again after this long
# Look at the price again after this long anyway (it drops when the game gets reset, or was
# bought by hand meanwhile)
RECHECK_SECONDS = 1800
STORM_MARGIN_SECONDS = 20  # don't open the menu this close to a magnet storm
DIALOG_FADE_SECONDS = 0.3  # the brick upgrade dialog fades in
SCRAPYARD_TAP_GAP_MS = (120, 220)
SCRAPYARD_MAX_LEVELS = 10  # per opening of the menu
SCRAPYARD_TAPS_PER_LEVEL = 10


def price_text(shot: Frame) -> str:
    """Reads the price on the Better Barrels button: pink-red text on gray, or white on green."""
    left, top, right, bottom = board.BETTER_BARRELS_PRICE_BOX
    area = shot[top:bottom, left:right].astype(int)
    green, red = area[:, :, 1], area[:, :, 2]
    text = (area.min(axis=2) > 200) | ((red > 200) & (red > green + 60))
    return hud.ocr_mask(text, hud.NUMBER_CHARACTERS + 'MAX')


class Upgrader:
    def __init__(self, enabled: bool, counts: stats.Stats, timing: Timing) -> None:
        self.enabled = enabled
        self.stats = counts
        self.timing = timing
        self.price: float | None = None  # scrap Better Barrels costs, None until read
        self.maxed = False
        self.opened_at = 0.0  # when the menu was last opened
        self.closed_at = 0.0  # scrap readings from before this are outdated (scrap was spent)
        self.bricks_required: int | None = None  # player level the next brick upgrade needs, None until read
        self.bricks_opened_at = 0.0
        self.scrapyard_price: float | None = None  # scrap a tap on the scrapyard costs, None until read
        self.scrapyard_taps_left: int | None = None  # taps to its next level
        self.scrapyard_opened_at = 0.0

    def update(self, phone: Phone, frame: Frame, watcher: HudWatcher) -> bool:
        """Buys Better Barrels when there's enough scrap; True when it touched the game (the frame is outdated then)."""
        if not self.enabled:
            return False
        background = board.grid_background(frame)
        # A menu left open (e.g. a tap that got lost while it faded out). Only looked for when
        # something covers the board: on the plain board the barrels could look like parts of it
        if background is None and (board.upgrades_open(frame) or board.bricks_dialog_open(frame)
                                   or board.scrapyard_info_open(frame)):
            self.close(phone)
            return True
        if background != 'normal':
            return False
        to_storm = watcher.seconds_to_storm()
        if to_storm is not None and to_storm < STORM_MARGIN_SECONDS:
            return False
        now = time.time()
        reading = watcher.last
        affordable = self.price is not None and reading is not None and reading.scrap is not None \
            and reading.time > self.closed_at and reading.scrap >= self.price
        if self.price is None:
            due = now - self.opened_at > RETRY_SECONDS
        else:
            due = affordable or now - self.opened_at > RECHECK_SECONDS
        if due and not self.maxed:
            self.upgrade(phone)
            return True
        # Only all the taps to the next level at once
        level_price = self.scrapyard_price * (self.scrapyard_taps_left or SCRAPYARD_TAPS_PER_LEVEL) \
            if self.scrapyard_price is not None else None
        affordable = level_price is not None and reading is not None and reading.scrap is not None \
            and reading.time > self.closed_at and reading.scrap >= level_price
        if self.scrapyard_price is None:
            due = now - self.scrapyard_opened_at > RETRY_SECONDS
        else:
            due = affordable or now - self.scrapyard_opened_at > RECHECK_SECONDS
        if due:
            self.upgrade_scrapyard(phone, reading)
            return True
        player = self.stats.player
        if self.bricks_required is None:
            due = now - self.bricks_opened_at > RETRY_SECONDS
        else:
            due = player is not None and player >= self.bricks_required
        if due:
            self.upgrade_bricks(phone)
            return True
        return False

    def upgrade(self, phone: Phone) -> None:
        self.opened_at = time.time()
        try:
            self.in_menu(phone)
        finally:
            self.close_after(phone)
            self.closed_at = time.time()

    def in_menu(self, phone: Phone) -> None:
        phone.run(actions.tap(board.UPGRADES_ARROW, board.UPGRADES_ARROW_SPREAD, self.timing))
        frame = actions.wait_for(phone, board.upgrades_open, MENU_TIMEOUT)
        if not board.upgrades_open(frame):
            print('%s tapped the upgrades arrow, but the menu did not open' % time.strftime('%H:%M:%S'))
            return
        if not board.upgrades_first_tab(frame):
            phone.run(actions.tap(board.UPGRADES_FIRST_TAB, (40, 20), self.timing))
            if not board.upgrades_first_tab(actions.wait_for(phone, board.upgrades_first_tab, MENU_TIMEOUT)):
                print('%s could not switch to the first upgrades tab' % time.strftime('%H:%M:%S'))
                return
        # Full resolution for reading the texts (the helper's frames are quarter size)
        shot = screenshot()
        name = hud.ocr(shot, board.BETTER_BARRELS_NAME_BOX, dark=True)
        if 'better barrels' not in name.lower().replace('  ', ' '):
            print('%s upgrades: expected "Better Barrels", read "%s"; not buying' % (time.strftime('%H:%M:%S'), name))
            return

        self.read_level(shot)  # before buying, for the levels gained in the stats
        bought = 0
        while bought < MAX_BUYS and board.better_barrels_affordable(phone.grab()):
            phone.run(actions.tap(board.BETTER_BARRELS_BUTTON, board.BETTER_BARRELS_BUTTON_SPREAD, self.timing))
            bought += 1
            time.sleep(AFTER_BUY_SECONDS)
        if bought:
            self.stats.add('upgrades', bought)
            shot = screenshot()

        text = price_text(shot)
        level = self.read_level(shot)
        if 'max' in text.lower():
            self.maxed = True
            self.price = None
        else:
            self.price = hud.parse_number(text)
        if bought:
            self.stats.event('better barrels', bought=bought, level=level, next_price=self.price)
        print('%s Better Barrels%s: level %s, next one costs %s'
              % (time.strftime('%H:%M:%S'), ' bought %dx' % bought if bought else '', level or '?',
                 'nothing (maxed)' if self.maxed else
                 '"%s" (unreadable)' % text if self.price is None else stats.format_number(self.price)))

    def read_level(self, shot: Frame) -> str:
        """Reads the Better Barrels level ("80/999") into the stats; returns the text."""
        level = hud.ocr(shot, board.BETTER_BARRELS_LEVEL_BOX, '0123456789/', dark=True)
        match = re.match(r'(\d+)\s*/\s*\d+$', level)
        if match:
            self.stats.level('Better Barrels', int(match.group(1)))
        return level

    def upgrade_bricks(self, phone: Phone) -> None:
        self.bricks_opened_at = time.time()
        try:
            self.in_prestige_menu(phone)
        finally:
            self.close_after(phone)

    def open_prestige(self, phone: Phone, tab: int, heading: str) -> Frame | None:
        """Opens the prestige menu on the tab (0-3) and checks its page heading; the frame then, None if it failed.

        The tabs have buttons in the same places and one of them restarts the game (prestige):
        nothing gets tapped unless the heading is the expected one.
        """
        phone.run(actions.tap(board.PRESTIGE_BUTTON, board.UPGRADES_ARROW_SPREAD, self.timing))
        frame = actions.wait_for(phone, board.upgrades_open, MENU_TIMEOUT)
        if not board.upgrades_open(frame):
            print('%s tapped the golden barrel, but the prestige menu did not open' % time.strftime('%H:%M:%S'))
            return None
        # It's still opening (the tabs can't be told apart yet)
        time.sleep(DIALOG_FADE_SECONDS)
        frame = phone.grab()
        if board.prestige_tab(frame) != tab:
            phone.run(actions.tap(board.PRESTIGE_TABS[tab], (40, 20), self.timing))
            frame = actions.wait_for(phone, lambda frame: board.prestige_tab(frame) == tab, MENU_TIMEOUT)
            if board.prestige_tab(frame) != tab:
                print('%s could not switch to the "%s" tab' % (time.strftime('%H:%M:%S'), heading))
                return None
        # The tab lights up before its page changes
        time.sleep(DIALOG_FADE_SECONDS * 2)
        read = hud.ocr(screenshot(), board.PRESTIGE_HEADING_BOX, dark=True)
        if heading.lower() not in read.lower():
            print('%s expected the "%s" tab, read "%s"; not tapping anything'
                  % (time.strftime('%H:%M:%S'), heading, read))
            return None
        return phone.grab()

    def in_prestige_menu(self, phone: Phone) -> None:
        frame = self.open_prestige(phone, board.BRICKS_TAB, 'Bricks')
        if frame is None:
            return
        if not board.blue_button(frame, board.BRICKS_UPGRADE_CHECK):
            print('%s no brick "Upgrade" button found' % time.strftime('%H:%M:%S'))
            return
        available = board.bricks_available(frame)
        phone.run(actions.tap(board.BRICKS_UPGRADE, board.BRICKS_UPGRADE_SPREAD, self.timing))
        if not board.bricks_dialog_open(actions.wait_for(phone, board.bricks_dialog_open, MENU_TIMEOUT)):
            print('%s the brick upgrade dialog did not open' % time.strftime('%H:%M:%S'))
            return
        for _ in range(3):
            time.sleep(DIALOG_FADE_SECONDS)
            shot = screenshot()
            need = hud.ocr(shot, board.BRICKS_NEED_BOX, dark=True)
            if 'you need' in need.lower():
                break
        else:
            print('%s not the brick upgrade dialog ("%s"); not upgrading' % (time.strftime('%H:%M:%S'), need))
            return
        # Only shown when the level is high enough
        warning = hud.ocr(shot, board.BRICKS_WARNING_BOX, dark=True).lower()
        allowed = 'level' in warning and 'reset' in warning
        level = re.search(r'(\d+)', hud.ocr(shot, board.BRICKS_DIALOG_TITLE_BOX))
        required = re.search(r'level\s*(\d+)', hud.ocr(shot, board.BRICKS_REQUIRED_BOX).lower())
        production = hud.ocr(shot, board.BRICKS_PRODUCTION_BOX).replace('=>', '->')
        if not required:
            print('%s could not read the level the brick upgrade needs' % time.strftime('%H:%M:%S'))
            return
        self.bricks_required = int(required.group(1))
        player = self.stats.player
        if not (available and allowed) or player is None or player < self.bricks_required:
            print('%s bricks: the next upgrade needs level %d (now %s%s)'
                  % (time.strftime('%H:%M:%S'), self.bricks_required, player if player is not None else '?',
                     ', but the game says not yet' if player is not None and player >= self.bricks_required else ''))
            return
        phone.run(actions.tap(board.BRICKS_CONFIRM, board.BRICKS_UPGRADE_SPREAD, self.timing))
        if board.bricks_dialog_open(actions.wait_for(phone, lambda frame: not board.bricks_dialog_open(frame),
                                                     MENU_TIMEOUT)):
            print('%s the brick upgrade was not confirmed' % time.strftime('%H:%M:%S'))
            return
        self.stats.add('brick upgrades')
        self.stats.event('bricks', level=int(level.group(1)) + 1 if level else None, production=production,
                         player_level=player)
        self.stats.player_level_reset()
        if level:
            self.stats.level('Bricks', int(level.group(1)))
            self.stats.level('Bricks', int(level.group(1)) + 1)
        print('%s bricks upgraded (player level %d spent, back to 1): %s; the next one needs level %d'
              % (time.strftime('%H:%M:%S'), player, production, self.bricks_required + 1))
        self.bricks_required += 1

    def upgrade_scrapyard(self, phone: Phone, reading: Reading | None) -> None:
        self.scrapyard_opened_at = time.time()
        # The scrap there is to spend (None: not known, only the price gets read)
        budget = reading.scrap if reading is not None and reading.time > self.closed_at else None
        try:
            self.in_scrapyard(phone, budget)
        finally:
            self.close_after(phone)
            self.closed_at = time.time()

    def scrapyard_state(self) -> tuple[int | None, int | None]:
        """(level, taps left to the next level) read from the scrapyard page; None for each one unreadable."""
        shot = screenshot()
        level = re.search(r'(\d+)', hud.ocr(shot, board.SCRAPYARD_LEVEL_BOX))
        taps = re.search(r'(\d+)\s*time', hud.ocr(shot, board.SCRAPYARD_TAPS_BOX, dark=True))
        return int(level.group(1)) if level else None, int(taps.group(1)) if taps else None

    def read_scrapyard_price(self, phone: Phone) -> float | None:
        """Opens the "i" dialog, reads the price of a tap and closes it; None when it couldn't."""
        phone.run(actions.tap(board.SCRAPYARD_INFO, (15, 15), self.timing))
        if not board.scrapyard_info_open(actions.wait_for(phone, board.scrapyard_info_open, MENU_TIMEOUT)):
            print('%s the scrapyard info did not open' % time.strftime('%H:%M:%S'))
            return None
        time.sleep(DIALOG_FADE_SECONDS)
        price = hud.parse_number(hud.ocr(screenshot(), board.SCRAPYARD_PRICE_BOX, hud.NUMBER_CHARACTERS + '=', dark=True))
        phone.run(actions.tap(board.SCRAPYARD_INFO_CLOSE, (15, 15), self.timing))
        actions.wait_for(phone, lambda frame: not board.scrapyard_info_open(frame), MENU_TIMEOUT)
        time.sleep(DIALOG_FADE_SECONDS)
        return price

    def in_scrapyard(self, phone: Phone, budget: float | None) -> None:
        if self.open_prestige(phone, board.SCRAPYARD_TAB, 'Scrapyard') is None:
            return
        level, taps_left = self.scrapyard_state()
        if level is not None:
            self.stats.level('Scrapyard', level)
        if self.scrapyard_price is None:
            self.scrapyard_price = self.read_scrapyard_price(phone)
        tapped = 0
        # Whole levels only: all the taps left, then the price of the next level
        for _ in range(SCRAPYARD_MAX_LEVELS):
            price = self.scrapyard_price
            if price is None or taps_left is None or level is None or budget is None or budget < price * taps_left:
                break
            commands = []
            for _ in range(taps_left):
                commands += actions.tap(board.SCRAPYARD_PILE, board.SCRAPYARD_PILE_SPREAD, self.timing) \
                    + ['s %d' % random.randint(*SCRAPYARD_TAP_GAP_MS)]
            phone.run(commands)
            time.sleep(AFTER_BUY_SECONDS)
            new_level, new_taps_left = self.scrapyard_state()
            if new_level is None or new_level <= level:
                print('%s the scrapyard did not level up (read level %s, %s taps left)'
                      % (time.strftime('%H:%M:%S'), new_level, new_taps_left))
                if new_level == level and new_taps_left is not None and new_taps_left < taps_left:
                    tapped += taps_left - new_taps_left  # some taps counted
                taps_left = new_taps_left
                break
            tapped += taps_left
            budget -= price * taps_left
            level, taps_left = new_level, new_taps_left
            self.stats.level('Scrapyard', level)
            self.scrapyard_price = self.read_scrapyard_price(phone)  # of the new level
        self.scrapyard_taps_left = taps_left
        if tapped:
            self.stats.add('scrapyard taps', tapped)
            self.stats.event('scrapyard', taps=tapped, level=level, tap_price=self.scrapyard_price)
        price = self.scrapyard_price
        print('%s scrapyard%s: level %s, %s taps to the next, a tap costs %s%s'
              % (time.strftime('%H:%M:%S'), ' tapped %dx' % tapped if tapped else '', level or '?',
                 taps_left if taps_left is not None else '?',
                 stats.format_number(price) if price else '? (unreadable)',
                 ' (the level: %s)' % stats.format_number(price * taps_left) if price and taps_left else ''))

    def close_after(self, phone: Phone) -> None:
        """Closes what's open after a visit, also one cut short by Ctrl+C (the helper may be gone then)."""
        try:
            self.close(phone)
        except (OSError, ValueError):
            pass  # the connection to the phone is closed: the bot is stopping

    def close(self, phone: Phone) -> None:
        """Closes the brick upgrade dialog and the menu, whichever is open."""
        for _ in range(5):
            # While a dialog fades in / out none of them can be told apart: wait until one can
            frame = actions.wait_for(phone, lambda frame: board.grid_visible(frame) or board.bricks_dialog_open(frame)
                                     or board.scrapyard_info_open(frame) or board.upgrades_open(frame), MENU_TIMEOUT)
            if board.bricks_dialog_open(frame):
                phone.run(actions.tap(board.BRICKS_DIALOG_CLOSE, (20, 20), self.timing))
            elif board.scrapyard_info_open(frame):
                phone.run(actions.tap(board.SCRAPYARD_INFO_CLOSE, (15, 15), self.timing))
            elif board.upgrades_open(frame):
                phone.run(actions.tap(board.UPGRADES_CLOSE, (20, 20), self.timing))
            else:
                return
            time.sleep(0.3)
