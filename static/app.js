/* app.js — loop stream + backend selector */

const state = {
    ws: null,
    wsConnected: false,
    chartInstance: null,

    metrics: {
        total: 0, blocked: 0, allowed: 0, blocked_rate: 0.0,
        attack_total: 0, clean_total: 0, miss: 0, false_positive: 0,
        fp_rate: 0.0, fn_rate: 0.0
    },

    // streaming queue
    eventQueue: [],
    queueTimer: null,

    // loop streaming state
    loopMode: false,
    history: [],        // [{type:'prompt'|'response', data}]
    historyIndex: 0,

    // backend list
    backbones: [],
    activeBackboneId: null
};

// Config
const RENDER_INTERVAL_MS = 250;
const MAX_STREAM_ITEMS = 200;
const MAX_HISTORY_ITEMS = 500;

// DOM cache
const els = {
    wsDot: document.getElementById('ws-status-dot'),
    wsText: document.getElementById('ws-status-text'),

    mainPrompts: document.getElementById('stream-main-prompts'),
    mainResponses: document.getElementById('stream-main-responses'),
    baselinePrompts: document.getElementById('stream-baseline-prompts'),
    baselineResponses: document.getElementById('stream-baseline-responses'),

    batchId: document.getElementById('batch-status-id'),
    batchProgress: document.getElementById('batch-progress'),

    mTotal: document.getElementById('m-total'),
    mBlocked: document.getElementById('m-blocked'),
    mBlockedRate: document.getElementById('m-blocked-rate'),
    mAllowed: document.getElementById('m-allowed'),
    mFpRate: document.getElementById('m-fp-rate'),
    mFp: document.getElementById('m-fp'),
    mCleanTotal: document.getElementById('m-clean-total'),
    mFnRate: document.getElementById('m-fn-rate'),
    mMiss: document.getElementById('m-miss'),
    mAttackTotal: document.getElementById('m-attack-total'),

    backendSelect: document.getElementById('backend-select')
};

/* ------------------- INIT ------------------- */

document.addEventListener('DOMContentLoaded', () => {
    initWebSocket();
    initChart();
    initLoopSwitch();
    startQueueProcessor();
    initBackendSelector();

    document.getElementById('btn-run-single')
        .addEventListener('click', runSingle);
    document.getElementById('btn-run-batch')
        .addEventListener('click', runBatch);
});

/* ------------------- LOOP SWITCH ------------------- */

function initLoopSwitch() {
    const sw = document.getElementById('loop-switch');
    if (!sw) return;

    sw.addEventListener('click', () => {
        sw.classList.toggle('on');
        state.loopMode = sw.classList.contains('on');
        console.log('Loop Stream:', state.loopMode);
    });
}

/* ------------------- BACKEND SELECTOR ------------------- */

async function initBackendSelector() {
    const select = els.backendSelect;
    if (!select) return;

    select.disabled = true;
    select.innerHTML = '<option>Loading...</option>';

    try {
        const res = await fetch('/api/backbones');
        const data = await res.json();

        state.backbones = data.backbones || [];
        state.activeBackboneId = data.active_id || null;

        renderBackendDropdown();

        select.addEventListener('change', onBackendChange);
    } catch (err) {
        console.error('Error loading backbones:', err);
        select.innerHTML = '<option>Error loading backends</option>';
    }
}

function renderBackendDropdown() {
    const select = els.backendSelect;
    if (!select) return;

    select.innerHTML = '';

    if (!state.backbones.length) {
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = 'No backends found';
        select.appendChild(opt);
        select.disabled = true;
        select.title = '';
        return;
    }

    select.disabled = false;

    state.backbones.forEach((b) => {
        const opt = document.createElement('option');
        opt.value = b.id;
        let label = b.display || b.id;
        if (b.available === false) {
            label += ' (unavailable)';
        }
        opt.textContent = label;
        if (b.id === state.activeBackboneId) {
            opt.selected = true;
        }
        select.appendChild(opt);
    });

    const active = state.backbones.find(b => b.id === state.activeBackboneId);
    if (active && active.detail) {
        select.title = active.detail;
    } else {
        select.title = '';
    }
}

async function onBackendChange(e) {
    const id = e.target.value;
    if (!id) return;

    try {
        const res = await fetch('/api/backbones/select', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ backbone_id: id })
        });
        const data = await res.json();

        state.backbones = data.backbones || state.backbones;
        state.activeBackboneId = data.active_id || id;

        renderBackendDropdown();
    } catch (err) {
        console.error('Error switching backend:', err);
        alert('Failed to switch backend.');
    }
}

/* ------------------- WEBSOCKET ------------------- */

