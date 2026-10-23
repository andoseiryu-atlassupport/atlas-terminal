"""Prepare versioned plugin and registry metadata without publishing or reading secrets."""

import argparse
import json
import shutil
from pathlib import Path
from typing import get_args
from zipfile import ZIP_DEFLATED, ZipFile

from atlas_terminal import __version__
from atlas_terminal.documentation import PROJECT_TAGLINE, SITE_ORIGIN
from atlas_terminal.remote_mcp import validated_origin
from atlas_terminal.research_models import Dataset

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/andoseiryu-atlassupport/atlas-terminal"
SERVER_NAME = "io.github.andoseiryu-atlassupport/atlas-terminal"
GIT_PACKAGE = f"git+{REPOSITORY}.git@v{__version__}"


def prepare(directory: Path, remote_url: str | None = None) -> None:
    package = directory / "plugin"
    package.mkdir(parents=True, exist_ok=True)
    skills = package / "skills/atlas-research"
    shutil.copytree(ROOT / "skills/atlas-research", skills, dirs_exist_ok=True)
    shutil.copyfile(ROOT / "LICENSE", package / "LICENSE")
    manifest = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "atlas-terminal",
        "version": __version__,
        "description": PROJECT_TAGLINE,
        "homepage": SITE_ORIGIN + "/",
        "repository": REPOSITORY,
        "license": "Apache-2.0",
        "keywords": ["financial-data", "mcp", "sec", "macro", "foreign-exchange"],
        "extensions": {
            "com.openai": {
                "interface": {
                    "displayName": "Atlas Terminal",
                    "shortDescription": "Financials, US/Japan macro and FX with original sources",
                    "longDescription": (
                        f"Retrieve {len(get_args(Dataset))} free primary-source datasets "
                        "through one MCP connection. "
                        "Use company, US/Japan macro and FX-context presets with explicit "
                        "units, dates, "
                        "missingness and evidence. No real-time prices, PIT history or trading."
                    ),
                    "developerName": "Atlas Terminal contributors",
                    "category": "Productivity",
                    "capabilities": ["Read"],
                    "websiteURL": SITE_ORIGIN + "/",
                    "supportURL": SITE_ORIGIN + "/docs/support",
                    "privacyPolicyURL": SITE_ORIGIN + "/docs/privacy",
                    "termsOfServiceURL": SITE_ORIGIN + "/docs/terms",
                    "defaultPrompt": [
                        "Retrieve quarterly financials and free cash flow with sources.",
                        "Get US inflation, employment and rates with observation dates.",
                        "Retrieve USD/JPY reference FX and US inflation/rate context.",
                    ],
                }
            }
        },
    }
    if remote_url:
        if not remote_url.endswith("/mcp"):
            raise ValueError("Remote URL must end in /mcp.")
        service_origin = validated_origin(remote_url[:-4])
        if not service_origin.startswith("https://"):
            raise ValueError("A public plugin requires an HTTPS endpoint.")
        server = {
            "type": "streamable-http",
            "url": remote_url,
            "extensions": {"com.openai": {"auth": {"type": "none"}}},
        }
        # Skill dependency follows the transport actually packaged for the client.
        config = skills / "agents/openai.yaml"
        text = config.read_text()
        text = text.replace(
            "\npolicy:",
            "      transport: streamable_http\n      url: " + remote_url + "\n\npolicy:",
        )
        config.write_text(text)
    else:
        server = {
            "type": "stdio",
            "command": "uvx",
            "args": [
                "--from",
                GIT_PACKAGE,
                "atlas",
                "mcp",
                "serve",
                "--provider",
                "public",
                "--research-only",
            ],
        }
    (package / "plugin.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (package / "mcp.json").write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
                "mcpServers": {"atlas-terminal": server},
            },
            indent=2,
        )
        + "\n"
    )
    registry = {
        "$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
        "name": SERVER_NAME,
        "title": "Atlas Terminal",
        "version": __version__,
        "description": PROJECT_TAGLINE,
        "repository": {"url": REPOSITORY, "source": "github"},
        "websiteUrl": SITE_ORIGIN + "/",
    }
    if remote_url:
        registry["remotes"] = [{"type": "streamable-http", "url": remote_url}]
    else:
        registry["packages"] = [
            {
                "registryType": "pypi",
                "identifier": "atlas-terminal",
                "version": __version__,
                "runtimeHint": "uvx",
                "transport": {"type": "stdio"},
                "packageArguments": [
                    {"type": "positional", "value": part}
                    for part in (
                        "mcp",
                        "serve",
                        "--provider",
                        "public",
                        "--research-only",
                    )
                ],
                "environmentVariables": [
                    {
                        "name": "SEC_USER_AGENT",
                        "description": "Private SEC contact setting, required only for SEC reads.",
                        "isRequired": False,
                        "isSecret": True,
                    }
                ],
            }
        ]
    (directory / "server.json").write_text(json.dumps(registry, indent=2) + "\n")
    archive = directory / f"atlas-terminal-{__version__}-plugin.zip"
    with ZipFile(archive, "w", ZIP_DEFLATED) as output:
        for path in sorted(package.rglob("*")):
            if path.is_file():
                output.write(path, path.relative_to(package))
    (directory / "README.txt").write_text(
        "Prepared distribution files; not a public listing or verification.\n"
        "The local plugin requires uv and Python 3.12+. SEC needs private operator configuration.\n"
        "PyPI registry metadata requires the matching actual PyPI publication before submission.\n"
        "Remote packages declare anonymous access and require a matching verified live endpoint.\n"
        "Publisher ownership, platform eligibility and review remain external prerequisites.\n"
    )
    print(
        json.dumps(
            {
                "status": "PREPARED",
                "published": False,
                "version": __version__,
                "transport": "remote_anonymous" if remote_url else "stdio",
                "archive": str(archive),
            }
        )
    )


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output", type=Path, default=ROOT / "build/distribution")
    cli.add_argument(
        "--remote-url", help="Actual anonymous HTTPS /mcp endpoint; does not deploy or verify it."
    )
    args = cli.parse_args()
    prepare(args.output, args.remote_url)
