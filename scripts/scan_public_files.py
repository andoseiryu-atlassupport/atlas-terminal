"""Bounded heuristic credential scan of public working files and reachable Git blobs.

Never prints matching contents. This is a release hygiene check, not a guarantee
against every credential format or a replacement for an operator's secret scanner.
"""

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = [
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}\b",
    rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b",
    rb"(?i)(?:private[_-]?key|seed)\s*[=:]\s*['\"](?:0x)?[0-9a-f]{64}['\"]",
    rb"(?i)Authorization\s*:\s*Bearer\s+[A-Za-z0-9._-]{32,}",
]


def suspicious(body: bytes) -> bool:
    return any(re.search(pattern, body) for pattern in PATTERNS)


def git(*args: str, input_bytes: bytes | None = None) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT, input=input_bytes)


def main() -> None:
    names = set(git("ls-files", "--cached", "--others", "--exclude-standard", "-z").split(b"\0"))
    findings = []
    checked = 0
    for name in sorted(n for n in names if n):
        path = ROOT / name.decode()
        if path.is_file():
            checked += 1
            if suspicious(path.read_bytes()):
                findings.append("working-file:" + name.decode())
    objects = git("rev-list", "--objects", "--all").splitlines()
    ids = list(dict.fromkeys(line.split(b" ", 1)[0] for line in objects))
    output = git("cat-file", "--batch", input_bytes=b"\n".join(ids) + b"\n")
    offset = 0
    history_blobs = 0
    for _ in ids:
        end = output.index(b"\n", offset)
        oid, kind, raw_size = output[offset:end].split()
        size = int(raw_size)
        body = output[end + 1 : end + 1 + size]
        offset = end + 2 + size
        if kind == b"blob":
            history_blobs += 1
            if suspicious(body):
                findings.append("git-blob:" + oid.decode())
    print(
        json.dumps(
            {
                "status": "FAIL" if findings else "PASS",
                "public_files": checked,
                "history_blobs": history_blobs,
                "suspect_locations": findings,
                "scope": "Heuristic credential patterns; no values printed",
            }
        )
    )
    raise SystemExit(bool(findings))


if __name__ == "__main__":
    main()
