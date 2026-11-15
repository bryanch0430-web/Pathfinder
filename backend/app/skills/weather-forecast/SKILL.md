---
name: weather-forecast
description: Reference for how the weather agent searches. Not a path the router chooses, and not injected into a writer prompt.
---

# Read the forecast

## When to use

The weather agent owns the daily forecast for the trip dates. Python calls it when the trip has dates. This file records that search. It is not a traveller-facing path.

## Tools

`weather.forecast` on Open-Meteo.

## Steps

1. Ask for the trip dates.
2. Dates past the forecast horizon are unavailable.

## Do not

- A missing forecast is not replaced with an invented temperature.
