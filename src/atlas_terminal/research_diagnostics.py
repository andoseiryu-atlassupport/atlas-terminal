"""Private-configuration diagnostics; source requests require an explicit opt-in."""

import os
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator

from atlas_terminal.economic_sources import bea_key
from atlas_terminal.errors import AtlasError
from atlas_terminal.models import Model
from atlas_terminal.providers import validate_sec_user_agent
from atlas_terminal.public_sources import SOURCE_CATALOG
from atlas_terminal.util import stamp

SourceId = Literal[
    "sec",
    "bls",
    "treasury",
    "fiscaldata",
    "federal_reserve",
    "ecb",
    "new_york_fed",
    "bea",
    "boj",
    "estat",
    "imf",
    "oecd",
]
ConfigurationState = Literal["ready", "configuration_required", "synthetic"]
ConnectivityState = Literal["not_checked", "pass", "fail", "not_run"]
DiagnosticStatus = Literal["configured", "action_required", "verified", "degraded", "synthetic"]


class ResearchDiagnosticsQuery(Model):
    sources: list[SourceId] | None = Field(default=None, min_length=1, max_length=12)
    allow_network: bool = Field(default=False, strict=True)

    @field_validator("sources")
    @classmethod
    def unique_sources(cls, values: list[SourceId] | None) -> list[SourceId] | None:
        if values is not None and len(set(values)) != len(values):
            raise ValueError("Choose unique source IDs")
        return values


class SourceDiagnostic(Model):
    source_id: str
    configuration: ConfigurationState
    connectivity: ConnectivityState
    next_action: str
    api_key_required: bool = False
    contact_user_agent_required: bool
    cadence_and_limits: str
    source_terms_url: str
    checks: list[dict[str, Any]] = Field(default_factory=list)


class ResearchDiagnosticsResult(Model):
    schema_version: Literal["0.2.0"] = "0.2.0"
    checked_at: str
    provider: str
    status: DiagnosticStatus
    scope: Literal["configuration_and_optional_fixed_source_probes"] = (
        "configuration_and_optional_fixed_source_probes"
    )
    allow_network: bool
    network_checks_executed: bool
    is_synthetic: bool
    requires_wallet: Literal[False] = False
    real_transfer: Literal[False] = False
    sources: list[SourceDiagnostic]
    limitations: list[str]


def parse_diagnostics(data: ResearchDiagnosticsQuery | dict[str, Any]) -> ResearchDiagnosticsQuery:
    try:
        return (
            data
            if isinstance(data, ResearchDiagnosticsQuery)
            else ResearchDiagnosticsQuery.model_validate(data)
        )
    except (ValidationError, ValueError, TypeError):
        raise AtlasError(
            "INVALID_INPUT",
            "Choose unique catalog source IDs and a boolean network opt-in.",
            fields=["sources", "allow_network"],
        ) from None


def research_diagnostics(
    data: ResearchDiagnosticsQuery | dict[str, Any], *, provider: str, directory: Path
) -> dict[str, Any]:
    query = parse_diagnostics(data)
    if provider not in {"public", "sec", "fixture"}:
        raise AtlasError("CONFIG_REQUIRED", "Research diagnostics use public or fixture providers.")
    selected = (
        [str(name) for name in query.sources] if query.sources is not None else list(SOURCE_CATALOG)
    )
    synthetic = provider == "fixture"
    sec_ready = True
    bea_ready = True
    if "bea" in selected and not synthetic:
        try:
            bea_key()
        except AtlasError:
            bea_ready = False
    if "sec" in selected and not synthetic:
        try:
            validate_sec_user_agent(os.environ.get("SEC_USER_AGENT", ""))
        except AtlasError:
            sec_ready = False
    checks: list[dict[str, Any]] = []
    if query.allow_network and not synthetic:
        from atlas_terminal.source_checks import check_sources

        checks = check_sources(selected, allow_network=True, directory=directory)["checks"]
    sources = []
    configuration: ConfigurationState
    connectivity: ConnectivityState
    for name in selected:
        spec = SOURCE_CATALOG[name]
        ready = (name != "sec" or sec_ready) and (name != "bea" or bea_ready)
        source_checks = [check for check in checks if check["source_id"] == name]
        if synthetic:
            configuration, connectivity = "synthetic", "not_run"
            action = (
                "Use the public provider and explicitly opt in to check real source connectivity."
            )
        elif not ready:
            configuration, connectivity = "configuration_required", "not_run"
            action = (
                "Set SEC_USER_AGENT privately with your application name and actual contact email."
                if name == "sec"
                else (
                    "Set BEA_API_KEY privately after free registration at "
                    "https://apps.bea.gov/api/signup/."
                )
            )
        elif source_checks:
            configuration = "ready"
            states = [check["status"] for check in source_checks]
            connectivity = (
                "pass"
                if all(state == "PASS" for state in states)
                else "fail"
                if "FAIL" in states
                else "not_run"
            )
            action = (
                "No configuration action; PASS covers only these fixed source probes."
                if connectivity == "pass"
                else "Review each probe error and its next_action before retrying a source read."
            )
        else:
            configuration, connectivity = "ready", "not_checked"
            action = (
                "Set allow_network=true to check connectivity; configuration is not a live check."
            )
        sources.append(
            SourceDiagnostic.model_validate(
                {
                    "source_id": name,
                    "configuration": configuration,
                    "connectivity": connectivity,
                    "next_action": action,
                    "contact_user_agent_required": spec["contact_user_agent_required"],
                    "api_key_required": spec["api_key_required"],
                    "cadence_and_limits": spec["cadence"],
                    "source_terms_url": spec["terms"],
                    "checks": source_checks,
                }
            )
        )
    status: DiagnosticStatus
    if synthetic:
        status = "synthetic"
    elif any(row.configuration == "configuration_required" for row in sources):
        status = "action_required"
    elif any(row.connectivity in {"fail", "not_run"} for row in sources):
        status = "degraded"
    elif all(row.connectivity == "pass" for row in sources):
        status = "verified"
    else:
        status = "configured"
    return ResearchDiagnosticsResult(
        checked_at=stamp(),
        provider=provider,
        status=status,
        allow_network=query.allow_network,
        network_checks_executed=any(check["status"] in {"PASS", "FAIL"} for check in checks),
        is_synthetic=synthetic,
        sources=sources,
        limitations=[
            "Configuration does not establish connectivity, source availability or data freshness.",
            "Opt-in probes share caches/quotas and save private baselines; no payments or signing.",
            "Probe PASS does not cover every dataset, source terms, remaining quota or an SLA.",
            "Fixtures never run network checks or establish live connectivity.",
        ],
    ).model_dump(mode="json")
