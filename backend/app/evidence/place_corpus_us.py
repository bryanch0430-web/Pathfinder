"""US city evidence for the curated place corpus (New York, San Francisco).

Kept in its own module so the main place corpus stays readable; ``place_corpus`` merges these
dicts at import time.
"""

from __future__ import annotations

from typing import Any

CITIES: dict[str, dict[str, Any]] = {
    "new york": {"name": "New York", "country_code": "US", "timezone": "America/New_York", "lat": 40.7128, "lng": -74.0060, "currency": "USD"},
    "san francisco": {"name": "San Francisco", "country_code": "US", "timezone": "America/Los_Angeles", "lat": 37.7749, "lng": -122.4194, "currency": "USD"},
}

ATTRACTIONS: dict[str, list[dict[str, Any]]] = {
    "new york": [
        dict(n="The Metropolitan Museum of Art", a="1000 Fifth Avenue, Upper East Side", g=(40.7794, -73.9632), s="One of the world's great museums: Egyptian temples, European painting and the Temple of Dendur.", h=["10:00-17:00", "closed Wednesday"], p=30, r=4.8, rc=84200, t=["museum", "art", "indoor"], u="https://www.metmuseum.org/", m=180, b="morning"),
        dict(n="Central Park", a="Midtown Manhattan", g=(40.7829, -73.9654), s="The 843-acre park of the Ramble, Bow Bridge and Bethesda Terrace.", h=["06:00-01:00"], p=0, r=4.8, rc=154000, t=["park", "free", "outdoors"], u="https://www.centralparknyc.org/", m=120, b="morning"),
        dict(n="Statue of Liberty", a="Liberty Island", g=(40.6892, -74.0445), s="The 1886 copper statue reached by ferry from Battery Park; book crown tickets early.", h=["09:00-17:00"], p=25, r=4.6, rc=62100, t=["landmark", "ferry", "family"], u="https://www.nps.gov/stli/index.htm", m=180, b="morning"),
        dict(n="Empire State Building", a="20 West 34th Street, Midtown", g=(40.7484, -73.9857), s="The 1931 landmark with an 86th-floor open-air deck.", h=["10:00-23:00"], p=44, r=4.7, rc=78900, t=["viewpoint", "iconic", "night view"], u="https://www.esbnyc.com/", m=90, b="sunset"),
        dict(n="Times Square", a="Broadway and 7th Avenue", g=(40.7580, -73.9855), s="The billboard canyon and theatre district, loudest after dark.", h=["24 hours"], p=0, r=4.4, rc=98200, t=["night", "free", "neighbourhood"], u="https://www.timessquarenyc.org/", m=45, b="evening"),
        dict(n="The High Line", a="Gansevoort Street to 34th Street", g=(40.7480, -74.0048), s="An elevated park on a former freight rail line through Chelsea.", h=["07:00-22:00"], p=0, r=4.7, rc=54100, t=["walk", "free", "outdoors"], u="https://www.thehighline.org/", m=75, b="afternoon"),
        dict(n="Brooklyn Bridge", a="Brooklyn Bridge Promenade", g=(40.7061, -73.9969), s="The 1883 suspension bridge walk between Manhattan and DUMBO.", h=["24 hours"], p=0, r=4.7, rc=46700, t=["walk", "viewpoint", "free"], u="https://www.nyc.gov/html/dot/html/infrastructure/brooklyn-bridge.shtml", m=60, b="morning"),
        dict(n="Museum of Modern Art", a="11 West 53rd Street, Midtown", g=(40.7614, -73.9776), s="MoMA's modern collection: Van Gogh, Warhol and the sculpture garden.", h=["10:30-17:30"], p=30, r=4.6, rc=51200, t=["museum", "art", "indoor"], u="https://www.moma.org/", m=120, b="morning"),
        dict(n="Top of the Rock", a="30 Rockefeller Plaza, Midtown", g=(40.7593, -73.9794), s="The Rockefeller Center deck with the classic view of the Empire State Building.", h=["09:00-00:00"], p=40, r=4.7, rc=58400, t=["viewpoint", "sunset"], u="https://www.rockefellercenter.com/attractions/top-of-the-rock-observation-deck/", m=75, b="sunset"),
        dict(n="9/11 Memorial & Museum", a="180 Greenwich Street, Financial District", g=(40.7115, -74.0134), s="The twin reflecting pools and the underground museum of the attacks.", h=["09:00-19:00"], p=36, r=4.8, rc=63100, t=["museum", "history", "indoor"], u="https://www.911memorial.org/", m=120, b="morning"),
    ],
    "san francisco": [
        dict(n="Golden Gate Bridge", a="Golden Gate Bridge, Presidio", g=(37.8199, -122.4783), s="The 1937 orange suspension bridge, walked or biked across.", h=["24 hours"], p=0, r=4.8, rc=76200, t=["landmark", "outdoors", "walk"], u="https://www.goldengate.org/", m=90, b="morning"),
        dict(n="Alcatraz Island", a="Alcatraz Island, San Francisco Bay", g=(37.8267, -122.4230), s="The former federal prison reached by ferry; book weeks ahead.", h=["09:00-16:30"], p=45, r=4.6, rc=32400, t=["history", "ferry", "indoor"], u="https://www.nps.gov/alca/index.htm", m=180, b="morning"),
        dict(n="Fisherman's Wharf", a="Jefferson Street, Fisherman's Wharf", g=(37.8080, -122.4177), s="Sea lions at Pier 39, clam chowder and the Hyde Street cable car.", h=["24 hours"], p=0, r=4.2, rc=61800, t=["waterfront", "family", "seafood"], u="https://www.fishermanswharf.org/", m=90, b="afternoon"),
        dict(n="Golden Gate Park", a="Golden Gate Park, Richmond District", g=(37.7694, -122.4862), s="A 1,017-acre park with the de Young, Academy of Sciences and Stow Lake.", h=["05:00-00:00"], p=0, r=4.7, rc=44200, t=["park", "free", "outdoors"], u="https://www.goldengatepark.org/", m=120, b="morning"),
        dict(n="Painted Ladies", a="Steiner Street, Alamo Square", g=(37.7763, -122.4326), s="The row of Victorian houses on Alamo Square's postcard view.", h=["24 hours"], p=0, r=4.4, rc=18700, t=["viewpoint", "free", "architecture"], u="https://www.sftravel.com/article/painted-ladies", m=40, b="afternoon"),
        dict(n="Ferry Building", a="1 Ferry Building, Embarcadero", g=(37.7955, -122.3937), s="The 1898 ferry terminal turned food hall, with Saturday farmers market.", h=["10:00-18:00"], p=0, r=4.5, rc=25100, t=["food hall", "market", "indoor"], u="https://www.ferrybuildingmarketplace.com/", m=75, b="morning"),
        dict(n="Coit Tower", a="1 Telegraph Hill Boulevard", g=(37.8024, -122.4058), s="Art-deco tower with WPA murals and a 360-degree city view.", h=["10:00-18:00"], p=10, r=4.5, rc=12800, t=["viewpoint", "art"], u="https://www.sfrecpark.org/1219/Coit-Tower", m=60, b="afternoon"),
        dict(n="de Young Museum", a="50 Hagiwara Tea Garden Drive", g=(37.7715, -122.4686), s="Fine arts museum in Golden Gate Park with a tower observation deck.", h=["09:30-17:15", "closed Monday"], p=20, r=4.5, rc=9600, t=["museum", "art", "indoor"], u="https://www.famsf.org/visit/de-young", m=120, b="morning"),
    ],
}

