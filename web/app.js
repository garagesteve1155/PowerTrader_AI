/* ═══════════════════════════════════════════════════════════
   PowerTrader · Obsidian Command · Frontend
   ═══════════════════════════════════════════════════════════ */

const $ = (sel, ctx = document) => ctx.querySelector(sel);
const $$ = (sel, ctx = document) => [...ctx.querySelectorAll(sel)];

// Shadow color is always #00D4FF. Real exchange colors fall back to a generic palette.
const XK_PALETTE = ['#F0B429', '#A78BFA', '#34D399', '#F87171', '#60A5FA'];
function xkColor(xk) {
  if (xk === 'shadow' || xk === 'demo') return '#00D4FF';
  const real = (state.exchangeList || []).filter(k => k !== 'shadow' && k !== 'demo');
  const idx = real.indexOf(xk);
  return XK_PALETTE[idx >= 0 ? idx : 0] || '#888';
}
function xkDisplayName(xk) {
  if (xk === 'demo') return 'Demo';
  if (xk === 'shadow') return 'Shadow';
  return xk.charAt(0).toUpperCase() + xk.slice(1);
}
function xkShortLabel(xk) {
  if (xk === 'demo') return 'D';
  if (xk === 'shadow') return 'S';
  return xk.charAt(0).toUpperCase();
}

const state = {
  coins: [],
  exchangeList: [],
  exchangeData: {},
  positions: {},
  dca24h: {},
  lth: {},
  tradingMode: 'demo',
  discoveredExchanges: [],
  tradeStartLevel: 1,
  selectedCoin: null,
  selectedTf: '1hour',
  thinkerRunning: false,
  traderRunning: false,
  dataManagerState: 'Stopped',
  chart: null,
  candleSeries: null,
  acctSeries: {},
  priceLines: [],
  chartRefreshTimer: null,
  chartMarkersTimer: null,
  chartMode: 'candle',
  accountRange: 0,
  logRefreshTimer: null,
  cfg: {},
  cfgSchema: {},
  cardMode: 'simple',
  historyFilterCoin: null,
  _acctRangeTimer: null,
  _acctRangeAbort: null,
};

const TF_LIST = ['1min','5min','15min','30min','1hour','2hour','4hour','8hour','12hour','1day','1week'];
const TF_MINUTES_LABEL = {60:'1h', 120:'2h', 240:'4h', 480:'8h', 720:'12h', 1440:'1d', 10080:'1w'};
const TF_REFRESH_MS = {
  '1min': 5_000, '5min': 15_000, '15min': 30_000, '30min': 45_000,
  '1hour': 60_000, '2hour': 90_000, '4hour': 120_000,
  '8hour': 180_000, '12hour': 300_000, '1day': 300_000, '1week': 600_000,
};
const TF_SECONDS = {
  '1min': 60, '5min': 300, '15min': 900, '30min': 1800,
  '1hour': 3600, '2hour': 7200, '4hour': 14400,
  '8hour': 28800, '12hour': 43200, '1day': 86400, '1week': 604800,
};
const ACCT_RANGES = [
  {label: '1D', hours: 24}, {label: '3D', hours: 72}, {label: '1W', hours: 168},
  {label: '2W', hours: 336}, {label: '1M', hours: 720}, {label: 'ALL', hours: 0},
];
const CHART_COLORS = {
  bg: '#0B0B14',
  text: '#555570',
  grid: '#1A1A2E',
  up: '#00CC66',
  down: '#FF4466',
  border: '#2A2A48',
};

function fmtUSD(v) {
  if (v == null || isNaN(v)) return '—';
  const n = Number(v);
  if (Math.abs(n) >= 1000) return '$' + n.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  if (Math.abs(n) >= 1) return '$' + n.toFixed(2);
  return '$' + n.toPrecision(4);
}
function fmtSignedUSD2(v) {
  const n = Number(v || 0);
  return (n >= 0 ? '+$' : '-$') + Math.abs(n).toFixed(2);
}

function fmtPct(v) {
  if (v == null || isNaN(v)) return '—';
  const n = Number(v);
  return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
}

function fmtQty(v, coin) {
  if (v == null) return '—';
  const n = Number(v);
  if (n === 0) return '0';
  if (n >= 100) return n.toFixed(2);
  if (n >= 1) return n.toFixed(4);
  return n.toPrecision(4);
}

function pricePrecision(price) {
  const ap = Math.abs(Number(price));
  if (!ap || isNaN(ap)) return {precision: 2, minMove: 0.01};
  if (ap >= 1) return {precision: 2, minMove: 0.01};
  const decimals = Math.min(12, Math.max(2, Math.floor(-Math.log10(ap)) + 3));
  return {precision: decimals, minMove: Math.pow(10, -decimals)};
}

function fmtPrice(v) {
  if (v == null || isNaN(v)) return '—';
  const n = Number(v);
  if (n >= 1000) return n.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  if (n >= 1) return n.toFixed(2);
  if (n >= 0.01) return n.toFixed(4);
  return n.toPrecision(4);
}

function fmtTime(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString('en-US', {hour: '2-digit', minute: '2-digit', hour12: false});
}

function fmtDateTime(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  const Y = d.getFullYear();
  const M = String(d.getMonth() + 1).padStart(2, '0');
  const D = String(d.getDate()).padStart(2, '0');
  const h = String(d.getHours()).padStart(2, '0');
  const m = String(d.getMinutes()).padStart(2, '0');
  return `${Y}-${M}-${D} ${h}:${m}`;
}

function fmtDate(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  return d.toLocaleDateString('en-US', {month: 'short', day: 'numeric'}) + ' ' +
         d.toLocaleTimeString('en-US', {hour: '2-digit', minute: '2-digit', hour12: false});
}

// ── API ──

async function api(path, opts = {}) {
  const resp = await fetch('/api/' + path, {
    headers: {'Content-Type': 'application/json'},
    ...opts,
  });
  return resp.json();
}

async function apiPost(path) { return api(path, {method: 'POST'}); }
async function apiPut(path, body) { return api(path, {method: 'PUT', body: JSON.stringify(body)}); }

// ── WebSocket ──

let ws = null;
let wsRetry = 0;

function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.onopen = () => { wsRetry = 0; };
  ws.onclose = () => {
    const delay = Math.min(1000 * Math.pow(2, wsRetry++), 30000);
    setTimeout(connectWS, delay);
  };
  ws.onerror = () => {};
  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      handleWSMessage(msg);
    } catch {}
  };
}

function handleWSMessage(msg) {
  switch (msg.type) {
    case 'trader_status':
      updateTraderStatus(msg.exchange, msg.data);
      break;
    case 'signals':
      updateSignals(msg.data);
      break;
    case 'pnl':
      updatePnl(msg.exchange, msg.data);
      break;
    case 'system':
      updateSystemStatus(msg.data);
      break;
    case 'thinker_ready':
      break;
    case 'data_manager_status':
      state.dataManagerState = msg.data.state || 'Stopped';
      updateDataManagerPill(state.dataManagerState);
      break;
    case 'error_event':
      onNewErrorEvent(msg.data);
      break;
  }
}

// ── Initialize ──

let _refreshInterval = null;
let _dataTabInterval = null;
let _lastExchangeListJson = null;

async function init() {
  setupTabs();
  setupMobileNav();
  setupButtons();
  setupTimeframes();
  setupSash();
  setupViewToggle();
  setupDataTabDelegation();
  setupTradesDelegation();

  $('#btn-chart-reset').addEventListener('click', () => {
    if (state.chart) state.chart.timeScale().fitContent();
  });

  // Load schema, config, and discovered exchanges once at startup
  try {
    const [schema, cfgData, discData] = await Promise.all([
      api('config/schema'), api('config'), api('discovered-exchanges'),
    ]);
    state.cfgSchema = schema;
    state.cfg = cfgData;
    state.tradeStartLevel = cfgData.trade_start_level || 1;
    state.discoveredExchanges = discData.exchanges || [];
    _applyUiPrefs(cfgData);
    renderConfig(cfgData);
  } catch (e) {
    console.warn('Could not load startup config:', e);
  }

  await refreshAll();
  connectWS();

  _refreshInterval = setInterval(refreshAll, 10000);
}

function _applyUiPrefs(cfg) {
  // Refresh interval
  const refreshMs = Math.max(1000, (cfg.ui_refresh_seconds || 10) * 1000);
  if (_refreshInterval) clearInterval(_refreshInterval);
  _refreshInterval = setInterval(refreshAll, refreshMs);

  // Font size
  const fs = cfg.ui_font_size;
  if (fs) document.documentElement.style.setProperty('--ui-font-size', fs + 'px');
  const lfs = cfg.ui_log_font_size;
  if (lfs) document.documentElement.style.setProperty('--log-font-size', lfs + 'px');

  // Default timeframe (only set on first load, not on subsequent refreshes)
  if (!state.selectedCoin && cfg.default_timeframe) {
    state.selectedTf = cfg.default_timeframe;
  }
}

async function refreshAll() {
  try {
    const [statusData, coinsData, posData] = await Promise.all([
      api('status'), api('coins'), api('positions'),
    ]);

    state.exchangeList = statusData.exchange_list || ['shadow'];
    state.tradingMode = statusData.trading_mode || 'demo';

    if (statusData.exchanges) {
      state.exchangeData = statusData.exchanges;
    }

    updateSystemStatus(statusData.system);

    const exchangeListJson = JSON.stringify(state.exchangeList);
    if (exchangeListJson !== _lastExchangeListJson) {
      _lastExchangeListJson = exchangeListJson;
      renderTopbarAccounts();
    }

    if (coinsData.coins) {
      state.coins = coinsData.coins;
    }

    if (posData.positions) {
      state.positions = posData.positions;
      state.dca24h = posData.dca_24h || {};
      state.lth = posData.lth || {};
    }

    mergePositionsIntoCoins();
    renderCoinGrid();
    populateLogSourceDropdown();

    if ($('#tab-compare').classList.contains('active')) loadCompare();
    if (!$('#training-list').querySelector('.train-row')) renderTraining(coinsData.coins);
    else updateTrainingBadges(coinsData.coins);
  } catch (e) {
    console.error('refreshAll failed:', e);
  }
}

// ── System Status ──

function updateDataManagerPill(dmState) {
  const pill = $('#pill-data');
  if (!pill) return;
  const dot = pill.querySelector('.pill-dot');
  const label = pill.querySelector('.pill-label');
  const stateMap = {
    'Backfill': 'pill-dm-backfill',
    'Topup':    'pill-dm-topup',
    'Normal':   'pill-dm-normal',
    'Stopped':  'pill-dm-stopped',
  };
  const cls = stateMap[dmState] || 'pill-dm-stopped';
  pill.className = 'vital-pill ' + cls;
  if (label) label.textContent = 'Data : ' + (dmState || 'Stopped');
  if (dot) dot.className = 'pill-dot' + (['Backfill','Topup'].includes(dmState) ? ' pulsing' : '');

  const badge = $('.dm-state-badge');
  if (badge) {
    badge.className = `dm-state-badge dm-state-${dmState.toLowerCase()}`;
    badge.textContent = dmState;
  }
}

function updateSystemStatus(sys) {
  if (!sys) return;
  state.thinkerRunning = sys.thinker_running;
  state.traderRunning = sys.trader_running;
  if (sys.traders) state.tradersStatus = sys.traders;
  if (sys.data_manager_state) {
    state.dataManagerState = sys.data_manager_state;
    updateDataManagerPill(state.dataManagerState);
  } else if (!sys.data_manager_running) {
    state.dataManagerState = 'Stopped';
    updateDataManagerPill('Stopped');
  }

  const pillNeural = $('#pill-thinker');
  const pillTrader = $('#pill-trader');
  const pillMode = $('#pill-mode');

  pillNeural.className = 'vital-pill ' + (sys.thinker_running ? 'running' : 'stopped');
  pillTrader.className = 'vital-pill ' + (sys.trader_running ? 'running' : 'stopped');
  if (pillMode) {
    const isDemo = state.tradingMode === 'demo';
    pillMode.className = 'vital-pill ' + (isDemo ? 'mode-demo' : 'mode-trading');
    const label = pillMode.querySelector('.pill-label');
    if (label) label.textContent = isDemo ? 'Mode : Demo' : 'Mode : Trading';
  }

  const running = sys.thinker_running || sys.trader_running;
  const btnStart = $('#btn-start-all');
  const btnStop = $('#btn-stop-all');
  btnStart.style.display = running ? 'none' : '';
  btnStop.style.display = running ? '' : 'none';

  const hasPositions = Object.values(state.positions || {}).some(xkPos =>
    Object.values(xkPos || {}).some(p => p && p.quantity > 0)
  );

  const btnClose = $('#btn-close-all');
  if (btnClose) btnClose.disabled = !hasPositions;

  const btnTrainAll = $('#btn-train-all');
  if (btnTrainAll) {
    btnTrainAll.disabled = running;
    btnTrainAll.title = running ? 'Stop trader and thinker before training' : '';
  }

  const btnSync = $('#btn-sync-shadow');
  if (btnSync) {
    btnSync.disabled = sys.trader_running || hasPositions;
  }
}

// ── Topbar Account Display ──

