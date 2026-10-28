/**
 * script.js - Frontend application logic for AO-02 Capacity Intelligence Dashboard.
 * 
 * Fetches data from FastAPI backend endpoints:
 * - /health
 * - /api/v1/overview
 * - /api/v1/resources
 * - /api/v1/resources/{type}/history
 * - /api/v1/resources/{type}/forecast
 * - /api/v1/alerts
 * - /api/v1/tenants
 * - /api/v1/audit
 */

// Application State
const state = {
  activeFacility: "DC-NORTH-PRIMARY",
  activeAsset: "",
  activeResource: "storage",
  activeHorizon: 30,
  activeRole: "admin",
  chartInstance: null,
  cachedOverview: null,
  isRefreshing: false,
  resourceTrends: {},
  trendFacility: ""
};

// Resource metadata for visual rendering
const RESOURCE_META = {
  power: { name: "Power", icon: "⚡", unit: "kW", capacity: 500 },
  cooling: { name: "Cooling", icon: "❄️", unit: "°C", capacity: 35 },
  storage: { name: "Storage", icon: "💾", unit: "TB", capacity: 2000 },
  network: { name: "Internet connection", icon: "🌐", unit: "Gbps", capacity: 100 },
  vm: { name: "Computer spaces", icon: "🖥️", unit: "VMs", capacity: 1200 }
};

// --------------------------------------------------------------------------
// FastAPI Backend Connection Config
// Automatically routes requests to FastAPI server at http://127.0.0.1:8000
// --------------------------------------------------------------------------
const API_BASE = (window.location.protocol === "file:" || (window.location.port !== "8000" && window.location.port !== ""))
  ? "http://127.0.0.1:8000"
  : "";

/**
 * Standardized API client for all backend communication.
 * Connects frontend to FastAPI with tenant authorization headers and error resiliency.
 */
async function apiFetch(endpoint, options = {}) {
  const url = `${API_BASE}${endpoint}`;
  const defaultHeaders = {
    "X-Tenant-ID": state.activeRole === "admin" ? "datacenter-global" : state.activeRole,
    "X-Role": state.activeRole === "admin" ? "admin" : "viewer"
  };
  options.headers = {
    ...defaultHeaders,
    ...(options.headers || {})
  };
  return fetch(url, options);
}


// --------------------------------------------------------------------------
// Initialization
// --------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", async () => {
  setupEventListeners();
  await loadFacilities();
  await loadFacilityAssets();
  loadAllDashboardData();
  // Set up periodic sync every 30 seconds
  setInterval(loadAllDashboardData, 30000);
});

function setupEventListeners() {
  // Facility dropdown selector
  const facilitySelect = document.getElementById("facilitySelect");
  if (facilitySelect) {
    facilitySelect.addEventListener("change", async (e) => {
      state.activeFacility = e.target.value;
      state.activeAsset = "";
      state.trendFacility = "";
      state.resourceTrends = {};
      await loadFacilityAssets();
      loadAllDashboardData();
    });
  }

  // Asset scope dropdown selector
  const assetSelect = document.getElementById("assetSelect");
  if (assetSelect) {
    assetSelect.addEventListener("change", (e) => {
      state.activeAsset = e.target.value;
      loadForecastData(state.activeResource, state.activeHorizon);
    });
  }

  // CSV Ingestion Modal
  const importCsvBtn = document.getElementById("importCsvBtn");
  const csvModal = document.getElementById("csvModal");
  const closeCsvModalBtn = document.getElementById("closeCsvModalBtn");
  const cancelCsvBtn = document.getElementById("cancelCsvBtn");
  const csvFileInput = document.getElementById("csvFileInput");
  const csvFileNameDisplay = document.getElementById("csvFileNameDisplay");
  const submitCsvBtn = document.getElementById("submitCsvBtn");

  if (importCsvBtn && csvModal) {
    importCsvBtn.addEventListener("click", () => {
      const csvFacilitySelect = document.getElementById("csvFacilitySelect");
      if (csvFacilitySelect) csvFacilitySelect.value = state.activeFacility;
      csvModal.showModal();
    });
  }
  if (closeCsvModalBtn && csvModal) {
    closeCsvModalBtn.addEventListener("click", () => csvModal.close());
  }
  if (cancelCsvBtn && csvModal) {
    cancelCsvBtn.addEventListener("click", () => csvModal.close());
  }
  if (csvFileInput && csvFileNameDisplay) {
    csvFileInput.addEventListener("change", (e) => {
      if (e.target.files && e.target.files[0]) {
        csvFileNameDisplay.textContent = e.target.files[0].name + ` (${(e.target.files[0].size / 1024).toFixed(1)} KB)`;
      } else {
        csvFileNameDisplay.textContent = "Choose a file or drop it here";
      }
    });
  }
  if (submitCsvBtn) {
    submitCsvBtn.addEventListener("click", handleCsvUpload);
  }

  // Refresh button
  const refreshBtn = document.getElementById("refreshBtn");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => {
      showToast("Checking for new readings...", "info");
      loadAllDashboardData();
    });
  }

  // Horizon selector
  const horizonSelect = document.getElementById("horizonSelect");
  if (horizonSelect) {
    horizonSelect.addEventListener("change", (e) => {
      state.activeHorizon = parseInt(e.target.value, 10);
      loadForecastData(state.activeResource, state.activeHorizon);
    });
  }

  // Resource tabs
  const tabButtons = document.querySelectorAll("#resourceTabs .tab-btn");
  tabButtons.forEach(btn => {
    btn.addEventListener("click", async () => {
      tabButtons.forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      state.activeResource = btn.dataset.resource;
      state.activeAsset = "";
      updateSelectedCardUI(state.activeResource);
      await loadFacilityAssets();
      loadForecastData(state.activeResource, state.activeHorizon);
    });
  });

  // Security role simulator (for Tenant Isolation testing)
  const tenantRoleSelector = document.getElementById("tenantRoleSelector");
  if (tenantRoleSelector) {
    tenantRoleSelector.addEventListener("change", (e) => {
      state.activeRole = e.target.value;
      loadTenantData();
    });
  }

  // What-If Simulator listeners
  const simGrowthSlider = document.getElementById("simGrowthMultiplier");
  const simGrowthDisplay = document.getElementById("simGrowthDisplay");
  if (simGrowthSlider && simGrowthDisplay) {
    simGrowthSlider.addEventListener("input", (e) => {
      const val = parseFloat(e.target.value);
      const pct = Math.round((val - 1.0) * 100);
      simGrowthDisplay.textContent = pct > 0
        ? `${pct}% faster`
        : pct < 0
          ? `${Math.abs(pct)}% slower`
          : "No change";
    });
  }

  const simResourceSelect = document.getElementById("simResourceSelect");
  if (simResourceSelect) {
    simResourceSelect.addEventListener("change", (e) => {
      const res = e.target.value;
      const meta = RESOURCE_META[res] || { unit: "units" };
      const wUnit = document.getElementById("simWorkloadUnit");
      const cUnit = document.getElementById("simCapacityUnit");
      if (wUnit) wUnit.textContent = meta.unit;
      if (cUnit) cUnit.textContent = meta.unit;
    });
  }

  const runSimBtn = document.getElementById("runSimulationBtn");
  if (runSimBtn) {
    runSimBtn.addEventListener("click", () => {
      runWhatIfSimulation();
    });
  }
}

