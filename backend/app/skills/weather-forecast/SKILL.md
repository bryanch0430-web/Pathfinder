---
name: weather-forecast
description: How the weather agent searches. Loaded as a reference in this folder, not as a path the router chooses.
---

# Weather agent

Owns the daily forecast for the trip dates. Tool: `weather.forecast` on Open-Meteo.

Dates past the forecast horizon are unavailable. A missing forecast is not replaced with an invented temperature.
