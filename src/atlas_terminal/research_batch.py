"""Bounded, ordered batches of independent free research reads."""

from collections.abc import Callable
from typing import Annotated, Any, Literal

from pydantic import Field, ValidationError, model_validator

from atlas_terminal.errors import AtlasError, ErrorPayload
from atlas_terminal.models import Model
from atlas_terminal.research_views import ResearchResult
from atlas_terminal.util import canonical

MAX_BATCH_ITEMS = 10
MAX_BATCH_REQUEST_BYTES = 16384
MAX_BATCH_RESPONSE_BYTES = 4 * 1024 * 1024


class ResearchBatchItem(Model):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    query: dict[str, Any] = Field(
        description=(
            "One dataset-specific ResearchQuery from atlas_research_catalog. "
            "Queries are validated independently; invalid queries produce per-item errors."
        )
    )


class ResearchBatchQuery(Model):
    items: list[ResearchBatchItem] = Field(min_length=1, max_length=MAX_BATCH_ITEMS)

    @model_validator(mode="after")
    def unique_ids(self) -> "ResearchBatchQuery":
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("Batch item IDs must be unique")
        return self


class ResearchBatchSuccess(Model):
    id: str
    status: Literal["ok"] = "ok"
    result: ResearchResult


class ResearchBatchFailure(Model):
    id: str
    status: Literal["error"] = "error"
    error: ErrorPayload


class ResearchBatchSummary(Model):
    requested: int
    successful: int
    failed: int
    retryable_read_ids: list[str]


class ResearchBatchResult(Model):
    schema_version: Literal["0.2.0"] = "0.2.0"
    status: Literal["ok", "partial", "error"]
    execution: Literal["sequential_independent_reads"] = "sequential_independent_reads"
    upstream_data_cost: Literal["0"] = "0"
    requires_wallet: Literal[False] = False
    items: list[
        Annotated[ResearchBatchSuccess | ResearchBatchFailure, Field(discriminator="status")]
    ]
    summary: ResearchBatchSummary


def parse_batch(data: ResearchBatchQuery | dict[str, Any]) -> ResearchBatchQuery:
    try:
        query = (
            data
            if isinstance(data, ResearchBatchQuery)
            else ResearchBatchQuery.model_validate(data)
        )
        if len(canonical(query)) > MAX_BATCH_REQUEST_BYTES:
            raise ValueError("Batch request exceeds bounds")
        return query
    except (ValidationError, ValueError, TypeError):
        raise AtlasError(
            "INVALID_INPUT",
            "Use 1–10 uniquely identified research items within the 16 KiB request limit.",
            fields=["items"],
        ) from None


def research_batch(
    data: ResearchBatchQuery | dict[str, Any],
    get_research: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    query = parse_batch(data)
    items: list[dict[str, Any]] = []
    size = 0
    for item in query.items:
        try:
            result = get_research(item.query)
            ResearchResult.model_validate(result)
            entry = {"id": item.id, "status": "ok", "result": result}
        except AtlasError as exc:
            entry = {"id": item.id, "status": "error", "error": exc.envelope()["error"]}
        except Exception:
            failure = AtlasError(
                "INTERNAL_ERROR", "Research item stopped due to an internal error."
            )
            entry = {"id": item.id, "status": "error", "error": failure.envelope()["error"]}
        if size + len(canonical(entry)) > MAX_BATCH_RESPONSE_BYTES - MAX_BATCH_REQUEST_BYTES:
            failure = AtlasError(
                "RESPONSE_TOO_LARGE",
                "Item exceeds the remaining batch response budget; reduce its result size.",
                fields=["response", "limit", "count"],
            )
            entry = {"id": item.id, "status": "error", "error": failure.envelope()["error"]}
        size += len(canonical(entry))
        items.append(entry)
    successful = sum(item["status"] == "ok" for item in items)
    result = {
        "schema_version": "0.2.0",
        "status": "ok" if successful == len(items) else "partial" if successful else "error",
        "execution": "sequential_independent_reads",
        "upstream_data_cost": "0",
        "requires_wallet": False,
        "items": items,
        "summary": {
            "requested": len(items),
            "successful": successful,
            "failed": len(items) - successful,
            "retryable_read_ids": [
                item["id"]
                for item in items
                if item["status"] == "error"
                and item["error"]["retryable"]
                and item["error"]["next_action"]["action"] in {"wait", "retry_read"}
            ],
        },
    }
    ResearchBatchResult.model_validate(result)
    return result
