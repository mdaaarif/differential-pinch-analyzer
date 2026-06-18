/* ==========================================================================
   DIFFERENTIAL PINCH ANALYSER - FRONTEND ENGINE
   ========================================================================== */

const state = {
  deltaTmin: 20,
  streams: [],
  selectedExample: 'paper1',
  
  // Results populated by Flask D-PTA API
  targets: { QHmin: 0, QCmin: 0, pinchShifted: 0, pinchHot: 0, pinchCold: 0, nMin: 0 },
  curves: { hot_H: [], hot_T: [], cold_H_shifted: [], cold_T: [], gcc_Q: [], gcc_T: [], h_max: 0, t_min: 0, t_max: 0 },
  profiles: {},
  comparison: { constQH: 0, constQC: 0, constPinchShifted: 0, errQH: 0, errQC: 0 }
};

const FLUID_OPTIONS = [
  { value: 'mixedrefrigerant', label: 'Mixed Refrigerant (Precool)' },
  { value: 'air', label: 'Air (N2/O2/Ar)' },
  { value: 'water', label: 'Water (H2O)' },
  { value: 'propane', label: 'Propane (C3H8)' },
  { value: 'methanol', label: 'Methanol (CH3OH)' },
  { value: 'carbondioxide', label: 'Carbon Dioxide (CO2)' },
  { value: 'nitrogen', label: 'Nitrogen (N2)' },
  { value: 'oxygen', label: 'Oxygen (O2)' }
];

// Preloaded Case Studies from PRES '23 paper
const EXAMPLES = {
  paper1: {
    deltaTmin: 20,
    streams: [
      { id: 'H1', type: 'hot', fluid: 'mixedrefrigerant', flow: 22.718, pressure: 7.09, Tin: 120.0, Tout: 65.0 },
      { id: 'H2', type: 'hot', fluid: 'mixedrefrigerant', flow: 136.209, pressure: 7.09, Tin: 80.0, Tout: 50.0 },
      { id: 'H3', type: 'hot', fluid: 'mixedrefrigerant', flow: 79.146, pressure: 7.09, Tin: 135.0, Tout: 110.0 },
      { id: 'H4', type: 'hot', fluid: 'mixedrefrigerant', flow: 7.988, pressure: 7.09, Tin: 220.0, Tout: 95.0 },
      { id: 'H5', type: 'hot', fluid: 'mixedrefrigerant', flow: 64.967, pressure: 7.09, Tin: 135.0, Tout: 105.0 },
      { id: 'H6', type: 'cold', fluid: 'mixedrefrigerant', flow: 68.118, pressure: 7.09, Tin: 65.0, Tout: 90.0 },
      { id: 'C1', type: 'cold', fluid: 'mixedrefrigerant', flow: 35.057, pressure: 7.09, Tin: 75.0, Tout: 200.0 },
      { id: 'C2', type: 'cold', fluid: 'mixedrefrigerant', flow: 30.190, pressure: 7.09, Tin: 30.0, Tout: 210.0 },
      { id: 'C3', type: 'cold', fluid: 'mixedrefrigerant', flow: 22.754, pressure: 7.09, Tin: 60.0, Tout: 140.0 }
    ]
  },
  paper2: {
    deltaTmin: 5,
    streams: [
      { id: 'S1', type: 'hot', fluid: 'air', flow: 1.000, pressure: 60.0, Tin: 36.85, Tout: -158.41 },
      { id: 'S2', type: 'cold', fluid: 'air', flow: 0.308, pressure: 1.0, Tin: -193.84, Tout: 21.85 },
      { id: 'S3', type: 'cold', fluid: 'propane', flow: 0.755, pressure: 1.0, Tin: -180.15, Tout: -48.15 },
      { id: 'S4', type: 'cold', fluid: 'methanol', flow: 0.386, pressure: 1.0, Tin: -48.15, Tout: 21.85 }
    ]
  }
};

// --- Page Initialization ---
document.addEventListener('DOMContentLoaded', () => {
  loadCaseStudy('paper1');
  setupEventListeners();
});