function initWebSocket() {
    const wsUrl = 'ws://127.0.0.1:8000/ws/dashboard';
    state.ws = new WebSocket(wsUrl);

    state.ws.onopen = () => {
        state.wsConnected = true;
        updateConnectionStatus();
    };

    state.ws.onclose = () => {
        state.wsConnected = false;
        updateConnectionStatus();
        setTimeout(initWebSocket, 3000);
    };

    state.ws.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            handleWsMessage(msg);
        } catch (e) {
            console.error('WS parse error:', e);
        }
    };
}

function updateConnectionStatus() {
    if (state.wsConnected) {
        els.wsDot.classList.add('connected');
        els.wsText.textContent = 'Connected';
    } else {
        els.wsDot.classList.remove('connected');
        els.wsText.textContent = 'Disconnected';
    }
}

function handleWsMessage(msg) {
    const { type, data } = msg;

    if (type === 'prompt' || type === 'response') {
        const evt = { type, data };
        state.eventQueue.push(evt);

        // push into loop history
        state.history.push(evt);
        if (state.history.length > MAX_HISTORY_ITEMS) {
            state.history.shift();
            if (state.historyIndex > 0) {
                state.historyIndex--;
            }
        }
        return;
    }

    if (type === 'metrics') {
        updateMetrics(data);
        return;
    }

    if (type === 'batch_status') {
        updateBatchStatus(data);
        return;
    }

    console.warn('Unknown WS type:', type);
}

/* ------------------- QUEUE & LOOP ------------------- */

function startQueueProcessor() {
    if (state.queueTimer) clearInterval(state.queueTimer);

    state.queueTimer = setInterval(() => {
        // if queue empty and loop enabled, recycle from history
        if (state.eventQueue.length === 0 &&
            state.loopMode &&
            state.history.length > 0) {

            if (state.historyIndex >= state.history.length) {
                state.historyIndex = 0;
            }
            const evt = state.history[state.historyIndex];
            state.historyIndex = (state.historyIndex + 1) % state.history.length;
            if (evt) {
                state.eventQueue.push(evt);
            }
        }

        if (state.eventQueue.length === 0) return;

        const event = state.eventQueue.shift();
        if (!event) return;

        if (event.type === 'prompt') renderPrompt(event.data);
        if (event.type === 'response') renderResponse(event.data);
    }, RENDER_INTERVAL_MS);
}

/* ------------------- RENDER HELPERS ------------------- */

function shouldAutoScroll(container) {
    const threshold = 50;
    return container.scrollHeight - container.scrollTop - container.clientHeight < threshold;
}

function scrollToBottom(container) {
    container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' });
}

function appendLog(container, element) {
    container.appendChild(element);
    if (container.childElementCount > MAX_STREAM_ITEMS) {
        container.removeChild(container.firstChild);
    }
}

/* ------------------- RENDER PROMPTS ------------------- */

function renderPrompt(data) {
    const container = data.pipeline === 'baseline'
        ? els.baselinePrompts
        : els.mainPrompts;

    const autoScroll = shouldAutoScroll(container);

    const div = document.createElement('div');
    div.className = 'log-entry';

    let labelBadge = '';
    if (data.label) {
        const css = data.label === 'clean' ? 'clean' : 'danger';
        labelBadge = `<span class="badge ${css}">${data.label}</span>`;
    }

    const idDisplay = data.prompt_id ? `#${data.prompt_id}` : 'Single';

    div.innerHTML = `
        <div class="log-meta">
            <span>${idDisplay} ${labelBadge}</span>
            <span style="font-size:0.65rem;color:#888;">${data.source.toUpperCase()}</span>
        </div>
        <div class="log-text" title="${escapeHtml(data.text || '')}">
            ${truncate(data.text || '', 140)}
        </div>
    `;

    appendLog(container, div);
    if (autoScroll) scrollToBottom(container);
}

/* ------------------- RENDER RESPONSES ------------------- */

function renderResponse(data) {
    const isMain = data.pipeline === 'main';
    const container = isMain ? els.mainResponses : els.baselineResponses;
    const autoScroll = shouldAutoScroll(container);

    const div = document.createElement('div');
    div.className = 'log-entry';

    let statusHtml = '';
    if (isMain && data.decision) {
        const css = data.decision === 'allow' ? 'allow' : 'block';
        const riskText = (data.risk_score != null)
            ? `Risk: ${(data.risk_score * 100).toFixed(1)}%`
            : '';
        statusHtml = `
            <span class="badge ${css}">${data.decision.toUpperCase()}</span>
            <span style="font-size:0.7rem;color:#888;">${riskText}</span>`;
    } else if (!isMain) {
        statusHtml = `<span style="font-size:0.7rem;color:#666;">
            ${truncate(data.model_name || '', 20)}
        </span>`;
    }

    const idDisplay = data.prompt_id ? `#${data.prompt_id}` : 'Single';

    div.innerHTML = `
        <div class="log-meta">
            <span>${idDisplay}</span>
            <span>${statusHtml}</span>
        </div>
        <div class="log-text" title="${escapeHtml(data.text || '')}">
            ${truncate(data.text || '', 140)}
        </div>
    `;

    appendLog(container, div);
    if (autoScroll) scrollToBottom(container);

    // real-time chart update from main pipeline
    if (isMain && data.label_probs) {
        updateChart(data.label_probs);
    }
}

