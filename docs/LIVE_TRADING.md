# Live trading

The bot (`engine/bot.py`) trades two paper accounts on every refresh. `engine/live.py` mirrors each of its fills with a real order, inside hard limits. **Both accounts ship switched off.**

## The three locks

An account places real orders only when all three are open. Until then it runs as a dry run and logs the order it *would* have placed (Practice page, Bot tab, "Live trading").

1. `data/live.json`: `"armed": true` for that account. Only changed on Marvin's explicit command.
2. Repository variable `LIVE_TRADING` = `on` (Settings, Secrets and variables, Actions, Variables). This is the kill switch: delete it or set it to `off` and every account stops placing orders on the next run.
3. The account's secrets (below).

## Limits (data/live.json)

| | Crypto | Bittensor |
|---|---|---|
| Total budget the bot may deploy | `budget_usd` (500) | `budget_tao` (2) |
| Largest single order | `max_order_usd` (100) | `max_order_tao` (0.5) |
| Daily loss that pauses new buys | `max_daily_loss_usd` (50) | `max_daily_loss_tao` (0.3) |
| Other | 6 positions max; skips a buy if the exchange price is >2% from the signal; stop-loss order placed on the exchange | 20% of budget per subnet; 2% slippage protection on every stake and unstake; SDK policy caps each call's spend and fee |

New buys also need the AI reviewer's approval (`require_ai_approval`), which needs the `ANTHROPIC_API_KEY` secret. Sells and stops always go through. A paused account stays paused until cleared by hand.

## Crypto setup (Coinbase Advanced Trade, via ccxt)

1. In Coinbase, create an API key with **trade** permission only. Never enable transfer or withdraw.
2. Add repository secrets `LIVE_EXCHANGE_API_KEY` (the key name) and `LIVE_EXCHANGE_SECRET` (the private key). `LIVE_EXCHANGE_PASSWORD` is only for exchanges that use a passphrase.
3. Keep only the trading budget in USD or USDC on that account.

Another exchange: set `"exchange"` to its ccxt id (for example `kraken`).

## Bittensor setup (Staking proxy)

The bot never holds the coldkey. It signs with a separate **delegate** key that the coldkey authorises as a `Staking` proxy: it can stake and unstake, and it cannot transfer TAO anywhere.

1. Create a new key for the delegate and send it a little TAO for fees (about 0.05 τ).
2. From the coldkey, add the delegate as a proxy with type `Staking` (btcli, or the SDK's `AddProxy` intent).
3. Pick the validator hotkey to stake through and set `"validator_hotkey"` in `data/live.json`.
4. Add repository secrets `LIVE_TAO_PROXY_MNEMONIC` (the delegate's mnemonic, not the coldkey's) and `LIVE_TAO_COLDKEY_SS58` (the coldkey's public address).

To revoke, remove the proxy from the coldkey. The delegate stops working at once.

## Things to know

- Orders go out when the site refreshes (about three times a day, sometimes late). Crypto stops sit on the exchange between refreshes; Bittensor has no exchange-side stop, so its 30% stop is checked at each refresh.
- The repository is public, so `live.json` (budgets, positions, order log) is public on the site. No keys or secrets are ever written to it.
- If a run fails after placing an order but before saving state, the next run checks the exchange balance before buying the same coin again.
