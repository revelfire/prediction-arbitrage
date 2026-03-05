// ============================================================
// Arb Scanner Dashboard
// ============================================================

// --- State ---
let activeTab = 'tickets';
let refreshTimer = null;
let spreadChart = null;
let healthChart = null;
let discCategoryChart = null;
let discHitrateChart = null;
let discMethodChart = null;
let tickerSource = null;
let tickerSparklines = {};
let wsThroughputChart = null;
let wsSchemaTrendChart = null;

// --- Auth ---
const _apiToken = document.querySelector('meta[name="api-token"]')?.content || '';
function authHeaders(extra = {}) {
    const h = { ...extra };
    if (_apiToken) h['Authorization'] = `Bearer ${_apiToken}`;
    return h;
}

// --- Helpers ---
function formatPct(val) {
    if (val == null) return 'N/A';
    return (parseFloat(val) * 100).toFixed(2) + '%';
}

function formatUSD(val) {
    if (val == null) return 'N/A';
    return '$' + parseFloat(val).toFixed(2);
}

function formatTime(iso) {
    if (!iso) return 'N/A';
    const d = new Date(iso);
    return d.toLocaleString();
}

function formatDepthContracts(val) {
    if (val == null) return 'N/A';
    return `${val} contracts`;
}

function shortTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return d.toLocaleTimeString();
}

async function fetchJSON(url) {
    try {
        const resp = await fetch(url, { headers: authHeaders() });
        if (!resp.ok) {
            const body = await resp.text().catch(() => '');
            const detail = body ? `: ${body.substring(0, 120)}` : '';
            setStatus(`API error ${resp.status} on ${url}${detail}`);
            console.error(`Fetch failed: ${url} HTTP ${resp.status}`, body);
            return null;
        }
        return await resp.json();
    } catch (err) {
        setStatus(`Network error: ${err.message}`);
        console.error(`Fetch failed: ${url}`, err);
        return null;
    }
}

async function postJSON(url) {
    try {
        const resp = await fetch(url, { method: 'POST', headers: authHeaders() });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return await resp.json();
    } catch (err) {
        console.error(`POST failed: ${url}`, err);
        return null;
    }
}

async function patchJSON(url, body) {
    try {
        const resp = await fetch(url, {
            method: 'PATCH',
            headers: authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(body),
        });
        if (!resp.ok) {
            const text = await resp.text().catch(() => '');
            setStatus(`PATCH error ${resp.status}: ${text.substring(0, 120)}`);
            return null;
        }
        return await resp.json();
    } catch (err) {
        console.error(`PATCH failed: ${url}`, err);
        return null;
    }
}

function el(id) { return document.getElementById(id); }

function setStatus(msg) {
    const s = el('status-text');
    if (s) s.textContent = msg;
}

function updateRefreshTime() {
    const t = el('last-refresh');
    if (t) t.textContent = 'Last refreshed: ' + new Date().toLocaleTimeString();
}

// --- Tab Switching ---
function switchTab(tabName) {
    if (activeTab === 'autoexec' && tabName !== 'autoexec') closeAeSSE();
    activeTab = tabName;
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tabName);
    });
    document.querySelectorAll('.tab-content').forEach(panel => {
        panel.classList.toggle('active', panel.id === 'tab-' + tabName);
    });
    refreshActiveTab();
}

// --- Opportunities Tab ---
async function refreshOpportunities() {
    const data = await fetchJSON('/api/opportunities?limit=50');
    const tbody = el('opps-tbody');
    if (!tbody) return;

    if (!data) {
        tbody.innerHTML = '<tr><td colspan="8" class="empty-state">Failed to load opportunities (check status bar)</td></tr>';
        return;
    }

    if (data.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="empty-state">No opportunities found</td></tr>';
    } else {
        tbody.innerHTML = data.map(o => `
            <tr class="clickable" onclick="loadPairDetail('${o.poly_event_id}', '${o.kalshi_event_id}')">
                <td title="${o.poly_event_id}">${(o.poly_event_id || '').substring(0, 12)}...</td>
                <td>${o.buy_venue || 'N/A'}</td>
                <td>${o.sell_venue || 'N/A'}</td>
                <td><strong>${formatPct(o.net_spread_pct)}</strong></td>
                <td>${formatUSD(o.max_size)}</td>
                <td>${o.depth_risk ? '\u26a0\ufe0f' : '\u2713'}</td>
                <td>${o.annualized_return ? formatPct(o.annualized_return) : 'N/A'}</td>
                <td>${shortTime(o.detected_at)}</td>
            </tr>
        `).join('');
    }

    // Also load pair summaries
    const summaries = await fetchJSON('/api/pairs/summaries?hours=24&top=10');
    const sumBody = el('summaries-tbody');
    if (sumBody) {
        if (!summaries || summaries.length === 0) {
            sumBody.innerHTML = '<tr><td colspan="6" class="empty-state">No pair data</td></tr>';
        } else {
            sumBody.innerHTML = summaries.map(s => `
                <tr class="clickable" onclick="loadPairDetail('${s.poly_event_id}', '${s.kalshi_event_id}')">
                    <td>${(s.poly_event_id || '').substring(0, 12)}/${(s.kalshi_event_id || '').substring(0, 12)}</td>
                    <td><strong>${formatPct(s.peak_spread)}</strong></td>
                    <td>${formatPct(s.avg_spread)}</td>
                    <td>${s.total_detections}</td>
                    <td>${shortTime(s.first_seen)}</td>
                    <td>${shortTime(s.last_seen)}</td>
                </tr>
            `).join('');
        }
    }
}

async function loadPairDetail(polyId, kalshiId) {
    const panel = el('pair-detail');
    if (!panel) return;

    panel.style.display = 'block';
    panel.querySelector('h3').textContent = `Spread History: ${polyId.substring(0, 16)} / ${kalshiId.substring(0, 16)}`;

    const data = await fetchJSON(`/api/pairs/${encodeURIComponent(polyId)}/${encodeURIComponent(kalshiId)}/history?hours=24`);
    if (!data || data.length === 0) {
        el('pair-chart-container').innerHTML = '<p class="empty-state">No history data</p>';
        return;
    }

    // Sort by time ascending
    data.sort((a, b) => new Date(a.detected_at) - new Date(b.detected_at));

    const ctx = el('spread-chart');
    if (spreadChart) spreadChart.destroy();

    spreadChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: data.map(d => new Date(d.detected_at).toLocaleTimeString()),
            datasets: [{
                label: 'Net Spread %',
                data: data.map(d => parseFloat(d.net_spread_pct) * 100),
                borderColor: '#4fc3f7',
                backgroundColor: 'rgba(79, 195, 247, 0.1)',
                fill: true,
                tension: 0.3,
                pointRadius: 2,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { labels: { color: '#e0e0e0' } } },
            scales: {
                x: { ticks: { color: '#a0a0b0', maxTicksLimit: 10 }, grid: { color: '#2a2a4a' } },
                y: { ticks: { color: '#a0a0b0', callback: v => v.toFixed(1) + '%' }, grid: { color: '#2a2a4a' } },
            }
        }
    });
}

// --- Health Tab ---
async function refreshHealth() {
    const [health, scans] = await Promise.all([
        fetchJSON('/api/health?hours=24'),
        fetchJSON('/api/health/scans?limit=20'),
    ]);

    if (health && health.length > 0) {
        // Compute aggregate metrics
        const totalScans = health.reduce((s, h) => s + h.scan_count, 0);
        const avgDuration = health.reduce((s, h) => s + h.avg_duration_s, 0) / health.length;
        const totalLLM = health.reduce((s, h) => s + h.total_llm_calls, 0);
        const totalErrors = health.reduce((s, h) => s + h.total_errors, 0);
        const totalOpps = health.reduce((s, h) => s + h.total_opps, 0);

        el('metric-scans').textContent = totalScans;
        el('metric-duration').textContent = avgDuration.toFixed(1) + 's';
        el('metric-llm').textContent = totalLLM;
        el('metric-errors').textContent = totalErrors;
        el('metric-opps').textContent = totalOpps;

        // Hourly chart
        const sorted = [...health].sort((a, b) => new Date(a.hour) - new Date(b.hour));
        const ctx = el('health-chart');
        if (healthChart) healthChart.destroy();

        healthChart = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: sorted.map(h => new Date(h.hour).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })),
                datasets: [
                    {
                        label: 'Scans',
                        data: sorted.map(h => h.scan_count),
                        backgroundColor: '#4fc3f7',
                    },
                    {
                        label: 'Opportunities',
                        data: sorted.map(h => h.total_opps),
                        backgroundColor: '#66bb6a',
                    },
                    {
                        label: 'Errors',
                        data: sorted.map(h => h.total_errors),
                        backgroundColor: '#e94560',
                    },
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { labels: { color: '#e0e0e0' } } },
                scales: {
                    x: { ticks: { color: '#a0a0b0' }, grid: { color: '#2a2a4a' } },
                    y: { ticks: { color: '#a0a0b0' }, grid: { color: '#2a2a4a' } },
                }
            }
        });
    }

    // Recent scans table
    if (scans) {
        const tbody = el('scans-tbody');
        if (tbody) {
            if (scans.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" class="empty-state">No scan data</td></tr>';
            } else {
                tbody.innerHTML = scans.map(s => {
                    const duration = s.completed_at && s.started_at
                        ? ((new Date(s.completed_at) - new Date(s.started_at)) / 1000).toFixed(1) + 's'
                        : '<span style="color: var(--warning, orange)">incomplete</span>';
                    const errs = Array.isArray(s.errors) ? s.errors : (typeof s.errors === 'string' ? JSON.parse(s.errors || '[]') : []);
                    const errCount = errs.length;
                    const errTip = errCount > 0 ? errs.map(e => e.substring(0, 100)).join('\n') : '';
                    const poly = s.poly_markets_fetched || 0;
                    const kalshi = s.kalshi_markets_fetched || 0;
                    return `
                        <tr>
                            <td>${shortTime(s.started_at)}</td>
                            <td>${duration}</td>
                            <td title="Poly: ${poly}, Kalshi: ${kalshi}">${poly + kalshi}</td>
                            <td>${s.candidate_pairs || 0}</td>
                            <td>${s.opportunities_found || 0}</td>
                            <td${errCount > 0 ? ' title="' + errTip.replace(/"/g, '&quot;') + '" style="color: var(--danger, #e94560); cursor: help"' : ''}>${errCount}</td>
                        </tr>
                    `;
                }).join('');
            }
        }
    }
}

