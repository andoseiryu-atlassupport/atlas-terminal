"""Record executed fictional walkthroughs, with optional 60-second video replays."""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from tempfile import TemporaryDirectory

from atlas_terminal.showcase import transcript


def replay(report, output):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError("Video rendering requires ffmpeg with drawtext and libx264.")
    scenario = report["scenario"]
    lines = transcript(report).splitlines()
    command = "atlas showcase " + scenario
    scenes = [
        [
            command,
            "",
            *lines[:3],
            "",
            "Recorded JSON rendered as a paced replay.",
            "Playback duration is not execution time.",
        ],
        [command, "", *lines[:6]],
        [command, "", *lines[4:10]],
        [command, "", *lines[7:13]],
        [command, "", *lines[max(4, len(lines) - 7) :]],
        [
            command + " --json",
            "",
            "Complete queries, results and evidence are in the JSON report.",
            "",
            "Research: add --provider public --allow-network for actual source reads.",
            "Japan risk: supply your own licensed histories and a broad estimation universe.",
            "",
            "FICTIONAL DATA | Experimental risk methods | No market accuracy claim",
        ],
    ]
    filters = []
    captions = ["WEBVTT", ""]
    with TemporaryDirectory(prefix="atlas-replay-") as temporary:
        for i, scene in enumerate(scenes):
            body = "\n".join(
                wrapped
                for line in scene
                for wrapped in (textwrap.wrap(line, width=82) if line else [""])
            ).rstrip("\n")
            path = Path(temporary) / f"scene-{i}.txt"
            path.write_text(body + "\n")
            filters.append(
                f"drawtext=font=monospace:textfile={path}:expansion=none:"
                f"fontcolor=0xe6edf3:fontsize=23:x=45:y=120:"
                f"enable='gte(t,{10 * i})*lt(t,{10 * (i + 1)})'"
            )
            start = f"00:00:{10 * i:02d}.000"
            end = f"00:00:{10 * (i + 1):02d}.000" if i < 5 else "00:01:00.000"
            captions += [f"{start} --> {end}", body, ""]
        badge = Path(temporary) / "badge.txt"
        badge.write_text(f"ATLAS TERMINAL {report['atlas_version']} | {scenario} | FICTIONAL DATA")
        filters.append(
            f"drawtext=font=monospace:textfile={badge}:expansion=none:"
            "fontcolor=0x79c0ff:fontsize=24:x=45:y=40"
        )
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=0x0d1117:s=1280x720:r=10:d=60",
                "-vf",
                ",".join(filters),
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "28",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(output / f"{scenario}.mp4"),
            ],
            check=True,
            timeout=120,
        )
    (output / f"{scenario}.vtt").write_text("\n".join(captions))


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output", type=Path, default=Path("build/showcases"))
    cli.add_argument("--video", action="store_true", help="Also render MP4/VTT; requires ffmpeg")
    args = cli.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    recordings = []
    for scenario in ("research", "japan-risk"):
        command = [sys.executable, "-m", "atlas_terminal", "showcase", scenario, "--json"]
        completed = subprocess.run(command, capture_output=True, text=True, check=True, timeout=120)
        report = json.loads(completed.stdout)
        if report["status"] != "PASS" or report["is_synthetic"] is not True:
            raise ValueError("Only executed fictional walkthroughs belong in this replay bundle.")
        (args.output / f"{scenario}.json").write_text(json.dumps(report, indent=2) + "\n")
        (args.output / f"{scenario}.txt").write_text(transcript(report) + "\n")
        if args.video:
            replay(report, args.output)
        files = [path for path in args.output.glob(f"{scenario}.*") if path.is_file()]
        recordings.append(
            {
                "scenario": scenario,
                "atlas_version": report["atlas_version"],
                "command": f"atlas showcase {scenario} --json",
                "is_synthetic": True,
                "recorded_at": report["recorded_at"],
                "mcp_sessions": report["mcp_sessions"],
                "video_replay_seconds": 60 if args.video else None,
                "runtime_benchmark": False,
                "sha256": {
                    p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)
                },
            }
        )
    (args.output / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "is_synthetic": True,
                "recordings": recordings,
            },
            indent=2,
        )
        + "\n"
    )
    print(json.dumps({"status": "PASS", "recordings": len(recordings), "output": str(args.output)}))


if __name__ == "__main__":
    main()
