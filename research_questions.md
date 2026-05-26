# Research Questions for External AI

These are standalone research questions. No project context needed — just find the answers with sources.

---

## Question 1: CF Benchmarks Real-Time Index Methodology

How does the CF Benchmarks Real-Time Index (BRTI for Bitcoin, ETRI for Ethereum) calculate its value? Specifically:

- Which exchanges are constituent data sources? (e.g., Coinbase, Kraken, Bitstamp, Gemini — which ones exactly?)
- How are they weighted? (equal weight, volume-weighted, something else?)
- What is the "trimmed average" methodology? The index reportedly uses per-second observations with the top and bottom 20% trimmed — does this mean 20% of exchanges are dropped, or 20% of price observations within a time window?
- How frequently does the index update? (every second? every tick?)
- Is there a public methodology document? If so, provide the URL.
- How much does CF BRTI typically diverge from Coinbase BTC spot price intraday? Any published data on this basis?

## Question 2: Cross-Exchange Crypto Price Basis

What is the typical intraday price difference (basis) between major crypto exchanges for BTC and ETH?

- Specifically between Coinbase, Kraken, and Bitstamp — how many basis points do they typically diverge during normal trading?
- Does the basis widen during volatile periods? By how much?
- Are there published academic papers or empirical studies measuring real-time cross-exchange crypto basis at the sub-minute level?
- Has anyone measured the basis between Coinbase spot and the CF Benchmarks Real-Time Index specifically?

## Question 3: Bartlett & O'Hara 2026 Prediction Markets Paper

There is a 2026 academic paper by Bartlett & O'Hara from Stanford/Cornell (SSRN paper #6615739) analyzing Kalshi market microstructure using 41.6 million trades across 478,167 markets. Find and verify the following claims attributed to this paper:

- Do market makers on Kalshi earn an average of 1.91 cents per contract on single-name markets?
- Is the maker win rate 63.4% on single-name markets?
- Do traders buy YES 60.9% of the time on single-name markets, despite YES settling only 32.5% of the time?
- What is Kyle's lambda estimate for Kalshi crypto markets vs broad-based markets?
- Does the paper specifically analyze 15-minute crypto contracts (KXBTC15M series)?
- Provide the full citation and any publicly accessible URL (SSRN, Stanford Law, etc.)

## Question 4: Kalshi Technical Infrastructure

Where is Kalshi's matching engine and API infrastructure hosted?

- What cloud provider does Kalshi use? (AWS, GCP, Azure, bare metal?)
- What AWS region, if applicable? (us-east-1 Virginia, us-east-2 Ohio, etc.)
- What is the typical REST API round-trip latency from AWS us-east-1 to Kalshi's trade API?
- Does Kalshi support order placement via WebSocket, or only via REST API?
- Is there any public documentation on Kalshi's infrastructure, tech blog posts, or job postings that reveal their stack?

## Question 5: Polymarket 5-Minute Crypto Contracts

Polymarket reportedly offers 5-minute crypto binary contracts. Research:

- Do these contracts actually exist as of May 2026? What are the exact market names/tickers?
- What is the settlement source? Is it CF Benchmarks (same as Kalshi) or something different?
- What is Polymarket's fee structure for crypto contracts? (maker fee, taker fee, any rebates?)
- What is the API for accessing Polymarket's orderbook and placing trades? Is there a WebSocket feed?
- What is the daily trading volume on Polymarket's short-duration crypto markets?
- Are there published studies or blog posts comparing Polymarket and Kalshi crypto market microstructure?

## Question 6: Latency Optimization for Crypto Trading APIs

For a trading bot connecting to REST and WebSocket APIs of crypto prediction markets (like Kalshi) from a cloud VPS:

- What is the typical latency difference between DigitalOcean NYC1 and AWS us-east-1 for reaching services also hosted in AWS us-east-1?
- Does using HTTP/2 or HTTP/3 for REST API calls measurably reduce latency compared to HTTP/1.1 for repeated small JSON requests?
- What are best practices for minimizing order submission latency in Python asyncio applications? (connection pooling, pre-warming, keep-alive, etc.)
- Is there a measurable latency advantage to submitting orders via WebSocket (persistent connection) vs REST (new TCP handshake or keep-alive)?