function renderTopbarAccounts() {
  const container = $('#topbar-account');
  if (!container) return;
  const xks = state.exchangeList;

  let html = '<table class="xk-table"><thead><tr><th></th><th>Portfolio</th><th>Buying Power</th><th>Holdings</th><th>Inv %</th><th>Realized</th><th>Unrealized</th>';
  if (xks.length >= 2) html += '<th>Δ</th>';
  html += '</tr></thead><tbody>';

  xks.forEach((xk, i) => {
    const xd = state.exchangeData[xk] || {};
    const acct = xd.account || {};
    const pnl = xd.pnl || {};
    const total = acct.total_account_value || 0;
    const bp = acct.buying_power || 0;
    const holdings = acct.holdings_sell_value || 0;
    const pctInv = acct.percent_in_trade || 0;
    const realized = pnl.total_realized_profit_usd || 0;
    const unrealized = pnl.unrealized_profit_usd || 0;
    const rClass = realized >= 0 ? 'positive' : 'negative';
    const uClass = unrealized >= 0 ? 'positive' : 'negative';
    const color = xkColor(xk);

    html += `<tr>
      <td class="xk-table-name"><span class="xk-dot" style="background:${color}"></span>${xkDisplayName(xk)}</td>
      <td class="xk-table-val" data-xk-total="${xk}">${fmtUSD(total)}</td>
      <td class="xk-table-val" data-xk-bp="${xk}">${fmtUSD(bp)}</td>
      <td class="xk-table-val" data-xk-hold="${xk}">${fmtUSD(holdings)}</td>
      <td class="xk-table-val" data-xk-inv="${xk}">${pctInv.toFixed(1)}%</td>
      <td class="xk-table-val ${rClass}" data-xk-pnl="${xk}">${fmtSignedUSD2(realized)}</td>
      <td class="xk-table-val ${uClass}" data-xk-upnl="${xk}">${fmtSignedUSD2(unrealized)}</td>`;

    if (xks.length >= 2) {
      if (i === 0) {
        html += '<td class="xk-table-val xk-table-dash">—</td>';
      } else {
        const t0 = (state.exchangeData[xks[0]]?.account?.total_account_value || 0);
        const t1 = (state.exchangeData[xks[1]]?.account?.total_account_value || 0);
        const delta = t1 - t0;
        const dPct = t0 > 0 ? (delta / t0) * 100 : 0;
        const dClass = delta >= 0 ? 'positive' : 'negative';
        html += `<td class="xk-table-val ${dClass}" id="acct-delta">${fmtSignedUSD2(delta)}</td>`;
      }
    }
    html += '</tr>';
  });

  html += '</tbody></table>';
  container.innerHTML = html;
}

function updateTopbarExchange(xk, account, pnl) {
  if (account) {
    if (state.exchangeData[xk]) state.exchangeData[xk].account = account;
    const setEl = (attr, val) => { const el = $(`[${attr}="${xk}"]`); if (el) el.textContent = val; };
    setEl('data-xk-total', fmtUSD(account.total_account_value || 0));
    setEl('data-xk-bp', fmtUSD(account.buying_power || 0));
    setEl('data-xk-hold', fmtUSD(account.holdings_sell_value || 0));
    setEl('data-xk-inv', (account.percent_in_trade || 0).toFixed(1) + '%');
  }
  if (pnl) {
    if (state.exchangeData[xk]) state.exchangeData[xk].pnl = pnl;
    const v = pnl.total_realized_profit_usd || 0;
    const el = $(`[data-xk-pnl="${xk}"]`);
    if (el) {
      el.textContent = fmtSignedUSD2(v);
      el.className = 'xk-table-val ' + (v >= 0 ? 'positive' : 'negative');
    }
    const u = pnl.unrealized_profit_usd || 0;
    const uel = $(`[data-xk-upnl="${xk}"]`);
    if (uel) {
      uel.textContent = fmtSignedUSD2(u);
      uel.className = 'xk-table-val ' + (u >= 0 ? 'positive' : 'negative');
    }
  }

  if (state.exchangeList.length >= 2) {
    const xks = state.exchangeList;
    const t0 = (state.exchangeData[xks[0]]?.account?.total_account_value || 0);
    const t1 = (state.exchangeData[xks[1]]?.account?.total_account_value || 0);
    const delta = t1 - t0;
    const el = $('#acct-delta');
    if (el) {
      el.textContent = fmtSignedUSD2(delta);
      el.className = 'xk-table-val ' + (delta >= 0 ? 'positive' : 'negative');
    }
  }
}

function updatePnl(xk, pnl) {
  if (!pnl || !xk) return;
  updateTopbarExchange(xk, null, pnl);
}

// ── Trader Status (from WS) ──

function updateTraderStatus(xk, data) {
  if (!data || !xk) return;
  if (data.account) updateTopbarExchange(xk, data.account, null);
  if (data.positions) {
    if (!state.positions[xk]) state.positions[xk] = {};
    state.positions[xk] = data.positions;
    mergePositionsIntoCoins();
    renderCoinGrid();
    if ($('#tab-lth').classList.contains('active')) renderLTH();
    if (state.selectedCoin) {
      updateCoinPosition(state.selectedCoin);
      updateMidPriceLine();
    }
    if ($('#tab-compare').classList.contains('active')) loadCompare();
  }
}

// ── Coin Grid (unified signal + position cards) ──

let _lastCardMode = null;

function _createSimpleCard(coin) {
  const card = document.createElement('div');
  card.className = 'coin-card simple';
  card.dataset.coin = coin;
  card.innerHTML = `
    <span class="cc-left">
      <span class="cc-name">${coin}</span>
      <span class="cc-train-badge" data-f="train-badge"></span>
      <span class="cc-mid" data-f="mid"></span>
    </span>
    <span class="cc-right">
      <span class="cc-pos-fields" data-f="pos-fields"></span>
      <span class="cc-ls" title="Long / Short (0–7)">
        <span class="cc-long" data-f="long"></span><span class="cc-sep">/</span><span class="cc-short" data-f="short"></span>
      </span>
    </span>`;
  card.addEventListener('click', () => selectCoin(coin));
  return card;
}

function _createDetailCard(coin) {
  const card = document.createElement('div');
  card.className = 'coin-card detail';
  card.dataset.coin = coin;
  card.innerHTML = `
    <div class="cc-header">
      <div class="cc-name-col">
        <span class="cc-name">${coin}</span>
        <span class="cc-mid" data-f="mid"></span>
      </div>
      <div class="cc-bars" title="Signal strength (0–7) · Marker = trade start level">
        <div class="cc-bar-row"><span class="cc-bar-lbl">L</span><div class="signal-bar"><div class="signal-bar-fill long" data-f="long-bar"></div><div class="signal-bar-marker" data-f="marker"></div></div><span class="cc-long" data-f="long"></span></div>
        <div class="cc-bar-row"><span class="cc-bar-lbl">S</span><div class="signal-bar"><div class="signal-bar-fill short" data-f="short-bar"></div></div><span class="cc-short" data-f="short"></span></div>
      </div>
    </div>
    <div class="cc-position" data-f="pos-section"></div>`;
  card.addEventListener('click', () => selectCoin(coin));
  return card;
}

function _updateCard(card, c, modeOverride) {
  const mode = modeOverride || (card.classList.contains('detail') ? 'detail' : 'simple');
  const xks = state.exchangeList;
  const positions = c.positions || {};
  const hasAnyPos = xks.some(xk => positions[xk] && positions[xk].quantity > 0);
  const mid = c.mid_price || 0;
  const tsl = state.tradeStartLevel || 1;
  const isTradeReady = c.long_signal >= tsl && c.short_signal === 0;

  card.classList.toggle('active', state.selectedCoin === c.coin);
  card.classList.toggle('trade-ready', isTradeReady);
  card.classList.toggle('has-position', hasAnyPos);

  const f = key => card.querySelector(`[data-f="${key}"]`);

  f('mid').textContent = fmtPrice(mid);
  f('long').textContent = c.long_signal;
  f('short').textContent = c.short_signal;

  const badge = f('train-badge');
  if (badge) {
    if (c.training_running) {
      badge.textContent = 'TRAINING';
      badge.className = 'cc-train-badge training';
    } else if (!c.is_trained) {
      const isFailed = c.training_state === 'FAILED';
      const isRetrain = c.training_state === 'FINISHED';
      badge.textContent = isFailed ? 'FAILED' : isRetrain ? 'RETRAIN' : 'UNTRAINED';
      badge.className = 'cc-train-badge ' + (isFailed ? 'failed' : isRetrain ? 'retrain' : 'untrained');
    } else {
      badge.textContent = '';
      badge.className = 'cc-train-badge';
    }
  }

  if (mode === 'simple') {
    const container = f('pos-fields');
    if (hasAnyPos) {
      let html = '';
      xks.forEach(xk => {
        const pos = positions[xk];
        if (!pos || pos.quantity <= 0) return;
        const pnl = pos.gain_loss_pct_buy;
        const pnlClass = pnl >= 0 ? 'positive' : 'negative';
        const color = xkColor(xk);
        html += `<span class="cc-xk-group"><span class="cc-xk-tag" style="color:${color}">${xkShortLabel(xk)}</span><span class="cc-notional">${fmtUSD(pos.value_usd)}</span><span class="cc-pnl ${pnlClass}">${fmtPct(pnl)}</span></span>`;
      });
      container.innerHTML = html;
    } else {
      if (container.innerHTML !== '') container.innerHTML = '';
    }
  } else {
    const longPct = (c.long_signal / 7) * 100;
    const shortPct = (c.short_signal / 7) * 100;
    const markerPos = (tsl / 7) * 100;
    f('long-bar').style.width = longPct + '%';
    f('short-bar').style.width = shortPct + '%';
    f('marker').style.left = markerPos + '%';

    const posEl = f('pos-section');
    let posHtml = '';
    xks.forEach(xk => {
      const pos = positions[xk];
      if (!pos || pos.quantity <= 0) return;
      const pnl = pos.gain_loss_pct_buy;
      const pnlClass = pnl >= 0 ? 'positive' : 'negative';
      const color = xkColor(xk);
      const maxDca = state.cfg.max_dca_buys_per_24h || 1;
      const dca24 = (state.dca24h[xk] || {})[c.coin] || 0;
      const sellPrice = pos.trail_line > 0 ? fmtPrice(pos.trail_line) : '—';
      const totalDcaLevels = (state.cfg.dca_levels || []).length;
      const dcaChip = pos.dca_triggered_stages > 0 ? `<span class="pos-dca-chip">stg ${pos.dca_triggered_stages}/${totalDcaLevels}</span>` : '—';
      const nextDca = pos.next_dca_display ? `${pos.dca_line_price ? fmtPrice(pos.dca_line_price) + ' ' : ''}(${pos.next_dca_display})` : '—';
      const realExchangeFields = xk !== 'shadow' ? `
            <div class="cc-pf"><span class="cc-pf-l">Sell Level</span><span class="cc-pf-v">${sellPrice}</span></div>
            <div class="cc-pf"><span class="cc-pf-l">DCA</span><span class="cc-pf-v">${dcaChip} ${pos.trail_active ? '<span class="pos-trail-active">TRAILING</span>' : ''} <span class="cc-dca24">${dca24}/${maxDca} 24h</span></span></div>
            <div class="cc-pf"><span class="cc-pf-l">Next DCA</span><span class="cc-pf-v">${nextDca}</span></div>` : '';
      posHtml += `
        <div class="cc-xk-section">
          <div class="cc-pos-header">
            <span class="cc-xk-label" style="color:${color}">${xk}</span>
            <span class="cc-pos-value">${fmtUSD(pos.value_usd)}</span>
            <span class="cc-pos-pnl ${pnlClass}">${fmtPct(pnl)}</span>
            ${xk !== 'shadow' ? `<button class="cc-close-btn" onclick="event.stopPropagation(); closeCoinPosition('${c.coin}', '${xk}')" title="Close ${c.coin} on ${xk}">CLOSE</button>` : ''}
          </div>
          <div class="cc-pos-grid">
            <div class="cc-pf"><span class="cc-pf-l">Qty</span><span class="cc-pf-v">${fmtQty(pos.quantity, c.coin)}</span></div>
            <div class="cc-pf"><span class="cc-pf-l">Avg Cost</span><span class="cc-pf-v">${fmtPrice(pos.avg_cost_basis)}</span></div>
            <div class="cc-pf"><span class="cc-pf-l">Bid / Ask</span><span class="cc-pf-v">${fmtPrice(pos.current_sell_price)} / ${fmtPrice(pos.current_buy_price)}</span></div>${realExchangeFields}
          </div>
        </div>`;
    });
    if (posHtml) {
      posEl.style.display = '';
      posEl.innerHTML = posHtml;
    } else {
      posEl.style.display = 'none';
      if (posEl.innerHTML !== '') posEl.innerHTML = '';
    }
  }
}

function renderCoinGrid() {
  const grid = $('#signal-grid');
  const coins = state.coins;
  if (!coins.length) return;

  const mode = state.cardMode;
  const sorted = [...coins].sort((a, b) => a.coin.localeCompare(b.coin));
  const modeChanged = _lastCardMode !== mode;

  if (modeChanged) {
    _lastCardMode = mode;
    state._expandedCoin = null;
    grid.innerHTML = '';
    const creator = mode === 'simple' ? _createSimpleCard : _createDetailCard;
    sorted.forEach(c => grid.appendChild(creator(c.coin)));
  }

  const existingCards = {};
  $$('.coin-card', grid).forEach(c => { existingCards[c.dataset.coin] = c; });
  const expanded = mode === 'simple' ? state._expandedCoin : null;

  sorted.forEach(c => {
    let card = existingCards[c.coin];
    if (!card) {
      const isExpanded = expanded === c.coin;
      card = isExpanded ? _createDetailCard(c.coin) : (mode === 'simple' ? _createSimpleCard : _createDetailCard)(c.coin);
      grid.appendChild(card);
    }
    _updateCard(card, c);
    delete existingCards[c.coin];
  });

  Object.values(existingCards).forEach(c => c.remove());
}

function updateSignals(signals) {
  if (!signals) return;
  state.coins.forEach(coin => {
    const sig = signals[coin.coin];
    if (sig) {
      coin.long_signal = sig.long;
      coin.short_signal = sig.short;
      coin.long_price_levels = sig.long_prices;
      coin.short_price_levels = sig.short_prices;
    }
  });
  renderCoinGrid();
  if (state.selectedCoin) updateChartPriceLines();
}

