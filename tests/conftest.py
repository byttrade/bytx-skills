"""Offline harness: public data from a real-candle fixture, orders on a bytx.testing paper account. No network."""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from bytx.types import Candle, FundingPayment, FundingRate, OpenInterest, Ticker

from bytx_trader import core

FIX = Path(__file__).parent / 'fixtures'
NOW = 1795006800000          # 2026-11-18 13:00 UTC
SYM = 'BTC/USDT:USDT'


def load_bars() -> list[list]:
    with gzip.open(FIX / 'binance-BTCUSDT-5m-2026-10-19_2026-11-18.json.gz', 'rt') as f:
        return json.load(f)['bars']


class FakeData:
    """Just the public-data calls the steps use, served from the fixture as of NOW."""

    def __init__(self, bars: list[list], now: int = NOW):
        self.bars = [b for b in bars if b[0] + 300_000 <= now]
        last = self.bars[-1][4]
        self.history = SimpleNamespace(candles=self._history)
        self._ticker = Ticker(SYM, last - 0.1, last + 0.1, last, quote_volume=1e10, change_pct=-0.5)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def market(self, symbol):
        return SimpleNamespace(symbol=SYM)

    async def tickers(self):
        return {SYM: self._ticker}

    async def ticker(self, symbol):
        return self._ticker if ':' in symbol else Ticker('BTC/USDT', None, None, self._ticker.last * 1.0005)

    async def candles(self, symbol, timeframe, since=None, limit=None):
        rows = [b for b in self.bars if since is None or b[0] >= since]
        return [Candle(*b) for b in rows[:limit]]

    async def _history(self, symbol, timeframe, since, until):
        return SimpleNamespace(rows=[Candle(*b) for b in self.bars if since <= b[0] < until])

    async def funding_rate(self, symbol):
        return FundingRate(SYM, 0.0006, NOW + 3_600_000, None, None, NOW)

    async def funding_history(self, symbol, since=None):
        return [FundingPayment(SYM, 0.0001, NOW - i * 28_800_000) for i in range(21)]

    async def open_interest(self, symbol):
        return OpenInterest(SYM, 90_000.0, None, NOW)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """crypto/ in tmp, clock pinned, exchanges replaced. Returns a namespace to tweak the paper account."""
    from bytx.testing import paper_exchange
    bars = load_bars()
    last = [b for b in bars if b[0] + 300_000 <= NOW][-1][4]
    monkeypatch.setenv('BYTX_TRADER_HOME', str(tmp_path / 'crypto'))
    monkeypatch.setenv('BYTX_TRADER_NOW_MS', str(NOW))
    ns = SimpleNamespace(price=last, sent=[])

    def factory(cfg, kind):
        if kind in ('data', 'spot'):
            return FakeData(bars)
        ex = paper_exchange('binance', bids=[(ns.price - 0.1, 50.0)], asks=[(ns.price + 0.1, 50.0)])
        orig = ex.open_position

        async def spy(*a, **kw):
            ns.sent.append(kw.get('client_id'))
            return await orig(*a, **kw)
        ex.open_position = spy
        return ex

    monkeypatch.setattr(core, 'open_exchange', factory)
    core.write('config.json', {'account_usdt': 5000})
    return ns