// --------------------------------------------------------------------------
// Orchestrated Data Fetching
// --------------------------------------------------------------------------
async function loadAllDashboardData() {
  if (state.isRefreshing) return;
  state.isRefreshing = true;

  try {
    await Promise.all([
      loadHealth(),
      loadOverview(),
      loadForecastData(state.activeResource, state.activeHorizon),
      loadCapacityPlanning(),
      loadRecommendations(),
      runWhatIfSimulation(),
      loadTenantData(),
      loadAlerts(),
      loadAuditLog(),
      loadBacktestSummary()
    ]);
  } catch (err) {
    console.error("Dashboard synchronization error:", err);
    showToast("Could not connect. Check that the app is running and try again.", "error");
  } finally {
    state.isRefreshing = false;
  }
}

// --------------------------------------------------------------------------
// 1. Health Status & Observation Count
// --------------------------------------------------------------------------
async function loadHealth() {
  try {
    const res = await apiFetch("/health");
    if (!res.ok) return;
    const data = await res.json();
    const dataPointsEl = document.getElementById("kpiDataPointsCount");
    if (dataPointsEl) {
      dataPointsEl.textContent = Number(data.data_points_ingested).toLocaleString();
    }
  } catch (err) {
    console.warn("Health endpoint check failed:", err);
  }
}

// --------------------------------------------------------------------------
// 2. Overview & Resource Headroom Cards
// --------------------------------------------------------------------------
async function loadOverview() {
  const res = await apiFetch(`/api/v1/overview?facility_id=${encodeURIComponent(state.activeFacility)}`);
  if (!res.ok) throw new Error("Overview query failed");
  const data = await res.json();
  state.cachedOverview = data;

  // Global Health Pill
  const healthDot = document.getElementById("globalHealthDot");
  const healthText = document.getElementById("globalHealthText");
  if (healthDot && healthText) {
    const health = String(data.overall_health || "normal").toLowerCase();
    healthDot.className = `pulsing-dot ${health}`;
    healthText.textContent = {
      normal: "Everything looks good",
      warning: "Some things are getting full",
      critical: "Some things need attention"
    }[health] || "Checking system...";
  }

  // Earliest Crunch KPI
  const earliestName = document.getElementById("kpiEarliestCrunchName");
  const earliestDays = document.getElementById("kpiEarliestCrunchDays");
  if (earliestName && earliestDays) {
    if (data.earliest_capacity_crunch && data.days_to_earliest_crunch !== null) {
      const meta = RESOURCE_META[data.earliest_capacity_crunch] || { name: data.earliest_capacity_crunch };
      earliestName.textContent = meta.name;
      earliestDays.textContent = `About ${data.days_to_earliest_crunch.toFixed(0)} days until it gets too full`;
    } else {
      earliestName.textContent = "Nothing yet";
      earliestDays.textContent = "No resource is expected to fill up soon";
    }
  }

  // Active Alerts KPI
  const activeAlertsEl = document.getElementById("kpiActiveAlertsCount");
  const alertBreakdownEl = document.getElementById("kpiAlertBreakdown");
  if (activeAlertsEl && alertBreakdownEl) {
    const totalAlerts = data.critical_alerts_count + data.warning_alerts_count;
    activeAlertsEl.textContent = totalAlerts;
    alertBreakdownEl.textContent = `${data.critical_alerts_count} urgent · ${data.warning_alerts_count} getting full`;
  }

  // Render 5 Resource Cards
  renderResourceCards(data.resources);
  renderResourceTable(data.resources);
  loadResourceTrends();
}

async function loadResourceTrends() {
  const facilityId = state.activeFacility;
  if (state.trendFacility === facilityId) return;
  state.trendFacility = facilityId;
  state.resourceTrends = {};

  const trends = await Promise.all(Object.keys(RESOURCE_META).map(async resourceType => {
    const url = `/api/v1/resources/${resourceType}/history?facility_id=${encodeURIComponent(facilityId)}&limit=24`;
    try {
      const response = await apiFetch(url);
      if (!response.ok) throw new Error(`History request failed for ${resourceType}`);
      const history = await response.json();
      return [resourceType, Array.isArray(history) ? history : []];
    } catch (err) {
      console.warn(`Unable to load ${resourceType} trend:`, err);
      return [resourceType, null];
    }
  }));

  if (facilityId !== state.activeFacility) return;
  state.resourceTrends = Object.fromEntries(trends);
  if (trends.some(([, history]) => history === null)) state.trendFacility = "";
  if (state.cachedOverview) renderResourceCards(state.cachedOverview.resources);
}