function mergePositionsIntoCoins() {
  state.coins.forEach(coin => {
    if (!coin.positions) coin.positions = {};
    coin.skip_reasons = {};
    state.exchangeList.forEach(xk => {
      const xkPos = (state.positions[xk] || {})[coin.coin];
      coin.positions[xk] = (xkPos && xkPos.quantity > 0) ? xkPos : null;
      if (xkPos && xkPos.skip_reason) coin.skip_reasons[xk] = xkPos.skip_reason;
    });
  });
}

// ── Coin Selection & Chart ──

function selectCoin(coin) {
  const wasAccount = state.chartMode === 'account';
  state.selectedCoin = coin;
  state.chartMode = 'candle';

  if (state.cardMode === 'simple') {
    const grid = $('#signal-grid');
    const prev = state._expandedCoin;
    if (prev === coin) {
      const oldCard = grid.querySelector(`.coin-card[data-coin="${prev}"]`);
      if (oldCard) _replaceCard(grid, oldCard, prev, 'simple');
      state._expandedCoin = null;
      return;
    }
    if (prev) {
      const oldCard = grid.querySelector(`.coin-card[data-coin="${prev}"]`);
      if (oldCard) _replaceCard(grid, oldCard, prev, 'simple');
    }
    const curCard = grid.querySelector(`.coin-card[data-coin="${coin}"]`);
    if (curCard) _replaceCard(grid, curCard, coin, 'detail');
    state._expandedCoin = coin;
  }

  $$('.coin-card').forEach(c => c.classList.toggle('active', c.dataset.coin === coin));
  const pb = $('#acct-block-portfolio');
  if (pb) pb.classList.remove('active');

  $('#chart-coin-label').textContent = coin;
  $('#tf-selector').style.display = '';
  _setChartResetVisible(false);
  $('#panel-chart').classList.add('mobile-active');

  if (wasAccount) rebuildTimeframeButtons();

  loadChart(coin, state.selectedTf);
  updateCoinPosition(coin);

  state.historyFilterCoin = coin;
  _updateHistoryTabLabel();
  if ($('#tab-history').classList.contains('active')) loadTradeHistory();
}

function _updateHistoryTabLabel() {
  const btn = $('[data-tab="history"]');
  if (!btn) return;
  btn.textContent = state.historyFilterCoin ? `Trades: ${state.historyFilterCoin}` : 'Trades';
}

function _replaceCard(grid, oldCard, coin, mode) {
  const newCard = mode === 'simple' ? _createSimpleCard(coin) : _createDetailCard(coin);
  const c = state.coins.find(x => x.coin === coin);
  if (c) _updateCard(newCard, c, mode);
  grid.replaceChild(newCard, oldCard);
}

function selectAccountChart(hours) {
  state.chartMode = 'account';
  state.accountRange = hours != null ? hours : state.accountRange;
  if (!state.acctDisplayMode) state.acctDisplayMode = 'usd';

  if (state.cardMode === 'simple' && state._expandedCoin) {
    const grid = $('#signal-grid');
    const oldCard = grid.querySelector(`.coin-card[data-coin="${state._expandedCoin}"]`);
    if (oldCard) _replaceCard(grid, oldCard, state._expandedCoin, 'simple');
    state._expandedCoin = null;
  }
  state.selectedCoin = null;

  $$('.coin-card').forEach(c => c.classList.remove('active'));
  const pb = $('#acct-block-portfolio');
  if (pb) pb.classList.add('active');

  $('#chart-coin-label').textContent = 'PORTFOLIO';
  $('#tf-selector').style.display = 'none';
  _setChartResetVisible(false);
  $('#coin-position').classList.add('hidden');
  $('#panel-chart').classList.add('mobile-active');

  loadAccountChart(state.accountRange);
}

function _setChartResetVisible(v) {
  const btn = $('#btn-chart-reset');
  if (btn) btn.style.display = v ? '' : 'none';
}

async function showHistoricChart(coin, tfMinutes) {
  state.chartMode = 'historic';
  state.selectedCoin = null;
  _setChartResetVisible(true);

  if (_historicFetchTimer) { clearTimeout(_historicFetchTimer); _historicFetchTimer = null; }
  if (_historicFetchAbort) { _historicFetchAbort.abort(); _historicFetchAbort = null; }
  if (state.chartRefreshTimer) {
    clearInterval(state.chartRefreshTimer);
    state.chartRefreshTimer = null;
  }
  if (state.chartMarkersTimer) {
    clearInterval(state.chartMarkersTimer);
    state.chartMarkersTimer = null;
  }
  state._diffSeries = null;
  if (state.chart) {
    state.chart.remove();
    state.chart = null;
    state.candleSeries = null;
    state.priceLines = [];
    state._midPriceLine = null;
    state._currentBar = null;
  }

  $$('.coin-card').forEach(c => c.classList.remove('active'));
  const pb = $('#acct-block-portfolio');
  if (pb) pb.classList.remove('active');

  const tfLabel = TF_MINUTES_LABEL[tfMinutes] || `${tfMinutes}m`;
  $('#chart-coin-label').textContent = `${coin} · ${tfLabel} (Historic)`;
  $('#tf-selector').style.display = 'none';
  $('#chart-diff-container').classList.add('hidden');
  $('#chart-legend').classList.add('hidden');
  $('#coin-position').classList.add('hidden');
  $('#panel-chart').classList.add('mobile-active');

  const container = $('#chart-container');
  state.chart = LightweightCharts.createChart(container, {
    width: container.clientWidth,
    height: container.clientHeight,
    layout: {
      background: {type: 'solid', color: CHART_COLORS.bg},
      textColor: CHART_COLORS.text,
      fontFamily: "'Azeret Mono', monospace",
      fontSize: 10,
    },
    grid: {
      vertLines: {color: CHART_COLORS.grid},
      horzLines: {color: CHART_COLORS.grid},
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: {color: '#3A3A60', style: 2, width: 1},
      horzLine: {color: '#3A3A60', style: 2, width: 1},
    },
    timeScale: {
      borderColor: CHART_COLORS.border,
      timeVisible: true,
      secondsVisible: false,
    },
    rightPriceScale: {borderColor: CHART_COLORS.border},
  });

  state.candleSeries = state.chart.addCandlestickSeries({
    upColor: CHART_COLORS.up,
    downColor: CHART_COLORS.down,
    borderUpColor: CHART_COLORS.up,
    borderDownColor: CHART_COLORS.down,
    wickUpColor: CHART_COLORS.up,
    wickDownColor: CHART_COLORS.down,
  });

  try {
    const data = await api(`data-manager/chart/${coin}/${tfMinutes}`);
    if (data.candles && data.candles.length > 0) {
      state.candleSeries.setData(data.candles);
      const last = data.candles[data.candles.length - 1];
      const pp = pricePrecision(last.close);
      state.candleSeries.applyOptions({priceFormat: {type: 'price', ...pp}});
      state.chart.timeScale().fitContent();

      _updateHistoricLabel(coin, tfMinutes, data);

      // Subscribe to visible range changes for adaptive resolution
      state.chart.timeScale().subscribeVisibleTimeRangeChange(range => {
        if (!range || state.chartMode !== 'historic') return;
        clearTimeout(_historicFetchTimer);
        _historicFetchTimer = setTimeout(() => _fetchHistoricRange(coin, tfMinutes, range), 300);
      });
    }
  } catch (e) {
    console.error('Failed to load historic candles:', e);
  }
}

function _updateHistoricLabel(coin, tfMinutes, data) {
  const base = TF_MINUTES_LABEL[tfMinutes] || `${tfMinutes}m`;
  const eff = data.effective_minutes;
  const resampled = eff && eff !== tfMinutes;
  const suffix = resampled ? ` → ${_fmtMinutes(eff)}` : '';
  const rows = data.total_rows ? ` · ${data.total_rows.toLocaleString()} rows` : '';
  $('#chart-coin-label').textContent = `${coin} · ${base}${suffix} (Historic${rows})`;
}

async function _fetchHistoricRange(coin, tfMinutes, range) {
  if (_historicFetchAbort) _historicFetchAbort.abort();
  _historicFetchAbort = new AbortController();

  // Fetch the visible window plus 100% buffer on each side so panning
  // a short distance doesn't immediately trigger another request.
  const span = range.to - range.from;
  const start = Math.floor(range.from - span);
  const end   = Math.ceil(range.to   + span);

  try {
    const params = new URLSearchParams({start, end, limit: 1500});
    const data = await api(`data-manager/chart/${coin}/${tfMinutes}?${params}`,
                            {signal: _historicFetchAbort.signal});
    if (!data.candles || !data.candles.length || state.chartMode !== 'historic') return;

    const visibleRange = state.chart.timeScale().getVisibleRange();
    state.candleSeries.setData(data.candles);
    if (visibleRange) state.chart.timeScale().setVisibleRange(visibleRange);

    _updateHistoricLabel(coin, tfMinutes, data);
  } catch (e) {
    if (e.name !== 'AbortError') console.error('Historic range fetch failed:', e);
  }
}

function _fmtMinutes(m) {
  function fmt(v) { return Number.isInteger(v) ? String(v) : v.toFixed(1); }
  if (m < 60)    return `${m}m`;
  if (m < 1440)  return `${fmt(m / 60)}h`;
  if (m < 10080) return `${fmt(m / 1440)}d`;
  return `${fmt(m / 10080)}w`;
}

async function loadChart(coin, tf) {
  const container = $('#chart-container');
  $('#chart-legend').classList.add('hidden');
  $('#chart-diff-container').classList.add('hidden');

  if (_historicFetchTimer) { clearTimeout(_historicFetchTimer); _historicFetchTimer = null; }
  if (_historicFetchAbort) { _historicFetchAbort.abort(); _historicFetchAbort = null; }
  if (state.chartRefreshTimer) {
    clearInterval(state.chartRefreshTimer);
    state.chartRefreshTimer = null;
  }
  if (state.chartMarkersTimer) {
    clearInterval(state.chartMarkersTimer);
    state.chartMarkersTimer = null;
  }
  state._diffSeries = null;
  if (state.chart) {
    state.chart.remove();
    state.chart = null;
    state.candleSeries = null;
    state.priceLines = [];
    state._midPriceLine = null;
    state._currentBar = null;
  }

  state.chart = LightweightCharts.createChart(container, {
    width: container.clientWidth,
    height: container.clientHeight,
    layout: {
      background: {type: 'solid', color: CHART_COLORS.bg},
      textColor: CHART_COLORS.text,
      fontFamily: "'Azeret Mono', monospace",
      fontSize: 10,
    },
    grid: {
      vertLines: {color: CHART_COLORS.grid},
      horzLines: {color: CHART_COLORS.grid},
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: {color: '#3A3A60', style: 2, width: 1},
      horzLine: {color: '#3A3A60', style: 2, width: 1},
    },
    timeScale: {
      borderColor: CHART_COLORS.border,
      timeVisible: true,
      secondsVisible: false,
    },
    rightPriceScale: {
      borderColor: CHART_COLORS.border,
    },
  });

  state.candleSeries = state.chart.addCandlestickSeries({
    upColor: CHART_COLORS.up,
    downColor: CHART_COLORS.down,
    borderUpColor: CHART_COLORS.up,
    borderDownColor: CHART_COLORS.down,
    wickUpColor: CHART_COLORS.up,
    wickDownColor: CHART_COLORS.down,
  });

  try {
    const data = await api(`candles/${coin}?timeframe=${tf}&limit=${state.cfg.candles_limit || 300}`);
    if (data.candles && data.candles.length > 0) {
      state.candleSeries.setData(data.candles);
      const last = data.candles[data.candles.length - 1];
      state._currentBar = { ...last };
      const pp = pricePrecision(last.close);
      state.candleSeries.applyOptions({priceFormat: {type: 'price', ...pp}});
      state.chart.timeScale().fitContent();
    }
  } catch (e) {
    console.error('Failed to load candles:', e);
  }

  updateChartPriceLines();
  updateMidPriceLine();
  await updateChartTradeMarkers(coin);

  const refreshMs = (state.cfg.chart_refresh_seconds && state.cfg.chart_refresh_seconds * 1000) || TF_REFRESH_MS[tf] || 60_000;
  state.chartRefreshTimer = setInterval(async () => {
    if (!state.candleSeries || state.selectedCoin !== coin || state.selectedTf !== tf) return;
    try {
      const fresh = await api(`candles/${coin}?timeframe=${tf}&limit=2`);
      if (fresh.candles && fresh.candles.length) {
        fresh.candles.forEach(c => state.candleSeries.update(c));
        const last = fresh.candles[fresh.candles.length - 1];
        state._currentBar = { ...last };
      }
      updateChartPriceLines();
    } catch {}
  }, refreshMs);

  state.chartMarkersTimer = setInterval(() => {
    if (!state.candleSeries || state.selectedCoin !== coin || state.selectedTf !== tf) return;
    updateChartTradeMarkers(coin);
  }, 60_000);

  const resizeObserver = new ResizeObserver(() => {
    if (state.chart) {
      state.chart.applyOptions({
        width: container.clientWidth,
        height: container.clientHeight,
      });
    }
  });
  resizeObserver.observe(container);
}

