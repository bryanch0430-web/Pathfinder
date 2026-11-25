# Pathfinder on TravelPlanner: 30-case test

Current `main` (`017e3bf`), live tools, writer **Claude Sonnet 5.5** (Poe), verifier **DeepSeek Flash**. Sandbox was off. The router was told the date was 1 March 2022, so a March 2022 trip is not treated as the past. Flights and weather still run against today's providers, which have no March 2022 fares or forecast.

## Sample

Public Hugging Face set `osunlp/TravelPlanner`, **test** split (1,000 of the 1,225). Seed `20261124`. Ten easy, ten medium, ten hard. Inside each level: 4 one-city trips, 3 two-city trips, 3 three-city trips, different origin/destination pairs.

The user message is the dataset `query`. The Evaluation tab also loads the row's origin, destination, dates, party size, budget (USD) and local constraints before that message. Three cases still asked a clarifying question; they were answered once with those same row values, then scored.

`reference_information` is the checking catalogue: the cities, attractions, restaurants and hotels that row is allowed to use.

## How many are satisfied

**0 / 30** meet every expected check at once (a saved plan, the right days, dates and party, every city named in that row's reference, the budget, every local constraint, and stops grounded in this run's tool calls).

What did hold, on the 29 plans that were saved:

| Check | Result |
|---|---|
| A plan was saved on the plan path | **29 / 30** |
| Days, start date and party size match the row | **29 / 29** |
| Every stop cites a tool result from this run | **29 / 29** |
| The plan is in the reference city, when the trip is one city | **12 / 12** |
| The plan visits every city listed in the reference, when the trip is 2 or 3 cities | **0 / 17** |
| Named stops that also appear in `reference_information` | **81 / 151** on one-city trips (54%). **4 / 136** on multi-city trips (3%) |
| Plan total is within the stated budget | **8 / 29** |
| The app's own constraint checker marks every constraint pass | **3 / 29** |

The three plans whose own checker is all pass are easy multi-city trips (`TP-E08`, `TP-E09`, `TP-E10`). They stay in budget and name some cities in the state. They do not use the cities in that row's reference.

One case, `TP-E06` (Atlanta to Colorado), was cut off at 300 seconds and saved no plan.

## Tool calls

On every saved plan the five workers ran:

| Worker | What came back |
|---|---|
| Places | Useful on 27 / 29. Two state-level trips returned nothing useful. |
| Hotels | Useful on 29 / 29. |
| Flights | Called on 29 / 29, and every one is **partly estimated**. Google Flights has no fare for March 2022, so the app inserts a labelled estimate. |
| Weather | Called on 29 / 29, all partial. March 2022 is outside the live forecast window. |
| Videos | 29 / 29 returned notes, up to 3 clips. Example: "22 Places to Visit in EUGENE". These are watch-notes, not places or prices. |

Grounding is separate from the reference list. A stop can cite a live Google result and still be absent from the 2022 catalogue.

## Constraints, as the Evaluation tab records them

Across 29 plans the verifier wrote **77** checks: **25 pass, 31 fail, 21 unknown**.

| Constraint | Pass | Fail | Unknown |
|---|---:|---:|---:|
| Stay within budget | 8 | 21 | 0 |
| Requested cuisines | 1 | 6 | 1 |
| House rule (pets, parties, visitors, smoking, children) | 2 | 2 | 8 |
| Room type (entire, private, shared, not shared) | 2 | 1 | 12 |
| Do not fly / do not self-drive | 3 | 1 | 0 |

Budget misses are mostly the estimated flight. Oakland to Eugene, budget **$1,100**: the plan total is **$2,330**, of which **$2,240** is the estimated return flight and $90 is local transport. The same pattern repeats on other one-city trips.

House rules and room type are usually **unknown**, not a clear fail. The hotel tool returns a Google place (name, a short blurb, generic types). It does not return the dataset's `house_rules` or `room type` column, so the checker cannot confirm "allows parties" or "entire room". Two were confirmed from the plan text: a pet-friendly Embassy Suites, and a private room kept all week.

"No flight" is a split result. On the two hard cases that forbid flying, the written itinerary is a drive and the verifier marks **pass**. The ticket worker still attaches an estimated flight option beside that plan. "No self-driving" passed once (taxi and transit) and failed once (a 161-minute drive was left in the day).

## Why multi-city trips miss the reference

The reference does not say "any two cities in Texas". It names a specific pair. For "2 cities in Texas" from Cincinnati, the reference cities are Houston and Dallas. For Providence to North Carolina, the reference cities are Wilmington and Fayetteville. The app planned Raleigh, Durham and Chapel Hill instead. That can be a reasonable reading of the sentence, and it is not the row's expected route, so the reference-city check fails and almost none of the place names match.

