import argparse
import re
import shutil
import time
from pathlib import Path

import cv2
import numpy as np
import pyautogui
import pytesseract


DEFAULT_KEYWORDS = ("\u6d6e\u6f02", "\u6e85\u8d77", "\u6c34\u82b1")
DEFAULT_TESSERACT_EXE = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
DEFAULT_TESSDATA_DIR = r"C:\Program Files\Tesseract-OCR\tessdata"


def configure_tesseract():
    exe = shutil.which("tesseract")
    if exe is None and Path(DEFAULT_TESSERACT_EXE).exists():
        exe = DEFAULT_TESSERACT_EXE

    if exe:
        pytesseract.pytesseract.tesseract_cmd = exe

    if Path(DEFAULT_TESSDATA_DIR).exists():
        return exe, DEFAULT_TESSDATA_DIR
    return exe, None


def parse_region(value):
    parts = [int(part.strip()) for part in value.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("region must be x,y,width,height")
    x, y, width, height = parts
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("region width and height must be positive")
    return x, y, width, height


def parse_keywords(value):
    keywords = tuple(part.strip() for part in value.split(",") if part.strip())
    if not keywords:
        raise argparse.ArgumentTypeError("keywords cannot be empty")
    return keywords


def default_subtitle_region():
    width, height = pyautogui.size()
    return (int(width * 0.55), int(height * 0.12), int(width * 0.43), int(height * 0.62))


def screenshot_image(region):
    return pyautogui.screenshot(region=region)


def preprocess_for_ocr(image, scale=2.0):
    frame = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    scaled = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    denoised = cv2.GaussianBlur(scaled, (3, 3), 0)
    return cv2.threshold(denoised, 150, 255, cv2.THRESH_BINARY)[1]


def normalize_text(text):
    return re.sub(r"\s+", "", text).replace("\uff1a", ":")


def ocr_text(region, lang="chi_sim+eng", scale=2.0):
    _image, _processed, text = ocr_capture(region, lang=lang, scale=scale)
    return text


def ocr_capture(region, lang="chi_sim+eng", scale=2.0):
    """Capture once and return the original image, processed OCR image, and text."""
    _exe, tessdata_dir = configure_tesseract()
    image = screenshot_image(region)
    processed = preprocess_for_ocr(image, scale=scale)
    config_parts = []
    if tessdata_dir:
        config_parts.extend(["--tessdata-dir", f'"{tessdata_dir}"'])
    config_parts.extend(["--psm", "6"])
    text = pytesseract.image_to_string(processed, lang=lang, config=" ".join(config_parts))
    return image, processed, normalize_text(text)


def text_matches(text, keywords=DEFAULT_KEYWORDS):
    compact = normalize_text(text)
    return all(keyword in compact for keyword in keywords)


def check_tesseract_ready():
    exe, _tessdata_dir = configure_tesseract()
    if exe is None:
        return False, "tesseract.exe was not found in PATH."
    try:
        version = pytesseract.get_tesseract_version()
    except Exception as exc:
        return False, str(exc)
    try:
        langs = set(pytesseract.get_languages(config=""))
    except Exception as exc:
        return False, f"could not list languages: {exc}"
    if "chi_sim" not in langs:
        return False, "Chinese Simplified language data chi_sim.traineddata was not found."
    return True, f"{version}; exe={exe}"


def wait_for_splash_ocr(region, keywords, lang, poll_seconds, timeout_seconds, scale=2.0):
    started_at = time.monotonic()
    last_text = ""

    while True:
        text = ocr_text(region, lang=lang, scale=scale)
        last_text = text

        if text_matches(text, keywords):
            return True, text

        if timeout_seconds and time.monotonic() - started_at > timeout_seconds:
            return False, last_text

        time.sleep(poll_seconds)


def main():
    parser = argparse.ArgumentParser(
        description="Auto fish in Minecraft Java by OCR-reading the subtitle splash cue."
    )
    parser.add_argument("--region", type=parse_region, default=None, help="Subtitle scan area: x,y,width,height")
    parser.add_argument("--keywords", type=parse_keywords, default=DEFAULT_KEYWORDS)
    parser.add_argument("--lang", default="chi_sim+eng")
    parser.add_argument("--poll", type=float, default=0.05)
    parser.add_argument("--ocr-scale", type=float, default=2.0)
    parser.add_argument("--cast-delay", type=float, default=1.5)
    parser.add_argument("--recast-delay", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=45.0)
    args = parser.parse_args()

    ready, detail = check_tesseract_ready()
    if not ready:
        raise RuntimeError(
            f"OCR is not ready: {detail}\n"
            "Install Tesseract OCR with Chinese Simplified language data and add it to PATH."
        )

    region = args.region or default_subtitle_region()

    print("Starting in 5 seconds. Switch to Minecraft Java with subtitles enabled.")
    print(f"OCR region: {region}")
    print(f"Keywords: {args.keywords}; language: {args.lang}; Tesseract: {detail}")
    print("Stop with Ctrl+C or move the mouse to the top-left corner.")
    time.sleep(5)

    pyautogui.FAILSAFE = True
    print("Auto fishing started.")

    catches = 0
    resets = 0
    pyautogui.click(button="right")
    time.sleep(args.cast_delay)

    while True:
        found, text = wait_for_splash_ocr(
            region=region,
            keywords=args.keywords,
            lang=args.lang,
            poll_seconds=args.poll,
            timeout_seconds=args.timeout,
            scale=args.ocr_scale,
        )

        if found:
            catches += 1
            print(f"Splash subtitle detected: {text!r}. Reel in. catches={catches}")
            pyautogui.click(button="right")
        else:
            resets += 1
            print(f"No splash in {args.timeout:.0f}s. Last OCR: {text!r}. resets={resets}")
            pyautogui.click(button="right")

        time.sleep(args.recast_delay)
        pyautogui.click(button="right")
        time.sleep(args.cast_delay)


if __name__ == "__main__":
    main()
