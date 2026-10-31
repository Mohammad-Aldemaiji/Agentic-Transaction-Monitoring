"use strict";
const element = id => document.getElementById(id);
async function api(path, params = {}) {
  const response = await fetch(`/api/v1/${path}?${new URLSearchParams(params)}`, { credentials: "same-origin", cache: "no-store" });
  if (!response.ok) throw new Error("Monitoring unavailable");
  const payload = await response.json();
  if (payload.status !== "success") throw new Error("Monitoring unavailable");
  return payload.data;
}
async function value(query) {
  const data = await api("query", { query });
  const result = Number(data.result[0]?.value[1]);
  return Number.isFinite(result) ? result : null;
}
function status(id, healthy) {
  element(id).textContent = healthy ? "Healthy" : "Unavailable";
  element(id).className = healthy ? "healthy" : "unhealthy";
}
async function refresh() {
  try {
    const now = Date.now() / 1000;
    const [apiUp, workerUp, heartbeat, queueSample, queued, failed, age, latency, rate, errors, alerts, history] = await Promise.all([
      value('up{job="atm-api"}'), value('up{job="atm-worker"}'),
      value('atm_worker_last_success_timestamp_seconds{job="atm-worker"}'),
      value('atm_queue_sample_timestamp_seconds{job="atm-worker"}'),
      value('atm_jobs{job="atm-worker",state="PENDING"}'),
      value('atm_jobs{job="atm-worker",state="FAILED"}'),
      value('atm_oldest_ready_job_seconds{job="atm-worker"}'),
      value('histogram_quantile(0.95, sum by (le) (rate(atm_http_response_seconds_bucket{job="atm-api"}[5m])))'),
      value('sum(rate(atm_http_responses_total{job="atm-api"}[1m]))'),
      value('sum(increase(atm_http_responses_total{job="atm-api",status=~"5.."}[5m])) or vector(0)'),
      api("alerts"), api("query_range", {query:'sum(rate(atm_http_responses_total{job="atm-api"}[1m]))',start:now-300,end:now,step:15})
    ]);
    status("api", apiUp === 1);
    const workerHealthy = workerUp === 1 && heartbeat !== null && now - heartbeat < 60;
    status("worker", workerHealthy);
    status("database", workerHealthy);
    const queueFresh = workerHealthy && queueSample !== null && now - queueSample < 60;
    element("queued").textContent = queueFresh ? queued ?? "—" : "Unknown";
    element("failed").textContent = queueFresh ? failed ?? "—" : "Unknown";
    element("age").textContent = queueFresh && age !== null ? `${Math.round(age)} s` : "Unknown";
    element("latency").textContent = apiUp !== 1 ? "Unknown" : latency === null ? "Awaiting traffic" : `${Math.round(latency * 1000)} ms`;
    element("rate").textContent = apiUp !== 1 ? "Unknown" : rate === null ? "—" : `${rate.toFixed(1)} /s`;
    element("errors").textContent = apiUp !== 1 ? "Unknown" : errors === null ? "—" : Math.round(errors);
    const list = element("alerts"); list.replaceChildren();
    element("alert-count").textContent = `${alerts.alerts.length} ACTIVE`;
    for (const alert of alerts.alerts) {
      const item = document.createElement("li");
      const state = document.createElement("span"); state.className = "alert-state"; state.textContent = alert.state;
      item.append(state, document.createTextNode(alert.annotations.summary || alert.labels.alertname)); list.append(item);
    }
    if (!alerts.alerts.length) { const item = document.createElement("li"); item.textContent = "No operational alerts are active."; list.append(item); }
    const samples = history.result[0]?.values || [];
    const numbers = samples.map(sample => Number(sample[1])).map(n => Number.isFinite(n) ? Math.max(0,n) : 0);
    const max = Math.max(1, ...numbers);
    const points = numbers.map((n,i) => `${i*640/Math.max(1,numbers.length-1)},${170-n/max*150}`);
    const line = points.length ? `M${points.join(" L")}` : "";
    element("line").setAttribute("d", line);
    element("area").setAttribute("d", points.length ? `${line} L640,180 L0,180 Z` : "");
    element("connection").hidden = true;
    element("updated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
  } catch {
    element("connection").hidden = false;
    element("updated").textContent = "Connection interrupted";
    element("alert-count").textContent = "UNKNOWN";
    element("alerts").replaceChildren();
    const unavailable = document.createElement("li");
    unavailable.textContent = "Alert status is unavailable until monitoring reconnects.";
    element("alerts").append(unavailable);
    element("line").setAttribute("d", "");
    element("area").setAttribute("d", "");
    ["api", "worker", "database"].forEach(id => status(id, false));
    ["queued", "failed", "age", "latency", "rate", "errors"].forEach(id => { element(id).textContent = "Unknown"; });
  } finally { setTimeout(refresh, 15000); }
}
refresh();
