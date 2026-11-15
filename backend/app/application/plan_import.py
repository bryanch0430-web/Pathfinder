"""What a plan file from another computer may not bring in with it.

A saved plan is data the traveller (or anyone who sent them the file) wrote. Opening it as a trip here clears what only
this computer's traveller may set (locks, bookings and confirmations), keeps only web addresses, and writes the derived data
(links, ranks) again from the fields; the caller works the airport transfers out again.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.application.links import _http as is_web_url
from app.domain.models import TripPlan

#: Fields only the traveller sets by pressing a button: an imported plan starts with none of them.
_FLAGS = ("locked", "booked", "confirmed")
#: Fields that hold an address the page may turn into a link or an image.
_ADDRESSES = ("url", "booking_url", "link", "website", "thumbnail_url")


def schema_version() -> str:
    """The version of the plan file this app writes and reads."""
    return str(TripPlan.model_fields["schema_version"].default)


def clean_imported_plan(plan: TripPlan) -> TripPlan:
    """Clear every locked, booked and confirmed flag, keep only http and https addresses, and write the links and the rank labels
    again. Works in place and returns the plan."""
    _clean(plan)
    plan.refresh_derived()
    return plan


def _clean(node: object) -> None:
    if isinstance(node, BaseModel):
        for name in type(node).model_fields:
            value = getattr(node, name)
            if name in _FLAGS and value is True:
                setattr(node, name, False)
            elif name in _ADDRESSES and isinstance(value, str):
                if value and not is_web_url(value):
                    setattr(node, name, "")
            else:
                _clean(value)
    elif isinstance(node, list):
        for value in node:
            _clean(value)
    elif isinstance(node, dict):
        for value in node.values():
            _clean(value)
