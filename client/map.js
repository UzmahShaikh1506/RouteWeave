/**
 * RouteWeave — Client-side map and interaction logic.
 *
 * Handles:
 *   - CSV file upload (drag-and-drop + file picker)
 *   - Job creation via API
 *   - Real-time SSE updates for job status (with polling fallback)
 *   - Leaflet map rendering (naive vs optimized routes + animation)
 *   - GPX export download
 *   - Job history listing
 *   - Pipeline tracker (Manifest → Geocode → Optimize → Dispatch)
 */

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const API_BASE = window.location.port === '3000'
    ? '/api'
    : 'http://localhost:8000';

const POLL_INTERVAL_MS = 1500;
const MAX_POLL_ATTEMPTS = 120;

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let selectedFile = null;
let map = null;
let routeLayers = {
    naive: null,
    optimized: null,
    markers: [],
    animationMarker: null,
};
let currentJobId = null;
let sseConnection = null;
let jobFinished = false;
let activeJobId = null; // job whose status we are currently tracking

function getApiKey() {
    try { return localStorage.getItem('routeweave_api_key') || ''; } catch (e) { return ''; }
}

function setApiKey(key) {
    try {
        if (key) localStorage.setItem('routeweave_api_key', key);
        else localStorage.removeItem('routeweave_api_key');
    } catch (e) { /* storage unavailable */ }
}

// ---------------------------------------------------------------------------
// DOM References
// ---------------------------------------------------------------------------

const uploadArea = document.getElementById('upload-area');
const fileInput = document.getElementById('file-input');
const uploadContent = document.getElementById('upload-content');
const depotInput = document.getElementById('depot-address');
const optimizeBtn = document.getElementById('optimize-btn');
const btnText = document.getElementById('btn-text');
const statusBar = document.getElementById('status-bar');
const statusSpinner = document.getElementById('status-spinner');
const statusText = document.getElementById('status-text');
const resultsCard = document.getElementById('results-card');
const stopsCard = document.getElementById('stops-card');
const stopList = document.getElementById('stop-list');
const historyList = document.getElementById('history-list');
const mapEmpty = document.getElementById('map-empty');
const refreshHistoryBtn = document.getElementById('refresh-history-btn');
const gpxBtn = document.getElementById('gpx-btn');
const pipelineEl = document.getElementById('pipeline');
const mapMeta = document.getElementById('map-meta');

// Stat elements
const statNaive = document.getElementById('stat-naive');
const statOptimized = document.getElementById('stat-optimized');
const statImprovement = document.getElementById('stat-improvement');
const statStops = document.getElementById('stat-stops');

// Map look: light street basemap, route colours match the stylesheet tokens
const TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const ROUTE_NAIVE_COLOR = '#9AA1AD';
const ROUTE_OPTIMIZED_COLOR = '#1D4E89';

// ---------------------------------------------------------------------------
// Pipeline tracker (Manifest → Geocode → Optimize → Dispatch)
// ---------------------------------------------------------------------------

const PIPELINE_STEPS = ['upload', 'geocoding', 'optimizing', 'completed'];
let pipelineIndex = -1;

function setPipeline(status) {
    const steps = pipelineEl.querySelectorAll('.pipeline-step');
    pipelineEl.classList.remove('complete');

    if (status === 'failed') {
        steps.forEach((el, i) => {
            el.classList.toggle('failed', i === Math.max(pipelineIndex, 0));
            el.classList.remove('active');
        });
        return;
    }

    const idx = status === 'pending' ? 0 : PIPELINE_STEPS.indexOf(status);
    pipelineIndex = idx;
    steps.forEach((el, i) => {
        el.classList.remove('active', 'done', 'failed');
        if (status === 'completed' || i < idx) el.classList.add('done');
        else if (i === idx) el.classList.add('active');
    });
    if (status === 'completed') pipelineEl.classList.add('complete');
}