// --- Alerts Tab ---
async function refreshAlerts() {
    const typeFilter = el('alert-type-filter');
    const typeParam = typeFilter && typeFilter.value ? `&type=${typeFilter.value}` : '';
    const data = await fetchJSON(`/api/alerts?limit=50${typeParam}`);

    const container = el('alerts-list');
    if (!container || !data) return;

    if (data.length === 0) {
        container.innerHTML = '<div class="empty-state">No alerts found</div>';
        return;
    }

    container.innerHTML = data.map(a => {
        const pair = a.poly_event_id && a.kalshi_event_id
            ? `${a.poly_event_id.substring(0, 12)} / ${a.kalshi_event_id.substring(0, 12)}`
            : 'System';
        const before = a.spread_before != null ? formatPct(a.spread_before) : '';
        const after = a.spread_after != null ? formatPct(a.spread_after) : '';
        const spread = before && after ? `${before} \u2192 ${after}` : '';

        return `
            <div class="alert-item ${a.alert_type}">
                <div class="alert-type">${a.alert_type}</div>
                <div class="alert-message">${a.message}</div>
                <div class="alert-meta">${pair} ${spread ? '| ' + spread : ''} | ${formatTime(a.dispatched_at)}</div>
            </div>
        `;
    }).join('');
}

// --- Tickets Tab ---
async function refreshTickets() {
    const params = new URLSearchParams();
    const statusVal = el('ticket-status-filter')?.value;
    const catVal = el('ticket-category-filter')?.value;
    const typeVal = el('ticket-type-filter')?.value;
    if (statusVal) params.set('status', statusVal);
    if (catVal) params.set('category', catVal);
    if (typeVal) params.set('ticket_type', typeVal);
    const qs = params.toString() ? `?${params.toString()}` : '';

    const [data, summary] = await Promise.all([
        fetchJSON(`/api/tickets${qs}`),
        fetchJSON('/api/tickets/summary?days=30'),
    ]);

    // Summary metrics
    if (summary && Array.isArray(summary) && summary.length > 0) {
        const totals = summary.reduce((a, r) => ({
            tickets: a.tickets + (r.total_tickets || 0),
            executed: a.executed + (r.executed_count || 0),
            pnl: a.pnl + parseFloat(r.total_pnl || 0),
            slippage: a.slippage + parseFloat(r.avg_slippage || 0) * (r.executed_count || 0),
            wins: a.wins + (r.wins || 0),
            withPnl: a.withPnl + (r.total_with_pnl || 0),
        }), { tickets: 0, executed: 0, pnl: 0, slippage: 0, wins: 0, withPnl: 0 });

        el('tkt-total').textContent = totals.tickets;
        el('tkt-exec-rate').textContent = totals.tickets > 0
            ? (totals.executed / totals.tickets * 100).toFixed(1) + '%' : '-';
        el('tkt-avg-slippage').textContent = totals.executed > 0
            ? (totals.slippage / totals.executed).toFixed(4) : '-';
        el('tkt-win-rate').textContent = totals.withPnl > 0
            ? (totals.wins / totals.withPnl * 100).toFixed(1) + '%' : '-';
        el('tkt-total-pnl').textContent = totals.pnl !== 0
            ? (totals.pnl >= 0 ? '+' : '') + totals.pnl.toFixed(4) : '-';
    } else {
        ['tkt-total', 'tkt-exec-rate', 'tkt-avg-slippage', 'tkt-win-rate', 'tkt-total-pnl']
            .forEach(id => { el(id).textContent = '-'; });
    }

    const tbody = el('tickets-tbody');
    if (!tbody || !data) return;

    if (data.length === 0) {
        const label = statusVal || '';
        tbody.innerHTML = `<tr><td colspan="8" class="empty-state">No ${label} tickets</td></tr>`;
        return;
    }

    tbody.innerHTML = data.map(t => {
        const leg1 = typeof t.leg_1 === 'string' ? JSON.parse(t.leg_1) : (t.leg_1 || {});
        const title = leg1.market_title || leg1.title || (t.arb_id || '').substring(0, 16);
        const actions = ticketActionButtons(t);
        return `
            <tr class="clickable" onclick="openTicketDetail('${t.arb_id}')">
                <td title="${t.arb_id}">${title.length > 30 ? title.substring(0, 30) + '...' : title}</td>
                <td>${t.category ? `<span class="category-badge">${t.category}</span>` : '-'}</td>
                <td>${t.ticket_type || '-'}</td>
                <td>${formatUSD(t.expected_cost)}</td>
                <td>${formatUSD(t.expected_profit)}</td>
                <td><span class="badge badge-${t.status}">${t.status}</span></td>
                <td>${shortTime(t.created_at)}</td>
                <td>${actions}</td>
            </tr>
        `;
    }).join('');
}

function renderLegEntries(leg) {
    const hide = new Set(['market_url', 'title']);
    const truncate = new Set(['token_id', 'arb_id', 'event_id']);
    return Object.entries(leg).filter(([k]) => !hide.has(k)).map(([k, v]) => {
        const label = k.replace(/_/g, ' ');
        let display = (v === null || v === undefined || v === '') ? 'N/A' : v;
        if (truncate.has(k) && typeof display === 'string' && display.length > 16) {
            display = `<span title="${display}">${display.substring(0, 8)}…${display.substring(display.length - 8)}</span>`;
        }
        return `<div class="detail-row"><span class="detail-label">${label}</span><span class="detail-value">${display}</span></div>`;
    }).join('') + (leg.market_url
        ? `<div class="detail-row"><span class="detail-label">market</span><span class="detail-value"><a href="${leg.market_url}" target="_blank" rel="noopener" class="market-link">Open on ${leg.venue || 'venue'}</a></span></div>`
        : '');
}

function ticketActionButtons(t) {
    const id = t.arb_id;
    const btns = [];
    btns.push(`<button class="btn btn-primary btn-sm" onclick="event.stopPropagation(); openTicketDetail('${id}')">View</button>`);
    if (t.status === 'pending') {
        btns.push(`<button class="btn btn-success btn-sm" onclick="event.stopPropagation(); approveTicket('${id}')">Approve</button>`);
        btns.push(`<button class="btn btn-danger btn-sm" onclick="event.stopPropagation(); expireTicket('${id}')">Expire</button>`);
    }
    if (t.status === 'approved') {
        if (_execReady) {
            btns.push(`<button class="btn btn-success btn-sm" onclick="event.stopPropagation(); oneClickExecute('${id}')" title="Run preflight then execute">1-Click</button>`);
            btns.push(`<button class="btn btn-success btn-sm" onclick="event.stopPropagation(); openExecuteModal('${id}')">Manual</button>`);
        }
        btns.push(`<button class="btn btn-warning btn-sm" onclick="event.stopPropagation(); cancelTicket('${id}')">Cancel</button>`);
    }
    return btns.join(' ');
}

async function openTicketDetail(arbId) {
    const modal = el('ticket-modal');
    const body = el('modal-body');
    if (!modal || !body) return;
    modal.style.display = 'flex';
    body.innerHTML = '<div class="empty-state">Loading...</div>';

    const d = await fetchJSON(`/api/tickets/${encodeURIComponent(arbId)}`);
    if (!d) {
        body.innerHTML = '<div class="empty-state">Failed to load ticket detail</div>';
        return;
    }

    const leg1 = typeof d.leg_1 === 'string' ? JSON.parse(d.leg_1) : (d.leg_1 || {});
    const leg2 = typeof d.leg_2 === 'string' ? JSON.parse(d.leg_2) : (d.leg_2 || {});
    const isFlip = d.ticket_type === 'flippening';
    const actionsLog = renderActionLog(d.actions || []);
    let execOrdersHtml = '';
    let flipPositionHtml = '';
    let flipPosition = null;
    if (d.status === 'executed') {
        const orders = await fetchJSON(`/api/execution/orders/${encodeURIComponent(d.arb_id)}`);
        execOrdersHtml = renderExecOrders(orders || []);
        if (isFlip) {
            flipPosition = await fetchJSON(`/api/execution/flip-position/${encodeURIComponent(d.arb_id)}`);
            if (flipPosition) flipPositionHtml = renderFlipPosition(flipPosition);
        }
    }
    const actionBtns = buildDetailActions(d, flipPosition);

    if (isFlip) {
        const flipTitle = leg1.market_title || d.market_title || 'N/A';
        const flipUrl = leg1.market_url || '';
        const flipMarketEl = flipUrl
            ? `<a href="${flipUrl}" target="_blank" rel="noopener" class="market-link">${flipTitle}</a>`
            : flipTitle;
        body.innerHTML = `
            <div class="detail-section">
                <h4>Flippening Trade</h4>
                <div class="detail-grid">
                    <div class="detail-row"><span class="detail-label">Market</span><span class="detail-value">${flipMarketEl}</span></div>
                    <div class="detail-row"><span class="detail-label">Category</span><span class="detail-value">${d.category || leg1.sport || 'N/A'}</span></div>
                </div>
            </div>
            <div class="detail-section">
                <h4>Execution Legs</h4>
                <div class="detail-grid">
                    <div class="leg-card">
                        <div class="leg-title">Leg 1 - ${leg1.action || 'Entry'}</div>
                        <div class="detail-row"><span class="detail-label">Price</span><span class="detail-value">${formatUSD(leg1.price)}</span></div>
                    </div>
                    <div class="leg-card">
                        <div class="leg-title">Leg 2 - ${leg2.action || 'Exit'}</div>
                        <div class="detail-row"><span class="detail-label">Target</span><span class="detail-value">${formatUSD(leg2.target_price)}</span></div>
                        <div class="detail-row"><span class="detail-label">Stop loss</span><span class="detail-value">${formatUSD(leg2.stop_loss)}</span></div>
                        <div class="detail-row"><span class="detail-label">Max hold</span><span class="detail-value">${leg2.max_hold_minutes || 'N/A'} min</span></div>
                    </div>
                </div>
            </div>
            <div class="detail-section">
                <h4>Ticket</h4>
                <div class="detail-grid">
                    <div class="detail-row"><span class="detail-label">Expected cost</span><span class="detail-value">${formatUSD(d.expected_cost)}</span></div>
                    <div class="detail-row"><span class="detail-label">Expected profit</span><span class="detail-value">${formatUSD(d.expected_profit)}</span></div>
                    <div class="detail-row"><span class="detail-label">Created</span><span class="detail-value">${formatTime(d.created_at)}</span></div>
                </div>
            </div>
            ${flipPositionHtml}
            ${execOrdersHtml}
            ${actionsLog}
            ${actionBtns}
        `;
    } else {
        const t1 = leg1.title || '';
        const t2 = leg2.title || '';
        const url1 = leg1.market_url || '';
        const url2 = leg2.market_url || '';
        const link1 = url1 ? `<a href="${url1}" target="_blank" rel="noopener" class="market-link">${t1}</a>` : t1;
        const link2 = url2 ? `<a href="${url2}" target="_blank" rel="noopener" class="market-link">${t2}</a>` : t2;
        body.innerHTML = `
            <div class="detail-section">
                <h4>Arbitrage Ticket</h4>
                <div class="detail-grid">
                    <div class="detail-row"><span class="detail-label">Arb ID</span><span class="detail-value">${d.arb_id}</span></div>
                    <div class="detail-row"><span class="detail-label">Category</span><span class="detail-value">${d.category || '-'}</span></div>
                </div>
            </div>
            ${(t1 || t2) ? `<div class="detail-section match-comparison">
                <h4>Match Comparison</h4>
                <div class="detail-grid">
                    <div class="match-card"><div class="match-venue">${leg1.venue || 'Venue 1'}</div><div class="match-title">${link1 || 'N/A'}</div></div>
                    <div class="match-card"><div class="match-venue">${leg2.venue || 'Venue 2'}</div><div class="match-title">${link2 || 'N/A'}</div></div>
                </div>
            </div>` : ''}
            <div class="detail-section">
                <h4>Execution Legs</h4>
                <div class="detail-grid">
                    <div class="leg-card">
                        <div class="leg-title">Leg 1</div>
                        ${renderLegEntries(leg1)}
                    </div>
                    <div class="leg-card">
                        <div class="leg-title">Leg 2</div>
                        ${renderLegEntries(leg2)}
                    </div>
                </div>
            </div>
            <div class="detail-section">
                <h4>Ticket</h4>
                <div class="detail-grid">
                    <div class="detail-row"><span class="detail-label">Expected cost</span><span class="detail-value">${formatUSD(d.expected_cost)}</span></div>
                    <div class="detail-row"><span class="detail-label">Expected profit</span><span class="detail-value">${formatUSD(d.expected_profit)}</span></div>
                    <div class="detail-row"><span class="detail-label">Created</span><span class="detail-value">${formatTime(d.created_at)}</span></div>
                </div>
            </div>
            ${execOrdersHtml}
            ${actionsLog}
            ${actionBtns}
        `;
    }
}