function createSparkline(resourceType, statusClass) {
  const history = state.resourceTrends[resourceType];
  if (!Array.isArray(history) || history.length < 2) {
    return `<div class="resource-sparkline unavailable" aria-label="Recent trend is not available"><span>Recent trend unavailable</span></div>`;
  }

  const values = history.map(point => Number(point.value)).filter(Number.isFinite);
  if (values.length < 2) {
    return `<div class="resource-sparkline unavailable" aria-label="Not enough readings for a trend"><span>Not enough readings for a trend</span></div>`;
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const points = values.map((value, index) => {
    const x = (index / (values.length - 1)) * 160;
    const y = 31 - ((value - min) / range) * 25;
    return [x.toFixed(1), y.toFixed(1)];
  });
  const path = points.map(([x, y], index) => `${index === 0 ? "M" : "L"}${x},${y}`).join(" ");
  const [lastX, lastY] = points[points.length - 1];

  return `
    <div class="resource-sparkline" aria-label="Recent readings trend, ${values.length} points">
      <span class="sparkline-caption">Recent trend</span>
      <svg viewBox="0 0 160 38" preserveAspectRatio="none" role="img" aria-hidden="true">
        <path class="sparkline-track" d="M0,34 L160,34"></path>
        <path class="sparkline-line ${statusClass}" pathLength="1" d="${path}"></path>
        <circle class="sparkline-end ${statusClass}" cx="${lastX}" cy="${lastY}" r="2.5"></circle>
      </svg>
    </div>
  `;
}

function renderResourceCards(resources) {
  const grid = document.getElementById("resourcesGrid");
  if (!grid) return;
  grid.innerHTML = "";

  if (!Array.isArray(resources) || resources.length === 0) {
    grid.innerHTML = `<div class="empty-state">No resource telemetry is available for this facility yet.</div>`;
    return;
  }

  resources.forEach(r => {
    const meta = RESOURCE_META[r.resource_type] || { name: r.resource_type, icon: "📊" };
    const pct = Math.min(100, Math.max(0, r.utilization_pct));
    const statusClass = r.status.toLowerCase();
    const statusText = { normal: "Plenty of room", warning: "Getting full", critical: "Nearly full" }[statusClass] || r.status;

    const card = document.createElement("div");
    card.className = `resource-card ${r.resource_type === state.activeResource ? "selected" : ""}`;
    card.id = `card-${r.resource_type}`;
    card.setAttribute("role", "button");
    card.setAttribute("tabindex", "0");
    card.setAttribute("aria-label", `View forecast for ${meta.name}`);

    // Click handler to select and switch chart
    card.addEventListener("click", () => {
      selectResourceTab(r.resource_type);
    });

    const daysText = r.days_to_capacity !== null 
      ? `About ${r.days_to_capacity.toFixed(0)} days until full`
      : "Not expected to fill soon";

    card.innerHTML = `
      <div class="card-top">
        <div class="resource-title-row">
          <span class="resource-icon">${meta.icon}</span>
          <span class="resource-name">${meta.name}</span>
        </div>
        <span class="status-pill ${statusClass}">${statusText}</span>
      </div>

      <div class="card-metric-block">
        <span class="metric-current">${r.current_value.toLocaleString()} <small style="font-size:0.8rem;color:var(--text-subtle);">${r.unit}</small></span>
        <span class="metric-capacity">Full at ${r.capacity.toLocaleString()} ${r.unit}</span>
      </div>

      <div class="progress-container">
        <div class="progress-track">
          <div class="progress-fill ${statusClass}" style="width: ${pct}%"></div>
        </div>
        <div style="display:flex;justify-content:space-between;font-size:0.75rem;color:var(--text-subtle);">
          <span>How full</span>
          <span style="font-weight:600;color:var(--text-main);">${r.utilization_pct.toFixed(1)}%</span>
        </div>
      </div>

      ${createSparkline(r.resource_type, statusClass)}

      <div class="card-footer">
        <span class="days-badge ${statusClass}">${daysText}</span>
        <span style="color:var(--color-cyan);font-weight:600;">See chart →</span>
      </div>
    `;

    grid.appendChild(card);
  });
}

function renderResourceTable(resources) {
  const tbody = document.getElementById("resourceTableBody");
  if (!tbody) return;
  tbody.innerHTML = "";

  if (!Array.isArray(resources) || resources.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" class="resource-table-state">No resource telemetry is available for this facility yet.</td></tr>`;
    return;
  }

  resources.forEach(resource => {
    const meta = RESOURCE_META[resource.resource_type] || { name: resource.resource_type, icon: "📊" };
    const statusClass = String(resource.status || "normal").toLowerCase();
    const remaining = resource.capacity - resource.current_value;
    const headroomText = remaining >= 0
      ? `${remaining.toLocaleString()} ${resource.unit}`
      : `${Math.abs(remaining).toLocaleString()} ${resource.unit} over limit`;
    const exhaustionText = resource.days_to_capacity !== null && resource.days_to_capacity !== undefined
      ? `About ${resource.days_to_capacity.toFixed(0)} days`
      : "Not expected to fill soon";
    const row = document.createElement("tr");
    row.className = "resource-table-row";
    row.tabIndex = 0;
    row.setAttribute("aria-label", `View forecast for ${meta.name}`);
    row.addEventListener("click", () => selectResourceTab(resource.resource_type));
    row.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectResourceTab(resource.resource_type);
      }
    });

    const nameCell = document.createElement("td");
    nameCell.innerHTML = `<span class="resource-table-name"><span aria-hidden="true">${meta.icon}</span><strong></strong></span>`;
    nameCell.querySelector("strong").textContent = meta.name;

    const utilizationCell = document.createElement("td");
    const utilization = Math.min(100, Math.max(0, resource.utilization_pct));
    utilizationCell.innerHTML = `<div class="table-utilization"><span></span><div class="table-utilization-track"><i></i></div></div>`;
    utilizationCell.querySelector("span").textContent = `${resource.utilization_pct.toFixed(1)}%`;
    utilizationCell.querySelector("i").style.width = `${utilization}%`;

    const headroomCell = document.createElement("td");
    headroomCell.textContent = headroomText;

    const riskCell = document.createElement("td");
    const riskPill = document.createElement("span");
    riskPill.className = `status-pill ${statusClass}`;
    riskPill.textContent = {
      normal: "Plenty of room",
      warning: "Getting full",
      critical: "Nearly full"
    }[statusClass] || "Not known";
    riskCell.appendChild(riskPill);

    const exhaustionCell = document.createElement("td");
    exhaustionCell.textContent = exhaustionText;

    row.append(nameCell, utilizationCell, headroomCell, riskCell, exhaustionCell);
    tbody.appendChild(row);
  });
}

