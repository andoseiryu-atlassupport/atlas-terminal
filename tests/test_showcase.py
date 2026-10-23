import json
from decimal import Decimal

import pytest
from typer.testing import CliRunner

from atlas_terminal.cli import app
from atlas_terminal.errors import AtlasError
from atlas_terminal.showcase import evidence_ids, run_showcase, transcript


@pytest.mark.parametrize("scenario", ["research", "japan-risk"])
def test_installed_style_walkthrough_uses_actual_mcp_and_preserves_interpretation(scenario):
    report = run_showcase(scenario)
    assert report["status"] == "PASS" and report["is_synthetic"] is True
    assert report["transport"] == "stdio" and report["mcp_sessions"] == 1
    assert report["llm_used"] is report["transfers"] is False
    encoded = json.dumps(report)
    assert "atlas-showcase-" not in encoded and "connections.json" not in encoded
    assert "FICTIONAL DATA" in transcript(report)
    if scenario == "research":
        payload = report["result"]
        assert evidence_ids(payload["preset"]["batch"]) == set(payload["evidence"])
        for identifier, resolved in payload["evidence"].items():
            assert resolved["evidence_id"] == identifier
            assert resolved["evidence"]["raw_source_url"].split("/")[2].endswith(".invalid")
        rows = {
            item["id"]: item["result"]["records"][0] for item in payload["preset"]["batch"]["items"]
        }
        fx = rows["fx"]
        quote, base = {row["role"]: row for row in fx["inputs"]}.values()
        by_role = {row["role"]: row for row in fx["inputs"]}
        assert Decimal(fx["value"]) == (
            Decimal(by_role["quote_per_EUR"]["value"]) / Decimal(by_role["base_per_EUR"]["value"])
        )
        assert fx["date"] == quote["date"] == base["date"]
        assert fx["unit"] == "JPY_per_USD" and rows["us_sofr"]["unit"] == "percent_per_annum"
    else:
        model = report["result"]["run"]["data"]["model"]
        portfolio = report["result"]["run"]["data"]["analysis"]["portfolio"]
        holdout = report["result"]["holdout"]
        assert model["country"] == "JP" and model["industry_classification"] == "jpx33"
        assert model["assets"] == 60 and model["currency"] == "JPY"
        assert len(portfolio["asset_contributions"]) == 20
        assert holdout["is_synthetic"] is True and holdout["action"] == "validate"
        assert Decimal(portfolio["daily_variance"]) > 0
        assert abs(
            Decimal(portfolio["daily_variance"])
            - sum(Decimal(portfolio[key]) for key in ["common_variance", "specific_variance"])
        ) < Decimal("1e-27")


@pytest.mark.parametrize(
    "scenario,opt_in", [("research", False), ("japan-risk", False), ("japan-risk", True)]
)
def test_public_source_opt_in_is_checked_before_starting_a_process(monkeypatch, scenario, opt_in):
    def forbidden(*args, **kwargs):
        pytest.fail("MCP must not be started before checking source-request opt-in")

    monkeypatch.setattr("atlas_terminal.showcase.execute", forbidden)
    with pytest.raises(AtlasError, match="Only research supports public mode"):
        run_showcase(scenario, "public", allow_network=opt_in)


def test_showcase_does_not_echo_transport_credentials(monkeypatch):
    async def stopped(*args, **kwargs):
        raise RuntimeError("PRIVATE_TRANSPORT_SENTINEL")

    monkeypatch.setattr("atlas_terminal.showcase.execute", stopped)
    result = CliRunner().invoke(app, ["showcase", "research", "--json"])
    assert result.exit_code != 0
    assert "PRIVATE_TRANSPORT_SENTINEL" not in result.output


def test_cli_can_save_a_complete_research_report():
    result = CliRunner().invoke(app, ["showcase", "research", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["result"]["evidence"]
