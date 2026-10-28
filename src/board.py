"""Where things are on the screen and what the board looks like.

All positions are screen pixels of a 1220x2712 screen; other phones are adapted
by calibration.json (see calibrate.py). Other modules read these values as
board.NAME at call time, so the calibration applies everywhere.
"""
from __future__ import annotations

import json
import os
import random
from collections.abc import Sequence
from typing import Any, Literal

import cv2
import cv2.typing
import numpy as np
import numpy.typing as npt

Point = tuple[int, int]  # (x, y) screen pixels
Size = tuple[int, int]  # (width, height), or a random spread (x, y) around a point
Box = tuple[int, int, int, int]  # (left, top, right, bottom)
Frame = cv2.typing.MatLike  # a BGR picture of the screen, SCREEN_SIZE
Signature = npt.NDArray[np.float64]  # downscaled sample of a barrel (see signatures())
Color = npt.NDArray[np.float64]  # mean (blue, green, red)
Background = Literal['normal', 'storm']

SCREEN_SIZE: Size = (1220, 2712)
FRAME_SIZE: Size = (305, 678)  # screen is grabbed at quarter size, plenty for telling barrels apart

# Grid geometry
COLUMNS_X = [189, 473, 755, 1039]
FIRST_ROW_TOP = 441
ROW_STEP = 383
ROWS = 5

# Part of the barrel body that gets sampled, relative to the slot's column
# center / row top. Skips the "+1.397 OD" label at the top of the slot.
SAMPLE_TOP = 120
SAMPLE_BOTTOM = 270
SAMPLE_HALF_WIDTH = 55
TOUCH_OFFSET_Y = 190

# Max random offset (x, y) from the barrel center where a drag starts / ends
START_SPREAD = (40, 60)
END_SPREAD = (10, 15)  # small: the drop must land on the target barrel

# Background between the barrels: normal, and darkened during a magnet storm (no merging then)
BACKGROUNDS_BGR: dict[Background, npt.NDArray[np.int_]] = {'normal': np.array([173, 208, 171]),
                                                            'storm': np.array([52, 62, 50])}
GAP_X = [331, 614, 897]  # background between columns, used to check the grid is visible
GAP_MAX_COVERED = 5  # gap points that may be covered (falling storm magnets) with the grid still visible

EMPTY_MAX_STD = 6  # empty slot = flat gray silhouette or plain background
SIGNATURE_SIZE = (6, 8)  # (width, height) of the downscaled sample

# "Convert Barrel?" dialog: double tap on a barrel, then "Yes" converts it into Star Fragments
CONVERT_DIALOG_BODY = (610, 1464)  # plain gray part of the dialog
# Where the Yes button overlaps the barrel in row 3, column 2: without the dialog a
# tap there is just a harmless single tap on that barrel
# (Higher up, taps hit the barrel's "+1.38 ND" label instead of the barrel.)
CONVERT_YES = (730, 1728)
CONVERT_YES_SPREAD = (25, 12)
CONVERT_YES_CHECK = (690, 1700)  # plain green part of the Yes button (the label is white)
CONVERT_NO_CHECK = (210, 1700)  # plain red part of the No button
# Red X box top right of the dialog. Both dialogs have their buttons in about the same
# place, but the X box of each is at a height the other one's doesn't reach
CONVERT_CLOSE_CHECK = (1035, 1005)
OFFLINE_POPUP_OK = (610, 1770)  # "Ok" of the "Offline Production" popup shown after resuming

# Texts at the top, read with OCR (see hud.py): (left, top, right, bottom)
SCRAP_BOX = (150, 205, 510, 290)
MAGNETS_BOX = (690, 205, 1010, 290)
STORM_BOX = (600, 320, 1215, 380)  # "Next Storm in: 02:39"
LEVEL_BOX = (200, 325, 600, 380)  # "Level 27"
# Plain dark green parts of the scrap and magnet boxes; a level up covers the whole top bar
# with a gray "LEVEL UP!" banner for ~1.5 s
HUD_BACKGROUND_CHECKS = [(485, 225), (985, 225)]
LEVEL_UP_TEXT_BOX = (100, 320, 1120, 410)  # "You got 156 Magnets!" on the banner

