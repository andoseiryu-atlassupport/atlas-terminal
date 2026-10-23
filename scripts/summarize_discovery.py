"""Prepare and summarize manual search-enabled assistant observations without model requests."""

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "evals/discovery-questions.json"
ATLAS_HOST = "atlas-terminal-docs.citrus-genet-7815.chatgpt.site"
REPOSITORY_PATH = "/andoseiryu-atlassupport/atlas-terminal"


def template():
    questions = json.loads(QUESTIONS.read_text())["questions"]
    return {
        "schema_version": "1.0.0",
        "question_set_sha256": hashlib.sha256(QUESTIONS.read_bytes()).hexdigest(),
        "observations": [
            {
                "question_id": f"q{index:02d}",
                "prompt": prompt,
                "status": "not_run",
                "product": None,
                "model": None,
                "observed_at": None,
                "search_enabled": None,
                "response_sha256": None,
                "atlas_mentioned": None,
                "cited_urls": [],
                "accuracy": "unassessed",
                "task_completed": None,
            }
            for index, prompt in enumerate(questions, 1)
        ],
    }


def atlas_url(value):
    if not isinstance(value, str):
        raise ValueError("Citation URLs must be strings.")
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("Citation URLs must be public HTTPS URLs without credentials.")
    if parts.hostname == ATLAS_HOST:
        return True
    if parts.hostname == "github.com":
        return parts.path == REPOSITORY_PATH or parts.path.startswith(REPOSITORY_PATH + "/")
    return False


def summarize(data):
    expected = template()
    if data.get("schema_version") != "1.0.0":
        raise ValueError("Unsupported observation schema.")
    if data.get("question_set_sha256") != expected["question_set_sha256"]:
        raise ValueError("Question set changed; observations must identify their exact prompts.")
    prompts = {item["question_id"]: item["prompt"] for item in expected["observations"]}
    measured, pending, seen = [], 0, set()
    observations = data.get("observations", [])
    if not observations or len(observations) > 2000:
        raise ValueError("Expected one to 2000 observations.")
    for row in observations:
        if (
            row.get("question_id") not in prompts
            or row.get("prompt") != prompts[row["question_id"]]
        ):
            raise ValueError("Only the exact controlled public prompts are accepted.")
        if row.get("status") == "not_run":
            pending += 1
            continue
        if row.get("status") != "observed":
            raise ValueError("Invalid observation status.")
        if not all(
            isinstance(row.get(key), str) and row[key].strip()
            for key in ("product", "model", "observed_at")
        ):
            raise ValueError("Observed answers require product, model and time.")
        when = datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00"))
        if when.tzinfo is None or row.get("search_enabled") is not True:
            raise ValueError("Use timezone-aware, search-enabled observations.")
        if not re.fullmatch(r"[0-9a-f]{64}", row.get("response_sha256") or ""):
            raise ValueError("Retain an answer hash with the private evidence log.")
        if not isinstance(row.get("atlas_mentioned"), bool):
            raise ValueError("Review whether the answer actually mentions this project.")
        if row.get("accuracy") not in {"correct", "incorrect", "unassessed"}:
            raise ValueError("Invalid accuracy review.")
        if row.get("task_completed") is not None and not isinstance(row["task_completed"], bool):
            raise ValueError("Task completion must be observed or unknown.")
        urls = row.get("cited_urls")
        if not isinstance(urls, list) or len(urls) > 100:
            raise ValueError("Invalid citation list.")
        cited = any([atlas_url(url) for url in urls])
        key = (row["question_id"], row["product"], row["model"], row["observed_at"])
        if key in seen:
            raise ValueError("Duplicate answer observation.")
        seen.add(key)
        measured.append(
            {**row, "atlas_cited": cited, "branded": "atlas terminal" in row["prompt"].lower()}
        )

    def group(rows):
        reviewed = [r for r in rows if r["accuracy"] != "unassessed"]
        tasks = [r for r in rows if r["task_completed"] is not None]
        return {
            "answers_observed": len(rows),
            "mentions": sum(r["atlas_mentioned"] for r in rows),
            "answers_citing_atlas": sum(r["atlas_cited"] for r in rows),
            "citation_rate": sum(r["atlas_cited"] for r in rows) / len(rows) if rows else None,
            "accuracy_reviewed": len(reviewed),
            "accurate": sum(r["accuracy"] == "correct" for r in reviewed),
            "tasks_observed": len(tasks),
            "tasks_completed": sum(r["task_completed"] for r in tasks),
        }

    return {
        "schema_version": "1.0.0",
        "scope": "manual_model_answer_observations",
        "status": "measured" if measured else "not_measured",
        "model_requests_made": False,
        "pending": pending,
        "overall": group(measured),
        "branded": group([r for r in measured if r["branded"]]),
        "unbranded": group([r for r in measured if not r["branded"]]),
        "observations": measured,
        "limitations": [
            "Manual records are operator attestations, not independent verification.",
            "Search results, crawl checks and fixture tests are excluded.",
            "Search settings, product versions and personalization affect results.",
        ],
    }


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--template", action="store_true")
    cli.add_argument("--input", type=Path)
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    if args.template == bool(args.input):
        cli.error("Choose --template or --input.")
    try:
        result = template() if args.template else summarize(json.loads(args.input.read_text()))
    except (ValueError, TypeError, KeyError, OSError):
        cli.error("Invalid controlled observation record; no report written.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": "PREPARED" if args.template else result["status"],
                "output": str(args.output),
                "model_requests_made": False,
            }
        )
    )


if __name__ == "__main__":
    main()
