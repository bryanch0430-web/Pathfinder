"""A curated, source-labelled atlas of real places.

Two jobs:

* **Sandbox evidence** for offline demos, reproducible evaluation and tests, so the
  same pipeline runs without spending quota or depending on a provider being up.
* **Fallback evidence** when a live provider is unavailable: the record is returned
  with ``data_status="stale"`` and a gap explains that live data was not reached.
  The planner still gets real, sourced places instead of nothing.

Coordinates are the place's own; prices are indicative and marked ``reference``.
"""

from __future__ import annotations

from typing import Any

from app.domain.models import GeoPoint, Money, SourceRef
from app.evidence.models import Evidence

CITIES: dict[str, dict[str, Any]] = {
    "hong kong": {"name": "Hong Kong", "country_code": "HK", "timezone": "Asia/Hong_Kong", "lat": 22.3193, "lng": 114.1694, "currency": "HKD"},
    "kyoto": {"name": "Kyoto", "country_code": "JP", "timezone": "Asia/Tokyo", "lat": 35.0116, "lng": 135.7681, "currency": "JPY"},
    "tokyo": {"name": "Tokyo", "country_code": "JP", "timezone": "Asia/Tokyo", "lat": 35.6762, "lng": 139.6503, "currency": "JPY"},
    "osaka": {"name": "Osaka", "country_code": "JP", "timezone": "Asia/Tokyo", "lat": 34.6937, "lng": 135.5023, "currency": "JPY"},
    "singapore": {"name": "Singapore", "country_code": "SG", "timezone": "Asia/Singapore", "lat": 1.3521, "lng": 103.8198, "currency": "SGD"},
    "taipei": {"name": "Taipei", "country_code": "TW", "timezone": "Asia/Taipei", "lat": 25.0330, "lng": 121.5654, "currency": "TWD"},
    "bangkok": {"name": "Bangkok", "country_code": "TH", "timezone": "Asia/Bangkok", "lat": 13.7563, "lng": 100.5018, "currency": "THB"},
    "seoul": {"name": "Seoul", "country_code": "KR", "timezone": "Asia/Seoul", "lat": 37.5665, "lng": 126.9780, "currency": "KRW"},
    "paris": {"name": "Paris", "country_code": "FR", "timezone": "Europe/Paris", "lat": 48.8566, "lng": 2.3522, "currency": "EUR"},
    "london": {"name": "London", "country_code": "GB", "timezone": "Europe/London", "lat": 51.5074, "lng": -0.1278, "currency": "GBP"},
}