## Case list

`ref` is whether every reference city appears. `names` is how many plan stops are in that row's catalogue. `budget` is the plan total against the row budget.

| Case | Level | Trip | People | Plan | Ref cities | Names | Budget |
|---|---|---|---:|---|---|---|---|
| TP-E01 | easy | Oakland → Eugene, 3 days | 1 | yes | yes | 6/12 | 2330 / 1100 over |
| TP-E02 | easy | Miami → Des Moines, 3 days | 1 | yes | yes | 8/14 | 2330 / 1900 over |
| TP-E03 | easy | Denver → Fort Lauderdale, 3 days | 1 | yes | yes | 6/11 | 2330 / 2000 over |
| TP-E04 | easy | Omaha → Seattle, 3 days | 1 | yes | yes | 9/14 | 2330 / 1700 over |
| TP-E05 | easy | Providence → North Carolina, 5 days, 2 cities | 1 | yes | no (planned Raleigh area; reference is Wilmington, Fayetteville) | 0/5 | 2390 / 2100 over |
| TP-E06 | easy | Atlanta → Colorado, 7 days, 3 cities | — | no, timed out at 300s | — | — | — |
| TP-E07 | easy | Nashville → Arizona, 5 days, 2 cities | 1 | yes | no | 0/11 | 2390 / 2100 over |
| TP-E08 | easy | Des Moines → Arizona, 7 days, 3 cities | 1 | yes | no | 0/13 | 2450 / 2600 within |
| TP-E09 | easy | Philadelphia → Wisconsin, 7 days, 3 cities | 1 | yes | no | 0/11 | 2450 / 4900 within |
| TP-E10 | easy | Atlanta → Texas, 7 days, 3 cities | 1 | yes | no | 0/8 | 2450 / 5200 within |
| TP-M01 | medium | Las Vegas → Minot, 3 days | 8 | yes | yes | 6/12 | 18670 / 13500 over |
| TP-M02 | medium | Little Rock → Atlanta, 3 days | 8 | yes | yes | 8/13 | 18670 / 13600 over |
| TP-M03 | medium | Houston → Colorado Springs, 3 days | 2 | yes | yes | 6/13 | 4670 / 2200 over |
| TP-M04 | medium | La Crosse → Detroit, 3 days | 6 | yes | yes | 5/13 | 14000 / 2600 over |
| TP-M05 | medium | New York → Michigan, 5 days, 2 cities | 6 | yes | no | 1/7 | 14360 / 12500 over |
| TP-M06 | medium | St. Louis → Michigan, 5 days, 2 cities | 4 | yes | no | 1/7 | 9580 / 5400 over |
| TP-M07 | medium | Rochester → Michigan, 5 days, 2 cities | 2 | yes | no | 0/6 | 4790 / 4100 over |
| TP-M08 | medium | Grand Rapids → Texas, 7 days, 3 cities | 2 | yes | no | 0/5 | 4910 / 5100 within |
| TP-M09 | medium | Cedar Rapids → Texas, 7 days, 3 cities | 4 | yes | no | 0/5 | 9820 / 8600 over |
| TP-M10 | medium | Baltimore → Michigan, 7 days, 3 cities | 6 | yes | no | 1/8 | 14720 / 15900 within |
| TP-H01 | hard | Wichita → Dallas, 3 days | 2 | yes | yes | 8/13 | 4670 / 1400 over |
| TP-H02 | hard | Jacksonville → Boston, 3 days | 3 | yes | yes | 5/11 | 7000 / 1700 over |
| TP-H03 | hard | Indianapolis → Huntsville, 3 days | 2 | yes | yes | 7/13 | 4670 / 1000 over |
| TP-H04 | hard | San Francisco → Dallas, 3 days | 2 | yes | yes | 7/12 | 4670 / 2200 over |
| TP-H05 | hard | Austin → Louisiana, 5 days, 2 cities | 2 | yes | no | 0/9 | 4790 / 3900 over |
| TP-H06 | hard | Knoxville → Texas, 5 days, 2 cities | 2 | yes | no | 0/5 | 4790 / 3300 over |
| TP-H07 | hard | Santa Ana → Texas, 5 days, 2 cities | 2 | yes | no | 0/7 | 4790 / 4500 over |
| TP-H08 | hard | Dallas → Utah, 7 days, 3 cities | 2 | yes | no | 0/9 | 4910 / 7700 within |
| TP-H09 | hard | Dallas → Virginia, 7 days, 3 cities | 2 | yes | no | 1/10 | 4910 / 8500 within |
| TP-H10 | hard | New York → California, 7 days, 3 cities | 6 | yes | no | 0/10 | 14720 / 19200 within |

Raw per-case checks: `report_material/evaluation/tp30-results.json`.
