"""Paper-specific validation must happen before exchange or persistence I/O."""
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.mark.parametrize("label", ["official", "scale_300_150"])
@pytest.mark.parametrize("use_costs", [False, True])
def test_paper_forced_close_is_rejected_before_exchange_access(label, use_costs):
    with patch.object(main, "load_okx_candles", new=AsyncMock(side_effect=httpx.RequestError("exchange offline"))) as candles, \
         patch.object(main, "append_paper_ledger", new=AsyncMock()) as ledger, \
         patch.object(main, "supabase_upsert", new=AsyncMock()) as save, \
         TestClient(main.app) as client:
        response = client.post("/api/simulations/run", json={
            "settings": {"selected_pairs": ["HYPE/USDT"]},
            "paper_label": label, "force_close_at_end": True,
            "use_current_cost_model": use_costs,
        })
    assert response.status_code == 422
    candles.assert_not_awaited()
    ledger.assert_not_awaited()
    save.assert_not_awaited()


def test_historical_backtest_can_still_force_close_without_a_paper_label():
    result = {"trades": [], "metrics": {}, "equity_curve": [],
              "per_pair_metrics": {}, "per_pair_equity_curves": {}, "rejections": {}}
    with patch.object(main, "load_okx_candles", new=AsyncMock(return_value=[])), \
         patch.object(main, "simulate", return_value=result) as simulate, \
         patch.object(main, "append_paper_ledger", new=AsyncMock()) as ledger, \
         patch.object(main, "supabase_upsert", new=AsyncMock()) as save, \
         TestClient(main.app) as client:
        response = client.post("/api/simulations/run", json={
            "settings": {"selected_pairs": ["HYPE/USDT"]}, "force_close_at_end": True,
        })
    assert response.status_code == 200
    assert simulate.call_args.kwargs["force_close_at_end"] is True
    ledger.assert_not_awaited()
    save.assert_awaited_once()