# n name · a address · g (lat, lng) · s summary · h hours · p price · r rating · rc reviews
# t tags · c cuisine · u source url · src source label · m suggested minutes · b best time · step step-free
_ATTRACTIONS: dict[str, list[dict[str, Any]]] = {
    "hong kong": [
        dict(n="Victoria Peak", a="The Peak, Central", g=(22.2759, 114.1455), s="The island's highest hill, with the skyline view from Sky Terrace 428 and the Peak Circle Walk.", h=["10:00-23:00"], p=88, r=4.6, rc=48210, t=["viewpoint", "sunset", "family"], u="https://www.thepeak.com.hk/en", m=120, b="late afternoon"),
        dict(n="Star Ferry", a="Tsim Sha Tsui Star Ferry Pier", g=(22.2940, 114.1690), s="The 1888 cross-harbour ferry between Tsim Sha Tsui and Central; the cheapest harbour cruise in the city.", h=["06:30-23:30"], p=5, r=4.6, rc=21410, t=["harbour", "iconic", "budget"], u="https://www.starferry.com.hk/en/home", m=30, b="any"),
        dict(n="Tsim Sha Tsui Promenade", a="Salisbury Road, Tsim Sha Tsui", g=(22.2930, 114.1722), s="The Avenue of Stars waterfront facing the Hong Kong Island skyline, best at the 20:00 light show.", h=["24 hours"], p=0, r=4.5, rc=31020, t=["waterfront", "free", "night view"], u="https://www.discoverhongkong.com/eng/explore/attractions/avenue-of-stars.html", m=60, b="evening"),
        dict(n="M+ Museum", a="38 Museum Drive, West Kowloon", g=(22.3010, 114.1600), s="Visual culture museum in West Kowloon with contemporary art, design and moving image galleries.", h=["10:00-18:00", "closed Monday"], p=120, r=4.4, rc=9120, t=["museum", "art", "indoor"], u="https://www.mplus.org.hk/en/", m=150, b="morning"),
        dict(n="Hong Kong Palace Museum", a="8 Museum Drive, West Kowloon", g=(22.3020, 114.1580), s="Nine galleries of Chinese art and antiquities on long-term loan from the Palace Museum, Beijing.", h=["10:00-18:00", "closed Tuesday"], p=60, r=4.6, rc=7400, t=["museum", "history", "indoor"], u="https://www.hkpm.org.hk/en/home", m=120, b="morning"),
        dict(n="Wong Tai Sin Temple", a="2 Chuk Yuen Village, Wong Tai Sin", g=(22.3419, 114.1930), s="Taoist temple known for answered prayers, with fortune-tellers and a landscaped garden.", h=["07:00-17:30"], p=0, r=4.5, rc=18630, t=["temple", "culture", "free"], u="https://www2.hk/temples/wongtaisin/", m=60, b="morning"),
        dict(n="Tian Tan Buddha", a="Ngong Ping, Lantau Island", g=(22.2540, 113.9050), s="The 34-metre bronze Buddha above Ngong Ping, reached by the cable car or bus from Tung Chung.", h=["10:00-17:30"], p=268, r=4.6, rc=39400, t=["landmark", "outdoors", "lantau", "temple"], u="https://www.plm.org.hk/eng/buddha.php", m=180, b="morning"),
        dict(n="Temple Street Night Market", a="Temple Street, Yau Ma Tei", g=(22.3067, 114.1700), s="Evening market of stalls, dai pai dong food and Cantonese opera singers.", h=["14:00-00:00"], p=0, r=4.2, rc=15300, t=["market", "street food", "night"], u="https://www.discoverhongkong.com/eng/explore/attractions/temple-street-night-market.html", m=75, b="evening"),
        dict(n="Tai Kwun", a="10 Hollywood Road, Central", g=(22.2810, 114.1540), s="The former Central Police Station compound, restored as a centre for heritage and contemporary art.", h=["11:00-19:00"], p=0, r=4.5, rc=11800, t=["heritage", "art", "free"], u="https://www.taikwun.hk/en/", m=90, b="afternoon"),
        dict(n="Man Mo Temple", a="124 Hollywood Road, Sheung Wan", g=(22.2842, 114.1503), s="Mid-19th-century temple to the gods of literature and war, with hanging incense coils.", h=["08:00-18:00"], p=0, r=4.4, rc=8600, t=["temple", "heritage", "free"], u="https://www.discoverhongkong.com/eng/explore/attractions/man-mo-temple.html", m=30, b="morning"),
        dict(n="Ocean Park", a="180 Wong Chuk Hang Road, Aberdeen", g=(22.2350, 114.1700), s="Theme park and marine conservation centre with a cable car over the south side.", h=["10:00-18:00"], p=498, r=4.3, rc=26500, t=["theme park", "family", "animals"], u="https://www.oceanpark.com.hk/en", m=300, b="morning"),
        dict(n="Hong Kong Disneyland", a="Penny's Bay, Lantau Island", g=(22.3129, 114.0385), s="Lantau theme park of seven themed lands, castle shows and evening fireworks.", h=["10:00-20:30"], p=799, r=4.5, rc=71200, t=["theme park", "family", "full day", "lantau"], u="https://www.hongkongdisneyland.com/", m=420, b="morning"),
        dict(n="University of Hong Kong", a="Pokfulam Road, Pok Fu Lam", g=(22.2833, 114.1375), s="Hong Kong's oldest university: the Main Building, the University Museum and Art Gallery, and a hillside campus above Pok Fu Lam.", h=["09:00-18:00"], p=0, r=4.4, rc=13200, t=["university", "campus", "architecture", "pokfulam"], u="https://www.hku.hk/", m=240, b="morning"),
        dict(n="Cheung Chau", a="Cheung Chau Island, outlying islands", g=(22.2110, 114.0280), s="Car-free island of seafood, bike paths and the Bun Festival in May.", h=["ferry from Central 05:20-23:45"], p=0, r=4.4, rc=9100, t=["island", "outdoors", "seafood"], u="https://www.discoverhongkong.com/eng/explore/great-outdoor/cheung-chau.html", m=240, b="morning"),
        dict(n="Sai Kung", a="Sai Kung Town, New Territories", g=(22.3810, 114.2700), s="Harbour town and gateway to the geopark islands; seafood restaurants line the promenade.", h=["24 hours"], p=0, r=4.4, rc=11200, t=["outdoors", "seafood", "hiking"], u="https://www.discoverhongkong.com/eng/explore/great-outdoor/sai-kung.html", m=240, b="morning"),
        dict(n="Stanley", a="Stanley, Hong Kong Island South", g=(22.2180, 114.2180), s="South-side seaside town with a market, colonial buildings and a promenade.", h=["24 hours"], p=0, r=4.2, rc=7800, t=["market", "seaside", "relaxed"], u="https://www.discoverhongkong.com/eng/explore/attractions/stanley.html", m=120, b="afternoon"),
        dict(n="Hong Kong Museum of Art", a="10 Salisbury Road, Tsim Sha Tsui", g=(22.2933, 114.1720), s="Chinese antiquities, modern Hong Kong art and the Chih Lo Lou collection facing the harbour.", h=["10:00-18:00", "closed Thursday"], p=10, r=4.5, rc=6100, t=["museum", "art", "indoor"], u="https://hk.art.museum/en/web/ma/home.html", m=90, b="afternoon"),
    ],
    "kyoto": [
        dict(n="Kinkaku-ji", a="1 Kinkakujicho, Kita Ward", g=(35.0394, 135.7292), s="The Golden Pavilion, a Zen temple whose top two floors are covered in gold leaf above a mirror pond.", h=["09:00-17:00"], p=500, r=4.6, rc=72100, t=["temple", "iconic", "garden"], u="https://www.shokoku-ji.jp/kinkakuji/", m=75, b="morning"),
        dict(n="Fushimi Inari Taisha", a="68 Fukakusa Yabunouchicho, Fushimi Ward", g=(34.9671, 135.7727), s="Head shrine of Inari with thousands of vermilion torii gates winding up Mount Inari.", h=["24 hours"], p=0, r=4.7, rc=98400, t=["shrine", "hiking", "free"], u="https://inari.jp/en/", m=150, b="early morning"),
        dict(n="Kiyomizu-dera", a="1-294 Kiyomizu, Higashiyama Ward", g=(34.9949, 135.7850), s="Hillside temple with a wooden stage over the valley and the Otowa waterfall.", h=["06:00-18:00"], p=500, r=4.6, rc=83200, t=["temple", "viewpoint", "heritage"], u="https://www.kiyomizudera.or.jp/en/", m=90, b="morning"),
        dict(n="Arashiyama Bamboo Grove", a="Ukyo Ward, Arashiyama", g=(35.0170, 135.6710), s="A path through tall bamboo beside Tenryu-ji, best before the tour groups arrive.", h=["24 hours"], p=0, r=4.4, rc=61500, t=["nature", "free", "walk"], u="https://www.kyoto.travel/en/spot/1043", m=60, b="early morning"),
        dict(n="Gion", a="Gion, Higashiyama Ward", g=(35.0037, 135.7780), s="The geisha district of wooden machiya, tea houses and lantern-lit Hanamikoji street.", h=["24 hours"], p=0, r=4.5, rc=57800, t=["neighbourhood", "evening", "culture"], u="https://www.kyoto.travel/en/spot/1015", m=90, b="evening"),
        dict(n="Nijo Castle", a="541 Nijojo-cho, Nakagyo Ward", g=(35.0142, 135.7480), s="Tokugawa shogun's castle with nightingale floors and painted screens.", h=["08:45-17:00"], p=1300, r=4.5, rc=32400, t=["castle", "heritage", "garden"], u="https://nijo-jocastle.city.kyoto.lg.jp/?lang=en", m=100, b="morning"),
        dict(n="Ginkaku-ji", a="2 Ginkakujicho, Sakyo Ward", g=(35.0270, 135.7980), s="The Silver Pavilion and its moss garden along the Philosopher's Path.", h=["08:30-17:00"], p=500, r=4.5, rc=38600, t=["temple", "garden", "walk"], u="https://www.shokoku-ji.jp/ginkakuji/", m=75, b="morning"),
        dict(n="Nishiki Market", a="Nishikikoji-dori, Nakagyo Ward", g=(35.0050, 135.7649), s="Five blocks of food stalls known as Kyoto's kitchen.", h=["09:00-18:00"], p=0, r=4.3, rc=44100, t=["market", "street food"], u="https://www.kyoto.travel/en/spot/1213", m=60, b="lunch"),
        dict(n="Sanjusangen-do", a="657 Sanjusangendo Mawari-cho, Higashiyama Ward", g=(34.9877, 135.7717), s="Hall of 1,001 gilded Kannon statues.", h=["08:30-17:00"], p=600, r=4.4, rc=14200, t=["temple", "heritage", "indoor"], u="https://www.sanjusangendo.jp/", m=60, b="morning"),
        dict(n="Kyoto Imperial Palace", a="3 Kyoto Gyoen, Kamigyo Ward", g=(35.0254, 135.7622), s="The former imperial residence inside the large Kyoto Gyoen park.", h=["09:00-17:00", "closed Monday"], p=0, r=4.4, rc=12800, t=["heritage", "garden", "free"], u="https://sankan.kunaicho.go.jp/english/guide/kyoto.html", m=90, b="morning"),
        dict(n="Yasaka Shrine", a="625 Gionmachi Kitagawa, Higashiyama Ward", g=(35.0036, 135.7785), s="The Gion shrine whose July Gion Matsuri is one of Japan's great festivals.", h=["24 hours"], p=0, r=4.4, rc=26900, t=["shrine", "free", "evening"], u="https://www.yasaka-jinja.or.jp/en/", m=40, b="evening"),
    ],
    "tokyo": [
        dict(n="Senso-ji", a="2-3-1 Asakusa, Taito City", g=(35.7148, 139.7967), s="Tokyo's oldest temple, approached through the Kaminarimon gate and Nakamise shopping street.", h=["06:00-17:00"], p=0, r=4.5, rc=91200, t=["temple", "free", "heritage"], u="https://www.senso-ji.jp/english/", m=90, b="early morning"),
        dict(n="Tokyo Skytree", a="1-1-2 Oshiage, Sumida City", g=(35.7101, 139.8107), s="634 m broadcasting tower with observation decks over the Kanto plain.", h=["10:00-21:00"], p=2100, r=4.4, rc=65400, t=["viewpoint", "family"], u="https://www.tokyo-skytree.jp/en/", m=120, b="sunset"),
        dict(n="Meiji Jingu", a="1-1 Yoyogikamizonocho, Shibuya City", g=(35.6764, 139.6993), s="Shinto shrine in a 70-hectare forest of donated trees beside Yoyogi Park.", h=["06:00-16:30"], p=0, r=4.6, rc=48700, t=["shrine", "forest", "free"], u="https://www.meijijingu.or.jp/en/", m=75, b="morning"),
        dict(n="Shibuya Crossing", a="2 Chome Dogenzaka, Shibuya City", g=(35.6595, 139.7005), s="The world's busiest pedestrian scramble, overlooked by the Shibuya Sky deck.", h=["24 hours"], p=0, r=4.3, rc=38200, t=["neighbourhood", "free", "night view"], u="https://www.gotokyo.org/en/spot/18/index.html", m=45, b="evening"),
        dict(n="Tsukiji Outer Market", a="4 Chome Tsukiji, Chuo City", g=(35.6655, 139.7708), s="Stalls of tamagoyaki, sea urchin and knives beside the old inner market.", h=["05:00-14:00"], p=0, r=4.4, rc=52100, t=["market", "street food", "breakfast"], u="https://www.tsukiji.or.jp/english/", m=75, b="breakfast"),
        dict(n="Ueno Park", a="5-20 Ueno, Taito City", g=(35.7148, 139.7739), s="Museum quarter with Tokyo National Museum, Shinobazu Pond and cherry trees.", h=["24 hours"], p=0, r=4.4, rc=39800, t=["park", "museum", "free"], u="https://www.gotokyo.org/en/spot/69/index.html", m=120, b="morning"),
        dict(n="teamLab Planets", a="6-1-16 Toyosu, Koto City", g=(35.6490, 139.7900), s="Immersive digital art museum visited barefoot through water and mirrored rooms.", h=["09:00-22:00"], p=3800, r=4.5, rc=28700, t=["museum", "art", "indoor"], u="https://www.teamlab.art/e/planets/", m=120, b="afternoon"),
        dict(n="Shinjuku Gyoen", a="11 Naitomachi, Shinjuku City", g=(35.6852, 139.7100), s="Imperial garden of lawns, ponds and 1,000 cherry trees.", h=["09:00-17:30", "closed Monday"], p=500, r=4.6, rc=33400, t=["garden", "family", "relaxed"], u="https://www.env.go.jp/garden/shinjukugyoen/english/", m=90, b="morning"),
        dict(n="Tokyo Tower", a="4-2-8 Shibakoen, Minato City", g=(35.6586, 139.7454), s="1958 lattice tower with main and top decks above Shiba Park.", h=["09:00-23:00"], p=1200, r=4.3, rc=41200, t=["viewpoint", "iconic"], u="https://www.tokyotower.co.jp/en/", m=90, b="evening"),
        dict(n="Imperial Palace East Gardens", a="1-1 Chiyoda, Chiyoda City", g=(35.6852, 139.7528), s="Edo castle ruins and gardens open to the public, free, closed Monday and Friday.", h=["09:00-16:30", "closed Monday"], p=0, r=4.4, rc=22100, t=["garden", "heritage", "free"], u="https://www.kunaicho.go.jp/e-event/higashigyoen.html", m=75, b="morning"),
        dict(n="Nezu Museum", a="6-5-1 Minamiaoyama, Minato City", g=(35.6620, 139.7170), s="Asian antiquities in a Kuma-designed gallery with a garden and tea house.", h=["10:00-17:00", "closed Monday"], p=1100, r=4.5, rc=7300, t=["museum", "garden", "art"], u="https://www.nezu-muse.or.jp/en/", m=90, b="afternoon"),
        dict(n="Ghibli Museum", a="1-1-83 Shimorenjaku, Mitaka, Tokyo", g=(35.6962, 139.5704), s="Miyazaki's animation museum in Mitaka: a Catbus room, a rooftop robot and short films. Tickets are sold in advance for a fixed date and time.", h=["10:00-18:00", "closed Tuesday"], p=1000, r=4.6, rc=24100, t=["museum", "indoor", "art", "family"], u="https://www.ghibli-museum.jp/en/", m=120, b="morning"),
        dict(n="Unicorn Gundam Statue", a="DiverCity Tokyo Plaza, 1-1-10 Aomi, Koto City", g=(35.6252, 139.7756), s="The life-size Unicorn Gundam outside DiverCity in Odaiba, with transformation shows several times a day.", h=["24 hours"], p=0, r=4.6, rc=26800, t=["gundam", "anime", "landmark", "odaiba"], u="https://www.unicorn-gundam-statue.jp/", m=60, b="afternoon"),
    ],
    "osaka": [
        dict(n="Osaka Castle", a="1-1 Osakajo, Chuo Ward", g=(34.6873, 135.5259), s="The shogun's castle in a moat-ringed park, with an observation deck on the eighth floor.", h=["09:00-17:00"], p=600, r=4.5, rc=58900, t=["castle", "heritage", "park"], u="https://www.osakacastle.net/english/", m=120, b="morning"),
        dict(n="Dotonbori", a="Dotonbori, Chuo Ward", g=(34.6687, 135.5013), s="The neon canal district of takoyaki stalls, the Glico sign and giant crab.", h=["24 hours"], p=0, r=4.4, rc=82100, t=["night", "street food", "iconic"], u="https://www.osaka-info.jp/en/spot/1048", m=90, b="evening"),
        dict(n="Tsutenkaku", a="1-18-6 Ebisuhigashi, Naniwa Ward", g=(34.6524, 135.5063), s="The Shinsekai tower with a 100 m deck over the retro south-side district.", h=["09:00-21:00"], p=1000, r=4.2, rc=26100, t=["viewpoint", "neighbourhood"], u="https://www.tsutenkaku.co.jp/", m=60, b="evening"),
        dict(n="Umeda Sky Building", a="1-1-88 Oyodonaka, Kita Ward", g=(34.7051, 135.4900), s="Twin towers joined by a floating garden observatory 173 m up.", h=["09:30-22:30"], p=1500, r=4.4, rc=43100, t=["viewpoint", "sunset"], u="https://www.skybldg.co.jp/en/", m=90, b="sunset"),
        dict(n="Osaka Aquarium Kaiyukan", a="1-1-10 Kaigandori, Minato Ward", g=(34.6545, 135.4289), s="One of the world's largest aquariums, built around a Pacific tank with whale sharks.", h=["10:00-20:00"], p=2700, r=4.4, rc=37200, t=["aquarium", "family", "indoor"], u="https://www.kaiyukan.com/language/eng/", m=150, b="morning"),
        dict(n="Kuromon Market", a="2 Chome Nipponbashi, Chuo Ward", g=(34.6680, 135.5060), s="Covered market of seafood, wagyu skewers and fruit stalls.", h=["08:00-17:00"], p=0, r=4.3, rc=29400, t=["market", "street food"], u="https://kuromon.com/en/", m=60, b="lunch"),
        dict(n="Sumiyoshi Taisha", a="2-9-89 Sumiyoshi, Sumiyoshi Ward", g=(34.6120, 135.4930), s="Head of 2,300 Sumiyoshi shrines, with a vermilion drum bridge.", h=["06:30-17:00"], p=0, r=4.5, rc=16700, t=["shrine", "heritage", "free"], u="https://www.sumiyoshitaisha.net/", m=60, b="morning"),
        dict(n="Universal Studios Japan", a="2-1-33 Sakurajima, Konohana Ward", g=(34.6654, 135.4323), s="Film-themed park with Super Nintendo World and Harry Potter areas.", h=["09:00-20:00"], p=8900, r=4.4, rc=96400, t=["theme park", "family"], u="https://www.usj.co.jp/web/en/us", m=420, b="morning"),
    ],
    "singapore": [
        dict(n="Gardens by the Bay", a="18 Marina Gardens Drive", g=(1.2816, 103.8636), s="Supertree Grove, flower domes and the nightly Garden Rhapsody light show.", h=["09:00-21:00"], p=32, r=4.6, rc=78400, t=["garden", "family", "night"], u="https://www.gardensbythebay.com.sg/en.html", m=150, b="late afternoon"),
        dict(n="Marina Bay Sands SkyPark", a="10 Bayfront Avenue", g=(1.2834, 103.8607), s="The 57th-floor observation deck over the bay and the city.", h=["11:00-21:00"], p=32, r=4.5, rc=51200, t=["viewpoint", "sunset"], u="https://www.marinabaysands.com/attractions/skypark.html", m=75, b="sunset"),
        dict(n="Merlion Park", a="1 Fullerton Road", g=(1.2868, 103.8545), s="The half-lion half-fish fountain facing Marina Bay.", h=["24 hours"], p=0, r=4.4, rc=64300, t=["landmark", "free", "waterfront"], u="https://www.visitsingapore.com/en/see-do-singapore/architecture/merlion-park/", m=30, b="morning"),
        dict(n="Chinatown", a="Chinatown, Outram", g=(1.2830, 103.8440), s="Shophouses, the Buddha Tooth Relic Temple and hawker centres.", h=["24 hours"], p=0, r=4.3, rc=42100, t=["neighbourhood", "food", "heritage"], u="https://www.visitsingapore.com/en/see-do-singapore/places-to-see/chinatown/", m=120, b="afternoon"),
        dict(n="Singapore Botanic Gardens", a="1 Cluny Road", g=(1.3138, 103.8159), s="UNESCO-listed tropical garden with the National Orchid Garden.", h=["05:00-00:00"], p=0, r=4.7, rc=47800, t=["garden", "free", "family"], u="https://www.nparks.gov.sg/sbg", m=120, b="morning"),
        dict(n="Little India", a="Little India, Rochor", g=(1.3066, 103.8496), s="Colourful district of temples, spice shops and banana-leaf restaurants.", h=["24 hours"], p=0, r=4.3, rc=31200, t=["neighbourhood", "food"], u="https://www.visitsingapore.com/en/see-do-singapore/places-to-see/little-india/", m=90, b="afternoon"),
        dict(n="ArtScience Museum", a="6 Bayfront Avenue", g=(1.2863, 103.8593), s="Lotus-shaped museum of art, science and technology exhibitions.", h=["10:00-19:00"], p=25, r=4.5, rc=18700, t=["museum", "indoor", "art"], u="https://www.marinabaysands.com/museum.html", m=90, b="afternoon"),
        dict(n="Sentosa", a="Sentosa Island", g=(1.2494, 103.8303), s="Resort island of beaches, cable car and family attractions.", h=["24 hours"], p=0, r=4.3, rc=39800, t=["beach", "family", "outdoors"], u="https://www.sentosa.com.sg/", m=240, b="morning"),
    ],
    "taipei": [
        dict(n="Taipei 101", a="No. 7, Section 5, Xinyi Road", g=(25.0339, 121.5645), s="The bamboo-shaped tower with an 89th-floor observatory and a tuned mass damper.", h=["11:00-21:00"], p=600, r=4.5, rc=68100, t=["viewpoint", "iconic"], u="https://www.taipei-101.com.tw/en/", m=90, b="sunset"),
        dict(n="National Palace Museum", a="No. 221, Sec 2, Zhi Shan Road", g=(25.1024, 121.5485), s="The world's largest collection of Chinese imperial artefacts.", h=["09:00-17:00"], p=350, r=4.6, rc=41200, t=["museum", "history", "indoor"], u="https://www.npm.gov.tw/?l=2", m=150, b="morning"),
        dict(n="Chiang Kai-shek Memorial Hall", a="No. 21, Zhongshan South Road", g=(25.0340, 121.5216), s="White marble hall in a memorial park with the National Theatre and Concert Hall.", h=["09:00-18:00"], p=0, r=4.5, rc=33400, t=["landmark", "park", "free"], u="https://www.cksmh.gov.tw/en/", m=60, b="morning"),
        dict(n="Shilin Night Market", a="Shilin District", g=(25.0880, 121.5240), s="Taipei's largest night market for oyster omelettes and giant fried chicken.", h=["16:00-00:00"], p=0, r=4.3, rc=52800, t=["market", "street food", "night"], u="https://www.travel.taipei/en/attraction/details/730", m=90, b="evening"),
        dict(n="Beitou Hot Springs", a="Beitou District", g=(25.1370, 121.5030), s="Steaming sulphur springs, public baths and the Beitou Hot Spring Museum.", h=["09:00-18:00"], p=200, r=4.4, rc=26700, t=["hot spring", "relaxed"], u="https://www.travel.taipei/en/attraction/details/700", m=150, b="afternoon"),
        dict(n="Elephant Mountain", a="Xinyi District", g=(25.0270, 121.5710), s="A 20-minute stair climb to the classic view of Taipei 101.", h=["24 hours"], p=0, r=4.7, rc=31900, t=["hiking", "viewpoint", "free"], u="https://www.travel.taipei/en/attraction/details/744", m=90, b="sunset"),
        dict(n="Longshan Temple", a="No. 211, Guangzhou Street, Wanhua", g=(25.0375, 121.4990), s="The 1738 temple of Guanyin, busy with worshippers and lanterns.", h=["06:00-22:00"], p=0, r=4.5, rc=23800, t=["temple", "heritage", "free"], u="https://www.travel.taipei/en/attraction/details/677", m=40, b="morning"),
        dict(n="Ximending", a="Ximending, Wanhua", g=(25.0421, 121.5069), s="Pedestrian shopping and street-performance district.", h=["24 hours"], p=0, r=4.2, rc=45100, t=["shopping", "night", "street food"], u="https://www.travel.taipei/en/attraction/details/694", m=90, b="evening"),
    ],
}