function renderExecOrders(orders) {
    if (!orders || orders.length === 0) return '';
    const rows = orders.map(o => {
        const venue = o.venue || '';
        const vid = o.venue_order_id || '';
        const status = o.status || '';
        const side = (o.side || '').replace('_', ' ');
        const reqPrice = o.requested_price ? formatUSD(o.requested_price) : 'N/A';
        const fillPrice = o.fill_price ? formatUSD(o.fill_price) : '-';
        const contracts = o.size_contracts != null ? o.size_contracts : '-';
        const vidDisplay = vid
            ? `<span class="detail-value mono" title="${vid}">${vid.slice(0, 12)}...</span>`
            : '<span class="detail-value">N/A</span>';
        return `
            <div class="leg-card">
                <div class="leg-title">${venue} - ${side}</div>
                <div class="detail-row"><span class="detail-label">Status</span><span class="detail-value badge badge-${status === 'filled' ? 'approved' : status === 'submitted' ? 'pending' : 'cancelled'}">${status}</span></div>
                <div class="detail-row"><span class="detail-label">Requested</span><span class="detail-value">${reqPrice}</span></div>
                <div class="detail-row"><span class="detail-label">Filled at</span><span class="detail-value">${fillPrice}</span></div>
                <div class="detail-row"><span class="detail-label">Contracts</span><span class="detail-value">${contracts}</span></div>
                <div class="detail-row"><span class="detail-label">Order ID</span>${vidDisplay}</div>
                ${o.error_message ? `<div class="detail-row"><span class="detail-label">Error</span><span class="detail-value exec-status-failed">${o.error_message}</span></div>` : ''}
            </div>`;
    }).join('');
    return `<div class="detail-section"><h4>Execution Orders</h4><div class="detail-grid">${rows}</div></div>`;
}

function renderFlipPosition(pos) {
    const status = pos.status || 'unknown';
    const statusClass = status === 'open' ? 'approved' : status === 'closed' ? 'executed' : 'cancelled';
    const pnl = pos.realized_pnl ? formatUSD(pos.realized_pnl) : '-';
    const exitPrice = pos.exit_price ? formatUSD(pos.exit_price) : '-';
    return `
        <div class="detail-section">
            <h4>Open Position</h4>
            <div class="detail-grid">
                <div class="leg-card">
                    <div class="leg-title">Polymarket Position</div>
                    <div class="detail-row"><span class="detail-label">Status</span><span class="detail-value badge badge-${statusClass}">${status}</span></div>
                    <div class="detail-row"><span class="detail-label">Side</span><span class="detail-value">${(pos.side || '').toUpperCase()}</span></div>
                    <div class="detail-row"><span class="detail-label">Contracts</span><span class="detail-value">${pos.size_contracts || '-'}</span></div>
                    <div class="detail-row"><span class="detail-label">Entry price</span><span class="detail-value">${pos.entry_price ? formatUSD(pos.entry_price) : 'N/A'}</span></div>
                    <div class="detail-row"><span class="detail-label">Exit price</span><span class="detail-value">${exitPrice}</span></div>
                    <div class="detail-row"><span class="detail-label">Realized P&amp;L</span><span class="detail-value">${pnl}</span></div>
                    <div class="detail-row"><span class="detail-label">Opened</span><span class="detail-value">${formatTime(pos.opened_at)}</span></div>
                </div>
            </div>
        </div>`;
}

async function flipExit(arbId) {
    if (!confirm('Place a sell order for this position now?')) return;
    const result = await postJSON(`/api/execution/flip-exit/${encodeURIComponent(arbId)}`);
    if (result) {
        setStatus(`Exit order submitted: ${result.order_id}`);
        openTicketDetail(arbId);
    }
}

function buildDetailActions(d, position = null) {
    const id = d.arb_id;
    const btns = [];
    if (d.status === 'pending') {
        btns.push(`<button class="btn btn-success" onclick="approveTicket('${id}'); closeTicketModal();">Approve</button>`);
        btns.push(`<button class="btn btn-danger" onclick="expireTicket('${id}'); closeTicketModal();">Expire</button>`);
    } else if (d.status === 'approved') {
        if (_execReady) {
            btns.push(`<button class="btn btn-success" onclick="closeTicketModal(); oneClickExecute('${id}');">1-Click Execute</button>`);
            btns.push(`<button class="btn btn-success" onclick="openExecuteModal('${id}'); closeTicketModal();">Manual</button>`);
        }
        btns.push(`<button class="btn btn-warning" onclick="cancelTicket('${id}'); closeTicketModal();">Cancel</button>`);
    }
    if (d.ticket_type === 'flippening' && position && position.status === 'open') {
        btns.push(`<button class="btn btn-danger" onclick="flipExit('${id}')">Exit Now</button>`);
    }
    btns.push(`<button class="btn btn-primary" onclick="addAnnotation('${id}')">Add Note</button>`);
    if (btns.length === 0) return `<div class="modal-actions"><span class="badge badge-${d.status}">${d.status}</span></div>`;
    return `<div class="modal-actions">${btns.join('')}</div>`;
}

function renderActionLog(actions) {
    if (!actions || actions.length === 0) return '';
    const items = actions.map(a => `
        <div class="action-log-item">
            <span class="action-time">${formatTime(a.created_at)}</span>
            <span class="action-type badge badge-${a.action === 'execute' ? 'executed' : a.action === 'approve' ? 'approved' : a.action === 'cancel' ? 'cancelled' : 'pending'}">${a.action}</span>
            ${a.actual_entry_price ? `<span>Entry: ${formatUSD(a.actual_entry_price)}</span>` : ''}
            ${a.slippage ? `<span>Slip: ${parseFloat(a.slippage).toFixed(4)}</span>` : ''}
            ${a.notes ? `<span class="action-notes">${a.notes}</span>` : ''}
        </div>
    `).join('');
    return `<div class="detail-section"><h4>Action Log</h4><div class="action-log">${items}</div></div>`;
}

function closeTicketModal() {
    const modal = el('ticket-modal');
    if (modal) modal.style.display = 'none';
}

async function approveTicket(arbId) {
    const result = await postJSON(`/api/tickets/${encodeURIComponent(arbId)}/approve`);
    if (result) refreshTickets();
}

async function expireTicket(arbId) {
    const result = await postJSON(`/api/tickets/${encodeURIComponent(arbId)}/expire`);
    if (result) refreshTickets();
}

async function openExecuteModal(arbId) {
    el('exec-arb-id').value = arbId;
    el('exec-entry-price').value = '';
    el('exec-size-usd').value = '';
    el('exec-notes').value = '';
    el('execute-modal').style.display = 'flex';
    const d = await fetchJSON(`/api/tickets/${encodeURIComponent(arbId)}`);
    if (!d) return;
    const leg1 = typeof d.leg_1 === 'string' ? JSON.parse(d.leg_1) : (d.leg_1 || {});
    const price = leg1.price || '';
    const size = leg1.size_usd || leg1.size || '';
    if (price) el('exec-entry-price').value = parseFloat(price).toFixed(4);
    if (size) el('exec-size-usd').value = parseFloat(size).toFixed(2);
}

function closeExecuteModal() {
    el('execute-modal').style.display = 'none';
}

async function submitExecution() {
    const arbId = el('exec-arb-id').value;
    const body = { status: 'executed' };
    const ep = el('exec-entry-price').value;
    const sz = el('exec-size-usd').value;
    const notes = el('exec-notes').value;
    if (ep) body.actual_entry_price = parseFloat(ep);
    if (sz) body.actual_size_usd = parseFloat(sz);
    if (notes) body.notes = notes;
    const result = await patchJSON(`/api/tickets/${encodeURIComponent(arbId)}`, body);
    closeExecuteModal();
    if (result) refreshTickets();
}

async function cancelTicket(arbId) {
    const notes = prompt('Reason for cancellation (optional):') || '';
    const result = await patchJSON(`/api/tickets/${encodeURIComponent(arbId)}`, {
        status: 'cancelled', notes: notes
    });
    if (result) refreshTickets();
}

async function addAnnotation(arbId) {
    const notes = prompt('Add a note to this ticket:');
    if (notes === null) return;
    await patchJSON(`/api/tickets/${encodeURIComponent(arbId)}`, { notes: notes });
    openTicketDetail(arbId);
}

// --- Execution Engine ---
let _execReady = false;

async function refreshExecStatus() {
    const data = await fetchJSON('/api/execution/status');
    const ind = el('exec-status-indicator');
    _execReady = !!(data && data.enabled && data.initialised);
    if (!ind) return;
    if (_execReady) {
        ind.textContent = 'EXEC READY';
        ind.className = 'exec-indicator exec-ready';
    } else {
        ind.textContent = 'EXEC OFF';
        ind.className = 'exec-indicator exec-disabled';
    }
}