function resetPipeline() {
    pipelineIndex = -1;
    pipelineEl.classList.remove('complete');
    pipelineEl.querySelectorAll('.pipeline-step').forEach(el => el.classList.remove('active', 'done', 'failed'));
}

gpxBtn.addEventListener('click', () => {
    if (currentJobId) downloadGPX(currentJobId);
});

// ---------------------------------------------------------------------------
// File Upload Handling
// ---------------------------------------------------------------------------

uploadArea.addEventListener('click', () => fileInput.click());

uploadArea.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        fileInput.click();
    }
});

fileInput.addEventListener('change', (e) => {
    if (e.target.files.length > 0) {
        handleFile(e.target.files[0]);
    }
});

// Drag and drop
uploadArea.addEventListener('dragover', (e) => {
    e.preventDefault();
    uploadArea.classList.add('drag-over');
});

uploadArea.addEventListener('dragleave', () => {
    uploadArea.classList.remove('drag-over');
});

uploadArea.addEventListener('drop', (e) => {
    e.preventDefault();
    uploadArea.classList.remove('drag-over');
    if (e.dataTransfer.files.length > 0) {
        handleFile(e.dataTransfer.files[0]);
    }
});

function handleFile(file) {
    if (!file.name.toLowerCase().endsWith('.csv')) {
        showStatus('Please upload a CSV file.', 'error');
        return;
    }

    selectedFile = file;
    uploadArea.classList.add('has-file');
    uploadContent.innerHTML = `
        <div class="file-name">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
            <span>${escapeHtml(file.name)}</span>
            <span class="file-size">(${formatFileSize(file.size)})</span>
        </div>
    `;
    optimizeBtn.disabled = false;
}

// ---------------------------------------------------------------------------
// Job Submission
// ---------------------------------------------------------------------------

optimizeBtn.addEventListener('click', async () => {
    if (!selectedFile) return;

    optimizeBtn.disabled = true;
    btnText.textContent = 'Processing...';
    resetPipeline();
    setPipeline('upload');
    showStatus('Uploading manifest and starting optimization...', 'processing');

    try {
        const formData = new FormData();
        formData.append('file', selectedFile);
        formData.append('depot_address', depotInput.value.trim());

        const submit = () => {
            const headers = {};
            const key = getApiKey();
            if (key) headers['X-API-Key'] = key;
            return fetch(`${API_BASE}/jobs`, { method: 'POST', body: formData, headers });
        };

        let response = await submit();

        if (response.status === 401) {
            const entered = window.prompt('This server requires an API key. Enter it to continue:');
            if (entered && entered.trim()) {
                setApiKey(entered.trim());
                response = await submit();
            }
        }

        if (response.status === 429) {
            setPipeline('failed');
            showStatus('Rate limit exceeded. Please wait a minute.', 'error');
            resetButton();
            return;
        }

        if (response.status === 401) {
            setApiKey('');
            setPipeline('failed');
            showStatus('Invalid or missing API key.', 'error');
            resetButton();
            return;
        }

        if (!response.ok) {
            const err = await response.json();
            setPipeline('failed');
            showStatus(`Error: ${err.detail || 'Upload failed'}`, 'error');
            resetButton();
            return;
        }

        const data = await response.json();
        const jobId = data.job_id;
        currentJobId = jobId;
        activeJobId = jobId;
        jobFinished = false;
        showStatus('Job queued, waiting for worker...', 'processing');

        if (data.status === 'completed') {
            showStatus('Route optimized successfully!', 'success');
            await loadRoute(jobId);
            refreshHistory();
            resetButton();
        } else if (data.status === 'failed') {
            showStatus(`Optimization failed: ${data.error || 'Unknown error'}`, 'error');
            refreshHistory();
            resetButton();
        } else {
            // Try SSE first, fall back to polling
            const sseOk = connectSSE(jobId);
            if (!sseOk) {
                showStatus('Using polling fallback for status updates...', 'processing');
                await pollJob(jobId);
            }
        }

    } catch (err) {
        console.error('Submit error:', err);
        setPipeline('failed');
        showStatus(`Network error: ${err.message}`, 'error');
        resetButton();
    }
});

