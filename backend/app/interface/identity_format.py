"""The shape of a guest token, shared by the routes and the caller binding."""

from __future__ import annotations

import re

#: A guest token is 1 to 120 printable ASCII characters without spaces (a UUID is 36). A header carries
#: nothing else and is trimmed, so a session made with any other token could not be opened again.
TOKEN_MAX_LENGTH = 120
TOKEN_FORMAT = re.compile(rf"[!-~]{{1,{TOKEN_MAX_LENGTH}}}")