_HOTELS: dict[str, list[dict[str, Any]]] = {
    "hong kong": [
        dict(n="The Peninsula Hong Kong", a="Salisbury Road, Tsim Sha Tsui", g=(22.2950, 114.1720), s="1928 colonial grande dame with harbour-view rooms and a fleet of green Rolls-Royces.", p=4200, r=4.8, rc=12400, t=["harbour view", "historic", "luxury"], area="Tsim Sha Tsui"),
        dict(n="Hotel ICON", a="17 Science Museum Road, Tsim Sha Tsui East", g=(22.3000, 114.1770), s="Design hotel with a rooftop pool and a harbour-facing restaurant.", p=1800, r=4.6, rc=9600, t=["pool", "harbour view", "design"], area="Tsim Sha Tsui"),
        dict(n="Cordis, Hong Kong", a="555 Shanghai Street, Mong Kok", g=(22.3190, 114.1690), s="Mong Kok hotel with a rooftop pool and direct mall access.", p=1300, r=4.5, rc=8100, t=["pool", "family", "mall"], area="Mong Kok"),
        dict(n="Ovolo Central", a="2 Arbuthnot Road, Central", g=(22.2810, 114.1555), s="Small design hotel in Central with breakfast and social hour included.", p=1500, r=4.4, rc=2300, t=["central", "boutique", "breakfast"], area="Central"),
    ],
    "kyoto": [
        dict(n="Hotel Granvia Kyoto", a="901 Higashishiokoji-cho, Shimogyo Ward", g=(34.9855, 135.7585), s="Built into Kyoto Station, with a pool and direct train access.", p=22000, r=4.4, rc=12100, t=["station", "pool", "family"], area="Kyoto Station"),
        dict(n="Kyoto Tower Hotel", a="721-1 Higashishiokoji-cho, Shimogyo Ward", g=(34.9870, 135.7590), s="Simple rooms opposite the station tower, good value for early trains.", p=9000, r=4.0, rc=6800, t=["station", "budget"], area="Kyoto Station"),
        dict(n="The Ace Hotel Kyoto", a="245-2 Kurumayacho, Nakagyo Ward", g=(35.0090, 135.7590), s="Design hotel in a former telephone exchange, with a Stumptown coffee bar.", p=25000, r=4.5, rc=3400, t=["design", "central", "coffee"], area="Nakagyo"),
        dict(n="Yoshida-sanso Ryokan", a="59-1 Yoshidakaguraokacho, Sakyo Ward", g=(35.0270, 135.7860), s="Family-run ryokan with garden baths near Ginkaku-ji.", p=45000, r=4.7, rc=900, t=["ryokan", "quiet", "garden"], area="Sakyo"),
    ],
    "tokyo": [
        dict(n="Park Hyatt Tokyo", a="3-7-1-2 Nishishinjuku, Shinjuku City", g=(35.6855, 139.6905), s="High-floor rooms with Mount Fuji views and the New York Bar.", p=90000, r=4.7, rc=7600, t=["view", "luxury", "shinjuku"], area="Shinjuku"),
        dict(n="Mitsui Garden Hotel Ginza Premier", a="8-13-1 Ginza, Chuo City", g=(35.6680, 139.7620), s="Ginza tower hotel with a top-floor bar facing Tokyo Tower.", p=30000, r=4.4, rc=5200, t=["ginza", "view", "central"], area="Ginza"),
        dict(n="Shinjuku Granbell Hotel", a="2-14-5 Kabukicho, Shinjuku City", g=(35.6950, 139.7020), s="Compact design rooms in Kabukicho, walkable to the station.", p=18000, r=4.2, rc=6100, t=["shinjuku", "nightlife", "budget"], area="Shinjuku"),
        dict(n="Hoshinoya Tokyo", a="1-9-1 Otemachi, Chiyoda City", g=(35.6860, 139.7660), s="A ryokan on the 17th floor of an Otemachi tower, with a rooftop onsen.", p=120000, r=4.8, rc=1800, t=["ryokan", "onsen", "luxury"], area="Otemachi"),
    ],
    "osaka": [
        dict(n="The St. Regis Osaka", a="3-6-12 Honmachi, Chuo Ward", g=(34.6820, 135.5010), s="Midosuji boulevard hotel with butler service and a top-floor bar.", p=55000, r=4.6, rc=4300, t=["luxury", "central"], area="Honmachi"),
        dict(n="Cross Hotel Osaka", a="2-5-15 Shinsaibashi-suji, Chuo Ward", g=(34.6720, 135.5010), s="Directly off Shinsaibashi shopping street, minutes from Dotonbori.", p=22000, r=4.4, rc=8900, t=["dotonbori", "shopping", "central"], area="Shinsaibashi"),
        dict(n="Hotel Nikko Osaka", a="1-3-3 Nishi-Shinsaibashi, Chuo Ward", g=(34.6740, 135.5000), s="Large Midosuji hotel with airport limousine buses at the door.", p=26000, r=4.4, rc=7100, t=["central", "airport bus", "family"], area="Shinsaibashi"),
    ],
    "singapore": [
        dict(n="Marina Bay Sands", a="10 Bayfront Avenue", g=(1.2834, 103.8607), s="The three-tower hotel with the SkyPark infinity pool over the bay.", p=680, r=4.7, rc=42300, t=["pool", "iconic", "bay"], area="Marina Bay"),
        dict(n="Raffles Singapore", a="1 Beach Road", g=(1.2949, 103.8542), s="1887 colonial hotel, home of the Singapore Sling.", p=950, r=4.8, rc=8900, t=["historic", "luxury"], area="City Hall"),
        dict(n="Pan Pacific Singapore", a="7 Raffles Boulevard", g=(1.2925, 103.8587), s="Marina Square hotel with a club lounge and bay-view pool.", p=420, r=4.5, rc=11200, t=["pool", "bay", "family"], area="Marina Bay"),
        dict(n="Hotel G Singapore", a="200 Middle Road", g=(1.2990, 103.8540), s="Compact design hotel between Bugis and Bras Basah.", p=220, r=4.2, rc=4800, t=["budget", "design", "central"], area="Bugis"),
    ],
    "taipei": [
        dict(n="W Taipei", a="No. 10, Section 5, Zhongxiao East Road", g=(25.0405, 121.5650), s="Design hotel above the Taipei City Hall MRT, with a rooftop pool.", p=12000, r=4.5, rc=6400, t=["pool", "design", "xinyi"], area="Xinyi"),
        dict(n="Regent Taipei", a="No. 3, Lane 39, Section 2, Zhongshan North Road", g=(25.0530, 121.5230), s="Zhongshan hotel with a shopping arcade and rooftop pool.", p=7800, r=4.5, rc=5600, t=["pool", "shopping", "central"], area="Zhongshan"),
        dict(n="amba Taipei Ximen", a="No. 77, Section 2, Wuchang Street", g=(25.0450, 121.5070), s="Cinema-themed hotel in Ximending, walkable to the night market.", p=4200, r=4.3, rc=3900, t=["ximending", "budget", "design"], area="Ximending"),
    ],
}