async function oneClickExecute(arbId) {
    const modal = el('preflight-modal');
    const body = el('preflight-body');
    const actions = el('preflight-actions');
    if (!modal || !body || !actions) return;
    modal.style.display = 'flex';
    actions.style.display = 'none';
    body.innerHTML = '<div class="empty-state">Running preflight checks...</div>';

    const result = await postJSON(`/api/execution/preflight/${encodeURIComponent(arbId)}`);
    if (!result) {
        body.innerHTML = '<div class="empty-state">Preflight failed - execution engine may not be available</div>';
        return;
    }

    const checks = (result.checks || []).map(c => `
        <li class="preflight-check">
            <span class="preflight-icon ${c.passed ? 'preflight-pass' : 'preflight-fail'}">${c.passed ? '\u2713' : '\u2717'}</span>
            <span class="preflight-name">${c.name}</span>
            <span class="preflight-msg">${c.message}</span>
        </li>
    `).join('');

    const summaryHtml = `
        <div class="preflight-summary">
            <div class="detail-row"><span class="detail-label">Suggested size</span><span class="detail-value">${formatUSD(result.suggested_size_usd)}</span></div>
            <div class="detail-row"><span class="detail-label">Max size</span><span class="detail-value">${formatUSD(result.max_size_usd)}</span></div>
            <div class="detail-row"><span class="detail-label">Poly balance</span><span class="detail-value">${formatUSD(result.poly_balance)}</span></div>
            <div class="detail-row"><span class="detail-label">Kalshi balance</span><span class="detail-value">${formatUSD(result.kalshi_balance)}</span></div>
            <div class="detail-row"><span class="detail-label">Poly slippage</span><span class="detail-value">${formatPct(result.estimated_slippage_poly)}</span></div>
            <div class="detail-row"><span class="detail-label">Kalshi slippage</span><span class="detail-value">${formatPct(result.estimated_slippage_kalshi)}</span></div>
            <div class="detail-row"><span class="detail-label">Poly depth</span><span class="detail-value">${formatDepthContracts(result.poly_depth_contracts)}</span></div>
            <div class="detail-row"><span class="detail-label">Kalshi depth</span><span class="detail-value">${formatDepthContracts(result.kalshi_depth_contracts)}</span></div>
        </div>
    `;

    body.innerHTML = `
        <div class="detail-section"><h4>Preflight Checks</h4><ul class="preflight-checks">${checks}</ul></div>
        <div class="detail-section"><h4>Sizing & Liquidity</h4>${summaryHtml}</div>
    `;

    if (result.all_passed) {
        el('preflight-arb-id').value = arbId;
        el('preflight-size').value = parseFloat(result.suggested_size_usd || 0).toFixed(2);
        actions.style.display = 'flex';
    } else {
        body.innerHTML += '<div class="exec-progress"><span class="exec-status-failed">Preflight failed - resolve issues before executing</span></div>';
    }
}

function closePreflightModal() {
    const modal = el('preflight-modal');
    if (modal) modal.style.display = 'none';
}

