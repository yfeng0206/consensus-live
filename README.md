# ConsensusAITrader — Live Trading Dashboard

Live MixLLM strategy running daily with $100k starting capital.

## Setup

1. Clone this repo
2. Edit `live/config.json` — set `trader_root` to your ConsensusAITrader directory
3. Create a GitHub Gist and set the `gist_id` in config
4. Run: `python live/live_trader.py --dry-run`

## Daily Usage

```bash
# After market close (~2:00 PM PST):
python live/live_trader.py

# Or use the wrapper:
./run_daily.bat     # Windows
./run_daily.sh      # Linux/Mac
```

## How It Works

- Runs all 9 ConsensusAITrader strategies (MixLLM needs 8 peers as sensors)
- Persists state in `live/state.json` between runs
- Generates dashboard JSON matching the website schema
- Pushes to GitHub Gist — website fetches and renders

## Timing

- Run after market close: **2:00 PM PST** (5:00 PM ET)
- Script processes any missed trading days since last run
- Skips weekends/holidays automatically