_MEALS: dict[str, list[dict[str, Any]]] = {
    "hong kong": [
        dict(n="Tim Ho Wan (Sham Shui Po)", a="9-11 Fuk Wing Street, Sham Shui Po", g=(22.3310, 114.1620), s="The cheapest Michelin-starred dim sum in the world; expect a queue.", p=90, r=4.2, rc=18300, c="dim sum", h=["10:00-21:00"], t=["dim sum", "michelin", "budget"]),
        dict(n="Yung Kee", a="32-40 Wellington Street, Central", g=(22.2830, 114.1560), s="1942 Cantonese institution known for roast goose.", p=280, r=3.8, rc=7600, c="cantonese", h=["11:00-23:00"], t=["roast goose", "traditional"]),
        dict(n="Mak's Noodle", a="77 Wellington Street, Central", g=(22.2835, 114.1565), s="Wonton noodles in a clear prawn broth since 1968.", p=60, r=4.1, rc=8900, c="noodles", h=["11:00-21:00"], t=["wonton", "budget"]),
        dict(n="Australia Dairy Company", a="47 Parkes Street, Jordan", g=(22.3040, 114.1710), s="Cha chaan teng famous for scrambled eggs and milk pudding.", p=55, r=4.0, rc=12400, c="cha chaan teng", h=["07:00-23:00"], t=["breakfast", "eggs", "budget"]),
        dict(n="Ho Lee Fook", a="1 Elgin Street, Central", g=(22.2815, 114.1540), s="Modern Cantonese from a open kitchen, roast wagyu short rib.", p=380, r=4.2, rc=4200, c="modern cantonese", h=["18:00-23:00"], t=["dinner", "modern"]),
    ],
    "kyoto": [
        dict(n="Nishiki Warai", a="Nishikikoji-dori, Nakagyo Ward", g=(35.0052, 135.7645), s="Okonomiyaki and yakisoba in the Nishiki arcade.", p=1400, r=4.1, rc=2100, c="okonomiyaki", h=["11:00-22:00"], t=["casual", "budget"]),
        dict(n="Gion Tanto", a="Gion, Higashiyama Ward", g=(35.0030, 135.7770), s="Kyo-ryori set meals in a Gion townhouse.", p=3200, r=4.3, rc=1700, c="kyo-ryori", h=["11:30-21:00"], t=["traditional", "dinner"]),
        dict(n="Honke Owariya", a="322 Kurumayacho, Nakagyo Ward", g=(35.0070, 135.7640), s="Soba house founded in 1465, the oldest noodle shop in Kyoto.", p=1800, r=4.3, rc=2600, c="soba", h=["11:00-19:00"], t=["soba", "historic"]),
        dict(n="Kyoto Gogyo", a="452 Jumokucho, Nakagyo Ward", g=(35.0030, 135.7620), s="Charred-miso ramen in a dark, stylish room.", p=1300, r=4.2, rc=3100, c="ramen", h=["11:30-22:00"], t=["ramen", "dinner"]),
        dict(n="Ippudo Kyoto", a="Shijo-dori, Shimogyo Ward", g=(35.0035, 135.7680), s="Hakata tonkotsu ramen chain, quick and reliable.", p=1200, r=4.0, rc=4200, c="ramen", h=["11:00-23:00"], t=["ramen", "casual"]),
    ],
    "tokyo": [
        dict(n="Sushi Dai", a="6-21-1 Toyosu, Koto City", g=(35.6450, 139.7870), s="Toyosu market sushi counter with a legendary morning queue.", p=4500, r=4.6, rc=3800, c="sushi", h=["05:00-14:00"], t=["sushi", "breakfast", "market"]),
        dict(n="Afuri Ebisu", a="1-1-7 Ebisu, Shibuya City", g=(35.6460, 139.7100), s="Yuzu-scented shio ramen, light and citrusy.", p=1400, r=4.2, rc=5100, c="ramen", h=["11:00-23:00"], t=["ramen", "dinner"]),
        dict(n="Tonkatsu Maisen Aoyama", a="4-8-5 Jingumae, Shibuya City", g=(35.6680, 139.7060), s="Tonkatsu in a converted bathhouse since 1965.", p=1900, r=4.3, rc=6400, c="tonkatsu", h=["11:00-22:00"], t=["tonkatsu", "casual"]),
        dict(n="Ichiran Shibuya", a="1-22-7 Jinnan, Shibuya City", g=(35.6610, 139.6980), s="Solo-booth tonkotsu ramen, open late.", p=1200, r=4.1, rc=8900, c="ramen", h=["24 hours"], t=["ramen", "late", "budget"]),
        dict(n="Ain Soph Journey", a="3-8-9 Shinjuku, Shinjuku City", g=(35.6935, 139.7045), s="Shinjuku vegan restaurant: pancakes, pasta and set meals, all plant-based.", p=1800, r=4.4, rc=2600, c="vegan", h=["11:30-21:00"], t=["vegetarian", "vegan", "casual"]),
        dict(n="Tsuta", a="1-14-1 Sugamo, Toshima City", g=(35.7340, 139.7390), s="Michelin-starred shoyu soba with truffle oil.", p=1600, r=4.2, rc=2900, c="ramen", h=["11:00-15:00"], t=["ramen", "michelin"]),
    ],
    "osaka": [
        dict(n="Daruma Kushikatsu", a="1-5-4 Ebisuhigashi, Naniwa Ward", g=(34.6520, 135.5060), s="Shinsekai kushikatsu, deep-fried skewers with a no-double-dip rule.", p=1800, r=4.2, rc=4300, c="kushikatsu", h=["11:00-22:00"], t=["fried", "casual"]),
        dict(n="Harukoma", a="5-5-2 Tenjinbashi, Kita Ward", g=(34.7000, 135.5110), s="Sushi counter in Tenjinbashisuji with generous cuts.", p=3500, r=4.4, rc=2200, c="sushi", h=["11:30-21:00"], t=["sushi", "dinner"]),
        dict(n="Endo Sushi", a="1-1-86 Noda, Fukushima Ward", g=(34.6900, 135.4700), s="Market sushi since 1907, five-piece sets by the Osaka fish market.", p=2500, r=4.3, rc=1900, c="sushi", h=["05:00-14:00"], t=["sushi", "breakfast"]),
        dict(n="Takoyaki Wanaka", a="1-2-32 Namba, Chuo Ward", g=(34.6660, 135.5000), s="Octopus balls in Namba, crisp outside and molten inside.", p=600, r=4.1, rc=3400, c="takoyaki", h=["10:00-22:00"], t=["street food", "budget"]),
        dict(n="Green Earth", a="1-3-20 Higashishinsaibashi, Chuo Ward", g=(34.6730, 135.5000), s="Osaka vegan kitchen: set meals of brown rice, seasonal vegetables and soy mains.", p=1800, r=4.4, rc=2100, c="vegan", h=["11:30-21:00"], t=["vegetarian", "vegan", "casual"]),
    ],
    "singapore": [
        dict(n="Lau Pa Sat", a="18 Raffles Quay", g=(1.2808, 103.8505), s="Victorian market hall whose Satay Street takes over the lane at night.", p=18, r=4.2, rc=14200, c="hawker", h=["24 hours"], t=["hawker", "satay", "budget"]),
        dict(n="Maxwell Food Centre", a="1 Kadayanallur Street", g=(1.2803, 103.8440), s="Chinatown hawker centre, home of Tian Tian chicken rice.", p=12, r=4.4, rc=18700, c="hawker", h=["08:00-22:00"], t=["hawker", "chicken rice", "budget"]),
        dict(n="Jumbo Seafood Riverside", a="30 Merchant Road", g=(1.2870, 103.8460), s="Chilli crab and black pepper crab by the river.", p=95, r=4.3, rc=9800, c="seafood", h=["11:30-22:00"], t=["chilli crab", "dinner"]),
        dict(n="Song Fa Bak Kut Teh", a="11 New Bridge Road", g=(1.2875, 103.8475), s="Peppery pork-rib soup since 1969.", p=22, r=4.3, rc=7600, c="bak kut teh", h=["09:00-21:30"], t=["soup", "casual"]),
    ],
    "taipei": [
        dict(n="Din Tai Fung Xinyi", a="No. 194, Section 2, Xinyi Road", g=(25.0300, 121.5320), s="The xiaolongbao original, with 18 folds per dumpling.", p=650, r=4.5, rc=21400, c="xiaolongbao", h=["10:00-21:00"], t=["dumplings", "family"]),
        dict(n="Yongkang Beef Noodle", a="No. 17, Lane 31, Section 2, Jinshan South Road", g=(25.0280, 121.5290), s="Braised beef noodle soup in Yongkang Street.", p=280, r=4.2, rc=6700, c="noodles", h=["11:00-21:00"], t=["beef noodle", "casual"]),
        dict(n="Raohe Night Market", a="Raohe Street, Songshan District", g=(25.0510, 121.5770), s="Night market anchored by the Ciyou Temple; pepper buns at the entrance.", p=200, r=4.4, rc=15600, c="street food", h=["17:00-00:00"], t=["night market", "budget"]),
        dict(n="Addiction Aquatic", a="No. 18, Alley 2, Lane 410, Minzu East Road", g=(25.0650, 121.5360), s="Seafood market with a standing sashimi counter.", p=900, r=4.4, rc=4300, c="seafood", h=["09:00-22:00"], t=["sashimi", "market"]),
    ],
}