function selectResourceTab(resourceType) {
  state.activeResource = resourceType;
  state.activeAsset = "";
  const tabButtons = document.querySelectorAll("#resourceTabs .tab-btn");
  tabButtons.forEach(b => {
    b.classList.toggle("active", b.dataset.resource === resourceType);
  });
  updateSelectedCardUI(resourceType);
  loadFacilityAssets();
  loadForecastData(resourceType, state.activeHorizon);
}

function updateSelectedCardUI(resourceType) {
  document.querySelectorAll(".resource-card").forEach(c => {
    c.classList.toggle("selected", c.id === `card-${resourceType}`);
  });
}

// --------------------------------------------------------------------------
// 3. Historical Telemetry & Predictive Forecast Chart
// --------------------------------------------------------------------------
async function loadForecastData(resourceType, horizonDays) {
  setChartState("loading");
  try {
    let histUrl = `/api/v1/resources/${resourceType}/history?facility_id=${encodeURIComponent(state.activeFacility)}&limit=168`;
    let foreUrl = `/api/v1/resources/${resourceType}/forecast?facility_id=${encodeURIComponent(state.activeFacility)}&days=${horizonDays}`;
    if (state.activeAsset) {
      histUrl += `&asset_id=${encodeURIComponent(state.activeAsset)}`;
      foreUrl += `&asset_id=${encodeURIComponent(state.activeAsset)}`;
    }

    const [historyRes, forecastRes] = await Promise.all([
      apiFetch(histUrl),
      apiFetch(foreUrl)
    ]);

    if (!historyRes.ok || !forecastRes.ok) throw new Error("Forecast query failed");

    const historyData = await historyRes.json();
    const forecastData = await forecastRes.json();

    if (!Array.isArray(historyData) || historyData.length === 0 ||
        !forecastData || !Array.isArray(forecastData.forecast_points) ||
        forecastData.forecast_points.length === 0) {
      if (state.chartInstance) {
        state.chartInstance.destroy();
        state.chartInstance = null;
      }
      setChartState("empty");
      return;
    }

    renderChart(historyData, forecastData);
    renderExplainability(forecastData);
    setChartState("ready");
  } catch (err) {
    console.error("Failed to load forecast for", resourceType, err);
    if (state.chartInstance) {
      state.chartInstance.destroy();
      state.chartInstance = null;
    }
    setChartState("error");
  }
}

function setChartState(status) {
  const chartState = document.getElementById("chartState");
  if (!chartState) return;

  chartState.className = `chart-state ${status === "ready" ? "hidden" : ""}`;
  if (status === "loading") {
    chartState.innerHTML = `<span class="chart-state-mark" aria-hidden="true">⌁</span><strong>Loading the estimate</strong><span>Past readings and the best guess will appear here.</span>`;
  } else if (status === "empty") {
    chartState.innerHTML = `<span class="chart-state-mark" aria-hidden="true">⌁</span><strong>Not enough readings yet</strong><span>We need more past readings to make an estimate for this selection.</span>`;
  } else if (status === "error") {
    chartState.innerHTML = `<span class="chart-state-mark" aria-hidden="true">!</span><strong>Could not load the estimate</strong><span>Check that the app is running, then try again.</span>`;
  }
}