async function loadAccountChart(hours) {
  const container = $('#chart-container');
  const diffContainer = $('#chart-diff-container');

  clearTimeout(state._acctRangeTimer);
  if (state._acctRangeAbort) { state._acctRangeAbort.abort(); state._acctRangeAbort = null; }
  if (state.chartRefreshTimer) { clearInterval(state.chartRefreshTimer); state.chartRefreshTimer = null; }
  if (state.chartMarkersTimer) { clearInterval(state.chartMarkersTimer); state.chartMarkersTimer = null; }
  if (state.chart) {
    state.chart.remove();
    state.chart = null;
    state.candleSeries = null;
    state.acctSeries = {};
    state.priceLines = [];
  }
  state._diffSeries = null;

  const isPct = state.acctDisplayMode === 'pct';
  const hasMulti = state.exchangeList.length >= 2;

  const _fmtUsd     = v => ('$' + v.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})).padStart(10);
  const _fmtPct     = v => ((v >= 0 ? '+' : '') + v.toFixed(2) + '%').padStart(8);
  const _fmtDiff    = v => ((v >= 0 ? '+$' : '-$') + Math.abs(v).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})).padStart(10);
  const _fmtDiffPct = v => ((v >= 0 ? '+' : '') + v.toFixed(2) + '%').padStart(8);

  state.chart = LightweightCharts.createChart(container, {
    width: container.clientWidth,
    height: container.clientHeight || 300,
    layout: {
      background: {type: 'solid', color: CHART_COLORS.bg},
      textColor: CHART_COLORS.text,
      fontFamily: "'Azeret Mono', monospace",
      fontSize: 10,
    },
    grid: {
      vertLines: {color: CHART_COLORS.grid},
      horzLines: {color: CHART_COLORS.grid},
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: {color: '#3A3A60', style: 2, width: 1},
      horzLine: {color: '#3A3A60', style: 2, width: 1},
    },
    timeScale: {borderColor: CHART_COLORS.border, timeVisible: true, secondsVisible: false},
    leftPriceScale: {visible: hasMulti, borderColor: CHART_COLORS.border, scaleMargins: {top: 0.15, bottom: 0.05}},
    rightPriceScale: {borderColor: CHART_COLORS.border},
    localization: {priceFormatter: isPct ? _fmtPct : _fmtUsd},
  });

  state._acctHidden = state._acctHidden || {};
  state.exchangeList.forEach(xk => {
    state.acctSeries[xk] = state.chart.addLineSeries({
      color: xkColor(xk), lineWidth: 2, title: '', priceScaleId: 'right',
      priceFormat: {type: 'price', precision: 2, minMove: 0.01},
      visible: !state._acctHidden[xk],
    });
  });

  state._acctMarkerSeries = state.chart.addLineSeries({
    color: 'transparent', lineWidth: 0, lastValueVisible: false,
    priceLineVisible: false, crosshairMarkerVisible: false, priceScaleId: 'right',
  });

  if (hasMulti) {
    diffContainer.classList.add('hidden');
    state._diffSeries = state.chart.addLineSeries({
      color: '#555570', lineWidth: 1, title: '', priceScaleId: 'left',
      priceFormat: {type: 'custom', formatter: isPct ? _fmtDiffPct : _fmtDiff, minMove: 0.01},
      lastValueVisible: true, priceLineVisible: false,
    });
    state._diffSeries.createPriceLine({price: 0, color: '#2A2A48', lineWidth: 1, lineStyle: 0, axisLabelVisible: false});
  } else {
    diffContainer.classList.add('hidden');
  }

  // Initial load
  await _acctApplyData(hours, null, null, null);
  state.chart.timeScale().fitContent();

  // Adaptive zoom handler. setData fires visibleTimeRangeChange asynchronously
  // (after the await in the timeout), so we unsubscribe before any data write and
  // re-subscribe after — the only reliable way to stop the event→fetch→event loop.
  let _rangeHandler = null;
  const _unsub = () => { if (_rangeHandler && state.chart) try { state.chart.timeScale().unsubscribeVisibleTimeRangeChange(_rangeHandler); } catch {} };
  const _resub  = () => { if (_rangeHandler && state.chart && state.chartMode === 'account') state.chart.timeScale().subscribeVisibleTimeRangeChange(_rangeHandler); };

  _rangeHandler = range => {
    if (!range || state.chartMode !== 'account') return;
    clearTimeout(state._acctRangeTimer);
    state._acctRangeTimer = setTimeout(async () => {
      if (state._acctRangeAbort) state._acctRangeAbort.abort();
      state._acctRangeAbort = new AbortController();
      const span = range.to - range.from;
      const visibleRange = state.chart?.timeScale().getVisibleRange();
      _unsub();
      await _acctApplyData(0, Math.floor(range.from - span), Math.ceil(range.to + span), state._acctRangeAbort.signal);
      if (visibleRange && state.chartMode === 'account' && state.chart) state.chart.timeScale().setVisibleRange(visibleRange);
      _resub();
    }, 300);
  };
  state.chart.timeScale().subscribeVisibleTimeRangeChange(_rangeHandler);

  // Resize: preserve zoom/pan
  const resizeObserver = new ResizeObserver(() => {
    if (!state.chart) return;
    const r = state.chart.timeScale().getVisibleRange();
    _unsub();
    state.chart.applyOptions({width: container.clientWidth, height: container.clientHeight});
    if (r) state.chart.timeScale().setVisibleRange(r);
    else state.chart.timeScale().fitContent();
    _resub();
  });
  resizeObserver.observe(container);

  // Controls: range buttons + $/% toggle
  const tfContainer = $('#tf-selector');
  tfContainer.style.display = '';
  tfContainer.innerHTML = '';
  ACCT_RANGES.forEach(r => {
    const btn = document.createElement('button');
    btn.className = 'tf-btn' + (r.hours === hours ? ' active' : '');
    btn.textContent = r.label;
    btn.addEventListener('click', () => selectAccountChart(r.hours));
    tfContainer.appendChild(btn);
  });
  const sep = document.createElement('span');
  sep.className = 'tf-sep';
  tfContainer.appendChild(sep);
  ['usd', 'pct'].forEach(mode => {
    const btn = document.createElement('button');
    btn.className = 'tf-btn' + (state.acctDisplayMode === mode ? ' active' : '');
    btn.textContent = mode === 'usd' ? '$' : '%';
    btn.addEventListener('click', () => { state.acctDisplayMode = mode; loadAccountChart(state.accountRange); });
    tfContainer.appendChild(btn);
  });

  _buildAccountLegend();

  // Timer refresh: preserve zoom/pan
  state.chartRefreshTimer = setInterval(async () => {
    if (state.chartMode !== 'account' || !state.chart) return;
    const r = state.chart.timeScale().getVisibleRange();
    _unsub();
    await _acctApplyData(state.accountRange, null, null, null);
    if (r && state.chart) state.chart.timeScale().setVisibleRange(r);
    _resub();
  }, (state.cfg.chart_refresh_seconds && state.cfg.chart_refresh_seconds * 1000) || 300_000);
}

function _buildAccountLegend() {
  const legend = $('#chart-legend');
  legend.classList.remove('hidden');
  legend.innerHTML = '';
  state.exchangeList.forEach(xk => {
    const color = xkColor(xk);
    const hidden = !!state._acctHidden[xk];
    const item = document.createElement('button');
    item.className = 'legend-item' + (hidden ? ' legend-hidden' : '');
    item.innerHTML = `<span class="legend-dot" style="background:${color}"></span><span class="legend-label">${xk}</span>`;
    item.addEventListener('click', () => {
      state._acctHidden[xk] = !state._acctHidden[xk];
      const series = state.acctSeries[xk];
      if (series) series.applyOptions({visible: !state._acctHidden[xk]});
      item.classList.toggle('legend-hidden');
    });
    legend.appendChild(item);
  });
  if (state._diffSeries) {
    const item = document.createElement('span');
    item.className = 'legend-item legend-static';
    const realXk = state.exchangeList.find(xk => xk !== 'shadow');
    const ctrlLabel = xkDisplayName('shadow');
    const realLabel = realXk ? xkDisplayName(realXk) : 'real';
    item.innerHTML = `<span class="legend-dot" style="background:#555570"></span><span class="legend-label">Δ ${realLabel} − ${ctrlLabel}</span>`;
    legend.appendChild(item);
  }
}

async function _acctApplyData(hours, start, end, signal) {
  try {
    const qs = (start !== null && end !== null) ? `?start=${start}&end=${end}` : hours > 0 ? `?hours=${hours}` : '';
    const opts = signal ? {signal} : {};
    const [histData, tradeData] = await Promise.all([
      api('account-history' + qs, opts),
      api('trades?limit=500', opts),
    ]);
    if (!histData.history || state.chartMode !== 'account') return;
    const histByXk = histData.history;
    const pct = state.acctDisplayMode === 'pct';

    const pointsByXk = {};
    state.exchangeList.forEach(xk => {
      const series = state.acctSeries[xk];
      const raw = histByXk[xk] || [];
      if (!series || !raw.length) return;
      const base = raw[0].total_account_value;
      const pts = raw.map(h => ({
        time: Math.floor(h.ts),
        value: pct ? (h.total_account_value - base) / base * 100 : h.total_account_value,
      }));
      series.setData(pts);
      pointsByXk[xk] = pts;
    });

    if (state._diffSeries && state.exchangeList.length >= 2) {
      const ctrlPts = pointsByXk['shadow'] || [];
      const realXk = state.exchangeList.find(xk => xk !== 'shadow');
      const realPts = realXk ? (pointsByXk[realXk] || []) : [];
      if (ctrlPts.length && realPts.length) {
        const ctrlMap = new Map(ctrlPts.map(p => [p.time, p.value]));
        state._diffSeries.setData(
          realPts.flatMap(p => { const cv = ctrlMap.get(p.time); return cv !== undefined ? [{time: p.time, value: p.value - cv}] : []; })
        );
      }
    }

    const realXk = state.exchangeList.find(xk => xk !== 'shadow');
    if (state._acctMarkerSeries && realXk) {
      const refRaw = histByXk[realXk] || [];
      if (refRaw.length) {
        const base = refRaw[0].total_account_value;
        state._acctMarkerSeries.setData(refRaw.map(h => ({
          time: Math.floor(h.ts),
          value: pct ? (h.total_account_value - base) / base * 100 : h.total_account_value,
        })));
        const markers = [];
        state.exchangeList.forEach(xk => {
          if (xk === 'shadow') return;
          ((tradeData.trades || {})[xk] || []).forEach(t => {
            const side = (t.side || '').toLowerCase();
            markers.push({
              time: Math.floor(t.ts),
              position: side === 'buy' ? 'belowBar' : 'aboveBar',
              color: side === 'buy' ? ((t.tag || '').toUpperCase() === 'DCA' ? '#A855F7' : '#FF4466') : '#00CC66',
              shape: side === 'buy' ? 'arrowUp' : 'arrowDown',
              text: (t.symbol || '').split('_')[0],
            });
          });
        });
        state._acctMarkerSeries.setMarkers(markers.sort((a, b) => a.time - b.time));
      }
    }
  } catch (e) {
    if (!signal || e.name !== 'AbortError') console.error('_acctApplyData failed:', e);
  }
}

async function updateChartTradeMarkers(coin) {
  if (!state.candleSeries || !coin) return;
  try {
    const data = await api(`coins/${coin}`);
    const allTrades = [];
    const tradesByXk = data.trades || {};
    state.exchangeList.forEach(xk => {
      if (xk === 'shadow') return;
      (tradesByXk[xk] || []).forEach(t => allTrades.push({...t, _xk: xk}));
    });

    if (allTrades.length === 0) { state.candleSeries.setMarkers([]); return; }

    const markers = allTrades.flatMap(t => {
      const side = (t.side || '').toLowerCase();
      if (side === 'skip') return [];
      const tag = (t.tag || '').toUpperCase();
      let label, color, shape, position;

      if (side === 'buy') {
        label = tag === 'DCA' ? 'DCA' : 'BUY';
        color = tag === 'DCA' ? '#A855F7' : '#FF4466';
        shape = 'arrowUp';
        position = 'belowBar';
      } else {
        label = 'SELL';
        color = '#00CC66';
        shape = 'arrowDown';
        position = 'aboveBar';
      }

      return [{ time: Math.floor(t.ts), position, color, shape, text: label }];
    });

    markers.sort((a, b) => a.time - b.time);
    state.candleSeries.setMarkers(markers);
  } catch {}
}

function updateChartPriceLines() {
  if (!state.candleSeries || !state.selectedCoin) return;

  state.priceLines.forEach(pl => {
    try { state.candleSeries.removePriceLine(pl); } catch {}
  });
  state.priceLines = [];

  const coinData = state.coins.find(c => c.coin === state.selectedCoin);
  if (!coinData) return;

  const longPrices = coinData.long_price_levels || [];
  const shortPrices = coinData.short_price_levels || [];

  longPrices.forEach((price, i) => {
    if (!price) return;
    const pl = state.candleSeries.createPriceLine({
      price: price,
      color: `rgba(0, 212, 255, ${0.3 + (i * 0.08)})`,
      lineWidth: 1,
      lineStyle: 2,
      axisLabelVisible: true,
      title: `N${i + 1}`,
    });
    state.priceLines.push(pl);
  });

  shortPrices.forEach((price, i) => {
    if (!price) return;
    const pl = state.candleSeries.createPriceLine({
      price: price,
      color: `rgba(255, 68, 102, ${0.2 + (i * 0.06)})`,
      lineWidth: 1,
      lineStyle: 2,
      axisLabelVisible: false,
    });
    state.priceLines.push(pl);
  });

  state.exchangeList.forEach(xk => {
    const pos = (state.positions[xk] || {})[state.selectedCoin];
    if (!pos || pos.quantity <= 0) return;
    const color = xkColor(xk);
    const label = xkShortLabel(xk);

    if (pos.avg_cost_basis) {
      const pl = state.candleSeries.createPriceLine({
        price: pos.avg_cost_basis, color, lineWidth: 1, lineStyle: 0,
        axisLabelVisible: true, title: `${label}:AVG`,
      });
      state.priceLines.push(pl);
    }
    if (pos.trail_line && pos.trail_line > 0) {
      const pl = state.candleSeries.createPriceLine({
        price: pos.trail_line, color: '#00CC66', lineWidth: 2, lineStyle: 0,
        axisLabelVisible: true, title: `${label}:SELL`,
      });
      state.priceLines.push(pl);
    }
    if (pos.dca_line_price) {
      const pl = state.candleSeries.createPriceLine({
        price: pos.dca_line_price, color: '#A855F7', lineWidth: 1, lineStyle: 2,
        axisLabelVisible: true, title: `${label}:DCA`,
      });
      state.priceLines.push(pl);
    }
  });
}

