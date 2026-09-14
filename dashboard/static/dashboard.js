/* Dashboard frontend: renders the snapshots pushed over SocketIO by
   dashboard/app.py. Topology positions are computed here (not sent by the
   server) so the layout adapts to any switch count. */

const COLORS = {
  ok: getCss('--ok'),
  warn: getCss('--warn'),
  critical: getCss('--critical'),
  down: getCss('--down'),
  accent: getCss('--accent'),
  muted: getCss('--muted'),
  faint: getCss('--faint'),
  panel2: getCss('--panel-2'),
  border: getCss('--border-strong'),
  text: getCss('--text'),
};

function getCss(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/* ---------- topology ---------- */

const SVG_NS = 'http://www.w3.org/2000/svg';
const CENTER = { x: 400, y: 258 };
const RADIUS = 168;

function switchPositions(switches) {
  // Ring layout: switch i sits at angle i/N around a circle, starting at the
  // top. Chord links then cut across the middle, which is exactly how the
  // ring+chord topology reads visually.
  const positions = {};
  const n = switches.length;
  switches.forEach((dpid, i) => {
    const angle = (i / n) * 2 * Math.PI - Math.PI / 2;
    positions[dpid] = {
      x: CENTER.x + RADIUS * Math.cos(angle),
      y: CENTER.y + RADIUS * Math.sin(angle),
      angle,
    };
  });
  return positions;
}

function el(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

function renderTopology(topology) {
  const svg = document.getElementById('topology');
  svg.textContent = '';

  const switches = topology.switches || [];
  if (!switches.length) {
    svg.appendChild(el('text', {
      x: CENTER.x, y: CENTER.y, 'text-anchor': 'middle',
      fill: COLORS.faint, 'font-size': '13',
    })).textContent = 'waiting for switches to connect…';
    return;
  }

  const pos = switchPositions(switches);

  // Links first so the switch boxes draw on top of the line ends.
  for (const link of topology.links || []) {
    const a = pos[link.source];
    const b = pos[link.target];
    if (!a || !b) continue;
    const colour = link.up ? COLORS[link.level] : COLORS.down;
    const line = el('line', {
      x1: a.x, y1: a.y, x2: b.x, y2: b.y,
      stroke: colour,
      'stroke-width': link.level === 'critical' ? 3.6 : 2.6,
      'stroke-linecap': 'round',
    });
    if (!link.up) line.setAttribute('stroke-dasharray', '6,5');
    const title = el('title', {});
    title.textContent =
      `s${link.source}–s${link.target}  ` +
      `util ${(link.utilization * 100).toFixed(1)}%  ` +
      `delay ${link.delay_ms}ms  loss ${link.loss_pct}%` +
      (link.up ? '' : '  (DOWN)');
    line.appendChild(title);
    svg.appendChild(line);
  }

  for (const dpid of switches) {
    const p = pos[dpid];
    svg.appendChild(el('rect', {
      x: p.x - 16, y: p.y - 16, width: 32, height: 32, rx: 8,
      fill: COLORS.panel2, stroke: COLORS.border, 'stroke-width': 1.6,
    }));
    const label = el('text', {
      x: p.x, y: p.y + 4.5, 'text-anchor': 'middle',
      fill: COLORS.text, 'font-size': '12', 'font-weight': '600',
      'font-family': 'JetBrains Mono, monospace',
    });
    label.textContent = `s${dpid}`;
    svg.appendChild(label);

    // Host stub, pointing radially outward from the ring.
    const hx = CENTER.x + (RADIUS + 40) * Math.cos(p.angle);
    const hy = CENTER.y + (RADIUS + 40) * Math.sin(p.angle);
    svg.appendChild(el('line', {
      x1: p.x, y1: p.y, x2: hx, y2: hy, stroke: COLORS.border, 'stroke-width': 1.2,
    }));
    svg.appendChild(el('circle', { cx: hx, cy: hy, r: 4.5, fill: COLORS.faint }));
  }
}

/* ---------- metrics table ---------- */

function renderMetrics(rows) {
  const body = document.getElementById('metrics-body');
  if (!rows || !rows.length) {
    body.innerHTML = '<tr><td colspan="6" class="muted center">waiting for stats…</td></tr>';
    return;
  }
  body.innerHTML = rows.map((row) => `
    <tr class="${row.up ? '' : 'down'}">
      <td>${escapeHtml(row.link)}</td>
      <td class="level-${row.level}">${(row.utilization * 100).toFixed(1)}%</td>
      <td>${row.delay_ms.toFixed(2)}ms</td>
      <td>${row.loss_pct.toFixed(2)}%</td>
      <td>${row.trust.toFixed(2)}</td>
      <td><span class="swatch ${row.up ? row.level : 'down'}"></span></td>
    </tr>`).join('');
}

/* ---------- decision log ---------- */

function renderDecisions(decisions) {
  const container = document.getElementById('decision-log');
  if (!decisions || !decisions.length) {
    container.innerHTML = '<p class="muted center pad">no decisions yet — send traffic between hosts</p>';
    return;
  }

  container.innerHTML = decisions.map((decision) => {
    const time = new Date(decision.timestamp * 1000).toLocaleTimeString();
    const pathText = decision.path.map((d) => `s${d}`).join(' → ');
    const badges = [`<span class="badge ${decision.mode_used}">${decision.mode_used.toUpperCase()}</span>`];
    if (decision.event === 'reroute') badges.unshift('<span class="badge reroute">reroute</span>');
    if (decision.fallback_reason) badges.unshift('<span class="badge fallback">fallback</span>');

    return `
      <div class="decision">
        <div class="decision-head">
          <span class="decision-time mono">${time}</span>
          <span>${badges.join(' ')}</span>
        </div>
        <div class="decision-body">
          <span class="mono">${shortMac(decision.src_mac)} → ${shortMac(decision.dst_mac)}</span>
          via <span class="mono">${pathText}</span>
        </div>
        ${reasoning(decision)}
      </div>`;
  }).join('');
}

function reasoning(decision) {
  if (decision.fallback_reason) {
    return `<div class="decision-why">fell back to Dijkstra: ${escapeHtml(decision.fallback_reason)}</div>`;
  }
  const chosen = (decision.candidates || []).find((c) => c.chosen);
  if (!chosen) return '';

  const others = (decision.candidates || []).filter((c) => !c.chosen);
  let why = `bottleneck util ${(chosen.bottleneck_utilization * 100).toFixed(0)}%, ` +
            `delay ${chosen.total_delay_ms.toFixed(1)}ms, loss ${chosen.max_loss_pct.toFixed(1)}%`;

  // Only worth calling out when it actually passed over a busier option.
  const worst = others.reduce(
    (acc, c) => (c.bottleneck_utilization > (acc ? acc.bottleneck_utilization : -1) ? c : acc), null);
  if (worst && worst.bottleneck_utilization > chosen.bottleneck_utilization + 0.05) {
    why += ` <span class="beat">— avoided ${worst.path.map((d) => `s${d}`).join('→')} ` +
           `at ${(worst.bottleneck_utilization * 100).toFixed(0)}%</span>`;
  }
  return `<div class="decision-why mono">${why}</div>`;
}

function shortMac(mac) {
  // Mininet host MACs are all 00:00:00:00:00:XX - the last octet is the only
  // part that distinguishes them, so lead with that.
  const parts = String(mac).split(':');
  return parts.length === 6 ? `h${parseInt(parts[5], 16)}` : mac;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* ---------- KPIs ---------- */

function renderSummary(summary) {
  document.getElementById('kpi-flows').textContent = summary.active_flows;
  document.getElementById('kpi-util').textContent = `${(summary.avg_utilization * 100).toFixed(1)}%`;
  document.getElementById('kpi-util-sub').textContent = `across ${summary.link_count} switch links`;
  document.getElementById('kpi-decisions').textContent = summary.decisions_total;
  document.getElementById('kpi-decisions-sub').textContent =
    `${summary.decisions_rl} RL / ${summary.decisions_dijkstra} Dijkstra`;
  document.getElementById('kpi-mode').textContent = summary.routing_mode.toUpperCase();
  document.getElementById('kpi-fallbacks').textContent = `fallbacks: ${summary.fallbacks}`;
  document.getElementById('brand-sub').textContent =
    `${summary.switch_count} switches · ${summary.link_count} links · mode ${summary.routing_mode}`;
  document.getElementById('topology-sub').textContent =
    `${summary.switch_count} switches · ${summary.link_count} links`;

  for (const button of document.querySelectorAll('.mode-toggle button')) {
    button.classList.toggle('active', button.dataset.mode === summary.routing_mode);
  }
}

/* ---------- charts ---------- */

const chartDefaults = {
  responsive: true,
  animation: false,
  plugins: { legend: { labels: { color: COLORS.muted, font: { size: 11 } } } },
  scales: {
    x: { ticks: { color: COLORS.faint, font: { size: 10 } }, grid: { color: COLORS.border } },
    y: { ticks: { color: COLORS.faint, font: { size: 10 } }, grid: { color: COLORS.border } },
  },
};

async function loadRewardChart() {
  const response = await fetch('/api/training-curve');
  const data = await response.json();
  const points = data.points || [];
  const sub = document.getElementById('curve-sub');
  if (!points.length) {
    sub.textContent = 'no training log found — run: python3 -m rl.train';
    return;
  }
  sub.textContent = `Phase 2 training run · ${points[points.length - 1].episode} episodes`;

  new Chart(document.getElementById('reward-chart'), {
    type: 'line',
    data: {
      labels: points.map((p) => p.episode),
      datasets: [{
        label: 'avg reward / step',
        data: points.map((p) => p.avg_reward),
        borderColor: COLORS.accent,
        backgroundColor: 'transparent',
        borderWidth: 2,
        pointRadius: 0,
        tension: 0.25,
      }],
    },
    options: {
      ...chartDefaults,
      scales: {
        ...chartDefaults.scales,
        x: { ...chartDefaults.scales.x, title: { display: true, text: 'Episode', color: COLORS.faint } },
      },
    },
  });
}

async function loadBenchmarkChart() {
  const response = await fetch('/api/benchmark');
  const data = await response.json();
  const sub = document.getElementById('benchmark-sub');

  if (!data.available) {
    sub.textContent = 'no benchmark run yet — run: sudo python3 -m scripts.benchmark';
    return;
  }

  const results = data.results;
  const modes = Object.keys(results.modes || {});
  if (!modes.length) {
    sub.textContent = 'benchmark file has no results';
    return;
  }
  sub.textContent = `measured ${new Date(results.timestamp * 1000).toLocaleString()}`;

  // Normalise each metric against the worst mode so different units share
  // one axis; the tooltip carries the real measured values.
  const metrics = [
    { key: 'avg_latency_ms', label: 'Latency (ms)', lowerIsBetter: true },
    { key: 'throughput_mbps', label: 'Throughput (Mbps)', lowerIsBetter: false },
    { key: 'packet_loss_pct', label: 'Loss (%)', lowerIsBetter: true },
    { key: 'recovery_time_s', label: 'Recovery (s)', lowerIsBetter: true },
  ].filter((m) => modes.some((mode) => results.modes[mode][m.key] != null));

  const palette = { rl: COLORS.accent, dijkstra: COLORS.muted };
  new Chart(document.getElementById('benchmark-chart'), {
    type: 'bar',
    data: {
      labels: metrics.map((m) => m.label),
      datasets: modes.map((mode) => ({
        label: mode === 'rl' ? 'RL agent' : 'Dijkstra baseline',
        data: metrics.map((m) => results.modes[mode][m.key] ?? 0),
        backgroundColor: palette[mode] || COLORS.faint,
        borderRadius: 4,
      })),
    },
    options: { ...chartDefaults, scales: { ...chartDefaults.scales, y: { ...chartDefaults.scales.y, beginAtZero: true } } },
  });
}

/* ---------- wiring ---------- */

const socket = io();

socket.on('connect', () => {
  document.getElementById('conn-dot').className = 'dot live';
  document.getElementById('conn-label').textContent = 'connected';
});

socket.on('disconnect', () => {
  document.getElementById('conn-dot').className = 'dot lost';
  document.getElementById('conn-label').textContent = 'disconnected';
});

socket.on('snapshot', (snapshot) => {
  renderSummary(snapshot.summary);
  renderTopology(snapshot.topology);
  renderMetrics(snapshot.metrics);
  renderDecisions(snapshot.decisions);
});

for (const button of document.querySelectorAll('.mode-toggle button')) {
  button.addEventListener('click', async () => {
    const mode = button.dataset.mode;
    button.disabled = true;
    try {
      await fetch(`/api/mode/${mode}`, { method: 'POST' });
    } finally {
      button.disabled = false;
    }
  });
}

loadRewardChart();
loadBenchmarkChart();