// ---------------------------------------------------------------------------
// SSE Real-Time Updates
// ---------------------------------------------------------------------------

function connectSSE(jobId) {
    try {
        const evtSource = new EventSource(`${API_BASE}/jobs/${jobId}/events`);
        sseConnection = evtSource;

        evtSource.onmessage = (event) => {
            const data = JSON.parse(event.data);
            handleJobStatus(data.status, jobId, data.error_message);
        };

        evtSource.onerror = () => {
            evtSource.close();
            if (sseConnection === evtSource) sseConnection = null;
            // Stream dropped before a terminal state: switch to polling
            if (!jobFinished && activeJobId === jobId) {
                pollJob(jobId);
            }
        };

        return true;
    } catch (e) {
        console.warn('SSE not supported, falling back to polling');
        return false;
    }
}

function disconnectSSE() {
    if (sseConnection) {
        sseConnection.close();
        sseConnection = null;
    }
}

function handleJobStatus(status, jobId, errorMessage) {
    if (jobFinished || jobId !== activeJobId) return;
    switch (status) {
        case 'pending':
            setPipeline('pending');
            showStatus('Job queued, waiting for worker...', 'processing');
            break;
        case 'geocoding':
            setPipeline('geocoding');
            mapMeta.textContent = `Job ${jobId.slice(0, 8)} · locating addresses`;
            showStatus('Geocoding delivery addresses...', 'processing');
            break;
        case 'optimizing':
            setPipeline('optimizing');
            mapMeta.textContent = `Job ${jobId.slice(0, 8)} · computing route`;
            showStatus('Running route optimization...', 'processing');
            break;
        case 'completed':
            jobFinished = true;
            disconnectSSE();
            setPipeline('completed');
            showStatus('Route optimized and ready for dispatch.', 'success');
            loadRoute(jobId);
            refreshHistory();
            resetButton();
            break;
        case 'failed':
            jobFinished = true;
            disconnectSSE();
            setPipeline('failed');
            mapMeta.textContent = `Job ${jobId.slice(0, 8)} · failed`;
            showStatus(`Failed: ${errorMessage || 'Unknown error'}`, 'error');
            refreshHistory();
            resetButton();
            break;
        case 'not_found':
            jobFinished = true;
            disconnectSSE();
            showStatus('Job no longer exists (it may have been deleted).', 'error');
            refreshHistory();
            resetButton();
            break;
    }
}

// ---------------------------------------------------------------------------
// Polling Fallback
// ---------------------------------------------------------------------------

async function pollJob(jobId) {
    let attempts = 0;

    const poll = async () => {
        if (attempts >= MAX_POLL_ATTEMPTS) {
            showStatus('Timeout — job is still processing. Check history later.', 'error');
            resetButton();
            return;
        }
        attempts++;

        if (jobFinished || jobId !== activeJobId) return;

        try {
            const response = await fetch(`${API_BASE}/jobs/${jobId}`);
            if (response.status === 404) {
                handleJobStatus('not_found', jobId);
                return;
            }
            if (!response.ok) {
                showStatus('Failed to check job status.', 'error');
                resetButton();
                return;
            }
            const job = await response.json();
            handleJobStatus(job.status, jobId, job.error_message);

            if (job.status !== 'completed' && job.status !== 'failed') {
                setTimeout(poll, POLL_INTERVAL_MS);
            }
        } catch (err) {
            console.error('Poll error:', err);
            setTimeout(poll, POLL_INTERVAL_MS);
        }
    };

    await poll();
}

// ---------------------------------------------------------------------------
// Route Loading & Display
// ---------------------------------------------------------------------------

async function loadRoute(jobId) {
    try {
        const response = await fetch(`${API_BASE}/jobs/${jobId}/route`);
        if (!response.ok) {
            const err = await response.json();
            showStatus(`Error loading route: ${err.detail}`, 'error');
            return;
        }

        const data = await response.json();
        currentJobId = jobId;
        showResults(data);
        drawRoute(data);

    } catch (err) {
        console.error('Load route error:', err);
        showStatus(`Failed to load route: ${err.message}`, 'error');
    }
}

