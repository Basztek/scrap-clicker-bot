"""The phone: touch injector and screen grabber running on it (see injector/), plus adb helpers."""
from __future__ import annotations

import ctypes
import os
import select
import signal
import subprocess
import sys

from typing import IO

import cv2
import numpy as np

from src import board
from src.board import Frame

GAME_PACKAGE = 'com.scrap.clicker.android'

INJECTOR_DEX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'injector', 'injector.dex')
INJECTOR_ON_PHONE = '/data/local/tmp/injector.dex'
PR_SET_PDEATHSIG = 1
HELPER_START_SECONDS = 15


def die_with_parent() -> None:
    # Linux: end this child (and with it the process on the phone) when the bot
    # dies in any way, so nothing keeps running on the phone
    ctypes.CDLL(None).prctl(PR_SET_PDEATHSIG, signal.SIGTERM)


class Phone:
    """Touch injector and screen grabber running on the phone, see injector/Injector.java."""

    def __init__(self) -> None:
        if not os.path.exists(INJECTOR_DEX):
            subprocess.run([os.path.join(os.path.dirname(INJECTOR_DEX), 'build.sh')], check=True)
        subprocess.run(['adb', 'push', INJECTOR_DEX, INJECTOR_ON_PHONE], stdout=subprocess.DEVNULL, check=True)
        self.process = subprocess.Popen(
            # Ignoring SIGHUP lets the helper notice a lost connection as end of input
            # and clean up (screen back on, settings restored) instead of being killed
            ['adb', 'shell', "trap '' HUP; CLASSPATH=%s exec app_process / Injector" % INJECTOR_ON_PHONE],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            preexec_fn=die_with_parent if sys.platform.startswith('linux') else None
        )
        assert self.process.stdin is not None and self.process.stdout is not None
        self.stdin: IO[bytes] = self.process.stdin
        self.stdout: IO[bytes] = self.process.stdout
        if not select.select([self.stdout], [], [], HELPER_START_SECONDS)[0]:
            self.process.kill()
            raise RuntimeError('helper on the phone did not start within %ds (is the phone connected '
                               'and unlocked? try again)' % HELPER_START_SECONDS)
        ready = self.stdout.readline().split()
        if not ready or ready[0] != b'ready':
            raise RuntimeError('helper on the phone did not start')
        self.touch_device = int(ready[1])

    def run(self, commands: list[str]) -> None:
        """Sends the commands and waits until all of them were injected."""
        self.stdin.write(('\n'.join(commands + ['sync']) + '\n').encode())
        self.stdin.flush()
        self.stdout.readline()

    def grab(self) -> Frame:
        """Returns the current screen, scaled up to SCREEN_SIZE."""
        self.stdin.write(b'frame %d %d\n' % board.FRAME_SIZE)
        self.stdin.flush()
        width, height, stride = map(int, self.stdout.readline().split()[1:])
        if not width:
            raise RuntimeError('no picture from the phone')
        data = self.stdout.read(stride * height)
        rgba = np.frombuffer(data, np.uint8).reshape(height, stride // 4, 4)[:, :width]
        frame: Frame = cv2.resize(cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR), board.SCREEN_SIZE,
                                  interpolation=cv2.INTER_LINEAR)
        return frame

    def close(self) -> None:
        try:
            self.stdin.close()
        except OSError:
            pass
        self.process.wait()


def connect() -> Phone:
    phone = Phone()
    if phone.touch_device < 0:
        print('touchscreen not found, touches are sent from a virtual device')
    return phone


def screenshot() -> Frame:
    """Full resolution screenshot through adb (~1.3 s); the helper's frames are quarter size."""
    # Own session: Ctrl+C stops the bot, which then cleans up, instead of killing this in the middle
    png = subprocess.run(['adb', 'exec-out', 'screencap', '-p'], capture_output=True, check=True,
                         start_new_session=True).stdout
    frame: Frame | None = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError('no screenshot from the phone (is it connected and unlocked?)')
    return frame


def focused_window() -> str:
    return subprocess.run(['adb', 'shell', 'dumpsys window | grep mCurrentFocus'],
                          capture_output=True, text=True).stdout


def game_in_front() -> bool:
    return GAME_PACKAGE in focused_window()


def ad_in_front() -> bool:
    """Google's ad screen (it runs inside the game, so game_in_front() is True as well)."""
    return 'AdActivity' in focused_window()


def switch_screen_off(phone: Phone) -> str:
    """Switches the display panel off; returns the stay-awake setting to restore afterwards."""
    # Android doesn't know the panel is off, so stop it from going to sleep
    # at the screen timeout (which would pause the game) while charging over USB
    stay_on = subprocess.run(['adb', 'shell', 'settings get global stay_on_while_plugged_in'],
                             capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(['adb', 'shell', 'settings put global stay_on_while_plugged_in 7'], check=True)
    # The helper on the phone restores it even when the bot gets killed
    phone.run(['atexit settings put global stay_on_while_plugged_in %s' % stay_on, 'screen 0'])
    print('display switched off, press Ctrl+C to stop the bot and switch it back on')
    return stay_on


def switch_screen_on(phone: Phone, stay_on: str) -> None:
    try:
        phone.run(['screen 1'])
    except OSError:
        pass  # the helper died with Ctrl+C; it switches the screen back on itself when it exits
    subprocess.run(['adb', 'shell', 'settings put global stay_on_while_plugged_in %s' % stay_on])
