import time
from pynput import keyboard
import mss
import numpy as np
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

if pytesseract is not None:
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

# Flag for if OCR failed on last, to allow if a certain number is bad
last_ocr_failed = False  

POLL_INTERVAL_SECONDS = 0.1
COLOR_TOLERANCE = 3
# Screen positions and OCR bounds below are pixels for a 3840x2160 screen.
_mouse = None

if MouseController is not None and Button is not None:
    try:
        _mouse = MouseController()
    except Exception as e:
        print(f"Mouse controller unavailable, falling back to Windows clicks: {e}")
        _mouse = None

def on_press(key):
    global running
    try:
        if key.char == 'q':
            print("Exiting the script.")
            running = False
            return False  # Stop the listener
    except AttributeError:
        pass  # Ignore special keys


def click_after_random_delay(x: int, y: int, lowtime: int = 1000, hightime: int = 2000) -> None:
    """Wait 1000-2000 ms randomly, then click at screen coordinate (x, y)."""
    delay_ms = random.randint(lowtime, hightime)
    time.sleep(delay_ms / 1000.0)
    if _mouse is not None and Button is not None:
        _mouse.position = (x, y)
        _mouse.click(Button.left, 1)
    else:
        # Fallback for Windows when pynput.mouse is unavailable
        try:
            _win_left_click(x, y)  # type: ignore[name-defined]
        except Exception as e:
            print(f"Mouse click failed: {e}")


def _ocr_number_from_region(bbox: Tuple[int, int, int, int], required_digits: Optional[int] = None) -> Optional[int]:
    """OCR a number from screen region with minimal preprocessing"""
    if Image is None or pytesseract is None:
        print("OCR unavailable: install Pillow and pytesseract to use trophy checks.")
        return None

    left, top, right, bottom = bbox
    width, height = right - left, bottom - top
    
    with mss.mss() as sct:
        shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
    
    # Convert to PIL RGB
    arr = np.asarray(shot)[:, :, :3][:, :, ::-1]
    img = Image.fromarray(arr)
    
    # Minimal preprocessing - just upscale
    img = img.resize((img.width * 4, img.height * 4), Image.LANCZOS)
    
    # Save debug image
    img.save("debug_trophy_ocr_edrag.png")
    print(f"Debug: Saved OCR region to debug_trophy_ocr_edrag.png")
    
    # Simple OCR configs to try
    configs = [
        "--oem 3 --psm 8 -c tessedit_char_whitelist=0123456789",
        "--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789", 
        "--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789"
    ]
    
    for config in configs:
        try:
            text = pytesseract.image_to_string(img, config=config).strip()
            
            # Extract number
            match = re.search(r'\d+', text)
            if match:
                value = int(match.group())
                digits = len(str(value))
                
                if required_digits is None or digits == required_digits:
                    return value
        except Exception as e:
            print(f"OCR failed with config {config}: {e}")
            continue
    
    print("All OCR attempts failed")
    return None


def trophies_above(target: int, required_digits: int = 4) -> bool:
    global last_ocr_failed
    """Capture the box, OCR a number, return True if it's under target.

    required_digits: number of digits the OCR result must have (default 4)
    """
    # Hard-coded rectangle: (left, top, right, bottom)
    bbox = (260, 330, 480, 440)
    value = _ocr_number_from_region(bbox, required_digits=required_digits)
    if value is None:
        print("OCR failed to read the trophy count.")
        if (not last_ocr_failed):
            last_ocr_failed = True
            return False
        return True
    print(f"OCR saw: {value}")
    return value > int(target)


def colors_match(actual_rgb: Optional[Tuple[int, int, int]], expected_rgb: Tuple[int, int, int], tolerance: int = COLOR_TOLERANCE) -> bool:
    if actual_rgb is None:
        return False
    return all(abs(actual - expected) <= tolerance for actual, expected in zip(actual_rgb, expected_rgb))


def get_pixel_rgb(
    point: Tuple[int, int],
    sct: Optional[mss.mss] = None,
    *,
    log_errors: bool = True,
) -> Optional[Tuple[int, int, int]]:
    try:
        if sct is None:
            with mss.mss() as local_sct:
                shot = local_sct.grab({"left": point[0], "top": point[1], "width": 1, "height": 1})
        else:
            shot = sct.grab({"left": point[0], "top": point[1], "width": 1, "height": 1})
            # mss returns BGRA
        b, g, r, _ = np.array(shot)[0, 0]
        return (int(r), int(g), int(b))
    except Exception as e:
        if log_errors:
            print(f"Pixel check failed: {e}")
        return None


