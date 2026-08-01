(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function api(path) {
    const response = await fetch(path, { headers: { Accept: "application/json" } });
    const text = await response.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text };
    }
    if (!response.ok) {
      throw new Error(data?.error?.message || data?.error || response.statusText);
    }
    return data;
  }

  function badge(status) {
    const value = String(status || "UNKNOWN").toUpperCase();
    return `<span class="badge ${esc(value.toLowerCase())}">${esc(value)}</span>`;
  }

  function renderCapabilities(product) {
    const items = product.capabilities || [];
    const open = items.filter((item) => ["PARTIAL", "PLANNED", "BLOCKED", "NOT_VERIFIED"].includes(item.status));
    $("capabilityCounts").innerHTML = Object.entries(product.counts || {})
      .map(([key, value]) => `<div class="kpi"><div class="label">${esc(key)}</div><strong>${esc(value)}</strong></div>`)
      .join("");
    $("capabilityTable").innerHTML = items
      .map(
        (item) =>
          `<tr><td>${esc(item.name)}</td><td>${esc(item.domain)}</td><td>${badge(item.status)}</td><td>${esc(item.priority)}</td><td>${esc((item.qualification_reason || item.blocker || "").slice(0, 160))}</td></tr>`
      )
      .join("");
    $("gapCount").textContent = `${open.length} open gaps`;
  }

  function renderHealth(gop) {
    const blocks = [
      ["Webcams", gop.webcam_coverage],
      ["Transport", gop.transport_health || gop.transport_coverage],
      ["Infrastructure", gop.infrastructure_health || gop.infrastructure_coverage],
      ["Markets", gop.markets_health || gop.markets_coverage],
      ["Replay", gop.replay_coverage],
      ["Media", gop.media_coverage],
    ];
    $("healthPanels").innerHTML = blocks
      .map(([title, payload]) => {
        const json = payload ? JSON.stringify(payload, null, 2) : "No data";
        return `<section class="card"><h3>${esc(title)}</h3><pre>${esc(json).slice(0, 1800)}</pre></section>`;
      })
      .join("");
  }

  function renderIncidents(gop) {
    const rows = []
      .concat(gop.recent_infrastructure_observations || [])
      .concat(gop.recent_markets_observations || [])
      .concat(gop.recent_transport_observations || [])
      .concat(gop.recent_replay || [])
      .slice(0, 40);
    $("incidentFeed").innerHTML =
      rows
        .map((row) => {
          const title = row.title || row.symbol || row.external_id || row.record_id || "Observation";
          const meta = [row.layer || row.domain || row.provider, row.event_time || row.observed_at || ""]
            .filter(Boolean)
            .join(" · ");
          return `<article class="incident"><strong>${esc(title)}</strong><div class="meta">${esc(meta)}</div></article>`;
        })
        .join("") || `<div class="empty">No durable observations yet. Run workers or sync replay.</div>`;
  }

  async function refresh() {
    $("statusLine").textContent = "Loading runtime evidence…";
    const [bootstrap, product, gop, gaps] = await Promise.all([
      api("/api/public/ui/bootstrap"),
      api("/api/public/product/capabilities"),
      api("/api/public/global-operating-picture"),
      api("/api/public/product/gaps?priority=P0"),
    ]);
    $("phaseLabel").textContent = `Phase ${product.phase || bootstrap.phase || "?"}`;
    $("statusLine").textContent = `Runtime product phase ${product.phase}; P0 gaps ${gaps.total}`;
    renderCapabilities(product);
    renderHealth(gop);
    renderIncidents(gop);
    $("p0Gaps").innerHTML = (gaps.gaps || [])
      .slice(0, 20)
      .map((item) => `<li><strong>${esc(item.name)}</strong> ${badge(item.status)} <span class="meta">${esc(item.key)}</span></li>`)
      .join("");
  }

  function showView(name) {
    document.querySelectorAll("[data-view-panel]").forEach((node) => node.classList.toggle("active", node.dataset.viewPanel === name));
    document.querySelectorAll("[data-view]").forEach((node) => node.classList.toggle("active", node.dataset.view === name));
    history.replaceState(null, "", "#" + name);
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-view]").forEach((button) => {
      button.addEventListener("click", () => showView(button.dataset.view));
    });
    $("refreshBtn")?.addEventListener("click", () => refresh().catch((error) => ($("statusLine").textContent = error.message)));
    const initial = location.hash.replace("#", "") || "gop";
    showView(initial);
    refresh().catch((error) => {
      $("statusLine").textContent = error.message;
    });
  });
})();