/* ------------------- CHART ------------------- */

function initChart() {
    const ctx = document.getElementById('safety-chart').getContext('2d');

    const labels = ['P.Inj', 'Malicious', 'Sem.Pois', 'Emb.Anom', 'Clean'];
    Chart.defaults.color = '#A0A0A0';
    Chart.defaults.borderColor = '#333';

    state.chartInstance = new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                data: [0, 0, 0, 0, 1],
                backgroundColor: [
                    '#ef4444',
                    '#f59e0b',
                    '#eab308',
                    '#d946ef',
                    '#10b981'
                ],
                borderRadius: 4,
                borderWidth: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: { beginAtZero: true, max: 1.0, grid: { color: '#333' } },
                x: { grid: { display: false } }
            },
            plugins: {
                legend: { display: false }
            }
        }
    });
}

function updateChart(labelProbs) {
    if (!state.chartInstance) return;

    const order = ['prompt_injection','malicious','semantic_poisoning','embedding_anomaly','clean'];
    const newData = order.map(k => labelProbs[k] || 0);

    state.chartInstance.data.datasets[0].data = newData;
    state.chartInstance.update();
}

/* ------------------- METRICS ------------------- */

function updateMetrics(data) {
    state.metrics = data;

    els.mTotal.textContent = data.total;
    els.mBlocked.textContent = data.blocked;
    els.mBlockedRate.textContent = (data.blocked_rate * 100).toFixed(1) + '% Rate';
    els.mAllowed.textContent = data.allowed;

    els.mFpRate.textContent = (data.fp_rate * 100).toFixed(2) + '%';
    els.mFp.textContent = data.false_positive;
    els.mCleanTotal.textContent = data.clean_total;

    els.mFnRate.textContent = (data.fn_rate * 100).toFixed(2) + '%';
    els.mMiss.textContent = data.miss;
    els.mAttackTotal.textContent = data.attack_total;
}

function updateBatchStatus(data) {
    const idStr = data.batch_id ? data.batch_id.substring(0, 8) : '-';
    els.batchId.textContent = `ID: ${idStr}`;
    els.batchProgress.textContent = `${data.processed} / ${data.requested}`;
    els.batchProgress.style.color =
        data.status === 'finished'
            ? 'var(--success-color)'
            : 'var(--warning-color)';
}

/* ------------------- API CALLS ------------------- */

async function runSingle() {
    const text = document.getElementById('single-prompt-text').value;
    if (!text.trim()) {
        alert('Enter a prompt');
        return;
    }

    const payload = {
        prompt: text,
        mode: document.getElementById('single-mode').value,
        risk_threshold: parseFloat(document.getElementById('single-threshold').value),
        log_to_db: document.getElementById('single-log-db').checked,
        label_hint: document.getElementById('single-hint').value || 'clean'
    };

    try {
        await fetch('/api/single', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
    } catch (err) {
        console.error('Single run error:', err);
        alert('Error running single prompt.');
    }
}

async function runBatch() {
    const payload = {
        label_mode: document.getElementById('batch-label-mode').value,
        batch_size: parseInt(document.getElementById('batch-size').value, 10),
        mode: document.getElementById('batch-mode').value,
        risk_threshold: parseFloat(document.getElementById('batch-threshold').value),
        log_to_db: document.getElementById('batch-log-db').checked
    };

    try {
        const res = await fetch('/api/batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();

        els.batchId.textContent =
            `ID: ${data.batch_id ? data.batch_id.substring(0, 8) : '...'}`;
        els.batchProgress.textContent =
            `${data.processed || 0} / ${payload.batch_size}`;
        els.batchProgress.style.color = 'var(--text-primary)';
    } catch (err) {
        console.error('Batch run error:', err);
        alert('Error running batch.');
    }
}

/* ------------------- UTILS ------------------- */

function truncate(str, n) {
    if (!str) return '';
    return str.length > n ? str.substr(0, n - 1) + '...' : str;
}

function escapeHtml(text) {
    if (!text) return '';
    return text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}