HOTELS: dict[str, list[dict[str, Any]]] = {
    "new york": [
        dict(n="The Standard High Line", a="848 Washington Street, Meatpacking", g=(40.7410, -74.0080), s="Design hotel straddling the High Line with a rooftop bar.", p=420, r=4.5, rc=4200, t=["design", "rooftop", "chelsea"], area="Meatpacking"),
        dict(n="citizenM Times Square", a="218 West 50th Street, Midtown", g=(40.7600, -73.9860), s="Compact smart rooms steps from the theatre district.", p=260, r=4.4, rc=5100, t=["midtown", "budget", "design"], area="Midtown"),
        dict(n="The Ned NoMad", a="1170 Broadway, NoMad", g=(40.7460, -73.9880), s="Club-style hotel in the former Johnston Building with a jazz bar.", p=520, r=4.6, rc=1900, t=["luxury", "nomad", "bar"], area="NoMad"),
    ],
    "san francisco": [
        dict(n="Fairmont San Francisco", a="950 Mason Street, Nob Hill", g=(37.7923, -122.4103), s="The 1907 grande dame on Nob Hill with the Tonga Room.", p=480, r=4.6, rc=5300, t=["historic", "luxury", "nob hill"], area="Nob Hill"),
        dict(n="Hotel Zephyr", a="250 Beach Street, Fisherman's Wharf", g=(37.8085, -122.4170), s="Playful wharf-side hotel with fire pits and a games courtyard.", p=290, r=4.4, rc=4700, t=["wharf", "family", "fun"], area="Fisherman's Wharf"),
        dict(n="The Marker", a="501 Geary Street, Union Square", g=(37.7855, -122.4080), s="Boutique hotel near Union Square with a lively bar.", p=260, r=4.3, rc=3200, t=["union square", "boutique"], area="Union Square"),
    ],
}

