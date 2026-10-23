"""Check recorded walkthrough identities, versions and exact report/media bytes."""

import hashlib
import json
from pathlib import Path

from atlas_terminal import __version__

ROOT = Path(__file__).resolve().parents[1]


def verify(directory: Path) -> None:
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("is_synthetic") is not True or manifest.get("schema_version") != "1.0.0":
        raise ValueError("A fictional recording manifest is required.")
    recordings = manifest["recordings"]
    if {row["scenario"] for row in recordings} != {"research", "japan-risk"} or len(
        recordings
    ) != 2:
        raise ValueError("Expected both recorded walkthroughs exactly once.")
    for row in recordings:
        name = row["scenario"]
        if row["atlas_version"] != __version__ or row["is_synthetic"] is not True:
            raise ValueError("Recording version or data mode does not match the package.")
        required = {f"{name}.{suffix}" for suffix in ("json", "txt", "mp4", "vtt")}
        if not required <= set(row["sha256"]):
            raise ValueError("Missing report, transcript, replay or captions.")
        for filename, expected in row["sha256"].items():
            if Path(filename).name != filename or not filename.startswith(name + "."):
                raise ValueError("Invalid recording filename.")
            if hashlib.sha256((directory / filename).read_bytes()).hexdigest() != expected:
                raise ValueError("Recording checksum mismatch.")
        report = json.loads((directory / f"{name}.json").read_text())
        if (
            report["scenario"] != name
            or report["status"] != "PASS"
            or report["atlas_version"] != __version__
            or report["is_synthetic"] is not True
            or report["mcp_sessions"] != 1
            or report["transport"] != "stdio"
            or report["llm_used"] is not False
            or report["transfers"] is not False
        ):
            raise ValueError("Invalid executed walkthrough metadata.")


if __name__ == "__main__":
    verify(ROOT / "docs/assets/showcase")
    print(json.dumps({"status": "PASS", "recordings": 2, "checksums": "verified"}))
