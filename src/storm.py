"""Magnet storms: for ~20 s magnets fall over the darkened board; touching them collects them.

Every magnet passes the top row, so four fingers circling over the top row
barrels (one per column) catch most of them. Merging isn't possible meanwhile.
"""
from __future__ import annotations

import math
import random
import time

from src import board
from src.hud import HudWatcher
from src.phone import Phone

STEP_MS = 16  # one move of all fingers per frame (60 Hz)
CHUNK_STEPS = 20  # moves sent at once (~0.3 s), then check whether the storm is still on
SPEED = (0.2, 0.4)  # radians per step of a finger's circle
# Size of each finger's circle, as a fraction of the column / row step: wide enough
# to also sweep most of the gap between the columns
RADIUS = (0.35, 0.2)


def collect(phone: Phone, watcher: HudWatcher | None = None) -> float:
    """Keeps a finger circling over each top row barrel until the storm is over; returns how long it took."""
    columns = len(board.COLUMNS_X)
    radius_x = (board.COLUMNS_X[1] - board.COLUMNS_X[0]) * RADIUS[0]
    radius_y = board.ROW_STEP * RADIUS[1]
    centers = [board.slot_center(column) for column in range(columns)]
    angles = [random.uniform(0, 2 * math.pi) for _ in range(columns)]
    speeds = [random.uniform(*SPEED) * random.choice((-1, 1)) for _ in range(columns)]

    def fingers() -> str:
        return ' '.join('%d %d %d' % (finger, x + radius_x * math.cos(angle), y + radius_y * math.sin(angle))
                        for finger, ((x, y), angle) in enumerate(zip(centers, angles)))

    magnets_before = watcher.magnets if watcher else None
    start = time.time()
    phone.run(['d ' + fingers()])
    try:
        while True:
            commands = []
            for _ in range(CHUNK_STEPS):
                for finger in range(columns):
                    angles[finger] += speeds[finger] * random.uniform(0.7, 1.3)
                commands += ['m ' + fingers(), 's %d' % STEP_MS]
            phone.run(commands)
            if board.grid_background(phone.grab()) != 'storm':
                break
    finally:
        phone.run(['u ' + fingers()])
    seconds = time.time() - start
    if watcher:
        watcher.storm_over(magnets_before, seconds)
    return seconds
