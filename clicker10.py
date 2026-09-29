"""
Vision auto-clicker: finds the x10 / x5 / x2 buttons that pop up at random
positions, smoothly moves the mouse onto them and clicks.

Detection is based on COLOR + SHAPE (not pixel-perfect matching):
  - the button color is taken automatically from your template images
    (templates/x10.png, x5.png, x2.png);
  - on screen the script looks for a round blob of that color, of button size,
    with a white hand inside.
This works even when the button rotates, pulses or the background changes.

Install:
    pip install pynput mss opencv-python numpy

Run:
    python clicker10.py

Hotkeys:
    F8 - pause / resume
    F9 - exit
"""

import ctypes
import math
import os
import sys
import threading
import time
from datetime import datetime

import cv2
import mss
import numpy as np
from pynput import keyboard
from pynput.mouse import Button, Controller

# ---------------- SETTINGS ----------------
TEMPLATES = ["x10.png", "x5.png", "x2.png"]   # order = priority
TEMPLATE_DIR = "templates"
HUE_OVERRIDE = {}         # e.g. {"x5.png": 18} to set a button hue manually (OpenCV hue 0..179)
HUE_TOL = 8               # allowed hue difference from the button color
SAT_MIN = 120             # min saturation of button pixels (0..255)
VAL_MIN = 120             # min brightness of button pixels (0..255)
MIN_DIAM = 0.09           # min button diameter as a fraction of screen height
MAX_DIAM = 0.30           # max button diameter as a fraction of screen height
FILL_MIN = 0.70           # how "round and solid" the blob must be (1.0 = perfect circle)
WHITE_MIN = 0.06          # min share of white (the hand) inside the circle
WHITE_MAX = 0.55          # max share of white inside the circle
DOWNSCALE = 0.5           # analyze a downscaled screen (faster)
SCAN_EVERY = 0.2          # how often to scan the screen, seconds
MOVE_TIME = 0.15          # how long the mouse takes to reach the button, seconds
CLICK_HOLD = 0.05         # how long the left button is held down, seconds
CLICK_COOLDOWN = 1.0      # pause after a click, seconds
RETURN_MOUSE = False      # True - move the mouse back to where it was before the click
PERIODIC_CLICK = 5 * 60   # regular click every N seconds; None - disabled
MONITOR = 1               # 1 = primary monitor, 2 = second, 0 = all monitors
DEBUG = True              # print the best candidate for every button once per second
# -------------------------------------------

IS_WIN = sys.platform == "win32"

# Make the process DPI-aware so screen coordinates match mouse coordinates
if IS_WIN:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()

mouse = Controller()
paused = threading.Event()   # set = paused
stop = threading.Event()     # set = exit

# Windows mouse_event flags
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

OPEN_KERNEL = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}")


# ---------------- COLOR DETECTION ----------------

