"""A stored LLM credential: the encrypted key and what is known about it."""

from __future__ import annotations

from dataclasses import dataclass

#: The owner value for the operator's own key, saved from the admin Settings card.
SERVER_OWNER = "server"


@dataclass
class StoredCredential:
    id: str
    owner: str
    provider: str
    model: str
    ciphertext: bytes
    key_tail: str
    active: bool
    use_for_search: bool
    last_test_ok: bool | None
    last_tested_at: str | None
    last_error: str
    created_at: str
    updated_at: str