async function confirmExecution() {
    const arbId = el('preflight-arb-id').value;
    const size = parseFloat(el('preflight-size').value);
    if (!arbId || !size || size <= 0) return;

    const body = el('preflight-body');
    const actions = el('preflight-actions');
    if (actions) actions.style.display = 'none';
    if (body) body.innerHTML += '<div class="exec-progress">Placing order...</div>';

    try {
        const resp = await fetch(`/api/execution/execute/${encodeURIComponent(arbId)}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ size_usd: size }),
        });
        const result = await resp.json();
        if (!resp.ok) {
            if (body) body.innerHTML += `<div class="exec-progress"><span class="exec-status-failed">Error: ${result.detail || resp.status}</span></div>`;
            return;
        }

        const statusClass = result.status === 'complete' ? 'exec-status-complete' : result.status === 'partial' ? 'exec-status-partial' : 'exec-status-failed';
        const isTwoLeg = result.poly_order_id && result.kalshi_order_id;
        const statusLabel = result.status === 'complete' ? (isTwoLeg ? 'Both legs filled' : 'Order filled') : result.status === 'partial' ? 'Partial execution - check orders' : 'Execution failed';
        if (body) body.innerHTML += `
            <div class="exec-progress">
                <span class="${statusClass}">${statusLabel}</span><br>
                Total cost: ${formatUSD(result.total_cost_usd)}<br>
                ${result.slippage_from_ticket ? `Slippage: ${formatUSD(result.slippage_from_ticket)}` : ''}
            </div>
        `;
        refreshTickets();
    } catch (err) {
        if (body) body.innerHTML += `<div class="exec-progress"><span class="exec-status-failed">Network error: ${err.message}</span></div>`;
    }
}

// --- Flippenings Tab ---
async function refreshFlippenings() {
    const [active, history, stats] = await Promise.all([
        fetchJSON('/api/flippenings/active?limit=50'),
        fetchJSON('/api/flippenings/history?limit=20'),
        fetchJSON('/api/flippenings/stats'),
    ]);

    // Stats cards — stats is a list of per-category rows, aggregate for summary
    if (stats && Array.isArray(stats) && stats.length > 0) {
        const total = stats.reduce((s, r) => s + (r.total_signals || 0), 0);
        const wAvg = (field) => {
            const weighted = stats.reduce((s, r) => s + (parseFloat(r[field]) || 0) * (r.total_signals || 0), 0);
            return total > 0 ? weighted / total : null;
        };
        el('flip-total').textContent = total;
        const wr = wAvg('win_rate_pct');
        el('flip-winrate').textContent = wr != null ? wr.toFixed(1) + '%' : '-';
        const ap = wAvg('avg_pnl');
        el('flip-avgpnl').textContent = ap != null ? (ap >= 0 ? '+' : '') + ap.toFixed(4) : '-';
        const ah = wAvg('avg_hold_minutes');
        el('flip-avghold').textContent = ah != null ? ah.toFixed(0) + 'm' : '-';
    } else {
        el('flip-total').textContent = 0;
        el('flip-winrate').textContent = '-';
        el('flip-avgpnl').textContent = '-';
        el('flip-avghold').textContent = '-';
    }

    // Active table
    const activeTbody = el('flip-active-tbody');
    if (activeTbody && active) {
        if (active.length === 0) {
            activeTbody.innerHTML = '<tr><td colspan="7" class="empty-state">No active flippenings</td></tr>';
        } else {
            activeTbody.innerHTML = active.map(a => `
                <tr>
                    <td>${a.category || a.sport || ''}</td>
                    <td>${a.side || ''}</td>
                    <td>${formatUSD(a.price)}</td>
                    <td>${a.target_exit ? formatUSD(a.target_exit) : '-'}</td>
                    <td>${a.stop_loss ? formatUSD(a.stop_loss) : '-'}</td>
                    <td>${a.suggested_size ? formatUSD(a.suggested_size) : '-'}</td>
                    <td>${a.confidence ? formatPct(a.confidence) : '-'}</td>
                </tr>
            `).join('');
        }
    }

    // History table
    const histTbody = el('flip-history-tbody');
    if (histTbody && history) {
        if (history.length === 0) {
            histTbody.innerHTML = '<tr><td colspan="7" class="empty-state">No history</td></tr>';
        } else {
            histTbody.innerHTML = history.map(h => {
                const pnl = h.realized_pnl != null ? (parseFloat(h.realized_pnl) >= 0 ? '+' : '') + parseFloat(h.realized_pnl).toFixed(4) : '-';
                return `
                    <tr>
                        <td>${h.category || h.sport || ''}</td>
                        <td>${h.side || ''}</td>
                        <td>${h.entry_price ? formatUSD(h.entry_price) : '-'}</td>
                        <td>${h.exit_price ? formatUSD(h.exit_price) : '-'}</td>
                        <td>${pnl}</td>
                        <td>${h.hold_minutes ? parseFloat(h.hold_minutes).toFixed(0) + 'm' : '-'}</td>
                        <td>${h.exit_reason || '-'}</td>
                    </tr>
                `;
            }).join('');
        }
    }
}

// --- Live Price Ticker (SSE) ---
let tickerReconnectDelay = 1000;
const TICKER_MAX_RECONNECT_DELAY = 30000;

function initTickerSSE() {
    if (tickerSource) { tickerSource.close(); tickerSource = null; }
    const sseUrl = _apiToken ? `/api/flippenings/price-stream?token=${_apiToken}` : '/api/flippenings/price-stream';
    tickerSource = new EventSource(sseUrl);
    tickerSource.addEventListener('status', function(e) {
        tickerReconnectDelay = 1000;
        const data = JSON.parse(e.data);
        setTickerStatus(data.status === 'idle' ? 'idle' : 'connected');
        if (data.status === 'idle') {
            const tbody = el('ticker-tbody');
            if (tbody) tbody.innerHTML = '<tr><td colspan="8" class="empty-state">Engine not active</td></tr>';
        }
    });
    tickerSource.addEventListener('snapshot', function(e) {
        tickerReconnectDelay = 1000;
        setTickerStatus('connected');
        const data = JSON.parse(e.data);
        renderTickerTable(data.markets || []);
    });
    tickerSource.addEventListener('heartbeat', function() {
        tickerReconnectDelay = 1000;
        setTickerStatus('connected');
    });
    tickerSource.onerror = function() {
        setTickerStatus('disconnected');
        tickerSource.close();
        tickerSource = null;
        setTimeout(function() {
            tickerReconnectDelay = Math.min(tickerReconnectDelay * 2, TICKER_MAX_RECONNECT_DELAY);
            initTickerSSE();
        }, tickerReconnectDelay);
    };
}

function setTickerStatus(state) {
    const banner = el('ticker-status');
    if (!banner) return;
    banner.className = 'ticker-banner';
    if (state === 'idle') {
        banner.classList.add('ticker-idle');
        banner.textContent = 'Engine not active';
    } else if (state === 'connected') {
        banner.classList.add('ticker-connected');
        banner.textContent = 'Live - connected';
    } else {
        banner.classList.add('ticker-disconnected');
        banner.textContent = 'Disconnected - reconnecting...';
    }
}

function deviationClass(pct) {
    const abs = Math.abs(pct);
    if (abs < 5) return 'deviation-green';
    if (abs < 10) return 'deviation-amber';
    return 'deviation-red';
}

function renderTickerTable(markets) {
    const tbody = el('ticker-tbody');
    if (!tbody) return;
    if (markets.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="empty-state">No live data</td></tr>';
        return;
    }
    markets.sort((a, b) => Math.abs(b.deviation_pct) - Math.abs(a.deviation_pct));
    tbody.innerHTML = markets.map(m => {
        const devCls = deviationClass(m.deviation_pct);
        const devSign = m.deviation_pct >= 0 ? '+' : '';
        const bl = m.baseline_yes != null ? parseFloat(m.baseline_yes).toFixed(3) : '-';
        const title = (m.market_title || '').length > 40
            ? m.market_title.substring(0, 40) + '...'
            : m.market_title || '';
        return `<tr>
            <td title="${m.market_title || ''}">${title}</td>
            <td><span class="category-badge">${m.category || '-'}</span></td>
            <td>${parseFloat(m.yes_mid).toFixed(3)}</td>
            <td>${bl}</td>
            <td class="${devCls}">${devSign}${m.deviation_pct.toFixed(2)}%</td>
            <td><canvas class="sparkline-canvas" data-market="${m.market_id}"></canvas></td>
            <td>${parseFloat(m.spread).toFixed(3)}</td>
            <td>${m.book_depth_bids}/${m.book_depth_asks}</td>
        </tr>`;
    }).join('');
    renderSparklines(markets);
}

function renderSparklines(markets) {
    // Store history per market for sparklines (keep last 60 points in memory)
    markets.forEach(m => {
        if (!tickerSparklines[m.market_id]) tickerSparklines[m.market_id] = [];
        const hist = tickerSparklines[m.market_id];
        hist.push(parseFloat(m.yes_mid));
        if (hist.length > 60) hist.shift();
    });
    document.querySelectorAll('.sparkline-canvas').forEach(canvas => {
        const mid = canvas.dataset.market;
        const hist = tickerSparklines[mid];
        if (!hist || hist.length < 2) return;
        drawSparkline(canvas, hist);
    });
}

function drawSparkline(canvas, data) {
    const ctx = canvas.getContext('2d');
    const w = canvas.width = 100;
    const h = canvas.height = 30;
    ctx.clearRect(0, 0, w, h);
    const min = Math.min(...data);
    const max = Math.max(...data);
    const range = max - min || 0.001;
    ctx.strokeStyle = '#4fc3f7';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    data.forEach((v, i) => {
        const x = (i / (data.length - 1)) * w;
        const y = h - ((v - min) / range) * (h - 4) - 2;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    });
    ctx.stroke();
}

// --- Discovery Tab ---
async function refreshDiscovery() {
    const [snapshots, history, alerts] = await Promise.all([
        fetchJSON('/api/flippenings/discovery-health?limit=1'),
        fetchJSON('/api/flippenings/discovery-health/history?hours=24'),
        fetchJSON('/api/flippenings/discovery-health/alerts?limit=20'),
    ]);
    renderDiscoverySummary(snapshots);
    renderDiscoveryCategoryChart(snapshots);
    renderDiscoveryHitrateChart(history);
    renderDiscoveryMethodChart(snapshots);
    renderDiscoveryAlerts(alerts);
    renderDiscoveryUnclassified(snapshots);
}

function renderDiscoverySummary(snapshots) {
    if (snapshots && snapshots.length > 0) {
        const s = snapshots[0];
        el('disc-total-scanned').textContent = s.total_scanned || 0;
        el('disc-classified').textContent = s.sports_found || 0;
        const hr = s.hit_rate != null ? (parseFloat(s.hit_rate) * 100).toFixed(1) + '%' : '-';
        el('disc-hit-rate').textContent = hr;
        el('disc-unclassified').textContent = s.unclassified_candidates || 0;
    }
}

function renderDiscoveryCategoryChart(snapshots) {
    if (!snapshots || snapshots.length === 0) return;
    const bySport = snapshots[0].by_sport;
    if (!bySport) return;
    const parsed = typeof bySport === 'string' ? JSON.parse(bySport) : bySport;
    const labels = Object.keys(parsed);
    const values = Object.values(parsed);
    const ctx = el('disc-category-chart');
    if (discCategoryChart) discCategoryChart.destroy();
    discCategoryChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [{ label: 'Markets', data: values, backgroundColor: '#4fc3f7' }],
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { labels: { color: '#e0e0e0' } } },
            scales: {
                x: { ticks: { color: '#a0a0b0' }, grid: { color: '#2a2a4a' } },
                y: { ticks: { color: '#a0a0b0' }, grid: { color: '#2a2a4a' } },
            },
        },
    });
}

function renderDiscoveryHitrateChart(history) {
    if (!history || history.length === 0) return;
    const sorted = [...history].sort((a, b) => new Date(a.cycle_timestamp) - new Date(b.cycle_timestamp));
    const ctx = el('disc-hitrate-chart');
    if (discHitrateChart) discHitrateChart.destroy();
    discHitrateChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: sorted.map(h => new Date(h.cycle_timestamp).toLocaleTimeString()),
            datasets: [{
                label: 'Hit Rate %',
                data: sorted.map(h => parseFloat(h.hit_rate) * 100),
                borderColor: '#66bb6a', backgroundColor: 'rgba(102, 187, 106, 0.1)',
                fill: true, tension: 0.3, pointRadius: 2,
            }],
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { labels: { color: '#e0e0e0' } } },
            scales: {
                x: { ticks: { color: '#a0a0b0', maxTicksLimit: 12 }, grid: { color: '#2a2a4a' } },
                y: { ticks: { color: '#a0a0b0', callback: v => v.toFixed(1) + '%' }, grid: { color: '#2a2a4a' } },
            },
        },
    });
}

function renderDiscoveryMethodChart(snapshots) {
    if (!snapshots || snapshots.length === 0) return;
    const bySport = snapshots[0].by_sport;
    if (!bySport) return;
    const methods = ['slug', 'tag', 'title', 'fuzzy', 'manual_override'];
    const colors = ['#4fc3f7', '#66bb6a', '#ffa726', '#e94560', '#ffd54f'];
    const parsed = typeof bySport === 'string' ? JSON.parse(bySport) : bySport;
    const total = Object.values(parsed).reduce((s, v) => s + v, 0);
    const ctx = el('disc-method-chart');
    if (discMethodChart) discMethodChart.destroy();
    discMethodChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: methods,
            datasets: [{ data: methods.map(() => Math.max(Math.round(total / methods.length), 1)), backgroundColor: colors }],
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { labels: { color: '#e0e0e0' }, position: 'right' } },
        },
    });
}

function renderDiscoveryAlerts(alerts) {
    const tbody = el('disc-alerts-tbody');
    if (!tbody) return;
    if (!alerts || alerts.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" class="empty-state">No degradation alerts</td></tr>';
        return;
    }
    tbody.innerHTML = alerts.map(a => `
        <tr>
            <td>${formatTime(a.created_at)}</td>
            <td>${a.alert_text || ''}</td>
            <td>${a.category || '-'}</td>
            <td>${a.resolved ? 'Yes' : 'No'}</td>
        </tr>
    `).join('');
}

function renderDiscoveryUnclassified(snapshots) {
    const tbody = el('disc-unclassified-tbody');
    if (!tbody) return;
    if (!snapshots || snapshots.length === 0) {
        tbody.innerHTML = '<tr><td colspan="2" class="empty-state">No data</td></tr>';
        return;
    }
    // unclassified_sample is not stored in DB; show placeholder from latest snapshot
    tbody.innerHTML = '<tr><td colspan="2" class="empty-state">Run a live scan to see unclassified markets</td></tr>';
}

// --- WS Health Tab ---
async function refreshWsHealth() {
    const [latest, history, events] = await Promise.all([
        fetchJSON('/api/flippening/ws-telemetry'),
        fetchJSON('/api/flippening/ws-telemetry/history?hours=1'),
        fetchJSON('/api/flippening/ws-telemetry/events?limit=50'),
    ]);
    renderWsStatusBanner(latest);
    renderWsMetrics(latest);
    renderWsSchemaGauge(latest);
    renderWsSchemaTrend(history);
    renderWsThroughputChart(history);
    renderWsEventsTable(events);
}

function renderWsStatusBanner(data) {
    const banner = el('ws-status-banner');
    const label = el('ws-status-label');
    if (!banner || !label) return;
    banner.className = 'ws-status-banner';
    if (!data) {
        banner.classList.add('ws-idle');
        label.textContent = 'Idle - Start flip-watch to see telemetry';
        return;
    }
    const state = data.connection_state || 'unknown';
    if (state === 'connected') {
        banner.classList.add('ws-connected');
        label.textContent = 'Connected - Messages flowing';
    } else if (state === 'disconnected') {
        banner.classList.add('ws-disconnected');
        label.textContent = 'Disconnected - Reconnecting...';
    } else if (state === 'stalled') {
        banner.classList.add('ws-stalled');
        label.textContent = 'Stalled - No messages received';
    } else {
        banner.classList.add('ws-idle');
        label.textContent = 'Idle - Start flip-watch to see telemetry';
    }
}

function renderWsMetrics(data) {
    el('ws-total-received').textContent = data ? (data.messages_received || 0) : '-';
    el('ws-total-parsed').textContent = data ? (data.messages_parsed || 0) : '-';
    el('ws-total-failed').textContent = data ? (data.messages_failed || 0) : '-';
    const hitRate = data && data.book_cache_hit_rate != null
        ? (parseFloat(data.book_cache_hit_rate) * 100).toFixed(1) + '%'
        : '-';
    el('ws-cache-hit-rate').textContent = hitRate;
}

function renderWsSchemaGauge(data) {
    const canvas = el('ws-schema-gauge');
    const pctEl = el('ws-schema-pct');
    if (!canvas || !pctEl) return;
    const rate = data ? parseFloat(data.schema_match_rate || 1.0) : 1.0;
    const pct = rate * 100;
    pctEl.textContent = pct.toFixed(1) + '%';
    if (pct > 90) pctEl.style.color = '#66bb6a';
    else if (pct >= 50) pctEl.style.color = '#ffa726';
    else pctEl.style.color = '#e94560';
    drawGaugeArc(canvas, rate);
}

function drawGaugeArc(canvas, fraction) {
    const ctx = canvas.getContext('2d');
    const w = canvas.width;
    const h = canvas.height;
    const cx = w / 2;
    const cy = h - 10;
    const r = Math.min(cx, cy) - 10;
    ctx.clearRect(0, 0, w, h);
    // Background arc
    ctx.beginPath();
    ctx.arc(cx, cy, r, Math.PI, 0, false);
    ctx.lineWidth = 14;
    ctx.strokeStyle = '#2a2a4a';
    ctx.stroke();
    // Value arc
    const endAngle = Math.PI + (Math.PI * Math.min(fraction, 1.0));
    ctx.beginPath();
    ctx.arc(cx, cy, r, Math.PI, endAngle, false);
    ctx.lineWidth = 14;
    if (fraction > 0.9) ctx.strokeStyle = '#66bb6a';
    else if (fraction >= 0.5) ctx.strokeStyle = '#ffa726';
    else ctx.strokeStyle = '#e94560';
    ctx.lineCap = 'round';
    ctx.stroke();
}

function renderWsSchemaTrend(history) {
    if (!history || history.length === 0) return;
    const sorted = [...history].sort((a, b) => new Date(a.snapshot_time) - new Date(b.snapshot_time));
    const ctx = el('ws-schema-trend-chart');
    if (!ctx) return;
    if (wsSchemaTrendChart) wsSchemaTrendChart.destroy();
    wsSchemaTrendChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: sorted.map(h => new Date(h.snapshot_time).toLocaleTimeString()),
            datasets: [{
                label: 'Schema Match %',
                data: sorted.map(h => parseFloat(h.schema_match_rate) * 100),
                borderColor: '#66bb6a', backgroundColor: 'rgba(102, 187, 106, 0.1)',
                fill: true, tension: 0.3, pointRadius: 1,
            }],
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { labels: { color: '#e0e0e0' } } },
            scales: {
                x: { ticks: { color: '#a0a0b0', maxTicksLimit: 10 }, grid: { color: '#2a2a4a' } },
                y: { min: 0, max: 100, ticks: { color: '#a0a0b0', callback: v => v + '%' }, grid: { color: '#2a2a4a' } },
            },
        },
    });
}

function renderWsThroughputChart(history) {
    if (!history || history.length < 2) return;
    const sorted = [...history].sort((a, b) => new Date(a.snapshot_time) - new Date(b.snapshot_time));
    const labels = [];
    const received = [];
    const parsed = [];
    const avgData = [];
    for (let i = 1; i < sorted.length; i++) {
        labels.push(new Date(sorted[i].snapshot_time).toLocaleTimeString());
        const dr = sorted[i].messages_received - sorted[i - 1].messages_received;
        const dp = sorted[i].messages_parsed - sorted[i - 1].messages_parsed;
        received.push(Math.max(dr, 0));
        parsed.push(Math.max(dp, 0));
    }
    // Rolling average (window of 6 = ~30s at 5s interval)
    const win = 6;
    for (let i = 0; i < received.length; i++) {
        const start = Math.max(0, i - win + 1);
        const slice = received.slice(start, i + 1);
        avgData.push(slice.reduce((s, v) => s + v, 0) / slice.length);
    }
    const ctx = el('ws-throughput-chart');
    if (!ctx) return;
    if (wsThroughputChart) wsThroughputChart.destroy();
    wsThroughputChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [
                { label: 'Received', data: received, borderColor: '#4fc3f7', backgroundColor: 'rgba(79,195,247,0.1)', fill: true, tension: 0.3, pointRadius: 1 },
                { label: 'Parsed', data: parsed, borderColor: '#66bb6a', backgroundColor: 'rgba(102,187,106,0.1)', fill: true, tension: 0.3, pointRadius: 1 },
                { label: '30s Avg', data: avgData, borderColor: '#ffd54f', borderDash: [5, 3], fill: false, tension: 0.3, pointRadius: 0 },
            ],
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { labels: { color: '#e0e0e0' } } },
            scales: {
                x: { ticks: { color: '#a0a0b0', maxTicksLimit: 12 }, grid: { color: '#2a2a4a' } },
                y: { ticks: { color: '#a0a0b0' }, grid: { color: '#2a2a4a' }, beginAtZero: true },
            },
        },
    });
}

function wsEventClass(evtType) {
    if (evtType === 'stall_detected') return 'ws-event-stall';
    if (evtType === 'stall_reconnect') return 'ws-event-reconnect';
    if (evtType === 'ws_disconnected') return 'ws-event-disconnect';
    if (evtType === 'ws_connected') return 'ws-event-connect';
    return '';
}

function renderWsEventsTable(events) {
    const tbody = el('ws-events-tbody');
    if (!tbody) return;
    if (!events || events.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="empty-state">No stall or reconnect events</td></tr>';
        return;
    }
    tbody.innerHTML = events.map(e => `
        <tr>
            <td>${formatTime(e.event_time)}</td>
            <td class="${wsEventClass(e.event_type)}">${e.event_type}</td>
            <td>${e.prev_state || '-'}</td>
            <td>${e.new_state || '-'}</td>
            <td>${e.messages_received_at_event || 0}</td>
        </tr>
    `).join('');
}

// --- Scan Trigger ---
async function triggerScan() {
    const btn = el('scan-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Scanning...'; }
    setStatus('Running scan...');

    const result = await postJSON('/api/scan');

    if (btn) { btn.disabled = false; btn.textContent = 'Run Scan'; }

    if (result) {
        setStatus(`Scan complete: ${(result.opportunities || []).length} opportunities`);
        refreshActiveTab();
    } else {
        setStatus('Scan failed');
    }
}

// --- Refresh Logic ---
async function refreshActiveTab() {
    updateRefreshTime();
    switch (activeTab) {
        case 'opportunities': await refreshOpportunities(); break;
        case 'health': await refreshHealth(); break;
        case 'alerts': await refreshAlerts(); break;
        case 'tickets': await refreshTickets(); break;
        case 'flippenings': await refreshFlippenings(); break;
        case 'discovery': await refreshDiscovery(); break;
        case 'wshealth': await refreshWsHealth(); break;
        case 'autoexec': await refreshAutoExec(); break;
        case 'balances': await refreshBalances(); break;
    }
}

function startAutoRefresh() {
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(refreshActiveTab, 30000);
}

// --- Auto-Exec Activity Feed ---
let _aeSSE = null;

const _AE_EVENT_META = {
    considering:     { icon: '🔍', label: 'Considering',     cls: 'ae-info' },
    criteria_passed: { icon: '✓',  label: 'Criteria OK',     cls: 'ae-ok' },
    criteria_failed: { icon: '✗',  label: 'Criteria Fail',   cls: 'ae-bad' },
    size_computed:   { icon: '📏', label: 'Size',            cls: 'ae-info' },
    size_rejected:   { icon: '✗',  label: 'Size Fail',       cls: 'ae-bad' },
    critic_checking: { icon: '🤖', label: 'Critic...',       cls: 'ae-active' },
    critic_approved: { icon: '✓',  label: 'Critic OK',       cls: 'ae-ok' },
    critic_rejected: { icon: '✗',  label: 'Critic Blocked',  cls: 'ae-bad' },
    slippage_check:  { icon: '📊', label: 'Slippage...',     cls: 'ae-active' },
    slippage_ok:     { icon: '✓',  label: 'Slippage OK',     cls: 'ae-ok' },
    slippage_failed: { icon: '✗',  label: 'Slippage Fail',   cls: 'ae-bad' },
    placing:         { icon: '⏳', label: 'Placing Order',   cls: 'ae-active' },
    placed_complete: { icon: '💰', label: 'Filled',          cls: 'ae-ok' },
    placed_partial:  { icon: '⚠️', label: 'Partial Fill',    cls: 'ae-warn' },
    placed_failed:   { icon: '✗',  label: 'Order Failed',    cls: 'ae-bad' },
};

function _aeDetail(ev) {
    const t = ev.type;
    if (t === 'considering') return `${ev.spread || ''} conf ${ev.confidence || ''}`;
    if (t === 'criteria_failed') return (ev.reasons || []).slice(0, 2).join('; ');
    if (t === 'size_computed')   return ev.size_usd != null ? `$${parseFloat(ev.size_usd).toFixed(2)}` : '';
    if (t === 'critic_approved') return ev.skipped ? 'no flags' : `${(ev.flags || []).length} flag(s)`;
    if (t === 'critic_rejected') return (ev.reasoning || '').substring(0, 60);
    if (t === 'slippage_ok')    return `poly ${ev.poly_slip ?? ''} / kalshi ${ev.kalshi_slip ?? ''}`;
    if (t === 'slippage_failed') return `poly ${ev.poly_slip ?? ''} / kalshi ${ev.kalshi_slip ?? ''}`;
    if (t === 'placing')        return ev.size_usd != null ? `$${parseFloat(ev.size_usd).toFixed(2)}` : '';
    if (t.startsWith('placed_')) return ev.cost_usd != null ? `cost $${parseFloat(ev.cost_usd).toFixed(2)}` : '';
    return '';
}

function renderAeEvent(ev) {
    const meta = _AE_EVENT_META[ev.type] || { icon: '·', label: ev.type, cls: 'ae-info' };
    const ts = ev.ts ? new Date(ev.ts).toLocaleTimeString() : '';
    const title = (ev.title || ev.arb_id || '').substring(0, 40);
    const detail = _aeDetail(ev);
    const shortId = (ev.arb_id || '').substring(0, 8);
    const pipe = ev.pipeline && ev.pipeline !== 'unknown' ? `<span class="ae-pipeline ae-pipe-${ev.pipeline}">${ev.pipeline}</span>` : '';
    return `<div class="ae-event ${meta.cls}">
        <span class="ae-icon">${meta.icon}</span>
        <span class="ae-time">${ts}</span>
        ${pipe}
        <span class="ae-stage">${meta.label}</span>
        <span class="ae-title" title="${ev.title || ''}">${title || shortId}</span>
        ${detail ? `<span class="ae-detail">${detail}</span>` : ''}
    </div>`;
}

function prependAeEvent(ev) {
    const feed = el('ae-activity-feed');
    if (!feed) return;
    const empty = feed.querySelector('.empty-state');
    if (empty) empty.remove();
    feed.insertAdjacentHTML('afterbegin', renderAeEvent(ev));
    // trim to 80 items
    while (feed.children.length > 80) feed.removeChild(feed.lastChild);
}

function setAeBannerConnected(state, extra) {
    const banner = el('ae-activity-banner');
    const label = el('ae-activity-label');
    if (!banner || !label) return;
    banner.className = 'ws-status-banner';
    if (state === 'connecting') {
        banner.classList.add('ws-idle');
        label.textContent = 'Connecting to pipeline feed…';
    } else if (state === 'connected') {
        banner.classList.add('ws-connected');
        label.textContent = 'Live — pipeline feed connected';
    } else if (state === 'heartbeat') {
        banner.classList.add('ws-connected');
        label.textContent = `Live · ♥ ${extra || new Date().toLocaleTimeString()}`;
    } else {
        banner.classList.add('ws-disconnected');
        label.textContent = 'Disconnected — reconnecting…';
    }
}

function _aeConnectedNotice() {
    const feed = el('ae-activity-feed');
    if (!feed) return;
    const empty = feed.querySelector('.empty-state');
    if (empty) empty.remove();
    if (!feed.querySelector('.ae-notice')) {
        feed.insertAdjacentHTML('afterbegin',
            `<div class="ae-notice">Feed live — no pipeline events yet. ` +
            `Set mode to <strong>Auto</strong> and ensure the scanner is running.</div>`);
    }
}

function initAeSSE() {
    if (_aeSSE) { _aeSSE.close(); _aeSSE = null; }
    const url = _apiToken
        ? `/api/auto-execution/activity-stream?token=${_apiToken}`
        : '/api/auto-execution/activity-stream';
    setAeBannerConnected('connecting');
    _aeSSE = new EventSource(url);
    _aeSSE.onopen = function() {
        setAeBannerConnected('connected');
    };
    _aeSSE.addEventListener('history', function(e) {
        setAeBannerConnected('connected');
        const feed = el('ae-activity-feed');
        if (!feed) return;
        const events = JSON.parse(e.data);
        if (events.length === 0) {
            _aeConnectedNotice();
            return;
        }
        feed.innerHTML = events.slice().reverse().map(renderAeEvent).join('');
    });
    _aeSSE.addEventListener('activity', function(e) {
        setAeBannerConnected('connected');
        const notice = el('ae-activity-feed')?.querySelector('.ae-notice');
        if (notice) notice.remove();
        prependAeEvent(JSON.parse(e.data));
    });
    _aeSSE.addEventListener('heartbeat', function() {
        setAeBannerConnected('heartbeat', new Date().toLocaleTimeString());
    });
    _aeSSE.onerror = function() {
        setAeBannerConnected('disconnected');
        _aeSSE.close(); _aeSSE = null;
        setTimeout(initAeSSE, 5000);
    };
}

function closeAeSSE() {
    if (_aeSSE) { _aeSSE.close(); _aeSSE = null; }
}

// --- Auto-Exec Tab ---
async function refreshAutoExec() {
    await Promise.all([refreshAutoExecStatus(), refreshAutoExecStats(), refreshAutoExecLog(), refreshOpenPositions()]);
    if (!_aeSSE) initAeSSE();
}

async function refreshAutoExecStatus() {
    const data = await fetchJSON('/api/auto-execution/status');
    if (!data) return;
    const modeSelect = el('autoexec-mode-select');
    const mode = data.mode || 'off';
    if (modeSelect) modeSelect.value = mode;
    const badge = el('ae-mode-badge');
    if (badge) {
        badge.textContent = mode.toUpperCase();
        badge.className = `ae-mode-badge ae-mode-${mode}`;
    }
    function _updateBreakers(prefix, breakers) {
        if (!breakers) return;
        breakers.forEach(cb => {
            const card = el(prefix + '-breaker-' + cb.breaker_type);
            if (!card) return;
            const val = card.querySelector('.value');
            if (val) {
                if (cb.tripped) {
                    val.textContent = 'TRIPPED';
                    val.className = 'value breaker-tripped';
                    val.title = cb.reason || '';
                } else {
                    val.textContent = 'OK';
                    val.className = 'value breaker-ok';
                    val.title = '';
                }
            }
        });
    }
    _updateBreakers('arb', data.arb_breakers);
    _updateBreakers('flip', data.flip_breakers);
}

async function refreshAutoExecStats() {
    const data = await fetchJSON('/api/auto-execution/stats?days=1');
    if (!data) return;
    setText('ae-trades', data.total_trades || '0');
    setText('ae-winloss', `${data.wins || 0}/${data.losses || 0}`);
    const pnl = parseFloat(data.total_pnl || '0');
    const pnlEl = el('ae-pnl');
    if (pnlEl) {
        pnlEl.textContent = formatUSD(pnl);
        pnlEl.style.color = pnl >= 0 ? 'var(--success)' : 'var(--danger)';
    }
    const slip = parseFloat(data.avg_slippage || '0');
    setText('ae-slippage', (slip * 100).toFixed(3) + '%');
}

function formatHoldTime(openedAt) {
    if (!openedAt) return '-';
    const ms = Date.now() - new Date(openedAt).getTime();
    const sec = Math.floor(ms / 1000);
    if (sec < 60) return `${sec}s`;
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min}m ${sec % 60}s`;
    const hrs = Math.floor(min / 60);
    return `${hrs}h ${min % 60}m`;
}

function holdTimeClass(openedAt, maxHoldMin) {
    if (!openedAt || !maxHoldMin) return '';
    const elapsed = (Date.now() - new Date(openedAt).getTime()) / 60000;
    if (elapsed >= maxHoldMin) return 'hold-expired';
    if (elapsed >= maxHoldMin * 0.75) return 'hold-warning';
    return '';
}

async function refreshOpenPositions() {
    const data = await fetchJSON('/api/auto-execution/positions');
    const tbody = el('open-positions-tbody');
    if (!tbody) return;
    if (!data || data.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="empty-state">No open positions</td></tr>';
        setText('ae-open-count', '0');
        setText('ae-open-exposure', '$0.00');
        return;
    }
    setText('ae-open-count', String(data.length));
    const exposure = data.reduce((sum, p) => sum + parseFloat(p.entry_cost_usd || p.entry_price || 0), 0);
    setText('ae-open-exposure', formatUSD(exposure));
    _positionCache = {};
    tbody.innerHTML = data.map(p => {
        const arbId = p.arb_id || '';
        _positionCache[arbId] = p;
        const title = p.market_title || '';
        const market = title || p.market_id || p.poly_market_id || p.kalshi_ticker || arbId || '-';
        const shortMarket = market.length > 32 ? market.substring(0, 32) + '...' : market;
        const side = p.side || '-';
        const size = p.size_contracts ? `${p.size_contracts} ct` : (p.entry_cost_usd ? formatUSD(p.entry_cost_usd) : '-');
        const entry = p.entry_price ? formatUSD(p.entry_price) : (p.entry_spread ? formatPct(p.entry_spread) : '-');
        const maxHold = p.max_hold_minutes ? parseInt(p.max_hold_minutes) : null;
        const target = maxHold ? `${maxHold}m` : '-';
        const holdClass = holdTimeClass(p.opened_at, maxHold);
        const posType = p.pipeline_type || (p.market_id ? 'flip' : 'arb');
        const typeBadge = posType === 'flip'
            ? '<span class="badge badge-approved">Flip</span>'
            : '<span class="badge badge-pending">Arb</span>';
        const closeBtn = posType === 'flip'
            ? `<button class="btn btn-danger btn-sm" onclick="event.stopPropagation(); closePosition('${arbId}')">Close</button>`
            : '<span style="color:var(--text-secondary);font-size:11px">atomic</span>';
        return `<tr style="cursor:pointer" onclick="openPositionDetail('${arbId}')">
            <td title="${title || market}">${shortMarket}</td>
            <td>${typeBadge}</td>
            <td>${side}</td>
            <td>${size}</td>
            <td>${entry}</td>
            <td class="${holdClass}">${formatHoldTime(p.opened_at)}</td>
            <td>${target}</td>
            <td>${closeBtn}</td>
        </tr>`;
    }).join('');
}

let _positionCache = {};

function openPositionDetail(arbId) {
    const p = _positionCache[arbId];
    if (!p) return;
    const modal = el('position-modal');
    const body = el('position-modal-body');
    const titleEl = el('position-modal-title');
    if (!modal || !body) return;

    const title = p.market_title || p.market_id || arbId;
    if (titleEl) titleEl.textContent = 'Position Detail';

    const slug = p.market_slug || '';
    const marketUrl = slug ? `https://polymarket.com/event/${slug}` : '';
    const marketLink = marketUrl
        ? `<a href="${marketUrl}" target="_blank" rel="noopener" class="market-link">${title}</a>`
        : title;

    const posType = p.pipeline_type || 'flip';
    const side = p.side || '-';
    const size = p.size_contracts ? `${p.size_contracts} contracts` : '-';
    const entry = p.entry_price ? formatUSD(p.entry_price) : '-';
    const maxHold = p.max_hold_minutes ? `${parseInt(p.max_hold_minutes)}m` : '-';
    const holdTime = formatHoldTime(p.opened_at);
    const opened = p.opened_at ? new Date(p.opened_at).toLocaleString() : '-';
    const tokenId = p.token_id || '-';
    const shortToken = tokenId.length > 16 ? tokenId.substring(0, 8) + '...' + tokenId.slice(-6) : tokenId;
    const marketId = p.market_id || '-';

    const closeBtnHtml = posType === 'flip'
        ? `<button class="btn btn-danger btn-sm" onclick="closePosition('${arbId}'); closePositionModal();">Close Position</button>`
        : '';

    body.innerHTML = `
        <div class="detail-section">
            <div class="detail-grid">
                <div class="detail-row"><span class="detail-label">Market</span><span class="detail-value">${marketLink}</span></div>
                <div class="detail-row"><span class="detail-label">Pipeline</span><span class="detail-value">${posType.toUpperCase()}</span></div>
                <div class="detail-row"><span class="detail-label">Side</span><span class="detail-value">${side.toUpperCase()}</span></div>
                <div class="detail-row"><span class="detail-label">Size</span><span class="detail-value">${size}</span></div>
                <div class="detail-row"><span class="detail-label">Entry Price</span><span class="detail-value">${entry}</span></div>
                <div class="detail-row"><span class="detail-label">Hold Time</span><span class="detail-value">${holdTime}</span></div>
                <div class="detail-row"><span class="detail-label">Max Hold</span><span class="detail-value">${maxHold}</span></div>
                <div class="detail-row"><span class="detail-label">Opened</span><span class="detail-value">${opened}</span></div>
                <div class="detail-row"><span class="detail-label">Market ID</span><span class="detail-value" style="font-size:11px;word-break:break-all">${marketId}</span></div>
                <div class="detail-row"><span class="detail-label">Token ID</span><span class="detail-value" style="font-size:11px" title="${tokenId}">${shortToken}</span></div>
            </div>
        </div>
        ${closeBtnHtml ? `<div class="modal-actions" style="margin-top:12px">${closeBtnHtml}</div>` : ''}
    `;
    modal.style.display = 'flex';
}

function closePositionModal(event) {
    if (event && event.target !== event.currentTarget) return;
    const modal = el('position-modal');
    if (modal) modal.style.display = 'none';
}

async function closePosition(arbId) {
    if (!arbId) return;
    if (!confirm('Close this position? This will place a market exit order.')) return;
    const result = await postJSON(`/api/execution/flip-exit/${arbId}`);
    if (result) {
        setStatus(`Position close submitted for ${arbId.substring(0, 10)}...`);
        await refreshOpenPositions();
    } else {
        setStatus(`Failed to close position ${arbId.substring(0, 10)}...`);
    }
}

let _aeLogCache = {};

async function refreshAutoExecLog() {
    const data = await fetchJSON('/api/auto-execution/log?limit=20');
    const tbody = el('autoexec-log-tbody');
    if (!tbody) return;
    if (!data || data.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No auto-trades yet</td></tr>';
        return;
    }
    _aeLogCache = {};
    tbody.innerHTML = data.map(e => {
        _aeLogCache[e.id] = e;
        const cv = e.critic_verdict;
        let criticBadge = '<span class="badge badge-critic-skipped">skipped</span>';
        if (cv && !cv.skipped) {
            if (cv.approved) {
                criticBadge = `<span class="badge badge-critic-approved">approved (${(cv.risk_flags || []).length})</span>`;
            } else {
                criticBadge = `<span class="badge badge-critic-rejected">rejected (${(cv.risk_flags || []).length})</span>`;
            }
        }
        const statusClass = e.status === 'executed' ? 'badge-approved' :
                           e.status === 'failed' ? 'badge-cancelled' :
                           e.status === 'partial' ? 'badge-expired' :
                           e.status === 'rejected' ? 'badge-expired' : 'badge-pending';
        return `<tr class="clickable" onclick="openAeLogDetail('${e.id}')">
            <td>${shortTime(e.created_at)}</td>
            <td title="${e.arb_id}">${(e.arb_id || '').substring(0, 10)}...</td>
            <td>${formatPct(e.trigger_spread_pct)}</td>
            <td>${formatUSD(e.size_usd)}</td>
            <td>${criticBadge}</td>
            <td><span class="badge ${statusClass}">${e.status}</span></td>
            <td>${e.duration_ms != null ? e.duration_ms + 'ms' : '-'}</td>
        </tr>`;
    }).join('');
}

function openAeLogDetail(logId) {
    const e = _aeLogCache[logId];
    if (!e) return;
    const modal = el('ae-log-modal');
    const body = el('ae-log-modal-body');
    const title = el('ae-log-modal-title');
    if (!modal || !body) return;

    const statusClass = e.status === 'executed' ? 'badge-approved' :
                       e.status === 'failed' ? 'badge-cancelled' :
                       e.status === 'rejected' ? 'badge-expired' : 'badge-pending';

    // Rejection reasons — JSONB may arrive as string or object depending on asyncpg codec
    const snap = typeof e.criteria_snapshot === 'string'
        ? JSON.parse(e.criteria_snapshot)
        : (e.criteria_snapshot || {});
    const reasons = snap.rejection_reasons || [];
    let reasonsHtml = '';
    if (reasons.length > 0) {
        reasonsHtml = `
        <div class="detail-section">
            <h4>Rejection Reasons</h4>
            <ul class="ae-reasons-list">
                ${reasons.map(r => `<li>${r}</li>`).join('')}
            </ul>
        </div>`;
    }

    // Critic verdict — JSONB may arrive as string or object
    const cv = typeof e.critic_verdict === 'string'
        ? JSON.parse(e.critic_verdict)
        : (e.critic_verdict || null);
    let criticHtml = '';
    if (cv) {
        const cvStatus = cv.skipped ? 'Skipped (mechanical check)' :
                        cv.approved ? '✓ Approved' : '✗ Rejected';
        const cvColor = cv.skipped ? 'var(--text-secondary)' :
                       cv.approved ? 'var(--success)' : 'var(--danger)';
        const flags = (cv.risk_flags || []);
        criticHtml = `
        <div class="detail-section">
            <h4>AI Critic</h4>
            <div class="detail-grid">
                <div class="detail-row"><span class="detail-label">Decision</span>
                    <span class="detail-value" style="color:${cvColor}">${cvStatus}</span></div>
                ${cv.confidence != null ? `<div class="detail-row"><span class="detail-label">Confidence</span>
                    <span class="detail-value">${(cv.confidence * 100).toFixed(0)}%</span></div>` : ''}
                ${cv.reasoning ? `<div class="detail-row"><span class="detail-label">Reasoning</span>
                    <span class="detail-value ae-reasoning">${cv.reasoning}</span></div>` : ''}
                ${flags.length > 0 ? `<div class="detail-row"><span class="detail-label">Risk Flags</span>
                    <span class="detail-value">${flags.map(f => `<span class="ae-flag">${f}</span>`).join(' ')}</span></div>` : ''}
            </div>
        </div>`;
    }

    // Balances — JSONB may arrive as string or object
    const bals = typeof e.pre_exec_balances === 'string'
        ? JSON.parse(e.pre_exec_balances)
        : (e.pre_exec_balances || {});
    const balsHtml = (bals.poly != null || bals.kalshi != null) ? `
        <div class="detail-section">
            <h4>Balances at Trigger</h4>
            <div class="detail-grid">
                ${bals.poly != null ? `<div class="detail-row"><span class="detail-label">Polymarket</span><span class="detail-value">${formatUSD(bals.poly)}</span></div>` : ''}
                ${bals.kalshi != null ? `<div class="detail-row"><span class="detail-label">Kalshi</span><span class="detail-value">${formatUSD(bals.kalshi)}</span></div>` : ''}
            </div>
        </div>` : '';

    if (title) title.textContent = `Auto-Exec — ${e.status.toUpperCase()}`;
    body.innerHTML = `
        <div class="detail-section">
            <h4>Summary</h4>
            <div class="detail-grid">
                <div class="detail-row"><span class="detail-label">Status</span>
                    <span class="detail-value"><span class="badge ${statusClass}">${e.status}</span></span></div>
                <div class="detail-row"><span class="detail-label">Arb ID</span>
                    <span class="detail-value" style="font-size:11px;word-break:break-all">${e.arb_id || '-'}</span></div>
                <div class="detail-row"><span class="detail-label">Spread</span>
                    <span class="detail-value">${formatPct(e.trigger_spread_pct)}</span></div>
                <div class="detail-row"><span class="detail-label">Confidence</span>
                    <span class="detail-value">${e.trigger_confidence != null ? formatPct(e.trigger_confidence) : '-'}</span></div>
                <div class="detail-row"><span class="detail-label">Size</span>
                    <span class="detail-value">${formatUSD(e.size_usd)}</span></div>
                <div class="detail-row"><span class="detail-label">Source</span>
                    <span class="detail-value">${e.source || '-'}</span></div>
                <div class="detail-row"><span class="detail-label">Duration</span>
                    <span class="detail-value">${e.duration_ms != null ? e.duration_ms + 'ms' : '-'}</span></div>
                <div class="detail-row"><span class="detail-label">Time</span>
                    <span class="detail-value">${formatTime(e.created_at)}</span></div>
            </div>
        </div>
        ${reasonsHtml}
        ${criticHtml}
        ${balsHtml}
    `;
    modal.style.display = 'flex';
}

function closeAeLogModal(evt) {
    if (evt && evt.target !== el('ae-log-modal')) return;
    const modal = el('ae-log-modal');
    if (modal) modal.style.display = 'none';
}

function setText(id, text) {
    const e = el(id);
    if (e) e.textContent = text;
}

async function postJSONBody(url, body) {
    try {
        const resp = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!resp.ok) {
            const text = await resp.text().catch(() => '');
            setStatus(`POST error ${resp.status}: ${text.substring(0, 120)}`);
            return null;
        }
        return await resp.json();
    } catch (err) {
        console.error(`POST failed: ${url}`, err);
        return null;
    }
}

async function setAutoExecMode() {
    const modeSelect = el('autoexec-mode-select');
    const applyBtn = el('autoexec-apply-btn');
    if (!modeSelect) return;
    const mode = modeSelect.value;
    if (applyBtn) { applyBtn.disabled = true; applyBtn.textContent = 'Applying...'; }
    const result = await postJSONBody('/api/auto-execution/enable', { mode });
    if (applyBtn) { applyBtn.disabled = false; applyBtn.textContent = 'Apply'; }
    if (result) {
        setStatus(`Auto-exec mode set to "${result.mode}"`);
        await refreshAutoExecStatus();
    }
}

async function killAutoExec() {
    const btn = el('autoexec-kill-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Killing...'; }
    const result = await postJSON('/api/auto-execution/disable');
    if (btn) { btn.disabled = false; btn.textContent = 'KILL SWITCH'; }
    if (result) {
        setStatus('Auto-exec killed — mode set to OFF');
        await refreshAutoExecStatus();
    }
}

// --- Balances Tab ---
async function refreshBalances() {
    let data = null;
    try {
        data = await fetchJSON('/api/execution/balances');
    } catch (err) {
        console.error('refreshBalances fetch error', err);
    }
    if (!data) {
        setText('bal-poly', 'N/A');
        setText('bal-kalshi', 'N/A');
        setText('bal-total', 'N/A');
        setText('bal-suggested', 'N/A');
        setText('bal-exposure', 'N/A');
        setText('bal-remaining', 'N/A');
        setText('bal-pnl', 'N/A');
        setText('bal-positions', 'N/A');
        const tbody = el('bal-constraints-tbody');
        if (tbody) tbody.innerHTML = '<tr><td colspan="3" class="empty-state">Capital manager not available</td></tr>';
        return;
    }
    setText('bal-poly', formatUSD(data.poly_balance));
    setText('bal-kalshi', formatUSD(data.kalshi_balance));
    setText('bal-total', formatUSD(data.total_balance));
    setText('bal-suggested', formatUSD(data.suggested_size_usd));
    setText('bal-exposure', formatUSD(data.current_exposure));
    setText('bal-remaining', formatUSD(data.remaining_capacity));
    const pnl = parseFloat(data.daily_pnl || '0');
    const pnlEl = el('bal-pnl');
    if (pnlEl) {
        pnlEl.textContent = formatUSD(pnl);
        pnlEl.style.color = pnl >= 0 ? 'var(--success)' : 'var(--danger)';
    }
    setText('bal-positions', data.open_positions);
    const tbody = el('bal-constraints-tbody');
    if (!tbody) return;
    const rows = (data.constraints || []);
    if (rows.length === 0) {
        tbody.innerHTML = '<tr><td colspan="3" class="empty-state">No constraints</td></tr>';
        return;
    }
    tbody.innerHTML = rows.map(c => {
        const badge = c.ok
            ? '<span class="badge badge-approved">OK</span>'
            : '<span class="badge badge-cancelled">BLOCKED</span>';
        return `<tr><td>${c.name}</td><td>${badge}</td><td>${c.detail}</td></tr>`;
    }).join('');
}

// --- Init ---
document.addEventListener('DOMContentLoaded', () => {
    // Tab click handlers
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => switchTab(btn.dataset.tab));
    });

    // Refresh button
    const refreshBtn = el('refresh-btn');
    if (refreshBtn) refreshBtn.addEventListener('click', refreshActiveTab);

    // Scan button
    const scanBtn = el('scan-btn');
    if (scanBtn) scanBtn.addEventListener('click', triggerScan);

    // Alert type filter
    const alertFilter = el('alert-type-filter');
    if (alertFilter) alertFilter.addEventListener('change', refreshAlerts);

    // Ticket filters
    const ticketFilter = el('ticket-status-filter');
    if (ticketFilter) ticketFilter.addEventListener('change', refreshTickets);
    const ticketCatFilter = el('ticket-category-filter');
    if (ticketCatFilter) ticketCatFilter.addEventListener('change', refreshTickets);
    const ticketTypeFilter = el('ticket-type-filter');
    if (ticketTypeFilter) ticketTypeFilter.addEventListener('change', refreshTickets);

    // Close modals on Escape key or overlay click
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') { closeTicketModal(); closeExecuteModal(); closePreflightModal(); }
    });
    const modalOverlay = el('ticket-modal');
    if (modalOverlay) {
        modalOverlay.addEventListener('click', (e) => {
            if (e.target === modalOverlay) closeTicketModal();
        });
    }
    const execOverlay = el('execute-modal');
    if (execOverlay) {
        execOverlay.addEventListener('click', (e) => {
            if (e.target === execOverlay) closeExecuteModal();
        });
    }

    // Preflight modal overlay click
    const preflightOverlay = el('preflight-modal');
    if (preflightOverlay) {
        preflightOverlay.addEventListener('click', (e) => {
            if (e.target === preflightOverlay) closePreflightModal();
        });
    }

    // Initial load
    switchTab('opportunities');
    startAutoRefresh();
    initTickerSSE();
    refreshExecStatus();
});
