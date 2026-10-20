import json
from pathlib import Path

import pytest

from atlas_terminal.client import Atlas
from atlas_terminal.providers import FixtureTransport, SECProvider

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def atlas():
    return Atlas("fixture")


@pytest.fixture
def scenario():
    def create(case):
        name = case["base"].split("/")[-1]
        data = json.loads((FIXTURES / case["base"]).read_bytes())
        for patch in case["patches"]:
            parts = patch["path"].strip("/").split("/")
            target = data
            for part in parts[:-1]:
                target = target[int(part)] if isinstance(target, list) else target[part]
            key = parts[-1]
            if key == "-":
                target.append(patch["value"])
            elif isinstance(target, list):
                target[int(key)] = patch["value"]
            else:
                target[key] = patch["value"]
        return Atlas(
            "fixture", source=SECProvider(FixtureTransport({name: json.dumps(data).encode()}))
        )

    return create
