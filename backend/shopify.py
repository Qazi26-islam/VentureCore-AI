from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable

from cryptography.fernet import Fernet, InvalidToken

from backend import config
from backend.db import get_connection
from backend.money import major_to_minor, multiply_minor


logger = logging.getLogger("shopify_connector")
SOURCE = "shopify"
SHOP_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\.myshopify\.com$")
WEBHOOK_TOPICS = (
    "PRODUCTS_CREATE",
    "PRODUCTS_UPDATE",
    "ORDERS_CREATE",
    "ORDERS_UPDATED",
    "APP_UNINSTALLED",
)
PRODUCTS_QUERY = """query Products($cursor: String, $filter: String) {
  shop { currencyCode }
  products(first: 50, after: $cursor, query: $filter, sortKey: UPDATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes { id title productType updatedAt variants(first: 100) { nodes {
      id sku title price inventoryQuantity updatedAt inventoryItem { unitCost { amount currencyCode } }
    } } }
  }
}"""
ORDERS_QUERY = """query Orders($cursor: String, $filter: String) {
  shop { currencyCode }
  orders(first: 50, after: $cursor, query: $filter, sortKey: UPDATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes { id name updatedAt createdAt displayFinancialStatus customer { id displayName email phone }
      lineItems(first: 100) { nodes { id name sku quantity variant { id } originalUnitPriceSet {
        shopMoney { amount currencyCode } } } }
    }
  }
}"""


class ShopifyError(RuntimeError):
    pass


class ShopifyRequestError(ShopifyError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> dict[str, Any]:
        return json.loads(self.body.decode("utf-8"))


def _transport(url: str, method: str, headers: dict[str, str], body: bytes | None) -> HttpResponse:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return HttpResponse(response.status, dict(response.headers), response.read())
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.code, dict(exc.headers), exc.read())


def _configured() -> bool:
    return bool(
        config.SHOPIFY_CLIENT_ID
        and config.SHOPIFY_CLIENT_SECRET
        and config.SHOPIFY_TOKEN_ENCRYPTION_KEY
        and config.SHOPIFY_APP_URL
    )