def city_meta(destination: str) -> dict[str, Any] | None:
    key = _key(destination)
    return dict(CITIES[key]) if key else None


ALIASES = {
    "nyc": "new york", "new york city": "new york", "manhattan": "new york",
    "sf": "san francisco", "san fran": "san francisco", "bay area": "san francisco",
    "hk": "hong kong", "tky": "tokyo", "osa": "osaka",
}


def _key(destination: str) -> str:
    raw = (destination or "").strip().lower()
    if raw in CITIES:
        return raw
    if raw in ALIASES:
        return ALIASES[raw]
    for alias, name in ALIASES.items():
        if alias in raw:
            return name
    for name in CITIES:
        if name in raw:
            return name
    return ""


def _build(entries: list[dict[str, Any]], kind: str, city: str, prefix: str) -> list[Evidence]:
    out: list[Evidence] = []
    meta = CITIES.get(city, {})
    currency = meta.get("currency", "HKD")
    for index, entry in enumerate(entries, start=1):
        lat, lng = entry["g"]
        name = entry["n"]
        url = entry.get("u") or f"https://www.google.com/maps/search/?api=1&query={name.replace(' ', '+')}"
        source = "Official site" if entry.get("u") else "Google Maps"
        ref = f"{prefix}{index}"
        tags = list(entry.get("t", []))
        if kind == "hotel":
            tags = tags
        price = entry.get("p")
        out.append(
            Evidence(
                ref=ref,
                kind=kind,  # type: ignore[arg-type]
                name=name,
                address=entry.get("a", ""),
                geo=GeoPoint(lat=lat, lng=lng),
                summary=entry.get("s", ""),
                source_refs=[
                    SourceRef(title=name, url=url, source=source, fetched_at=None)
                ],
                data_status="ok",
                rating=entry.get("r"),
                review_count=entry.get("rc"),
                price_level=None,
                cuisine=entry.get("c"),
                opening_hours=list(entry.get("h", [])),
                hours_stated=bool(entry.get("h")),
                price=Money(amount=float(price), currency=currency) if price is not None else None,
                price_kind="reference" if price is not None else "none",
                tags=tags,
                source_types=[kind],
                suggested_minutes=int(entry.get("m", 120 if kind == "attraction" else 60)),
                best_time=entry.get("b", "any"),
                area=entry.get("area", ""),
            )
        )
    return out


