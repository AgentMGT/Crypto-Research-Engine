# Crypto research engine

A static research site that rebuilds itself three times a day, in two editions:

- **Crypto & broader markets** (`index.html`) — top ~250 coins screened on relative
  strength, RSI, position in the 7-day range and turnover; global aggregates,
  Fear & Greed, DeFi TVL and stablecoin supply, plus a macro dashboard
  (S&P, Nasdaq, VIX, DXY, 10Y, gold, oil, COIN, MSTR, IBIT).
- **Bittensor (TAO) & subnets** (`bittensor.html`) — every subnet's alpha price,
  premium or discount to the chain's own moving price, pool liquidity, emission
  share, annualised emission against alpha market cap, and net TAO flow — all read
  straight from the Finney chain.

Each run also writes a dated snapshot to `data/history/`, which is what gives the
pages their "since last refresh", 1-day and 7-day columns. Thirty days are kept.

## How it runs

```
.github/workflows/refresh.yml   06:00, 14:00, 22:00 UTC + manual + on push
  └─ python -m engine.build all
       ├─ engine/fetch_crypto.py   CoinGecko, alternative.me, DefiLlama, Yahoo Finance
       ├─ engine/fetch_tao.py      Bittensor SDK against Finney, CoinGecko for TAO/USD
       ├─ engine/signals.py        rule-based scores, tags and idea lists
       ├─ engine/ai_brief.py       optional Claude commentary over this run's figures
       └─ templates/*.html         rendered into site/
  └─ commits data/, deploys site/ to GitHub Pages
```

Every data source is free and needs no account. Two optional secrets:

| Secret | Effect if missing |
| --- | --- |
| `ANTHROPIC_API_KEY` | No AI commentary; the rule-based signals still render. |
| `COINGECKO_API_KEY` | CoinGecko runs on the public rate limit instead of the free demo tier. |

A source that fails leaves its section empty and is listed in a banner at the top
of the page, so one bad API never breaks the build.

## Coin research

`research.html` (static/research.js) lets you search any coin. It loads the profile,
supply, venues and on-chain pools live from CoinGecko and GeckoTerminal in the browser,
adds this engine's latest signals and headlines from `crypto-latest.json`, and runs a
set of mechanical checks (dilution, liquidity, drawdown, momentum, venue concentration,
funding). Deep link with `research.html?id=<coingecko id>` or `?q=<ticker>`.

## News and the CLARITY Act tracker

`engine/fetch_news.py` pulls the last 7 days of headlines from CoinDesk, Cointelegraph,
The Block, Decrypt, Blockworks and Google News RSS searches (CLARITY Act, market
structure, SEC/CFTC, Bittensor). Headlines are de-duplicated, tagged by topic and by
the coins they mention, and fed to the AI brief as context.

The CLARITY Act panel combines live headlines and GovTrack's official bill status with
the hand-kept milestone list in `data/clarity.json`. Edit that file when the bill moves
(new vote, markup, signing) and update `status_label`, `status_note` and `status_as_of`.

## Running it locally

```bash
pip install -r requirements.txt
python -m engine.build all      # live data; needs outbound network
python tools/demo_build.py      # synthetic data, no network, for layout work
open site/index.html
```

## Signals

Scores are mechanical screens, deliberately simple and readable in
`engine/signals.py`:

- **Crypto** — relative strength against BTC over 7 and 30 days, position in the
  7-day range, volume as a share of market cap, with a penalty above RSI 80.
  Stablecoins and wrapped assets are excluded.
- **Subnets** — premium to the chain's moving price, EMA net TAO flow, annualised
  emission against alpha market cap, 7-day price change and a liquidity floor.

They are a reading list, not trade instructions, and nothing in this repository
places an order.
