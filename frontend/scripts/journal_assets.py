"""Prepare the journal's local photography. Run with Python and Pillow."""
from pathlib import Path
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    'mountain-morning': ('photography', '01865dc7'),
    'crosswalk': ('photography', '176da998'),
    'ocean-play': ('photography', '191a0097'),
    'grass-daydream': ('photography', '46d52646'),
    'birds': ('photography', '98d7c531'),
    'alpine-walk': ('photography', 'aec94afc'),
    'on-the-road': ('photography', 'b6674e4a'),
    'rainy-afternoon': ('photography', 'd5b1a877'),
    'camping': ('photography', 'd883223e'),
    'moon': ('photography', 'e18f9f47'),
    'sunlit-pause': ('photography', 'e54db8a5'),
    'friends': ('photography', 'e6efeef7'),
    'valley': ('photography', 'e9461727'),
    'surfers': ('photography', 'eb080185'),
    'laughter': ('photography', 'f769aa64'),
    'enjoy-now': ('creative', '0122837f'),
    'cloud-daydream': ('creative', '1cc08fc0'),
    'mountain-collage': ('creative', '328606a7'),
    'tulips': ('creative', '35169f77'),
    'blue-seats': ('creative', '7104fd9d'),
    'color-city': ('creative', '774470ca'),
    'green-trail': ('creative', '800dff80'),
    'city-type': ('creative', 'e5a9b966'),
    'sky-streaks': ('creative', 'fc87c397'),
}

if __name__ == '__main__':
    destination = ROOT / 'public' / 'images' / 'journal'
    destination.mkdir(parents=True, exist_ok=True)
    for name, (folder, prefix) in SOURCES.items():
        source = next((ROOT / 'media' / 'mmm' / folder).glob(prefix + '*'))
        with Image.open(source) as original:
            image = ImageOps.exif_transpose(original).convert('RGB')
            image.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
            image.save(destination / (name + '.webp'), quality=84, method=6)
    print(f'Prepared {len(SOURCES)} journal assets in {destination}')