# Boosts (see boost.py): blue square top right with a crown (boost on offer) or a timer (boost running)
CROWN_BOX = (1080, 178, 1210, 308)
# "Event!" dialog opened by the crown: "Faster Barrels / Watch an ad to receive ..."
BOOST_TITLE_BOX = (250, 1260, 970, 1370)
BOOST_TEXT_BOX = (150, 1370, 1070, 1590)  # up to three lines
BOOST_DIALOG_BODY = (610, 1600)  # plain gray part
BOOST_NO = (368, 1712)  # plain red part of the No button
BOOST_YES = (850, 1712)  # plain green part of the Yes button
BOOST_CLOSE_CHECK = (1035, 805)  # red X box (see CONVERT_CLOSE_CHECK)
BOOST_BUTTON_SPREAD = (120, 25)

# "Upgrades" menu, opened by the arrow at the bottom; its first tab (scrap upgrades) lists "Better Barrels" second
UPGRADES_ARROW = (757, 2450)
UPGRADES_ARROW_SPREAD = (30, 30)
UPGRADES_CLOSE = (1090, 595)  # X top right
UPGRADES_CLOSE_CHECK = (1045, 560)  # plain red part of the X box
# Plain gray parts of the title bar, also where the board shows its background between the
# columns (barrels can look like anything, the board background never looks gray)
UPGRADES_TITLE_CHECKS = [(150, 600), (331, 600), (897, 600)]
UPGRADES_FIRST_TAB = (245, 730)
UPGRADES_FIRST_TAB_CHECK = (90, 730)  # tab background: light gray when selected, dark gray otherwise
UPGRADES_SECOND_TAB_CHECK = (480, 730)
BETTER_BARRELS_NAME_BOX = (230, 985, 830, 1045)
BETTER_BARRELS_LEVEL_BOX = (60, 1090, 225, 1150)  # "80/999"
BETTER_BARRELS_PRICE_BOX = (900, 1012, 1155, 1112)  # "9.807e61" (or "MAX")
BETTER_BARRELS_BUTTON = (995, 1062)
BETTER_BARRELS_BUTTON_SPREAD = (110, 30)
BETTER_BARRELS_BUTTON_CHECK = (865, 1005)  # plain part of the button: green when affordable, gray otherwise

# "Prestige" menu, opened by the golden barrel left of the arrow (same frame as the upgrades menu);
# its 4th tab upgrades the brick production, which costs the player level (reset to 1)
PRESTIGE_BUTTON = (455, 2455)
PRESTIGE_TABS = [(245, 742), (470, 742), (742, 742), (1020, 742)]  # golden scrap, stars, scrapyard, bricks
PRESTIGE_TAB_CHECKS = [(90, 700), (330, 700), (630, 700), (1150, 700)]  # background: light gray when selected
# The page heading ("Bricks", "Scrapyard v2"): the tabs have buttons in the same places, and one
# of them restarts the game (prestige), so the page is always checked before tapping
PRESTIGE_HEADING_BOX = (250, 840, 970, 930)
BRICKS_TAB = 3
BRICKS_UPGRADE = (607, 1980)  # blue "Upgrade" button
BRICKS_UPGRADE_CHECK = (330, 1940)
BRICKS_UPGRADE_SPREAD = (150, 30)
BRICKS_BADGE_CHECK = (912, 1905)  # red "1" badge on the button: the upgrade is available
# The dialog it opens: "Level 18" (bricks), production before -> after, "You need: Level 20"
BRICKS_DIALOG_CLOSE = (1090, 787)
BRICKS_DIALOG_CLOSE_CHECK = (1045, 770)  # plain red part of its X box
BRICKS_DIALOG_TITLE_CHECKS = [(331, 770), (897, 770)]  # its gray title bar (see UPGRADES_TITLE_CHECKS)
BRICKS_DIALOG_TITLE_BOX = (250, 740, 960, 830)
BRICKS_PRODUCTION_BOX = (100, 930, 1120, 1040)
BRICKS_REQUIRED_BOX = (290, 1295, 945, 1355)
BRICKS_NEED_BOX = (300, 1135, 920, 1215)  # "You need:", tells the dialog apart
# "Your level will be reset to 1!", only there when the level is high enough (else "Currently: Level 5")
BRICKS_WARNING_BOX = (100, 1500, 1120, 1600)
BRICKS_CONFIRM = (607, 1800)  # blue "Upgrade" button of the dialog
BRICKS_CONFIRM_CHECK = (330, 1740)

