/**
 * RouteWeave Admin Dashboard
 *
 * Features:
 *   - Admin key login
 *   - System stats cards
 *   - Jobs table with status filtering
 *   - Job deletion
 *   - Auto-refresh every 10 seconds
 */

const API_BASE = window.location.port === '3000' ? '/api' : 'http://localhost:8000';

let adminKey = localStorage.getItem('routeweave_admin_key') || '';

// ─────────────────────────────────────────────────────────────────────────
// DOM References
// ─────────────────────────────────────────────────────────────────────────

const loginScreen = document.getElementById('login-screen');
const dashboard = document.getElementById('dashboard');
const adminKeyInput = document.getElementById('admin-key-input');
const loginBtn = document.getElementById('login-btn');
const loginError = document.getElementById('login-error');
const statusFilter = document.getElementById('status-filter');
const refreshJobsBtn = document.getElementById('refresh-jobs-btn');

// ─────────────────────────────────────────────────────────────────────────
// Auth
// ─────────────────────────────────────────────────────────────────────────

async function tryAutoLogin() {
    if (!adminKey) return;
    const ok = await verifyAdminKey(adminKey);
    if (ok) showDashboard();
}

async function verifyAdminKey(key) {
    try {
        const resp = await fetch(`${API_BASE}/admin/stats`, {
            headers: { 'X-Admin-Key': key },
        });
        return resp.status === 200;
    } catch (e) {
        return false;
    }
}

loginBtn.addEventListener('click', async () => {
    const key = adminKeyInput.value.trim();
    if (!key) return;
    loginError.textContent = '';
    const ok = await verifyAdminKey(key);
    if (ok) {
        adminKey = key;
        localStorage.setItem('routeweave_admin_key', key);
        showDashboard();
    } else {
        loginError.textContent = 'Invalid admin key.';
    }
});

adminKeyInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') loginBtn.click();
});

function showDashboard() {
    loginScreen.style.display = 'none';
    dashboard.style.display = 'flex';
    loadStats();
    loadJobs();
    startAutoRefresh();
}

// ─────────────────────────────────────────────────────────────────────────
// Stats
// ─────────────────────────────────────────────────────────────────────────

async function loadStats() {
    try {
        const resp = await fetch(`${API_BASE}/admin/stats`, {
            headers: { 'X-Admin-Key': adminKey },
        });
        if (!resp.ok) return;
        const data = await resp.json();

        document.getElementById('stat-total-jobs').textContent = data.jobs.total;
        document.getElementById('stat-completed').textContent = data.jobs.completed;

        const total = data.jobs.total;
        const completed = data.jobs.completed;
        const rate = total > 0 ? ((completed / total) * 100).toFixed(1) + '%' : '0%';
        document.getElementById('stat-success-rate').textContent = rate;

        document.getElementById('stat-avg-improvement').textContent = data.routes.avg_improvement_pct + '%';
        document.getElementById('stat-saved').textContent = data.routes.total_distance_saved_km;
        document.getElementById('stat-cache').textContent = data.geocoding_cache.cached_addresses;

        renderHealth(data);
    } catch (e) {
        console.error('Stats error:', e);
    }
}

function renderHealth(data) {
    const grid = document.getElementById('health-grid');
    const statuses = data.jobs.by_status || {};
    const total = data.jobs.total;

    const pending = statuses.pending || 0;
    const failed = statuses.failed || 0;
    const inFlight = (statuses.geocoding || 0) + (statuses.optimizing || 0);

    const items = [
        { label: 'API Service', state: 'online', count: 'Operational' },
        { label: 'Database', state: 'online', count: 'Connected' },
        { label: 'In Transit', state: 'online', count: `${inFlight} job${inFlight === 1 ? '' : 's'} processing` },
        { label: 'Queue', state: pending < 10 ? 'online' : 'warn', count: `${pending} pending` },
        { label: 'Failed Jobs', state: failed === 0 ? 'online' : 'warn', count: `${failed} of ${total}` },
        { label: 'Geocode Cache', state: 'online', count: `${data.geocoding_cache.total_hits} hits` },
    ];

    grid.innerHTML = items.map(item => `
        <div class="health-item">
            <span class="health-dot ${item.state}" aria-hidden="true"></span>
            <div>
                <div class="health-label">${item.label}</div>
                <div class="health-count">${item.count}</div>
            </div>
        </div>
    `).join('');

    const updated = document.getElementById('last-updated');
    if (updated) updated.textContent = `Updated ${new Date().toLocaleTimeString()} · refresh 10s`;
}