MEALS: dict[str, list[dict[str, Any]]] = {
    "new york": [
        dict(n="Katz's Delicatessen", a="205 East Houston Street, Lower East Side", g=(40.7223, -73.9874), s="Pastrami on rye since 1888, with the ticket-and-tip ritual.", p=32, r=4.5, rc=18400, c="delicatessen", h=["08:00-22:45"], t=["pastrami", "historic"]),
        dict(n="Joe's Pizza", a="7 Carmine Street, Greenwich Village", g=(40.7305, -74.0025), s="Thin-crust New York slices eaten standing up.", p=6, r=4.6, rc=12800, c="pizza", h=["10:00-02:00"], t=["pizza", "budget"]),
        dict(n="Chelsea Market", a="75 9th Avenue, Chelsea", g=(40.7425, -74.0060), s="A food hall in a former biscuit factory under the High Line.", p=25, r=4.5, rc=38600, c="food hall", h=["07:00-22:00"], t=["market", "food hall"]),
        dict(n="Russ & Daughters", a="179 East Houston Street, Lower East Side", g=(40.7220, -73.9880), s="Appetising store since 1914: lox, bagels and whitefish.", p=22, r=4.5, rc=5900, c="appetising", h=["09:00-18:00"], t=["breakfast", "bagels", "vegetarian options"]),
        dict(n="Superiority Burger", a="119 Avenue A, East Village", g=(40.7266, -73.9817), s="Vegetarian burger counter with a cult following; everything on the menu is meat-free.", p=18, r=4.5, rc=3100, c="vegetarian", h=["11:30-22:00"], t=["vegetarian", "burgers", "casual"]),
        dict(n="Vanessa's Dumpling House", a="118 Eldridge Street, Lower East Side", g=(40.7180, -73.9910), s="Cheap dumplings and sesame pancakes, with vegetable fillings.", p=12, r=4.4, rc=4600, c="chinese", h=["10:00-22:00"], t=["vegetarian options", "dumplings", "budget"]),
    ],
    "san francisco": [
        dict(n="Tartine Bakery", a="600 Guerrero Street, Mission", g=(37.7614, -122.4241), s="Country loaf and morning buns with a permanent queue.", p=14, r=4.5, rc=8900, c="bakery", h=["07:30-17:00"], t=["breakfast", "bakery"]),
        dict(n="Swan Oyster Depot", a="1517 Polk Street, Nob Hill", g=(37.7915, -122.4205), s="An 18-seat seafood counter since 1912.", p=55, r=4.6, rc=4100, c="seafood", h=["10:30-17:30"], t=["seafood", "lunch"]),
        dict(n="House of Prime Rib", a="1906 Van Ness Avenue, Nob Hill", g=(37.7935, -122.4230), s="Cart-carved prime rib with all the sides, since 1949.", p=65, r=4.6, rc=6200, c="steakhouse", h=["17:00-22:00"], t=["dinner", "classic"]),
        dict(n="Ferry Building Marketplace", a="1 Ferry Building, Embarcadero", g=(37.7955, -122.3937), s="Oysters, cheese and coffee under one roof.", p=25, r=4.5, rc=15400, c="market", h=["10:00-18:00"], t=["lunch", "market", "vegetarian options"]),
        dict(n="Greens Restaurant", a="2 Marina Boulevard, Fort Mason", g=(37.8065, -122.4324), s="Vegetarian restaurant with bay views, serving since 1979.", p=42, r=4.4, rc=2800, c="vegetarian", h=["11:30-21:00"], t=["vegetarian", "dinner", "bay view"]),
    ],
}