function showResults(data) {
    statNaive.textContent = data.naive_distance_km.toFixed(1);
    statOptimized.textContent = data.optimized_distance_km.toFixed(1);
    statImprovement.textContent = `${data.pct_improvement.toFixed(1)}%`;
    statStops.textContent = data.stops.length;

    resultsCard.style.display = 'block';
    resultsCard.style.animation = 'fadeIn 0.4s ease forwards';

    stopsCard.style.display = 'block';
    stopsCard.style.animation = 'fadeIn 0.4s ease 0.1s forwards';

    stopList.innerHTML = '';

    mapMeta.textContent = `Job ${data.job_id.slice(0, 8)} · ${data.stops.length} stops · ${data.optimized_distance_km.toFixed(1)} km`;

    // Add depot
    if (data.depot) {
        const depotEl = document.createElement('div');
        depotEl.className = 'stop-item';
        depotEl.style.animationDelay = '0s';
        depotEl.innerHTML = `
            <div class="stop-number depot">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>
            </div>
            <div class="stop-address" title="${escapeHtml(data.depot.address)}">
                <strong>Depot:</strong> ${escapeHtml(data.depot.address)}
            </div>
        `;
        stopList.appendChild(depotEl);
    }

    // Addresses that could not be geocoded
    const skipped = data.skipped_addresses || [];
    if (skipped.length > 0) {
        const warn = document.createElement('div');
        warn.className = 'stop-warning';
        const plural = skipped.length > 1;
        warn.innerHTML = `<strong>${skipped.length} address${plural ? 'es' : ''} could not be located and ${plural ? 'were' : 'was'} skipped:</strong>`
            + skipped.map(a => `<div>${escapeHtml(a)}</div>`).join('');
        stopList.prepend(warn);  // above the timeline so it doesn't break the route line
    }

    // Add stops
    data.stops.forEach((stop, idx) => {
        const el = document.createElement('div');
        el.className = 'stop-item';
        el.style.animationDelay = `${(idx + 1) * 0.05}s`;
        el.innerHTML = `
            <div class="stop-number">${stop.visit_order}</div>
            <div class="stop-address" title="${escapeHtml(stop.address)}">
                ${escapeHtml(stop.address)}
            </div>
        `;
        stopList.appendChild(el);
    });
}

function downloadGPX(jobId) {
    const url = `${API_BASE}/jobs/${jobId}/route.gpx`;
    const a = document.createElement('a');
    a.href = url;
    a.download = `routeweave_${jobId}.gpx`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
}

// ---------------------------------------------------------------------------
// Map Rendering
// ---------------------------------------------------------------------------

function initMap() {
    if (map) return;

    if (mapEmpty) mapEmpty.style.display = 'none';

    map = L.map('map', {
        zoomControl: true,
        attributionControl: true,
    }).setView([40.7128, -74.0060], 12);

    L.tileLayer(TILE_URL, {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        maxZoom: 19,
    }).addTo(map);
}

function clearRoutes() {
    if (routeLayers.naive) {
        map.removeLayer(routeLayers.naive);
        routeLayers.naive = null;
    }
    if (routeLayers.optimized) {
        map.removeLayer(routeLayers.optimized);
        routeLayers.optimized = null;
    }
    routeLayers.markers.forEach(m => map.removeLayer(m));
    routeLayers.markers = [];
    if (routeLayers.animationMarker) {
        map.removeLayer(routeLayers.animationMarker);
        routeLayers.animationMarker = null;
    }
}

