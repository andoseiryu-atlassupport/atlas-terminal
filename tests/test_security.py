import json
import logging
from datetime import timedelta

import httpx
import pytest
from conftest import FIXTURES
from test_payments import ORIGIN, QUERY, make_service, policy

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.hosted import HostedClient
from atlas_terminal.payments.policy import BudgetLedger, SpendingPolicy
from atlas_terminal.providers import FixtureTransport, SECProvider
from atlas_terminal.security import private_library_logs
from atlas_terminal.util import utcnow


def test_redirect_does_not_forward_a_payment_capability(tmp_path):
    destinations = []

    def redirect(request):
        destinations.append(str(request.url))
        return httpx.Response(307, headers={"Location": "https://attacker.invalid/steal"})

    http = httpx.Client(transport=httpx.MockTransport(redirect), follow_redirects=True)
    client = HostedClient(ORIGIN, directory=tmp_path, client=http)
    with pytest.raises(AtlasError) as e:
        client.request(
            "GET",
            "/v1/quotes/synthetic/result",
            headers={"PAYMENT-SIGNATURE": "SIGNATURE_SENTINEL"},
        )
    assert e.value.code == "POLICY_DENIED"
    assert destinations == [ORIGIN + "/v1/quotes/synthetic/result"]


def test_private_library_debug_records_cannot_reach_root(caplog):
    private_library_logs()
    with caplog.at_level(logging.DEBUG):
        for name in ["httpcore.http11", "httpx", "x402.mechanisms.evm.signers"]:
            logging.getLogger(name).debug("Atlas-Recovery-Token: CAPABILITY_SENTINEL")
            logging.getLogger(name).error("PAYMENT-SIGNATURE: SIGNATURE_SENTINEL")
    assert "CAPABILITY_SENTINEL" not in caplog.text
    assert "SIGNATURE_SENTINEL" not in caplog.text


def test_missing_policy_or_lost_budget_is_fail_closed(tmp_path):
    with pytest.raises(AtlasError):
        SpendingPolicy.load(tmp_path / "absent.json")
    ledger = BudgetLedger(tmp_path / "budgets.sqlite3")
    service = make_service(tmp_path)
    quote, _ = service.prepare(QUERY)
    ledger.db.path.unlink()
    with pytest.raises(AtlasError) as e:
        ledger.reserve(quote, policy(), ORIGIN, "fixture:agent", "session")
    assert e.value.code == "LEDGER_UNAVAILABLE"


def test_signing_claim_is_durable_and_single_use(tmp_path):
    quote, _ = make_service(tmp_path).prepare(QUERY)
    ledger = BudgetLedger(tmp_path / "budgets.sqlite3")
    ledger.reserve(quote, policy(), ORIGIN, "fixture:agent", "session")
    ledger.begin_signing(quote, ORIGIN)
    resumed = BudgetLedger(tmp_path / "budgets.sqlite3")
    with pytest.raises(AtlasError) as e:
        resumed.begin_signing(quote, ORIGIN)
    assert e.value.code == "POLICY_DENIED"
    assert resumed.get(quote.quote_id)["state"] == "SIGNING"


def test_confirmation_across_utc_midnight_counts_in_new_day(tmp_path, monkeypatch):
    quote, _ = make_service(tmp_path).prepare(QUERY)
    ledger = BudgetLedger(tmp_path / "budgets.sqlite3")
    p = policy(per_day_atomic="10000")
    ledger.reserve(quote, p, ORIGIN, "fixture:agent", "session")
    future = utcnow() + timedelta(days=1)
    monkeypatch.setattr("atlas_terminal.payments.policy.utcnow", lambda: future)
    ledger.state(quote.quote_id, "SPENT")
    assert ledger.get(quote.quote_id)["day"] == future.date().isoformat()
    another = quote.model_copy(
        update={
            "quote_id": "another-order",
            "expires_at": (future + timedelta(hours=1)).isoformat(),
        }
    )
    with pytest.raises(AtlasError) as e:
        ledger.reserve(another, p, ORIGIN, "fixture:agent", "another-task")
    assert e.value.code == "BUDGET_EXCEEDED"


def test_equal_value_deduplication_and_same_day_unknown_order():
    facts = json.loads((FIXTURES / "sec_like/companyfacts.json").read_bytes())
    rows = facts["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"]
    rows.append(dict(rows[3]))
    app = Atlas(
        "fixture",
        source=SECProvider(FixtureTransport({"companyfacts.json": json.dumps(facts).encode()})),
    )
    assert app.get_financials(QUERY).facts[0].value == "24000000"
    # Two primary filings on the same day, one without a publication time.
    submissions = json.loads((FIXTURES / "sec_like/submissions.json").read_bytes())
    submissions["filings"]["recent"]["filingDate"][0] = "2026-02-15"
    submissions["filings"]["recent"]["acceptanceDateTime"][0] = None
    rows[3]["filed"] = "2026-02-15"
    rows[4]["filed"] = "2026-02-15"
    app = Atlas(
        "fixture",
        source=SECProvider(
            FixtureTransport(
                {
                    "companyfacts.json": json.dumps(facts).encode(),
                    "submissions.json": json.dumps(submissions).encode(),
                }
            )
        ),
    )
    result = app.get_financials(QUERY.model_copy(update={"allow_partial": True}))
    assert result.facts[0].status == "ambiguous" and result.facts[0].value is None


def test_missing_usd_unit_is_not_converted():
    facts = json.loads((FIXTURES / "sec_like/companyfacts.json").read_bytes())
    del facts["facts"]["us-gaap"]["RevenueFromContractWithCustomerExcludingAssessedTax"]["units"][
        "USD"
    ]
    app = Atlas(
        "fixture",
        source=SECProvider(FixtureTransport({"companyfacts.json": json.dumps(facts).encode()})),
    )
    result = app.get_financials(QUERY.model_copy(update={"allow_partial": True}))
    assert all(f.value is None for f in result.facts if f.metric == "revenue")
    assert result.coverage.reported_cells == 3
