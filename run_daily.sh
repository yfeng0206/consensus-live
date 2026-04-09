#!/bin/bash
# ConsensusAITrader - Daily Live Trading Script
# Run after market close (~2:00 PM PST / 5:00 PM ET)
#
# Usage:
#   ./run_daily.sh              Normal daily run
#   ./run_daily.sh --dry-run    Run without pushing to gist
#   ./run_daily.sh --force      Force re-run even if already ran today

set -e
cd "$(dirname "$0")"

echo "============================================================"
echo " ConsensusAITrader - Daily Live Run"
echo " $(date)"
echo "============================================================"

python live/live_trader.py "$@"

echo ""
echo "Done. Dashboard updated."
