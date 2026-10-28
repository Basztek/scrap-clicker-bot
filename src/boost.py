"""Boosts: takes the "Faster Barrels" (10x barrels) boost whenever the game offers it.

A crown in the blue square top right means a boost is on offer. Tapping it opens
an "Event!" dialog naming the boost; Yes starts an ad, after which the boost runs
(the crown turns into its timer), No declines (the square disappears until the
next offer). Only the 10x barrels boost is taken, any other one is declined.

The ad is closed once it says "Reward granted" (or shows just an X): the X
buttons in its top corners are found by their shape, and the text next to
each one is read so that none gets tapped during the "Reward in 5 seconds"
countdown, which would cost the reward.
"""
from __future__ import annotations

import re
import time

import cv2
import numpy as np
import numpy.typing as npt

from src import actions, board, hud
from src.actions import Timing
from src.board import Frame, Point
from src.phone import Phone, ad_in_front, screenshot
from src.stats import Stats

CHECK_SECONDS = 5  # how often to look for the crown
DIALOG_TIMEOUT = 2  # seconds for the dialog to open after tapping the crown
DIALOG_FADE_SECONDS = 0.3  # the dialog fades in and only takes taps once it is fully there
CROWN_MISSES = 2  # crown taps in a row without a dialog: the game ignores taps, recover it
TIMER_CHECK_SECONDS = 1  # how often to look whether a boost runs (its timer top right), for the stats
# Longest time without seeing the timer (storm cloud, popup...) that still counts as boosted
# when the timer is there before and after
TIMER_MAX_GAP = 60

AD_CHECK_SECONDS = 2  # how often to try closing the ad
AD_BAR_HEIGHT = 0.13  # the ad's X buttons are in this top part of the screen (fraction of its height)
AD_CORNER_WIDTH = 0.25  # ... and this far from the left / right edge (fraction of its width)
X_MATCH = 0.75  # template match score of an X (X buttons ~0.9, anything in the game < 0.5)
X_SIZES = range(28, 96, 6)  # px
X_TEXT_WIDTH = 0.5  # text read left of an X ("Reward granted"), fraction of the screen width


def wanted(text: str) -> bool:
    """The 10x barrels boost: "Faster Barrels / ... Barrels x10 times faster for 10 minutes!"."""
    text = text.lower()
    return 'barrel' in text and re.search(r'x\s*10\b|\b10\s*x', text) is not None


def x_templates() -> list[npt.NDArray[np.uint8]]:
    """An X drawn light on dark and dark on light, in several sizes."""
    templates = []
    for size in X_SIZES:
        for light in (True, False):
            template = np.full((size, size), 0 if light else 255, np.uint8)
            margin, width = size // 5, max(2, size // 9)
            cv2.line(template, (margin, margin), (size - margin, size - margin), 255 if light else 0, width)
            cv2.line(template, (margin, size - margin), (size - margin, margin), 255 if light else 0, width)
            templates.append(template)
    return templates


X_TEMPLATES = x_templates()


