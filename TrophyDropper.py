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
    img.save("debug_trophy_ocr_dropper.png")
    print(f"Debug: Saved OCR region to debug_trophy_ocr_dropper.png")
    
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


def trophies_under(target: int, required_digits: int = 4) -> bool:
    global last_ocr_failed
    """Capture the box, OCR a number, return True if it's under target.

    required_digits: number of digits the OCR result must have (default 4)
    """
    # Hard-coded rectangle: (left, top, right, bottom)
    bbox = (140, 165, 240, 200)
    value = _ocr_number_from_region(bbox, required_digits=required_digits)
    if value is None:
        print("OCR failed to read the trophy count.")
        if (not last_ocr_failed):
            last_ocr_failed = True
            return False
        return True
    print(f"OCR saw: {value}")
    return value < int(target)


def wait_until_pixel_not_color(expected_rgb: Tuple[int, int, int], point: Tuple[int, int]) -> bool:
    """Wait until pixel (1,1) is no longer exactly expected_rgb.

    Returns True when the pixel changes
    """
    poll_interval: float = 0.05

    try:
        with mss.mss() as sct:
            while True:
                shot = sct.grab({"left": point[0], "top": point[1], "width": 1, "height": 1})
                # mss returns BGRA
                b, g, r, _ = np.array(shot)[0, 0]
                rgb = (int(r), int(g), int(b))
                if rgb != expected_rgb:
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
                shot = sct.grab({"left": point[0], "top": point[1], "width": 1, "height": 1})
                # mss returns BGRA
                b, g, r, _ = np.array(shot)[0, 0]
                rgb = (int(r), int(g), int(b))
                if rgb == expected_rgb:
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

    # Start a keyboard listener in a separate thread to listen for the quit command
    listener = keyboard.Listener(on_press=on_press)
    listener.start()
    keyboard_controller = Controller()

    # util func


    with mss.mss() as sct:
        while running:
            wait_until_pixel_color((33, 221, 255), (77, 35))
            time.sleep(2)
            if (trophies_under(target)):
                print(f"Exiting script as trophies are below {target}.")
                exit(0)
            # Click attack
            click_after_random_delay(random.randint(75, 175), random.randint(900, 1000))
            # Click find match
            click_after_random_delay(random.randint(1250, 1500), random.randint(600, 650))
            # Wait for base to be found
            time.sleep(2)
            wait_until_pixel_not_color((234, 239, 244), (1, 1))
            wait_until_pixel_color((247, 13, 23), (161, 776))
            # Select troop
            click_after_random_delay(random.randint(160, 260), random.randint(920, 1040))
            # Place Troop
            rand = random.randint(1, 2)
            if (rand == 1):
                click_after_random_delay(random.randint(1230, 1250), random.randint(140, 150))
            else:
                click_after_random_delay(random.randint(640, 660), random.randint(160, 175))
            # Surrender
            click_after_random_delay(random.randint(40, 220), random.randint(780, 830))
            click_after_random_delay(random.randint(1000, 1300), random.randint(650, 750))
            # Go home
            click_after_random_delay(random.randint(850, 1050), random.randint(900, 950))


        listener.join()



if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
