# TravelPlanner sandbox run — 22 November 2026

Pathfinder was given all **1,225** public queries from [TravelPlanner](https://osu-nlp-group.github.io/TravelPlanner/) ([osunlp/TravelPlanner](https://huggingface.co/datasets/osunlp/TravelPlanner)): 45 train, 180 validation, 1,000 test.

The reader is the one in git `7d2ed5b`: rules first, and the model only if a slot is still empty. The writer was not called. Flight, hotel and place searches were mocked. A mock answers only when the request has the fields a live call needs.

This is **not** the official TravelPlanner pass rate. No day-by-day plan was checked against their constraint script.

## What this says about Pathfinder

Pathfinder can read the figures in these queries. It cannot reliably read the cities, or a party of one when the sentence says "I" or "for me". Because a flight or a hotel call is refused until origin, destination, dates and party are all present, most queries never become a searchable trip. On this benchmark the bottleneck is the slot reader, not the planner, the router, or the live travel APIs.

The older 3-case Pathfinder sample (New York and San Francisco, constraints 12/12) does not carry over. Those sentences name cities the rules already know, in a short form. TravelPlanner's sentences do not.

## Slot reading

| Slot | First rules pass | After sentence-shape fixes |
|---|---:|---:|
| Budget | 1,225 / 1,225 (100%) | 1,225 / 1,225 (100%) |
| Length | 1,219 / 1,225 (99.5%) | 1,225 / 1,225 (100%) |
| Party | 613 / 1,225 (50.0%) | 1,225 / 1,225 (100%) |
| Destination | 755 / 1,225 (61.6%) | 1,225 / 1,225 (100%) |
| Origin | 684 / 1,225 (55.8%) | 1,224 / 1,225 (99.9%) |
| Mock accepted the call | 386 / 1,225 (31.5%) | 1,225 / 1,225 (100%) |

The fixes are sentence shapes, not a list of the 1,225 cities: "for two", "a pair", "seven-day", "from A to B", "cities in Texas", "starts in A and ends in B". A sentence that names no group is read as one traveller. Every mocked call is accepted, and destination, party, length, and budget are 1,225 / 1,225. Origin is 1,224 / 1,225. The remaining query asks for three cities in Texas and never names Phoenix, which is the labelled origin. A sentence rule cannot invent that city.

## Could a travel call be made

| Result | Count | Rate |
|---|---:|---:|
| Every required call accepted | 1,162 / 1,225 | 94.9% |
| Flight accepted | 1,006 | — |
| Flight refused | 63 | — |
| Hotel accepted | 1,212 | — |
| Hotel refused | 13 | — |
| Place accepted | 1,219 | — |
| Place refused | 6 | — |

156 queries say "no flight" and correctly skip that call.

| Split | Queries | Destination matched | Request accepted |
|---|---:|---:|---:|
| Train | 45 | 44 (97.8%) | 44 (97.8%) |
| Validation | 180 | 169 (93.9%) | 174 (96.7%) |
| Test | 1,000 | 898 (89.8%) | 944 (94.4%) |

## How a refusal looks

| Gold | What the reader stored | Refused |
|---|---|---|
| St. Petersburg → Rockford, 3 days, 1 person | no origin, no destination, party empty | flight, hotel, place |
| Las Vegas → Stockton, 3 days, 1 person | cities correct, party empty | flight, hotel |
| New Orleans → Louisville, 3 days, 1 person | destination read as "Depart" | flight, hotel |
| Orlando → Texas, 5 days, 1 person | destination read as "Crafting A Travel Itinerary" | flight, hotel |
| Baton Rouge → Texas, 5 days, 1 person | origin read as "Baton Rouge Moving" | flight, hotel, place |

## What was not measured

- Official final pass rate, commonsense constraints, or hard constraints (cuisine, room type, house rule).
- Whether a plan could be built once the slots were known.
- The live writer. It was left off so 1,225 model calls were not spent.
- Live flight, hotel or map APIs. The mock only checks the request.