function setupEventListeners() {
  // Example Selection
  document.getElementById('example-select').addEventListener('change', (e) => {
    if (e.target.value !== 'custom') {
      loadCaseStudy(e.target.value);
    }
  });

  // Delta Tmin Slider
  const slider = document.getElementById('tmin-slider');
  const display = document.getElementById('tmin-value');
  slider.addEventListener('input', (e) => {
    state.deltaTmin = parseFloat(e.target.value);
    display.textContent = `${state.deltaTmin} °C`;
    solveAndRender();
  });

  // Reset Button
  document.getElementById('reset-btn').addEventListener('click', () => {
    const selector = document.getElementById('example-select');
    loadCaseStudy(selector.value);
  });

  // Add Stream Button
  document.getElementById('add-stream-btn').addEventListener('click', () => {
    const newId = `S${state.streams.length + 1}`;
    state.streams.push({
      id: newId,
      type: 'hot',
      fluid: 'water',
      flow: 10.0,
      pressure: 1.0,
      Tin: 100.0,
      Tout: 40.0
    });
    document.getElementById('example-select').value = 'custom';
    renderStreamTable();
    solveAndRender();
  });

  // Tab Switching
  const tabButtons = document.querySelectorAll('.tab-nav .tab-btn');
  tabButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      tabButtons.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      
      const tabId = btn.getAttribute('data-tab');
      document.querySelectorAll('.tab-pane').forEach(pane => {
        pane.classList.remove('active');
      });
      document.getElementById(tabId).classList.add('active');
      
      // Re-render plots to fit container dimensions
      renderCharts();
    });
  });
}

// --- Load Preloaded Case ---
function loadCaseStudy(key) {
  const ex = EXAMPLES[key];
  if (!ex) return;
  state.deltaTmin = ex.deltaTmin;
  state.streams = JSON.parse(JSON.stringify(ex.streams));
  
  // Set slider value
  document.getElementById('tmin-slider').value = ex.deltaTmin;
  document.getElementById('tmin-value').textContent = `${ex.deltaTmin} °C`;
  
  renderStreamTable();
  solveAndRender();
}

// --- Render Stream List Table ---
function renderStreamTable() {
  const tbody = document.getElementById('streams-body');
  tbody.innerHTML = '';

  state.streams.forEach((s, idx) => {
    const tr = document.createElement('tr');
    
    // Build select dropdown for fluids
    let fluidOptionsHtml = '';
    FLUID_OPTIONS.forEach(opt => {
      fluidOptionsHtml += `<option value="${opt.value}" ${s.fluid === opt.value ? 'selected' : ''}>${opt.label}</option>`;
    });

    tr.innerHTML = `
      <td><span class="stream-id-label">${s.id}</span></td>
      <td>
        <select class="input-cell type-select ${s.type}" data-idx="${idx}" data-field="type">
          <option value="hot" ${s.type === 'hot' ? 'selected' : ''}>Hot</option>
          <option value="cold" ${s.type === 'cold' ? 'selected' : ''}>Cold</option>
        </select>
      </td>
      <td>
        <select class="input-cell" data-idx="${idx}" data-field="fluid">
          ${fluidOptionsHtml}
        </select>
      </td>
      <td><input type="number" class="input-cell" data-idx="${idx}" data-field="flow" value="${s.flow}" step="0.1" min="0.01"></td>
      <td><input type="number" class="input-cell" data-idx="${idx}" data-field="pressure" value="${s.pressure}" step="0.1" min="0.01"></td>
      <td><input type="number" class="input-cell" data-idx="${idx}" data-field="Tin" value="${s.Tin}" step="1"></td>
      <td><input type="number" class="input-cell" data-idx="${idx}" data-field="Tout" value="${s.Tout}" step="1"></td>
      <td>
        <button class="btn btn-xs btn-danger delete-stream-btn" data-idx="${idx}">
          <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </td>
    `;

    tbody.appendChild(tr);
  });

  // Bind change listeners to input elements
  tbody.querySelectorAll('.input-cell').forEach(input => {
    input.addEventListener('change', (e) => {
      const idx = parseInt(e.target.getAttribute('data-idx'));
      const field = e.target.getAttribute('data-field');
      let val = e.target.value;
      
      if (field === 'flow' || field === 'pressure' || field === 'Tin' || field === 'Tout') {
        val = parseFloat(val);
      }
      
      state.streams[idx][field] = val;
      document.getElementById('example-select').value = 'custom';
      
      // Update cell color if type changed
      if (field === 'type') {
        e.target.className = `input-cell type-select ${val}`;
      }
      
      solveAndRender();
    });
  });

  // Bind delete button events
  tbody.querySelectorAll('.delete-stream-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      const idx = parseInt(e.currentTarget.getAttribute('data-idx'));
      state.streams.splice(idx, 1);
      document.getElementById('example-select').value = 'custom';
      renderStreamTable();
      solveAndRender();
    });
  });
}

