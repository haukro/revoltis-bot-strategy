"""Known rollout blocker: distinct overlapping IDs need an atomic storage guard.

This store models the current primary-key-only INSERT ON CONFLICT behaviour.
This regression intentionally fails until the storage guard is implemented.
Replace this model with a real database concurrency regression with that guard.
"""
import asyncio

from app.paper_ledger import append_closed_trades
from test_paper_ledger import FakeStore, trade


def test_simultaneous_distinct_entries_cannot_freeze_overlapping_positions():
    async def exercise():
        class ConcurrentStore(FakeStore):
            def __init__(self):
                super().__init__()
                self.reads = 0
                self.both_read = asyncio.Event()

            async def read(self, label):
                snapshot = await super().read(label)
                self.reads += 1
                if self.reads == 2:
                    self.both_read.set()
                await self.both_read.wait()
                return snapshot

        store = ConcurrentStore()
        shifted = trade(-0.4)
        shifted["opened_at"] = "2026-09-26T21:04:59.999+00:00"
        await asyncio.wait_for(asyncio.gather(
            append_closed_trades("official", [trade(0.257)], "tab-a", store.read, store.insert),
            append_closed_trades("official", [shifted], "tab-b", store.read, store.insert),
        ), timeout=2)
        assert len(store.rows) == 1, "Both requests read an empty ledger and froze overlapping trades"

    asyncio.run(exercise())