function renderChart(history, forecast) {
  const canvas = document.getElementById("forecastChart");
  if (!canvas) return;
  if (typeof Chart === "undefined") {
    throw new Error("Chart.js is unavailable");
  }
  const ctx = canvas.getContext("2d");

  // Format labels & values
  const histLabels = history.map(h => {
    const d = new Date(h.timestamp);
    return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:00`;
  });
  const histValues = history.map(h => h.value);

  const forecastLabels = forecast.forecast_points.map(f => {
    const d = new Date(f.timestamp);
    return `+${d.getMonth() + 1}/${d.getDate()}`;
  });

  const combinedLabels = [...histLabels, ...forecastLabels];
  const totalLength = combinedLabels.length;

  // Build continuous forecast dataset starting from last historical point
  const lastHistVal = histValues[histValues.length - 1];
  const predDataset = new Array(histValues.length - 1).fill(null);
  predDataset.push(lastHistVal);
  forecast.forecast_points.forEach(f => predDataset.push(f.predicted_value));

  // Upper & Lower 95% uncertainty band
  const upperBand = new Array(histValues.length - 1).fill(null);
  upperBand.push(lastHistVal);
  forecast.forecast_points.forEach(f => upperBand.push(f.upper_bound_95));

  const lowerBand = new Array(histValues.length - 1).fill(null);
  lowerBand.push(lastHistVal);
  forecast.forecast_points.forEach(f => lowerBand.push(f.lower_bound_95));

  // Rated Capacity Threshold line
  const capacityLine = new Array(totalLength).fill(forecast.capacity);

  // Pad history array for chart length
  const paddedHistory = [...histValues, ...new Array(forecastLabels.length).fill(null)];

  // Destroy previous chart instance if exists
  if (state.chartInstance) {
    state.chartInstance.destroy();
  }

  state.chartInstance = new Chart(ctx, {
    type: "line",
    data: {
      labels: combinedLabels,
      datasets: [
        {
          label: "Historical Utilization",
          data: paddedHistory,
          borderColor: "#0f766e",
          backgroundColor: "rgba(15, 118, 110, 0.08)",
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 4,
          tension: 0.15
        },
        {
          label: "Projected Trajectory",
          data: predDataset,
          borderColor: "#2563eb",
          borderWidth: 2.5,
          borderDash: [5, 4],
          pointRadius: 0,
          pointHoverRadius: 5,
          tension: 0.1
        },
        {
          label: "Upper 95% Confidence",
          data: upperBand,
          borderColor: "rgba(37, 99, 235, 0.35)",
          borderWidth: 1,
          pointRadius: 0,
          fill: "+1", // Fill to lower band
          backgroundColor: "rgba(37, 99, 235, 0.1)"
        },
        {
          label: "Lower 95% Confidence",
          data: lowerBand,
          borderColor: "rgba(37, 99, 235, 0.35)",
          borderWidth: 1,
          pointRadius: 0,
          fill: false
        },
        {
          label: "Rated Capacity Limit",
          data: capacityLine,
          borderColor: "#dc2626",
          borderWidth: 1.8,
          borderDash: [6, 6],
          pointRadius: 0,
          fill: false
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: {
        duration: 850,
        easing: "easeOutQuart"
      },
      transitions: {
        active: {
          animation: {
            duration: 220
          }
        }
      },
      interaction: {
        mode: "index",
        intersect: false
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "#ffffff",
          titleColor: "#142d4e",
          bodyColor: "#52677e",
          borderColor: "#e2e8f0",
          borderWidth: 1,
          padding: 10,
          displayColors: true
        }
      },
      scales: {
        x: {
          grid: { color: "rgba(20, 45, 78, 0.08)" },
          border: { color: "#e2e8f0" },
          ticks: {
            color: "#64788e",
            maxTicksLimit: 10,
            font: { family: "Inter, sans-serif", size: 11 }
          }
        },
        y: {
          grid: { color: "rgba(20, 45, 78, 0.08)" },
          border: { color: "#e2e8f0" },
          ticks: {
            color: "#64788e",
            font: { family: "Inter, sans-serif", size: 11 }
          }
        }
      }
    }
  });
}

function renderExplainability(forecast) {
  // Model status pill
  const statusPill = document.getElementById("modelStatusPill");
  if (statusPill) {
    statusPill.className = `status-pill ${forecast.status.toLowerCase()}`;
    statusPill.textContent = forecast.status.toUpperCase();
  }

  // Slope
  const slopeVal = document.getElementById("modelSlopeVal");
  if (slopeVal) {
    const meta = RESOURCE_META[forecast.resource_type] || { unit: "units" };
    slopeVal.textContent = `${forecast.trend_slope_per_day >= 0 ? "+" : ""}${forecast.trend_slope_per_day.toFixed(2)} ${meta.unit}/day`;
  }

  // Days to capacity
  const daysVal = document.getElementById("modelDaysVal");
  if (daysVal) {
    if (forecast.days_to_capacity !== null) {
      daysVal.textContent = `~${forecast.days_to_capacity.toFixed(0)} days`;
    } else {
      daysVal.textContent = "Indefinite (stable/declining)";
    }
  }

  // 95% Exhaustion Uncertainty Window & Date Range
  const uncWindowVal = document.getElementById("modelUncertaintyWindowVal");
  const uncDatesVal = document.getElementById("modelExhaustionRangeVal");
  if (uncWindowVal) {
    if (forecast.exhaustion_uncertainty_window_days !== null && forecast.exhaustion_uncertainty_window_days !== undefined) {
      uncWindowVal.textContent = `±${(forecast.exhaustion_uncertainty_window_days / 2.0).toFixed(1)} days (${forecast.exhaustion_uncertainty_window_days.toFixed(1)}d span)`;
    } else {
      uncWindowVal.textContent = "N/A (Stable)";
    }
  }
  if (uncDatesVal) {
    if (forecast.earliest_exhaustion_date && forecast.latest_exhaustion_date) {
      const d1 = new Date(forecast.earliest_exhaustion_date);
      const d2 = new Date(forecast.latest_exhaustion_date);
      uncDatesVal.textContent = `${d1.toLocaleDateString(undefined, {month:'short', day:'numeric'})} — ${d2.toLocaleDateString(undefined, {month:'short', day:'numeric', year:'numeric'})}`;
    } else {
      uncDatesVal.textContent = "Stable / Safe Runway";
    }
  }

  // Confidence
  const confVal = document.getElementById("modelConfidenceVal");
  if (confVal) {
    confVal.textContent = `${Math.round(forecast.confidence_score * 100)}%`;
  }

  // Reason text
  const reasonText = document.getElementById("modelReasonText");
  if (reasonText) {
    reasonText.textContent = forecast.reason;
  }

  // Assumptions
  const assumptionsList = document.getElementById("modelAssumptionsList");
  if (assumptionsList) {
    assumptionsList.innerHTML = "";
    (forecast.assumptions || []).forEach(a => {
      const li = document.createElement("li");
      li.textContent = a;
      assumptionsList.appendChild(li);
    });
  }

  // Regime change alert
  const regimeBox = document.getElementById("regimeAlertBox");
  const regimeText = document.getElementById("regimeAlertText");
  if (regimeBox && regimeText) {
    if (forecast.regime_change_detected && forecast.regime_change_detail) {
      regimeBox.classList.remove("hidden");
      regimeText.textContent = forecast.regime_change_detail;
    } else {
      regimeBox.classList.add("hidden");
    }
  }
}

// --------------------------------------------------------------------------
// 4. Tenant Resource Attribution & Tenant Isolation
// --------------------------------------------------------------------------
async function loadTenantData() {
  const grid = document.getElementById("tenantsGrid");
  if (!grid) return;

  const headers = {};
  if (state.activeRole === "admin") {
    headers["X-Role"] = "admin";
  } else {
    // Specific tenant requested
    headers["X-Tenant-ID"] = state.activeRole;
  }

  try {
    const res = await apiFetch("/api/v1/tenants", { headers });
    if (!res.ok) {
      grid.innerHTML = `
        <div class="card" style="grid-column: 1 / -1; border-left: 4px solid var(--color-critical);">
          <strong>Access Restricted:</strong> Tenant isolation prevented retrieval of requested records.
        </div>
      `;
      return;
    }

    const tenants = await res.json();
    grid.innerHTML = "";

    tenants.forEach(t => {
      const card = document.createElement("div");
      card.className = "tenant-card";

      let resourceRows = "";
      (t.allocated_resources || []).forEach(r => {
        const pct = Math.min(100, Math.max(0, r.quota_utilization_pct));
        const meta = RESOURCE_META[r.resource_type] || { name: r.resource_type };
        resourceRows += `
          <div class="tenant-resource-item">
            <div class="tenant-res-info">
              <span>${meta.name}:</span>
              <strong>${r.value.toFixed(1)} / ${r.quota.toFixed(1)} ${r.unit} (${pct.toFixed(0)}%)</strong>
            </div>
            <div class="tenant-progress-track">
              <div class="tenant-progress-bar" style="width: ${pct}%"></div>
            </div>
          </div>
        `;
      });

      card.innerHTML = `
        <div class="tenant-header">
          <span class="tenant-name">${t.tenant_id}</span>
          <span class="tenant-workload">${t.workload_type}</span>
        </div>
        <div style="display:flex;flex-direction:column;gap:12px;">
          ${resourceRows}
        </div>
      `;

      grid.appendChild(card);
    });
  } catch (err) {
    console.error("Failed to load tenant attribution:", err);
  }
}

// --------------------------------------------------------------------------
// 5. Active Alerts
// --------------------------------------------------------------------------
async function loadAlerts() {
  const container = document.getElementById("alertsList");
  if (!container) return;

  try {
    const res = await apiFetch(`/api/v1/alerts?facility_id=${encodeURIComponent(state.activeFacility)}`);
    if (!res.ok) return;
    const alerts = await res.json();

    if (alerts.length === 0) {
      container.innerHTML = `<div class="empty-state">Nothing needs attention right now. All watched resources have room left.</div>`;
      return;
    }

    container.innerHTML = "";
    alerts.forEach(a => {
      const item = document.createElement("div");
      item.className = `alert-item ${a.severity.toLowerCase()}`;
      item.innerHTML = `
        <div class="alert-item-header">
          <span class="alert-title">${a.title}</span>
          <span class="status-pill ${a.severity.toLowerCase()}">${a.severity.toUpperCase()}</span>
        </div>
        <div class="alert-msg">${a.message}</div>
        <div class="alert-action"><strong>Recommendation:</strong> ${a.suggested_action}</div>
      `;
      container.appendChild(item);
    });
  } catch (err) {
    console.error("Failed to load alerts:", err);
  }
}

// --------------------------------------------------------------------------
// 6. Audit Trail
// --------------------------------------------------------------------------
async function loadAuditLog() {
  const container = document.getElementById("auditList");
  if (!container) return;

  try {
    const res = await apiFetch(`/api/v1/audit?limit=25&facility_id=${encodeURIComponent(state.activeFacility)}`);
    if (!res.ok) return;
    const events = await res.json();

    if (events.length === 0) {
      container.innerHTML = `<div class="empty-state">No audit events recorded yet for facility ${state.activeFacility}.</div>`;
      return;
    }

    container.innerHTML = "";
    events.forEach(e => {
      const item = document.createElement("div");
      item.className = "audit-item";
      const d = new Date(e.timestamp);
      const timeStr = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`;
      item.innerHTML = `
        <div>
          <span class="audit-type">${e.event_type}</span>
          <div style="color:var(--text-muted);font-size:0.75rem;margin-top:2px;">${e.details}</div>
        </div>
        <span class="audit-time">${timeStr}</span>
      `;
      container.appendChild(item);
    });
  } catch (err) {
    console.error("Failed to load audit trail:", err);
  }
}

