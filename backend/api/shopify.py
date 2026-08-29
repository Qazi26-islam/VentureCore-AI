from __future__ import annotations

import secrets
import threading

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from backend import shopify
from backend.db import DEFAULT_ORGANIZATION_ID


router = APIRouter(prefix="/integrations/shopify", tags=["shopify"])


def _identity(request: Request) -> tuple[int, int]:
    if request.session.get("demo_mode"):
        raise HTTPException(status_code=403, detail="The sample workspace cannot connect external stores.")
    user_id = request.session.get("user_id")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Sign in before connecting Shopify.")
    return int(user_id), DEFAULT_ORGANIZATION_ID


def _start_initial_sync(connection_id: int, organization_id: int) -> None:
    def initial_sync() -> None:
        try:
            shopify.sync_connection(connection_id, organization_id)
        except Exception:
            # The connector records a plain-language stale-data status for the UI.
            return

    threading.Thread(target=initial_sync, daemon=True, name="shopify-initial-sync").start()


@router.get("/install")
def install(shop: str, request: Request):
    user_id, organization_id = _identity(request)
    state = secrets.token_urlsafe(32)
    request.session["shopify_oauth"] = {
        "state": state, "user_id": user_id, "organization_id": organization_id,
    }
    try:
        return RedirectResponse(shopify.oauth_install_url(shop, state), status_code=302)
    except shopify.ShopifyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/callback")
def callback(request: Request, shop: str, code: str, state: str, hmac: str):
    saved = request.session.pop("shopify_oauth", None)
    if saved is None or not secrets.compare_digest(str(saved.get("state", "")), state):
        raise HTTPException(status_code=403, detail="Shopify authorization state was invalid or expired.")
    params = {key: value for key, value in request.query_params.items()}
    try:
        connection_id = shopify.complete_oauth(
            int(saved["organization_id"]), int(saved["user_id"]), shop, code, params
        )
    except shopify.ShopifyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _start_initial_sync(connection_id, int(saved["organization_id"]))
    return RedirectResponse("/?shopify=connected", status_code=302)


@router.get("/status")
def status(request: Request):
    _, organization_id = _identity(request)
    return shopify.public_status(organization_id)


@router.post("/sync")
def sync_now(request: Request):
    _, organization_id = _identity(request)
    connection = shopify.connection_for_organization(organization_id)
    if connection is None or connection["status"] == "disconnected":
        raise HTTPException(status_code=404, detail="Connect Shopify before syncing.")
    try:
        result = shopify.sync_connection(
            int(connection["id"]), organization_id, incremental=False
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Your existing data is still available, but Shopify could not sync right now. Please try again later.",
        ) from exc
    return shopify.public_status(int(result["organization_id"]))


@router.post("/reconcile")
def reconcile(request: Request):
    _, organization_id = _identity(request)
    connection = shopify.connection_for_organization(organization_id)
    if connection is None or connection["status"] == "disconnected":
        raise HTTPException(status_code=404, detail="Connect Shopify before reconciling.")
    try:
        result = shopify.sync_connection(
            int(connection["id"]), organization_id, incremental=True
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="VentureCore is showing the last good Shopify data. Reconciliation will retry later.",
        ) from exc
    return shopify.public_status(int(result["organization_id"]))


@router.delete("")
def disconnect(request: Request):
    _, organization_id = _identity(request)
    shopify.disconnect(organization_id)
    return {"connected": False, "message": "Shopify has been disconnected."}


@router.post("/webhook")
async def webhook(request: Request):
    raw_body = await request.body()
    signature = request.headers.get("X-Shopify-Hmac-Sha256", "")
    if not shopify.verify_webhook(raw_body, signature):
        raise HTTPException(status_code=401, detail="Invalid Shopify webhook signature.")
    webhook_id = request.headers.get("X-Shopify-Webhook-Id", "")
    if not webhook_id:
        raise HTTPException(status_code=400, detail="Shopify webhook identifier is missing.")
    try:
        outcome = shopify.process_webhook(
            request.headers.get("X-Shopify-Shop-Domain", ""),
            webhook_id,
            request.headers.get("X-Shopify-Event-Id"),
            request.headers.get("X-Shopify-Topic", ""),
            raw_body,
        )
    except shopify.ShopifyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    status_code = 202 if outcome == "dead_letter" else 200
    return JSONResponse({"status": outcome}, status_code=status_code)
