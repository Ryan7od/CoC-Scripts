import time
from pynput import keyboard
import mss
import numpy as np
from pynput.keyboard import Key, Controller
import os
import psutil
import random
import logging
import importlib
import re
from typing import Optional, Tuple

# Optional OCR deps
try:
    from PIL import Image, ImageOps, ImageFilter
except Exception:
    Image = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]
    ImageFilter = None  # type: ignore[assignment]
try:
    import pytesseract
except Exception:
    pytesseract = None  # type: ignore[assignment]

pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# Try to access mouse controller; provide a Windows fallback if unavailable
try:
    _pynput_mouse = importlib.import_module("pynput.mouse")
    Button = _pynput_mouse.Button  # type: ignore[attr-defined]
    MouseController = _pynput_mouse.Controller  # type: ignore[attr-defined]
except Exception:
    Button = None  # type: ignore[assignment]
    MouseController = None  # type: ignore[assignment]
    import ctypes
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    def _win_left_click(x: int, y: int) -> None:
        ctypes.windll.user32.SetCursorPos(int(x), int(y))
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)



# Flag to control the monitoring loop
running = True


def on_press(key):
    global running
    try:
        if key.char == 'q':
            print("Exiting the script.")
            running = False
            return False  # Stop the listener
    except AttributeError:
        pass  # Ignore special keys


def click_after_random_delay(x: int, y: int) -> None:
    """Wait 1000-3000 ms randomly, then left-click at screen coordinate (x, y)."""
    delay_ms = random.randint(1000, 2000)
    time.sleep(delay_ms / 1000.0)
    if MouseController is not None and Button is not None:
        mouse = MouseController()
        mouse.position = (x, y)
        mouse.click(Button.left, 1)
    else:
        # Fallback for Windows when pynput.mouse is unavailable
        try:
            _win_left_click(x, y)  # type: ignore[name-defined]
        except Exception as e:
            print(f"Mouse click failed: {e}")


def _ocr_number_from_region(bbox: Tuple[int, int, int, int], required_digits: Optional[int] = None) -> Optional[int]:
    """Capture screen region and OCR a number with robust preprocessing.

    bbox: (left, top, right, bottom)
    required_digits: if provided, only return a number with exactly this many digits
    Returns an int if detected, else None.
    """
    if Image is None or pytesseract is None or ImageOps is None or ImageFilter is None:
        return None

    left, top, right, bottom = bbox

    try:
        with mss.mss() as sct:
            # Pad the bbox to avoid clipping last/first digits and clamp to monitor bounds
            pad_x, pad_y = 20, 10
            mon = sct.monitors[0]
            mon_left, mon_top = int(mon.get("left", 0)), int(mon.get("top", 0))
            mon_right = mon_left + int(mon.get("width", 0))
            mon_bottom = mon_top + int(mon.get("height", 0))

            cap_left = max(left - pad_x, mon_left)
            cap_top = max(top - pad_y, mon_top)
            cap_right = min(right + pad_x, mon_right)
            cap_bottom = min(bottom + pad_y, mon_bottom)
            cap_w = max(1, cap_right - cap_left)
            cap_h = max(1, cap_bottom - cap_top)

            shot = sct.grab({"left": cap_left, "top": cap_top, "width": cap_w, "height": cap_h})
        arr = np.asarray(shot)[:, :, :3][:, :, ::-1]  # BGRA -> RGB
        img = Image.fromarray(arr)

        # Preprocess: grayscale, autocontrast, upscale, sharpen
        g = img.convert("L")
        g = ImageOps.autocontrast(g, cutoff=2)
        scale = 3
        g = g.resize((g.width * scale, g.height * scale), Image.BICUBIC)
        g = g.filter(ImageFilter.UnsharpMask(radius=1.2, percent=200, threshold=3))

        thresholds = [140, 160, 180]
        cfgs = [
            "--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789",
            "--oem 3 --psm 8 -c tessedit_char_whitelist=0123456789",
        ]

        def extract_int(s: str) -> Optional[int]:
            nums = re.findall(r"\d+", s)
            if not nums:
                return None
            best = max(nums, key=len)
            try:
                return int(best)
            except ValueError:
                return None

        candidates: list[int] = []
        for thr in thresholds:
            bw = g.point(lambda p, t=thr: 255 if p > t else 0, mode="1").convert("L")
            for cfg in cfgs:
                try:
                    text1 = pytesseract.image_to_string(bw, config=cfg)
                    text2 = pytesseract.image_to_string(ImageOps.invert(bw), config=cfg)
                except Exception:
                    continue
                for t in (text1, text2):
                    n = extract_int(t)
                    # Reject >= 5 digits; only accept up to 4-digit numbers
                    if n is not None and len(str(n)) == required_digits:
                        candidates.append(n)

        if not candidates:
            return None
        return candidates[0]
    except Exception:
        return None