function updateMidPriceLine() {
  if (!state.candleSeries || !state.selectedCoin || state.chartMode !== 'candle') return;
  const coinData = state.coins.find(c => c.coin === state.selectedCoin);
  if (!coinData) return;
  const mid = coinData.mid_price || 0;
  if (!mid) return;

  if (state._midPriceLine) {
    try { state.candleSeries.removePriceLine(state._midPriceLine); } catch {}
    state._midPriceLine = null;
  }
  state._midPriceLine = state.candleSeries.createPriceLine({
    price: mid,
    color: '#00CC66',
    lineWidth: 1,
    lineStyle: 0,
    axisLabelVisible: true,
    title: 'MID',
  });
}

function updateCoinPosition(coin) {
  const container = $('#coin-position');
  const xks = state.exchangeList;
  const anyPos = xks.some(xk => {
    const pos = (state.positions[xk] || {})[coin];
    return pos && pos.quantity > 0;
  });

  if (!anyPos) {
    container.classList.add('hidden');
    return;
  }

  container.classList.remove('hidden');
  let html = '';
  xks.forEach(xk => {
    const pos = (state.positions[xk] || {})[coin];
    if (!pos || pos.quantity <= 0) return;
    const pnl = pos.gain_loss_pct_buy;
    const pnlClass = pnl >= 0 ? 'positive' : 'negative';
    const color = xkColor(xk);
    html += `
      <div class="pos-stat-group">
        <span class="pos-stat-xk" style="color:${color}">${xk}</span>
        <div class="pos-stat"><span class="pos-stat-label">Value</span><span class="pos-stat-value">${fmtUSD(pos.value_usd)}</span></div>
        <div class="pos-stat"><span class="pos-stat-label">P&L</span><span class="pos-stat-value ${pnlClass}">${fmtPct(pnl)}</span></div>
        <div class="pos-stat"><span class="pos-stat-label">Avg Cost</span><span class="pos-stat-value">${fmtPrice(pos.avg_cost_basis)}</span></div>
      </div>`;
  });
  container.innerHTML = html;
}

// ── Trade History Tab ──

let _tradeSortCol = 'ts';
let _tradeSortAsc = false;

function setupTradesDelegation() {
  $('#tab-history').addEventListener('click', e => {
    const th = e.target.closest('.trades-th[data-col]');
    if (th) {
      if (_tradeSortCol === th.dataset.col) _tradeSortAsc = !_tradeSortAsc;
      else { _tradeSortCol = th.dataset.col; _tradeSortAsc = true; }
      _renderTradesTable && _renderTradesTable();
      return;
    }
    const row = e.target.closest('.trades-row[data-coin]');
    if (row) selectCoin(row.dataset.coin);
  });
}

let _renderTradesTable = null;

async function loadTradeHistory() {
  const container = $('#history-list');
  const data = await api('trades?limit=200');
  if (!data.trades) return;

  let allTrades = [];
  state.exchangeList.forEach(xk => {
    (data.trades[xk] || []).forEach(t => allTrades.push({...t, _xk: xk}));
  });

  const filter = state.historyFilterCoin;
  if (filter) allTrades = allTrades.filter(t => (t.symbol || '').startsWith(filter + '_'));

  if (allTrades.length === 0) {
    container.innerHTML = '<div class="empty-state">No trades</div>';
    return;
  }

  const cols = [
    {key: 'ts',             label: 'Time'},
    {key: '_xk',           label: 'Exch'},
    {key: 'side',          label: 'Side'},
    {key: 'symbol',        label: 'Pair'},
    {key: 'notional_usd',  label: 'Value'},
    {key: 'pnl_pct',       label: 'PnL%'},
  ];

  function sortTrades(rows) {
    return [...rows].sort((a, b) => {
      let va = a[_tradeSortCol], vb = b[_tradeSortCol];
      if (va == null) va = _tradeSortAsc ? Infinity : -Infinity;
      if (vb == null) vb = _tradeSortAsc ? Infinity : -Infinity;
      if (typeof va === 'string') return _tradeSortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
      return _tradeSortAsc ? va - vb : vb - va;
    });
  }

  function renderRow(t) {
    const pair   = (t.symbol || '').replace('_', '/');
    const coin   = (t.symbol || '').split('_')[0];
    const isSell = t.side === 'sell';
    const hasPnl = isSell && t.pnl_pct != null;
    const pnlClass = hasPnl ? (t.pnl_pct >= 0 ? 'positive' : 'negative') : '';
    const tagHtml = t.tag ? `<span class="hist-tag ${t.tag}">${t.tag}</span>` : '';
    const xkC    = xkColor(t._xk);
    const rowCls = isSell ? 'trades-row trades-row-sell' : 'trades-row';

    const pnlCell = hasPnl
      ? `<span class="hist-pnl ${pnlClass}">${fmtPct(t.pnl_pct)}</span>
         ${t.realized_profit_usd != null ? `<span class="hist-pnl ${pnlClass}">${fmtSignedUSD2(t.realized_profit_usd)}</span>` : ''}`
      : (isSkip ? `<span class="hist-reason">${t.reason || ''}</span>` : '');

    return `<tr class="${rowCls}" data-coin="${coin}">
      <td class="trades-td hist-time">${fmtDateTime(t.ts)}</td>
      <td class="trades-td"><span class="hist-xk" style="color:${xkC}">${xkShortLabel(t._xk)}</span></td>
      <td class="trades-td"><span class="hist-side ${t.side}">${t.side}</span></td>
      <td class="trades-td">${pair} ${isSkip ? '' : `${fmtQty(t.qty, coin)} @ ${fmtPrice(t.price)}`} ${tagHtml}</td>
      <td class="trades-td hist-amount">${isSkip ? '' : fmtUSD(t.notional_usd)}</td>
      <td class="trades-td">${pnlCell}</td>
    </tr>`;
  }

  function renderTable() {
    const sorted = sortTrades(allTrades);
    const thHtml = cols.map(c => {
      const active = c.key === _tradeSortCol;
      const arrow  = active ? (_tradeSortAsc ? ' ▲' : ' ▼') : '';
      return `<th class="trades-th${active ? ' trades-th-active' : ''}" data-col="${c.key}">${c.label}${arrow}</th>`;
    }).join('');

    const existing = container.querySelector('table.trades-table');
    if (existing) {
      $$('.trades-th[data-col]', container).forEach(th => {
        const active = th.dataset.col === _tradeSortCol;
        th.classList.toggle('trades-th-active', active);
        const col = cols.find(c => c.key === th.dataset.col);
        th.textContent = col.label + (active ? (_tradeSortAsc ? ' ▲' : ' ▼') : '');
      });
      const tbody = existing.querySelector('tbody');
      if (tbody) tbody.innerHTML = sorted.map(renderRow).join('');
    } else {
      container.innerHTML = `<table class="trades-table">
        <thead><tr>${thHtml}</tr></thead>
        <tbody>${sorted.map(renderRow).join('')}</tbody>
      </table>`;
    }
  }

  _renderTradesTable = renderTable;
  renderTable();
}

// ── Compare Tab ──

function cmpVal(v) {
  if (v == null || isNaN(v)) return '—';
  return Number(v).toFixed(2);
}
function cmpPct(v) {
  if (v == null || isNaN(v)) return '—';
  const n = Number(v);
  return (n >= 0 ? '+' : '') + n.toFixed(2);
}

async function loadCompare() {
  const container = $('#compare-content');
  try {
    const data = await api('comparison');
    if (!data.coins) return;

    const xks = data.exchanges || state.exchangeList;
    const multi = xks.length >= 2;
    const c0 = xkColor(xks[0]);
    const c1 = multi ? xkColor(xks[1]) : c0;

    let html = '<table class="compare-table"><thead>';
    html += '<tr class="compare-hdr-top"><th rowspan="2">Coin</th>';
    html += '<th colspan="2">Value $</th><th colspan="2">P&L %</th>';
    if (multi) html += '<th colspan="2">Δ</th><th rowspan="2">Fees $</th>';
    html += '</tr><tr class="compare-hdr-sub">';
    html += `<th style="color:${c0}">${xks[0]}</th><th style="color:${c1}">${multi ? xks[1] : ''}</th>`;
    html += `<th style="color:${c0}">${xks[0]}</th><th style="color:${c1}">${multi ? xks[1] : ''}</th>`;
    if (multi) html += '<th>$</th><th>%</th>';
    html += '</tr></thead><tbody>';

    data.coins.forEach(row => {
      const hasPos = xks.some(xk => (row[xk]?.value_usd || 0) > 0) || row.coin === 'USDT';
      const dimClass = hasPos ? '' : ' compare-dim';
      html += `<tr class="${dimClass}"><td class="compare-coin">${row.coin}</td>`;
      xks.forEach(xk => {
        const d = row[xk] || {};
        html += `<td>${cmpVal(d.value_usd)}</td>`;
      });
      xks.forEach(xk => {
        const d = row[xk] || {};
        const pnlClass = hasPos ? ((d.pnl_pct || 0) >= 0 ? 'positive' : 'negative') : '';
        html += `<td class="${pnlClass}">${cmpPct(d.pnl_pct)}</td>`;
      });
      if (multi) {
        const d0 = row[xks[0]] || {};
        const d1 = row[xks[1]] || {};
        const dVal = (d1.value_usd || 0) - (d0.value_usd || 0);
        const dvClass = hasPos ? (dVal >= 0 ? 'positive' : 'negative') : '';
        const dPnl = (d1.pnl_pct || 0) - (d0.pnl_pct || 0);
        const dpClass = hasPos ? (dPnl >= 0 ? 'positive' : 'negative') : '';
        html += `<td class="${dvClass}">${cmpVal(dVal)}</td>`;
        html += `<td class="${dpClass}">${cmpPct(dPnl)}</td>`;
        html += `<td>${cmpVal(d1.total_fees)}</td>`;
      }
      html += '</tr>';
    });

    if (data.totals && multi) {
      const t0 = data.totals[xks[0]] || {};
      const t1 = data.totals[xks[1]] || {};
      html += `<tr class="compare-totals"><td>TOTAL</td>`;
      html += '<td>—</td><td>—</td>';
      xks.forEach(xk => {
        const t = data.totals[xk] || {};
        const rClass = (t.realized_profit || 0) >= 0 ? 'positive' : 'negative';
        html += `<td class="${rClass}">${cmpVal(t.realized_profit)}</td>`;
      });
      const delta = (t1.realized_profit || 0) - (t0.realized_profit || 0);
      const dClass = delta >= 0 ? 'positive' : 'negative';
      html += '<td>—</td>';
      html += `<td class="${dClass}">${cmpVal(delta)}</td>`;
      html += `<td>${cmpVal(t1.total_fees)}</td>`;
      html += '</tr>';
    }

    html += '</tbody></table>';
    container.innerHTML = html;
  } catch (e) {
    container.innerHTML = '<div class="empty-state">Failed to load comparison</div>';
  }
}

// ── LTH Tab ──

function renderLTH() {
  const container = $('#lth-list');

  // Aggregate LTH holdings from trade-history ledger across all exchanges
  const merged = {};
  for (const xk of state.exchangeList) {
    for (const [coin, h] of Object.entries((state.lth || {})[xk] || {})) {
      if (h.qty > 0) {
        const prev = merged[coin];
        if (prev) {
          prev.qty += h.qty;
          prev.cost_usd += h.cost_usd;
          prev.trades += h.trades;
        } else {
          merged[coin] = {...h, xk};
        }
      }
    }
  }

  // Look up current prices from positions data
  for (const [coin, h] of Object.entries(merged)) {
    h.price = 0;
    for (const xk of state.exchangeList) {
      const p = (state.positions[xk] || {})[coin];
      if (p && p.current_buy_price > 0) { h.price = p.current_buy_price; break; }
    }
  }

  const lthCoins = Object.entries(merged);
  if (lthCoins.length === 0) {
    container.innerHTML = '<div class="empty-state">No long-term holdings</div>';
    return;
  }

  lthCoins.sort((a, b) => (b[1].qty * b[1].price) - (a[1].qty * a[1].price));

  container.innerHTML = lthCoins.map(([coin, h]) => {
    const value = h.qty * h.price;
    const avgEntry = h.qty > 0 ? h.cost_usd / h.qty : 0;
    const pnl = h.price > 0 ? value - h.cost_usd : 0;
    const pnlPct = h.cost_usd > 0 ? (pnl / h.cost_usd) * 100 : 0;
    const pnlColor = pnl >= 0 ? 'var(--green, #0c6)' : 'var(--red, #f46)';
    return `
      <div class="pos-card">
        <div class="pos-card-header">
          <span class="pos-coin">${coin}</span>
          <span class="pos-pnl" style="color: ${pnlColor}">${fmtSignedUSD2(pnl)} (${fmtPct(pnlPct)})</span>
        </div>
        <div class="pos-field">
          <span class="pos-field-label">Quantity</span>
          <span class="pos-field-value">${fmtQty(h.qty, coin)}</span>
        </div>
        <div class="pos-field">
          <span class="pos-field-label">Value</span>
          <span class="pos-field-value">${fmtUSD(value)}</span>
        </div>
        <div class="pos-field">
          <span class="pos-field-label">Avg Entry</span>
          <span class="pos-field-value">${fmtPrice(avgEntry)}</span>
        </div>
        <div class="pos-field">
          <span class="pos-field-label">Buys</span>
          <span class="pos-field-value">${h.trades}</span>
        </div>
      </div>
    `;
  }).join('');
}

