# Scrap Clicker 2 bots

Automation for [Scrap Clicker 2](https://play.google.com/store/apps/details?id=com.scrap.clicker.android),
controlled from a PC over USB (adb). No root needed.

- **`simple_merge.py`** merges barrels that look the same.
- **`star_farm.py`** converts barrels into Star Fragments, over and over.
- **`cascade_merge.py`** is the 2018 merger (`merge-barrels*.py`) working again.
- **`calibrate.py`** adapts all of them to your phone's screen.
- **`runs.py`** shows the [stats](#stats) of past runs.

All of them collect the magnets of [magnet storms](#magnet-storms), can take the
[10x barrels boost](#boosts), can [buy Better Barrels, level the scrapyard and upgrade bricks](#upgrades) and track scrap and magnets for the [stats](#stats).

This is a fork of [Arhimenrius/scrap-clicker-bot](https://github.com/Arhimenrius/scrap-clicker-bot);
see [Original bot](#original-bot).

> Automating the game is probably against its terms of service. Use at your own risk.

## Requirements

- Linux PC with Python 3.11 or newer, `adb`, a JDK (`javac`) and [tesseract](https://github.com/tesseract-ocr/tesseract)
  with English data (`pacman -S tesseract tesseract-data-eng`, `apt install tesseract-ocr`), used to
  read scrap, magnets and the storm timer. Without it the bots still run, just without those stats
- Android SDK with build-tools (`d8`) and a platform (`android.jar`), used once to build the
  phone helper. Found via `ANDROID_HOME` / `ANDROID_SDK_ROOT`, default `/opt/android-sdk`
- Android phone with USB debugging enabled, connected over USB, game open
- Made on a 1220x2712 screen; on other screens run `calibrate.py` first
  (see [Other phones](#other-phones))

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install opencv-python numpy pillow pyocr
adb devices          # the phone should be listed as "device"
```

OpenCV reads the screen, numpy does the math on it, Pillow and pyocr pass the texts to
tesseract.

The phone helper (`injector/injector.dex`) is built and pushed to the phone automatically on
the first run. To build it by hand: `injector/build.sh`.

## simple_merge.py: merging

```sh
.venv/bin/python simple_merge.py                           # merge
.venv/bin/python simple_merge.py --fast                    # during a spawn boost
.venv/bin/python simple_merge.py --fast --delete-overflow  # also clear a stuck board
.venv/bin/python simple_merge.py --dry-run --once --debug-image debug.png   # look, don't touch
```

Every round it looks at the 20 slots, groups barrels that look alike and drags pairs onto
each other. Drags start and end at random points, follow a slight curve and have random
timing.

| Option | |
|---|---|
| `--fast` | Short drags (30-50 ms) and pauses, for spawn boosts: ~6 merges/s with the 10x boost. The drags slow down into the target: the game draws the dragged barrel trailing the finger and drops it where the barrel is, so drags ending at full speed missed (~4%, nearly all long ones upwards); now none of 836 did |
| `--delete-overflow` | When the board is full and nothing can be merged (10 checks, at least 3 s), convert **all** barrels into Star Fragments. Without this flag nothing is ever deleted |
| `--screen-off` | Switch the display off while the bot runs, see [Screen off](#screen-off) |
| `--no-storm` | Just wait during magnet storms instead of collecting the magnets |
| `--boost` | Take the 10x barrels boost whenever it is offered, see [Boosts](#boosts) |
| `--upgrade` | Buy Better Barrels and tap the scrapyard whenever there's enough scrap, upgrade bricks whenever the level allows, see [Upgrades](#upgrades) |
| `--swipe-ms 150-300` | Drag duration range in ms (default 150-300, with `--fast` 40-80) |
| `--pause 0.1-0.4` | Pause between rounds in seconds (default 0.1-0.4, with `--fast` 0-0.05) |
| `--threshold 10` | How different two barrels may look and still count as the same |
| `--dry-run` | Only print what would be merged |
| `--once` | One round, then exit |
| `--debug` | Log every failed merge with its details, see `runs.py details` |
| `--debug-image FILE` | Save each round's frame with the detected slots and groups drawn on it |

What it handles by itself:

- **Barrels still animating** (dropping in, being merged) are left alone until they settle.
- **Barrels that don't merge** (max level) are skipped for 60 s.
- **Magnet storms**: merging isn't possible then, so it collects the magnets meanwhile.
- **Popups**: messages that only need an "Ok" ("Team Challenge: Task finished!", "Offline
  Production") are closed after 3 s: the blue buttons on the screen are found and their labels
  read, and only one saying "Ok" is tapped. For anything else (other screens, menus) it waits
  and tells you every 15 s.
- **The game ignoring drags or all touches** (happens now and then, e.g. after a finger was
  left down): several merges failing at once (or the boost crown not reacting to taps) makes
  it pause and resume the game through the Recents screen, which fixes it without
  restarting the game. The phone helper lifts any finger still down when the bot stops,
  however it stops.
- **Merge animations**: the two slots of a drag are left alone for 0.35 s, until the merge
  shows, so no barrel gets dropped on one that is still changing.
- **`--debug`** logs every failed merge with the slots, the drag's place in its batch,
  distance and direction (`runs.py details`), to find out why the game missed them.

## star_farm.py: Star Fragment farming

```sh
.venv/bin/python star_farm.py           # convert every barrel that appears
.venv/bin/python star_farm.py --fast    # during the 10x spawn boost
```

A barrel is converted by double tapping it and confirming the "Convert Barrel?" dialog.

- **Normal mode** checks on screen that the dialog opened before tapping Yes (~0.6 s per barrel).
- **`--fast`** doesn't look in between (~0.24 s per barrel). With a full board (10x boost) it
  keeps tapping the spot where the barrel in row 4, column 3 overlaps the Yes button: two
  taps open that barrel's dialog, the next one hits Yes and a new barrel spawns into the
  slot. That converts ~4 barrels per second. Every tap is harmless if the timing slips.
- **`--screen-off`**, **`--no-storm`**, **`--boost`** and **`--upgrade`** work like in the merger.

Storms are collected, popups are waited out, a leftover dialog is finished with Yes. With
`--fast` the blind tapping stops just before the next storm (read from the storm timer).

## cascade_merge.py: the 2018 merger

```sh
.venv/bin/python cascade_merge.py               # merge in the fixed pattern, convert the top-left barrel
.venv/bin/python cascade_merge.py --no-convert  # keep it instead
```

`merge-barrels.py` / `merge-barrels-advanced.py` from the original bot, working again (the
originals are in the git history). It doesn't tell barrels apart: it drags every barrel of a row
onto the one above it, row after row in a fixed pattern (rows 1,2,1 / 2,3,2,1 /
2,3,2,3,4,3,2,1) that pushes merges up to the top row. Then it merges the top row into one
barrel in the top-left slot and converts that barrel into Star Fragments, and starts over.
`simple_merge.py` merges smarter; this is the original idea.

Changes from 2018: touches go through the phone helper (the originals moved the PC mouse or
wrote to `/dev/input`), drags are curved and randomized like `simple_merge.py`'s, it collects
magnet storms and waits out popups. `--side-taps` brings back the 2018 tap on the board left of
the barrels after every drag; `--fast`, `--once`, `--no-storm`, `--boost`, `--upgrade` and `--screen-off`
work like in the other scripts.

## Magnet storms

Every few minutes ("Next Storm in" at the top) the board darkens for ~20 s and magnets fall
over it; touching them collects them. Every magnet passes the top row, so the bots put four
fingers down there, one per column, and keep them circling over the barrels until the storm is
over. In one test that brought +95 magnets, against +49 for a storm left alone. After each storm
the bots print how many magnets it brought.

## Boosts

With `--boost`, every 5 s the bots look at the square top right:

- **Crown on blue**: a boost is on offer. The bot taps it, reads the "Event!" dialog (OCR) and
  taps **Yes** for "Faster Barrels" (barrels x10 for 10 minutes), **No** for anything else
  (e.g. "Auto Merger").
  After No the square disappears until the next offer.
- **Timer on blue**: a boost is running. **No square**: nothing on offer right now.

Yes starts an ad (sometimes two). Every 2 s while it is in front, the bot looks for X buttons
in its top corners and reads the text next to each: during the "Reward in 5 seconds" countdown
it taps nothing (closing then would cost the reward); once it says "Reward granted" it taps that
X, otherwise any X it finds. Back in the game the boost timer replaces the crown. A boost
dialog that is already open (e.g. you tapped the crown) is answered the same way. Run
`simple_merge.py --fast` or `star_farm.py --fast` during the boost.

## Upgrades

With `--upgrade` the bots buy **Better Barrels** (barrels spawn 1 tier higher). At the start
they open the upgrades menu (the arrow at the bottom), switch to the first tab if needed,
read the upgrade's price and close the menu again. Once the scrap read at the top of the screen
(every 10 s, see [Stats](#stats)) reaches that price, they open the menu again, tap the green
button as long as it stays green, read the next price and close the menu:

```
11:59:08 Better Barrels: level 80/999, next one costs 98.07 ND
11:59:43 Better Barrels bought 1x: level 81/999, next one costs 392.3 ND
```

The price is looked at again every 30 minutes anyway, in case it changed in other ways
(bought by hand, game reset). The menu isn't opened in the 20 s before a magnet storm.
It also levels the **scrapyard** (3rd tab of the prestige menu, the golden barrel left of the
arrow): every 10 taps on it make a level, each tap costs scrap. At the start it reads the
price of a tap (the "i" button) and the taps left to the next level ("Click 7 times to level
up!"). Only once the scrap read at the top pays for all those taps does it open the scrapyard
again, tap them all at once, check that the level went up and read the price of the next
level (and goes on while the scrap pays for whole levels):

```
15:30:00 scrapyard tapped 5x: level 136, 10 taps to the next, a tap costs 441.7 DV (the level: 4.417 TV)
```

The prestige menu's tabs have buttons in the same places, and the first one restarts the game,
so before tapping anything the bots check the page heading ("Scrapyard v2", "Bricks").

It also upgrades the **brick production**, which costs the player level (reset to 1) and
needs a certain level, one more each time. It opens the prestige menu (the golden barrel left
of the arrow), switches to the 4th tab (bricks) if needed and taps the blue "Upgrade" button.
Before tapping it checks the page says "Bricks": the other tabs have blue buttons in the same
place ("Restart Game", "Upgrade Stars"). The dialog that opens shows the level needed; it
confirms only when the player level read at the top reaches it, the button showed the red "1"
badge and the dialog warns "Your level will be reset to 1!" (with too low a level it says
"Currently: Level 5" instead). Otherwise it closes the dialog and remembers the level for next time:

```
12:26:27 bricks upgraded (player level 30 spent, back to 1): 1.717e10 B/s -> 3.435e10 B/s; the next one needs level 21
```

The stats show the purchases and the levels gained:

```
upgrades: 3  (3/h, 0.1/min, 0.00/s)
scrapyard taps: 20  (20/h, 0.3/min, 0.01/s)
brick upgrades: 1  (1/h, 0.0/min, 0.00/s)
Better Barrels: level 80 -> 83  (+3, 3.0/h)
Scrapyard: level 135 -> 137  (+2, 2.0/h)
Bricks: level 18 -> 19  (+1, 1.0/h)
```

## Stats

Press **Ctrl+C** to stop. The scripts then print what they did and save it into `runs/`:

```
--- stats after 2h 15m 08s ---
merges: 36,246  (16,093/h, 268.2/min, 4.47/s)
storms: 21  (9/h, 0.2/min, 0.00/s)
storm magnets: 3,143  (1,396/h, 23.3/min, 0.39/s)
boosts: 11  (5/h, 0.1/min, 0.00/s)
boosts declined: 50  (22/h, 0.4/min, 0.01/s)
scrap: 1.685 DV -> 135.1 TV  +135.1 TV (x80,169), 59.98 TV/h
magnets: 9,974 -> 70,836  +60,862 (x7.1), 27,023/h
player levels: 15  (7/h, 0.1/min, 0.00/s)
level-up magnets: 1,872  (831/h, 13.9/min, 0.23/s)
Better Barrels: level 80 -> 83  (+3, 1.3/h)
boost active: 1h 44m 40s (77% of the time)
saved as runs/2026-10-28_09-20-49_simple_merge.json (see runs.py)
```

Numbers below a billion are written out in full; bigger ones the way the game's Normal number
format writes them (K, M, B, T, Qa, ... V, UV, DV, TV: each 1000x the one before; past UV the
names are a guess), `x80,169` is how many times as much there is at the end.

`runs.py` shows the saved runs:

```
.venv/bin/python runs.py                  # list them, numbered
.venv/bin/python runs.py show             # the latest: its stats and how it went every 30 min (incl. boost %)
.venv/bin/python runs.py show 3 --every 15
.venv/bin/python runs.py details 8          # everything about run 8 (see below)
.venv/bin/python runs.py total            # all runs added up, incl. levels gained (--script simple_merge for one bot)
```

`--scientific` writes big numbers as `1.351e74` instead.

`details` shows everything that was recorded about a run:

- all counts (also failed drags, level ups whose banner was read, boost time)
- speed: merges per minute overall, with and without the boost, the fastest minute
- scrap growth (x per hour, doubling time) and where the magnets came from (storms, level ups,
  the rest: merging minus anything spent)
- levels: player levels gained and how often, the level-up magnet factor and the "More
  Magnets" bonus it implies, every Better Barrels / brick upgrade with its price / production
- every storm (magnets, duration) and boost offer (which ones came, which was taken)
- one-line charts of merges per minute, boost, magnets, player level and scrap over the run
- the log: everything that happened, with the time

```
== storms ==
1 storms, 106 magnets each on average
  13:10:27  +106 magnets in 22 s
== over time (00m 13s per column, low to high) ==
merges/min     ▁▁▁▁▁▁ ▁  ▁█  39 .. 345
boost on %     ▁▁▁▁▁▁ ▁  ▂█  0 .. 97
magnets       ▁▁▁▃▃▃▃ ▆  ▆█  30,895 .. 31,247
player level  ▁▁▁▅▅▅▅ ▅  ▅█  15 .. 17
== log ==
13:09:14  level up  magnets 96, level 16
13:10:27  storm  seconds 22, magnets 106
13:10:30  boost offer  name Faster Barrels, taken yes
```

Runs saved before the log existed show all the totals, just not the storms, offers and
upgrades one by one.

`simple_merge.py` counts merges (and conversions with `--delete-overflow`),
`star_farm.py` counts conversions, `cascade_merge.py` loops, drags and conversions; all of
them count storms, the magnets they brought, boosts taken / declined and how long a boost
was running (its timer top right, checked every second; also boosts you took by hand, also
without `--boost`). During `star_farm.py --fast` continuous
tapping the conversions can't be seen without slowing it down, so that part is estimated at
the measured ~4/s and the total is shown with a `~`.

Scrap and magnets are read with OCR from a full resolution screenshot every 10 s, in the
background (the helper's quarter size frames lose the decimal point), whenever the plain
board is on screen (not during ads, menus, storms or the "LEVEL UP!" banner that covers the
top bar for a moment); the player level is read there too, and the levels gained are counted
(a brick upgrade resetting it doesn't count as a loss). When the bot sees the banner, the
screenshot thread reads its "You got 156 Magnets!" right away: `level-up magnets`. Banners
can still be missed (each shows for ~2-3 s, and right after a reset to level 1 they come so
quickly that they cover each other); the summary then says on how many level ups it was read
and estimates the total: a level up gives 5 magnets per level reached, plus the bonus of the
"More Magnets" upgrade (+2% per level of it), rounded down. That factor (5.8 with +16%) is
learned from the banners that were read, so it follows the upgrade by itself. The change counts
everything, including what you spend meanwhile. Set the game's number format (settings) to
**Scientific** (`1.948e66`): those are read exactly. **Normal** (`1.948 UV`) works too, but only
with the suffixes listed in `SUFFIXES` in `src/stats.py` (seen in the game up to UV); a missing
one leaves scrap out of the stats. Abstract (`1a`, `1b`) isn't supported.

## Screen off

`--screen-off` switches the display panel itself off (black, no pixels lit, good for OLED
at night) while the game keeps running and the bot keeps seeing it, the same way
`scrcpy --turn-screen-off` does. "Stay awake while charging" is turned on meanwhile so the
phone doesn't go to sleep. Both come back when the script stops, however it stops
(Ctrl+C, crash, killed). If the screen ever stays off, press the power button twice.

## How it works

- **`injector/Injector.java`**: a small helper started on the phone with `app_process`
  (like scrcpy). It injects touches (~1 ms each instead of ~80 ms for `input`, several
  fingers at once for storms), grabs
  quarter-size screenshots from a virtual display mirroring the screen (no video encoding)
  and switches the display panel on/off. The scripts talk to it over `adb shell`.
- Barrels are told apart by a small downscaled sample of each slot; empty slots are the flat
  gray silhouettes. Storms are recognized by the darkened background.
- **`src/`** holds what the scripts share: `board.py` (positions, calibration, reading the
  board), `phone.py` (the helper, adb), `actions.py` (drags, taps, converting), `storm.py`,
  `boost.py`, `upgrade.py`, `popup.py`, `hud.py` (OCR), `stats.py` and `bot.py` (common options
  and setup).

The code has type hints throughout; `mypy.ini` checks that every function has them:

```
.venv/bin/pip install mypy
.venv/bin/mypy
```

## Other phones

All coordinates in `src/board.py` are screen pixels of a 1220x2712 screen. For
another phone, open the game on the board (not during a magnet storm) and run:

```sh
.venv/bin/python calibrate.py
```

It takes a screenshot, finds the 4x5 grid from the board background and saves
`calibration.png`, showing the sampled part of each slot (should sit on the barrel bodies),
the background points between the columns (green = found) and where the "Convert Barrel?"
buttons are expected, and the boxes of the texts read with OCR and of the boost crown (it also prints what it read).
Answer `y` to save `calibration.json`, which all scripts load from then
on; `m` lets you click the centers of the top-left and bottom-right barrels instead (also
`--manual`). `--image FILE` calibrates from a saved screenshot.

Only the grid and the background color are measured; everything else (sample area, dialog
buttons, texts...) is scaled along with the grid. If converting or the stats don't work, check
the `Yes` / `No` marks and the text boxes in the preview and adjust `CONVERT_*` / `*_BOX` in
`src/board.py`.
`SCRAP_CALIBRATION=other.json` picks another calibration file, `SCRAP_CALIBRATION=` uses none.
`simple_merge.py --dry-run --once --debug-image debug.png` shows where the slots are sampled.

## Troubleshooting

- **"helper on the phone did not start"**: check `adb devices`, unlock the phone, try again.
- **"touchscreen not found"**: touches are sent from a virtual device instead; usually still works.
- **"grid not visible" for long**: something covers the board (popup, menu); run with
  `--debug-image debug.png` to see what the script sees.
- **Xiaomi / MIUI**: writing to `/dev/input` is blocked by SELinux and MIUI blocks changing the
  refresh rate from adb; the scripts don't rely on either. The game redraws at the display's
  refresh rate even with the screen off, so setting 60 Hz in the phone's settings should make
  it run cooler.

## Original bot

The original bot by Arhimenrius (2018) streamed the screen through OpenCV, merged by a fixed
color table, collected steel and magnets, watched ads for bonuses and solved the anti-macro
popup. Its colors and coordinates no longer match the game, so it was removed; it is in the
git history (`ultra-barrels-merger.py`, the old `src/`). Its first, blind merger lives on as
`cascade_merge.py`.
