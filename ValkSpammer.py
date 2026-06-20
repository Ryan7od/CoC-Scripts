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
    """Wait 1000-2000 ms randomly, then left-click at screen coordinate (x, y)."""
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
    bbox = (130, 165, 240, 220)
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
                    is_match = colors_match(last_rgb, expected_rgb)
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
                if colors_match(get_pixel_rgb((1680, 855), sct), (198, 203, 198)):
                    # 1 Star
                    click_after_random_delay(random.randint(50, 180), random.randint(830, 870))
                    click_after_random_delay(random.randint(1000, 1250), random.randint(620, 720), 50, 200)
                    break

                if colors_match(get_pixel_rgb((900, 955), sct), (112, 187, 29)):
                    # Battle ended
                    break

                time.sleep(POLL_INTERVAL_SECONDS)
    except Exception as e:
        print(f"Battle end check failed: {e}")


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
    # click_after_random_delay(random.randint(40, 100), random.randint(760, 820))
    # click_after_random_delay(random.randint(750, 1100), random.randint(80, 140))
    # click_after_random_delay(random.randint(1680, 1840), random.randint(260, 300))
    # click_after_random_delay(random.randint(1830, 1880), random.randint(80, 120))

    # Start a keyboard listener in a separate thread to listen for the quit command
    listener = keyboard.Listener(on_press=on_press)
    listener.start()

    # offset for event troop ~145
    offset = 0

    while running:
        print("cycle initiated")
        wait_until_pixel_color((54, 236, 255), (77, 35))
        time.sleep(2)
        print("start cycle")
        # if (trophies_above(target)):
        #     exit(0)
        # Click attack
        click_after_random_delay(random.randint(50, 150), random.randint(920, 1020), 100, 200)
        # Click find match
        click_after_random_delay(random.randint(120, 440), random.randint(720, 800), 500, 1000)
        # Click attack on army screen
        # wait_until_pixel_color((189, 235, 137), (1620, 950))
        click_after_random_delay(random.randint(1480, 1760), random.randint(900, 950), 300, 600)
        # Wait for base to be found
        time.sleep(2)
        print("attack")
        wait_until_pixel_not_color((233, 242, 245), (0, 0))
        wait_until_pixel_color((252, 94, 101), (90, 835))
        print("base found")
        # Select troop  
        click_after_random_delay(random.randint(310 + offset, 380 + offset), random.randint(920, 1040))
        # Place troop
        place_in_interval((random.randint(110, 130), random.randint(440, 460)), (random.randint(710, 730), random.randint(20, 40)), 11, 50, 150)
        place_in_interval((random.randint(1160, 1170), random.randint(30, 40)), (random.randint(1790, 1800), random.randint(510, 520)), 11, 50, 150)
        place_in_interval((random.randint(1790, 1800), random.randint(520, 530)), (random.randint(1370, 1380), random.randint(850, 860)), 11, 50, 150)
        place_in_interval((random.randint(480, 500), random.randint(810, 830)), (random.randint(100, 120), random.randint(540, 560)), 11, 10, 150)
        # # Siege Machine
        # click_after_random_delay(random.randint(320 + offset, 420 + offset), random.randint(920, 1040), 500, 1000)
        # click_after_random_delay(random.randint(1790, 1800), random.randint(510, 530), 300, 500)
        # # EQ spells for siege
        # click_after_random_delay(random.randint(930 + offset, 1010 + offset), random.randint(920, 1040), 200, 300)
        # click_after_random_delay(random.randint(1380, 1400), random.randint(500, 520), 200, 300)
        # click_after_random_delay(random.randint(1380, 1400), random.randint(500, 520), 20, 50)
        # click_after_random_delay(random.randint(1380, 1400), random.randint(500, 520), 20, 50)
        # click_after_random_delay(random.randint(1380, 1400), random.randint(500, 520), 20, 50)
        # click_after_random_delay(random.randint(1380, 1400), random.randint(500, 520), 20, 50)
        # click_after_random_delay(random.randint(1380, 1400), random.randint(500, 520), 20, 50)
        # # EQ for heroes
        # click_after_random_delay(random.randint(1250, 1300), random.randint(550, 600), 200, 300)
        # click_after_random_delay(random.randint(1250, 1300), random.randint(550, 600), 20, 50)
        # click_after_random_delay(random.randint(1250, 1300), random.randint(550, 600), 20, 50)
        # click_after_random_delay(random.randint(1250, 1300), random.randint(550, 600), 20, 50)
        # click_after_random_delay(random.randint(1250, 1300), random.randint(550, 600), 20, 50)
        # click_after_random_delay(random.randint(1250, 1300), random.randint(550, 600), 20, 50)
        # Place heroes
        click_after_random_delay(random.randint(430 + offset, 510 + offset), random.randint(920, 1040), 300, 400)
        click_after_random_delay(random.randint(1410, 1430), random.randint(800, 820), 300, 400)
        click_after_random_delay(random.randint(550 + offset, 640 + offset), random.randint(920, 980), 300, 400)
        click_after_random_delay(random.randint(1470, 1490), random.randint(755, 770), 300, 400)
        click_after_random_delay(random.randint(670 + offset, 750 + offset), random.randint(920, 970), 300, 400)
        click_after_random_delay(random.randint(1500, 1520), random.randint(730, 745), 300, 400)
        click_after_random_delay(random.randint(800 + offset, 870 + offset), random.randint(920, 1040), 300, 400)
        click_after_random_delay(random.randint(1530, 1550), random.randint(707, 720), 300, 400)
        # Activate abilities
        click_after_random_delay(random.randint(430 + offset, 510 + offset), random.randint(920, 1040))
        click_after_random_delay(random.randint(550 + offset, 640 + offset), random.randint(920, 1040), 100, 200)
        click_after_random_delay(random.randint(810 + offset, 870 + offset), random.randint(920, 1040), 100, 200)
        click_after_random_delay(random.randint(670 + offset, 750 + offset), random.randint(920, 1040), 2000, 2100)
        # End battle
        wait_for_battle_to_finish()

        # Return to base
        click_after_random_delay(random.randint(900, 1050), random.randint(900, 970), 800, 950)
        print("returned to base")


    running = False
    listener.stop()
    listener.join(timeout=1)



if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