// --------------------------------------------------------------------------
// UI Helpers
// --------------------------------------------------------------------------
function showToast(message, type = "info") {
  const container = document.getElementById("toastContainer");
  if (!container) return;

  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.innerHTML = `<span>${type === "error" ? "❌" : "ℹ️"}</span><span>${message}</span>`;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = "0";
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}


// --------------------------------------------------------------------------
// 7. Capacity Planning & Lead Time Engine
// --------------------------------------------------------------------------
async function loadCapacityPlanning() {
  const grid = document.getElementById("planningGrid");
  const pill = document.getElementById("planningSummaryText");
  if (!grid) return;

  try {
    const res = await apiFetch(`/api/v1/capacity-planning?facility_id=${encodeURIComponent(state.activeFacility)}`);
    if (!res.ok) return;
    const data = await res.json();

    if (pill) {
      pill.textContent = `${data.overdue_count} Overdue · ${data.imminent_count} Imminent · ${data.total_resources - data.overdue_count - data.imminent_count} On Track`;
    }

    grid.innerHTML = "";
    data.items.forEach(item => {
      const meta = RESOURCE_META[item.resource_type] || { name: item.resource_type, icon: "📦" };
      const card = document.createElement("div");
      card.className = `planning-card ${item.planning_status}`;

      const leadDays = `${item.procurement_lead_time_days.toFixed(0)}d lead time`;
      const deadlineStr = item.days_until_order_deadline !== null
        ? (item.overdue_by_days !== null ? `Overdue by ~${item.overdue_by_days.toFixed(0)}d` : `Order in ~${item.days_until_order_deadline.toFixed(0)}d`)
        : "Runway Indefinite";

      card.innerHTML = `
        <div class="planning-header">
          <span class="planning-res-title">${meta.icon} ${meta.name}</span>
          <span class="planning-badge ${item.planning_status}">${item.planning_status.replace('_', ' ')}</span>
        </div>

        <div class="planning-headline">${item.planning_headline}</div>

        <div class="planning-metric-row">
          <span>Exhaustion: <strong>${item.days_to_capacity !== null ? `~${item.days_to_capacity.toFixed(0)}d` : 'None projected'}</strong></span>
          <span>Lead Time: <strong>${leadDays}</strong></span>
        </div>

        <div class="planning-metric-row">
          <span>Requisition Deadline:</span>
          <span style="font-weight:700;color:${item.overdue_by_days ? 'var(--color-critical)' : 'var(--color-cyan)'};">${deadlineStr}</span>
        </div>

        <p class="planning-desc">${item.explanation}</p>
      `;
      grid.appendChild(card);
    });
  } catch (err) {
    console.error("Failed to load capacity planning:", err);
  }
}

