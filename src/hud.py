"""Reads the texts at the top of the screen with OCR (tesseract): scrap, magnets and time to the next storm.

The helper's quarter size frames lose the decimal point, so the texts are read
from full resolution adb screenshots, in a background thread (see HudWatcher).
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, NamedTuple

import cv2
import numpy as np
import numpy.typing as npt
import pyocr
import pyocr.builders
from PIL import Image

from src import board
from src.board import Box, Frame
from src.phone import screenshot
from src.stats import SUFFIXES, Stats  # K, M, B, ... each 1000x the one before

READ_SECONDS = 10  # how often HudWatcher reads the texts
AFTER_STORM_SECONDS = 2  # retry interval while the storm cloud still covers the magnet count
LEVEL_UP_SECONDS = 5  # one banner can be in several screenshots (for up to ~3 s), count it once
TEXT_MIN_BRIGHTNESS = 150  # the texts are white on dark green / blue / orange
DARK_TEXT_MAX_BRIGHTNESS = 100  # dark texts on the light gray dialogs
NUMBER_CHARACTERS = '0123456789.,e+' + ''.join(sorted(set(''.join(SUFFIXES))))



class Reading(NamedTuple):
    """The texts at the top of the screen; None for each one that couldn't be read."""
    time: float  # time.time() of the screenshot
    scrap_text: str
    scrap: float | None
    magnets: float | None
    next_storm: int | None  # seconds
    level: int | None  # player level


_tool: Any = None  # the pyocr tool (pyocr has no type hints)


def ocr(frame: Frame, box: Box, characters: str | None = None, dark: bool = False, lines: int = 1) -> str:
    """Text in the box (left, top, right, bottom) of the frame.

    The text is white unless dark is set (dark text on a light dialog); lines > 1 reads a paragraph.
    """
    left, top, right, bottom = box
    gray = cv2.cvtColor(frame[top:bottom, left:right], cv2.COLOR_BGR2GRAY)
    if dark:
        text = gray < DARK_TEXT_MAX_BRIGHTNESS
    else:
        text = gray > TEXT_MIN_BRIGHTNESS
    return ocr_mask(text, characters, lines)


def ocr_mask(text: npt.NDArray[np.bool_], characters: str | None = None, lines: int = 1) -> str:
    """Text drawn by the True pixels of a boolean image."""
    global _tool
    if _tool is None:
        tools = pyocr.get_available_tools()
        if not tools:
            raise RuntimeError('tesseract not found, install it (e.g. pacman -S tesseract tesseract-data-eng)')
        # The C API is faster than starting the tesseract program every time
        _tool = next((tool for tool in tools if 'C-API' in tool.get_name()), tools[0])
    # Black text on white with a margin, which tesseract reads best
    image = cv2.copyMakeBorder(np.where(text, 0, 255).astype(np.uint8), 20, 20, 20, 20, cv2.BORDER_CONSTANT,
                               value=255)
    builder = pyocr.builders.TextBuilder(tesseract_layout=7 if lines == 1 else 6)  # a line / a block
    if characters:
        builder.tesseract_configs += ['-c', 'tessedit_char_whitelist=' + characters + ' ']
    return str(_tool.image_to_string(Image.fromarray(image), lang='eng', builder=builder)).strip()


def parse_number(text: str) -> float | None:
    """"7.215 V" -> 7.215e63, "434" -> 434.0, "7.215e63" -> 7.215e63; None when unreadable or the suffix is unknown.

    The game's "Normal" and "Scientific" number formats (settings) are understood; with
    Scientific no suffix list is needed.
    """
    scientific = re.search(r'(\d+(?:\.\d+)?)\s*e\s*\+?(\d+)', text.replace(',', ''))
    if scientific:
        return float(scientific.group(1)) * 10.0 ** int(scientific.group(2))
    # "2,072": the comma separates thousands
    match = re.search(r'(\d+(?:\.\d+)?)\s*([A-Za-z]*)', text.replace(',', ''))
    if not match:
        return None
    number, suffix = float(match.group(1)), match.group(2)
    if not suffix:
        # The game shows decimals only with an exponent / suffix: "1.269" is a misread 1.269e67
        return None if '.' in match.group(1) else number
    lower = [known.lower() for known in SUFFIXES]
    # With a suffix the number is below 1000, more means a misread (lost decimal point)
    if suffix.lower() not in lower or number >= 1000:
        return None
    return number * 1000.0 ** (lower.index(suffix.lower()) + 1)