def dominant_hue(img):
    """Most common hue among bright, saturated pixels of a template (= button color)."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = (hsv[..., 1] > 150) & (hsv[..., 2] > 150)
    hues = hsv[..., 0][mask]
    if hues.size < 50:
        return None
    hist = np.bincount(hues, minlength=180).astype(float)
    smooth = sum(np.roll(hist, i) for i in range(-4, 5))  # circular smoothing
    return int(np.argmax(smooth))


def load_buttons():
    """Return a list of (name, hue) in priority order."""
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), TEMPLATE_DIR)
    buttons = []
    for name in TEMPLATES:
        if name in HUE_OVERRIDE:
            buttons.append((name, HUE_OVERRIDE[name]))
            log(f"{name}: hue {HUE_OVERRIDE[name]} (manual)")
            continue
        img = cv2.imread(os.path.join(base, name), cv2.IMREAD_COLOR)
        if img is None:
            log(f"Template not found: {TEMPLATE_DIR}/{name} - skipping")
            continue
        hue = dominant_hue(img)
        if hue is None:
            log(f"{name}: could not detect button color - skipping")
            continue
        buttons.append((name, hue))
        log(f"{name}: button hue = {hue}")
    if not buttons:
        log("No buttons configured. Put x10.png / x5.png / x2.png into the templates folder.")
        sys.exit(1)
    return buttons


def hue_mask(hsv, hue):
    """Binary mask of pixels whose color is close to the given hue."""
    diff = np.abs(hsv[..., 0].astype(np.int16) - hue)
    diff = np.minimum(diff, 180 - diff)  # hue is circular
    m = (diff <= HUE_TOL) & (hsv[..., 1] > SAT_MIN) & (hsv[..., 2] > VAL_MIN)
    return m.astype(np.uint8) * 255


def find_button(hsv, white, hue):
    """
    Look for a round blob of the given color with a white hand inside.
    Returns (fill, white_share, cx, cy) of the best candidate, or None.
    Also returns the best rejected candidate for debugging.
    """
    h = hsv.shape[0]
    min_d, max_d = MIN_DIAM * h, MAX_DIAM * h

    mask = cv2.morphologyEx(hue_mask(hsv, hue), cv2.MORPH_OPEN, OPEN_KERNEL)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best, best_debug = None, None
    for c in contours:
        area = cv2.contourArea(c)
        if area < math.pi * (min_d / 2) ** 2 * 0.5:
            continue
        (x, y), r = cv2.minEnclosingCircle(c)
        if not (min_d <= 2 * r <= max_d):
            continue

        fill = area / (math.pi * r * r)  # ~1.0 for a solid circle

        # Share of white pixels inside the circle (the hand icon)
        x0, y0 = max(0, int(x - r)), max(0, int(y - r))
        x1, y1 = min(white.shape[1], int(x + r) + 1), min(white.shape[0], int(y + r) + 1)
        roi = white[y0:y1, x0:x1]
        circle = np.zeros_like(roi)
        cv2.circle(circle, (int(x) - x0, int(y) - y0), int(r * 0.85), 255, -1)
        inside = cv2.countNonZero(circle)
        white_share = cv2.countNonZero(cv2.bitwise_and(roi, circle)) / max(1, inside)

        cand = (fill, white_share, x, y)
        if best_debug is None or fill > best_debug[0]:
            best_debug = cand
        if fill >= FILL_MIN and WHITE_MIN <= white_share <= WHITE_MAX:
            if best is None or fill > best[0]:
                best = cand
    return best, best_debug


# ---------------- MOUSE ----------------

def set_cursor(x, y):
    if IS_WIN:
        ctypes.windll.user32.SetCursorPos(int(x), int(y))
    else:
        mouse.position = (int(x), int(y))


def get_cursor():
    x, y = mouse.position
    return int(x), int(y)


def jiggle():
    """Send real relative mouse-move events so the game registers the hover."""
    if IS_WIN:
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_MOVE, 1, 0, 0, 0)
        time.sleep(0.01)
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_MOVE, -1, 0, 0, 0)
    else:
        x, y = mouse.position
        mouse.position = (x + 1, y)
        time.sleep(0.01)
        mouse.position = (x, y)


def left_click():
    """Press and hold the left button briefly - Roblox ignores instant clicks."""
    if IS_WIN:
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(CLICK_HOLD)
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    else:
        mouse.press(Button.left)
        time.sleep(CLICK_HOLD)
        mouse.release(Button.left)


def smooth_move(x, y):
    """Move the mouse to (x, y) smoothly instead of jumping."""
    sx, sy = get_cursor()
    steps = max(1, int(MOVE_TIME / 0.01))
    for i in range(1, steps + 1):
        t = i / steps
        t = t * t * (3 - 2 * t)  # ease-in / ease-out
        set_cursor(sx + (x - sx) * t, sy + (y - sy) * t)
        time.sleep(0.01)


def click_at(x, y):
    """Move to the button, let the game register the hover, then click."""
    old = get_cursor()
    smooth_move(x, y)
    jiggle()
    time.sleep(0.05)
    left_click()
    if RETURN_MOUSE:
        time.sleep(0.05)
        smooth_move(*old)


# ---------------- MAIN LOOP ----------------

def scanner(buttons):
    """Grab the screen, look for buttons, click them."""
    last_periodic = time.time()
    last_debug = 0.0
    with (mss.MSS() if hasattr(mss, "MSS") else mss.mss()) as sct:
        mon = sct.monitors[MONITOR]
        log(f"Scanning monitor {MONITOR}: {mon['width']}x{mon['height']} "
            f"at ({mon['left']}, {mon['top']})")
        while not stop.is_set():
            if paused.is_set():
                stop.wait(0.2)
                continue

            frame = np.array(sct.grab(mon))[:, :, :3]  # BGRA -> BGR
            small = cv2.resize(frame, None, fx=DOWNSCALE, fy=DOWNSCALE,
                               interpolation=cv2.INTER_AREA)
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            # White = low saturation, high brightness (the hand icon)
            white = ((hsv[..., 1] < 60) & (hsv[..., 2] > 200)).astype(np.uint8) * 255

            hit, debug = None, []
            for name, hue in buttons:  # priority order
                found, cand = find_button(hsv, white, hue)
                if cand:
                    debug.append(f"{name}: fill={cand[0]:.2f} white={cand[1]:.2f}")
                else:
                    debug.append(f"{name}: -")
                if hit is None and found:
                    hit = (name, *found)

            if DEBUG and time.time() - last_debug >= 1.0:
                log(" | ".join(debug))
                last_debug = time.time()

            if hit:
                name, fill, _, cx, cy = hit
                x = mon["left"] + int(cx / DOWNSCALE)
                y = mon["top"] + int(cy / DOWNSCALE)
                click_at(x, y)
                log(f"Found {name} (fill {fill:.2f}) -> click at ({x}, {y})")
                stop.wait(CLICK_COOLDOWN)
                continue

            if PERIODIC_CLICK and time.time() - last_periodic >= PERIODIC_CLICK:
                jiggle()
                left_click()
                last_periodic = time.time()
                log(f"Periodic click at {get_cursor()}")

            stop.wait(SCAN_EVERY)


def on_press(key):
    """Global hotkeys: F8 = pause/resume, F9 = exit."""
    if key == keyboard.Key.f8:
        if paused.is_set():
            paused.clear()
            log("Resumed")
        else:
            paused.set()
            log("Paused")
    elif key == keyboard.Key.f9:
        log("Exiting.")
        stop.set()
        return False  # stop the keyboard listener


if __name__ == "__main__":
    btns = load_buttons()
    log("F8 - pause/resume, F9 - exit. Starting in 3 seconds...")
    time.sleep(3)

    threading.Thread(target=scanner, args=(btns,), daemon=True).start()
    try:
        with keyboard.Listener(on_press=on_press) as listener:
            listener.join()
    except KeyboardInterrupt:
        stop.set()
        log("Stopped.")