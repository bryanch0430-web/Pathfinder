---
name: web-search
description: Reference for how a web lookup is used on an ask. Not a path the router chooses, and not injected into a writer prompt.
---

# Look up a page

## When to use

An ask cannot be answered from the plan. The lookup returns a page title, url, and summary as a reference. This file records that use. It is not a traveller-facing path.

## Tools

A web search that returns `title`, `url`, and `summary`.

## Steps

1. Use the page only for a question the plan does not contain.
2. State only what the summary says.

## Do not

- A web page is not a stop, a hotel, or a flight.
- The attraction, hotel, and ticket agents are what may put a place on the plan, and only from a row they returned.