// --- Solve PTA from Flask and Re-Render Targets & Plots ---
async function solveAndRender() {
  try {
    const res = await fetch('/api/solve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        streams: state.streams,
        deltaTmin: state.deltaTmin
      })
    });

    if (!res.ok) throw new Error("Solver server error.");
    const data = await res.json();
    
    state.targets = data.targets;
    state.curves = data.curves;
    state.profiles = data.profiles;
    state.comparison = data.comparison;

    updateTargetsPanel();
    renderCharts();
  } catch (err) {
    console.error("Solver error:", err);
  }
}

// --- Update Numerical Comparison Panel ---
function updateTargetsPanel() {
  const unit = "kW"; // All outputs scaled to kW in our new app.py
  
  // Set Rigorous targets
  document.getElementById('target-qh').innerHTML = `${state.targets.QHmin.toFixed(1)} <span class="unit">${unit}</span>`;
  document.getElementById('target-qc').innerHTML = `${state.targets.QCmin.toFixed(1)} <span class="unit">${unit}</span>`;
  document.getElementById('target-pinch-hot').innerHTML = `${state.targets.pinchHot.toFixed(1)} <span class="unit">°C</span>`;
  document.getElementById('target-pinch-cold').innerHTML = `${state.targets.pinchCold.toFixed(1)} <span class="unit">°C</span>`;
  document.getElementById('target-nmin').textContent = state.targets.nMin;

  // Set Const CP target approximations
  document.getElementById('const-qh').innerHTML = `${state.comparison.constQH.toFixed(1)} <span class="unit">${unit}</span>`;
  document.getElementById('const-qc').innerHTML = `${state.comparison.constQC.toFixed(1)} <span class="unit">${unit}</span>`;
  document.getElementById('const-pinch').innerHTML = `${state.comparison.constPinchShifted.toFixed(1)} <span class="unit">°C</span>`;

  // Update deviations badges
  const qhBadge = document.getElementById('err-qh');
  const qcBadge = document.getElementById('err-qc');

  const updateBadge = (badge, err) => {
    badge.textContent = `${err > 0 ? '+' : ''}${err.toFixed(1)}%`;
    if (Math.abs(err) < 0.1) {
      badge.textContent = "0.0%";
      badge.className = "error-badge no-error";
    } else {
      badge.className = "error-badge";
    }
  };

  updateBadge(qhBadge, state.comparison.errQH);
  updateBadge(qcBadge, state.comparison.errQC);
}

// --- Main Chart Router ---
function renderCharts() {
  const activeTab = document.querySelector('.tab-nav .tab-btn.active').getAttribute('data-tab');
  
  if (activeTab === 'composite-tab') {
    drawCompositeCurves();
  } else if (activeTab === 'gcc-tab') {
    drawGrandCompositeCurve();
  } else if (activeTab === 'cp-tab') {
    drawCpProfiles();
  }
}