// ── Training Tab ──

async function loadAndRenderTraining() {
  try {
    const data = await api('coins');
    if (data.coins) renderTraining(data.coins);
  } catch (e) {
    console.error('loadAndRenderTraining failed:', e);
  }
}

function renderTraining(coins) {
  const container = $('#training-list');
  container.querySelectorAll('.train-log-panel').forEach(el => {
    if (el.dataset.timer) clearInterval(Number(el.dataset.timer));
  });

  const locked = state.thinkerRunning || state.traderRunning;
  const anyTraining = (coins || []).some(c => c.training_running);
  const header = $('#training-header');
  if (header) {
    const stateLabel = anyTraining ? 'Training' : (locked ? 'Locked' : 'Idle');
    const stateClass = anyTraining ? 'backfill' : (locked ? 'stopped' : 'normal');
    header.innerHTML = `
      <div class="dm-status">
        <span class="dm-state-badge dm-state-${stateClass}">${stateLabel}</span>
      </div>
      <div class="dm-controls">
        <button class="btn btn-primary btn-small" id="btn-train-all-tab"
          ${locked ? `disabled title="Stop trader and thinker before training"` : ''}>
          Train All
        </button>
      </div>`;
    const btn = $('#btn-train-all-tab');
    if (btn) btn.addEventListener('click', async () => {
      await apiPost('train-all');
    });
  }

  if (!coins || coins.length === 0) {
    container.innerHTML = '<div class="empty-state">No coins configured</div>';
    return;
  }
  const trainDisabled = locked ? 'disabled title="Stop trader and thinker before training"' : '';

  const _needsAttention = c => !c.is_trained && (c.training_state === 'FAILED' || c.training_state === 'FINISHED');
  const sorted = [...coins].sort((a, b) => {
    const aN = _needsAttention(a) ? 0 : 1;
    const bN = _needsAttention(b) ? 0 : 1;
    if (aN !== bN) return aN - bN;
    return a.coin.localeCompare(b.coin);
  });

  container.innerHTML = sorted.map(c => {
    const rawState = c.training_running ? 'TRAINING' : (c.training_state || 'UNKNOWN');
    const trained = !c.training_running && c.is_trained;
    const tState = trained ? 'TRAINED' : (rawState === 'FINISHED' ? 'RETRAIN' : rawState);
    const lastTs = c.last_trained_ts;
    const ageText = lastTs > 0 ? fmtDate(lastTs) : 'Never';
    const fail = c.training_running ? null : c.training_failure;

    let failHtml = '';
    if (fail && fail.exception_type) {
      const esc = s => String(s).replace(/</g, '&lt;').replace(/>/g, '&gt;');
      const utc = ts => ts ? new Date(ts * 1000).toISOString().replace('T', ' ').replace(/\.\d+Z/, ' UTC') : '—';
      const tsKeys = new Set(['start_time', 'end_time']);
      const fmtVal = (k, v) => tsKeys.has(k) && /^\d{9,}$/.test(String(v)) ? utc(Number(v)) : esc(v);
      const stateRows = fail.trainer_state ? Object.entries(fail.trainer_state)
        .filter(([, v]) => !Array.isArray(v))
        .map(([k, v]) => `<tr><td class="fail-key">${esc(k)}</td><td>${fmtVal(k, v)}</td></tr>`)
        .join('') : '';

      failHtml = `
        <div class="train-failure" onclick="this.classList.toggle('open')">
          <div class="train-failure-summary">${esc(fail.exception_type)}: ${esc(fail.exception_message || '')}</div>
          <div class="train-failure-detail">
            <table class="fail-table">
              <tr><td class="fail-key">Failed at</td><td>${utc(fail.timestamp)}</td></tr>
              <tr><td class="fail-key">Started at</td><td>${utc(fail.started_at)}</td></tr>
              ${stateRows}
            </table>
            <div class="fail-tb-label">Traceback</div>
            <pre class="train-failure-tb">${esc(fail.traceback || '')}</pre>
          </div>
        </div>`;
    }

    return `
      <div class="train-row" data-train-coin="${c.coin}">
        <span class="train-coin">${c.coin}</span>
        <div>
          <span style="font-family: var(--font-mono); font-size: 10px; color: var(--text-muted)">
            ${ageText}
          </span>
        </div>
        <div class="train-actions">
          <span class="train-status ${tState}">${tState}</span>
          <button class="btn btn-small btn-secondary" onclick="toggleTrainerLog('${c.coin}')">Log</button>
          <button class="btn btn-small btn-secondary train-btn" ${trainDisabled} onclick="trainCoin('${c.coin}')">Train</button>
        </div>
      </div>${failHtml}
    `;
  }).join('');
}

function updateTrainingBadges(coins) {
  if (!coins) return;
  const locked = state.thinkerRunning || state.traderRunning;
  for (const c of coins) {
    const row = document.querySelector(`[data-train-coin="${c.coin}"]`);
    if (!row) continue;
    const badge = row.querySelector('.train-status');
    if (badge) {
      const rawState = c.training_running ? 'TRAINING' : (c.training_state || 'UNKNOWN');
      const trained = !c.training_running && c.is_trained;
      const tState = trained ? 'TRAINED' : (rawState === 'FINISHED' ? 'RETRAIN' : rawState);
      badge.className = 'train-status ' + tState;
      badge.textContent = tState;
    }
    const trainBtn = row.querySelector('.train-btn');
    if (trainBtn) {
      trainBtn.disabled = locked;
      trainBtn.title = locked ? 'Stop trader and thinker before training' : '';
    }
  }
}

window.closeCoinPosition = async function(coin, xk) {
  if (!confirm(`Close ${coin} position on ${xk}?`)) return;
  const btn = event.target;
  btn.disabled = true;
  const res = await apiPost(`close-coin/${coin}/${xk}`);
  if (res && !res.ok) alert(res.error || 'Close failed');
  setTimeout(refreshAll, 1000);
  setTimeout(() => { btn.disabled = false; }, 3000);
};

window.trainCoin = async function(coin) {
  await apiPost(`train/${coin}`);
  setTimeout(loadAndRenderTraining, 1000);
};

window.toggleTrainerLog = function(coin) {
  const id = `train-log-${coin}`;
  const existing = document.getElementById(id);
  if (existing) {
    const timer = existing.dataset.timer;
    if (timer) clearInterval(Number(timer));
    existing.remove();
    return;
  }

  const row = document.querySelector(`[data-train-coin="${coin}"]`);
  if (!row) return;

  const logEl = document.createElement('div');
  logEl.id = id;
  logEl.className = 'train-log-panel';
  logEl.innerHTML = '<pre class="train-log-output">Loading...</pre>';
  row.after(logEl);

  const refresh = async () => {
    try {
      const data = await api(`logs/trainer-${coin.toLowerCase()}`);
      const pre = logEl.querySelector('pre');
      if (pre) {
        const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
        pre.textContent = (data.lines || []).join('\n') || '(no output)';
        if (atBottom) pre.scrollTop = pre.scrollHeight;
      }
    } catch {}
  };

  refresh();
  const timer = setInterval(refresh, 3000);
  logEl.dataset.timer = timer;
};

// ── Config Tab ──

const _CFG_GROUP_ORDER = [
  'General', 'Trading', 'Trailing Profit', 'Long-Term Holdings',
  'Control Exchange', 'UI Preferences', 'Notifications', 'Training', 'Startup', 'Data Manager',
];

function _cfgParseField(el, rule, key) {
  if (key === 'trading_mode') {
    const checked = $('input[name="trading_mode"]:checked', el);
    return checked ? checked.value : rule.options[0];
  }
  if (rule.type === 'bool') return el.checked;
  if (rule.type === 'int') return parseInt(el.value, 10);
  if (rule.type === 'float') return parseFloat(el.value);
  if (rule.type === 'list_float') {
    return el.value.split(',').map(s => parseFloat(s.trim())).filter(s => s === 0 || !isNaN(s));
  }
  if (rule.type === 'list_str') {
    return el.value.split(',').map(s => s.trim()).filter(Boolean);
  }
  return el.value;
}

function _cfgValidateField(key, value, rule) {
  if (rule.type === 'int') {
    if (!Number.isFinite(value) || !Number.isInteger(value)) return 'Must be a whole number';
    if (rule.min !== undefined && value < rule.min) return `Min ${rule.min}`;
    if (rule.max !== undefined && value > rule.max) return `Max ${rule.max}`;
  } else if (rule.type === 'float') {
    if (!Number.isFinite(value)) return 'Must be a number';
    if (rule.min !== undefined && value < rule.min) return `Min ${rule.min}`;
    if (rule.max !== undefined && value > rule.max) return `Max ${rule.max}`;
  } else if (rule.type === 'list_float') {
    if (!Array.isArray(value) || value.some(v => !Number.isFinite(v))) return 'Comma-separated numbers required';
    if (rule.min_len && value.length < rule.min_len) return `At least ${rule.min_len} value(s) required`;
    if (rule.each_max !== undefined && value.some(v => v > rule.each_max)) return `All values must be ≤ ${rule.each_max}`;
  } else if (rule.type === 'list_str') {
    if (rule.min_len && value.length < rule.min_len) return `At least ${rule.min_len} value(s) required`;
  } else if (rule.type === 'enum') {
    if (rule.options && !rule.options.includes(value)) return `Must be one of: ${(rule.options || []).join(', ')}`;
  }
  return null;
}

function _cfgReadForm() {
  const schema = state.cfgSchema || {};
  const patch = {};
  for (const [key, rule] of Object.entries(schema)) {
    if (!rule.group) continue;
    const el = $(`#cfg-field-${key}`);
    if (!el) continue;
    patch[key] = _cfgParseField(el, rule, key);
  }
  return patch;
}

function _cfgCheckDirty(original) {
  const schema = state.cfgSchema || {};
  const btn = $('#btn-save-cfg');
  if (!btn) return;

  let dirty = false;
  let allValid = true;

  for (const [key, rule] of Object.entries(schema)) {
    if (!rule.group) continue;
    const el = $(`#cfg-field-${key}`);
    if (!el) continue;

    const val = _cfgParseField(el, rule, key);
    const err = _cfgValidateField(key, val, rule);

    const fieldEl = el.closest('.settings-field');
    const errEl = fieldEl && fieldEl.querySelector('.settings-field-error');

    if (err) {
      allValid = false;
      el.classList.add('cfg-input-error');
      if (errEl) errEl.textContent = err;
    } else {
      el.classList.remove('cfg-input-error');
      if (errEl) errEl.textContent = '';
    }

    const orig = original[key];
    if (rule.type === 'list_float' || rule.type === 'list_str') {
      if (JSON.stringify(orig) !== JSON.stringify(val)) dirty = true;
    } else {
      if (orig !== val) dirty = true;
    }
  }

  btn.disabled = !(dirty && allValid);
}

function _cfgBuildInput(key, rule, value) {
  const id = `cfg-field-${key}`;

  // Special: exchange — dropdown of discovered real exchanges, greyed in demo mode
  if (key === 'exchange') {
    const isDemo = state.tradingMode === 'demo';
    const discovered = state.discoveredExchanges;
    const opts = ['', ...discovered].map(xk =>
      `<option value="${xk}" ${value === xk ? 'selected' : ''}>${xk ? xk.charAt(0).toUpperCase() + xk.slice(1) : '(none)'}</option>`
    ).join('');
    return `<div class="settings-field" id="cfg-field-exchange-wrap"${isDemo ? ' style="opacity:0.4;pointer-events:none"' : ''}>
      <label for="${id}">${rule.label}</label>
      <select id="${id}" ${isDemo ? 'disabled' : ''}>${opts}</select>
      ${rule.hint ? `<div class="settings-field-hint">${rule.hint}</div>` : ''}
      <div class="settings-field-error"></div>
    </div>`;
  }

  // Special: shadow_sync_exchange — render as dropdown of discovered exchanges
  if (key === 'shadow_sync_exchange') {
    const discovered = state.discoveredExchanges;
    const opts = ['', ...discovered].map(xk =>
      `<option value="${xk}" ${value === xk ? 'selected' : ''}>${xk || '(use configured exchange)'}</option>`
    ).join('');
    return `<div class="settings-field">
      <label for="${id}">${rule.label}</label>
      <select id="${id}">${opts}</select>
      ${rule.hint ? `<div class="settings-field-hint">${rule.hint}</div>` : ''}
      <div class="settings-field-error"></div>
    </div>`;
  }

  if (rule.ui_widget === 'radio') {
    const locked = state.traderRunning;
    const opts = (rule.options || []).map(o =>
      `<label class="cfg-radio-opt${locked ? ' cfg-radio-locked' : ''}">
        <input type="radio" name="${key}" value="${o}" ${value === o ? 'checked' : ''} ${locked ? 'disabled' : ''}>
        <span>${o.charAt(0).toUpperCase() + o.slice(1)}</span>
      </label>`
    ).join('');
    return `<div class="settings-field">
      <label>${rule.label}</label>
      <div class="cfg-radio-group" id="${id}">${opts}</div>
      ${locked ? `<div class="settings-field-hint cfg-mode-locked">Stop all traders to change mode.</div>` : ''}
      ${rule.hint ? `<div class="settings-field-hint">${rule.hint}</div>` : ''}
      <div class="settings-field-error"></div>
    </div>`;
  }
  if (rule.type === 'bool') {
    return `<div class="settings-field settings-field-toggle">
      <input type="checkbox" id="${id}" ${value ? 'checked' : ''}>
      <label for="${id}">${rule.label}</label>
      ${rule.hint ? `<div class="settings-field-hint settings-field-hint-toggle">${rule.hint}</div>` : ''}
    </div>`;
  }
  if (rule.type === 'enum') {
    const opts = (rule.options || []).map(o =>
      `<option value="${o}" ${value === o ? 'selected' : ''}>${o}</option>`
    ).join('');
    return `<div class="settings-field">
      <label for="${id}">${rule.label}</label>
      <select id="${id}">${opts}</select>
      ${rule.hint ? `<div class="settings-field-hint">${rule.hint}</div>` : ''}
      <div class="settings-field-error"></div>
    </div>`;
  }
  if (rule.type === 'int' || rule.type === 'float') {
    const attrs = [`type="number"`, `id="${id}"`, `value="${value ?? ''}"`];
    if (rule.min !== undefined) attrs.push(`min="${rule.min}"`);
    if (rule.max !== undefined) attrs.push(`max="${rule.max}"`);
    attrs.push(rule.type === 'int' ? 'step="1"' : 'step="any"');
    return `<div class="settings-field">
      <label for="${id}">${rule.label}</label>
      <input ${attrs.join(' ')}>
      ${rule.hint ? `<div class="settings-field-hint">${rule.hint}</div>` : ''}
      <div class="settings-field-error"></div>
    </div>`;
  }
  // list_str, list_float, str
  const displayVal = Array.isArray(value) ? value.join(', ') : (value ?? '');
  return `<div class="settings-field">
    <label for="${id}">${rule.label}</label>
    <input type="text" id="${id}" value="${displayVal}">
    ${rule.hint ? `<div class="settings-field-hint">${rule.hint}</div>` : ''}
    <div class="settings-field-error"></div>
  </div>`;
}

