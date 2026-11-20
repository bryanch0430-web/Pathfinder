"""Cut the home page's product images out of the guide screenshots.

    python frontend/scripts/home_crops.py        (needs Pillow)

The Capabilities panels on the home page show the app itself. Their figures are 4:3.5 with a parallax
image 130% as tall, so an image fills the width when it is about 0.88 wide per 1 tall, and its top and
bottom ~11% drift in and out of view: keep what matters in the middle. Boxes are (left, top, right,
bottom) in the 1440x900 shots from capture_guide.py; re-check them after a re-shoot.
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1] / "public"
CROPS = {
    "cap-changes": ("05e-budget-from-chat", (500, 36, 1260, 900)),  # receipts, a message, the reply
    "cap-sketch": ("07c-sketch-day", (300, 36, 1060, 900)),         # a day page and its pager
    "cap-map": ("08-studio-map", (400, 100, 1100, 896)),            # the route map and the heads-up panel
    "cap-budget": ("12-budget-suggestions", (740, 56, 1420, 830)),  # summary, target, suggested rows
    # "How it works" thumbnails: 4:5 frames with the same parallax, so about 0.61 wide per 1 tall.
    "step-1": ("02-brief-step-1", (490, 300, 830, 853)),            # the brief's first step
    "step-2": ("04-one-shot-itinerary", (515, 70, 855, 623)),       # the draft arriving
    "step-3": ("07c-sketch-day", (300, 190, 640, 743)),             # a sketchbook page
    "step-4": ("10-export-menu", (1122, 58, 1420, 543)),            # the export menu
}

if __name__ == "__main__":
    out = ROOT / "home"
    out.mkdir(exist_ok=True)
    for name, (shot, box) in CROPS.items():
        image = Image.open(ROOT / "guide" / f"{shot}.png").convert("RGB").crop(box)
        image.save(out / f"{name}.png", optimize=True)
        print(name, image.size, f"{(out / f'{name}.png').stat().st_size // 1024} kB")
