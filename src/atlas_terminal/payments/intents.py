"""Private quote-replay capabilities, separate from model-visible task identifiers."""

import hmac
import re

from atlas_terminal.errors import AtlasError
from atlas_terminal.models import FinancialQuery
from atlas_terminal.util import canonical, digest

INTENT_HEADER = "Atlas-Intent-Key"
INTENT_ACK_HEADER = "Atlas-Intent-Accepted"


def intent_identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}", value):
        raise AtlasError("INVALID_INPUT", "Intent ID must be 1-128 ASCII identifier characters.")
    return value


def intent_binding(key: str, query: FinancialQuery, origin: str) -> tuple[str, str, str]:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{32,128}", key):
        raise AtlasError("INVALID_INPUT", "Invalid private intent capability.")
    # Domain separation keeps the public lookup hash distinct from recovery authority.
    token = hmac.digest(key.encode(), b"atlas-intent-recovery-v1", "sha256").hex()
    query_hash = digest(canonical({"origin": origin, "query": query.model_dump(mode="json")}))
    return digest(key.encode()), query_hash, token
