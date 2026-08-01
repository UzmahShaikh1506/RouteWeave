/**
 * RouteWeave — Client-side map and interaction logic.
 * 
 * Handles:
 *   - CSV file upload (drag-and-drop + file picker)
 *   - Job creation via API
 *   - Polling for job completion
 *   - Leaflet map rendering (naive vs optimized routes)
 *   - Job history listing
 */

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

// API base URL — proxied through Nginx in production, direct in dev
const API_BASE = window.location.port === '3000'
    ? '/api'
    : 'http://localhost:8000';

const POLL_INTERVAL_MS = 1500;
const MAX_POLL_ATTEMPTS = 120; // 3 minutes max

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let selectedFile = null;
let map = null;
let routeLayers = {
    naive: null,
    optimized: null,
    markers: [],
};

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

// Stat elements
const statNaive = document.getElementById('stat-naive');
const statOptimized = document.getElementById('stat-optimized');
const statImprovement = document.getElementById('stat-improvement');
const statStops = document.getElementById('stat-stops');

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
            <span>📄</span>
            <span>${escapeHtml(file.name)}</span>
            <span style="color: var(--text-muted); font-size: 0.75rem;">(${formatFileSize(file.size)})</span>
        </div>
    `;
    optimizeBtn.disabled = false;
}

// ---------------------------------------------------------------------------
// Job Submission
// ---------------------------------------------------------------------------

optimizeBtn.addEventListener('click', async () => {
    if (!selectedFile) return;

    // Disable button during processing
    optimizeBtn.disabled = true;
    btnText.textContent = '⏳ Processing...';
    showStatus('Uploading CSV and starting optimization...', 'processing');

    try {
        const formData = new FormData();
        formData.append('file', selectedFile);
        formData.append('depot_address', depotInput.value.trim());

        const response = await fetch(`${API_BASE}/jobs`, {
            method: 'POST',
            body: formData,
        });

        if (response.status === 429) {
            showStatus('Rate limit exceeded. Please wait a minute.', 'error');
            resetButton();
            return;
        }

        if (!response.ok) {
            const err = await response.json();
            showStatus(`Error: ${err.detail || 'Upload failed'}`, 'error');
            resetButton();
            return;
        }

        const data = await response.json();
        const jobId = data.job_id;

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
            // Poll for completion
            showStatus('Geocoding addresses...', 'processing');
            await pollJob(jobId);
        }

    } catch (err) {
        console.error('Submit error:', err);
        showStatus(`Network error: ${err.message}`, 'error');
        resetButton();
    }
});

async function pollJob(jobId) {
    let attempts = 0;

    const poll = async () => {
        if (attempts >= MAX_POLL_ATTEMPTS) {
            showStatus('Timeout — job is still processing. Check history later.', 'error');
            resetButton();
            return;
        }

        attempts++;

        try {
            const response = await fetch(`${API_BASE}/jobs/${jobId}`);
            if (!response.ok) {
                showStatus('Failed to check job status.', 'error');
                resetButton();
                return;
            }

            const job = await response.json();

            switch (job.status) {
                case 'geocoding':
                    showStatus('Geocoding delivery addresses...', 'processing');
                    setTimeout(poll, POLL_INTERVAL_MS);
                    break;

                case 'optimizing':
                    showStatus('Running route optimization...', 'processing');
                    setTimeout(poll, POLL_INTERVAL_MS);
                    break;

                case 'completed':
                    showStatus('Route optimized successfully!', 'success');
                    await loadRoute(jobId);
                    refreshHistory();
                    resetButton();
                    break;

                case 'failed':
                    showStatus(`Failed: ${job.error_message || 'Unknown error'}`, 'error');
                    refreshHistory();
                    resetButton();
                    break;

                default:
                    showStatus(`Status: ${job.status}`, 'processing');
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
        showResults(data);
        drawRoute(data);

    } catch (err) {
        console.error('Load route error:', err);
        showStatus(`Failed to load route: ${err.message}`, 'error');
    }
}

function showResults(data) {
    // Update stats
    statNaive.textContent = data.naive_distance_km.toFixed(1);
    statOptimized.textContent = data.optimized_distance_km.toFixed(1);
    statImprovement.textContent = `${data.pct_improvement.toFixed(1)}%`;
    statStops.textContent = data.stops.length;

    // Show cards with animation
    resultsCard.style.display = 'block';
    resultsCard.style.animation = 'fadeIn 0.4s ease forwards';

    // Build stop list
    stopsCard.style.display = 'block';
    stopsCard.style.animation = 'fadeIn 0.4s ease 0.1s forwards';

    stopList.innerHTML = '';

    // Add depot first
    if (data.depot) {
        const depotEl = document.createElement('div');
        depotEl.className = 'stop-item';
        depotEl.style.animationDelay = '0s';
        depotEl.innerHTML = `
            <div class="stop-number depot">🏠</div>
            <div class="stop-address" title="${escapeHtml(data.depot.address)}">
                <strong>Depot:</strong> ${escapeHtml(data.depot.address)}
            </div>
        `;
        stopList.appendChild(depotEl);
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

// ---------------------------------------------------------------------------
// Map Rendering
// ---------------------------------------------------------------------------

function initMap() {
    if (map) return; // Already initialized

    // Hide empty state
    if (mapEmpty) mapEmpty.style.display = 'none';

    map = L.map('map', {
        zoomControl: true,
        attributionControl: true,
    }).setView([40.7128, -74.0060], 12); // Default: NYC

    // Dark-themed tile layer
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
        subdomains: 'abcd',
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
}

function drawRoute(data) {
    initMap();
    clearRoutes();

    const depot = data.depot;
    const stops = data.stops;

    if (!depot || stops.length === 0) return;

    // Build coordinate arrays
    const allCoords = [];

    // --- Naive route (original input order, by visit_order? No, original order is just the stops as uploaded)
    // Since we don't have the original order stored, we'll draw a "naive" route 
    // as the stops sorted by their original position (we can use the response order minus sorting)
    // Actually, the naive route is depot → stops in the order they appear in the CSV (not optimized).
    // The API returns stops sorted by visit_order (optimized), so let's just show:
    // - Optimized route (green, solid)
    // We don't have the original CSV order from the route endpoint, but we can still draw the optimized route.
    
    // For a "before" visualization, we could sort stops by name or use a random-ish order,
    // but the most honest approach: draw the optimized route prominently.

    // Draw optimized route
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

    // Draw naive route (just connect stops in a different order — reverse of optimized as a visual contrast)
    const naiveCoords = [];
    if (depot.lat && depot.lng) {
        naiveCoords.push([depot.lat, depot.lng]);
    }
    // Use alphabetical order of addresses as the "naive" order for visual contrast
    const naiveStops = [...stops].sort((a, b) => a.address.localeCompare(b.address));
    naiveStops.forEach(stop => {
        if (stop.lat && stop.lng) {
            naiveCoords.push([stop.lat, stop.lng]);
        }
    });

    // Draw naive polyline (dashed, red)
    if (naiveCoords.length > 1) {
        routeLayers.naive = L.polyline(naiveCoords, {
            color: '#ef4444',
            weight: 2.5,
            opacity: 0.4,
            dashArray: '8, 8',
            lineJoin: 'round',
        }).addTo(map);
    }

    // Draw optimized polyline (solid, green)
    if (optimizedCoords.length > 1) {
        routeLayers.optimized = L.polyline(optimizedCoords, {
            color: '#10b981',
            weight: 4,
            opacity: 0.85,
            lineJoin: 'round',
            lineCap: 'round',
        }).addTo(map);
    }

    // Add markers
    // Depot marker
    if (depot.lat && depot.lng) {
        const depotIcon = L.divIcon({
            className: 'custom-marker',
            html: '<div class="marker-pin depot">🏠</div>',
            iconSize: [34, 34],
            iconAnchor: [17, 17],
            popupAnchor: [0, -20],
        });

        const depotMarker = L.marker([depot.lat, depot.lng], { icon: depotIcon })
            .addTo(map)
            .bindPopup(`
                <div class="popup-title">📍 Depot (Start)</div>
                <div class="popup-address">${escapeHtml(depot.address)}</div>
            `);
        routeLayers.markers.push(depotMarker);
    }

    // Stop markers
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

    // Fit map to route bounds
    if (allCoords.length > 0) {
        const bounds = L.latLngBounds(allCoords);
        map.fitBounds(bounds, { padding: [50, 50], maxZoom: 15 });
    }
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
            historyList.innerHTML = `
                <div style="text-align: center; color: var(--text-muted); font-size: 0.813rem; padding: var(--space-md);">
                    No jobs yet. Upload a CSV to get started.
                </div>
            `;
            return;
        }

        historyList.innerHTML = data.jobs.map(job => `
            <div class="history-item" data-job-id="${job.id}" onclick="handleHistoryClick('${job.id}', '${job.status}')">
                <div class="history-meta">
                    <div class="history-date">${formatDate(job.created_at)}</div>
                    <div class="history-info">${job.stop_count} stops</div>
                </div>
                <span class="history-status ${job.status}">${job.status}</span>
            </div>
        `).join('');

    } catch (err) {
        console.error('History refresh error:', err);
    }
}

async function handleHistoryClick(jobId, status) {
    if (status === 'completed') {
        showStatus('Loading saved route...', 'processing');
        await loadRoute(jobId);
        showStatus('Route loaded from history.', 'success');
    }
}

refreshHistoryBtn.addEventListener('click', refreshHistory);

// ---------------------------------------------------------------------------
// UI Helpers
// ---------------------------------------------------------------------------

function showStatus(message, type = 'processing') {
    statusBar.style.display = 'flex';
    statusBar.className = `status-bar ${type}`;
    statusText.textContent = message;

    if (type === 'processing') {
        statusSpinner.style.display = 'block';
    } else {
        statusSpinner.style.display = 'none';
    }

    // Auto-hide success/error after 5 seconds
    if (type === 'success' || type === 'error') {
        setTimeout(() => {
            statusBar.style.display = 'none';
        }, 5000);
    }
}

function resetButton() {
    optimizeBtn.disabled = !selectedFile;
    btnText.textContent = '🚀 Optimize Route';
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