function drawRoute(data) {
    initMap();
    clearRoutes();

    const depot = data.depot;
    const stops = data.stops;

    if (!depot || stops.length === 0) return;

    const allCoords = [];

    // Draw naive route (dashed, grey)
    const naiveCoords = [];
    if (depot.lat && depot.lng) {
        naiveCoords.push([depot.lat, depot.lng]);
    }
    // Naive route = original CSV order, matching the backend's naive distance
    const naiveStops = [...stops].sort((a, b) => (a.input_order ?? 0) - (b.input_order ?? 0));
    naiveStops.forEach(stop => {
        if (stop.lat && stop.lng) {
            naiveCoords.push([stop.lat, stop.lng]);
        }
    });

    if (naiveCoords.length > 1) {
        routeLayers.naive = L.polyline(naiveCoords, {
            color: ROUTE_NAIVE_COLOR,
            weight: 2.5,
            opacity: 0.85,
            dashArray: '6, 8',
            lineJoin: 'round',
        }).addTo(map);
    }

    // Draw optimized route (solid, green)
    const optimizedCoords = [];
    if (depot.lat && depot.lng) {
        optimizedCoords.push([depot.lat, depot.lng]);
        allCoords.push([depot.lat, depot.lng]);
    }

    const sortedStops = [...stops].sort((a, b) => a.visit_order - b.visit_order);
    sortedStops.forEach(stop => {
        if (stop.lat && stop.lng) {
            optimizedCoords.push([stop.lat, stop.lng]);
            allCoords.push([stop.lat, stop.lng]);
        }
    });

    if (optimizedCoords.length > 1) {
        routeLayers.optimized = L.polyline(optimizedCoords, {
            color: ROUTE_OPTIMIZED_COLOR,
            weight: 4.5,
            opacity: 0.9,
            lineJoin: 'round',
            lineCap: 'round',
        }).addTo(map);
    }

    // Add depot marker
    if (depot.lat && depot.lng) {
        const depotIcon = L.divIcon({
            className: 'custom-marker',
            html: `<div class="marker-pin depot"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg></div>`,
            iconSize: [34, 34],
            iconAnchor: [17, 17],
            popupAnchor: [0, -20],
        });

        const depotMarker = L.marker([depot.lat, depot.lng], { icon: depotIcon })
            .addTo(map)
            .bindPopup(`
                <div class="popup-title">Depot (Start)</div>
                <div class="popup-address">${escapeHtml(depot.address)}</div>
            `);
        routeLayers.markers.push(depotMarker);
    }

    // Add stop markers
    sortedStops.forEach(stop => {
        if (!stop.lat || !stop.lng) return;

        const icon = L.divIcon({
            className: 'custom-marker',
            html: `<div class="marker-pin">${stop.visit_order}</div>`,
            iconSize: [28, 28],
            iconAnchor: [14, 14],
            popupAnchor: [0, -16],
        });

        const marker = L.marker([stop.lat, stop.lng], { icon: icon })
            .addTo(map)
            .bindPopup(`
                <div class="popup-title">Stop #${stop.visit_order}</div>
                <div class="popup-address">${escapeHtml(stop.address)}</div>
            `);
        routeLayers.markers.push(marker);
    });

    // Fit bounds
    if (allCoords.length > 0) {
        const bounds = L.latLngBounds(allCoords);
        map.fitBounds(bounds, { padding: [50, 50], maxZoom: 15 });
    }

    // Animate route
    if (optimizedCoords.length > 1) {
        animateRoute(optimizedCoords);
    }
}

// ---------------------------------------------------------------------------
// Route Animation
// ---------------------------------------------------------------------------

