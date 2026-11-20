
const API_BASE = 'http://127.0.0.1:8000';
const HISTORY_KEY = 'upiShieldHistoryV1';

const $ = (id) => document.getElementById(id);

let selectedFiles = [];
let toastTimer;

function getHistory() {
    try {
        return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
    } catch {
        return [];
    }
}

function saveHistory(items) {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(items.slice(0, 100)));
    renderHistory();
    renderStats();
}

function escapeHTML(value) {
    return String(value ?? '').replace(/[&<>"']/g, c => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
    }[c]));
}

function formatBytes(bytes) {
    return bytes < 1024
        ? `${bytes} B`
        : `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

function showToast(message) {
    const t = $('toast');
    if (!t) return;

    t.textContent = message;
    t.classList.add('show');

    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove('show'), 2600);
}

function riskClass(color) {
    return ['green', 'red', 'orange'].includes(color) ? color : 'orange';
}

function getRiskColor(result) {
    if (result.verdict === 'LOW_RISK') return 'green';
    if (result.verdict === 'SUSPICIOUS') return 'red';
    return 'orange';
}

function renderStats() {
    const items = getHistory();

    $('totalScans').textContent = items.length;
    $('lowScans').textContent = items.filter(x => x.risk_color === 'green').length;
    $('highScans').textContent = items.filter(x => x.risk_color === 'red').length;
    $('reviewScans').textContent = items.filter(x => x.risk_color === 'orange').length;
}

function renderHistory() {
    const items = getHistory();
    const body = $('historyBody');

    if (!body) return;

    if (!items.length) {
        body.innerHTML = '<tr class="empty-row"><td colspan="5">No scans yet. Upload a screenshot to get started.</td></tr>';
        return;
    }

    body.innerHTML = items.slice(0, 12).map(item => `
        <tr>
            <td class="file-cell">${escapeHTML(item.filename)}</td>
            <td>${escapeHTML(item.app || 'Unknown')}</td>
            <td>${escapeHTML(item.scannedAt || '—')}</td>
            <td>${Number(item.risk_score ?? 0)}/100</td>
            <td>
                <span class="verdict-pill ${riskClass(item.risk_color)}">
                    ${escapeHTML(item.verdict_label || item.verdict || 'REVIEW')}
                </span>
            </td>
        </tr>
    `).join('');
}

function updateApiStatus(ok) {
    $('apiDot').style.background = ok ? 'var(--green)' : 'var(--red)';
    $('apiDot').style.boxShadow = ok
        ? '0 0 0 4px #34d39918'
        : '0 0 0 4px #fb718518';

    $('apiStatus').textContent = ok ? 'API connected' : 'API offline';
    $('apiHint').textContent = ok ? API_BASE : 'Start FastAPI on port 8000';
}

async function checkApi() {
    try {
        const response = await fetch(`${API_BASE}/health`, {
            cache: 'no-store'
        });

        if (!response.ok) throw Error();

        const data = await response.json();
        updateApiStatus(data.status === 'healthy');

    } catch (error) {
        updateApiStatus(false);
    }
}

function setFiles(files) {
    const incoming = [...files];
    const rejected = [];

    for (const file of incoming) {
        if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
            rejected.push(`${file.name}: unsupported format`);
            continue;
        }

        if (file.size > 10 * 1024 * 1024) {
            rejected.push(`${file.name}: exceeds 10 MB`);
            continue;
        }

        if (!selectedFiles.some(f =>
            f.name === file.name &&
            f.size === file.size &&
            f.lastModified === file.lastModified
        )) {
            selectedFiles.push(file);
        }
    }

    renderSelectedFiles();
    $('analyzeButton').disabled = selectedFiles.length === 0;

    if (rejected.length) {
        showToast(rejected.join(' · ').slice(0, 180));
    }
}

function renderSelectedFiles() {
    const box = $('selectedFiles');

    if (!selectedFiles.length) {
        box.hidden = true;
        box.innerHTML = '';
        return;
    }

    box.hidden = false;

    box.innerHTML = selectedFiles.map((file, index) => `
        <div class="file-row">
            <img class="file-thumb" src="${URL.createObjectURL(file)}" alt="">
            <div class="file-meta">
                <b>${escapeHTML(file.name)}</b>
                <small>${formatBytes(file.size)}</small>
            </div>
            <button class="remove-file" data-remove="${index}" aria-label="Remove file">×</button>
        </div>
    `).join('');

    box.querySelectorAll('[data-remove]').forEach(button => {
        button.addEventListener('click', () => {
            selectedFiles.splice(Number(button.dataset.remove), 1);
            renderSelectedFiles();
            $('analyzeButton').disabled = !selectedFiles.length;
        });
    });
}

function resultCard(result) {
    const color = getRiskColor(result);
    const details = result.payment_details || {};

    const reasons = Array.isArray(result.reasons)
        ? result.reasons.map(r => typeof r === 'string' ? r : r.reason)
        : [];

    return `
        <article class="result-card">

            <div class="result-head">
                <div>
                    <div class="result-file">${escapeHTML(result.filename || 'Uploaded screenshot')}</div>
                    <div class="result-meta">
                        ${escapeHTML(details.app || 'Unknown app')}
                        · ${escapeHTML(details.payment_status || 'Status unavailable')}
                    </div>
                </div>

                <span class="verdict-pill ${color}">
                    ${escapeHTML(result.verdict || 'INCONCLUSIVE')}
                </span>
            </div>

            <div class="result-score-row">
                <div class="score-number ${color}">
                    ${Number(result.risk_score ?? 0)}/100
                </div>

                <div class="score-bar">
                    <div class="score-fill ${color}"
                        style="width:${Math.max(0, Math.min(100, Number(result.risk_score ?? 0)))}%">
                    </div>
                </div>
            </div>

            <div class="details-grid">
                <div class="detail">
                    <small>APP</small>
                    <b>${escapeHTML(details.app || 'Not detected')}</b>
                </div>

                <div class="detail">
                    <small>AMOUNT</small>
                    <b>${details.amount ? '₹' + escapeHTML(details.amount) : 'Not found'}</b>
                </div>

                <div class="detail">
                    <small>TRANSACTION ID</small>
                    <b>${escapeHTML(details.transaction_id || 'Not found')}</b>
                </div>

                <div class="detail">
                    <small>UPI ID</small>
                    <b>${escapeHTML(details.upi_id || 'Not found')}</b>
                </div>

                <div class="detail">
                    <small>DATE</small>
                    <b>${escapeHTML(details.date || 'Not found')}</b>
                </div>

                <div class="detail">
                    <small>OCR CONFIDENCE</small>
                    <b>${escapeHTML(result.ocr_confidence ?? '—')}%</b>
                </div>
            </div>

            <div class="reasons">
                <b>Analysis notes</b><br>
                ${reasons.length
                    ? reasons.map(r => '• ' + escapeHTML(r)).join('<br>')
                    : 'No notes returned.'}
            </div>

            <div class="result-meta" style="margin-top:10px">
                Screening only — confirm the transfer in your bank/UPI app.
            </div>
        </article>
    `;
}

function showResults(results) {
    $('resultsPanel').hidden = false;

    $('resultSubtitle').textContent =
        `${results.length} screenshot${results.length === 1 ? '' : 's'} processed`;

    $('resultsList').innerHTML = results.map(resultCard).join('');

    $('resultsPanel').scrollIntoView({
        behavior: 'smooth',
        block: 'start'
    });
}

async function analyzeFiles() {
    if (!selectedFiles.length) return;

    const files = [...selectedFiles];

    selectedFiles = [];
    renderSelectedFiles();

    $('analyzeButton').disabled = true;
    $('progressWrap').hidden = false;
    $('errorMessage').hidden = true;
    $('progressBar').style.width = '0%';
    $('progressCount').textContent = `0/${files.length}`;

    const results = [];

    for (let i = 0; i < files.length; i++) {
        const file = files[i];

        $('progressText').textContent = `Analyzing ${file.name}`;

        try {
            const form = new FormData();
            form.append('file', file);

            const response = await fetch(`${API_BASE}/api/analyze`, {
                method: 'POST',
                body: form
            });

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.detail || `Server error ${response.status}`);
            }

            data.filename = file.name;
            data.risk_color = getRiskColor(data);

            results.push(data);

            const items = getHistory();

            items.unshift({
                filename: file.name,
                app: data.payment_details?.app,
                risk_score: data.risk_score,
                risk_color: data.risk_color,
                verdict: data.verdict,
                verdict_label: data.verdict,
                scannedAt: new Date().toLocaleString()
            });

            saveHistory(items);

        } catch (error) {
            results.push({
                filename: file.name,
                verdict: 'ERROR',
                risk_score: 0,
                risk_color: 'orange',
                reasons: [error.message]
            });
        }

        $('progressCount').textContent = `${i + 1}/${files.length}`;
        $('progressBar').style.width = `${((i + 1) / files.length) * 100}%`;
    }

    showResults(results);

    $('progressText').textContent = 'Analysis complete';
    $('analyzeButton').disabled = selectedFiles.length === 0;

    showToast('Screenshot analysis finished');
}

// Upload and drag-and-drop controls

$('dropZone').addEventListener('click', e => {
    if (e.target.id !== 'browseButton') {
        $('fileInput').click();
    }
});

$('dropZone').addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        $('fileInput').click();
    }
});

$('browseButton').addEventListener('click', e => {
    e.stopPropagation();
    $('fileInput').click();
});

$('fileInput').addEventListener('change', e => {
    setFiles(e.target.files);
    e.target.value = '';
});

['dragenter', 'dragover'].forEach(name => {
    $('dropZone').addEventListener(name, e => {
        e.preventDefault();
        $('dropZone').classList.add('dragging');
    });
});

['dragleave', 'drop'].forEach(name => {
    $('dropZone').addEventListener(name, e => {
        e.preventDefault();
        $('dropZone').classList.remove('dragging');
    });
});

$('dropZone').addEventListener('drop', e => {
    setFiles(e.dataTransfer.files);
});

$('analyzeButton').addEventListener('click', analyzeFiles);

$('clearHistory').addEventListener('click', () => {
    if (confirm('Clear scan history saved in this browser?')) {
        localStorage.removeItem(HISTORY_KEY);
        renderHistory();
        renderStats();
        showToast('Local scan history cleared');
    }
});

$('closeResults').addEventListener('click', () => {
    $('resultsPanel').hidden = true;
});

$('mobileMenu').addEventListener('click', () => {
    $('sidebar').classList.toggle('open');
});

document.querySelectorAll('.nav-item').forEach(link => {
    link.addEventListener('click', () => {
        $('sidebar').classList.remove('open');
    });
});

$('year').textContent = new Date().getFullYear();

renderHistory();
renderStats();
checkApi();
setInterval(checkApi, 15000);