# Scrapyard (3rd prestige tab): each tap on the scrapyard costs scrap, 10 taps make a level
SCRAPYARD_TAB = 2
SCRAPYARD_PILE = (607, 1500)
SCRAPYARD_PILE_SPREAD = (150, 110)
SCRAPYARD_LEVEL_BOX = (380, 975, 800, 1060)  # "Level 135"
SCRAPYARD_TAPS_BOX = (150, 1985, 1070, 2060)  # "Click 7 times to level up!"
SCRAPYARD_INFO = (882, 1015)  # "i" button: a dialog with the price of a tap
SCRAPYARD_INFO_CLOSE = (1072, 975)
SCRAPYARD_INFO_CLOSE_CHECK = (1030, 950)  # plain red part of its X box
SCRAPYARD_INFO_TITLE_CHECKS = [(331, 950), (897, 950)]  # its gray title bar (see UPGRADES_TITLE_CHECKS)
SCRAPYARD_PRICE_BOX = (440, 1690, 800, 1765)  # "= 1.104e71"

# Written by calibrate.py for phones with another screen; SCRAP_CALIBRATION picks another file
# ('' = none, use the 1220x2712 values above)
CALIBRATION_FILE = os.environ.get('SCRAP_CALIBRATION', os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'calibration.json'))


