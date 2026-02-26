// ============================================================
// Arb Scanner Dashboard
// ============================================================

// --- State ---
let activeTab = 'opportunities';
let refreshTimer = null;
let spreadChart = null;
let healthChart = null;

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

function shortTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return d.toLocaleTimeString();
}

async function fetchJSON(url) {
    try {
        const resp = await fetch(url);
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
        const resp = await fetch(url, { method: 'POST' });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return await resp.json();
    } catch (err) {
        console.error(`POST failed: ${url}`, err);
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
    const data = await fetchJSON('/api/tickets');
    const tbody = el('tickets-tbody');
    if (!tbody || !data) return;

    if (data.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="empty-state">No pending tickets</td></tr>';
        return;
    }

    tbody.innerHTML = data.map(t => `
        <tr>
            <td title="${t.arb_id}">${(t.arb_id || '').substring(0, 12)}...</td>
            <td>${formatUSD(t.expected_cost)}</td>
            <td>${formatUSD(t.expected_profit)}</td>
            <td><span class="badge badge-${t.status}">${t.status}</span></td>
            <td>${shortTime(t.created_at)}</td>
            <td>
                <button class="btn btn-success btn-sm" onclick="approveTicket('${t.arb_id}')">Approve</button>
                <button class="btn btn-danger btn-sm" onclick="expireTicket('${t.arb_id}')">Expire</button>
            </td>
        </tr>
    `).join('');
}

async function approveTicket(arbId) {
    const result = await postJSON(`/api/tickets/${encodeURIComponent(arbId)}/approve`);
    if (result) refreshTickets();
}

async function expireTicket(arbId) {
    const result = await postJSON(`/api/tickets/${encodeURIComponent(arbId)}/expire`);
    if (result) refreshTickets();
}

// --- Flippenings Tab ---
async function refreshFlippenings() {
    const [active, history, stats] = await Promise.all([
        fetchJSON('/api/flippenings/active?limit=50'),
        fetchJSON('/api/flippenings/history?limit=20'),
        fetchJSON('/api/flippenings/stats'),
    ]);

    // Stats cards — stats is a list of per-sport rows, aggregate for summary
    if (stats && Array.isArray(stats) && stats.length > 0) {
        const total = stats.reduce((s, r) => s + (r.total || 0), 0);
        const wAvg = (field) => {
            const weighted = stats.reduce((s, r) => s + (parseFloat(r[field]) || 0) * (r.total || 0), 0);
            return total > 0 ? weighted / total : null;
        };
        el('flip-total').textContent = total;
        const wr = wAvg('win_rate');
        el('flip-winrate').textContent = wr != null ? (wr * 100).toFixed(1) + '%' : '-';
        const ap = wAvg('avg_pnl');
        el('flip-avgpnl').textContent = ap != null ? (ap >= 0 ? '+' : '') + ap.toFixed(4) : '-';
        const ah = wAvg('avg_hold');
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
                    <td>${a.sport || ''}</td>
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
                        <td>${h.sport || ''}</td>
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
    }
}

function startAutoRefresh() {
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(refreshActiveTab, 30000);
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

    // Initial load
    switchTab('opportunities');
    startAutoRefresh();
});