def close_buttons(shot: Frame) -> list[Point]:
    """Centers of the X buttons in the top corners of a full resolution screenshot, best match first."""
    height, width = shot.shape[:2]
    bar = cv2.cvtColor(shot[:round(height * AD_BAR_HEIGHT)], cv2.COLOR_BGR2GRAY)
    found: list[tuple[float, Point]] = []
    for template in X_TEMPLATES:
        scores = cv2.matchTemplate(bar, template, cv2.TM_CCOEFF_NORMED)
        for y, x in zip(*np.where(scores >= X_MATCH)):
            center = (int(x) + template.shape[1] // 2, int(y) + template.shape[0] // 2)
            if width * AD_CORNER_WIDTH < center[0] < width * (1 - AD_CORNER_WIDTH):
                continue
            found.append((float(scores[y, x]), center))
    buttons: list[Point] = []
    for score, center in sorted(found, reverse=True):
        if all(abs(center[0] - x) > 30 or abs(center[1] - y) > 30 for x, y in buttons):
            buttons.append(center)
    return buttons


def text_left_of(shot: Frame, button: Point) -> str:
    x, y = button
    box = (max(0, x - round(shot.shape[1] * X_TEXT_WIDTH)), max(0, y - 45), max(1, x - 35), y + 45)
    return hud.ocr(shot, box).lower()


class Booster:
    def __init__(self, enabled: bool, stats: Stats, timing: Timing) -> None:
        self.enabled = enabled
        self.stats = stats
        self.timing = timing
        self.last_check = 0.0
        self.last_ad_check = 0.0
        self.waiting_for_reward = False
        self.crown_misses = 0
        self.last_timer_check = 0.0
        self.timer_seen_at: float | None = None  # when the boost timer was last seen, None when it wasn't there
        self.boost_started: float | None = None  # when the boost timer showed up

    def update(self, phone: Phone, frame: Frame) -> bool:
        """Takes or declines an offered boost; True when it touched the game (the frame is outdated then).

        Can be called with any frame: during storms, dialogs and popups the crown can't be seen.
        Also counts how long a boost runs (any boost, also without --boost) as 'boost seconds'.
        """
        self.watch_timer(frame)
        if not self.enabled:
            return False
        if not board.grid_visible(frame) and self.close_ad(phone):
            return True
        if board.boost_dialog_open(frame):
            # Left open (e.g. the crown was tapped by hand)
            self.answer(phone)
            return True
        if time.time() - self.last_check < CHECK_SECONDS:
            return False
        self.last_check = time.time()
        if board.crown_state(frame) != 'crown':
            return False

        left, top, right, bottom = board.CROWN_BOX
        center = ((left + right) // 2, (top + bottom) // 2)
        phone.run(actions.tap(center, ((right - left) // 4, (bottom - top) // 4), self.timing))
        if not board.boost_dialog_open(actions.wait_for(phone, board.boost_dialog_open, DIALOG_TIMEOUT)):
            print('%s tapped the crown, but no boost dialog opened' % time.strftime('%H:%M:%S'))
            self.crown_misses += 1
            if self.crown_misses >= CROWN_MISSES:
                print('%s the game ignores taps, pausing and resuming it' % time.strftime('%H:%M:%S'))
                self.stats.event('recover', reason='crown taps ignored')
                actions.recover(phone)
                self.crown_misses = 0
            return True
        self.crown_misses = 0
        self.answer(phone)
        return True

    def watch_timer(self, frame: Frame) -> None:
        now = time.time()
        if now - self.last_timer_check < TIMER_CHECK_SECONDS:
            return
        self.last_timer_check = now
        if board.grid_background(frame) != 'normal':
            return  # the corner is covered or looks different (storm, ad, dialog)
        running = board.crown_state(frame) == 'timer'
        if running and self.timer_seen_at is not None and now - self.timer_seen_at < TIMER_MAX_GAP:
            self.stats.add('boost seconds', now - self.timer_seen_at)
        elif running:
            self.boost_started = now
            self.stats.event('boost started')
        elif self.boost_started is not None:
            self.stats.event('boost ended', seconds=round(now - self.boost_started))
            self.boost_started = None
        self.timer_seen_at = now if running else None

    def close_ad(self, phone: Phone) -> bool:
        """Taps the X of an ad that has granted its reward; True when it tapped."""
        if time.time() - self.last_ad_check < AD_CHECK_SECONDS:
            return False
        self.last_ad_check = time.time()
        if not ad_in_front():
            self.waiting_for_reward = False
            return False
        shot = screenshot()
        buttons = close_buttons(shot)
        texts = [text_left_of(shot, button) for button in buttons]
        if any('reward in' in text or 'second' in text for text in texts):
            if not self.waiting_for_reward:
                print('%s ad playing, waiting for the reward' % time.strftime('%H:%M:%S'))
            self.waiting_for_reward = True
            return False
        if not buttons:
            return False
        granted = [button for button, text in zip(buttons, texts) if 'granted' in text]
        x, y = (granted or buttons)[0]
        # Screenshot pixels are screen pixels, unlike the board's calibrated positions
        phone.run(actions.tap((x, y), (4, 4), self.timing))
        print('%s closing the ad%s' % (time.strftime('%H:%M:%S'), ' (reward granted)' if granted else ''))
        self.stats.event('ad closed', granted=bool(granted))
        self.waiting_for_reward = False
        return True

    def answer(self, phone: Phone) -> None:
        """Reads the open boost dialog and taps Yes for the 10x barrels boost, No for any other."""
        time.sleep(DIALOG_FADE_SECONDS)
        # Full resolution for reading the text (the helper's frames are quarter size)
        shot = screenshot()
        text = ' '.join([hud.ocr(shot, board.BOOST_TITLE_BOX, dark=True),
                         hud.ocr(shot, board.BOOST_TEXT_BOX, dark=True, lines=2)]).replace('\n', ' ')
        if 'watch an ad' not in text.lower():
            # Not readable (still fading in / out): look again next time
            print('%s boost dialog not readable yet: "%s"' % (time.strftime('%H:%M:%S'), text))
            return
        take = wanted(text)
        self.stats.event('boost offer', name=text.split(' Watch')[0].strip(), taken=take)
        phone.run(actions.tap(board.BOOST_YES if take else board.BOOST_NO, board.BOOST_BUTTON_SPREAD, self.timing))
        if take:
            self.stats.add('boosts')
            print('%s boost "%s": taking it, watching the ad' % (time.strftime('%H:%M:%S'), text))
        else:
            self.stats.add('boosts declined')
            print('%s boost "%s": declined' % (time.strftime('%H:%M:%S'), text))
