"""Test multi-exchange price feeds: Coinbase vs Kraken vs Bitstamp.

Runs all three feeds for 60 seconds and reports:
- Connection success/failure
- Price comparison (basis between exchanges)
- Tick frequency per exchange
- Latency of each feed
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time

sys.path.insert(0, "src")

from kalshi_bot.client.coinbase import CoinbaseFeed
from kalshi_bot.client.kraken import KrakenFeed
from kalshi_bot.client.bitstamp import BitstampFeed
from kalshi_bot.models.price import PriceTick


async def main() -> None:
    duration = 60
    print(f"Starting multi-exchange feed test ({duration}s)...")
    print()

    # Coinbase uses a queue
    cb_queue: asyncio.Queue[PriceTick] = asyncio.Queue(maxsize=5000)
    coinbase = CoinbaseFeed(cb_queue)
    kraken = KrakenFeed()
    bitstamp = BitstampFeed()

    # Start all feeds
    cb_task = asyncio.create_task(coinbase.start())
    kr_task = asyncio.create_task(kraken.start())
    bs_task = asyncio.create_task(bitstamp.start())

    # Collect data
    cb_prices: dict[str, list[tuple[float, float]]] = {"BTC": [], "ETH": []}
    kr_prices: dict[str, list[tuple[float, float]]] = {"BTC": [], "ETH": []}
    bs_prices: dict[str, list[tuple[float, float]]] = {"BTC": [], "ETH": []}
    basis_samples: dict[str, list[tuple[float, float, float]]] = {"BTC": [], "ETH": []}

    start = time.monotonic()

    # Drain coinbase queue and sample kraken/bitstamp every 0.5s
    while time.monotonic() - start < duration:
        # Drain coinbase
        while not cb_queue.empty():
            tick = cb_queue.get_nowait()
            if tick.symbol in cb_prices:
                cb_prices[tick.symbol].append((time.monotonic(), tick.price))

        # Sample kraken and bitstamp
        for symbol in ["BTC", "ETH"]:
            kr = kraken.get_price(symbol)
            bs = bitstamp.get_price(symbol)
            if kr is not None:
                kr_prices[symbol].append((time.monotonic(), kr[0]))
            if bs is not None:
                bs_prices[symbol].append((time.monotonic(), bs[0]))

            # Compute basis if all three are available
            if cb_prices[symbol] and kr is not None and bs is not None:
                cb_last = cb_prices[symbol][-1][1]
                basis_samples[symbol].append((cb_last, kr[0], bs[0]))

        await asyncio.sleep(0.5)

    # Stop feeds
    await coinbase.stop()
    await kraken.stop()
    await bitstamp.stop()
    cb_task.cancel()
    kr_task.cancel()
    bs_task.cancel()

    # Report
    print("=" * 70)
    print("RESULTS")
    print("=" * 70)

    for symbol in ["BTC", "ETH"]:
        print(f"\n--- {symbol} ---")
        cb_n = len(cb_prices[symbol])
        kr_n = len(kr_prices[symbol])
        bs_n = len(bs_prices[symbol])
        print(f"  Ticks:  Coinbase={cb_n}  Kraken={kr_n}  Bitstamp={bs_n}")

        if cb_n > 0:
            print(f"  Coinbase last: ${cb_prices[symbol][-1][1]:,.2f}")
        if kr_n > 0:
            print(f"  Kraken last:   ${kr_prices[symbol][-1][1]:,.2f}")
        if bs_n > 0:
            print(f"  Bitstamp last: ${bs_prices[symbol][-1][1]:,.2f}")

        samples = basis_samples[symbol]
        if len(samples) >= 5:
            cb_kr_basis = [(s[0] - s[1]) / s[0] * 10000 for s in samples]
            cb_bs_basis = [(s[0] - s[2]) / s[0] * 10000 for s in samples]
            kr_bs_basis = [(s[1] - s[2]) / s[1] * 10000 for s in samples]

            print(f"  Basis (bps) Coinbase-Kraken:   mean={statistics.mean(cb_kr_basis):+.2f}  stdev={statistics.stdev(cb_kr_basis):.2f}  max={max(abs(x) for x in cb_kr_basis):.2f}")
            print(f"  Basis (bps) Coinbase-Bitstamp: mean={statistics.mean(cb_bs_basis):+.2f}  stdev={statistics.stdev(cb_bs_basis):.2f}  max={max(abs(x) for x in cb_bs_basis):.2f}")
            print(f"  Basis (bps) Kraken-Bitstamp:   mean={statistics.mean(kr_bs_basis):+.2f}  stdev={statistics.stdev(kr_bs_basis):.2f}  max={max(abs(x) for x in kr_bs_basis):.2f}")

            # Composite vs Coinbase
            composite_basis = []
            for cb, kr, bs in samples:
                composite = (cb + kr + bs) / 3
                composite_basis.append((cb - composite) / cb * 10000)
            print(f"  Coinbase-vs-Composite basis:    mean={statistics.mean(composite_basis):+.2f}  stdev={statistics.stdev(composite_basis):.2f} bps")
        else:
            print(f"  Not enough samples for basis ({len(samples)})")

    # Latency
    print(f"\n--- Feed Latency ---")
    cb_age = coinbase.last_tick_age_s
    kr_age = kraken.last_tick_age_s
    bs_age = bitstamp.last_tick_age_s
    print(f"  Coinbase last tick age: {cb_age:.2f}s" if cb_age else "  Coinbase: no ticks")
    print(f"  Kraken last tick age:   {kr_age:.2f}s" if kr_age else "  Kraken: no ticks")
    print(f"  Bitstamp last tick age: {bs_age:.2f}s" if bs_age else "  Bitstamp: no ticks")

    # Composite price computation cost
    print(f"\n--- Composite Compute Cost ---")
    prices = [107000.0, 107005.0, 106995.0]
    t0 = time.perf_counter_ns()
    for _ in range(100000):
        _ = sum(prices) / len(prices)
    elapsed_ns = time.perf_counter_ns() - t0
    print(f"  100K composite averages: {elapsed_ns/1e6:.1f}ms ({elapsed_ns/100000:.0f}ns each)")
    print(f"  Impact on latency: effectively zero")


if __name__ == "__main__":
    asyncio.run(main())