function renderConfig(cfg) {
  if (!cfg) return;
  const schema = state.cfgSchema || {};
  const form = $('#settings-form');

  // Group fields by group, maintaining canonical order
  const grouped = {};
  for (const g of _CFG_GROUP_ORDER) grouped[g] = [];
  for (const [key, rule] of Object.entries(schema)) {
    if (rule.group && grouped[rule.group]) grouped[rule.group].push(key);
  }

  let html = '';
  for (const group of _CFG_GROUP_ORDER) {
    const fields = grouped[group];
    if (!fields || !fields.length) continue;
    html += `<div class="settings-group"><div class="settings-group-title">${group}</div>`;
    if (group === 'Notifications') {
      html += _cfgBuildInput('notifications_enabled', schema['notifications_enabled'], cfg['notifications_enabled']);
      const bodyDisabled = !cfg['notifications_enabled'];
      html += `<div id="notifications-body" style="${bodyDisabled ? 'opacity:0.4;pointer-events:none' : ''}">`;
      for (const key of fields.filter(k => k !== 'notifications_enabled')) {
        html += _cfgBuildInput(key, schema[key], cfg[key]);
      }
      html += `<div class="settings-field"><button type="button" class="btn btn-secondary" id="btn-test-notify">Send test notification</button><span id="notify-test-status" style="margin-left:10px;font-size:12px;color:var(--text-muted)"></span></div>`;
      html += '</div>';
    } else {
      for (const key of fields) {
        html += _cfgBuildInput(key, schema[key], cfg[key]);
      }
    }
    html += '</div>';
  }
  html += `<div class="settings-save">
    <button class="btn btn-primary" id="btn-save-cfg" disabled>Save Config</button>
  </div>`;

  form.innerHTML = html;

  const original = {...cfg};
  form.addEventListener('input', () => _cfgCheckDirty(original));
  form.addEventListener('change', () => {
    _cfgCheckDirty(original);
    // When notifications_enabled toggles, grey the body
    const notifyToggle = $('#cfg-field-notifications_enabled');
    const notifyBody = $('#notifications-body');
    if (notifyToggle && notifyBody) {
      notifyBody.style.opacity = notifyToggle.checked ? '' : '0.4';
      notifyBody.style.pointerEvents = notifyToggle.checked ? '' : 'none';
    }
    // When trading_mode changes, toggle exchange dropdown disabled state
    const modeEl = $('#cfg-field-trading_mode');
    const wrap = $('#cfg-field-exchange-wrap');
    if (modeEl && wrap) {
      const checkedMode = $('input[name="trading_mode"]:checked', modeEl);
      const isDemo = checkedMode ? checkedMode.value === 'demo' : true;
      wrap.style.opacity = isDemo ? '0.4' : '';
      wrap.style.pointerEvents = isDemo ? 'none' : '';
      const sel = $('#cfg-field-exchange', wrap);
      if (sel) sel.disabled = isDemo;
    }
  });

  $('#btn-save-cfg').addEventListener('click', () => saveConfig(original));

  const btnTest = $('#btn-test-notify');
  if (btnTest) {
    btnTest.addEventListener('click', async () => {
      const statusEl = $('#notify-test-status');
      btnTest.disabled = true;
      if (statusEl) statusEl.textContent = 'Sending…';
      try {
        const r = await api('notify/test', {method: 'POST'});
        if (statusEl) statusEl.textContent = r.ok ? '✓ Sent' : `Error: ${r.error}`;
      } catch (e) {
        if (statusEl) statusEl.textContent = `Error: ${e.message}`;
      } finally {
        btnTest.disabled = false;
      }
    });
  }
}

async function loadAndRenderConfig() {
  try {
    const cfg = await api('config');
    state.cfg = cfg;
    renderConfig(cfg);
  } catch (e) {
    console.error('loadAndRenderConfig failed:', e);
  }
}

function _confirmModeSwitch(fromMode, toMode) {
  return new Promise(resolve => {
    const dialog = $('#mode-switch-dialog');
    const title = dialog.querySelector('.msd-title');
    const body = dialog.querySelector('.msd-body');
    const confirmBtn = dialog.querySelector('.msd-confirm');
    const cancelBtn = dialog.querySelector('.msd-cancel');

    const toLabel = toMode.charAt(0).toUpperCase() + toMode.slice(1);
    title.textContent = `Switch to ${toLabel} Mode?`;
    confirmBtn.textContent = `Switch to ${toLabel}`;

    if (toMode === 'trading') {
      body.innerHTML = `
        <p>Activating <strong>Trading mode</strong> connects your real exchange accounts. <strong>Real capital will be at risk.</strong></p>
        <p>Your live trade ledger, account balance, and order history become active immediately.</p>
        <p class="msd-note">Your Demo account and history remain safely stored and untouched.</p>`;
    } else {
      body.innerHTML = `
        <p>Switching to <strong>Demo mode</strong> activates a paper trading account with simulated capital.</p>
        <p>No real orders will be placed. Your live exchange accounts are paused — existing positions remain open on the exchange but the bot will not trade them.</p>
        <p class="msd-note">Your Trading account and history remain safely stored and untouched.</p>`;
    }

    function onConfirm() { cleanup(); dialog.close(); resolve(true); }
    function onCancel()  { cleanup(); dialog.close(); resolve(false); }
    function cleanup() {
      confirmBtn.removeEventListener('click', onConfirm);
      cancelBtn.removeEventListener('click', onCancel);
    }
    confirmBtn.addEventListener('click', onConfirm);
    cancelBtn.addEventListener('click', onCancel);
    dialog.showModal();
  });
}

async function saveConfig(original) {
  const patch = _cfgReadForm();
  const btn = $('#btn-save-cfg');
  btn.disabled = true;

  const modeChanged = patch.trading_mode && patch.trading_mode !== original.trading_mode;
  if (modeChanged) {
    const confirmed = await _confirmModeSwitch(original.trading_mode, patch.trading_mode);
    if (!confirmed) {
      _cfgCheckDirty(original);
      return;
    }
  }

  const result = await apiPut('config', patch);
  if (result.ok) {
    const candlesChanged = patch.candles_limit !== undefined && patch.candles_limit !== state.cfg.candles_limit;
    state.cfg = {...state.cfg, ...patch};
    _applyUiPrefs(state.cfg);
    if (modeChanged) {
      state.tradingMode = patch.trading_mode;
      await refreshAll();
      renderConfig(state.cfg);
    } else {
      btn.textContent = 'Saved!';
      if (candlesChanged && state.selectedCoin) initCoinChart(state.selectedCoin, state.selectedTf);
      setTimeout(() => {
        btn.textContent = 'Save Config';
        renderConfig(state.cfg);
      }, 1500);
    }
  } else {
    btn.disabled = false;
    btn.textContent = 'Error — retry?';
    setTimeout(() => { btn.textContent = 'Save Config'; _cfgCheckDirty(original); }, 2500);
  }
}

// ── Logs Tab ──

// ── Data Manager Tab ──

let _historicFetchTimer = null;
let _historicFetchAbort = null;

let _dataSortCol = 'coin';
let _dataSortAsc = true;
let _dataFilter = '';
let _renderDataTable = null;

function _fmtAge(minutes) {
  if (minutes == null) return '—';
  if (minutes < 120) return `${minutes}m`;
  if (minutes < 1440) return `${Math.round(minutes / 60)}h`;
  return `${Math.round(minutes / 1440)}d`;
}

function _ageClass(minutes, tfMinutes, intervalMinutes) {
  if (minutes == null) return 'dm-age-error';
  const window = (tfMinutes || 60) + (intervalMinutes || 360);
  if (minutes < window)     return 'dm-age-ok';
  if (minutes < 2 * window) return 'dm-age-warn';
  return 'dm-age-stale';
}

async function loadAndRenderDataTab() {
  const el = $('#data-manager-content');
  if (!el) return;

  const running = state.dataManagerState && state.dataManagerState !== 'Stopped';
  const stateLabel = state.dataManagerState || 'Stopped';

  el.innerHTML = `
    <div class="dm-header">
      <div class="dm-status">
        <span class="dm-state-badge dm-state-${stateLabel.toLowerCase()}">${stateLabel}</span>
      </div>
      <input class="dm-filter" id="dm-filter" type="text" placeholder="Filter…" value="${_dataFilter}">
      <div class="dm-controls">
        ${running
          ? `<button class="btn btn-danger btn-small" id="btn-dm-stop">Stop Data Manager</button>`
          : `<button class="btn btn-primary btn-small" id="btn-dm-start">Start Data Manager</button>`}
      </div>
    </div>
    <div id="dm-table-wrap"><div class="dm-loading">Loading stats…</div></div>`;

  $('#btn-dm-start') && $('#btn-dm-start').addEventListener('click', async () => {
    await apiPost('data-manager/start');
    setTimeout(loadAndRenderDataTab, 800);
  });
  $('#btn-dm-stop') && $('#btn-dm-stop').addEventListener('click', async () => {
    await apiPost('data-manager/stop');
    setTimeout(loadAndRenderDataTab, 800);
  });
  $('#dm-filter').addEventListener('input', e => {
    _dataFilter = e.target.value.trim().toUpperCase();
    if (_renderDataTable) _renderDataTable();
  });

  await _loadDataTabStats();
}

async function _loadDataTabStats() {
  const wrap = $('#dm-table-wrap');
  if (!wrap) return;

  const data = await api('data-manager/stats');

  if (data.error) {
    wrap.innerHTML = `<div class="dm-error">Error: ${data.error}</div>`;
    return;
  }

  let rows = data.rows || [];
  const intervalMinutes = data.topup_interval_minutes || 360;

  const cols = ['coin', 'tf_minutes', 'rows', 'first', 'last', 'age_minutes'];
  const labels = {coin: 'Coin', tf_minutes: 'TF', rows: 'Rows', first: 'From', last: 'Latest', age_minutes: 'Age'};

  function sortRows() {
    rows.sort((a, b) => {
      let va = a[_dataSortCol], vb = b[_dataSortCol];
      if (va == null) va = _dataSortAsc ? Infinity : -Infinity;
      if (vb == null) vb = _dataSortAsc ? Infinity : -Infinity;
      const cmp = typeof va === 'string'
        ? (_dataSortAsc ? va.localeCompare(vb) : vb.localeCompare(va))
        : (_dataSortAsc ? va - vb : vb - va);
      return cmp !== 0 ? cmp : a.tf_minutes - b.tf_minutes;
    });
  }

  function rowsHtml(visible) {
    return visible.map(r => {
      const tfLabel = TF_MINUTES_LABEL[r.tf_minutes] || `${r.tf_minutes}m`;
      if (r.error) {
        const canBackfill = r.error === 'No data' && r.tf_minutes === 60;
        const backfillBtn = canBackfill
          ? `<button class="btn btn-small btn-primary dm-backfill-btn" data-coin="${r.coin}">Backfill</button>`
          : '';
        return `<tr class="dm-row dm-row-error">
          <td class="dm-td dm-coin">${r.coin}</td>
          <td class="dm-td dm-tf">${tfLabel}</td>
          <td class="dm-td dm-error-cell" colspan="4">${r.error}</td>
          <td class="dm-td">${backfillBtn}</td>
        </tr>`;
      }
      return `<tr class="dm-row">
        <td class="dm-td dm-coin">${r.coin}</td>
        <td class="dm-td dm-tf">${tfLabel}</td>
        <td class="dm-td dm-td-num">${(r.rows || 0).toLocaleString()}</td>
        <td class="dm-td dm-date">${r.first || '—'}</td>
        <td class="dm-td dm-date">${r.last ? r.last.slice(0, 16).replace('T', ' ') : '—'}</td>
        <td class="dm-td ${_ageClass(r.age_minutes, r.tf_minutes, intervalMinutes)}">${_fmtAge(r.age_minutes)}</td>
        <td class="dm-td"><button class="btn btn-small dm-chart-btn" data-coin="${r.coin}" data-tf="${r.tf_minutes}">Chart</button></td>
      </tr>`;
    }).join('');
  }

  function renderTable() {
    sortRows();
    const visible = _dataFilter ? rows.filter(r => r.coin.includes(_dataFilter)) : rows;
    const existingTable = wrap.querySelector('table.dm-table');

    if (existingTable) {
      // Patch thead sort indicators in-place — no DOM replacement, no flash
      $$('.dm-th[data-col]', wrap).forEach(th => {
        const c = th.dataset.col;
        const active = c === _dataSortCol;
        th.classList.toggle('dm-th-active', active);
        th.classList.toggle('dm-td-num', c === 'rows');
        th.textContent = labels[c] + (active ? (_dataSortAsc ? ' ▲' : ' ▼') : '');
      });
      // Only replace tbody
      const tbody = existingTable.querySelector('tbody');
      if (tbody) tbody.innerHTML = rowsHtml(visible);
    } else {
      // First render: build full table — delegation is handled by setupDataTabDelegation()
      const thHtml = cols.map(c => {
        const arrow = c === _dataSortCol ? (_dataSortAsc ? ' ▲' : ' ▼') : '';
        const extra = c === 'rows' ? ' dm-td-num' : '';
        return `<th class="dm-th${c === _dataSortCol ? ' dm-th-active' : ''}${extra}" data-col="${c}">${labels[c]}${arrow}</th>`;
      }).join('') + '<th class="dm-th">Chart</th>';

      wrap.innerHTML = `<table class="dm-table">
        <thead><tr>${thHtml}</tr></thead>
        <tbody>${rowsHtml(visible)}</tbody>
      </table>`;
    }
  }

  _renderDataTable = renderTable;
  renderTable();
}

