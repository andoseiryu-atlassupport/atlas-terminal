"""Render seller service files locally; never provision a host, deploy or transfer funds."""

import argparse
import ipaddress
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from atlas_terminal.payments.common import origin

ROOT = Path(__file__).resolve().parents[1]


def prepare(public_origin, output):
    public_origin = origin(public_origin)
    parsed = urlsplit(public_origin)
    host = parsed.hostname or ""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("Use an operator-controlled HTTPS DNS hostname")
    if (
        parsed.scheme != "https"
        or parsed.port is not None
        or "." not in host
        or public_origin != f"https://{host}"
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host)
    ):
        raise ValueError("Use an exact HTTPS DNS origin")
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    for name in ("atlas-seller.service", "Caddyfile.seller", "seller.env.example"):
        body = (
            (ROOT / "deploy" / name)
            .read_text()
            .replace("__ORIGIN__", public_origin)
            .replace("__DOMAIN__", host)
        )
        path = output / name
        path.write_text(body)
        path.chmod(0o600)
    return {
        "status": "PREPARED",
        "deployed": False,
        "real_transfers": 0,
        "origin": public_origin,
        "workers": 1,
        "persistent_state": "/var/lib/atlas-seller",
        "payments_enabled": False,
        "output": str(output),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.origin, args.output)))


if __name__ == "__main__":
    main()
