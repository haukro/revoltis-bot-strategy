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
         patch("app.main._fetch_okx_execution_book", new=AsyncMock(return_value=book)), \
         patch.dict("os.environ", {"PAPER_OUTBOX_LEASE_SECONDS": "30"}):
        asyncio.run(internal_paper_outbox_tick("test-token"))

    assert "paper_spec006_bind_execution_attempt" in rpc_names
    assert "paper_spec006_apply_fill" in rpc_names
    assert "paper_apply_fill" not in rpc_names
    assert "paper_bind_execution_attempt" not in rpc_names



def test_spec006_risk_order_wait_releases_outbox_for_retry():
    signal_id = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
    outbox_id = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
    reservation_id = "cccccccc-3333-4333-8333-cccccccccccc"
    policy_id = "dddddddd-4444-4444-8444-dddddddddddd"

    claimed = [{
        "id": outbox_id,
        "lease_generation": 7,
        "event_type": "RISK_EVALUATE",
        "entity_id": signal_id,
        "payload": {},
    }]
    signal = {
        "id": signal_id,
        "strategy_version_id": "TEST-SPEC-002",
        "pair": "ZEC/USDT",
        "side": "LONG",
    }
    lifecycle = {
        "entry_action_id": "eeeeeeee-5555-4555-8555-eeeeeeeeeeee",
        "paper_signal_id": signal_id,
        "status": "PENDING_DISPATCH",
    }
    reservation = {
        "id": reservation_id,
        "signal_id": signal_id,
        "policy_version_id": policy_id,
        "status": "RESERVED",
    }

    async def fake_get(table, query=""):
        if table == "paper_signals":
            return [signal]
        if table == "paper_entry_lifecycles":
            return [lifecycle]
        if table == "risk_reservations":
            return [reservation]
        return []

    rpc_names = []

    async def fake_rpc(name, payload):
        rpc_names.append(name)
        if name == "paper_claim_outbox":
            return claimed
        if name == "paper_record_worker_heartbeat":
            return {"ok": True}
        if name == "paper_reserve_risk":
            return {"decision": "APPROVED"}
        if name == "paper_spec006_create_entry_order_from_approved_signal":
            return {"created": False, "reason": "WAIT_OWNER_ENTRY_IN_FLIGHT"}
        raise AssertionError(f"unexpected RPC: {name}")

    ack_calls = []

    async def fake_ack(outbox_id_arg, worker, generation, error=None):
        ack_calls.append((outbox_id_arg, generation, error))
        return {"acknowledged": error is None}

    with patch("app.main.require_durable_production_store"), \
         patch("app.main._require_internal_secret"), \
         patch("app.main._paper_active_ops_policy", new=AsyncMock(return_value={"max_worker_batch": 10, "market_book_depth": 20})), \
         patch("app.main._paper_runtime_binding", new=AsyncMock(return_value={"enabled": True, "risk_policy_version_id": policy_id})), \
         patch("app.main.supabase_get", new=AsyncMock(side_effect=fake_get)), \
         patch("app.main.supabase_rpc", new=AsyncMock(side_effect=fake_rpc)), \
         patch("app.main._paper_ack", new=AsyncMock(side_effect=fake_ack)), \
         patch.dict("os.environ", {"PAPER_OUTBOX_LEASE_SECONDS": "30"}):
        result = asyncio.run(internal_paper_outbox_tick("test-token"))

    assert "paper_spec006_create_entry_order_from_approved_signal" in rpc_names
    assert ack_calls == [(outbox_id, 7, "SPEC006_ENTRY_ORDER_WAIT")]
    assert result["processed"] == 0
    assert result["errors"] == 1