def _fernet() -> Fernet:
    if not config.SHOPIFY_TOKEN_ENCRYPTION_KEY:
        raise ShopifyError("Shopify token encryption is not configured.")
    try:
        return Fernet(config.SHOPIFY_TOKEN_ENCRYPTION_KEY.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise ShopifyError("SHOPIFY_TOKEN_ENCRYPTION_KEY is invalid.") from exc


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ShopifyError("Stored Shopify credentials cannot be decrypted.") from exc


def normalize_shop(shop: str) -> str:
    domain = shop.strip().lower().rstrip(".")
    if not SHOP_RE.fullmatch(domain):
        raise ShopifyError("Enter a valid store address ending in .myshopify.com.")
    return domain


def oauth_install_url(shop: str, state: str) -> str:
    if not _configured():
        raise ShopifyError("Shopify is not configured on this server.")
    domain = normalize_shop(shop)
    query = urllib.parse.urlencode(
        {
            "client_id": config.SHOPIFY_CLIENT_ID,
            "scope": config.SHOPIFY_SCOPES,
            "redirect_uri": f"{config.SHOPIFY_APP_URL}/integrations/shopify/callback",
            "state": state,
        }
    )
    return f"https://{domain}/admin/oauth/authorize?{query}"


def verify_oauth_hmac(params: dict[str, str]) -> bool:
    supplied = params.get("hmac", "")
    message = "&".join(
        f"{key}={value}" for key, value in sorted(params.items()) if key not in {"hmac", "signature"}
    )
    expected = hmac.new(
        config.SHOPIFY_CLIENT_SECRET.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return bool(supplied) and hmac.compare_digest(expected, supplied)


def verify_webhook(raw_body: bytes, supplied_hmac: str) -> bool:
    expected = base64.b64encode(
        hmac.new(config.SHOPIFY_CLIENT_SECRET.encode("utf-8"), raw_body, hashlib.sha256).digest()
    ).decode("ascii")
    return bool(supplied_hmac) and hmac.compare_digest(expected, supplied_hmac)


def _request_with_retry(
    url: str, method: str, headers: dict[str, str], body: bytes | None,
    *, attempts: int = 4, sleep: Callable[[float], None] | None = None,
) -> HttpResponse:
    sleeper = sleep or time.sleep
    last_response = None
    for attempt in range(attempts):
        try:
            response = _transport(url, method, headers, body)
        except (OSError, TimeoutError) as exc:
            if attempt == attempts - 1:
                raise ShopifyRequestError("Shopify could not be reached.") from exc
            sleeper((0.5 * (2 ** attempt)) + random.uniform(0, 0.25))
            continue
        last_response = response
        if 200 <= response.status < 300:
            return response
        retryable = response.status == 429 or response.status >= 500
        if not retryable or attempt == attempts - 1:
            raise ShopifyRequestError(
                f"Shopify returned HTTP {response.status}.", status=response.status
            )
        retry_after = response.headers.get("Retry-After") or response.headers.get("retry-after")
        delay = float(retry_after) if retry_after else 0.5 * (2 ** attempt)
        sleeper(delay + random.uniform(0, 0.25))
    raise ShopifyRequestError(
        "Shopify request failed.", status=last_response.status if last_response else None
    )


def _token_request(shop: str, fields: dict[str, str]) -> dict[str, Any]:
    response = _request_with_retry(
        f"https://{shop}/admin/oauth/access_token",
        "POST",
        {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        urllib.parse.urlencode(fields).encode("utf-8"),
    )
    return response.json()


def _expiry(seconds: Any) -> str | None:
    if seconds is None:
        return None
    return (datetime.now(timezone.utc) + timedelta(seconds=int(seconds))).isoformat()


def store_connection(
    organization_id: int, user_id: int, shop: str, token_data: dict[str, Any]
) -> int:
    access_token = str(token_data.get("access_token") or "")
    if not access_token:
        raise ShopifyError("Shopify did not return an access token.")
    refresh_token = token_data.get("refresh_token")
    conn = get_connection()
    conn.execute(
        """INSERT INTO shopify_connections
           (organization_id, user_id, shop_domain, access_token_encrypted,
            refresh_token_encrypted, token_expires_at, refresh_token_expires_at,
            scopes, status, last_error, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'connected', NULL, CURRENT_TIMESTAMP)
           ON CONFLICT(organization_id) DO UPDATE SET
             user_id=excluded.user_id, shop_domain=excluded.shop_domain,
             access_token_encrypted=excluded.access_token_encrypted,
             refresh_token_encrypted=excluded.refresh_token_encrypted,
             token_expires_at=excluded.token_expires_at,
             refresh_token_expires_at=excluded.refresh_token_expires_at,
             scopes=excluded.scopes, status='connected', last_error=NULL,
             updated_at=CURRENT_TIMESTAMP""",
        (
            organization_id, user_id, shop, encrypt_secret(access_token),
            encrypt_secret(str(refresh_token)) if refresh_token else None,
            _expiry(token_data.get("expires_in")),
            _expiry(token_data.get("refresh_token_expires_in")),
            str(token_data.get("scope") or config.SHOPIFY_SCOPES),
        ),
    )
    row = conn.execute(
        "SELECT id FROM shopify_connections WHERE organization_id = ?", (organization_id,)
    ).fetchone()
    conn.commit()
    conn.close()
    return int(row["id"])


def complete_oauth(
    organization_id: int, user_id: int, shop: str, code: str, params: dict[str, str]
) -> int:
    domain = normalize_shop(shop)
    if not verify_oauth_hmac(params):
        raise ShopifyError("Shopify callback signature was invalid.")
    token_data = _token_request(
        domain,
        {
            "client_id": config.SHOPIFY_CLIENT_ID,
            "client_secret": config.SHOPIFY_CLIENT_SECRET,
            "code": code,
            "expiring": "1",
        },
    )
    connection_id = store_connection(organization_id, user_id, domain, token_data)
    try:
        register_webhooks(connection_id, organization_id)
    except ShopifyError as exc:
        _set_connection_error(
            connection_id, organization_id, f"Connected, but webhook setup failed: {exc}"
        )
    return connection_id


def _connection(connection_id: int, organization_id: int) -> dict[str, Any]:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM shopify_connections WHERE id = ? AND organization_id = ?",
        (connection_id, organization_id),
    ).fetchone()
    conn.close()
    if row is None:
        raise ShopifyError("Shopify connection was not found.")
    return dict(row)


def connection_for_organization(organization_id: int) -> dict[str, Any] | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM shopify_connections WHERE organization_id = ?", (organization_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _refresh(connection: dict[str, Any]) -> str:
    encrypted_refresh = connection.get("refresh_token_encrypted")
    if not encrypted_refresh:
        raise ShopifyError("Reconnect Shopify to renew access.")
    token_data = _token_request(
        connection["shop_domain"],
        {
            "grant_type": "refresh_token",
            "client_id": config.SHOPIFY_CLIENT_ID,
            "client_secret": config.SHOPIFY_CLIENT_SECRET,
            "refresh_token": decrypt_secret(encrypted_refresh),
        },
    )
    store_connection(
        int(connection["organization_id"]), int(connection["user_id"]),
        connection["shop_domain"], token_data,
    )
    return str(token_data["access_token"])


def _access_token(connection: dict[str, Any]) -> str:
    expires_at = connection.get("token_expires_at")
    if expires_at:
        expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        if expiry <= datetime.now(timezone.utc) + timedelta(minutes=5):
            return _refresh(connection)
    return decrypt_secret(connection["access_token_encrypted"])


def graphql(
    connection_id: int, organization_id: int, query: str, variables: dict[str, Any]
) -> dict[str, Any]:
    connection = _connection(connection_id, organization_id)
    token = _access_token(connection)
    url = (
        f"https://{connection['shop_domain']}/admin/api/"
        f"{config.SHOPIFY_API_VERSION}/graphql.json"
    )
    for throttle_attempt in range(4):
        response = _request_with_retry(
            url, "POST",
            {"Content-Type": "application/json", "X-Shopify-Access-Token": token},
            json.dumps({"query": query, "variables": variables}).encode("utf-8"),
        )
        payload = response.json()
        errors = payload.get("errors") or []
        throttled = any(
            error.get("extensions", {}).get("code") == "THROTTLED" for error in errors
        )
        if throttled and throttle_attempt < 3:
            time.sleep((0.5 * (2 ** throttle_attempt)) + random.uniform(0, 0.25))
            continue
        if errors:
            raise ShopifyRequestError(str(errors[0].get("message") or "Shopify query failed."))
        return payload.get("data") or {}
    raise ShopifyRequestError("Shopify remained rate limited.", status=429)


def register_webhooks(connection_id: int, organization_id: int) -> None:
    callback = f"{config.SHOPIFY_APP_URL}/integrations/shopify/webhook"
    mutation = """mutation Register($topic: WebhookSubscriptionTopic!, $uri: URL!) {
      webhookSubscriptionCreate(topic: $topic, webhookSubscription: {uri: $uri, format: JSON}) {
        userErrors { field message } webhookSubscription { id }
      }
    }"""
    for topic in WEBHOOK_TOPICS:
        data = graphql(connection_id, organization_id, mutation, {"topic": topic, "uri": callback})
        errors = data.get("webhookSubscriptionCreate", {}).get("userErrors", [])
        if errors and not any("already" in str(error.get("message", "")).lower() for error in errors):
            raise ShopifyError(str(errors[0].get("message") or "Webhook setup failed."))


def _money(value: Any, currency: str) -> int:
    return major_to_minor(Decimal(str(value or "0")), currency)


def _upsert_inventory_snapshot(
    conn, organization_id: int, user_id: int, product_id: int, variant_id: str,
    quantity: float, unit_cost_minor: int, currency: str, synced_at: str,
) -> None:
    external_id = f"inventory:{variant_id}"
    other_quantity = conn.execute(
        """SELECT COALESCE(SUM(quantity_change), 0) FROM inventory_transactions
           WHERE organization_id = ? AND product_id = ?
             AND NOT (source = ? AND external_id = ?)""",
        (organization_id, product_id, SOURCE, external_id),
    ).fetchone()[0]
    adjusted_snapshot = quantity - float(other_quantity)
    conn.execute(
        """INSERT INTO inventory_transactions
           (product_id, user_id, organization_id, transaction_type, quantity_change,
            unit_cost_minor, currency, reference_note, source, external_id, last_synced_at)
           VALUES (?, ?, ?, 'adjustment', ?, ?, ?, 'Shopify inventory snapshot', ?, ?, ?)
           ON CONFLICT(organization_id, source, external_id) WHERE external_id IS NOT NULL DO UPDATE SET
             product_id=excluded.product_id, quantity_change=excluded.quantity_change,
             unit_cost_minor=excluded.unit_cost_minor, currency=excluded.currency,
             last_synced_at=excluded.last_synced_at""",
        (
            product_id, user_id, organization_id, adjusted_snapshot, unit_cost_minor,
            currency, SOURCE, external_id, synced_at,
        ),
    )


def _upsert_product(
    conn, organization_id: int, user_id: int, product: dict[str, Any],
    variant: dict[str, Any], currency: str,
) -> int:
    external_id = str(variant.get("id") or "")
    if not external_id:
        raise ShopifyError("A Shopify variant was missing its identifier.")
    sku = str(variant.get("sku") or "").strip()
    row = conn.execute(
        "SELECT id, unit_cost_minor FROM products WHERE organization_id = ? AND source = ? AND external_id = ?",
        (organization_id, SOURCE, external_id),
    ).fetchone()
    if row is None and sku:
        row = conn.execute(
            "SELECT id, unit_cost_minor FROM products WHERE organization_id = ? AND sku = ?",
            (organization_id, sku),
        ).fetchone()
    cost = variant.get("inventoryItem", {}).get("unitCost") or {}
    cost_currency = str(cost.get("currencyCode") or currency)
    cost_minor = (
        _money(cost.get("amount"), currency)
        if cost and cost_currency == currency
        else int(row["unit_cost_minor"] if row else 0)
    )
    price_minor = _money(variant.get("price"), currency)
    title = str(product.get("title") or variant.get("title") or "Shopify product")
    if variant.get("title") and variant.get("title") != "Default Title":
        title = f"{title} — {variant['title']}"
    synced_at = str(variant.get("updatedAt") or product.get("updatedAt") or datetime.now(timezone.utc).isoformat())
    if row:
        product_id = int(row["id"])
        conn.execute(
            """UPDATE products SET user_id=?, sku=?, name=?, category=?, unit_cost_minor=?,
               selling_price_minor=?, currency=?, active=1, source=?, external_id=?,
               last_synced_at=? WHERE id=? AND organization_id=?""",
            (
                user_id, sku, title, str(product.get("productType") or ""), cost_minor,
                price_minor, currency, SOURCE, external_id, synced_at, product_id, organization_id,
            ),
        )
    else:
        cursor = conn.execute(
            """INSERT INTO products
               (user_id, organization_id, sku, name, category, unit_cost_minor,
                selling_price_minor, currency, source, external_id, last_synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id, organization_id, sku, title, str(product.get("productType") or ""),
                cost_minor, price_minor, currency, SOURCE, external_id, synced_at,
            ),
        )
        product_id = int(cursor.lastrowid)
    if variant.get("inventoryQuantity") is not None:
        _upsert_inventory_snapshot(
            conn, organization_id, user_id, product_id, external_id,
            float(variant["inventoryQuantity"]), cost_minor, currency, synced_at,
        )
    return product_id


def _upsert_customer(conn, organization_id: int, user_id: int, customer: dict[str, Any]) -> int | None:
    external_id = str(customer.get("id") or "")
    email = str(customer.get("email") or "").strip().lower()
    if not external_id and not email:
        return None
    row = None
    if external_id:
        row = conn.execute(
            "SELECT id FROM customers WHERE organization_id = ? AND source = ? AND external_id = ?",
            (organization_id, SOURCE, external_id),
        ).fetchone()
    if row is None and email:
        row = conn.execute(
            "SELECT id FROM customers WHERE organization_id = ? AND lower(email) = ?",
            (organization_id, email),
        ).fetchone()
    name = str(customer.get("displayName") or customer.get("first_name") or email or "Shopify customer")
    synced_at = datetime.now(timezone.utc).isoformat()
    if row:
        customer_id = int(row["id"])
        conn.execute(
            """UPDATE customers SET name=?, email=?, phone=?, source=?, external_id=?,
               last_synced_at=? WHERE id=? AND organization_id=?""",
            (name, email, str(customer.get("phone") or ""), SOURCE, external_id or None,
             synced_at, customer_id, organization_id),
        )
        return customer_id
    cursor = conn.execute(
        """INSERT INTO customers
           (user_id, organization_id, name, email, phone, source, external_id, last_synced_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, organization_id, name, email, str(customer.get("phone") or ""),
         SOURCE, external_id or None, synced_at),
    )
    return int(cursor.lastrowid)


def _upsert_order(
    conn, organization_id: int, user_id: int, order: dict[str, Any], currency: str
) -> int:
    customer_id = _upsert_customer(conn, organization_id, user_id, order.get("customer") or {})
    status = str(order.get("displayFinancialStatus") or order.get("financial_status") or "").upper()
    payment_status = "paid" if status in {"PAID", "PARTIALLY_REFUNDED"} else "due"
    created_at = str(order.get("createdAt") or order.get("created_at") or datetime.now(timezone.utc).isoformat())
    line_items = order.get("lineItems", {}).get("nodes") if isinstance(order.get("lineItems"), dict) else order.get("line_items")
    records = 0
    for line in line_items or []:
        line_id = str(line.get("id") or "")
        if not line_id:
            continue
        variant = line.get("variant") or {}
        variant_id = str(variant.get("id") or line.get("variant_id") or f"unknown:{line_id}")
        product_row = conn.execute(
            "SELECT id FROM products WHERE organization_id = ? AND source = ? AND external_id = ?",
            (organization_id, SOURCE, variant_id),
        ).fetchone()
        sku = str(line.get("sku") or "").strip()
        if product_row is None and sku:
            product_row = conn.execute(
                "SELECT id FROM products WHERE organization_id = ? AND sku = ?",
                (organization_id, sku),
            ).fetchone()
        price_money = line.get("originalUnitPriceSet", {}).get("shopMoney", {})
        price = price_money.get("amount", line.get("price", 0))
        line_currency = str(price_money.get("currencyCode") or currency)
        if product_row is None:
            product_id = _upsert_product(
                conn, organization_id, user_id,
                {"title": line.get("name"), "productType": "", "updatedAt": order.get("updatedAt")},
                {"id": variant_id, "sku": sku, "price": price, "title": "Default Title"},
                line_currency,
            )
        else:
            product_id = int(product_row["id"])
            conn.execute(
                """UPDATE products SET source=?, external_id=?, last_synced_at=?
                   WHERE id=? AND organization_id=?""",
                (SOURCE, variant_id, order.get("updatedAt") or created_at, product_id, organization_id),
            )
        quantity = float(line.get("quantity") or 0)
        unit_price_minor = _money(price, line_currency)
        total_minor = multiply_minor(unit_price_minor, quantity)
        external_id = f"{order.get('id')}:{line_id}"
        conn.execute(
            """INSERT INTO sales_orders
               (user_id, organization_id, customer_id, product_id, quantity,
                unit_price_minor, total_amount_minor, currency, payment_status,
                reference_note, source, external_id, last_synced_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(organization_id, source, external_id) WHERE external_id IS NOT NULL DO UPDATE SET
                 customer_id=excluded.customer_id, product_id=excluded.product_id,
                 quantity=excluded.quantity, unit_price_minor=excluded.unit_price_minor,
                 total_amount_minor=excluded.total_amount_minor, currency=excluded.currency,
                 payment_status=excluded.payment_status, last_synced_at=excluded.last_synced_at""",
            (
                user_id, organization_id, customer_id, product_id, quantity, unit_price_minor,
                total_minor, line_currency, payment_status, str(order.get("name") or ""),
                SOURCE, external_id, order.get("updatedAt") or created_at, created_at,
            ),
        )
        sale = conn.execute(
            "SELECT id FROM sales_orders WHERE organization_id = ? AND source = ? AND external_id = ?",
            (organization_id, SOURCE, external_id),
        ).fetchone()
        finance_external_id = f"payment:{external_id}"
        if payment_status == "paid":
            conn.execute(
                """INSERT INTO finance_transactions
                   (user_id, organization_id, transaction_type, amount_minor, currency,
                    category, description, source, external_id, last_synced_at,
                    related_sale_id, transaction_date)
                   VALUES (?, ?, 'income', ?, ?, 'Sales Revenue', ?, ?, ?, ?, ?, date(?))
                   ON CONFLICT(organization_id, source, external_id) WHERE external_id IS NOT NULL DO UPDATE SET
                     amount_minor=excluded.amount_minor, currency=excluded.currency,
                     related_sale_id=excluded.related_sale_id,
                     last_synced_at=excluded.last_synced_at""",
                (
                    user_id, organization_id, total_minor, line_currency,
                    f"Shopify payment for {order.get('name') or order.get('id')}", SOURCE,
                    finance_external_id, order.get("updatedAt") or created_at,
                    int(sale["id"]), created_at,
                ),
            )
        else:
            conn.execute(
                "DELETE FROM finance_transactions WHERE organization_id = ? AND source = ? AND external_id = ?",
                (organization_id, SOURCE, finance_external_id),
            )
        records += 1
    return records


def _process_page(connection: dict[str, Any], resource: str, data: dict[str, Any]) -> int:
    organization_id = int(connection["organization_id"])
    user_id = int(connection["user_id"])
    currency = str(data.get("shop", {}).get("currencyCode") or "MYR")
    conn = get_connection()
    try:
        conn.execute("BEGIN")
        records = 0
        if resource == "products":
            for product in data.get("products", {}).get("nodes", []):
                for variant in product.get("variants", {}).get("nodes", []):
                    _upsert_product(conn, organization_id, user_id, product, variant, currency)
                    records += 1
        else:
            for order in data.get("orders", {}).get("nodes", []):
                records += _upsert_order(conn, organization_id, user_id, order, currency)
        conn.commit()
        return records
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _set_connection_error(connection_id: int, organization_id: int, message: str) -> None:
    conn = get_connection()
    conn.execute(
        """UPDATE shopify_connections SET status='stale', last_error=?,
           updated_at=CURRENT_TIMESTAMP WHERE id=? AND organization_id=?""",
        (message[:800], connection_id, organization_id),
    )
    conn.commit()
    conn.close()


def sync_connection(
    connection_id: int, organization_id: int, *, incremental: bool = False
) -> dict[str, Any]:
    connection = _connection(connection_id, organization_id)
    if connection["status"] == "disconnected":
        raise ShopifyError("Reconnect Shopify before syncing.")
    resource = connection.get("sync_resource") if connection.get("sync_mode") == ("incremental" if incremental else "backfill") else None
    resource = resource or "products"
    cursor = connection.get("sync_cursor") if connection.get("sync_resource") == resource else None
    updated_since = connection.get("last_successful_sync_at") if incremental else None
    total = 0
    conn = get_connection()
    conn.execute(
        """UPDATE shopify_connections SET status='syncing', sync_mode=?, sync_resource=?,
           sync_cursor=?, last_attempt_at=CURRENT_TIMESTAMP, last_error=NULL
           WHERE id=? AND organization_id=?""",
        (
            "incremental" if incremental else "backfill", resource, cursor,
            connection_id, organization_id,
        ),
    )
    conn.commit()
    conn.close()
    try:
        while resource:
            query = PRODUCTS_QUERY if resource == "products" else ORDERS_QUERY
            filter_text = f"updated_at:>'{updated_since}'" if updated_since else None
            data = graphql(
                connection_id, organization_id, query,
                {"cursor": cursor, "filter": filter_text},
            )
            total += _process_page(connection, resource, data)
            page_info = data.get(resource, {}).get("pageInfo", {})
            if page_info.get("hasNextPage"):
                cursor = page_info.get("endCursor")
                conn = get_connection()
                conn.execute(
                    """UPDATE shopify_connections SET sync_resource=?, sync_cursor=?,
                       records_synced=records_synced+?, updated_at=CURRENT_TIMESTAMP
                       WHERE id=? AND organization_id=?""",
                    (resource, cursor, total, connection_id, organization_id),
                )
                conn.commit()
                conn.close()
                total = 0
                continue
            resource = "orders" if resource == "products" else None
            cursor = None
            conn = get_connection()
            conn.execute(
                """UPDATE shopify_connections SET sync_resource=?, sync_cursor=NULL,
                   records_synced=records_synced+?, updated_at=CURRENT_TIMESTAMP
                   WHERE id=? AND organization_id=?""",
                (resource, total, connection_id, organization_id),
            )
            conn.commit()
            conn.close()
            total = 0
        conn = get_connection()
        conn.execute(
            """UPDATE shopify_connections SET status='connected', sync_mode=NULL,
               sync_resource=NULL, sync_cursor=NULL, last_successful_sync_at=CURRENT_TIMESTAMP,
               last_error=NULL, updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND organization_id=?""",
            (connection_id, organization_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM shopify_connections WHERE id = ? AND organization_id = ?",
            (connection_id, organization_id),
        ).fetchone()
        conn.close()
        return dict(row)
    except Exception as exc:
        _set_connection_error(
            connection_id, organization_id, f"Shopify data is temporarily stale: {exc}"
        )
        raise


def process_webhook(
    shop: str, webhook_id: str, event_id: str | None, topic: str, raw_body: bytes
) -> str:
    domain = normalize_shop(shop)
    conn = get_connection()
    connection = conn.execute(
        """SELECT * FROM shopify_connections
           WHERE shop_domain = ? AND status != 'disconnected'""",
        (domain,),
    ).fetchone()
    if connection is None:
        conn.close()
        raise ShopifyError("No active Shopify connection matches this webhook.")
    try:
        conn.execute(
            """INSERT INTO shopify_webhook_events
               (organization_id, connection_id, webhook_id, event_id, topic, payload_encrypted)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                connection["organization_id"], connection["id"], webhook_id, event_id,
                topic, encrypt_secret(raw_body.decode("utf-8")),
            ),
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        if "UNIQUE" in str(exc):
            conn.close()
            return "duplicate"
        conn.close()
        raise
    event = conn.execute(
        """SELECT id FROM shopify_webhook_events
           WHERE organization_id = ? AND connection_id = ? AND webhook_id = ?""",
        (connection["organization_id"], connection["id"], webhook_id),
    ).fetchone()
    conn.close()
    try:
        payload = json.loads(raw_body)
        topic_key = topic.lower()
        if topic_key in {"products/create", "products/update"}:
            product_external_id = payload.get("admin_graphql_api_id") or payload.get("id")
            product = {
                "id": str(product_external_id) if product_external_id is not None else "",
                "title": payload.get("title"), "productType": payload.get("product_type"),
                "updatedAt": payload.get("updated_at"), "variants": {"nodes": []},
            }
            for variant in payload.get("variants") or []:
                variant_external_id = variant.get("admin_graphql_api_id") or variant.get("id")
                product["variants"]["nodes"].append(
                    {
                        "id": str(variant_external_id) if variant_external_id is not None else "",
                        "sku": variant.get("sku"), "title": variant.get("title"),
                        "price": variant.get("price"),
                        "inventoryQuantity": variant.get("inventory_quantity"),
                        "updatedAt": variant.get("updated_at"),
                    }
                )
            _process_page(dict(connection), "products", {
                "shop": {"currencyCode": payload.get("currency") or "MYR"},
                "products": {"nodes": [product]},
            })
        elif topic_key in {"orders/create", "orders/updated"}:
            order = dict(payload)
            order["id"] = str(payload.get("admin_graphql_api_id") or payload.get("id"))
            _process_page(dict(connection), "orders", {
                "shop": {"currencyCode": payload.get("currency") or "MYR"},
                "orders": {"nodes": [order]},
            })
        elif topic_key == "app/uninstalled":
            disconnect(int(connection["organization_id"]))
        conn = get_connection()
        conn.execute(
            """UPDATE shopify_webhook_events SET status='processed', attempts=attempts+1,
               processed_at=CURRENT_TIMESTAMP, last_error=NULL
               WHERE id=? AND organization_id=?""",
            (event["id"], connection["organization_id"]),
        )
        conn.commit()
        conn.close()
        return "processed"
    except Exception as exc:
        conn = get_connection()
        conn.execute(
            """UPDATE shopify_webhook_events SET status='dead_letter', attempts=attempts+1,
               last_error=? WHERE id=? AND organization_id=?""",
            (
                f"{type(exc).__name__}: {str(exc)[:500]}", event["id"],
                connection["organization_id"],
            ),
        )
        conn.commit()
        conn.close()
        _set_connection_error(
            int(connection["id"]), int(connection["organization_id"]),
            "A Shopify update could not be processed; existing data remains available.",
        )
        return "dead_letter"


def disconnect(organization_id: int) -> None:
    conn = get_connection()
    conn.execute(
        """UPDATE shopify_connections SET status='disconnected',
           access_token_encrypted='', refresh_token_encrypted=NULL,
           token_expires_at=NULL, refresh_token_expires_at=NULL,
           updated_at=CURRENT_TIMESTAMP WHERE organization_id=?""",
        (organization_id,),
    )
    conn.commit()
    conn.close()


def public_status(organization_id: int) -> dict[str, Any]:
    row = connection_for_organization(organization_id)
    if row is None or row["status"] == "disconnected":
        return {"connected": False, "configured": _configured()}
    return {
        "connected": True,
        "configured": _configured(),
        "shop": row["shop_domain"],
        "status": row["status"],
        "last_successful_sync": row["last_successful_sync_at"],
        "records_synced": int(row["records_synced"]),
        "error": row["last_error"],
    }


def reconcile_due_connections() -> int:
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=config.SHOPIFY_RECONCILE_HOURS)
    ).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    rows = conn.execute(
        """SELECT id, organization_id FROM shopify_connections WHERE status != 'disconnected'
           AND (last_successful_sync_at IS NULL OR last_successful_sync_at < ?)""",
        (cutoff,),
    ).fetchall()
    conn.close()
    for row in rows:
        try:
            sync_connection(
                int(row["id"]), int(row["organization_id"]), incremental=True
            )
        except Exception as exc:
            logger.warning("Shopify reconciliation failed for connection %s: %s", row["id"], type(exc).__name__)
    return len(rows)


_worker_started = False


def start_reconciliation_worker() -> None:
    global _worker_started
    if _worker_started or not _configured():
        return
    _worker_started = True

    def worker() -> None:
        while True:
            try:
                reconcile_due_connections()
            except Exception as exc:
                logger.warning("Shopify reconciliation pass failed: %s", type(exc).__name__)
            time.sleep(900)

    threading.Thread(target=worker, daemon=True, name="shopify-reconciliation").start()
