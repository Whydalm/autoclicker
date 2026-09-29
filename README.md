# Autoclicker

Two small Python auto-clickers for Windows (Linux X11 also works).

## `clicker10.py` — vision clicker

Watches the screen for pop-up bonus buttons (x10 / x5 / x2) that appear at random
positions, smoothly moves the mouse onto them and clicks.

Detection is based on **color + shape**, not pixel-perfect matching:

- the button color is read automatically from template images in `templates/`;
- on screen the script looks for a round blob of that color, of button size,
  with a white hand icon inside.

This keeps working when the button rotates, pulses or the background changes.

Optionally it also performs a regular click every 5 minutes.

## `autoclicker.py` — simple timer clicker

Clicks the left mouse button at the current cursor position every 5 minutes.

## Install

```bash
pip install -r requirements.txt
```

## Setup (vision clicker)

1. Create a `templates` folder next to `clicker10.py`.
2. When a button appears, take a tight screenshot of it (Win+Shift+S)
   and save it as `templates/x10.png`, `templates/x5.png` or `templates/x2.png`.
   Only the button color is used, so the crop does not need to be perfect.

## Run

```bash
python clicker10.py
```

| Key | Action         |
|-----|----------------|
| F8  | pause / resume |
| F9  | exit           |

All settings (thresholds, button size range, scan speed, monitor number,
periodic click interval, debug output) are at the top of `clicker10.py`.

With `DEBUG = True` the script prints, once per second, how close the best
candidate for each button is to passing the checks — use it to tune
`FILL_MIN`, `WHITE_MIN` / `WHITE_MAX` and `HUE_TOL`.

## Notes

- If the game ignores the clicks, run the script as administrator.
- Automating input may be against a game's terms of service. Use at your own risk.
