"""Read-only, finalized Base Sepolia USDC evidence; never signs or broadcasts."""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

import httpx
from eth_utils import keccak
from x402.http.utils import encode_payment_response_header
from x402.schemas import SettleResponse

from atlas_terminal.errors import AtlasError
from atlas_terminal.models import Quote
from atlas_terminal.payments.common import ASSET, NETWORK, origin, validate_testnet
from atlas_terminal.security import private_library_logs

TRANSFER = "0x" + keccak(text="Transfer(address,address,uint256)").hex()
AUTHORIZATION_USED = "0x" + keccak(text="AuthorizationUsed(address,bytes32)").hex()
AUTHORIZATION_STATE = keccak(text="authorizationState(address,bytes32)")[:4].hex()
HASH = re.compile(r"^0x[0-9a-fA-F]{64}$")
ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")


@dataclass(frozen=True)
class SettlementEvidence:
    state: Literal["SETTLED", "FAILED", "SETTLEMENT_UNKNOWN"]
    reason: str
    transaction: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


class SettlementReconciler(Protocol):
    def check(
        self, quote: Quote, attempt: dict[str, Any], transaction: str | None = None
    ) -> SettlementEvidence: ...

    def close(self) -> None: ...


def settlement_header(quote: Quote, payer: str, transaction: str) -> str:
    return encode_payment_response_header(
        SettleResponse(
            success=True,
            network=NETWORK,
            payer=payer,
            transaction=transaction,
            amount=quote.pricing.amount_atomic,
        )
    )


def quantity(value: Any) -> int:
    if not isinstance(value, str) or not re.fullmatch(
        r"0x(?:0|[1-9a-fA-F][0-9a-fA-F]{0,63})", value
    ):
        raise ValueError("Invalid RPC quantity")
    return int(value, 16)


def block_fields(value: Any) -> tuple[int, str, int]:
    if not isinstance(value, dict) or not HASH.fullmatch(str(value.get("hash", ""))):
        raise ValueError("Missing canonical block")
    return quantity(value.get("number")), value["hash"].lower(), quantity(value.get("timestamp"))


def address_topic(address: str) -> str:
    if not ADDRESS.fullmatch(address):
        raise ValueError("Invalid payer")
    return "0x" + "0" * 24 + address[2:].lower()