// --- Draw SVG Composite Curves ---
function drawCompositeCurves() {
  const svg = document.getElementById('composite-svg');
  svg.innerHTML = '';

  const { hot_H, hot_T, cold_H_shifted, cold_T, h_max, t_min, t_max } = state.curves;
  if (!hot_H || hot_H.length === 0) return;

  const w = 800;
  const h = 500;
  const padding = 50;

  const scaleX = (val) => padding + (val / (h_max || 1)) * (w - 2 * padding);
  const scaleY = (val) => h - padding - ((val - t_min) / ((t_max - t_min) || 1)) * (h - 2 * padding);

  // Draw Grid Lines & Ticks
  const gridGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  // Y-axis temperature ticks (steps of 20 or 50 depending on range)
  const range = t_max - t_min;
  const step = range > 200 ? 50 : 25;
  
  for (let t = Math.floor(t_min / step) * step; t <= t_max; t += step) {
    const sy = scaleY(t);
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('x1', padding); line.setAttribute('y1', sy);
    line.setAttribute('x2', w - padding); line.setAttribute('y2', sy);
    line.setAttribute('class', 'chart-grid-line');
    gridGroup.appendChild(line);

    const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    text.setAttribute('x', padding - 10); text.setAttribute('y', sy + 4);
    text.setAttribute('text-anchor', 'end');
    text.setAttribute('class', 'chart-axis-text');
    text.textContent = `${t.toFixed(0)}°C`;
    gridGroup.appendChild(text);
  }

  // X-axis Enthalpy ticks
  const h_step = Math.ceil(h_max / 5 / 10) * 10 || 10;
  for (let x = 0; x <= h_max; x += h_step) {
    const sx = scaleX(x);
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('x1', sx); line.setAttribute('y1', padding);
    line.setAttribute('x2', sx); line.setAttribute('y2', h - padding);
    line.setAttribute('class', 'chart-grid-line');
    gridGroup.appendChild(line);

    const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    text.setAttribute('x', sx); text.setAttribute('y', h - padding + 15);
    text.setAttribute('text-anchor', 'middle');
    text.setAttribute('class', 'chart-axis-text');
    text.textContent = x.toFixed(0);
    gridGroup.appendChild(text);
  }
  svg.appendChild(gridGroup);

  // Gradient Defs
  const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
  defs.innerHTML = `
    <linearGradient id="hot-grad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#f43f5e" />
      <stop offset="100%" stop-color="#ec4899" />
    </linearGradient>
    <linearGradient id="cold-grad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#0891b2" />
      <stop offset="100%" stop-color="#06b6d4" />
    </linearGradient>
  `;
  svg.appendChild(defs);

  // Axes
  const axes = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  const ax = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  ax.setAttribute('x1', padding); ax.setAttribute('y1', h - padding); ax.setAttribute('x2', w - padding); ax.setAttribute('y2', h - padding);
  ax.setAttribute('class', 'chart-axis-line');
  const ay = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  ay.setAttribute('x1', padding); ay.setAttribute('y1', padding); ay.setAttribute('x2', padding); ay.setAttribute('y2', h - padding);
  ay.setAttribute('class', 'chart-axis-line');
  axes.appendChild(ax); axes.appendChild(ay);

  // Axis labels
  const xl = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  xl.setAttribute('x', w / 2); xl.setAttribute('y', h - 10); xl.setAttribute('text-anchor', 'middle'); xl.setAttribute('class', 'chart-axis-text');
  xl.setAttribute('style', 'font-size:11px; fill:#fff; font-weight: 500;');
  xl.textContent = 'Enthalpy Heat Duty (kW)';
  const yl = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  yl.setAttribute('x', 12); yl.setAttribute('y', h / 2); yl.setAttribute('text-anchor', 'middle'); yl.setAttribute('class', 'chart-axis-text');
  yl.setAttribute('transform', `rotate(-90, 12, ${h / 2})`);
  yl.setAttribute('style', 'font-size:11px; fill:#fff; font-weight: 500;');
  yl.textContent = 'Temperature (°C)';
  axes.appendChild(xl); axes.appendChild(yl);
  svg.appendChild(axes);

  // Helper to draw a single continuous smooth curve
  const drawCurve = (ccH, ccT, className) => {
    let pathD = '';
    ccH.forEach((x, idx) => {
      const sx = scaleX(x);
      const sy = scaleY(ccT[idx]);
      pathD += `${idx === 0 ? 'M' : 'L'} ${sx} ${sy}`;
    });
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', pathD);
    path.setAttribute('class', `chart-curve ${className}`);
    svg.appendChild(path);
  };

  drawCurve(hot_H, hot_T, 'hot');
  drawCurve(cold_H_shifted, cold_T, 'cold');

  // Draw Pinch Point indicator dot
  const ph = state.targets.pinchHot;
  const pc = state.targets.pinchCold;
  
  // Find where T_hot equals pinchHot on hot composite
  const findPinchH = (ccH, ccT, pinchT) => {
    for (let i = 0; i < ccT.length - 1; i++) {
      const t1 = ccT[i], t2 = ccT[i+1];
      if ((t1 <= pinchT && pinchT <= t2) || (t2 <= pinchT && pinchT <= t1)) {
        const frac = (pinchT - t1) / (t2 - t1 || 1);
        return ccH[i] + frac * (ccH[i+1] - ccH[i]);
      }
    }
    return 0.0;
  };

  const ph_H = findPinchH(hot_H, hot_T, ph);
  const pc_H = findPinchH(cold_H_shifted, cold_T, pc);

  const pinchGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  
  const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  line.setAttribute('x1', scaleX(ph_H)); line.setAttribute('y1', scaleY(ph));
  line.setAttribute('x2', scaleX(pc_H)); line.setAttribute('y2', scaleY(pc));
  line.setAttribute('stroke', 'var(--color-pinch)');
  line.setAttribute('stroke-dasharray', '4 3');
  line.setAttribute('stroke-width', '1.5');
  pinchGroup.appendChild(line);

  const dotHot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
  dotHot.setAttribute('cx', scaleX(ph_H)); dotHot.setAttribute('cy', scaleY(ph));
  dotHot.setAttribute('r', 5); dotHot.setAttribute('class', 'chart-marker pinch');
  pinchGroup.appendChild(dotHot);

  const dotCold = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
  dotCold.setAttribute('cx', scaleX(pc_H)); dotCold.setAttribute('cy', scaleY(pc));
  dotCold.setAttribute('r', 5); dotCold.setAttribute('class', 'chart-marker pinch');
  pinchGroup.appendChild(dotCold);

  const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  text.setAttribute('x', (scaleX(ph_H) + scaleX(pc_H)) / 2 + 10);
  text.setAttribute('y', (scaleY(ph) + scaleY(pc)) / 2 + 4);
  text.setAttribute('fill', 'var(--color-pinch)');
  text.setAttribute('style', 'font-size: 10px; font-weight: 600;');
  text.textContent = `Pinch: ${ph.toFixed(1)}°C / ${pc.toFixed(1)}°C`;
  pinchGroup.appendChild(text);

  svg.appendChild(pinchGroup);
}