// ── Errors Tab ──

let _errorsCache = [];

function _errorsLevelClass(level) {
  return level === 'error' ? 'err-row--error' : 'err-row--warning';
}

function _errorsTimestamp(ts) {
  const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts);
  const Y = d.getFullYear();
  const M = String(d.getMonth() + 1).padStart(2, '0');
  const D = String(d.getDate()).padStart(2, '0');
  const h = String(d.getHours()).padStart(2, '0');
  const m = String(d.getMinutes()).padStart(2, '0');
  const s = String(d.getSeconds()).padStart(2, '0');
  return `${Y}${M}${D}.${h}${m}${s}`;
}

function renderErrors(entries) {
  const list = $('#errors-list');
  if (!list) return;
  if (!entries.length) {
    list.innerHTML = '<div class="err-empty">No errors recorded.</div>';
    return;
  }
  const rows = entries.slice().reverse().map(e => `
    <div class="err-row ${_errorsLevelClass(e.level)}">
      <div class="err-row-header">
        <span class="err-badge err-badge--${e.level}">${(e.level || '').toUpperCase()}</span>
        <span class="err-component">${e.component || ''}</span>
        <span class="err-time">${_errorsTimestamp(e.ts)}</span>
      </div>
      <div class="err-message">${_esc(e.message || '')}</div>
      ${e.detail ? `<div class="err-detail">${_esc(e.detail)}</div>` : ''}
    </div>
  `).join('');
  list.innerHTML = rows;
}

function _esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

async function loadErrors() {
  const level = $('#errors-filter-level').value;
  const component = $('#errors-filter-component').value;
  let url = 'errors?limit=200';
  if (level) url += `&level=${level}`;
  if (component) url += `&component=${encodeURIComponent(component)}`;
  const data = await api(url);
  _errorsCache = data.errors || [];
  _rebuildErrorComponentFilter(_errorsCache);
  renderErrors(_errorsCache);
}

function _rebuildErrorComponentFilter(entries) {
  const sel = $('#errors-filter-component');
  if (!sel) return;
  const current = sel.value;
  const components = [...new Set(entries.map(e => e.component).filter(Boolean))].sort();
  sel.innerHTML = '<option value="">All components</option>';
  components.forEach(c => {
    const opt = document.createElement('option');
    opt.value = c;
    opt.textContent = c;
    sel.appendChild(opt);
  });
  if (current) sel.value = current;
}

let _errorsBadgeCount = 0;
function _updateErrorsBadge(n) {
  const btn = $('[data-tab="errors"]');
  if (!btn) return;
  if (n > 0) {
    btn.dataset.badge = n > 99 ? '99+' : String(n);
    btn.classList.add('has-badge');
  } else {
    btn.removeAttribute('data-badge');
    btn.classList.remove('has-badge');
  }
}

function onNewErrorEvent(entry) {
  if (entry.level === 'error') {
    _errorsBadgeCount++;
    _updateErrorsBadge(_errorsBadgeCount);
  }
  const errTab = $('#tab-errors');
  if (errTab && errTab.classList.contains('active')) {
    loadErrors();
  }
}

function populateLogSourceDropdown() {
  const select = $('#log-source');
  if (!select) return;
  const currentVal = select.value;
  select.innerHTML = '<option value="thinker">Thinker</option>';
  select.innerHTML += '<option value="data-manager">Data Manager</option>';
  state.exchangeList.forEach(xk => {
    select.innerHTML += `<option value="trader-${xk}">Trader: ${xk}</option>`;
  });
  if (currentVal) select.value = currentVal;
}

async function refreshLogs() {
  const source = $('#log-source').value;
  const data = await api(`logs/${source}`);
  const output = $('#log-output');
  const atBottom = output.scrollHeight - output.scrollTop - output.clientHeight < 40;
  output.textContent = (data.lines || []).join('\n');
  if (atBottom) output.scrollTop = output.scrollHeight;
}

// ── Tabs ──

function setupDataTabDelegation() {
  $('#tab-data').addEventListener('click', async e => {
    const th = e.target.closest('.dm-th[data-col]');
    if (th) {
      if (_dataSortCol === th.dataset.col) _dataSortAsc = !_dataSortAsc;
      else { _dataSortCol = th.dataset.col; _dataSortAsc = true; }
      if (_renderDataTable) _renderDataTable();
      return;
    }
    const chartBtn = e.target.closest('.dm-chart-btn');
    if (chartBtn) { showHistoricChart(chartBtn.dataset.coin, parseInt(chartBtn.dataset.tf)); return; }

    const backfillBtn = e.target.closest('.dm-backfill-btn');
    if (backfillBtn) {
      const coin = backfillBtn.dataset.coin;
      backfillBtn.disabled = true;
      backfillBtn.textContent = 'Starting…';
      await apiPost(`data-manager/backfill/${coin}`);
      setTimeout(() => _loadDataTabStats(), 2000);
    }
  });
}

function setupTabs() {
  $$('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.tab;
      $$('.tab-btn').forEach(b => b.classList.toggle('active', b === btn));
      $$('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + tab));

      if (state.logRefreshTimer) { clearInterval(state.logRefreshTimer); state.logRefreshTimer = null; }
      if (tab === 'history') { state.historyFilterCoin = null; _updateHistoryTabLabel(); loadTradeHistory(); }
      if (tab === 'compare') loadCompare();
      if (tab === 'lth') renderLTH();
      if (tab === 'training') loadAndRenderTraining();
      if (tab === 'data') {
        loadAndRenderDataTab();
        if (_dataTabInterval) clearInterval(_dataTabInterval);
        _dataTabInterval = setInterval(_loadDataTabStats, 30000);
      } else {
        if (_dataTabInterval) { clearInterval(_dataTabInterval); _dataTabInterval = null; }
      }
      if (tab === 'settings') loadAndRenderConfig();
      if (tab === 'logs') {
        refreshLogs();
        state.logRefreshTimer = setInterval(refreshLogs, 3000);
      }
      if (tab === 'errors') {
        _errorsBadgeCount = 0;
        _updateErrorsBadge(0);
        loadErrors();
      }
    });
  });
}

// ── Mobile Nav ──

function setupMobileNav() {
  const panels = {
    signals: '#panel-signals',
    chart: '#panel-chart',
    trades: '#panel-trades',
    settings: '#panel-trades',
  };

  $$('.mnav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const view = btn.dataset.view;
      $$('.mnav-btn').forEach(b => b.classList.toggle('active', b === btn));
      $$('.panel').forEach(p => p.classList.remove('mobile-active'));

      const panelSel = panels[view];
      if (panelSel) $(panelSel).classList.add('mobile-active');

      if (view === 'settings') {
        $$('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === 'settings'));
        $$('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-settings'));
      }
    });
  });

  $('#panel-signals').classList.add('mobile-active');
}

// ── Buttons ──

function setupButtons() {
  $('#btn-start-all').addEventListener('click', async () => {
    $('#btn-start-all').disabled = true;
    await apiPost('start-all');
    setTimeout(refreshAll, 2000);
    setTimeout(() => { $('#btn-start-all').disabled = false; }, 5000);
  });

  $('#btn-stop-all').addEventListener('click', async () => {
    await apiPost('stop-all');
    setTimeout(refreshAll, 1000);
  });

  // Actions dropdown toggle
  $('#btn-actions').addEventListener('click', (e) => {
    e.stopPropagation();
    $('#actions-menu').classList.toggle('open');
  });
  $('#actions-dropdown').addEventListener('click', (e) => e.stopPropagation());
  document.addEventListener('click', () => {
    $('#actions-menu').classList.remove('open');
  });

  $('#btn-close-all').addEventListener('click', async () => {
    if (!confirm('Close ALL positions on ALL exchanges?')) return;
    $('#actions-menu').classList.remove('open');
    await apiPost('close-all');
    setTimeout(refreshAll, 2000);
  });

  $('#btn-sync-shadow').addEventListener('click', async () => {
    $('#actions-menu').classList.remove('open');
    const res = await apiPost('sync-shadow');
    if (res && !res.ok) alert(res.error || 'Sync failed');
    setTimeout(refreshAll, 1000);
  });

  $('#btn-clear-history').addEventListener('click', async () => {
    if (!confirm('Clear account value history for all exchanges?')) return;
    $('#actions-menu').classList.remove('open');
    await apiPost('clear-account-history');
    if (state.chartMode === 'account') selectAccountChart(state.accountRange);
  });

  $('#btn-train-all').addEventListener('click', async () => {
    $('#actions-menu').classList.remove('open');
    await apiPost('train-all');
    setTimeout(refreshAll, 2000);
  });

  $('#btn-reset-all').addEventListener('click', async () => {
    if (!confirm('RESET ALL STATE?\n\nThis will:\n• Stop all traders\n• Close all positions\n• Wipe trade history & PnL\n• Reset balances to Kraken USD\n\nThis cannot be undone.')) return;
    $('#actions-menu').classList.remove('open');
    const res = await apiPost('reset-all');
    if (res && !res.ok) alert(res.error || 'Reset failed');
    setTimeout(refreshAll, 2000);
  });

  $('#btn-refresh-logs').addEventListener('click', refreshLogs);

  $('#btn-refresh-errors').addEventListener('click', loadErrors);
  $('#btn-clear-errors').addEventListener('click', async () => {
    if (!confirm('Clear all error history?')) return;
    await fetch('/api/errors', {method: 'DELETE'});
    _errorsBadgeCount = 0;
    _updateErrorsBadge(0);
    loadErrors();
  });
  $('#errors-filter-level').addEventListener('change', loadErrors);
  $('#errors-filter-component').addEventListener('change', loadErrors);

  $('#acct-block-portfolio').addEventListener('click', () => selectAccountChart(0));
}

// ── Timeframes ──

function rebuildTimeframeButtons() {
  const container = $('#tf-selector');
  container.innerHTML = '';
  TF_LIST.forEach(tf => {
    const btn = document.createElement('button');
    btn.className = 'tf-btn' + (tf === state.selectedTf ? ' active' : '');
    btn.textContent = tf.replace('hour', 'H').replace('min', 'm').replace('day', 'D').replace('week', 'W');
    btn.addEventListener('click', () => {
      state.selectedTf = tf;
      $$('.tf-btn').forEach(b => b.classList.toggle('active', b === btn));
      if (state.selectedCoin) loadChart(state.selectedCoin, tf);
    });
    container.appendChild(btn);
  });
}

function setupTimeframes() { rebuildTimeframeButtons(); }

// ── Sashes (drag to resize panels) ──

function _initSash(sashEl, cssVar, storageKey, calcSize) {
  const app = $('#app');
  const stored = localStorage.getItem(storageKey);
  if (stored) app.style.setProperty(cssVar, stored + 'px');

  let dragging = false;

  sashEl.addEventListener('mousedown', (e) => {
    e.preventDefault();
    dragging = true;
    sashEl.classList.add('dragging');
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  });

  window.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    app.style.setProperty(cssVar, calcSize(e.clientX) + 'px');
  });

  window.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    sashEl.classList.remove('dragging');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    const val = getComputedStyle(app).getPropertyValue(cssVar).trim();
    localStorage.setItem(storageKey, parseInt(val));
    if (state.chart) state.chart.applyOptions({ autoSize: true });
  });
}

function setupSash() {
  const sashLeft = $('#sash-left');
  const sashRight = $('#sash');

  if (sashLeft) _initSash(sashLeft, '--lh-width', 'pt-lh-width',
    x => Math.max(200, Math.min(600, x)));

  if (sashRight) _initSash(sashRight, '--rh-width', 'pt-rh-width',
    x => Math.max(280, Math.min(700, window.innerWidth - x)));
}

// ── View Toggle ──

function setupViewToggle() {
  const stored = localStorage.getItem('pt-card-mode');
  if (stored === 'detail' || stored === 'simple') state.cardMode = stored;

  $$('.vtog-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.mode === state.cardMode);
    btn.addEventListener('click', () => {
      state.cardMode = btn.dataset.mode;
      $$('.vtog-btn').forEach(b => b.classList.toggle('active', b === btn));
      localStorage.setItem('pt-card-mode', state.cardMode);
      renderCoinGrid();
    });
  });
}

// ── Boot ──
document.addEventListener('DOMContentLoaded', init);
