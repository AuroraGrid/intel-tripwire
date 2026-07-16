(() => {
  const workspace = document.querySelector('.workspace');
  if (!workspace) return;

  const style = document.createElement('style');
  style.textContent = `
    .phase10-card { margin: 0 0 14px; padding: 12px; border: 1px solid var(--line); border-radius: 12px; background: #091321; }
    .phase10-head { display: flex; gap: 10px; justify-content: space-between; align-items: center; flex-wrap: wrap; }
    .phase10-controls { display: flex; gap: 8px; flex-wrap: wrap; }
    .phase10-map { position: relative; height: 430px; margin-top: 10px; border: 1px solid var(--line); border-radius: 10px; overflow: hidden; background: radial-gradient(circle at 50% 50%, #10243a, #07101d 70%); }
    .phase10-map canvas { position: absolute; inset: 0; width: 100%; height: 100%; }
    .phase10-overlay { position: absolute; left: 10px; top: 10px; padding: 7px 9px; border-radius: 8px; background: #07101dcc; border: 1px solid var(--line); font-size: 12px; color: var(--muted); }
    .phase10-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 8px; margin-top: 10px; }
    .phase10-stat { padding: 9px; border: 1px solid var(--line); border-radius: 8px; background: #0d1727; }
    .phase10-stat strong { display: block; font-size: 18px; }
  `;
  document.head.appendChild(style);

  const panel = document.createElement('section');
  panel.className = 'phase10-card';
  panel.innerHTML = `
    <div class="phase10-head">
      <div><strong>PHASE 10 · Geographic intelligence</strong><div class="meta">Interactive incident field, clustering, bounding-box and time filtering</div></div>
      <div class="phase10-controls">
        <select id="p10Severity" class="control"><option value="">All severity</option><option>critical</option><option>high</option><option>medium</option><option>low</option></select>
        <select id="p10Category" class="control"><option value="">All categories</option><option>conflict</option><option>civil_unrest</option><option>disaster</option><option>cyber</option><option>infrastructure</option><option>health</option><option>world</option></select>
        <button class="btn" id="p10Refresh">Refresh map</button>
      </div>
    </div>
    <div class="phase10-map"><canvas id="p10Canvas"></canvas><div class="phase10-overlay" id="p10Overlay">Loading geographic index…</div></div>
    <div class="phase10-grid" id="p10Stats"></div>
  `;
  workspace.prepend(panel);

  const canvas = document.getElementById('p10Canvas');
  const context = canvas.getContext('2d');
  const overlay = document.getElementById('p10Overlay');
  const stats = document.getElementById('p10Stats');
  let clusters = [];

  function resize() {
    const scale = window.devicePixelRatio || 1;
    canvas.width = Math.floor(canvas.clientWidth * scale);
    canvas.height = Math.floor(canvas.clientHeight * scale);
    context.setTransform(scale, 0, 0, scale, 0, 0);
  }

  function draw() {
    resize();
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    context.clearRect(0, 0, width, height);
    context.strokeStyle = 'rgba(100,160,210,.18)';
    context.lineWidth = 1;
    for (let lon = -150; lon <= 150; lon += 30) {
      const x = (lon + 180) / 360 * width;
      context.beginPath(); context.moveTo(x, 0); context.lineTo(x, height); context.stroke();
    }
    for (let lat = -60; lat <= 60; lat += 30) {
      const y = (90 - lat) / 180 * height;
      context.beginPath(); context.moveTo(0, y); context.lineTo(width, y); context.stroke();
    }
    for (const cluster of clusters) {
      const x = (Number(cluster.longitude) + 180) / 360 * width;
      const y = (90 - Number(cluster.latitude)) / 180 * height;
      const radius = Math.min(24, 4 + Math.sqrt(Number(cluster.count) || 1) * 3);
      context.beginPath();
      context.arc(x, y, radius, 0, Math.PI * 2);
      context.fillStyle = cluster.severity === 'critical' ? 'rgba(255,80,80,.72)' : cluster.severity === 'high' ? 'rgba(255,160,70,.70)' : 'rgba(50,205,255,.68)';
      context.fill();
    }
  }

  function parameters() {
    const query = new URLSearchParams({ zoom: '3' });
    const severity = document.getElementById('p10Severity').value;
    const category = document.getElementById('p10Category').value;
    if (severity) query.set('severities', severity);
    if (category) query.set('categories', category);
    return query;
  }

  async function refresh() {
    overlay.textContent = 'Refreshing geographic index…';
    try {
      const query = parameters();
      const [clusterResponse, summary] = await Promise.all([
        api('/api/platform/geo/clusters?' + query.toString()),
        api('/api/platform/geo/summary?' + query.toString())
      ]);
      clusters = clusterResponse.clusters || [];
      draw();
      overlay.textContent = `${summary.filtered_incidents || 0} filtered · ${clusterResponse.count || 0} clusters · ${(100 * (summary.coverage_ratio || 0)).toFixed(1)}% geolocated`;
      stats.innerHTML = `
        <div class="phase10-stat"><span class="meta">Geolocated</span><strong>${esc(summary.geolocated_incidents || 0)}</strong></div>
        <div class="phase10-stat"><span class="meta">Filtered</span><strong>${esc(summary.filtered_incidents || 0)}</strong></div>
        <div class="phase10-stat"><span class="meta">Clusters</span><strong>${esc(clusterResponse.count || 0)}</strong></div>
        <div class="phase10-stat"><span class="meta">Coverage</span><strong>${esc((100 * (summary.coverage_ratio || 0)).toFixed(1))}%</strong></div>
      `;
    } catch (error) {
      overlay.textContent = 'Geographic engine unavailable: ' + error.message;
    }
  }

  window.addEventListener('resize', draw);
  document.getElementById('p10Refresh').onclick = refresh;
  document.getElementById('p10Severity').onchange = refresh;
  document.getElementById('p10Category').onchange = refresh;
  refresh();
})();
