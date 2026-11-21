---
name: judge-a-place
description: Use after a place name has been split out of a sentence, before the map is asked. Examples decide what is not a trip, what has many branches, and what is several real places.
---

# Judge a place

Compare only the name that was split out. A known city is never one of these. A longer name that adds a real place, such as "Hell, Norway", is not the bare example "hell". That is the same rule libpostal uses when it keeps a qualified address apart from a bare token (https://github.com/openvenues/libpostal).

Do not accept a gazetteer hit whose label is a different name. Pelias, the open geocoder, returns several scored candidates and leaves a tie for a person to choose (https://github.com/pelias/pelias). A low place-search share means the same thing here: ask, do not pick.

## Not a trip

Do not call the map. Say it is not a place you can map, then use this line. These are bodies, myths, and fictional places that a fuzzy geocoder pins to a real town (Moon becomes Muhu, Mars becomes a town in Pennsylvania).

Reply: Extraplanar travel off Earth might be happening in 2077.

Examples:
- mars
- jupiter
- saturn
- venus
- mercury
- neptune
- uranus
- pluto
- moon
- the moon
- sun
- galaxy
- milky way
- outer space
- black hole
- orbit
- international space station
- heaven
- hell
- olympus
- atlantis
- valhalla
- avalon
- elysium
- shangri la
- hogwarts
- narnia
- wakanda
- gotham
- mordor
- middle earth
- asgard
- tatooine
- pandora
- neverland

## Many branches

Do not pick one shop. Ask which one. If the place search reports how sure it is of a single match, say that share, then use this line.

Reply: Name the city, or the branch.

Examples:
- mcdonald's
- starbucks
- kfc
- burger king
- pizza hut
- subway
- 7-eleven
- ikea
- costco
- walmart
- uniqlo
- zara
- muji
- haidilao
- yoshinoya
- family mart
- lawson
- mos burger
- cafe de coral
- fairwood
- genki sushi

## Several places

The same name is a real city in more than one country. Do not pick the first hit. Ask which one, then use this line.

Reply: Name the city and the country.

Examples:
- springfield
- portland
- cambridge
- richmond
- kingston
- georgetown
- salem
- manchester
- birmingham
- victoria
- alexandria
- newport