// ─────────────────────────────────────────────────────────────────────────
// Jobs Table
// ─────────────────────────────────────────────────────────────────────────

async function loadJobs() {
    const filter = statusFilter.value;
    const url = filter
        ? `${API_BASE}/admin/jobs?limit=50&status=${encodeURIComponent(filter)}`
        : `${API_BASE}/admin/jobs?limit=50`;

    try {
        const resp = await fetch(url, {
            headers: { 'X-Admin-Key': adminKey },
        });
        if (!resp.ok) return;
        const data = await resp.json();
        renderJobsTable(data.jobs || []);
    } catch (e) {
        console.error('Jobs error:', e);
    }
}

function renderJobsTable(jobs) {
    const tbody = document.getElementById('jobs-tbody');
    if (jobs.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" class="empty-cell">No jobs found.</td></tr>`;
        return;
    }

    tbody.innerHTML = jobs.map(job => {
        const id = escapeHtml(job.id);
        const status = escapeHtml(job.status);
        const date = new Date(job.created_at).toLocaleString(undefined, {
            month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
        });
        const routeInfo = job.route
            ? `${job.route.naive_distance_km.toFixed(1)} → ${job.route.optimized_distance_km.toFixed(1)} km<span class="saved">−${job.route.pct_improvement.toFixed(1)}%</span>`
            : '—';
        return `
            <tr data-job-id="${id}">
                <td data-label="Job ID"><span class="job-id" title="${id}">JOB-${id.slice(0, 8).toUpperCase()}</span></td>
                <td data-label="Status"><span class="badge ${status}">${status}</span></td>
                <td data-label="Created" class="cell-num">${date}</td>
                <td data-label="Depot" class="cell-depot" title="${escapeHtml(job.depot_address || '')}">${escapeHtml(job.depot_address || '—')}</td>
                <td data-label="Stops" class="cell-num">${job.stop_count}</td>
                <td data-label="Route" class="cell-route">${routeInfo}</td>
                <td class="cell-actions">
                    <button class="btn btn-danger btn-sm" data-delete="${id}" type="button">Delete</button>
                </td>
            </tr>
        `;
    }).join('');
}

async function deleteJob(jobId) {
    if (!confirm(`Delete job ${jobId.slice(0, 8)}…? This cannot be undone.`)) return;
    try {
        const resp = await fetch(`${API_BASE}/jobs/${jobId}`, {
            method: 'DELETE',
            headers: { 'X-Admin-Key': adminKey },
        });
        if (resp.status === 204) {
            loadJobs();
            loadStats();
        } else {
            alert('Failed to delete job.');
        }
    } catch (e) {
        console.error('Delete error:', e);
    }
}

document.getElementById('jobs-tbody').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-delete]');
    if (btn) deleteJob(btn.dataset.delete);
});

statusFilter.addEventListener('change', loadJobs);
refreshJobsBtn.addEventListener('click', () => { loadJobs(); loadStats(); });

// ─────────────────────────────────────────────────────────────────────────
// Auto Refresh
// ─────────────────────────────────────────────────────────────────────────

let refreshInterval = null;

function startAutoRefresh() {
    if (refreshInterval) clearInterval(refreshInterval);
    refreshInterval = setInterval(() => {
        loadStats();
        loadJobs();
    }, 10000);
}

// ─────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ─────────────────────────────────────────────────────────────────────────
// Initialize
// ─────────────────────────────────────────────────────────────────────────

tryAutoLogin();
