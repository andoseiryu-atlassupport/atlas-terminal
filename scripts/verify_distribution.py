"""Check built archive contents and metadata before publishing; no network access."""

import json
import tarfile
import tomllib
from email import message_from_bytes
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {
    "node_modules",
    ".venv",
    ".git",
    "build",
    "dist",
    ".atlas",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "private",
    "internal",
    "handoff",
}


def safe_name(name):
    parts = PurePosixPath(name).parts
    assert not name.startswith("/") and ".." not in parts, "Unsafe archive path."
    assert not (set(parts) & FORBIDDEN), "Generated, dependency or private path in archive."
    assert not any(part.startswith(".env") and part != ".env.example" for part in parts)
    assert not name.endswith((".sqlite3", ".db", ".pem", ".key", ".pyc"))


def main():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    prefix = f"atlas_terminal-{version}"
    with tarfile.open(ROOT / "dist" / f"{prefix}.tar.gz") as archive:
        names = {member.name for member in archive.getmembers()}
        for member in archive.getmembers():
            safe_name(member.name)
            assert member.isfile() or member.isdir(), "Archive links/devices are unsupported."
        for required in (
            "README.md",
            "LICENSE",
            "pyproject.toml",
            "uv.lock",
            "src/atlas_terminal/remote_mcp.py",
            "src/atlas_terminal/resources/research/fx.xml",
            "src/atlas_terminal/resources/research/sdmx_imf_data.xml",
            "src/atlas_terminal/resources/research/sdmx_oecd_structure.xml",
            "src/atlas_terminal/research_panel.py",
            "src/atlas_terminal/risk_service.py",
            "src/atlas_terminal/risk_engine.py",
            "src/atlas_terminal/risk_math.py",
            "src/atlas_terminal/risk_models.py",
            "src/atlas_terminal/risk_demo.py",
            "src/atlas_terminal/risk_connections.py",
            "src/atlas_terminal/risk_time_series.py",
            "src/atlas_terminal/risk_markets.py",
            "src/atlas_terminal/showcase.py",
            "docs/agent-research-walkthrough.md",
            "docs/japan-risk-walkthrough.md",
            "docs/assets/showcase/manifest.json",
            "docs/assets/showcase/research.mp4",
            "docs/assets/showcase/japan-risk.mp4",
            "scripts/record_showcases.py",
            "scripts/verify_showcases.py",
            "docs/risk-model.md",
            "docs/data-connections.md",
            "docs/equity-markets.md",
            "examples/risk/panel-template.json",
            "examples/risk/connections.template.json",
            "examples/risk/run-cross-sectional.json",
            "examples/risk/run-time-series.json",
            "examples/risk/run-japan.json",
            "examples/risk/run-korea.json",
            "examples/risk/run-britain.json",
            "examples/mcp.risk.json",
            "docs/international-macro.md",
            "examples/research_panels/us_japan.json",
            "docs/codex.md",
            "skills/atlas-research/SKILL.md",
            "tests/test_remote_mcp.py",
            "deploy/atlas-mcp.service",
            "deploy/.env.example",
            "evals/research-workflows.json",
            "scripts/run_research_workflow.py",
            "examples/company_macro_fx.json",
        ):
            assert f"{prefix}/{required}" in names, "Required project file missing."
        source_count = len(names)
    with ZipFile(ROOT / "dist" / f"{prefix}-py3-none-any.whl") as archive:
        names = set(archive.namelist())
        for name in names:
            safe_name(name)
        metadata = message_from_bytes(archive.read(f"{prefix}.dist-info/METADATA"))
        assert metadata["Version"] == version and metadata["Name"] == "atlas-terminal"
        marker = "mcp-name: io.github.andoseiryu-atlassupport/atlas-terminal "
        assert marker in metadata.get_payload()
        assert f"{prefix}.dist-info/licenses/LICENSE" in names
        assert "atlas_terminal/remote_mcp.py" in names
    print(
        json.dumps(
            {
                "status": "PASS",
                "version": version,
                "sdist_files": source_count,
                "wheel_files": len(names),
                "forbidden_paths": False,
            }
        )
    )


if __name__ == "__main__":
    main()
