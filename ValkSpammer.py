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

# Flag for if OCR failed on last, to allow if a certain number is bad
last_ocr_failed = False  

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
    """OCR a number from screen region with minimal preprocessing"""
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


def wait_until_pixel_not_color(expected_rgb: Tuple[int, int, int], point: Tuple[int, int]) -> bool:
    """Wait until pixel (1,1) is no longer exactly expected_rgb.

    Returns True when the pixel changes
    """
    poll_interval: float = 0.05

    try:
        with mss.mss() as sct:
            while True:
                if get_pixel_rgb(point) != expected_rgb:
                    return True
                time.sleep(poll_interval)
    except Exception as e:
        print(f"Pixel check failed: {e}")
        return False
    
def wait_until_pixel_color(expected_rgb: Tuple[int, int, int], point: Tuple[int, int]) -> bool:
    """Wait until pixel (1,1) is no longer exactly expected_rgb.

    Returns True when the pixel changes
    """
    poll_interval: float = 0.05

    try:
        with mss.mss() as sct:
            while True:
                if get_pixel_rgb(point) == expected_rgb:
                    return True
                time.sleep(poll_interval)
    except Exception as e:
        print(f"Pixel check failed: {e}")
        return False

def get_pixel_rgb(point: Tuple[int, int]) -> Tuple[int, int, int]:
    try:
        with mss.mss() as sct:
            shot = sct.grab({"left": point[0], "top": point[1], "width": 1, "height": 1})
            # mss returns BGRA
            b, g, r, _ = np.array(shot)[0, 0]
            return (int(r), int(g), int(b))
    except Exception as e:
        print(f"Pixel check failed: {e}")
        return (0, 0, 0)


def place_in_interval(point1: Tuple[int, int], point2: Tuple[int, int], number_of_units: int, lowtime: int = 200, hightime: int = 500) -> None:
    xcoords = np.linspace(point1[0], point2[0], number_of_units)
    ycoords = np.linspace(point1[1], point2[1], number_of_units)
    for x, y in zip(xcoords, ycoords):
        click_after_random_delay(int(x), int(y), lowtime, hightime)


def main():
    # Increase the script's process priority to high
    try:
        p = psutil.Process(os.getpid())
        p.nice(psutil.HIGH_PRIORITY_CLASS)
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
    keyboard_controller = Controller()

    while running:
        wait_until_pixel_color((33, 221, 255), (77, 35))
        time.sleep(2)
        # if (trophies_above(target)):
        #     exit(0)
        # Click attack
        click_after_random_delay(random.randint(75, 175), random.randint(900, 1000))
        # Click find match
        click_after_random_delay(random.randint(130, 500), random.randint(740, 860))
        # Click attack on army screen
        # wait_until_pixel_color((189, 235, 137), (1620, 950))
        click_after_random_delay(random.randint(1525, 1850), random.randint(930, 980))
        # Wait for base to be found
        time.sleep(2)
        wait_until_pixel_not_color((235, 240, 245), (1, 1))
        wait_until_pixel_color((247, 13, 22), (90, 775))
        # Select troop  
        click_after_random_delay(random.randint(160, 260), random.randint(920, 1040))
        # Place troop
        place_in_interval((random.randint(210, 220), random.randint(510, 520)), (random.randint(880, 890), random.randint(30, 40)), 11, 50, 150)
        place_in_interval((random.randint(1160, 1170), random.randint(30, 40)), (random.randint(1790, 1800), random.randint(510, 520)), 11, 50, 150)
        place_in_interval((random.randint(1790, 1800), random.randint(520, 530)), (random.randint(1370, 1380), random.randint(850, 860)), 10, 50, 150)
        place_in_interval((random.randint(670, 680), random.randint(850, 860)), (random.randint(210, 220), random.randint(520, 530)), 10, 50, 150)
        # Siege Machine
        click_after_random_delay(random.randint(320, 420), random.randint(920, 1040))
        click_after_random_delay(random.randint(1790, 1800), random.randint(510, 530))
        # EQ spells for siege
        click_after_random_delay(random.randint(1070, 1180), random.randint(920, 1040), 300, 500)
        click_after_random_delay(random.randint(1380, 1400), random.randint(500, 520), 300, 500)
        click_after_random_delay(random.randint(1380, 1400), random.randint(500, 520), 20, 50)
        click_after_random_delay(random.randint(1380, 1400), random.randint(500, 520), 20, 50)
        click_after_random_delay(random.randint(1380, 1400), random.randint(500, 520), 20, 50)
        click_after_random_delay(random.randint(1380, 1400), random.randint(500, 520), 20, 50)
        click_after_random_delay(random.randint(1380, 1400), random.randint(500, 520), 20, 50)
        # EQ for heroes
        click_after_random_delay(random.randint(1250, 1300), random.randint(550, 600), 300, 500)
        click_after_random_delay(random.randint(1250, 1300), random.randint(550, 600), 20, 50)
        click_after_random_delay(random.randint(1250, 1300), random.randint(550, 600), 20, 50)
        click_after_random_delay(random.randint(1250, 1300), random.randint(550, 600), 20, 50)
        click_after_random_delay(random.randint(1250, 1300), random.randint(550, 600), 20, 50)
        click_after_random_delay(random.randint(1250, 1300), random.randint(550, 600), 20, 50)
        # Place heroes
        click_after_random_delay(random.randint(460, 560), random.randint(920, 1040), 300, 500)
        click_after_random_delay(random.randint(1410, 1430), random.randint(800, 820), 300, 500)
        click_after_random_delay(random.randint(620, 720), random.randint(920, 980), 300, 500)
        click_after_random_delay(random.randint(1470, 1490), random.randint(755, 770), 300, 500)
        click_after_random_delay(random.randint(760, 860), random.randint(920, 1000), 300, 500)
        click_after_random_delay(random.randint(1500, 1520), random.randint(730, 745), 300, 500)
        click_after_random_delay(random.randint(910, 1020), random.randint(920, 1040), 300, 500)
        click_after_random_delay(random.randint(1530, 1550), random.randint(707, 720), 300, 500)
        # Activate abilities
        click_after_random_delay(random.randint(460, 560), random.randint(920, 1040))
        click_after_random_delay(random.randint(620, 720), random.randint(920, 1040), 100, 200)
        click_after_random_delay(random.randint(760, 860), random.randint(920, 1040), 100, 200)
        click_after_random_delay(random.randint(910, 1020), random.randint(920, 1040), 100, 200)
        # End battle
        while True:
            if get_pixel_rgb((1629, 809)) == (196, 200, 194):
                # 1 Star
                click_after_random_delay(random.randint(60, 220), random.randint(780, 825))
                click_after_random_delay(random.randint(1020, 1320), random.randint(640, 740), 50, 200)
                break
            if get_pixel_rgb((900, 955)) == (108, 187, 31):
                # Battle ended
                break
            time.sleep(0.05)

        # Return to base
        click_after_random_delay(random.randint(840, 1080), random.randint(880, 960))



    listener.join()



if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
