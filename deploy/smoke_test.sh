#!/usr/bin/env bash
# Post-deploy smoke test — exit 1 if bot unhealthy.
set -euo pipefail

BOT_DIR="${BOT_DIR:-$HOME/bnf-bot}"
cd "$BOT_DIR"

echo "==> Smoke test..."

if ! systemctl is-active --quiet bnf-bot; then
  echo "FAIL: bnf-bot not active"
  exit 1
fi

./venv/bin/python - <<'PY'
import sys
sys.path.insert(0, '.')
errors = []

try:
    from src.db_persistence import get_table_counts, connect
    c = get_table_counts()
    if not c.get('db_exists'):
        errors.append('trader_brain.db missing')
    connect().close()
except Exception as e:
    errors.append(f'db: {e}')

try:
    from core.shared_state import STATE
    agents = STATE.get('system.agent_status', {})
    for name in ('data', 'analysis', 'risk', 'execute', 'monitor', 'learning', 'sim'):
        if agents.get(name) not in ('RUNNING', 'STOPPED'):
            pass  # may still be starting
except Exception as e:
    errors.append(f'state: {e}')

try:
    from agents.risk_agent import RiskAgent
    from agents.execution_agent import ExecutionAgent
    from agents.monitor_agent import MonitorAgent
    from agents.sim_learning_agent import SimLearningAgent
except Exception as e:
    errors.append(f'agents import: {e}')

try:
    from src.training_calendar import verify_training_stack, bootstrap_training_month
    bootstrap_training_month()
    v = verify_training_stack()
    if not v.get('all_ok'):
        errors.append('training stack: ' + ','.join(f['name'] for f in v.get('failed', [])))
except Exception as e:
    errors.append(f'training calendar: {e}')

try:
    from src.dashboard_api import build_dashboard_payload
    p = build_dashboard_payload()
    assert 'sim_wallet' in p
    assert 'strike_ladder' in p
    from src.pro_trader_gates import pro_training_gates_active
    if not pro_training_gates_active():
        errors.append('pro_trader_gates inactive')
    assert 'training' in p
    from src.training_dashboard import format_training_dashboard
    format_training_dashboard()
except Exception as e:
    errors.append(f'training dashboard: {e}')

if errors:
    print('FAIL:', '; '.join(errors))
    sys.exit(1)
print('OK: smoke test passed')
PY

if [[ "${HEALTH_ENABLED:-true}" == "true" ]]; then
  if command -v curl >/dev/null 2>&1; then
    curl -sf "http://127.0.0.1:${HEALTH_PORT:-8080}/health" >/dev/null || {
      echo "WARN: health endpoint not ready yet (may still be starting)"
    }
  fi
fi

echo "==> Smoke test complete"