function animateRoute(coords) {
    if (routeLayers.animationMarker) {
        map.removeLayer(routeLayers.animationMarker);
    }

    const vehicleIcon = L.divIcon({
        className: 'vehicle-marker',
        html: `<div class="vehicle-pin"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="1" y="3" width="15" height="13"/><polygon points="16 8 20 8 23 11 23 16 16 16"/><circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/></svg></div>`,
        iconSize: [28, 28],
        iconAnchor: [14, 14],
    });

    const marker = L.marker(coords[0], { icon: vehicleIcon, zIndexOffset: 1000 }).addTo(map);
    routeLayers.animationMarker = marker;

    let currentIndex = 0;
    let progress = 0;
    const speed = 0.015; // progress per frame

    function step() {
        if (currentIndex >= coords.length - 1) {
            // Done — fade out vehicle
            setTimeout(() => {
                if (routeLayers.animationMarker) {
                    map.removeLayer(routeLayers.animationMarker);
                    routeLayers.animationMarker = null;
                }
            }, 1000);
            return;
        }

        const start = coords[currentIndex];
        const end = coords[currentIndex + 1];

        progress += speed;
        if (progress >= 1) {
            progress = 0;
            currentIndex++;
        }

        const lat = start[0] + (end[0] - start[0]) * progress;
        const lng = start[1] + (end[1] - start[1]) * progress;
        marker.setLatLng([lat, lng]);

        requestAnimationFrame(step);
    }

    requestAnimationFrame(step);
}

// ---------------------------------------------------------------------------
// Job History
// ---------------------------------------------------------------------------

async function refreshHistory() {
    try {
        const response = await fetch(`${API_BASE}/jobs?limit=10`);
        if (!response.ok) return;

        const data = await response.json();

        if (data.jobs.length === 0) {
            historyList.innerHTML = '<div class="empty-note">No jobs yet. Upload a CSV to get started.</div>';
            return;
        }

        historyList.innerHTML = data.jobs.map(job => {
            const clickable = job.status === 'completed';
            return `
            <div class="history-item${clickable ? ' clickable' : ''}" data-job-id="${escapeHtml(job.id)}" data-status="${escapeHtml(job.status)}"
                 ${clickable ? 'role="button" tabindex="0" title="Show this route on the map"' : ''}>
                <div class="history-meta">
                    <div class="history-id">JOB-${escapeHtml(job.id.slice(0, 8).toUpperCase())}</div>
                    <div class="history-info">${job.stop_count} stops · ${formatDate(job.created_at)}</div>
                </div>
                <span class="history-status ${escapeHtml(job.status)}">${escapeHtml(job.status)}</span>
            </div>`;
        }).join('');

    } catch (err) {
        console.error('History refresh error:', err);
    }
}

async function handleHistoryClick(jobId, status) {
    if (status === 'completed') {
        showStatus('Loading saved route...', 'processing');
        await loadRoute(jobId);
        setPipeline('completed');
        showStatus('Route loaded from shipment log.', 'success');
    }
}

function onHistoryActivate(e) {
    const item = e.target.closest('.history-item.clickable');
    if (!item) return;
    if (e.type === 'keydown' && e.key !== 'Enter' && e.key !== ' ') return;
    e.preventDefault();
    handleHistoryClick(item.dataset.jobId, item.dataset.status);
}

historyList.addEventListener('click', onHistoryActivate);
historyList.addEventListener('keydown', onHistoryActivate);

refreshHistoryBtn.addEventListener('click', refreshHistory);

// ---------------------------------------------------------------------------
// UI Helpers
// ---------------------------------------------------------------------------

let statusHideTimer = null;

function showStatus(message, type = 'processing') {
    clearTimeout(statusHideTimer);
    statusBar.style.display = 'flex';
    statusBar.className = `status-bar ${type}`;
    statusText.textContent = message;

    if (type === 'processing') {
        statusSpinner.style.display = 'block';
    } else {
        statusSpinner.style.display = 'none';
    }

    if (type === 'success' || type === 'error') {
        statusHideTimer = setTimeout(() => {
            statusBar.style.display = 'none';
        }, 6000);
    }
}

function resetButton() {
    optimizeBtn.disabled = !selectedFile;
    btnText.textContent = 'Optimize Route';
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatFileSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(dateStr) {
    const d = new Date(dateStr);
    const now = new Date();
    const diff = now - d;

    if (diff < 60000) return 'Just now';
    if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;

    return d.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    });
}

// ---------------------------------------------------------------------------
// Initialize
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
    refreshHistory();
});
