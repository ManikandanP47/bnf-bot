(function () {
  const REFRESH_MS = 15000;
  const params = new URLSearchParams(window.location.search);
  const token = params.get('token') || '';

  function apiUrl(path) {
    const q = token ? `?token=${encodeURIComponent(token)}` : '';
    return path + q;
  }

  function fmt(n, dec) {
    if (n == null || n === '') return '—';
    return Number(n).toLocaleString('en-IN', {
      minimumFractionDigits: dec ?? 0,
      maximumFractionDigits: dec ?? 0,
    });
  }

  function chip(text, cls) {
    return `<span class="chip ${cls || ''}">${text}</span>`;
  }

  function stat(k, v) {
    return `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`;
  }

  function renderMarket(m) {
    document.getElementById('m-price').textContent = m.price ? fmt(m.price, 2) : '—';
    const above = m.above_vwap;
    const vwapTxt = m.vwap
      ? `VWAP ${fmt(m.vwap, 2)}${above === true ? ' ▲ above' : above === false ? ' ▼ below' : ''}`
      : 'VWAP —';
    document.getElementById('m-vwap').textContent = vwapTxt;

    const chips = [];
    chips.push(chip(m.session || 'CLOSED', m.session === 'CLOSED' ? 'warn' : ''));
    if (m.regime) chips.push(chip(m.regime));
    if (m.data_source) chips.push(chip(m.data_source));
    if (m.market_open) chips.push(chip('MARKET OPEN', 'bull'));
    if (m.paused) chips.push(chip('PAUSED', 'warn'));
    document.getElementById('m-chips').innerHTML = chips.join('');

    const flow = m.flow || {};
    document.getElementById('m-flow').innerHTML = [
      stat('RSI 5m', fmt(m.rsi_5m, 1)),
      stat('RSI 1m', fmt(m.rsi_1m, 1)),
      stat('Flow', fmt(m.flow_score, 2)),
      stat('VIX', flow.vix != null ? fmt(flow.vix, 2) : '—'),
      stat('PCR', flow.pcr != null ? fmt(flow.pcr, 2) : '—'),
      stat('EMA', flow.ema || '—'),
    ].join('');

    const z = m.zone || {};
    const zoneEl = document.getElementById('m-zone');
    if (z.active) {
      zoneEl.className = 'zone-box active';
      zoneEl.innerHTML =
        `<strong>${z.bias || 'ZONE'}</strong> · ${fmt(z.low)} – ${fmt(z.high)}` +
        (z.option ? `<br><span style="color:var(--muted)">${z.option}</span>` : '');
    } else {
      zoneEl.className = 'zone-box';
      zoneEl.textContent = 'No active zone — evening scan sets tomorrow plan.';
    }
  }

  function renderTraining(t) {
    document.getElementById('phase-badge').textContent = t.phase || '—';

    const simPct = t.valid_sim_required
      ? Math.min(100, (t.valid_sim_days / t.valid_sim_required) * 100)
      : 0;
    document.getElementById('sim-progress').style.width = simPct + '%';
    document.getElementById('sim-progress-text').textContent =
      `${t.valid_sim_days || 0} / ${t.valid_sim_required || 14}`;

    const paperPct = t.valid_paper_required
      ? Math.min(100, (t.valid_paper_days / t.valid_paper_required) * 100)
      : 0;
    document.getElementById('paper-progress').style.width = paperPct + '%';
    document.getElementById('paper-progress-text').textContent =
      `${t.valid_paper_days || 0} / ${t.valid_paper_required || 14}`;

    const c = t.counts || {};
    document.getElementById('training-kv').innerHTML = [
      ['Shadow today', t.shadow_today ?? 0],
      ['Days until paper', t.days_until_paper ?? '—'],
      ['Days until live', t.days_until_live ?? '—'],
      ['Today valid', t.today_valid ? '✓' : '✗'],
      ['Scans today', c.scans ?? 0],
      ['Evidence events', c.events ?? 0],
    ].map(([k, v]) => `<li><span class="k">${k}</span><span>${v}</span></li>`).join('');
  }

  function renderReadiness(r) {
    const el = document.getElementById('readiness-status');
    if (r.ready) {
      el.className = 'readiness-pill ready';
      el.textContent = '✓ READY FOR LIVE';
    } else {
      el.className = 'readiness-pill not-ready';
      el.textContent = '✗ NOT READY' + (r.reason ? ' — ' + r.reason : '');
    }
    const tbody = document.querySelector('#gates-table tbody');
    const gates = r.gates || [];
    tbody.innerHTML = gates.map((g) => {
      const ok = g.pass || g.ok;
      return `<tr>
        <td>${g.name || g.gate || ''}</td>
        <td class="${ok ? 'pass' : 'fail'}">${ok ? 'PASS' : 'FAIL'}</td>
        <td>${g.detail || g.value || ''}</td>
      </tr>`;
    }).join('');
  }

  function renderScans(s) {
    document.getElementById('scan-stats').innerHTML = [
      stat('Total', s.total ?? 0),
      stat('Opens', s.opens ?? 0),
      stat('Skips', s.skips ?? 0),
    ].join('');

    const reasons = s.skip_reasons || {};
    document.getElementById('skip-reasons').innerHTML = Object.entries(reasons)
      .map(([k, v]) => `<span class="tag">${k}: ${v}</span>`)
      .join('');

    const recent = s.recent || [];
    document.getElementById('scan-list').innerHTML = recent.length
      ? recent.slice().reverse().map((row) => {
          const t = (row.time || row.ts || '').slice(11, 19);
          return `<div class="row">${t} ${row.event} ${row.reason || ''} ${row.bias || ''}</div>`;
        }).join('')
      : '<div class="row" style="color:var(--muted)">No scans logged today</div>';
  }

  function renderEvidence(tail, counts) {
    document.getElementById('evidence-stats').innerHTML = stat(
      'Events today',
      (counts && counts.events) ?? tail.length
    );
    document.getElementById('evidence-list').innerHTML = tail.length
      ? tail.slice().reverse().map((e) => {
          const t = (e.ts || e.time || '').slice(11, 19);
          return `<div class="row">${t} ${e.event || e.type} ${e.detail || e.reason || ''}</div>`;
        }).join('')
      : '<div class="row" style="color:var(--muted)">No evidence yet today</div>';
  }

  function renderML(ml) {
    const meta = ml.meta || {};
    document.getElementById('ml-stats').innerHTML = [
      stat('Samples', ml.samples ?? 0),
      stat('Active', ml.active || meta.active || 'none'),
      stat('CV accuracy', (ml.cv_accuracy ?? meta.cv_accuracy ?? 0) + '%'),
      stat('RF min', ml.rf_min ?? 15),
      stat('NN min', ml.nn_min ?? 100),
    ].join('');
  }

  function renderIntelligence(intel) {
    document.getElementById('suggestions').innerHTML = (intel.suggestions || [])
      .map((s) => `<li>${s}</li>`)
      .join('') || '<li style="opacity:0.6">No suggestions yet</li>';

    document.getElementById('rag-list').innerHTML = (intel.rag_chunks || []).length
      ? intel.rag_chunks.map((c) =>
          `<div class="row">[${c.score}] ${c.content}</div>`
        ).join('')
      : '<div class="row" style="color:var(--muted)">RAG warming up</div>';

    document.getElementById('pattern-list').innerHTML = (intel.patterns || []).length
      ? intel.patterns.map((p) =>
          `<div class="row">${p.key} · ${p.wr}% WR · n=${p.samples} · ₹${fmt(p.pnl)}</div>`
        ).join('')
      : '<div class="row" style="color:var(--muted)">No patterns yet</div>';

    document.getElementById('roadmap').innerHTML = (intel.roadmap || [])
      .map((r) =>
        `<div class="roadmap-item ${r.status}"><div class="title">${r.title}</div>${r.detail}</div>`
      ).join('');
  }

  function renderAgents(a) {
    const agents = a.agents || {};
    document.getElementById('agent-grid').innerHTML = Object.entries(agents)
      .map(([name, st]) => {
        const ok = st === 'running' || st === 'ok' || st === true;
        return `<div class="agent ${ok ? 'ok' : 'err'}">${name}: ${st}</div>`;
      }).join('') || '<div class="agent">No agent data</div>';
  }

  function renderSystem(groww, persist, line) {
    const feed = (groww && groww.feed) || {};
    document.getElementById('system-kv').innerHTML = [
      ['Data source', groww.data_source || '—'],
      ['Token cache', groww.token_cache_age_sec >= 0 ? groww.token_cache_age_sec + 's' : 'none'],
      ['Feed status', feed.status || feed.state || '—'],
      ['DB', persist.db_exists ? '✓' : '✗'],
      ['Lessons', persist.lessons ?? 0],
      ['Patterns', persist.pattern_memory ?? 0],
      ['Shadow trades', persist.shadow_trades ?? 0],
      ['Persistence', line || '—'],
    ].map(([k, v]) => `<li><span class="k">${k}</span><span>${v}</span></li>`).join('');
  }

  function renderExecuteGap(gap) {
    if (!gap) return;
    const v = document.getElementById('gap-verdict');
    v.textContent = gap.verdict || '—';
    v.className = 'gap-verdict' + (gap.misleading ? ' warn' : gap.sim_ok && gap.execute_ok ? ' ok' : '');

    const simCls = gap.sim_ok ? 'status-pass' : 'status-fail';
    const exCls = gap.execute_ok ? 'status-pass' : 'status-fail';
    document.getElementById('gap-grid').innerHTML = `
      <div class="gap-box">
        <h4>Virtual sim (score ≥ ${gap.sim_min_score})</h4>
        <p class="${simCls}">${gap.sim_ok ? 'PASS' : 'BLOCK'} — score ${gap.sim_score ?? 0}</p>
        <p>${gap.sim_reason || (gap.sim_reasons || []).join(', ') || '—'}</p>
      </div>
      <div class="gap-box">
        <h4>/execute + RiskAgent</h4>
        <p class="${exCls}">${gap.execute_ok ? 'WOULD APPROVE' : 'WOULD BLOCK'}</p>
        <p>${gap.execute_reason || '—'}</p>
        <p style="color:var(--muted);margin-top:0.3rem">Signal score: ${gap.signal_score ?? '—'} · Bias: ${gap.bias || '—'}</p>
      </div>`;
  }

  let playbookCache = null;
  let activePbTab = 'metrics';

  function initMainTabs() {
    const tabs = document.querySelectorAll('.main-tab');
    const panels = document.querySelectorAll('.tab-panel');
    const saved = sessionStorage.getItem('bnf_tab') || 'overview';

    function show(panelId) {
      tabs.forEach((t) => t.classList.toggle('active', t.dataset.panel === panelId));
      panels.forEach((p) => p.classList.toggle('active', p.dataset.panel === panelId));
      sessionStorage.setItem('bnf_tab', panelId);
    }

    tabs.forEach((btn) => {
      btn.addEventListener('click', () => show(btn.dataset.panel));
    });
    if (document.querySelector(`.main-tab[data-panel="${saved}"]`)) {
      show(saved);
    }
  }

  function renderPlaybookTab(pb, tab) {
    if (!pb) return '';
    if (tab === 'metrics') {
      return (pb.metrics || []).map((m) =>
        `<div class="metric-card"><strong>${m.name}</strong>
          <div class="math">${m.math}</div>
          <div><b>Use:</b> ${m.use}</div>
          <div><b>Trade:</b> ${m.trade}</div></div>`
      ).join('');
    }
    if (tab === 'timing') {
      return (pb.timing || []).map((t) =>
        `<div class="timing-row"><span>${t.window}</span><span>${t.label}</span><span>${t.action}</span></div>`
      ).join('');
    }
    if (tab === 'trust') {
      return (pb.trust_guide || []).map((t) =>
        `<div class="trust-row"><span class="trust-badge ${t.trust}">${t.trust}</span>
          <div><b>${t.metric}</b> — ${t.why}</div></div>`
      ).join('');
    }
    if (tab === 'india') {
      return (pb.indian_wisdom || []).map((w) =>
        `<div class="metric-card">${w}</div>`
      ).join('');
    }
    const entries = (pb.entry_stack || []).map((s) => `<li>${s}</li>`).join('');
    const exits = (pb.exit_rules || []).map((s) => `<li>${s}</li>`).join('');
    return `<h3 class="subhead">Entry stack</h3><ol class="stack-list">${entries}</ol>
      <h3 class="subhead">Exit rules</h3><ol class="stack-list">${exits}</ol>`;
  }

  function renderPlaybook(pb) {
    if (!pb) return;
    playbookCache = pb;
    document.getElementById('phase-tips').innerHTML = (pb.phase_tips || [])
      .map((t) => `<span class="phase-tip">${t}</span>`).join('');
    document.getElementById('playbook-content').innerHTML = renderPlaybookTab(pb, activePbTab);
  }

  document.querySelectorAll('.playbook-tabs .pb-tab').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.playbook-tabs .pb-tab').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      activePbTab = btn.dataset.tab;
      if (playbookCache) {
        document.getElementById('playbook-content').innerHTML = renderPlaybookTab(playbookCache, activePbTab);
      }
    });
  });

  function renderGreeks(g) {
    if (!g || g.error) return;
    const ch = g.chain || {};
    document.getElementById('greeks-note').textContent = g.math_note || '';
    document.getElementById('greeks-chain').innerHTML = [
      stat('ATM IV', ch.atm_iv_avg ? (ch.atm_iv_avg * 100).toFixed(1) + '%' : '—'),
      stat('IV rank', ch.iv_rank != null ? ch.iv_rank : '—'),
      stat('PCR', ch.pcr != null ? ch.pcr : '—'),
      stat('Max pain', ch.max_pain ? fmt(ch.max_pain, 0) : '—'),
      stat('Updated', ch.timestamp || '—'),
    ].join('');
    const c = g.contract || {};
    document.getElementById('greeks-contract').innerHTML = [
      stat('Delta δ', c.delta != null ? c.delta : '—'),
      stat('Theta θ/lot/d', c.theta_per_lot_day != null ? '₹' + c.theta_per_lot_day : '—'),
      stat('Vega ν/lot/1%', c.vega_per_lot_1pct != null ? '₹' + c.vega_per_lot_1pct : '—'),
      stat('Gamma γ', c.gamma != null ? c.gamma : '—'),
      stat('DTE', c.dte_days != null ? c.dte_days : '—'),
    ].join('');
  }

  function renderSimWallet(sw) {
    if (!sw || sw.error) return;
    const w = sw.wallet || {};
    const t = sw.today || {};
    const wk = sw.week || {};
    const rec = sw.recovery || {};
    const at = sw.all_time || {};
    const dead = sw.account_status || {};
    const cmp = sw.live_compare || {};
    const tw = sw.training_week || {};

    const banner = document.getElementById('wallet-dead-banner');
    if (banner) {
      if (dead.dead || w.account_dead_today) {
        banner.classList.remove('hidden');
        banner.textContent =
          `🛑 Sim account dead for today — loss ₹${fmt(Math.abs(dead.today_pnl || t.pnl), 0)} ` +
          `hit ${dead.pct_of_week_base || 2}% cap (₹${fmt(dead.cap_rs || w.daily_loss_cap_rs, 0)}). No new sim orders.`;
      } else {
        banner.classList.add('hidden');
      }
    }

    const balEl = document.getElementById('wallet-balance');
    if (balEl) balEl.textContent = '₹' + fmt(w.balance, 0);
    const phaseEl = document.getElementById('wallet-phase');
    if (phaseEl) {
      phaseEl.textContent = (w.phase_label || '') +
        (tw.week ? ` · Week ${tw.week} base ₹${fmt(w.week_base_rs, 0)}` : '');
    }

    const statsEl = document.getElementById('wallet-stats');
    if (statsEl) {
      statsEl.innerHTML = [
        stat('Available', '₹' + fmt(w.available, 0)),
        stat('Week P&L', '₹' + fmt(w.week_pnl ?? wk.pnl, 0)),
        stat('Lots / open', (w.lots_allowed ?? 1) + ' / ' + (w.max_open ?? 2)),
        stat('Today P&L', '₹' + fmt(t.pnl, 0)),
        stat('Daily cap left', '₹' + fmt(w.daily_remaining_rs ?? dead.remaining_rs, 0)),
        stat('Recovery P&L', '₹' + fmt(t.recovery_pnl, 0)),
      ].join('');
    }

    const pct = w.progress_pct ?? 0;
    const goalLbl = document.getElementById('wallet-goal-label');
    if (goalLbl) goalLbl.textContent = `Week ${tw.week || w.week || 1} target ₹${fmt(w.next_goal_rs, 0)}`;
    const prog = document.getElementById('wallet-progress');
    if (prog) prog.style.width = Math.min(100, pct) + '%';
    const progTxt = document.getElementById('wallet-progress-text');
    if (progTxt) progTxt.textContent = pct + '%';

    const cg = document.getElementById('live-compare-grid');
    if (cg && cmp.sim && cmp.live) {
      const s = cmp.sim;
      const l = cmp.live;
      cg.innerHTML = `
        <div class="gap-box">
          <h4>${s.label}</h4>
          <div>Base ₹${fmt(s.capital_base, 0)} → Balance ₹${fmt(s.balance, 0)}</div>
          <div>Today <strong>₹${fmt(s.today_pnl, 0)}</strong> · Week ₹${fmt(s.week_pnl, 0)}</div>
          <div>Open ${s.open_positions}/${s.max_open} · ${s.lots_allowed} lot(s) max</div>
        </div>
        <div class="gap-box">
          <h4>${l.label}</h4>
          <div>Capital ₹${fmt(l.capital, 0)}</div>
          <div>Today <strong>₹${fmt(l.today_pnl, 0)}</strong> · Week ₹${fmt(l.week_pnl, 0)}</div>
          <div style="color:var(--muted);font-size:0.78rem">${l.note || ''}</div>
        </div>`;
    }

    const kv = document.getElementById('wallet-kv');
    if (kv) {
      const ladder = (w.weekly_capital_ladder || [25000, 35000, 50000, 75000])
        .map((v, i) => `W${i + 1} ₹${fmt(v, 0)}`).join(' → ');
      const proTag = w.pro_training_mode ? ' · pro training' : '';
      kv.innerHTML = [
        ['Mode', w.pro_training_mode ? 'Pro trader training' : 'Standard sim'],
        ['Training week', `Week ${tw.week || 1} (day ${tw.days_in_week || '—'})`],
        ['Week base', '₹' + fmt(w.week_base_rs, 0) + proTag],
        ['Next week base', '₹' + fmt(w.next_week_base_rs, 0)],
        ['Capital ladder', ladder],
        ['Multi-order', w.multi_order ? 'ON (week 1+)' : 'off'],
        ['Daily loss cap', '₹' + fmt(w.daily_loss_cap_rs, 0) + ' (2%)'],
        ['Open sims', t.open ?? 0],
        ['All-time WR', at.win_rate != null ? at.win_rate + '%' : '—'],
      ].map(([k, v]) => `<li><span class="k">${k}</span><span>${v}</span></li>`).join('');
    }

    const rkv = document.getElementById('recovery-pnl-kv');
    if (rkv) {
      const ds = rec.drill_stats || {};
      rkv.innerHTML = [
        ['Recovery', rec.enabled_from_week1 !== false ? 'ON from week 1' : '—'],
        ['Recovery window', rec.active ? '🔄 OPEN' : 'closed'],
        ['Recovery P&L today', '₹' + fmt(t.recovery_pnl, 0)],
        ['Recovery trades', t.recovery_trades ?? 0],
        ['Drill WR', ds.wr != null ? ds.wr + '%' : '—'],
        ['Weekly recovery', (rec.weekly_used ?? 0) + '/' + (rec.weekly_cap ?? 2)],
      ].map(([k, v]) => `<li><span class="k">${k}</span><span>${v}</span></li>`).join('');
    }

    const tbody = document.getElementById('sim-orders-body');
    if (!tbody) return;
    const orders = sw.orders && sw.orders.length ? sw.orders : (sw.orders_recent || []);
    if (!orders.length) {
      tbody.innerHTML = '<tr><td colspan="9" style="color:var(--muted)">No sim orders yet — scans run when market opens.</td></tr>';
      return;
    }
    tbody.innerHTML = orders.slice().reverse().map((o) => {
      const st = o.status || '—';
      const cls = o.outcome === 'WIN' ? 'win' : o.outcome === 'LOSS' ? 'loss' : (st === 'OPEN' ? 'open' : '');
      const pnl = st === 'CLOSED' ? '₹' + fmt(o.pnl_rs, 0) : '—';
      const recTag = o.is_recovery ? '<span class="tag-recovery">🔄</span>' : '';
      const risk = o.max_loss_rs != null ? '₹' + fmt(o.max_loss_rs, 0) : '—';
      return `<tr class="${cls}">
        <td>${o.id}${recTag}</td>
        <td>${o.entry_time || ''}${o.exit_time ? '→' + o.exit_time : ''}</td>
        <td>${o.option_name || '—'}<br><span style="color:var(--muted)">${o.session || ''}</span></td>
        <td>${o.lots ?? 1}</td>
        <td>₹${fmt(o.entry_prem, 0)}</td>
        <td>${risk}</td>
        <td>${o.exit_prem ? '₹' + fmt(o.exit_prem, 0) : '—'}</td>
        <td>${pnl}</td>
        <td>${st}${o.outcome ? ' ' + o.outcome : ''}</td>
      </tr>`;
    }).join('');
  }

  function renderStrikeLadder(ld) {
    const head = document.getElementById('ladder-head');
    const body = document.getElementById('ladder-body');
    const cePe = document.getElementById('ladder-ce-pe');
    if (!head || !body) return;
    if (!ld || !ld.ok) {
      head.innerHTML = '';
      body.innerHTML = '<tr><td colspan="7" style="color:var(--muted)">Ladder fills when market is open and BNF price is live.</td></tr>';
      if (cePe) cePe.textContent = '';
      return;
    }
    head.innerHTML = [
      ['Bias', ld.bias || '—'],
      ['ATM', ld.atm != null ? ld.atm.toLocaleString('en-IN') : '—'],
      ['Scanned', ld.scanned ?? '—'],
      ['Budget/lot', '₹' + fmt(ld.max_lot_cost, 0)],
    ].map(([k, v]) => `<div class="stat"><span class="k">${k}</span><span>${v}</span></div>`).join('');
    const rows = ld.candidates || [];
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="7" style="color:var(--muted)">No affordable strikes in ladder.</td></tr>';
    } else {
      body.innerHTML = rows.map((c, i) => {
        const mark = i === 0 ? '👉 ' : '';
        return `<tr class="${i === 0 ? 'win' : ''}">
          <td>${mark}${i + 1}</td>
          <td>${c.name || c.strike + ' ' + (c.opt_type || '')}</td>
          <td>₹${fmt(c.premium, 0)}</td>
          <td>${c.rr_label || (c.rr_ratio ? '1:' + c.rr_ratio : '—')}</td>
          <td>${c.score ?? '—'}</td>
          <td>${c.archetype || c.otm_steps || '—'}</td>
          <td style="font-size:0.78rem;color:var(--muted)">${c.reason_txt || '—'}</td>
        </tr>`;
      }).join('');
    }
    const cmp = ld.ce_pe_compare || {};
    if (cePe) cePe.textContent = cmp.note ? `CE/PE compare: ${cmp.note}` : '';
  }

  function renderLossPrevention(lp) {
    const head = document.getElementById('loss-prev-head');
    const pre = document.getElementById('loss-pre-kv');
    const rect = document.getElementById('loss-rect-kv');
    if (!head) return;
    if (!lp || lp.enabled === false) {
      head.innerHTML = '';
      if (pre) pre.innerHTML = '<li><span class="k">Status</span><span>Loading…</span></li>';
      return;
    }
    const lt = lp.losses_today || {};
    head.innerHTML = [
      ['Losses today', (lt.real || 0) + ' real · ' + (lt.sim || 0) + ' sim'],
      ['Since last loss', (lp.minutes_since_loss ?? '—') + 'm'],
      ['Paused', lp.paused ? 'YES' : 'No'],
      ['Sim dead', lp.sim_account_dead ? 'YES' : 'No'],
    ].map(([k, v]) => `<div class="stat"><span class="k">${k}</span><span>${v}</span></div>`).join('');
    if (pre) {
      const steps = lp.pre_trade_steps || [];
      pre.innerHTML = steps.map((s) =>
        `<li><span class="k">${s.name}</span><span style="font-size:0.78rem;color:var(--muted)">${s.hint}</span></li>`
      ).join('');
      const inT = (lp.in_trade_steps || []).map((s) =>
        `<li><span class="k">↳ ${s.name}</span><span style="font-size:0.78rem;color:var(--muted)">${s.hint}</span></li>`
      ).join('');
      pre.innerHTML += inT;
    }
    if (rect) {
      const lr = lp.last_rectification || {};
      const plan = lr.rectification || {};
      const steps = plan.steps || lp.post_loss_steps || [];
      rect.innerHTML = steps.slice(0, 7).map((s) => {
        const done = s.done === true || (s.done === undefined && !plan.steps);
        const icon = done ? '✅' : '⏳';
        const title = s.title || s.name || '';
        const detail = s.detail || s.hint || '';
        return `<li><span class="k">${icon} ${title}</span><span style="font-size:0.78rem">${detail}</span></li>`;
      }).join('') || '<li><span class="k">Status</span><span>No loss today — gates active</span></li>';
    }
  }

  function renderProDecision(pd) {
    const head = document.getElementById('pro-decision-head');
    const rangesKv = document.getElementById('pro-ranges-kv');
    const checklistKv = document.getElementById('pro-checklist-kv');
    const chainBody = document.getElementById('chain-rr-body');
    const spreadNote = document.getElementById('pro-spread-note');
    const thetaNote = document.getElementById('pro-theta-note');
    if (!head) return;
    if (!pd || !pd.ok) {
      head.innerHTML = '';
      if (rangesKv) rangesKv.innerHTML = '<li><span class="k">Status</span><span>Waiting for market…</span></li>';
      if (checklistKv) checklistKv.innerHTML = '';
      if (chainBody) chainBody.innerHTML = '<tr><td colspan="6">—</td></tr>';
      return;
    }
    const cl = pd.checklist || {};
    const cp = pd.ce_pe || {};
    const pick = pd.pick || {};
    head.innerHTML = [
      ['Spot', '₹' + fmt(pd.price, 0)],
      ['Structure', pd.bias || '—'],
      ['Checklist', (cl.passed || 0) + '/' + (cl.total || 12)],
      ['Trade side', cp.trade_side || '—'],
    ].map(([k, v]) => `<div class="stat"><span class="k">${k}</span><span>${v}</span></div>`).join('');

    const levels = (pd.ranges && pd.ranges.levels) || [];
    if (rangesKv) {
      rangesKv.innerHTML = levels.slice(0, 8).map((l) =>
        `<li><span class="k">${l.name}</span><span>${fmt(l.value, 0)} · ${l.dist_pct}% ${l.near ? '⚡' : ''}</span></li>`
      ).join('') || '<li><span class="k">Ranges</span><span>loading</span></li>';
    }
    if (checklistKv) {
      const checks = cl.checks || [];
      checklistKv.innerHTML = checks.map((c) =>
        `<li><span class="k">${c.ok ? '✅' : '⛔'} ${c.name}</span><span style="font-size:0.78rem">${c.detail || ''}</span></li>`
      ).join('');
    }
    const top = (pd.chain && pd.chain.chain_top5) || [];
    if (chainBody) {
      chainBody.innerHTML = top.length ? top.map((c) =>
        `<tr><td>${c.opt_type}</td><td>${c.name}</td><td>₹${fmt(c.premium, 0)}</td>
         <td>${c.rr_label || '—'}</td><td>${c.archetype || '—'}</td><td>${c.composite_score ?? c.score}</td></tr>`
      ).join('') : '<tr><td colspan="6">No chain data</td></tr>';
    }
    const sp = pd.spread || {};
    if (spreadNote) {
      spreadNote.textContent = sp.available
        ? `Spread: ${sp.spread_type} · debit ₹${fmt(sp.net_debit_rs, 0)} · ${sp.vs_naked || ''}`
        : (cp.flip_note || cp.edge_note || '');
    }
    const th = pd.theta_advisory || {};
    if (thetaNote) thetaNote.textContent = th.note || (pick.archetype_label ? `Pick: ${pick.archetype_label}` : '');
  }

  function renderLearningFeed(lf) {
    if (!lf) return;
    const s = lf.summary || {};
    const li = lf.live_insights || {};
    const rec = lf.recovery || {};
    document.getElementById('learning-stats').innerHTML = [
      stat('Observations', s.observations_today ?? li.scans_today ?? 0),
      stat('Virtual opens', s.virtual_opens ?? li.virtual_opens_today ?? 0),
      stat('Skips logged', s.skips_logged ?? 0),
      stat('IV rank', li.iv_rank != null ? li.iv_rank : '—'),
      stat('Regime', li.regime || '—'),
      stat('Recovery', rec.active ? '🔄 open' : (rec.used_today ? '✓ used' : '—')),
    ].join('');
    const recEl = document.getElementById('recovery-panel');
    if (recEl && rec.enabled !== false) {
      const ds = rec.drill_stats || {};
      recEl.innerHTML = [
        `<li><span class="k">Protocol</span><span>${rec.active ? 'Window open' : 'Idle'} · min score ${rec.min_score || 9}</span></li>`,
        `<li><span class="k">Today loss</span><span>${rec.loss_pnl ? '₹' + rec.loss_pnl : '—'} ${rec.loss_type || ''}</span></li>`,
        `<li><span class="k">Virtual drills</span><span>${ds.samples || 0} resolved · WR ${ds.wr != null ? ds.wr + '%' : '—'}</span></li>`,
        `<li><span class="k">Weekly recovery</span><span>${rec.weekly_used || 0}/${rec.weekly_cap || 1}</span></li>`,
      ].join('');
    }
    const feed = lf.feed || [];
    document.getElementById('learning-feed').innerHTML = feed.length
      ? feed.map((r) =>
          `<div class="learn-row ${(r.event || '').toLowerCase()}">
            <div class="meta">${r.time} · ${r.event} · ${r.session} · score ${r.sim_score ?? '—'} · ₹${fmt(r.price, 0)}</div>
            <div>${r.lesson || '—'}</div>
          </div>`
        ).join('')
      : '<div class="learn-row skip"><div class="meta">No observations yet today</div><div>Scans start when market opens (~9:20 AM). Each scan logs what the bot sees — even skips.</div></div>';
  }

  function renderSimRealism(r) {
    if (!r || r.error) return;
    document.getElementById('realism-stats').innerHTML = [
      stat('Txn cost/rt', '₹' + (r.round_trip_cost_rs ?? 65)),
      stat('Daily loss cap', '₹' + (r.daily_loss_limit_rs ?? 100)),
      stat('Days to expiry', r.days_to_expiry ?? '—'),
      stat('Today sim P&L', '₹' + fmt(r.today_shadow_pnl, 0)),
    ].join('');
    document.getElementById('realism-kv').innerHTML = [
      ['Monthly expiry', r.monthly_expiry || '—'],
      ['Expiry week', r.is_expiry_week ? '⚠️ YES' : 'No'],
      ['Expiry day', r.is_expiry_day ? '⚠️ TODAY' : 'No'],
      ['Min DTE (sim)', r.min_days_to_expiry ?? 5],
      ['Sweet premium band', r.require_sweet_premium ? '₹120–₹280 enforced' : 'off'],
      ['Block expiry day entries', r.block_expiry_day ? 'Yes' : 'No'],
    ].map(([k, v]) => `<li><span class="k">${k}</span><span>${v}</span></li>`).join('');
  }

  function setConn(ok) {
    const dot = document.getElementById('conn-dot');
    const label = document.getElementById('conn-label');
    dot.className = 'dot ' + (ok ? 'online' : 'offline');
    label.textContent = ok ? 'Live' : 'Offline';
  }

  async function refresh() {
    try {
      const res = await fetch(apiUrl('/api/v1/snapshot'));
      if (!res.ok) throw new Error(res.status);
      const d = await res.json();
      setConn(true);
      document.getElementById('ts-display').textContent = d.ts_display || d.ts || '';
      renderMarket(d.market || {});
      renderTraining(d.training || {});
      renderReadiness(d.readiness || {});
      renderScans(d.scans || {});
      renderEvidence(d.evidence_tail || [], (d.training || {}).counts);
      renderML(d.ml || {});
      renderIntelligence(d.intelligence || {});
      renderExecuteGap(d.execute_gap);
      renderSimWallet(d.sim_wallet);
      renderStrikeLadder(d.strike_ladder);
      renderProDecision(d.pro_decision);
      renderLossPrevention(d.loss_prevention);
      renderSimRealism(d.sim_realism);
      renderLearningFeed(d.learning_feed);
      renderGreeks(d.greeks);
      renderPlaybook(d.playbook);
      renderAgents(d.agents || {});
      renderSystem(d.groww, d.persistence || {}, d.persistence_line);
    } catch (e) {
      setConn(false);
      document.getElementById('conn-label').textContent = 'Error: ' + e.message;
    }
  }

  document.getElementById('btn-refresh').addEventListener('click', refresh);
  initMainTabs();
  refresh();
  setInterval(refresh, REFRESH_MS);
})();