def wait_until_pixel(
    expected_rgb: Tuple[int, int, int],
    point: Tuple[int, int],
    *,
    should_match: bool,
) -> bool:
    try:
        with mss.mss() as sct:
            while True:
                last_rgb = get_pixel_rgb(point, sct)
                if last_rgb is not None:
                    is_match = colors_match(last_rgb, expected_rgb, tolerance=5)
                    if is_match == should_match:
                        return True
                time.sleep(POLL_INTERVAL_SECONDS)
    except Exception as e:
        print(f"Pixel check failed: {e}")
        return False


def wait_until_pixel_not_color(
    expected_rgb: Tuple[int, int, int],
    point: Tuple[int, int],
) -> bool:
    return wait_until_pixel(
        expected_rgb,
        point,
        should_match=False,
    )

def wait_until_pixel_color(
    expected_rgb: Tuple[int, int, int],
    point: Tuple[int, int],
) -> bool:
    return wait_until_pixel(
        expected_rgb,
        point,
        should_match=True,
    )


def place_in_interval(point1: Tuple[int, int], point2: Tuple[int, int], number_of_units: int, lowtime: int = 200, hightime: int = 500) -> None:
    xcoords = np.linspace(point1[0], point2[0], number_of_units)
    ycoords = np.linspace(point1[1], point2[1], number_of_units)
    for x, y in zip(xcoords, ycoords):
        click_after_random_delay(int(x), int(y), lowtime, hightime)


def wait_for_battle_to_finish() -> None:
    try:
        with mss.mss() as sct:
            while True:
                if colors_match(get_pixel_rgb((3360, 1710), sct), (210, 219, 231)):
                    # 1 Star
                    click_after_random_delay(random.randint(100, 360), random.randint(1660, 1740))
                    click_after_random_delay(random.randint(2000, 2500), random.randint(1240, 1440), 200, 300)
                    break

                if colors_match(get_pixel_rgb((1800, 1910), sct), (112, 187, 29)):
                    # Battle ended
                    break

                time.sleep(POLL_INTERVAL_SECONDS)
    except Exception as e:
        print(f"Battle end check failed: {e}")


def check_specials() -> None:
    startTime = time.time()
    try:
        with mss.mss() as sct:
            while time.time() < startTime + 5:
                if colors_match(get_pixel_rgb((1900, 140), sct), (239, 231, 214)):
                    # Chest
                    click_after_random_delay(random.randint(1700, 2100), random.randint(1000, 1400))
                    click_after_random_delay(random.randint(1700, 2100), random.randint(1000, 1400), 100, 300)
                    click_after_random_delay(random.randint(1700, 2100), random.randint(1000, 1400), 100, 300)
                    click_after_random_delay(random.randint(1700, 2100), random.randint(1000, 1400), 100, 300)

                    click_after_random_delay(random.randint(1700, 2200), random.randint(1900, 2000), 2500, 3500)

                    time.sleep(2)
                    break

                if colors_match(get_pixel_rgb((2000, 400), sct), (62, 47, 185)):
                    # Star Bonus
                    click_after_random_delay(random.randint(1700, 2100), random.randint(1750, 1900))

                    break

                time.sleep(POLL_INTERVAL_SECONDS)
    except Exception as e:
        print(f"Special check failed: {e}")


