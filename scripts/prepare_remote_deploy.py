"""Render single-host MCP service files locally; never install, deploy or create secrets."""

import argparse
import ipaddress
import json
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from atlas_terminal.errors import AtlasError
from atlas_terminal.remote_mcp import validated_origin

ROOT = Path(__file__).resolve().parents[1]


def prepare(origin, output, provider="fixture", anonymous=False):
    origin = validated_origin(origin)
    host = urlsplit(origin).hostname or ""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("Use a DNS hostname with operator-managed HTTPS.")
    if (
        not origin.startswith("https://")
        or urlsplit(origin).port is not None
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host)
        or "." not in host
        or provider not in {"fixture", "public"}
    ):
        raise ValueError("Use an exact HTTPS DNS origin on the default port.")
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    replacements = {
        "__ORIGIN__": origin,
        "__DOMAIN__": host,
        "__PROVIDER__": provider,
        "__AUTH_ARGUMENT__": "--allow-anonymous" if anonymous else "",
    }
    for name in ("atlas-mcp.service", "Caddyfile"):
        text = (ROOT / "deploy" / name).read_text()
        for old, new in replacements.items():
            text = text.replace(old, new)
        (output / name).write_text(text)
    shutil.copyfile(ROOT / "deploy/.env.example", output / ".env.example")
    return {
        "status": "PREPARED",
        "deployed": False,
        "provider": provider,
        "authentication": "anonymous" if anonymous else "bearer_required",
        "origin": origin,
        "workers": 1,
        "persistent_state": "/var/lib/atlas-terminal",
        "output": str(output),
    }


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--origin", required=True)
    cli.add_argument("--provider", choices=["fixture", "public"], default="fixture")
    cli.add_argument("--anonymous", action="store_true")
    cli.add_argument("--output", type=Path, default=ROOT / "build/remote-deploy")
    args = cli.parse_args()
    try:
        result = prepare(args.origin, args.output, args.provider, args.anonymous)
    except (AtlasError, ValueError, FileExistsError):
        cli.error(
            "Use a new output directory and an exact HTTPS DNS origin without a path or port."
        )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
