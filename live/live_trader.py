"""Live daily trader for ConsensusAITrader MixLLM strategy.

Runs all 9 strategies daily (MixLLM needs 8 peers as sensors).
Persists state between runs. Generates dashboard JSON and pushes to GitHub Gist.

Usage:
    python live/live_trader.py                # Normal daily run
    python live/live_trader.py --morning      # Morning preview (after market open)
    python live/live_trader.py --force        # Force re-run even if already ran today
    python live/live_trader.py --dry-run      # Run but don't push to gist
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

import pandas as pd
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")
STATE_FILE = os.path.join(SCRIPT_DIR, "state.json")
DASHBOARD_FILE = os.path.join(SCRIPT_DIR, "dashboard.json")
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")


def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)


def setup_paths(config):
    """Add ConsensusAITrader paths to sys.path."""
    trader_root = config["trader_root"]
    eval_dir = os.path.join(trader_root, "eval")
    tools_dir = os.path.join(trader_root, "tools")
    for p in [eval_dir, tools_dir]:
        if p not in sys.path:
            sys.path.insert(0, p)


    # No is_trading_day() needed — the sim uses SPY price data as ground truth.
    # No hardcoded holiday list to maintain. If SPY has no bar, market was closed.
    # The script always runs. Weekends/holidays just collect news, 0 trading days.


def load_state():
    """Load saved strategy state, or None if no state exists."""
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state):
    """Atomically save strategy state."""
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, default=str)
    os.replace(tmp, STATE_FILE)


def collect_data(trader_root):
    """Collect today's news. Prices are handled by download_data() automatically.

    Price logic (in daily_loop.py download_data):
      - Cache exists and fresh? Use it, no download.
      - Cache exists but stale? Download ONLY the gap (a few days), merge, save.
      - No cache? Full download, save.
      - Never re-downloads data that's already cached.

    So we only need to collect news here. Prices are pulled on-demand by the sim.
    """
    print("Collecting today's data...")

    # News (checks what's missing, only collects gaps — works weekends too)
    try:
        result = subprocess.run(
            [sys.executable, os.path.join(trader_root, "tools", "daily_collect.py")],
            cwd=trader_root, timeout=120, capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"  News collection FAILED (exit {result.returncode}): {result.stderr[:200]}")
        else:
            print("  News collected.")
    except Exception as e:
        print(f"  News collection ERROR: {e}")

    print("  Prices: handled automatically by sim (cache-first, incremental).")


def run_trading_day(config, resume_state, start_date, end_date):
    """Run the simulation from start_date to end_date with optional state resume.

    Returns (results_dict, strategies_list).
    """
    from daily_loop import run_daily_simulation, save_strategies_state

    results, strategies = run_daily_simulation(
        start=start_date,
        end=end_date,
        initial_cash=config["starting_capital"],
        max_positions=config["max_positions"],
        period_name="Live",
        realistic=True,
        slippage=config.get("slippage", 0.0005),
        exec_model=config.get("exec_model", "premarket"),
        frequency=config.get("frequency", "biweekly"),
        resume_state=resume_state,
        live_mode=True,
        quiet=False,
    )

    # Save state checkpoint
    new_state = save_strategies_state(strategies, end_date)
    save_state(new_state)
    print(f"State saved. Last date: {end_date}")

    return results, strategies


def get_current_prices(tickers):
    """Fetch current/latest prices for position valuation."""
    import yfinance as yf
    prices = {}
    try:
        data = yf.download(tickers, period="1d", progress=False)
        if "Close" in data.columns:
            for ticker in tickers:
                try:
                    if isinstance(data["Close"], pd.Series):
                        prices[ticker] = float(data["Close"].iloc[-1])
                    else:
                        val = data["Close"][ticker].iloc[-1]
                        if pd.notna(val):
                            prices[ticker] = float(val)
                except (KeyError, IndexError):
                    pass
    except Exception:
        pass
    return prices


def generate_dashboard_json(strategies, config, end_date):
    """Generate the dashboard JSON matching the website schema.

    Extracts MixLLM strategy data but uses all 9 strategies internally.
    """
    # Find MixLLM strategy
    display_name = config.get("strategy_display", "MixLLM")
    target_strat = None
    for s in strategies:
        if s.name == display_name or s.name.startswith(display_name):
            target_strat = s
            break
    if target_strat is None:
        target_strat = strategies[-1]  # fallback to last (MixLLM)

    strat = target_strat
    initial = config["starting_capital"]

    # Compute current portfolio value
    total_value = strat.cash
    position_tickers = list(strat.positions.keys())

    # Get latest prices for positions
    current_prices = {}
    if position_tickers:
        current_prices = get_current_prices(position_tickers)

    # Build positions list with current prices
    positions = []
    invested = 0
    for ticker, pos in strat.positions.items():
        shares = pos["shares"]
        avg_cost = pos["entry_price"]
        # Use current price if available, otherwise last known
        current_price = current_prices.get(ticker, avg_cost)
        value = shares * current_price
        invested += value
        positions.append({
            "ticker": ticker,
            "shares": shares,
            "avg_cost": round(avg_cost, 2),
            "current_price": round(current_price, 2),
            "sector": _get_sector(ticker),
        })

    total_value = strat.cash + invested

    # Day P&L from history
    day_pnl = 0
    if len(strat.portfolio_history) >= 2:
        day_pnl = strat.portfolio_history[-1]["total_value"] - strat.portfolio_history[-2]["total_value"]
    elif strat.portfolio_history:
        day_pnl = strat.portfolio_history[-1]["total_value"] - initial

    total_return_pct = (total_value - initial) / initial * 100
    total_return_usd = total_value - initial

    # Allocation
    alloc_stocks = (invested / total_value * 100) if total_value > 0 else 0
    alloc_cash = (strat.cash / total_value * 100) if total_value > 0 else 100
    # Check for commodity/bond allocation (Mix/MixLLM tracks this)
    alloc_commodity = 0
    alloc_bonds = 0
    # Commodity tickers in positions
    commodity_tickers = {"USO", "XLE", "XOM", "CVX", "COP", "SLB", "OXY", "GLD"}
    for p in positions:
        if p["ticker"] in commodity_tickers:
            pct = (p["shares"] * p["current_price"]) / total_value * 100 if total_value > 0 else 0
            alloc_commodity += pct
    alloc_stocks -= alloc_commodity

    # Regime
    regime = "UNCERTAIN"
    if hasattr(strat, "detected_regime"):
        regime = strat.detected_regime

    # Trades (all transactions, formatted for dashboard)
    trades = []
    for tx in reversed(strat.transactions):
        action = tx.get("action", "BUY")
        if action == "TRIM":
            action = "SELL"
        trades.append({
            "date": tx.get("date", ""),
            "action": action,
            "ticker": tx.get("ticker", ""),
            "shares": tx.get("shares", 0),
            "price": round(tx.get("price", 0), 2),
            "reason": tx.get("reason", tx.get("note", "")),
        })

    # Equity curve from portfolio history
    equity_curve = []
    for h in strat.portfolio_history:
        equity_curve.append({
            "date": h.get("date", ""),
            "value": round(h.get("total_value", initial), 2),
        })

    # SPY benchmark curve (scaled to same starting capital)
    spy_curve = _build_spy_curve(config, end_date)

    # Build dashboard JSON
    now_pst = datetime.now()
    dashboard = {
        "last_updated": now_pst.strftime("%Y-%m-%dT%H:%M:%S-07:00"),
        "strategy": strat.name,
        "regime": regime,
        "account": {
            "starting_capital": initial,
            "total_value": round(total_value, 2),
            "cash": round(strat.cash, 2),
            "invested": round(invested, 2),
            "day_pnl": round(day_pnl, 2),
            "total_return_pct": round(total_return_pct, 2),
            "total_return_usd": round(total_return_usd, 2),
        },
        "allocation": {
            "stocks": round(alloc_stocks, 1),
            "cash": round(alloc_cash, 1),
            "bonds": round(alloc_bonds, 1),
            "gold": round(alloc_commodity, 1),
        },
        "positions": positions,
        "trades": trades,
        "equity_curve": equity_curve,
        "spy_curve": spy_curve,
    }

    # Save locally
    with open(DASHBOARD_FILE, "w") as f:
        json.dump(dashboard, f, indent=2)
    print(f"Dashboard JSON saved to {DASHBOARD_FILE}")

    return dashboard


def _get_sector(ticker):
    """Quick sector lookup for dashboard display."""
    sectors = {
        "AAPL": "Tech", "MSFT": "Tech", "GOOGL": "Tech", "AMZN": "Tech",
        "META": "Tech", "NVDA": "Semis", "TSLA": "Auto", "CRM": "Software",
        "NFLX": "Tech", "AMD": "Semis", "ADBE": "Software", "INTC": "Semis",
        "AVGO": "Semis", "QCOM": "Semis", "TXN": "Semis", "MU": "Semis",
        "LRCX": "Semis", "AMAT": "Semis", "KLAC": "Semis", "MRVL": "Semis", "ON": "Semis",
        "NOW": "Software", "PANW": "Cyber", "ZS": "Cyber", "CRWD": "Cyber", "DDOG": "Software",
        "SHOP": "E-comm", "UBER": "Tech", "ABNB": "Tech", "DASH": "Tech",
        "PYPL": "Fintech", "COIN": "Crypto", "PLTR": "Software",
        "JPM": "Finance", "V": "Finance", "MA": "Finance", "GS": "Finance",
        "BAC": "Finance", "WFC": "Finance", "MS": "Finance", "AXP": "Finance",
        "UNH": "Health", "JNJ": "Health", "LLY": "Pharma", "ABBV": "Pharma",
        "MRK": "Pharma", "PFE": "Pharma", "TMO": "Health", "AMGN": "Biotech",
        "REGN": "Biotech", "VRTX": "Biotech", "ABT": "Health", "ISRG": "Health", "MRNA": "Biotech",
        "PG": "Staples", "KO": "Staples", "PEP": "Staples", "COST": "Retail",
        "WMT": "Retail", "SBUX": "Staples",
        "HD": "Retail", "MCD": "Staples", "NKE": "Consumer", "LULU": "Consumer",
        "TGT": "Retail", "ROKU": "Tech", "SPOT": "Tech",
        "XOM": "Energy", "CVX": "Energy", "COP": "Energy", "SLB": "Energy", "OXY": "Energy",
        "CAT": "Industrial", "BA": "Aerospace", "HON": "Industrial", "UPS": "Industrial",
        "DE": "Industrial", "LMT": "Defense", "RTX": "Defense", "GE": "Industrial",
        "DIS": "Media", "CMCSA": "Media", "TMUS": "Telecom", "CHTR": "Telecom",
        "NEE": "Utility", "SO": "Utility",
        "AMT": "REIT", "PLD": "REIT", "D": "Utility",
        "BLK": "Finance", "FIS": "Fintech", "EMR": "Industrial", "MMM": "Industrial",
        "SPY": "ETF", "QQQ": "ETF",
        "USO": "Commodity", "XLE": "Energy", "GLD": "Gold",
    }
    return sectors.get(ticker, "Other")


def _build_spy_curve(config, end_date):
    """Build SPY equity curve scaled to starting capital.

    Uses local price cache first (reliable), falls back to yfinance.
    """
    start = config["start_date"]
    initial = config["starting_capital"]
    trader_root = config["trader_root"]

    # Try cached price data first
    cache_path = os.path.join(trader_root, "data", "prices", "SPY.csv")
    try:
        if os.path.exists(cache_path):
            spy = pd.read_csv(cache_path, index_col=0, parse_dates=True)
        else:
            import yfinance as yf
            spy = yf.download("SPY", start=start, end=end_date, progress=False)

        if spy.empty:
            return []
        # Handle multi-level columns
        if isinstance(spy.columns, pd.MultiIndex):
            spy.columns = spy.columns.get_level_values(0)

        # Filter to date range
        mask = (spy.index >= pd.Timestamp(start)) & (spy.index <= pd.Timestamp(end_date))
        spy = spy.loc[mask]
        if spy.empty:
            return []

        entry_price = float(spy["Open"].iloc[0]) if "Open" in spy.columns else float(spy["Close"].iloc[0])
        shares = initial / entry_price
        curve = []
        for idx, row in spy.iterrows():
            date_str = idx.strftime("%Y-%m-%d")
            value = round(shares * float(row["Close"]), 2)
            curve.append({"date": date_str, "value": value})
        return curve
    except Exception:
        return []


def push_to_gist(config, dashboard_file=DASHBOARD_FILE):
    """Push dashboard JSON to GitHub Gist."""
    gist_id = config.get("gist_id", "")
    if not gist_id:
        print("No gist_id configured. Skipping push.")
        return False

    try:
        result = subprocess.run(
            ["gh", "gist", "edit", gist_id, "-f", "live_portfolio.json", dashboard_file],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            print(f"Dashboard pushed to gist {gist_id}")
            return True
        else:
            print(f"Gist push failed: {result.stderr}")
            return False
    except Exception as e:
        print(f"Gist push error: {e}")
        return False


def log_run(message, log_dir=LOG_DIR):
    """Append to today's log file."""
    os.makedirs(log_dir, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = os.path.join(log_dir, f"{today}.log")
    timestamp = datetime.now().strftime("%H:%M:%S")
    with open(log_file, "a") as f:
        f.write(f"[{timestamp}] {message}\n")


def main():
    parser = argparse.ArgumentParser(description="Live daily trader for MixLLM")
    parser.add_argument("--morning", action="store_true",
                        help="Morning run: signals + trades only (no news collection)")
    parser.add_argument("--force", action="store_true",
                        help="Force re-run even if already ran today")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run but don't push to gist")
    parser.add_argument("--date", type=str, default=None,
                        help="Override: run for a specific date (YYYY-MM-DD)")
    parser.add_argument("--no-collect", action="store_true",
                        help="Skip data collection (prices/news already fresh)")
    args = parser.parse_args()

    config = load_config()
    setup_paths(config)
    trader_root = config["trader_root"]

    today = args.date or datetime.now().strftime("%Y-%m-%d")
    start_date = config["start_date"]

    print("=" * 60)
    print(f"LIVE TRADER — {config.get('strategy_display', 'MixLLM')}")
    print(f"Date: {today} | Start: {start_date} | Capital: ${config['starting_capital']:,.0f}")
    print("=" * 60)

    # Load existing state
    state = load_state()
    if state:
        last_date = state.get("last_date", "")
        print(f"Resuming from: {last_date}")

        # Check if already ran today
        if last_date >= today and not args.force:
            print(f"Already ran for {last_date}. Use --force to re-run.")
            log_run(f"Skipped: already ran for {last_date}")
            return

        # Determine date range: day after last run to today
        sim_start = (pd.Timestamp(last_date) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        print("Fresh start — no previous state found.")
        sim_start = start_date
        state = None

    # Always collect news — news happens 7 days a week (weekends, holidays too)
    # Prices are handled automatically by the sim (cache-first, incremental)
    if not args.no_collect and not args.morning:
        collect_data(trader_root)

    # Run simulation for the new days
    # The sim uses SPY price data as ground truth for trading days.
    # No hardcoded holiday list — if SPY has no bar, market was closed.
    # Weekends/holidays: 0 new trading days processed, but news is captured.
    # Monday after holiday weekend: processes all missed trading days at once.
    # e.g., run Sunday → Mon holiday → run Tuesday: processes Tuesday with all weekend news.
    print(f"\nProcessing: {sim_start} to {today}")
    log_run(f"Running: {sim_start} to {today}")

    results, strategies = run_trading_day(config, state, sim_start, today)

    # Generate dashboard JSON
    print("\nGenerating dashboard...")
    dashboard = generate_dashboard_json(strategies, config, today)

    # Show summary
    mixllm = None
    for s in strategies:
        if s.name.startswith("MixLLM") or s.name == "MixLLM":
            mixllm = s
            break
    if mixllm:
        ret = (mixllm.portfolio_history[-1]["total_value"] - config["starting_capital"]) / config["starting_capital"] * 100 if mixllm.portfolio_history else 0
        print(f"\n{'=' * 60}")
        print(f"MixLLM: ${mixllm.portfolio_history[-1]['total_value']:,.0f} ({ret:+.2f}%)" if mixllm.portfolio_history else "MixLLM: Initializing...")
        print(f"Positions: {len(mixllm.positions)} | Cash: ${mixllm.cash:,.0f}")
        if hasattr(mixllm, "detected_regime"):
            print(f"Regime: {mixllm.detected_regime}")
        # Show today's trades
        today_trades = [t for t in mixllm.transactions if t.get("date") == today]
        if today_trades:
            print(f"Today's trades ({len(today_trades)}):")
            for t in today_trades:
                print(f"  {t['action']} {t.get('shares', '')} {t.get('ticker', '')} @ ${t.get('price', 0):.2f}")
        else:
            print("No trades today.")
        print(f"{'=' * 60}")

    # Push to gist
    if not args.dry_run:
        push_to_gist(config)
    else:
        print("Dry run — skipping gist push.")

    log_run(f"Complete. MixLLM value: ${dashboard['account']['total_value']:,.0f}")
    print("\nDone.")


if __name__ == "__main__":
    main()
