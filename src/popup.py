"""Popups: closes the game's messages that only need an "Ok" ("Team Challenge: Task finished!",
"Offline Production"...), which would otherwise cover the board until someone taps it.

Once the board has been covered for a few seconds (and no ad is in front), the blue
buttons on the screen are found by their color and their labels read; only one that
says "Ok" gets tapped. Other blue buttons ("Upgrade", "Restart Game") are left alone.
"""
from __future__ import annotations

import time

import cv2
import numpy as np

from src import actions, board, hud
from src.actions import Timing
from src.board import Frame, Point
from src.phone import Phone, ad_in_front, screenshot
from src.stats import Stats

COVERED_SECONDS = 3  # the board covered this long: look for a popup (menus and dialogs of the bot close sooner)
CHECK_SECONDS = 3  # how often to look meanwhile (a full screenshot takes ~1.3 s)
# Size of a button, as a fraction of the screen (the "Ok" button: 391x158 on 1220x2712)
BUTTON_WIDTH = (0.2, 0.65)
BUTTON_HEIGHT = (0.035, 0.1)


def ok_buttons(shot: Frame) -> list[Point]:
    """Centers of the blue buttons labeled "Ok" in a full resolution screenshot."""
    height, width = shot.shape[:2]
    pixels = shot.astype(int)
    blue, green, red = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
    mask = ((blue > 150) & (blue > red + 90) & (blue > green + 40)).astype(np.uint8)
    count, _, components, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    buttons = []
    for x, y, button_width, button_height, area in components[1:count]:
        if not (BUTTON_WIDTH[0] * width < button_width < BUTTON_WIDTH[1] * width
                and BUTTON_HEIGHT[0] * height < button_height < BUTTON_HEIGHT[1] * height
                and area > 0.6 * button_width * button_height):
            continue
        label = hud.ocr(shot, (int(x), int(y), int(x + button_width), int(y + button_height)))
        if label.lower().strip(' .!|') == 'ok':
            buttons.append((int(x + button_width // 2), int(y + button_height // 2)))
    return buttons


class PopupCloser:
    def __init__(self, enabled: bool, stats: Stats, timing: Timing) -> None:
        self.enabled = enabled
        self.stats = stats
        self.timing = timing
        self.covered_since: float | None = None
        self.last_check = 0.0

    def update(self, phone: Phone, frame: Frame) -> bool:
        """Taps the "Ok" of a popup covering the board; True when it tapped (the frame is outdated then)."""
        if not self.enabled:
            return False
        now = time.time()
        if board.grid_visible(frame):
            self.covered_since = None
            return False
        if self.covered_since is None:
            self.covered_since = now
        if now - self.covered_since < COVERED_SECONDS or now - self.last_check < CHECK_SECONDS:
            return False
        self.last_check = now
        if ad_in_front():
            return False
        buttons = ok_buttons(screenshot())
        if not buttons:
            return False
        # Screenshot pixels are screen pixels, unlike the board's calibrated positions
        phone.run(actions.tap(buttons[0], (20, 10), self.timing))
        print('%s closed a popup ("Ok")' % time.strftime('%H:%M:%S'))
        self.stats.event('popup closed')
        self.covered_since = None
        return True
