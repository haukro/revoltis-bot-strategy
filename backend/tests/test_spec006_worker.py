import asyncio
from unittest.mock import AsyncMock, patch

from app.main import internal_paper_outbox_tick


def test_spec006_fill_worker_never_calls_legacy_apply():
    order_id = "11111111-1111-1111-1111-111111111111"
    signal_id = "22222222-2222-2222-2222-222222222222"
    outbox_id = "33333333-3333-3333-3333-333333333333"
    snapshot_id = "44444444-4444-4444-4444-444444444444"
    attempt_id = "55555555-5555-5555-5555-555555555555"
    reservation_id = "66666666-6666-6666-6666-666666666666"
    policy_id = "77777777-7777-7777-7777-777777777777"

    claimed = [{
        "id": outbox_id,
        "lease_generation": 1,
        "event_type": "BIND_FILL_ATTEMPT",
        "entity_id": order_id,
        "payload": {"attempt_seq": 1},
    }]
    order = {
        "id": order_id,
        "signal_id": signal_id,
        "strategy_version_id": "TEST-SPEC-002",
        "pair": "ZEC/USDT",
        "side": "BUY",
        "intent_type": "ENTRY",
        "intended_notional": "50",
        "filled_notional": "0",
        "exit_intent_id": None,
    }
    lifecycle = {
        "entry_action_id": "88888888-8888-8888-8888-888888888888",
        "paper_entry_order_id": order_id,
        "status": "ORDER_ACTIVE",
    }
    reservation = {
        "id": reservation_id,
        "signal_id": signal_id,
        "policy_version_id": policy_id,
        "status": "RESERVED",
    }
    policy = {"id": policy_id, "fee_rate": "0.001", "partial_fill_allowed": True}

    async def fake_get(table, query=""):
        if table == "paper_orders":
            return [order]
        if table == "paper_entry_lifecycles":
            return [lifecycle]
        if table == "risk_reservations":
            return [reservation]
        if table == "risk_policy_versions":
            return [policy]
        if table == "paper_fills":
            return []
        return []

    rpc_names = []

    async def fake_rpc(name, payload):
        rpc_names.append(name)
        if name == "paper_claim_outbox":
            return claimed
        if name == "paper_record_worker_heartbeat":
            return {"ok": True}
        if name == "paper_ingest_market_snapshot":
            return {"id": snapshot_id}
        if name == "paper_spec006_bind_execution_attempt":
            return {"execution_attempt_id": attempt_id, "market_snapshot_id": snapshot_id}
        if name == "paper_spec006_apply_fill":
            return {"applied": True}
        if name == "paper_ack_outbox":
            return {"acknowledged": True}
        raise AssertionError(f"unexpected RPC: {name}")

    book = {
        "provider": "okx",
        "pair": "ZEC/USDT",
        "provider_ts_ms": 1_700_000_000_000,
        "received_at": "2026-09-25T08:00:00+00:00",
        "bids": [["99", "10"]],
        "asks": [["100", "10"]],
        "snapshot_hash": "a" * 64,
    }

    with patch("app.main.require_durable_production_store"), \
         patch("app.main._require_internal_secret"), \
         patch("app.main._paper_active_ops_policy", new=AsyncMock(return_value={"max_worker_batch": 10, "market_book_depth": 20})), \
         patch("app.main.supabase_get", new=AsyncMock(side_effect=fake_get)), \
         patch("app.main.supabase_rpc", new=AsyncMock(side_effect=fake_rpc)), \
         patch("app.main._fetch_okx_execution_book", new=AsyncMock(return_value=book)):
        asyncio.run(internal_paper_outbox_tick("test-token"))

    assert "paper_spec006_bind_execution_attempt" in rpc_names
    assert "paper_spec006_apply_fill" in rpc_names
    assert "paper_apply_fill" not in rpc_names
    assert "paper_bind_execution_attempt" not in rpc_names
