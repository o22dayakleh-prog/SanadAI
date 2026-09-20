import os
import json
import urllib.request
import urllib.error
from decimal import Decimal, InvalidOperation


# ============================================================
# SanadAI - TRON USDT TRC20 Payment Verification
# ============================================================

TRONGRID_API_KEY = os.getenv("TRONGRID_API_KEY")
PAYMENT_WALLET = os.getenv("PAYMENT_WALLET", "").strip()
SUBSCRIPTION_PRICE_USDT = os.getenv("SUBSCRIPTION_PRICE_USDT", "3")

# Official USDT TRC20 contract on TRON Mainnet.
USDT_TRON_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

TRONGRID_BASE_URL = "https://api.trongrid.io"

USDT_DECIMALS = 6


class PaymentVerificationError(Exception):
    """Raised when a payment cannot be verified."""
    pass


def _headers():
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "SanadAI-PaymentVerifier/1.0",
    }

    if TRONGRID_API_KEY:
        headers["TRON-PRO-API-KEY"] = TRONGRID_API_KEY

    return headers


def _get_json(url):
    request = urllib.request.Request(
        url,
        headers=_headers(),
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw)

    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")

        raise PaymentVerificationError(
            f"TRON API HTTP error {exc.code}: {body[:300]}"
        ) from exc

    except urllib.error.URLError as exc:
        raise PaymentVerificationError(
            f"Unable to connect to TRON API: {exc.reason}"
        ) from exc

    except json.JSONDecodeError as exc:
        raise PaymentVerificationError(
            "TRON API returned invalid JSON."
        ) from exc


def _post_json(url, payload):
    body = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        headers=_headers(),
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw)

    except urllib.error.HTTPError as exc:
        response_body = exc.read().decode(
            "utf-8",
            errors="replace"
        )

        raise PaymentVerificationError(
            f"TRON API HTTP error {exc.code}: {response_body[:300]}"
        ) from exc

    except urllib.error.URLError as exc:
        raise PaymentVerificationError(
            f"Unable to connect to TRON API: {exc.reason}"
        ) from exc

    except json.JSONDecodeError as exc:
        raise PaymentVerificationError(
            "TRON API returned invalid JSON."
        ) from exc


def validate_txid(txid):
    """
    Validate a TRON transaction ID.

    TRON transaction IDs are 64 hexadecimal characters.
    """
    if not txid:
        return False

    txid = txid.strip()

    if len(txid) != 64:
        return False

    try:
        int(txid, 16)
    except ValueError:
        return False

    return True


def _normalize_address(address):
    if address is None:
        return ""

    return str(address).strip()


def _normalize_event(event):
    """
    Normalize the different possible shapes returned by TronGrid.
    """
    if not isinstance(event, dict):
        return {}

    result = dict(event)

    # Some responses place decoded fields inside "result".
    nested = event.get("result")

    if isinstance(nested, dict):
        for key, value in nested.items():
            result.setdefault(key, value)

    # Some responses place decoded values inside "data".
    data = event.get("data")

    if isinstance(data, dict):
        for key, value in data.items():
            result.setdefault(key, value)

    return result


def get_transaction_events(txid):
    """
    Get events emitted by a transaction.

    We request confirmed events only.
    """
    url = (
        f"{TRONGRID_BASE_URL}"
        f"/v1/transactions/{txid}/events"
        f"?only_confirmed=true"
    )

    response = _get_json(url)

    if not isinstance(response, dict):
        raise PaymentVerificationError(
            "Unexpected response from TRON events API."
        )

    if "error" in response:
        raise PaymentVerificationError(
            str(response["error"])
        )

    data = response.get("data", [])

    if not isinstance(data, list):
        return []

    return [_normalize_event(item) for item in data]


def get_solidified_transaction_info(txid):
    """
    Get the confirmed execution receipt from SolidityNode.

    SolidityNode returns only solidified transactions.
    """
    url = (
        f"{TRONGRID_BASE_URL}"
        "/walletsolidity/gettransactioninfobyid"
    )

    response = _post_json(
        url,
        {
            "value": txid
        }
    )

    if not isinstance(response, dict):
        raise PaymentVerificationError(
            "Unexpected transaction receipt response."
        )

    if response.get("Error"):
        raise PaymentVerificationError(
            str(response["Error"])
        )

    return response


def transaction_is_successful(tx_info):
    """
    Check whether the confirmed transaction execution succeeded.
    """
    if not tx_info:
        return False

    receipt = tx_info.get("receipt")

    if not isinstance(receipt, dict):
        return False

    result = receipt.get("result")

    if result is None:
        return False

    return str(result).upper() == "SUCCESS"


def _event_contract_address(event):
    """
    Get the smart-contract address that emitted the event.
    """
    for key in (
        "contract_address",
        "contractAddress",
        "address",
    ):
        value = event.get(key)

        if value:
            return _normalize_address(value)

    return ""


def _event_name(event):
    """
    Get event name.
    """
    for key in (
        "event_name",
        "eventName",
        "name",
    ):
        value = event.get(key)

        if value:
            return str(value).strip()

    return ""


