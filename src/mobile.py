import random
import subprocess

# Touches are sent as `input motionevent` commands through one long-lived
# `adb shell`, because SELinux blocks writing to /dev/input on modern Android.
# Coordinates are screen pixels (see `adb shell wm size`).

back_button_position_x = 250
back_button_position_y = 1850

shell = None
touching = False
last_position = (0, 0)


def send(command):
    global shell
    if shell is None or shell.poll() is not None:
        shell = subprocess.Popen(
            ["adb", "shell"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True
        )
    shell.stdin.write(command + '\n')
    shell.stdin.flush()


def initTouch():
    global touching
    touching = False


def clearTouch():
    global touching
    if touching:
        send('input motionevent UP %d %d' % last_position)
    touching = False


def actionDuringTouch(x, y):
    global touching, last_position
    last_position = (int(x), int(y))
    send('input motionevent %s %d %d' % (('MOVE' if touching else 'DOWN',) + last_position))
    touching = True


def click_back_button():
    send('input keyevent BACK')
