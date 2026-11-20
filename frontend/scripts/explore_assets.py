"""Prepare the Explore page's images from the ignored media/ library. Run with Python and Pillow.

    python frontend/scripts/explore_assets.py

Writes WebP derivatives to public/images/explore/ (committed), so the page doesn't depend on media/,
which Git ignores. Long edges are capped and orientation is corrected.
"""
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / 'media'
OUT = ROOT / 'public' / 'images' / 'explore'

# Destination photographs: (file in media/web, long edge).
PLACES = {
    'kyoto-garden': 'david-emrich-VCM99u6HltA-unsplash.jpg',
    'porto-douro': 'will-goodman-1EikowqH9fs-unsplash.jpg',
    'tokyo-alley': 'matthieu-buhler-PaFHv0Zi71E-unsplash.jpg',
    'alpine-lake': 'datingscout-RlQ29vvbU2Q-unsplash.jpg',
    'prague-rooftops': 'fredy-martinez-frd7WNzipdU-unsplash.jpg',
    'seoul-palace': 'brady-bellini-t5dGNNQVwg8-unsplash.jpg',
    'fuji-blossom': 'jj-ying-9Qwbfa_RM94-unsplash.jpg',
    'budapest-aerial': 'philipp-trubchenko-oOTo9nR7f9Q-unsplash.jpg',
    'sydney-harbour': 'caleb-JmuyB_LibRo-unsplash.jpg',
    'izakaya-lane': 'pema-g-lama-6cfK0SEtpbY-unsplash.jpg',
    'pagoda-fuji': '3rd.jpg',
    'luxembourg-dusk': 'pedro-lastra-5g8dJvtYRYA-unsplash.jpg',
    'dubai-marina': 'kate-trysh-U3CntDq16yY-unsplash.jpg',
    'osaka-night': 'shigeki-wakabayashi-6nuz52vsbWc-unsplash.jpg',
    'new-york': 'pierre-blache-VMNG8BYFQfs-unsplash.jpg',
    'fuji-dusk': 'hero-bg.jpg',
    'contrail': 'ben-klewais-nLE3eLaQA6A-unsplash.jpg',
}

# Mood photographs from media/mmm/photography, by file-name prefix.
MOODS = {
    'prayer-flags-sunrise': '01865dc7',
    'sunlit-water': 'e54db8a5',
    'surfers-cliff': 'eb080185',
    'beach-leap': '191a0097',
    'alpine-jacket': 'aec94afc',
    'valley-rain-run': 'e9461727',
    'climber-blue-rock': 'ba5db82e',
    'motorbike-road': 'b6674e4a',
    'camp-by-the-sea': 'd883223e',
    'pink-umbrellas': 'd5b1a877',
    'headlamp-night': 'fb4c759d',
    'big-moon': 'e18f9f47',
    'gulls-and-ramparts': '98d7c531',
    'crosswalk-shadows': '7c19ca78',
}


def save(image: Image.Image, target: Path, edge: int) -> None:
    image = ImageOps.exif_transpose(image)
    image = image.convert('RGB')
    image.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, quality=82, method=6)


if __name__ == '__main__':
    for name, file in PLACES.items():
        with Image.open(MEDIA / 'web' / file) as source:
            save(source, OUT / 'places' / f'{name}.webp', 1600)
    for name, prefix in MOODS.items():
        source_path = next((MEDIA / 'mmm' / 'photography').glob(prefix + '*'))
        with Image.open(source_path) as source:
            save(source, OUT / 'moods' / f'{name}.webp', 1400)
    total = sum(f.stat().st_size for f in OUT.rglob('*.webp'))
    print(f'Prepared {len(PLACES) + len(MOODS)} images, {total // 1024} kB, in {OUT}')