class BaseSepoliaReconciler:
    """Use one trusted RPC and a bounded nonce-log scan, with optional transaction hint."""

    def __init__(
        self, rpc_url: str, *, client: httpx.Client | None = None, scan_blocks: int = 10000
    ) -> None:
        private_library_logs()
        parsed = urlsplit(rpc_url)
        # Paths may contain a private provider key. Never return or log this URL.
        origin(f"{parsed.scheme}://{parsed.netloc}")
        if parsed.query or parsed.fragment or not 1 <= scan_blocks <= 10000:
            raise AtlasError("CONFIG_REQUIRED", "Invalid configured reconciliation RPC.")
        self.url = rpc_url
        self.client = client or httpx.Client(timeout=10, follow_redirects=False)
        self.scan_blocks = scan_blocks
        self._request_id = 0

    def rpc(self, method: str, params: list[Any]) -> Any:
        self._request_id += 1
        request_id = self._request_id
        with self.client.stream(
            "POST",
            self.url,
            follow_redirects=False,
            json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        ) as response:
            if response.status_code != 200:
                raise ValueError("RPC unavailable")
            body = bytearray()
            for part in response.iter_bytes():
                body.extend(part)
                if len(body) > 2 * 1024 * 1024:
                    raise ValueError("RPC response exceeds limit")
        value = json.loads(body)
        if (
            not isinstance(value, dict)
            or value.get("jsonrpc") != "2.0"
            or type(value.get("id")) is not int
            or value["id"] != request_id
            or "error" in value
            or "result" not in value
        ):
            raise ValueError("Invalid RPC envelope")
        return value["result"]

    def check(
        self, quote: Quote, attempt: dict[str, Any], transaction: str | None = None
    ) -> SettlementEvidence:
        validate_testnet(quote.pricing.network, quote.pricing.asset, quote.pricing.pay_to)
        if quote.pricing.mode != "testnet" or quote.pricing.scheme != "exact":
            raise AtlasError("CONFIG_REQUIRED", "Reconciliation requires exact Base Sepolia USDC.")
        if transaction is not None and not HASH.fullmatch(transaction):
            raise AtlasError("INVALID_INPUT", "Transaction must be an EVM transaction hash.")
        try:
            return self._check(quote, attempt, transaction)
        except (
            httpx.HTTPError,
            ValueError,
            KeyError,
            TypeError,
            OverflowError,
            AttributeError,
            OSError,
        ):
            # An unavailable, malformed or inconsistent source never releases a reservation.
            return SettlementEvidence(
                "SETTLEMENT_UNKNOWN", "Finalized chain evidence is unavailable or inconsistent."
            )

    def _check(
        self, quote: Quote, attempt: dict[str, Any], transaction: str | None
    ) -> SettlementEvidence:
        if quantity(self.rpc("eth_chainId", [])) != 84532:
            return SettlementEvidence("SETTLEMENT_UNKNOWN", "RPC did not confirm Base Sepolia.")
        number, block_hash, timestamp = block_fields(
            self.rpc("eth_getBlockByNumber", ["finalized", False])
        )
        payer_topic = address_topic(attempt["payer"])
        nonce = attempt["nonce"]
        if not HASH.fullmatch(nonce):
            raise ValueError("Invalid authorization nonce")
        calldata = "0x" + AUTHORIZATION_STATE + payer_topic[2:] + nonce[2:]
        # EIP-1898 pins state to this exact canonical block, not a moving 'latest'.
        used = self.rpc(
            "eth_call",
            [
                {"to": ASSET, "data": calldata},
                {"blockHash": block_hash, "requireCanonical": True},
            ],
        )
        if used not in {"0x" + "0" * 64, "0x" + "0" * 63 + "1"}:
            raise ValueError("Invalid authorizationState result")
        used = used.endswith("1")
        details = {
            "network": NETWORK,
            "finalized_block": number,
            "finalized_block_hash": block_hash,
            "finalized_timestamp": timestamp,
            "authorization_used": used,
        }
        candidate = transaction or attempt.get("transaction_id")
        if candidate is None and used:
            logs = self.rpc(
                "eth_getLogs",
                [
                    {
                        "address": ASSET,
                        "fromBlock": hex(max(0, number - self.scan_blocks + 1)),
                        "toBlock": hex(number),
                        "topics": [AUTHORIZATION_USED, payer_topic, nonce],
                    }
                ],
            )
            if not isinstance(logs, list) or len(logs) > 1:
                raise ValueError("Nonunique authorization evidence")
            if logs:
                log = logs[0]
                if (
                    log.get("address", "").lower() != ASSET.lower()
                    or [v.lower() for v in log.get("topics", [])]
                    != [AUTHORIZATION_USED, payer_topic, nonce.lower()]
                    or log.get("removed") is not False
                    or not HASH.fullmatch(str(log.get("transactionHash", "")))
                ):
                    raise ValueError("Invalid nonce discovery log")
                candidate = log["transactionHash"]
        if candidate:
            if not HASH.fullmatch(candidate):
                raise ValueError("Invalid stored transaction")
            receipt = self.rpc("eth_getTransactionReceipt", [candidate])
            if receipt is not None and self.paid_receipt(
                quote, attempt, receipt, candidate, number
            ):
                if not used:
                    raise ValueError("Receipt conflicts with authorization state")
                # Re-read canonicality of the pinned finalized block after all observations.
                self.confirm_block(number, block_hash)
                return SettlementEvidence(
                    "SETTLED",
                    "Finalized USDC transfer and authorization nonce match the order.",
                    candidate.lower(),
                    details,
                )
        deadline = attempt.get("valid_before")
        if deadline is not None and (
            isinstance(deadline, bool)
            or not re.fullmatch(r"[0-9]{1,78}", str(deadline))
            or not 0 < int(deadline) < 2**256
        ):
            raise ValueError("Invalid recorded authorization expiry")
        if not used and deadline is not None and timestamp >= int(deadline):
            self.confirm_block(number, block_hash)
            return SettlementEvidence(
                "FAILED", "Authorization expired unused in finalized chain state.", details=details
            )
        return SettlementEvidence(
            "SETTLEMENT_UNKNOWN",
            "No matching finalized transfer; the reservation remains held.",
            details=details,
        )

    def confirm_block(self, number: int, block_hash: str) -> None:
        actual_number, actual_hash, _ = block_fields(
            self.rpc("eth_getBlockByNumber", [hex(number), False])
        )
        if actual_number != number or actual_hash != block_hash:
            raise ValueError("Canonical block changed")

    def paid_receipt(
        self,
        quote: Quote,
        attempt: dict[str, Any],
        receipt: Any,
        transaction: str,
        finalized_number: int,
    ) -> bool:
        if (
            not isinstance(receipt, dict)
            or receipt.get("transactionHash", "").lower() != transaction.lower()
            or quantity(receipt.get("status")) != 1
        ):
            return False
        number = quantity(receipt.get("blockNumber"))
        if number > finalized_number:
            return False
        block_hash = receipt.get("blockHash", "").lower()
        self.confirm_block(number, block_hash)
        logs = receipt.get("logs")
        if not isinstance(logs, list) or len(logs) > 1000:
            raise ValueError("Invalid receipt logs")
        authorizations: list[dict[str, Any]] = []
        transfers: list[dict[str, Any]] = []
        indexes: set[int] = set()
        payer = address_topic(attempt["payer"])
        for log in logs:
            if not isinstance(log, dict):
                raise ValueError("Invalid receipt log")
            if log.get("address", "").lower() != ASSET.lower():
                continue
            topics = [v.lower() for v in log.get("topics", [])]
            if (
                len(topics) != 3
                or topics[1] != payer
                or topics[0] not in {TRANSFER, AUTHORIZATION_USED}
            ):
                continue
            index = quantity(log.get("logIndex"))
            if (
                index in indexes
                or log.get("removed") is not False
                or log.get("transactionHash", "").lower() != transaction.lower()
                or log.get("blockHash", "").lower() != block_hash
                or quantity(log.get("blockNumber")) != number
            ):
                raise ValueError("Inconsistent receipt log")
            indexes.add(index)
            (authorizations if topics[0] == AUTHORIZATION_USED else transfers).append(log)
        # Be conservative for batched transactions: do not attribute a different
        # transfer to this nonce when the payer has multiple authorizations/transfers.
        return (
            len(authorizations) == len(transfers) == 1
            and authorizations[0]["topics"][2].lower() == attempt["nonce"].lower()
            and authorizations[0].get("data") == "0x"
            and transfers[0]["topics"][2].lower() == address_topic(quote.pricing.pay_to)
            and HASH.fullmatch(str(transfers[0].get("data", ""))) is not None
            and int(transfers[0]["data"], 16) == int(quote.pricing.amount_atomic)
        )

    def close(self) -> None:
        self.client.close()