def apply_calibration(calibration: dict[str, Any]) -> None:
    """Adapts the 1220x2712 coordinates above to the phone described by calibration.

    calibrate.py measures the screen size, the grid and the board background;
    everything else (sample area, dialog buttons, texts...) is scaled along with the grid.
    """
    global SCREEN_SIZE, FRAME_SIZE, COLUMNS_X, FIRST_ROW_TOP, ROW_STEP, SAMPLE_TOP, SAMPLE_BOTTOM, \
        SAMPLE_HALF_WIDTH, TOUCH_OFFSET_Y, START_SPREAD, END_SPREAD, BACKGROUNDS_BGR, GAP_X, \
        CONVERT_DIALOG_BODY, CONVERT_YES, CONVERT_YES_SPREAD, CONVERT_YES_CHECK, CONVERT_NO_CHECK, \
        OFFLINE_POPUP_OK, SCRAP_BOX, MAGNETS_BOX, STORM_BOX, LEVEL_BOX, HUD_BACKGROUND_CHECKS, CROWN_BOX, BOOST_TITLE_BOX, BOOST_TEXT_BOX, \
        BOOST_DIALOG_BODY, BOOST_NO, BOOST_YES, BOOST_BUTTON_SPREAD, CONVERT_CLOSE_CHECK, BOOST_CLOSE_CHECK, \
        UPGRADES_ARROW, UPGRADES_ARROW_SPREAD, UPGRADES_CLOSE, UPGRADES_CLOSE_CHECK, UPGRADES_TITLE_CHECKS, \
        UPGRADES_FIRST_TAB, UPGRADES_FIRST_TAB_CHECK, UPGRADES_SECOND_TAB_CHECK, BETTER_BARRELS_NAME_BOX, \
        BETTER_BARRELS_LEVEL_BOX, BETTER_BARRELS_PRICE_BOX, BETTER_BARRELS_BUTTON, BETTER_BARRELS_BUTTON_SPREAD, \
        BETTER_BARRELS_BUTTON_CHECK, PRESTIGE_BUTTON, PRESTIGE_TABS, PRESTIGE_TAB_CHECKS, PRESTIGE_HEADING_BOX, \
        BRICKS_UPGRADE, BRICKS_UPGRADE_CHECK, BRICKS_UPGRADE_SPREAD, BRICKS_BADGE_CHECK, BRICKS_DIALOG_CLOSE, BRICKS_DIALOG_CLOSE_CHECK, \
        BRICKS_DIALOG_TITLE_BOX, BRICKS_PRODUCTION_BOX, BRICKS_REQUIRED_BOX, BRICKS_CONFIRM, BRICKS_CONFIRM_CHECK, \
        LEVEL_UP_TEXT_BOX, BRICKS_WARNING_BOX, BRICKS_NEED_BOX, SCRAPYARD_PILE, SCRAPYARD_PILE_SPREAD, \
        SCRAPYARD_LEVEL_BOX, SCRAPYARD_TAPS_BOX, SCRAPYARD_INFO, SCRAPYARD_INFO_CLOSE, SCRAPYARD_INFO_CLOSE_CHECK, \
        SCRAPYARD_PRICE_BOX, BRICKS_DIALOG_TITLE_CHECKS, SCRAPYARD_INFO_TITLE_CHECKS
    columns_x = [int(x) for x in calibration['columns_x']]
    first_row_top = int(calibration['first_row_top'])
    scale_x = (columns_x[-1] - columns_x[0]) / (COLUMNS_X[-1] - COLUMNS_X[0])
    scale_y = calibration['row_step'] / ROW_STEP

    def point(position: Sequence[int]) -> Point:
        return (round(columns_x[0] + (position[0] - COLUMNS_X[0]) * scale_x),
                round(first_row_top + (position[1] - FIRST_ROW_TOP) * scale_y))

    def size(spread: Size) -> Size:
        return round(spread[0] * scale_x), round(spread[1] * scale_y)

    def box(corners: Box) -> Box:
        return point(corners[:2]) + point(corners[2:])

    CONVERT_DIALOG_BODY, CONVERT_YES, CONVERT_YES_CHECK, CONVERT_NO_CHECK, CONVERT_CLOSE_CHECK, OFFLINE_POPUP_OK, \
        BOOST_DIALOG_BODY, BOOST_NO, BOOST_YES, BOOST_CLOSE_CHECK = map(
            point, (CONVERT_DIALOG_BODY, CONVERT_YES, CONVERT_YES_CHECK, CONVERT_NO_CHECK, CONVERT_CLOSE_CHECK,
                    OFFLINE_POPUP_OK, BOOST_DIALOG_BODY, BOOST_NO, BOOST_YES, BOOST_CLOSE_CHECK))
    SCRAP_BOX, MAGNETS_BOX, STORM_BOX, LEVEL_BOX, CROWN_BOX, BOOST_TITLE_BOX, BOOST_TEXT_BOX = map(
        box, (SCRAP_BOX, MAGNETS_BOX, STORM_BOX, LEVEL_BOX, CROWN_BOX, BOOST_TITLE_BOX, BOOST_TEXT_BOX))
    HUD_BACKGROUND_CHECKS = [point(check) for check in HUD_BACKGROUND_CHECKS]
    UPGRADES_ARROW, UPGRADES_CLOSE, UPGRADES_CLOSE_CHECK, UPGRADES_FIRST_TAB, \
        UPGRADES_FIRST_TAB_CHECK, UPGRADES_SECOND_TAB_CHECK, BETTER_BARRELS_BUTTON, BETTER_BARRELS_BUTTON_CHECK = map(
            point, (UPGRADES_ARROW, UPGRADES_CLOSE, UPGRADES_CLOSE_CHECK, UPGRADES_FIRST_TAB,
                    UPGRADES_FIRST_TAB_CHECK, UPGRADES_SECOND_TAB_CHECK, BETTER_BARRELS_BUTTON,
                    BETTER_BARRELS_BUTTON_CHECK))
    UPGRADES_TITLE_CHECKS, BRICKS_DIALOG_TITLE_CHECKS, SCRAPYARD_INFO_TITLE_CHECKS = (
        [point(check) for check in checks]
        for checks in (UPGRADES_TITLE_CHECKS, BRICKS_DIALOG_TITLE_CHECKS, SCRAPYARD_INFO_TITLE_CHECKS))
    BETTER_BARRELS_NAME_BOX, BETTER_BARRELS_LEVEL_BOX, BETTER_BARRELS_PRICE_BOX = map(
        box, (BETTER_BARRELS_NAME_BOX, BETTER_BARRELS_LEVEL_BOX, BETTER_BARRELS_PRICE_BOX))
    UPGRADES_ARROW_SPREAD, BETTER_BARRELS_BUTTON_SPREAD = map(size, (UPGRADES_ARROW_SPREAD, BETTER_BARRELS_BUTTON_SPREAD))
    PRESTIGE_BUTTON, BRICKS_UPGRADE, BRICKS_UPGRADE_CHECK, BRICKS_BADGE_CHECK, BRICKS_DIALOG_CLOSE, \
        BRICKS_DIALOG_CLOSE_CHECK, BRICKS_CONFIRM, BRICKS_CONFIRM_CHECK, SCRAPYARD_PILE, SCRAPYARD_INFO, \
        SCRAPYARD_INFO_CLOSE, SCRAPYARD_INFO_CLOSE_CHECK = map(
            point, (PRESTIGE_BUTTON, BRICKS_UPGRADE, BRICKS_UPGRADE_CHECK, BRICKS_BADGE_CHECK, BRICKS_DIALOG_CLOSE,
                    BRICKS_DIALOG_CLOSE_CHECK, BRICKS_CONFIRM, BRICKS_CONFIRM_CHECK, SCRAPYARD_PILE, SCRAPYARD_INFO,
                    SCRAPYARD_INFO_CLOSE, SCRAPYARD_INFO_CLOSE_CHECK))
    PRESTIGE_TABS = [point(tab) for tab in PRESTIGE_TABS]
    PRESTIGE_TAB_CHECKS = [point(check) for check in PRESTIGE_TAB_CHECKS]
    SCRAPYARD_PILE_SPREAD = size(SCRAPYARD_PILE_SPREAD)
    BRICKS_DIALOG_TITLE_BOX, BRICKS_PRODUCTION_BOX, BRICKS_REQUIRED_BOX, LEVEL_UP_TEXT_BOX, PRESTIGE_HEADING_BOX, \
        BRICKS_WARNING_BOX, BRICKS_NEED_BOX, SCRAPYARD_LEVEL_BOX, SCRAPYARD_TAPS_BOX, SCRAPYARD_PRICE_BOX = map(
            box, (BRICKS_DIALOG_TITLE_BOX, BRICKS_PRODUCTION_BOX, BRICKS_REQUIRED_BOX, LEVEL_UP_TEXT_BOX,
                  PRESTIGE_HEADING_BOX, BRICKS_WARNING_BOX, BRICKS_NEED_BOX, SCRAPYARD_LEVEL_BOX, SCRAPYARD_TAPS_BOX,
                  SCRAPYARD_PRICE_BOX))
    BRICKS_UPGRADE_SPREAD = size(BRICKS_UPGRADE_SPREAD)
    GAP_X = [point((x, 0))[0] for x in GAP_X]
    START_SPREAD, END_SPREAD, CONVERT_YES_SPREAD, BOOST_BUTTON_SPREAD = map(
        size, (START_SPREAD, END_SPREAD, CONVERT_YES_SPREAD, BOOST_BUTTON_SPREAD))
    SAMPLE_HALF_WIDTH = round(SAMPLE_HALF_WIDTH * scale_x)
    SAMPLE_TOP, SAMPLE_BOTTOM, TOUCH_OFFSET_Y = (round(y * scale_y) for y in (SAMPLE_TOP, SAMPLE_BOTTOM, TOUCH_OFFSET_Y))
    # The storm darkens the board background by the same factor on every screen
    normal = np.array(calibration['background_bgr'], dtype=float)
    storm = normal * BACKGROUNDS_BGR['storm'] / BACKGROUNDS_BGR['normal']
    BACKGROUNDS_BGR = {'normal': normal.round().astype(int), 'storm': storm.round().astype(int)}
    SCREEN_SIZE = (int(calibration['screen_size'][0]), int(calibration['screen_size'][1]))
    FRAME_SIZE = (SCREEN_SIZE[0] // 4, SCREEN_SIZE[1] // 4)
    COLUMNS_X, FIRST_ROW_TOP, ROW_STEP = columns_x, first_row_top, int(calibration['row_step'])


if CALIBRATION_FILE and os.path.exists(CALIBRATION_FILE):
    with open(CALIBRATION_FILE) as calibration_file:
        apply_calibration(json.load(calibration_file))


def slot_count() -> int:
    return len(COLUMNS_X) * ROWS


def slot_center(slot: int) -> Point:
    row, column = divmod(slot, len(COLUMNS_X))
    return COLUMNS_X[column], FIRST_ROW_TOP + ROW_STEP * row + TOUCH_OFFSET_Y


def random_point(slot: int, spread: Size) -> Point:
    x, y = slot_center(slot)
    return x + random.randint(-spread[0], spread[0]), y + random.randint(-spread[1], spread[1])


def slot_sample(frame: Frame, slot: int) -> Frame:
    row, column = divmod(slot, len(COLUMNS_X))
    x = COLUMNS_X[column]
    top = FIRST_ROW_TOP + ROW_STEP * row
    return frame[top + SAMPLE_TOP:top + SAMPLE_BOTTOM, x - SAMPLE_HALF_WIDTH:x + SAMPLE_HALF_WIDTH]


def grid_background(frame: Frame) -> Background | None:
    """Returns 'normal' or 'storm' depending on the board's background, None when the grid is covered.

    When a popup/ad/menu covers the grid the gaps between barrels stop being background.
    """
    gaps = np.array([frame[FIRST_ROW_TOP + ROW_STEP * row + TOUCH_OFFSET_Y, x]
                     for row in range(ROWS) for x in GAP_X], dtype=int)
    for name, color in BACKGROUNDS_BGR.items():
        matches = (np.abs(gaps - color).sum(axis=1) < 30).sum()
        if matches >= len(gaps) - GAP_MAX_COVERED:
            return name
    return None


def grid_visible(frame: Frame) -> bool:
    return grid_background(frame) is not None


def signatures(frame: Frame) -> list[Signature | None]:
    """Returns a downscaled sample per slot, or None for empty slots."""
    result: list[Signature | None] = []
    for slot in range(slot_count()):
        sample = slot_sample(frame, slot)
        if sample.std() < EMPTY_MAX_STD:
            result.append(None)
        else:
            result.append(cv2.resize(sample, SIGNATURE_SIZE, interpolation=cv2.INTER_AREA).astype(float))
    return result


def difference(a: Signature, b: Signature) -> float:
    return float(np.abs(a - b).mean())


def area_color(frame: Frame, point: Point) -> Color:
    x, y = point
    return np.asarray(frame[y - 10:y + 10, x - 10:x + 10].reshape(-1, 3).mean(axis=0), dtype=np.float64)


def dialog_open(frame: Frame, body: Point, yes: Point, no: Point, close: Point) -> bool:
    """A gray dialog with a green Yes and a red No button and a red X box at these points."""
    body_color, yes_color, no_color, close_color = (area_color(frame, point) for point in (body, yes, no, close))
    return bool((np.abs(body_color - 190) < 20).all() and yes_color[1] > yes_color[2] + 60
                and no_color[2] > no_color[1] + 80 and close_color[2] > close_color[1] + 80)


def convert_dialog_open(frame: Frame) -> bool:
    return dialog_open(frame, CONVERT_DIALOG_BODY, CONVERT_YES_CHECK, CONVERT_NO_CHECK, CONVERT_CLOSE_CHECK)


def boost_dialog_open(frame: Frame) -> bool:
    return dialog_open(frame, BOOST_DIALOG_BODY, BOOST_YES, BOOST_NO, BOOST_CLOSE_CHECK)


def hud_visible(frame: Frame) -> bool:
    """The texts at the top can be read: their boxes are dark green, not covered (level up banner...)."""
    for check in HUD_BACKGROUND_CHECKS:
        blue, green, red = area_color(frame, check)
        if not (green > red + 25 and green > blue + 25):
            return False
    return True


def level_up_banner(frame: Frame) -> bool:
    """The gray "LEVEL UP!" banner covers the texts at the top."""
    for check in HUD_BACKGROUND_CHECKS:
        color = area_color(frame, check)
        # Gray (the banner: dark gray, or its light text), not the darkened top bar behind a menu
        if color.max() - color.min() > 15 or color.mean() < 40:
            return False
    return True


def blue_button(frame: Frame, check: Point) -> bool:
    blue, green, red = area_color(frame, check)
    return bool(blue > red + 100 and blue > green + 50)


def bricks_available(frame: Frame) -> bool:
    """The red badge on the brick "Upgrade" button."""
    blue, green, red = area_color(frame, BRICKS_BADGE_CHECK)
    return bool(red > green + 100 and red > blue + 100)


def prestige_tab(frame: Frame) -> int | None:
    """Which tab of the prestige menu is selected (0-3): the one lighter than the others; None if unclear."""
    brightness = [area_color(frame, check).mean() for check in PRESTIGE_TAB_CHECKS]
    selected = max(range(len(brightness)), key=brightness.__getitem__)
    others = [value for index, value in enumerate(brightness) if index != selected]
    return selected if brightness[selected] > max(others) + 30 else None


def scrapyard_info_open(frame: Frame) -> bool:
    """The scrapyard's info dialog (price of a tap): its red X box."""
    close = area_color(frame, SCRAPYARD_INFO_CLOSE_CHECK)
    return bool(close[2] > close[1] + 80) and gray_bar(frame, SCRAPYARD_INFO_TITLE_CHECKS)


def bricks_dialog_open(frame: Frame) -> bool:
    """The brick upgrade dialog: a red X box over the prestige menu's tabs, and its blue button."""
    close = area_color(frame, BRICKS_DIALOG_CLOSE_CHECK)
    return bool(close[2] > close[1] + 80) and blue_button(frame, BRICKS_CONFIRM_CHECK) \
        and gray_bar(frame, BRICKS_DIALOG_TITLE_CHECKS)


def gray_bar(frame: Frame, checks: Sequence[Point]) -> bool:
    """A plain gray title bar at all the points (menus and dialogs; the board background is green there)."""
    for check in checks:
        color = area_color(frame, check)
        if color.max() - color.min() > 12 or not 85 < color.mean() < 135:
            return False
    return True


def upgrades_open(frame: Frame) -> bool:
    """The "Upgrades" (or "Prestige") menu: gray title bar with the red X box."""
    close = area_color(frame, UPGRADES_CLOSE_CHECK)
    return bool(close[2] > close[1] + 80) and gray_bar(frame, UPGRADES_TITLE_CHECKS)


def upgrades_first_tab(frame: Frame) -> bool:
    """The first tab (scrap upgrades) is selected: its background is lighter than the next one's."""
    return bool(area_color(frame, UPGRADES_FIRST_TAB_CHECK).mean() > area_color(frame, UPGRADES_SECOND_TAB_CHECK).mean() + 30)


def better_barrels_affordable(frame: Frame) -> bool:
    """The Better Barrels upgrade button is green (gray when there isn't enough scrap)."""
    blue, green, red = area_color(frame, BETTER_BARRELS_BUTTON_CHECK)
    return bool(green > red + 60 and green > blue + 60)


def crown_state(frame: Frame) -> Literal['crown', 'timer'] | None:
    """'crown' (boost on offer), 'timer' (boost running) or None (no blue square) top right."""
    left, top, right, bottom = CROWN_BOX
    area = frame[top:bottom, left:right].astype(int)
    blue, green, red = area[:, :, 0], area[:, :, 1], area[:, :, 2]
    if ((blue > red + 80) & (blue > 150)).mean() < 0.3:
        return None
    return 'crown' if ((red > 200) & (green > 200) & (blue < 140)).mean() > 0.1 else 'timer'