def read(frame: Frame, taken: float | None = None) -> Reading:
    """Reads the texts of a full resolution frame; None for each one that can't be read (storm cloud...)."""
    taken = taken or time.time()
    if not board.hud_visible(frame):
        return Reading(taken, '', None, None, None, None)  # covered (level up banner)
    scrap_text = ocr(frame, board.SCRAP_BOX, NUMBER_CHARACTERS)
    magnets = parse_number(ocr(frame, board.MAGNETS_BOX, NUMBER_CHARACTERS))
    storm = re.search(r'(\d{1,2})\s*[:.]\s*(\d{2})', ocr(frame, board.STORM_BOX))
    level = re.search(r'level\s*(\d+)', ocr(frame, board.LEVEL_BOX).lower())
    return Reading(taken, scrap_text, parse_number(scrap_text), magnets,
                   int(storm.group(1)) * 60 + int(storm.group(2)) if storm else None,
                   int(level.group(1)) if level else None)


class HudWatcher(threading.Thread):
    """Takes a screenshot every READ_SECONDS, reads it and passes the values to the stats."""

    def __init__(self, stats: Stats) -> None:
        threading.Thread.__init__(self, daemon=True)
        self.stats = stats
        self.last: Reading | None = None
        self.magnets: float | None = None  # last magnet count that could be read
        self.next_storm_at: float | None = None  # time.time() of the next storm start, when known
        self.magnets_before_storm: float | None = None  # set by storm_over() until a reading shows the result
        self.storm_seconds: float | None = None  # how long that storm took
        self.wake = threading.Event()
        self.failed = False
        self.level_up_at = 0.0  # when the last "LEVEL UP!" banner was read
        self.level_up_magnets: int | None = None  # ... and the magnets it gave

    def run(self) -> None:
        while True:
            try:
                taken = time.time()
                shot = screenshot()
                if board.grid_background(shot) != 'normal':
                    # Ad, menu, dialog or storm: the numbers can't be read (or would be misread)
                    self.wake.wait(AFTER_STORM_SECONDS if self.magnets_before_storm is not None else READ_SECONDS)
                    self.wake.clear()
                    continue
                if board.level_up_banner(shot):
                    self.level_up(shot, taken)
                last = self.last = read(shot, taken)
            except Exception as error:  # the bot must keep running without stats
                if not self.failed:
                    print('could not read the texts at the top (no currency stats): %s' % error)
                self.failed = True
                self.wake.wait(READ_SECONDS)
                self.wake.clear()
                continue
            if last.magnets is not None:
                self.magnets = last.magnets
            if last.next_storm is not None:
                self.next_storm_at = taken + last.next_storm
            self.stats.hud(last)
            if self.magnets_before_storm is not None and last.magnets is not None:
                gained = last.magnets - self.magnets_before_storm
                self.magnets_before_storm = None
                self.stats.add('storm magnets', gained)
                self.stats.event('storm', seconds=round(self.storm_seconds or 0), magnets=gained)
                print('%s storm brought %+d magnets' % (time.strftime('%H:%M:%S'), gained))
            self.wake.wait(AFTER_STORM_SECONDS if self.magnets_before_storm is not None else READ_SECONDS)
            self.wake.clear()

    def level_up(self, shot: Frame, taken: float) -> None:
        """Counts the magnets of a "LEVEL UP! You got 156 Magnets!" banner.

        The same banner can be in several screenshots; a new one (the next level, which
        can come while the last one is still there) shows a different amount.
        """
        match = re.search(r'got\s*([\d,]+)\s*magnet', ocr(shot, board.LEVEL_UP_TEXT_BOX).lower())
        if not match:
            return  # fading in / out
        magnets = int(match.group(1).replace(',', ''))
        if magnets == self.level_up_magnets and taken - self.level_up_at < LEVEL_UP_SECONDS:
            return
        self.level_up_at, self.level_up_magnets = taken, magnets
        self.stats.level_up(magnets)

    def banner_seen(self) -> None:
        """The bot saw the level up banner: take a screenshot right away to read it while it's there."""
        self.wake.set()

    def storm_over(self, magnets_before: float | None, seconds: float | None = None) -> None:
        """Reads right away, to report how many magnets the storm brought."""
        self.stats.add('storms')
        self.storm_seconds = seconds
        self.magnets_before_storm = magnets_before
        self.wake.set()

    def seconds_to_storm(self) -> float | None:
        """Seconds until the next magnet storm, None when unknown."""
        return None if self.next_storm_at is None else self.next_storm_at - time.time()