def main():
    global running

    # Keep normal priority so pixel polling cannot starve BlueStacks or VS Code.
    try:
        p = psutil.Process(os.getpid())
        if hasattr(psutil, "NORMAL_PRIORITY_CLASS"):
            p.nice(psutil.NORMAL_PRIORITY_CLASS)
    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
        print(f"Could not set process priority: {e}")
        logging.warning(f"Could not set process priority: {e}")

    # print("What trophies do you want to climb to?")
    # target_input = input()
    # # Coerce target to int (accepts plain numbers or strings containing digits)
    # try:
    #     target = int(target_input)
    # except ValueError:
    #     m = re.search(r"(\d+)", target_input)
    #     if m:
    #         target = int(m.group(1))
    #     else:
    #         print("Please enter a numeric target (e.g., 1200).")
    #         return
    #
    # print(f"Climbing to {target} trophies, press 'q' to quit.")
    print("Starting. Press q to quit.")

    # print("Selecting army in slot 1...")
    # click_after_random_delay(random.randint(80, 200), random.randint(1520, 1640))
    # click_after_random_delay(random.randint(1500, 2200), random.randint(160, 280))
    # click_after_random_delay(random.randint(3360, 3680), random.randint(520, 600))
    # click_after_random_delay(random.randint(3660, 3760), random.randint(160, 240))

    # Start a keyboard listener in a separate thread to listen for the quit command
    listener = keyboard.Listener(on_press=on_press)
    listener.start()

    # Event troop offset in 4K screen pixels.
    offset = 290

    while running:
        print("cycle initiated")
        wait_until_pixel_color((255, 255, 255), (120, 90))
        time.sleep(2)
        print("start cycle")
        # if (trophies_above(target)):
        #     exit(0)
        # Click attack
        click_after_random_delay(random.randint(100, 300), random.randint(1840, 2040), 100, 200)
        # Click find match
        click_after_random_delay(random.randint(240, 880), random.randint(1440, 1600), 500, 1000)
        # Click attack on army screen
        # wait_until_pixel_color((189, 235, 137), (3240, 1900))
        click_after_random_delay(random.randint(2960, 3520), random.randint(1800, 1900), 300, 600)
        # Wait for base to be found
        time.sleep(2)
        print("attack")
        wait_until_pixel_not_color((233, 242, 245), (0, 0))
        wait_until_pixel_color((252, 94, 102), (180, 1670))
        print("base found")
        # Select troop  
        click_after_random_delay(random.randint(620 + offset, 760 + offset), random.randint(1880, 2080))
        # Place troop
        place_in_interval((random.randint(220, 260), random.randint(880, 920)), (random.randint(1420, 1460), random.randint(40, 80)), 11, 50, 150)
        place_in_interval((random.randint(2320, 2340), random.randint(60, 80)), (random.randint(3580, 3600), random.randint(1020, 1040)), 11, 50, 150)
        place_in_interval((random.randint(3580, 3600), random.randint(1040, 1060)), (random.randint(2740, 2760), random.randint(1700, 1720)), 11, 50, 150)
        place_in_interval((random.randint(960, 1000), random.randint(1620, 1660)), (random.randint(200, 240), random.randint(1080, 1120)), 11, 10, 150)
        # # Siege Machine
        # click_after_random_delay(random.randint(640 + offset, 840 + offset), random.randint(1840, 2080), 500, 1000)
        # click_after_random_delay(random.randint(3580, 3600), random.randint(1020, 1060), 300, 500)
        # # EQ spells for siege
        # click_after_random_delay(random.randint(1860 + offset, 2020 + offset), random.randint(1840, 2080), 200, 300)
        # click_after_random_delay(random.randint(2760, 2800), random.randint(1000, 1040), 200, 300)
        # click_after_random_delay(random.randint(2760, 2800), random.randint(1000, 1040), 20, 50)
        # click_after_random_delay(random.randint(2760, 2800), random.randint(1000, 1040), 20, 50)
        # click_after_random_delay(random.randint(2760, 2800), random.randint(1000, 1040), 20, 50)
        # click_after_random_delay(random.randint(2760, 2800), random.randint(1000, 1040), 20, 50)
        # click_after_random_delay(random.randint(2760, 2800), random.randint(1000, 1040), 20, 50)
        # # EQ for heroes
        # click_after_random_delay(random.randint(2500, 2600), random.randint(1100, 1200), 200, 300)
        # click_after_random_delay(random.randint(2500, 2600), random.randint(1100, 1200), 20, 50)
        # click_after_random_delay(random.randint(2500, 2600), random.randint(1100, 1200), 20, 50)
        # click_after_random_delay(random.randint(2500, 2600), random.randint(1100, 1200), 20, 50)
        # click_after_random_delay(random.randint(2500, 2600), random.randint(1100, 1200), 20, 50)
        # click_after_random_delay(random.randint(2500, 2600), random.randint(1100, 1200), 20, 50)
        # Place heroes
        click_after_random_delay(random.randint(860 + offset, 1020 + offset), random.randint(1880, 2080), 300, 400)
        click_after_random_delay(random.randint(2820, 2860), random.randint(1600, 1640), 300, 400)
        click_after_random_delay(random.randint(1100 + offset, 1280 + offset), random.randint(1880, 1960), 300, 400)
        click_after_random_delay(random.randint(2940, 2980), random.randint(1510, 1540), 300, 400)
        click_after_random_delay(random.randint(1340 + offset, 1500 + offset), random.randint(1880, 1940), 300, 400)
        click_after_random_delay(random.randint(3000, 3040), random.randint(1460, 1490), 300, 400)
        click_after_random_delay(random.randint(1600 + offset, 1740 + offset), random.randint(1880, 2080), 300, 400)
        click_after_random_delay(random.randint(3060, 3100), random.randint(1414, 1440), 300, 400)
        # Activate abilities
        click_after_random_delay(random.randint(860 + offset, 1020 + offset), random.randint(1840, 2080))
        click_after_random_delay(random.randint(1100 + offset, 1280 + offset), random.randint(1840, 2080), 100, 200)
        click_after_random_delay(random.randint(1620 + offset, 1740 + offset), random.randint(1840, 2080), 100, 200)
        click_after_random_delay(random.randint(1340 + offset, 1500 + offset), random.randint(1840, 2080), 2000, 2100)
        # End battle
        wait_for_battle_to_finish()

        # Return to base
        click_after_random_delay(random.randint(1800, 2100), random.randint(1800, 1940))

        check_specials()
        print("returned to base")


    running = False
    listener.stop()
    listener.join(timeout=1)



if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