// --- Draw SVG Grand Composite Curve (DGCC) ---
function drawGrandCompositeCurve() {
  const svg = document.getElementById('gcc-svg');
  svg.innerHTML = '';

  const { gcc_Q, gcc_T, t_min, t_max } = state.curves;
  if (!gcc_Q || gcc_Q.length === 0) return;

  const w = 800;
  const h = 500;
  const padding = 50;

  const q_max = Math.max(...gcc_Q) || 1.0;
  const scaleX = (val) => padding + (val / q_max) * (w - 2 * padding);
  const scaleY = (val) => h - padding - ((val - t_min) / ((t_max - t_min) || 1)) * (h - 2 * padding);

  // Draw Grid Lines & Ticks
  const gridGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  const range = t_max - t_min;
  const step = range > 200 ? 50 : 25;
  for (let t = Math.floor(t_min / step) * step; t <= t_max; t += step) {
    const sy = scaleY(t);
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('x1', padding); line.setAttribute('y1', sy);
    line.setAttribute('x2', w - padding); line.setAttribute('y2', sy);
    line.setAttribute('class', 'chart-grid-line');
    gridGroup.appendChild(line);

    const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    text.setAttribute('x', padding - 10); text.setAttribute('y', sy + 4);
    text.setAttribute('text-anchor', 'end');
    text.setAttribute('class', 'chart-axis-text');
    text.textContent = `${t.toFixed(0)}°C`;
    gridGroup.appendChild(text);
  }

  const q_step = Math.ceil(q_max / 5 / 10) * 10 || 10;
  for (let x = 0; x <= q_max; x += q_step) {
    const sx = scaleX(x);
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('x1', sx); line.setAttribute('y1', padding);
    line.setAttribute('x2', sx); line.setAttribute('y2', h - padding);
    line.setAttribute('class', 'chart-grid-line');
    gridGroup.appendChild(line);

    const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    text.setAttribute('x', sx); text.setAttribute('y', h - padding + 15);
    text.setAttribute('text-anchor', 'middle');
    text.setAttribute('class', 'chart-axis-text');
    text.textContent = x.toFixed(0);
    gridGroup.appendChild(text);
  }
  svg.appendChild(gridGroup);

  // Axes
  const axes = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  const ax = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  ax.setAttribute('x1', padding); ax.setAttribute('y1', h - padding); ax.setAttribute('x2', w - padding); ax.setAttribute('y2', h - padding);
  ax.setAttribute('class', 'chart-axis-line');
  const ay = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  ay.setAttribute('x1', padding); ay.setAttribute('y1', padding); ay.setAttribute('x2', padding); ay.setAttribute('y2', h - padding);
  ay.setAttribute('class', 'chart-axis-line');
  axes.appendChild(ax); axes.appendChild(ay);

  // Axis labels
  const xl = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  xl.setAttribute('x', w / 2); xl.setAttribute('y', h - 10); xl.setAttribute('text-anchor', 'middle'); xl.setAttribute('class', 'chart-axis-text');
  xl.setAttribute('style', 'font-size:11px; fill:#fff; font-weight: 500;');
  xl.textContent = 'Net Cascade Heat Flow (kW)';
  const yl = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  yl.setAttribute('x', 12); yl.setAttribute('y', h / 2); yl.setAttribute('text-anchor', 'middle'); yl.setAttribute('class', 'chart-axis-text');
  yl.setAttribute('transform', `rotate(-90, 12, ${h / 2})`);
  yl.setAttribute('style', 'font-size:11px; fill:#fff; font-weight: 500;');
  yl.textContent = 'Shifted Temperature (°C)';
  axes.appendChild(xl); axes.appendChild(yl);
  svg.appendChild(axes);

  // Draw DGCC Line Path
  let pathD = '';
  gcc_Q.forEach((q, idx) => {
    const sx = scaleX(q);
    const sy = scaleY(gcc_T[idx]);
    pathD += `${idx === 0 ? 'M' : 'L'} ${sx} ${sy}`;
  });
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('d', pathD);
  path.setAttribute('class', 'chart-curve gcc');
  svg.appendChild(path);

  // Draw Shifted Pinch Line
  const pinchShift = state.targets.pinchShifted;
  const pinchGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  
  const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  line.setAttribute('x1', scaleX(0)); line.setAttribute('y1', scaleY(pinchShift));
  line.setAttribute('x2', w - padding); line.setAttribute('y2', scaleY(pinchShift));
  line.setAttribute('stroke', 'var(--color-pinch)');
  line.setAttribute('stroke-dasharray', '5 3');
  line.setAttribute('stroke-width', '1.5');
  pinchGroup.appendChild(line);

  const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
  dot.setAttribute('cx', scaleX(0)); dot.setAttribute('cy', scaleY(pinchShift));
  dot.setAttribute('r', 5); dot.setAttribute('class', 'chart-marker pinch');
  pinchGroup.appendChild(dot);

  const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  text.setAttribute('x', scaleX(0) + 12); text.setAttribute('y', scaleY(pinchShift) + 4);
  text.setAttribute('fill', 'var(--color-pinch)');
  text.setAttribute('style', 'font-size: 10px; font-weight: 600;');
  text.textContent = `Shifted Pinch: ${pinchShift.toFixed(1)}°C`;
  pinchGroup.appendChild(text);

  svg.appendChild(pinchGroup);
}

