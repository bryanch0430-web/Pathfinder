---
name: read-a-wish
description: Use when a message names a place to go and a thing to see there. The thing to see is a wish, not a second city.
---

# Read a visit wish

A place to go and a thing to see are different slots.

- "I want to go to Japan to see Gundam" means destination Japan, origin null, and preference "must-see: Gundam". Gundam is an attraction. It is not a city, not a stop, and not the departure city.
- Set origin only when the message says the trip leaves from that place ("from Hong Kong", "leaving Osaka"). Never copy the destination into origin.
- A country is the destination. Do not invent a city inside it. Leave the city for a later question.
- "to see X" and "to visit X" are wishes when X is not a city the trip is going to. Write one preference, "must-see: X".