def test_spec006_exit_worker_uses_locked_base_qty_payload_and_disabled_binding_policy():
    order_id = "10000000-0000-4000-8000-000000000001"
    signal_id = "20000000-0000-4000-8000-000000000002"
    outbox_id = "30000000-0000-4000-8000-000000000003"
    snapshot_id = "40000000-0000-4000-8000-000000000004"
    attempt_id = "50000000-0000-4000-8000-000000000005"
    policy_id = "60000000-0000-4000-8000-000000000006"
    exit_intent_id = "70000000-0000-4000-8000-000000000007"

    claimed = [{
        "id": outbox_id,
        "lease_generation": 3,
        "event_type": "BIND_FILL_ATTEMPT",
        "entity_id": order_id,
        "payload": {
            "attempt_seq": 2,
            "spec006_exit_intent_id": exit_intent_id,
            "remaining_base_qty": "0.5",
        },
    }]
    order = {
        "id": order_id,
        "signal_id": signal_id,
        "strategy_version_id": "TEST-SPEC-002",
        "pair": "ZEC/USDT",
        "side": "SELL",
        "intent_type": "EXIT",
        "intended_notional": "50",
        "filled_notional": "0",
        "exit_intent_id": exit_intent_id,
    }
    policy = {"id": policy_id, "fee_rate": "0.001", "partial_fill_allowed": False}

    async def fake_get(table, query=""):
        if table == "paper_orders":
            return [order]
        if table == "paper_entry_lifecycles":
            return []
        if table == "risk_policy_versions":
            return [policy]
        if table == "paper_fills":
            return []
        if table == "paper_positions":
            raise AssertionError("EXIT sizing must come from locked outbox payload")
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
            assert payload["p_fill_quantity"] == "0.5"
            return {"applied": True}
        if name == "paper_ack_outbox":
            return {"acknowledged": True}
        raise AssertionError(f"unexpected RPC: {name}")

    book = {
        "provider": "okx",
        "pair": "ZEC/USDT",
        "provider_ts_ms": 1_700_000_000_000,
        "received_at": "2026-09-25T08:00:00+00:00",
        "bids": [["100", "1"]],
        "asks": [["101", "1"]],
        "snapshot_hash": "b" * 64,
    }

    with patch("app.main.require_durable_production_store"), \
         patch("app.main._require_internal_secret"), \
         patch("app.main._paper_active_ops_policy", new=AsyncMock(return_value={"max_worker_batch": 10, "market_book_depth": 20})), \
         patch("app.main._paper_runtime_binding", new=AsyncMock(return_value={"enabled": False, "risk_policy_version_id": policy_id})), \
         patch("app.main.supabase_get", new=AsyncMock(side_effect=fake_get)), \
         patch("app.main.supabase_rpc", new=AsyncMock(side_effect=fake_rpc)), \
         patch("app.main._fetch_okx_execution_book", new=AsyncMock(return_value=book)), \
         patch.dict("os.environ", {"PAPER_OUTBOX_LEASE_SECONDS": "30"}):
        result = asyncio.run(internal_paper_outbox_tick("test-token"))

    assert result["errors"] == 0
    assert "paper_spec006_bind_execution_attempt" in rpc_names
    assert "paper_spec006_apply_fill" in rpc_names
    assert "paper_apply_fill" not in rpc_names


def test_disabled_spec006_strategy_entry_is_fenced_and_acked_not_retried():
    action_id = "90000000-0000-4000-8000-000000000009"
    outbox_id = "91000000-0000-4000-8000-000000000010"
    claimed = [{
        "id": outbox_id,
        "lease_generation": 4,
        "event_type": "STRATEGY_ACTION_DISPATCH",
        "entity_id": action_id,
        "payload": {},
    }]
    action = {
        "id": action_id,
        "strategy_version_id": "TEST-SPEC-002",
        "pair": "ZEC/USDT",
        "action_type": "ENTRY",
    }

    async def fake_get(table, query=""):
        if table == "paper_strategy_actions":
            return [action]
        return []

    rpc_names = []
    ack_errors = []

    async def fake_rpc(name, payload):
        rpc_names.append(name)
        if name == "paper_claim_outbox":
            return claimed
        if name == "paper_record_worker_heartbeat":
            return {"ok": True}
        if name == "paper_spec006_fence_entry_lifecycle":
            return True
        if name == "paper_ack_outbox":
            ack_errors.append(payload.get("p_error"))
            return {"acknowledged": True}
        raise AssertionError(f"unexpected RPC: {name}")

    with patch("app.main.require_durable_production_store"), \
         patch("app.main._require_internal_secret"), \
         patch("app.main._paper_active_ops_policy", new=AsyncMock(return_value={"max_worker_batch": 10, "market_book_depth": 20})), \
         patch("app.main._paper_runtime_binding", new=AsyncMock(return_value={"enabled": False})), \
         patch("app.main.supabase_get", new=AsyncMock(side_effect=fake_get)), \
         patch("app.main.supabase_rpc", new=AsyncMock(side_effect=fake_rpc)), \
         patch.dict("os.environ", {"PAPER_OUTBOX_LEASE_SECONDS": "30"}):
        result = asyncio.run(internal_paper_outbox_tick("test-token"))

    assert result["processed"] == 1
    assert result["errors"] == 0
    assert "paper_spec006_fence_entry_lifecycle" in rpc_names
    assert "paper_spec006_dispatch_entry_action" not in rpc_names
    assert ack_errors == [None]
