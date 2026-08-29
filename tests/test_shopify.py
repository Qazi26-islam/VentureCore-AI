import base64
import hashlib
import hmac
import json
import os
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

import backend.config as config
import backend.db as database
import backend.shopify as shopify
from backend.main import app


def product_page(nodes, has_next=False, cursor=None):
    return {
        "shop": {"currencyCode": "USD"},
        "products": {
            "nodes": nodes,
            "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
        },
    }


def product(product_id, variant_id, sku):
    return {
        "id": product_id,
        "title": f"Product {sku}",
        "productType": "Goods",
        "updatedAt": "2026-08-28T00:00:00Z",
        "variants": {"nodes": [{
            "id": variant_id,
            "sku": sku,
            "title": "Default Title",
            "price": "12.50",
            "inventoryQuantity": 8,
            "updatedAt": "2026-08-28T00:00:00Z",
            "inventoryItem": {"unitCost": {"amount": "5.25", "currencyCode": "USD"}},
        }]},
    }


class ShopifyConnectorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "shopify.db"
        self.original_config = {
            "SHOPIFY_CLIENT_ID": config.SHOPIFY_CLIENT_ID,
            "SHOPIFY_CLIENT_SECRET": config.SHOPIFY_CLIENT_SECRET,
            "SHOPIFY_TOKEN_ENCRYPTION_KEY": config.SHOPIFY_TOKEN_ENCRYPTION_KEY,
            "SHOPIFY_APP_URL": config.SHOPIFY_APP_URL,
        }
        config.SHOPIFY_CLIENT_ID = "client-id"
        config.SHOPIFY_CLIENT_SECRET = "client-secret"
        config.SHOPIFY_TOKEN_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")
        config.SHOPIFY_APP_URL = "https://venturecore.example"
        database.init_db()
        conn = database.get_connection()
        cursor = conn.execute(
            "INSERT INTO users (email, password_hash, salt) VALUES ('shop@example.com', '!', 'salt')"
        )
        self.user_id = int(cursor.lastrowid)
        conn.commit()
        conn.close()

    def tearDown(self):
        for name, value in self.original_config.items():
            setattr(config, name, value)
        database.DB_PATH = self.original_database
        self.temp_dir.cleanup()

    def _connection(self):
        connection_id = shopify.store_connection(
            1, self.user_id, "fixture.myshopify.com",
            {
                "access_token": "access-secret",
                "refresh_token": "refresh-secret",
                "expires_in": 3600,
                "refresh_token_expires_in": 86400,
                "scope": config.SHOPIFY_SCOPES,
            },
        )
        return connection_id

    def test_oauth_callback_validates_and_encrypts_tokens(self):
        with patch("backend.main.start_reconciliation_worker"), patch(
            "backend.api.shopify._start_initial_sync"
        ), TestClient(app) as client:
            signup = client.post(
                "/auth/signup",
                json={"email": "oauth@example.com", "password": "strong-password"},
            )
            self.assertEqual(signup.status_code, 200)
            install = client.get(
                "/integrations/shopify/install?shop=fixture.myshopify.com",
                follow_redirects=False,
            )
            self.assertEqual(install.status_code, 302)
            state = urllib.parse.parse_qs(urllib.parse.urlparse(install.headers["location"]).query)["state"][0]
            params = {
                "shop": "fixture.myshopify.com", "code": "authorization-code",
                "state": state, "timestamp": "1787880000",
            }
            message = "&".join(f"{key}={value}" for key, value in sorted(params.items()))
            params["hmac"] = hmac.new(
                config.SHOPIFY_CLIENT_SECRET.encode(), message.encode(), hashlib.sha256
            ).hexdigest()
            with patch("backend.shopify._token_request", return_value={
                "access_token": "oauth-access-secret", "refresh_token": "oauth-refresh-secret",
                "expires_in": 3600, "refresh_token_expires_in": 7776000,
                "scope": config.SHOPIFY_SCOPES,
            }), patch("backend.shopify.register_webhooks"):
                callback = client.get(
                    "/integrations/shopify/callback?" + urllib.parse.urlencode(params),
                    follow_redirects=False,
                )
            self.assertEqual(callback.status_code, 302, callback.text)
        conn = database.get_connection()
        row = conn.execute("SELECT * FROM shopify_connections WHERE organization_id = 1").fetchone()
        conn.close()
        self.assertNotIn("oauth-access-secret", row["access_token_encrypted"])
        self.assertEqual(shopify.decrypt_secret(row["access_token_encrypted"]), "oauth-access-secret")

    def test_webhook_signature_replay_and_dead_letter(self):
        self._connection()
        payload = {
            "id": 10, "admin_graphql_api_id": "gid://shopify/Product/10",
            "title": "Webhook item", "product_type": "Goods", "currency": "USD",
            "updated_at": "2026-08-28T00:00:00Z",
            "variants": [{
                "id": 20, "admin_graphql_api_id": "gid://shopify/ProductVariant/20",
                "sku": "WEB-1", "title": "Default Title", "price": "9.99",
                "inventory_quantity": 4, "updated_at": "2026-08-28T00:00:00Z",
            }],
        }
        raw = json.dumps(payload, separators=(",", ":")).encode()
        signature = base64.b64encode(
            hmac.new(config.SHOPIFY_CLIENT_SECRET.encode(), raw, hashlib.sha256).digest()
        ).decode()
        headers = {
            "X-Shopify-Hmac-Sha256": signature,
            "X-Shopify-Webhook-Id": "delivery-1",
            "X-Shopify-Event-Id": "event-1",
            "X-Shopify-Shop-Domain": "fixture.myshopify.com",
            "X-Shopify-Topic": "products/update",
            "Content-Type": "application/json",
        }
        with patch("backend.main.start_reconciliation_worker"), TestClient(app) as client:
            self.assertEqual(client.post("/integrations/shopify/webhook", content=raw, headers={**headers, "X-Shopify-Hmac-Sha256": "bad"}).status_code, 401)
            first = client.post("/integrations/shopify/webhook", content=raw, headers=headers)
            replay = client.post("/integrations/shopify/webhook", content=raw, headers=headers)
        self.assertEqual(first.json()["status"], "processed")
        self.assertEqual(replay.json()["status"], "duplicate")
        conn = database.get_connection()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM products WHERE sku='WEB-1'").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM shopify_webhook_events").fetchone()[0], 1)
        conn.close()

        broken = json.dumps({"title": "No variant id", "variants": [{"sku": "BAD"}]}).encode()
        outcome = shopify.process_webhook(
            "fixture.myshopify.com", "delivery-2", None, "products/update", broken
        )
        self.assertEqual(outcome, "dead_letter")
        conn = database.get_connection()
        row = conn.execute(
            "SELECT status, last_error FROM shopify_webhook_events WHERE webhook_id='delivery-2'"
        ).fetchone()
        conn.close()
        self.assertEqual(row["status"], "dead_letter")
        self.assertTrue(row["last_error"])

    def test_paginated_backfill_is_resumable_and_finishes_cleanly(self):
        connection_id = self._connection()
        with self.assertRaises(shopify.ShopifyError):
            shopify.sync_connection(connection_id, 2)
        pages = []
        fail_second_page = True

        def fake_graphql(_connection_id, _organization_id, query, variables):
            nonlocal fail_second_page
            pages.append(("products" if "query Products" in query else "orders", variables["cursor"]))
            if "query Products" in query and variables["cursor"] is None:
                return product_page([product("p1", "v1", "PAGE-1")], True, "next-page")
            if "query Products" in query:
                if fail_second_page:
                    fail_second_page = False
                    raise shopify.ShopifyRequestError("temporary failure")
                return product_page([product("p2", "v2", "PAGE-2")])
            return {
                "shop": {"currencyCode": "USD"},
                "orders": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}},
            }

        with patch("backend.shopify.graphql", side_effect=fake_graphql):
            with self.assertRaises(shopify.ShopifyRequestError):
                shopify.sync_connection(connection_id, 1)
            saved = shopify.connection_for_organization(1)
            self.assertEqual(saved["sync_resource"], "products")
            self.assertEqual(saved["sync_cursor"], "next-page")
            result = shopify.sync_connection(connection_id, 1)
        self.assertEqual(pages, [
            ("products", None), ("products", "next-page"),
            ("products", "next-page"), ("orders", None),
        ])
        self.assertEqual(result["status"], "connected")
        self.assertIsNone(result["sync_cursor"])
        self.assertEqual(result["records_synced"], 2)

    def test_rate_limit_retries_with_backoff_but_other_4xx_does_not(self):
        responses = [
            shopify.HttpResponse(429, {"Retry-After": "1"}, b"{}"),
            shopify.HttpResponse(200, {}, b"{}"),
        ]
        with patch("backend.shopify._transport", side_effect=responses) as transport, patch(
            "backend.shopify.random.uniform", return_value=0
        ), patch("backend.shopify.time.sleep") as sleep:
            response = shopify._request_with_retry("https://fixture", "GET", {}, None)
        self.assertEqual(response.status, 200)
        self.assertEqual(transport.call_count, 2)
        sleep.assert_called_once_with(1.0)

        with patch(
            "backend.shopify._transport", return_value=shopify.HttpResponse(400, {}, b"{}")
        ) as transport:
            with self.assertRaises(shopify.ShopifyRequestError):
                shopify._request_with_retry("https://fixture", "GET", {}, None)
        self.assertEqual(transport.call_count, 1)

    def test_shopify_product_reuses_matching_csv_sku(self):
        connection_id = self._connection()
        conn = database.get_connection()
        conn.execute(
            """INSERT INTO products
               (user_id, organization_id, sku, name, unit_cost_minor,
                selling_price_minor, currency, source, external_id)
               VALUES (?, 1, 'SAME-1', 'CSV item', 100, 200, 'USD', 'csv_import', 'csv-1')""",
            (self.user_id,),
        )
        conn.commit()
        conn.close()

        def fake_graphql(_connection_id, _organization_id, query, variables):
            if "query Products" in query:
                return product_page([product("p3", "v3", "SAME-1")])
            return {
                "shop": {"currencyCode": "USD"},
                "orders": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}},
            }

        with patch("backend.shopify.graphql", side_effect=fake_graphql):
            shopify.sync_connection(connection_id, 1)
        conn = database.get_connection()
        rows = conn.execute("SELECT * FROM products WHERE organization_id=1 AND sku='SAME-1'").fetchall()
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "shopify")
        self.assertEqual(rows[0]["external_id"], "v3")

    def test_expiring_token_is_refreshed_and_rotated_at_rest(self):
        connection_id = self._connection()
        conn = database.get_connection()
        conn.execute(
            "UPDATE shopify_connections SET token_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
            (connection_id,),
        )
        conn.commit()
        row = dict(conn.execute("SELECT * FROM shopify_connections WHERE id=?", (connection_id,)).fetchone())
        conn.close()
        with patch("backend.shopify._token_request", return_value={
            "access_token": "rotated-access", "refresh_token": "rotated-refresh",
            "expires_in": 3600, "refresh_token_expires_in": 7776000,
        }) as request:
            token = shopify._access_token(row)
        self.assertEqual(token, "rotated-access")
        self.assertEqual(request.call_args.args[1]["grant_type"], "refresh_token")
        conn = database.get_connection()
        updated = conn.execute("SELECT * FROM shopify_connections WHERE id=?", (connection_id,)).fetchone()
        conn.close()
        self.assertEqual(shopify.decrypt_secret(updated["refresh_token_encrypted"]), "rotated-refresh")


if __name__ == "__main__":
    unittest.main()