def attractions(city: str, *, stale: bool = False) -> list[Evidence]:
    out = _build(_ATTRACTIONS.get(_key(city), []), "attraction", _key(city), "a")
    if stale:
        for item in out:
            item.data_status = "stale"
    return out


def hotels(city: str, *, stale: bool = False) -> list[Evidence]:
    out = _build(_HOTELS.get(_key(city), []), "hotel", _key(city), "h")
    if stale:
        for item in out:
            item.data_status = "stale"
    return out


def meals(city: str, *, stale: bool = False) -> list[Evidence]:
    out = _build(_MEALS.get(_key(city), []), "meal", _key(city), "m")
    if stale:
        for item in out:
            item.data_status = "stale"
    return out


def known_cities() -> list[str]:
    return list(CITIES)


# -- additional cities ---------------------------------------------------

from app.evidence.atlas_us import ATTRACTIONS as _US_ATTRACTIONS  # noqa: E402
from app.evidence.atlas_us import CITIES as _US_CITIES  # noqa: E402
from app.evidence.atlas_us import HOTELS as _US_HOTELS  # noqa: E402
from app.evidence.atlas_us import MEALS as _US_MEALS  # noqa: E402

CITIES.update(_US_CITIES)
_ATTRACTIONS.update(_US_ATTRACTIONS)
_HOTELS.update(_US_HOTELS)
_MEALS.update(_US_MEALS)
