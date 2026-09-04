#!/usr/bin/env bash
# Run all scenario scripts sequentially against fresh DBs.
# Exits 0 if every scenario passes, 1 otherwise.
#
# Usage: bash tests/scenarios/run_all.sh
set -u

SCRIPTS=(
  scenario_01_signal_transitions.py
  scenario_02_dedup_window.py
  scenario_03_quiet_hours.py
  scenario_04_per_book_mute.py
  scenario_05_alert_kind_toggle.py
  scenario_06_ui_surface.py
  scenario_07_product_lifecycle.py
  scenario_08_prime_toggle.py
)

FAILURES=()
for s in "${SCRIPTS[@]}"; do
  if ! uv run python "tests/scenarios/$s"; then
    FAILURES+=("$s")
  fi
done

echo
echo "===================================================================="
if [ ${#FAILURES[@]} -eq 0 ]; then
  echo "ALL SCENARIOS PASS (${#SCRIPTS[@]}/${#SCRIPTS[@]})"
  exit 0
else
  echo "FAILURES (${#FAILURES[@]}/${#SCRIPTS[@]}):"
  for f in "${FAILURES[@]}"; do
    echo "  - $f"
  done
  exit 1
fi