// --------------------------------------------------------------------------
// 8. What-If Scenario Simulator
// --------------------------------------------------------------------------
async function runWhatIfSimulation() {
  const resourceSelect = document.getElementById("simResourceSelect");
  const growthSlider = document.getElementById("simGrowthMultiplier");
  const workloadInput = document.getElementById("simWorkloadDelta");
  const capacityInput = document.getElementById("simCapacityAdjustment");

  if (!resourceSelect) return;

  const resType = resourceSelect.value;
  const growthMult = parseFloat(growthSlider ? growthSlider.value : "1.5");
  const workloadDelta = parseFloat(workloadInput ? workloadInput.value : "0") || 0.0;
  const capacityAdj = parseFloat(capacityInput ? capacityInput.value : "0") || 0.0;

  try {
    const res = await apiFetch(`/api/v1/simulations/forecast?facility_id=${encodeURIComponent(state.activeFacility)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        resource_type: resType,
        demand_growth_multiplier: growthMult,
        workload_delta: workloadDelta,
        capacity_adjustment: capacityAdj,
        forecast_days: 30
      })
    });

    if (!res.ok) throw new Error("Simulation calculation failed");
    const data = await res.json();

    // Update simulation comparison cards
    const baseDaysEl = document.getElementById("simBaseDays");
    const baseSlopeEl = document.getElementById("simBaseSlope");
    const newDaysEl = document.getElementById("simNewDays");
    const newSlopeEl = document.getElementById("simNewSlope");
    const headlineEl = document.getElementById("simImpactHeadline");
    const descEl = document.getElementById("simImpactDesc");

    if (baseDaysEl) {
      baseDaysEl.textContent = data.baseline_days_to_capacity !== null 
        ? `About ${data.baseline_days_to_capacity.toFixed(0)} days`
        : "Not expected to fill soon";
    }
    if (baseSlopeEl) {
      baseSlopeEl.textContent = `${data.baseline_utilization_pct.toFixed(1)}% full right now`;
    }

    if (newDaysEl) {
      newDaysEl.textContent = data.simulated_days_to_capacity !== null 
        ? `About ${data.simulated_days_to_capacity.toFixed(0)} days`
        : "Not expected to fill soon";
    }
    if (newSlopeEl) {
      newSlopeEl.textContent = `${data.simulated_utilization_pct.toFixed(1)}% full in this test`;
    }

    if (headlineEl) {
      headlineEl.textContent = "What changes?";
    }
    if (descEl) {
      const currentEstimate = data.baseline_days_to_capacity !== null
        ? `about ${data.baseline_days_to_capacity.toFixed(0)} days`
        : "not expected to fill soon";
      const testEstimate = data.simulated_days_to_capacity !== null
        ? `about ${data.simulated_days_to_capacity.toFixed(0)} days`
        : "not expected to fill soon";
      descEl.textContent = `With your changes, the estimate moves from ${currentEstimate} to ${testEstimate}. This is only a practice test.`;
    }
  } catch (err) {
    console.error("Simulation run failed:", err);
  }
}

// --------------------------------------------------------------------------
// 9. Actionable Advisory Recommendations
// --------------------------------------------------------------------------
async function loadRecommendations() {
  const container = document.getElementById("recommendationsList");
  if (!container) return;

  try {
    const res = await apiFetch(`/api/v1/recommendations?facility_id=${encodeURIComponent(state.activeFacility)}`);
    if (!res.ok) return;
    const recs = await res.json();

    if (recs.length === 0) {
      container.innerHTML = `<div class="empty-state">No immediate operational recommendations for facility ${state.activeFacility}. All systems healthy.</div>`;
      return;
    }

    container.innerHTML = "";
    recs.forEach(r => {
      const card = document.createElement("div");
      const pClass = r.priority.toLowerCase();
      card.className = `rec-card ${pClass}`;

      let checklistHtml = "";
      (r.action_items || []).forEach(item => {
        checklistHtml += `<li>${item}</li>`;
      });

      card.innerHTML = `
        <div class="rec-top-row">
          <div class="rec-title-wrap">
            <span class="priority-badge ${pClass}">${r.priority.replace('_', ' ')}</span>
            <span class="rec-title">${r.title}</span>
          </div>
          <span style="font-size:0.75rem;color:var(--text-subtle);text-transform:uppercase;">RESOURCE: ${r.resource_type}</span>
        </div>

        <div class="rec-summary">${r.summary}</div>

        <div class="rec-trigger"><strong>Trigger Criteria:</strong> ${r.trigger_reason}</div>

        <ul class="rec-checklist">
          ${checklistHtml}
        </ul>

        <div class="rec-impact"><strong>Expected Outcome:</strong> ${r.estimated_impact}</div>
      `;
      container.appendChild(card);
    });
  } catch (err) {
    console.error("Failed to load recommendations:", err);
  }
}

// --------------------------------------------------------------------------
// 10. Multi-Facility Management & Dynamic Facility List
// --------------------------------------------------------------------------
async function loadFacilities() {
  try {
    const res = await apiFetch("/api/v1/facilities");
    if (!res.ok) return;
    const facilities = await res.json();

    const facSelect = document.getElementById("facilitySelect");
    const csvFacSelect = document.getElementById("csvFacilitySelect");

    if (facSelect) {
      facSelect.innerHTML = "";
      facilities.forEach(f => {
        const opt = document.createElement("option");
        opt.value = f.facility_id;
        opt.textContent = `${f.facility_id} (${f.location || f.facility_name})`;
        if (f.facility_id === state.activeFacility) opt.selected = true;
        facSelect.appendChild(opt);
      });
    }

    if (csvFacSelect) {
      csvFacSelect.innerHTML = "";
      facilities.forEach(f => {
        const opt = document.createElement("option");
        opt.value = f.facility_id;
        opt.textContent = `${f.facility_id} (${f.location || f.facility_name})`;
        if (f.facility_id === state.activeFacility) opt.selected = true;
        csvFacSelect.appendChild(opt);
      });
    }
  } catch (err) {
    console.warn("Failed to load facilities:", err);
  }
}

// --------------------------------------------------------------------------
// 11. Asset-Level Tracking & Drilldown Scope
// --------------------------------------------------------------------------
async function loadFacilityAssets() {
  const assetSelect = document.getElementById("assetSelect");
  if (!assetSelect) return;

  try {
    const res = await apiFetch(`/api/v1/facilities/${encodeURIComponent(state.activeFacility)}/assets?resource_type=${encodeURIComponent(state.activeResource)}`);
    if (!res.ok) {
      assetSelect.innerHTML = `<option value="" selected>Whole facility</option>`;
      return;
    }
    const assets = await res.json();

    assetSelect.innerHTML = `<option value="" ${state.activeAsset === "" ? "selected" : ""}>Whole facility</option>`;
    assets.forEach(a => {
      const opt = document.createElement("option");
      opt.value = a.asset_id;
      opt.textContent = `${a.asset_name} [${a.asset_id}] (${a.rated_capacity} ${a.unit})`;
      if (a.asset_id === state.activeAsset) opt.selected = true;
      assetSelect.appendChild(opt);
    });
  } catch (err) {
    console.warn("Failed to load assets:", err);
    assetSelect.innerHTML = `<option value="" selected>Whole facility</option>`;
  }
}

// --------------------------------------------------------------------------
// 12. Empirical Model Validation & Out-of-Sample Backtesting
// --------------------------------------------------------------------------
async function loadBacktestSummary() {
  try {
    const res = await apiFetch("/api/v1/evaluation/summary");
    if (!res.ok) return;
    const summary = await res.json();

    // Populate top KPI cards
    const totalEl = document.getElementById("evalTotalSamples");
    const splitEl = document.getElementById("evalSplitDesc");
    const covEl = document.getElementById("evalCoverageVal");
    const maeEl = document.getElementById("evalOverallMae");
    const naiveEl = document.getElementById("evalNaiveImprovement");

    if (totalEl) totalEl.textContent = (summary.training_samples + summary.test_samples).toLocaleString();
    if (splitEl) splitEl.textContent = `${summary.training_samples.toLocaleString()} Train · ${summary.test_samples.toLocaleString()} Test (70/30)`;
    if (covEl) covEl.textContent = `${summary.overall_empirical_coverage_95.toFixed(1)}%`;
    if (maeEl) maeEl.textContent = `${summary.overall_mae.toFixed(1)}`;
    if (naiveEl) naiveEl.textContent = `+${summary.average_reduction_vs_naive_pct.toFixed(1)}%`;

    // Populate resource breakdown table
    const tbody = document.getElementById("evalTableBody");
    if (!tbody || !summary.per_resource) return;

    tbody.innerHTML = "";
    Object.entries(summary.per_resource).forEach(([resKey, m]) => {
      const meta = RESOURCE_META[resKey] || { name: resKey, icon: "📊", unit: "" };
      const tr = document.createElement("tr");

      const gainClass = m.improvement_over_naive_pct >= 0 ? "eval-gain-positive" : "";
      const gainText = `${m.improvement_over_naive_pct >= 0 ? "+" : ""}${m.improvement_over_naive_pct.toFixed(1)}%`;

      tr.innerHTML = `
        <td><strong>${meta.icon} ${meta.name}</strong></td>
        <td class="eval-metric-mono">${m.train_count} / ${m.test_count}</td>
        <td class="eval-metric-mono">${m.mae.toFixed(2)} ${meta.unit}</td>
        <td class="eval-metric-mono">${m.rmse.toFixed(2)} ${meta.unit}</td>
        <td><span class="status-pill ${m.empirical_coverage_95 >= 95 ? 'normal' : 'warning'}">${m.empirical_coverage_95.toFixed(1)}%</span></td>
        <td class="eval-metric-mono">${m.naive_persistence_mae.toFixed(2)} ${meta.unit}</td>
        <td class="${gainClass}">${gainText}</td>
      `;
      tbody.appendChild(tr);
    });
  } catch (err) {
    console.error("Failed to load backtest evaluation:", err);
  }
}

// --------------------------------------------------------------------------
// 13. CSV Ingestion & Line-Numbered Validation
// --------------------------------------------------------------------------
async function handleCsvUpload() {
  const fileInput = document.getElementById("csvFileInput");
  const facSelect = document.getElementById("csvFacilitySelect");
  const resultsContainer = document.getElementById("csvResultsContainer");
  const statsPills = document.getElementById("csvStatsPills");
  const errorsBox = document.getElementById("csvErrorsBox");
  const errorsList = document.getElementById("csvErrorsList");
  const submitBtn = document.getElementById("submitCsvBtn");

  if (!fileInput || !fileInput.files || fileInput.files.length === 0) {
    showToast("Please select a telemetry CSV file first.", "error");
    return;
  }

  const file = fileInput.files[0];
  const targetFacility = facSelect ? facSelect.value : state.activeFacility;

  const formData = new FormData();
  formData.append("file", file);
  formData.append("facility_id", targetFacility);

  submitBtn.disabled = true;
  submitBtn.innerHTML = `<span class="btn-icon">⏳</span> Uploading...`;

  try {
    const res = await apiFetch("/api/v1/telemetry/import-csv", {
      method: "POST",
      body: formData
    });

    const data = await res.json();
    if (!res.ok) {
      showToast(data.detail || "CSV Ingestion failed", "error");
      return;
    }

    // Display summary results
    if (resultsContainer) resultsContainer.classList.remove("hidden");
    if (statsPills) {
      statsPills.innerHTML = `
        <span class="stat-pill">Rows checked: ${data.total_rows_processed}</span>
        <span class="stat-pill success">Readings added: ${data.valid_rows_imported}</span>
        <span class="stat-pill warning">Already had: ${data.duplicate_rows_skipped}</span>
        <span class="stat-pill ${data.error_count > 0 ? 'error' : 'success'}">Problems: ${data.error_count}</span>
      `;
    }

    // Display line-numbered errors if any
    if (errorsBox && errorsList) {
      if (data.validation_errors && data.validation_errors.length > 0) {
        errorsBox.classList.remove("hidden");
        errorsList.innerHTML = "";
        data.validation_errors.slice(0, 15).forEach(err => {
          const li = document.createElement("li");
          li.textContent = `Line ${err.row_number}: Field '${err.field}' — ${err.reason} (rejected value: "${err.rejected_value}")`;
          errorsList.appendChild(li);
        });
        if (data.validation_errors.length > 15) {
          const li = document.createElement("li");
          li.textContent = `...and ${data.validation_errors.length - 15} additional line errors.`;
          errorsList.appendChild(li);
        }
      } else {
        errorsBox.classList.add("hidden");
      }
    }

    showToast(`Added ${data.valid_rows_imported} readings.`, "success");
    // Reload dashboard components
    await loadFacilities();
    await loadFacilityAssets();
    loadAllDashboardData();
  } catch (err) {
    console.error("CSV upload error:", err);
    showToast("Network error uploading CSV.", "error");
  } finally {
    submitBtn.disabled = false;
    submitBtn.innerHTML = `<span class="btn-icon">⚡</span> Check and upload`;
  }
}