// --- Draw Specific Heat Capacity Profiles ---
function drawCpProfiles() {
  const svg = document.getElementById('cp-svg');
  svg.innerHTML = '';

  const profiles = state.profiles;
  if (!profiles || Object.keys(profiles).length === 0) return;

  const w = 800;
  const h = 500;
  const padding = 50;

  // Determine global bounds for Cp and Temp plots
  let min_T = 9999;
  let max_T = -9999;
  let max_Cp = 0;

  Object.values(profiles).forEach(p => {
    p.forEach(([t, cp]) => {
      min_T = min(min_T, t);
      max_T = max(max_T, t);
      max_Cp = max(max_Cp, cp);
    });
  });

  const scaleX = (val) => padding + ((val - min_T) / ((max_T - min_T) || 1)) * (w - 2 * padding);
  const scaleY = (val) => h - padding - (val / (max_Cp || 1)) * (h - 2 * padding);

  // Draw Ticks & Grid
  const gridGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  const t_range = max_T - min_T;
  const t_step = t_range > 200 ? 50 : 25;
  for (let t = Math.floor(min_T / t_step) * t_step; t <= max_T; t += t_step) {
    const sx = scaleX(t);
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('x1', sx); line.setAttribute('y1', padding);
    line.setAttribute('x2', sx); line.setAttribute('y2', h - padding);
    line.setAttribute('class', 'chart-grid-line');
    gridGroup.appendChild(line);

    const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    text.setAttribute('x', sx); text.setAttribute('y', h - padding + 15);
    text.setAttribute('text-anchor', 'middle');
    text.setAttribute('class', 'chart-axis-text');
    text.textContent = `${t.toFixed(0)}°C`;
    gridGroup.appendChild(text);
  }

  const cp_step = max_Cp / 5;
  for (let y = 0; y <= max_Cp; y += cp_step) {
    const sy = scaleY(y);
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('x1', padding); line.setAttribute('y1', sy);
    line.setAttribute('x2', w - padding); line.setAttribute('y2', sy);
    line.setAttribute('class', 'chart-grid-line');
    gridGroup.appendChild(line);

    const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    text.setAttribute('x', padding - 10); text.setAttribute('y', sy + 4);
    text.setAttribute('text-anchor', 'end');
    text.setAttribute('class', 'chart-axis-text');
    text.textContent = y.toFixed(1);
    gridGroup.appendChild(text);
  }
  svg.appendChild(gridGroup);

  // Axes
  const axes = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  const ax = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  ax.setAttribute('x1', padding); ax.setAttribute('y1', h - padding); ax.setAttribute('x2', w - padding); ax.setAttribute('y2', h - padding);
  ax.setAttribute('class', 'chart-axis-line');
  const ay = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  ay.setAttribute('x1', padding); ay.setAttribute('y1', padding); ay.setAttribute('x2', padding); ay.setAttribute('y2', h - padding);
  ay.setAttribute('class', 'chart-axis-line');
  axes.appendChild(ax); axes.appendChild(ay);

  const xl = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  xl.setAttribute('x', w / 2); xl.setAttribute('y', h - 10); xl.setAttribute('text-anchor', 'middle'); xl.setAttribute('class', 'chart-axis-text');
  xl.setAttribute('style', 'font-size:11px; fill:#fff; font-weight: 500;');
  xl.textContent = 'Actual Stream Temperature (°C)';
  const yl = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  yl.setAttribute('x', 12); yl.setAttribute('y', h / 2); yl.setAttribute('text-anchor', 'middle'); yl.setAttribute('class', 'chart-axis-text');
  yl.setAttribute('transform', `rotate(-90, 12, ${h / 2})`);
  yl.setAttribute('style', 'font-size:11px; fill:#fff; font-weight: 500;');
  yl.textContent = 'Heat Flow Capacity CP (kW/°C)';
  axes.appendChild(xl); axes.appendChild(yl);
  svg.appendChild(axes);

  // Dynamic colors for each stream line in profile
  const colors = ["#06b6d4", "#ec4899", "#10b981", "#f59e0b", "#3b82f6", "#8b5cf6", "#14b8a6", "#f43f5e", "#a855f7"];
  const legendDiv = document.getElementById('cp-legend');
  legendDiv.innerHTML = '';

  Object.entries(profiles).forEach(([streamId, dataPoints], index) => {
    let pathD = '';
    dataPoints.forEach(([t, cp], idx) => {
      const sx = scaleX(t);
      const sy = scaleY(cp);
      pathD += `${idx === 0 ? 'M' : 'L'} ${sx} ${sy}`;
    });

    const color = colors[index % colors.length];

    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', pathD);
    path.setAttribute('fill', 'none');
    path.setAttribute('stroke', color);
    path.setAttribute('stroke-width', '2.5');
    path.setAttribute('class', 'chart-curve stream-cp');
    svg.appendChild(path);

    // Add legend item
    const item = document.createElement('div');
    item.className = "legend-item";
    item.innerHTML = `<span class="legend-color" style="background:${color};"></span>${streamId} Profile`;
    legendDiv.appendChild(item);
  });
}

// Math Min/Max helpers
function min(a, b) { return a < b ? a : b; }
function max(a, b) { return a > b ? a : b; }
