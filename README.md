# Scrap Clicker 2 bots

Automation for [Scrap Clicker 2](https://play.google.com/store/apps/details?id=com.scrap.clicker.android),
controlled from a PC over USB (adb). No root needed.

- **`simple_merge.py`** merges barrels that look the same.
- **`star_farm.py`** converts barrels into Star Fragments, over and over.

This is a fork of [Arhimenrius/scrap-clicker-bot](https://github.com/Arhimenrius/scrap-clicker-bot);
the original 2018 bot is still in `src/` (see [Original bot](#original-bot)).

> Automating the game is probably against its terms of service. Use at your own risk.

## Requirements

- Linux PC with Python 3, `adb` and a JDK (`javac`)
- Android SDK with build-tools (`d8`) and a platform (`android.jar`), used once to build the
  phone helper. Found via `ANDROID_HOME` / `ANDROID_SDK_ROOT`, default `/opt/android-sdk`
- Android phone with USB debugging enabled, connected over USB, game open
- **Calibrated for a 1220x2712 screen.** Other screens need the coordinates at the top of
  `simple_merge.py` adjusted (see [Other phones](#other-phones))

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
adb devices          # the phone should be listed as "device"
```

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
| `--fast` | Much shorter drags and pauses (~3x more merges), for spawn boosts like the 10x ad boost |
| `--delete-overflow` | When the board is full and nothing can be merged (10 checks, at least 3 s), convert **all** barrels into Star Fragments. Without this flag nothing is ever deleted |
| `--screen-off` | Switch the display off while the bot runs, see [Screen off](#screen-off) |
| `--swipe-ms 150-300` | Drag duration range in ms (default 150-300, with `--fast` 40-80) |
| `--pause 0.1-0.4` | Pause between rounds in seconds (default 0.1-0.4, with `--fast` 0-0.05) |
| `--threshold 10` | How different two barrels may look and still count as the same |
| `--dry-run` | Only print what would be merged |
| `--once` | One round, then exit |
| `--debug-image FILE` | Save each round's frame with the detected slots and groups drawn on it |

What it handles by itself:

- **Barrels still animating** (dropping in, being merged) are left alone until they settle.
- **Barrels that don't merge** (max level) are skipped for 60 s.
- **Magnet storms**: merging isn't possible then, so it waits until the storm is over.
- **Popups / other screens**: it waits and tells you every 15 s.
- **The game ignoring drags** (happens now and then, taps still work): it pauses and
  resumes the game through the Recents screen, which fixes it without restarting the game.

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
- **`--screen-off`** works like in the merger.

Storms and popups are waited out, a leftover dialog is finished with Yes.

## Stats

Press **Ctrl+C** to stop. Both scripts then print what they did:

```
--- stats after 0h 12m 34s ---
merges: 1234  (5891/h, 98.2/min, 1.64/s)
```

`simple_merge.py` counts merges (and conversions with `--delete-overflow`),
`star_farm.py` counts conversions. During `star_farm.py --fast` continuous tapping the
conversions can't be seen without slowing it down, so that part is estimated at the
measured ~4/s and the total is shown with a `~`.

## Screen off

`--screen-off` switches the display panel itself off (black, no pixels lit, good for OLED
at night) while the game keeps running and the bot keeps seeing it, the same way
`scrcpy --turn-screen-off` does. "Stay awake while charging" is turned on meanwhile so the
phone doesn't go to sleep. Both come back when the script stops, however it stops
(Ctrl+C, crash, killed). If the screen ever stays off, press the power button twice.

## How it works

- **`injector/Injector.java`**: a small helper started on the phone with `app_process`
  (like scrcpy). It injects touches (~1 ms each instead of ~80 ms for `input`), grabs
  quarter-size screenshots from a virtual display mirroring the screen (no video encoding)
  and switches the display panel on/off. The scripts talk to it over `adb shell`.
- Barrels are told apart by a small downscaled sample of each slot; empty slots are the flat
  gray silhouettes. Storms are recognized by the darkened background.

## Other phones

All coordinates are screen pixels of a 1220x2712 screen and live at the top of
`simple_merge.py`: `COLUMNS_X`, `FIRST_ROW_TOP`, `ROW_STEP`, the sample area, `GAP_X`
(background between columns), `BACKGROUNDS_BGR` and the `CONVERT_*` dialog positions.
`--dry-run --once --debug-image debug.png` shows where the slots are sampled.

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

`ultra-barrels-merger.py` and `src/` are the original bot by Arhimenrius (2018): it streamed
the screen through OpenCV, merged by a fixed color table, collected steel and magnets,
watched ads for bonuses and solved the anti-macro popup. Its author called it unusable;
this fork made it run on current Python and Android again, but its coordinates and colors
were never recalibrated, so use `simple_merge.py` instead.