def trophies_under(target: int, required_digits: int = 4) -> bool:
    """Capture the box, OCR a number, return True if it's under target.

    required_digits: number of digits the OCR result must have (default 4)
    """
    # Hard-coded rectangle: (left, top, right, bottom)
    bbox = (140, 165, 240, 200)
    value = _ocr_number_from_region(bbox, required_digits=required_digits)
    if value is None:
        return True
    print(f"OCR saw: {value}")
    return value < int(target)


def wait_until_pixel_not_color() -> bool:
    """Wait until pixel (1,1) is no longer exactly expected_rgb.

    Returns True when the pixel changes; False if timeout is reached (if provided).
    """
    expected_rgb = (234, 239, 244)
    poll_interval: float = 0.05

    try:
        with mss.mss() as sct:
            while True:
                shot = sct.grab({"left": 1, "top": 1, "width": 1, "height": 1})
                # mss returns BGRA
                b, g, r, _ = np.array(shot)[0, 0]
                rgb = (int(r), int(g), int(b))
                if rgb != expected_rgb:
                    return True
                time.sleep(poll_interval)
    except Exception as e:
        print(f"Pixel check failed: {e}")
        return False


def main():
    # Increase the script's process priority to high
    try:
        p = psutil.Process(os.getpid())
        p.nice(psutil.HIGH_PRIORITY_CLASS)
    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
        print(f"Could not set process priority: {e}")
        logging.warning(f"Could not set process priority: {e}")

    print("What trophies do you want to drop to?")
    target_input = input()
    # Coerce target to int (accepts plain numbers or strings containing digits)
    try:
        target = int(target_input)
    except ValueError:
        m = re.search(r"(\d+)", target_input)
        if m:
            target = int(m.group(1))
        else:
            print("Please enter a numeric target (e.g., 1200).")
            return

    print(f"Dropping to {target} trophies, press 'q' to quit.")
    print("Starting the script in:")
    for i in range(5, 0, -1):
        print(i)
        time.sleep(1)

    # Start a keyboard listener in a separate thread to listen for the quit command
    listener = keyboard.Listener(on_press=on_press)
    listener.start()
    keyboard_controller = Controller()

    # util func


    with mss.mss() as sct:
        while running:
            time.sleep(3)
            if (trophies_under(target)):
                exit(0)
            # Click attack
            click_after_random_delay(random.randint(75, 175), random.randint(900, 1000))
            # Click find match
            click_after_random_delay(random.randint(1250, 1500), random.randint(600, 650))
            # Wait for base to be found
            time.sleep(2)
            wait_until_pixel_not_color()
            # Select troop  
            click_after_random_delay(random.randint(40, 120), random.randint(900, 1050))
            # Place Troop
            click_after_random_delay(random.randint(1410, 1420), random.randint(765, 780))
            # Surrender
            click_after_random_delay(random.randint(40, 220), random.randint(780, 830))
            click_after_random_delay(random.randint(1000, 1300), random.randint(650, 750))
            # Go home
            click_after_random_delay(random.randint(850, 1050), random.randint(900, 950))


        listener.join()



if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