def _event_result(event):
    """
    Get decoded event result fields.
    """
    for key in (
        "result",
        "data",
    ):
        value = event.get(key)

        if isinstance(value, dict):
            return value

    return {}


def _get_event_value(event, field):
    """
    Find a decoded event field in several possible locations.
    """
    result = _event_result(event)

    if field in result:
        return result[field]

    if field in event:
        return event[field]

    return None


def _parse_token_amount(value):
    """
    Convert raw TRC20 token amount to human-readable USDT.

    USDT on TRON uses 6 decimals.
    """
    try:
        raw_amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None

    return raw_amount / Decimal(10 ** USDT_DECIMALS)


def _amount_to_raw(amount_usdt):
    """
    Convert configured USDT amount to raw token units.
    """
    try:
        amount = Decimal(str(amount_usdt))
    except (InvalidOperation, TypeError, ValueError):
        raise PaymentVerificationError(
            "Invalid subscription price configuration."
        )

    return int(
        amount * Decimal(10 ** USDT_DECIMALS)
    )


def find_matching_usdt_transfer(events):
    """
    Find a confirmed USDT TRC20 Transfer event that:

    1. Comes from the official USDT TRON contract.
    2. Is a Transfer event.
    3. Sends USDT to PAYMENT_WALLET.
    4. Has an amount >= configured subscription price.
    """
    if not PAYMENT_WALLET:
        raise PaymentVerificationError(
            "PAYMENT_WALLET is not configured."
        )

    minimum_amount = Decimal(
        str(SUBSCRIPTION_PRICE_USDT)
    )

    for event in events:
        contract = _event_contract_address(event)

        if contract != USDT_TRON_CONTRACT:
            continue

        event_name = _event_name(event)

        if event_name and event_name.lower() != "transfer":
            continue

        to_address = _get_event_value(
            event,
            "to"
        )

        if not to_address:
            continue

        to_address = _normalize_address(
            to_address
        )

        if to_address != PAYMENT_WALLET:
            continue

        raw_value = _get_event_value(
            event,
            "value"
        )

        if raw_value is None:
            continue

        amount_usdt = _parse_token_amount(
            raw_value
        )

        if amount_usdt is None:
            continue

        if amount_usdt < minimum_amount:
            continue

        from_address = _get_event_value(
            event,
            "from"
        )

        return {
            "from_address": _normalize_address(
                from_address
            ),
            "to_address": to_address,
            "amount_usdt": amount_usdt,
            "raw_amount": str(raw_value),
            "contract_address": contract,
            "event_name": event_name or "Transfer",
            "block_timestamp": event.get(
                "block_timestamp"
            ),
            "block_number": event.get(
                "block_number"
            ),
        }

    return None


def verify_payment(txid):
    """
    Verify a SanadAI subscription payment.

    Returns a dictionary containing verified payment information.

    Raises PaymentVerificationError when the payment is invalid,
    missing, unconfirmed, failed, or otherwise unsuitable.
    """
    txid = txid.strip()

    if not validate_txid(txid):
        raise PaymentVerificationError(
            "Invalid TRON transaction ID."
        )

    if not TRONGRID_API_KEY:
        raise PaymentVerificationError(
            "TRONGRID_API_KEY is not configured."
        )

    if not PAYMENT_WALLET:
        raise PaymentVerificationError(
            "PAYMENT_WALLET is not configured."
        )

    # --------------------------------------------------------
    # 1. Get solidified transaction receipt.
    # --------------------------------------------------------
    tx_info = get_solidified_transaction_info(
        txid
    )

    if not tx_info:
        raise PaymentVerificationError(
            "Transaction not found or not yet confirmed."
        )

    # --------------------------------------------------------
    # 2. Verify successful execution.
    # --------------------------------------------------------
    if not transaction_is_successful(
        tx_info
    ):
        raise PaymentVerificationError(
            "Transaction exists but execution was not successful."
        )

    # --------------------------------------------------------
    # 3. Get confirmed events.
    # --------------------------------------------------------
    events = get_transaction_events(txid)

    if not events:
        raise PaymentVerificationError(
            "No confirmed contract events were found for this transaction."
        )

    # --------------------------------------------------------
    # 4. Find the correct USDT transfer.
    # --------------------------------------------------------
    transfer = find_matching_usdt_transfer(
        events
    )

    if not transfer:
        raise PaymentVerificationError(
            "No confirmed USDT TRC20 payment to the SanadAI wallet was found."
        )

    # --------------------------------------------------------
    # 5. Return verified payment information.
    # --------------------------------------------------------
    return {
        "verified": True,
        "txid": txid,
        "network": "TRON",
        "token": "USDT",
        "contract_address": USDT_TRON_CONTRACT,
        "recipient_address": transfer[
            "to_address"
        ],
        "sender_address": transfer[
            "from_address"
        ],
        "amount_usdt": transfer[
            "amount_usdt"
        ],
        "raw_amount": transfer[
            "raw_amount"
        ],
        "block_timestamp": transfer.get(
            "block_timestamp"
        ),
        "block_number": transfer.get(
            "block_number"
        ),
  }
