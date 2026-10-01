/**
 * NL Analytics — Databricks Lakebase
 * Frontend application logic with robust unexecutable query & rationale display.
 */

const state = {
  schema: null,
  history: JSON.parse(localStorage.getItem("nl-analytics-history") || "[]"),
};

const questionInput = document.getElementById("question-input");
const runBtn = document.getElementById("run-btn");
const resultsArea = document.getElementById("results-area");
const emptyState = document.getElementById("empty-state");
const schemaTree = document.getElementById("schema-tree");
const historyList = document.getElementById("history-list");
const refreshSchemaBtn = document.getElementById("refresh-schema");
const projectLabel = document.getElementById("project-label");

// ── Schema Introspection ─────────────────────────────────────────────

async function loadSchema(refresh = false) {
  schemaTree.innerHTML = '<p class="muted">Loading schema from Lakebase…</p>';
  try {
    const res = await fetch(`/api/schema?refresh=${refresh}`);
    if (!res.ok) throw new Error(await res.text());
    state.schema = await res.json();
    if (projectLabel && state.schema.project_id) {
      projectLabel.textContent = state.schema.project_id;
    }
    renderSchemaTree(state.schema);
  } catch (err) {
    schemaTree.innerHTML = `<p class="muted" style="color:var(--error)">Failed to load schema: ${escapeHtml(err.message)}</p>`;
  }
}

function renderSchemaTree(schema) {
  if (!schema.tables || !schema.tables.length) {
    schemaTree.innerHTML = '<p class="muted">No user tables found in Lakebase.</p>';
    return;
  }

  // Group by schema
  const grouped = {};
  schema.tables.forEach((t) => {
    if (!grouped[t.schema_name]) grouped[t.schema_name] = [];
    grouped[t.schema_name].push(t);
  });

  let html = "";
  for (const [schemaName, tables] of Object.entries(grouped)) {
    html += `<div style="margin-top: 10px; margin-bottom: 4px;">
      <span class="schema-badge">schema: ${escapeHtml(schemaName)}</span>
    </div>`;

    tables.forEach((t) => {
      const isMainTable = t.table_name.includes("ecommerce");
      html += `
        <div class="table-node">
          <button class="table-toggle ${isMainTable ? "open" : ""}" onclick="toggleTable(this)">
            <span class="arrow">▶</span>
            <span>${escapeHtml(t.table_name)}</span>
            <span style="margin-left:auto;color:var(--text-dim);font-size:10px">${t.columns.length} cols</span>
          </button>
          <div class="column-list ${isMainTable ? "open" : ""}">
            ${t.columns
              .map(
                (c) =>
                  `<div class="column-item"><span>${escapeHtml(c.name)}</span><span class="col-type">${escapeHtml(c.data_type)}</span></div>`
              )
              .join("")}
          </div>
        </div>`;
    });
  }

  schemaTree.innerHTML = html;
}

function toggleTable(btn) {
  btn.classList.toggle("open");
  const cols = btn.nextElementSibling;
  cols.classList.toggle("open");
}

// ── Query Execution ──────────────────────────────────────────────────

async function runQuery() {
  const question = questionInput.value.trim();
  if (!question) return;

  addToHistory(question);

  runBtn.disabled = true;
  const loadingCard = createLoadingCard(question);
  if (emptyState) emptyState.remove();
  resultsArea.prepend(loadingCard);

  try {
    const res = await fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });

    loadingCard.remove();
    const data = await res.json();

    if (res.status === 422) {
      // Proposed query cannot be executed with rationale
      resultsArea.prepend(createUnexecutableCard(data));
      return;
    }

    if (!res.ok) {
      // General error
      resultsArea.prepend(createErrorCard("Request Error", data.detail || data.error));
      return;
    }

    // Success response
    resultsArea.prepend(createResultCard(data));
  } catch (err) {
    loadingCard.remove();
    resultsArea.prepend(createErrorCard("Network error", err.message));
  } finally {
    runBtn.disabled = false;
  }
}

// ── UI Card Builders ─────────────────────────────────────────────────

function createLoadingCard(question) {
  const div = document.createElement("div");
  div.className = "result-card";
  div.innerHTML = `
    <div class="result-header">
      <div class="result-question">${escapeHtml(question)}</div>
    </div>
    <div class="loading-overlay">
      <div class="spinner"></div>
      <span>Translating natural language to Lakebase SQL and analyzing executability…</span>
    </div>`;
  return div;
}

function createUnexecutableCard(data) {
  const card = document.createElement("div");
  card.className = "unexecutable-card";

  const badgeText = (data.error_type || "CANNOT_EXECUTE").replace(/_/g, " ");

  let alternativeHtml = "";
  if (data.suggested_alternative) {
    alternativeHtml = `
      <div class="alternative-box">
        <strong>💡 Suggested Alternative:</strong>
        <a onclick="setQuery('${escapeJs(data.suggested_alternative)}')">${escapeHtml(data.suggested_alternative)}</a>
      </div>`;
  }

  let proposedSqlHtml = "";
  if (data.proposed_sql) {
    proposedSqlHtml = `
      <div class="sql-toggle" style="border:none; padding: 10px 0 0 0;">
        <button class="sql-toggle-btn" onclick="this.nextElementSibling.classList.toggle('open'); this.textContent = this.textContent.includes('Show') ? 'Hide Proposed Approximation ▴' : 'Show Proposed Approximation ▾'">Show Proposed Approximation ▾</button>
        <div class="sql-block">${escapeHtml(data.proposed_sql)}</div>
      </div>`;
  }

  card.innerHTML = `
    <div class="unexecutable-header">
      <span class="unexecutable-badge">${escapeHtml(badgeText)}</span>
      <div>
        <div class="result-question">${escapeHtml(data.question)}</div>
      </div>
    </div>
    <div class="unexecutable-body">
      <div class="rationale-title">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="10"></circle>
          <line x1="12" y1="8" x2="12" y2="12"></line>
          <line x1="12" y1="16" x2="12.01" y2="16"></line>
        </svg>
        Command Cannot Be Executed on Database
      </div>
      <div class="rationale-text">${escapeHtml(data.rationale)}</div>
      ${proposedSqlHtml}
      ${alternativeHtml}
    </div>`;

  return card;
}

function createErrorCard(title, detail) {
  const div = document.createElement("div");
  div.className = "unexecutable-card";
  div.innerHTML = `
    <div class="unexecutable-header">
      <span class="unexecutable-badge">ERROR</span>
      <div class="result-question">${escapeHtml(title)}</div>
    </div>
    <div class="unexecutable-body">
      <div class="rationale-text">${escapeHtml(detail || "An unexpected error occurred.")}</div>
    </div>`;
  return div;
}

function createResultCard(data) {
  const card = document.createElement("div");
  card.className = "result-card";
  const cardId = `card-${Date.now()}`;

  card.innerHTML = `
    <div class="result-header">
      <div class="result-question">${escapeHtml(data.question)}</div>
      <div class="result-explanation">${escapeHtml(data.explanation)}</div>
      <div class="result-meta">
        <span class="badge">${data.row_count.toLocaleString()} rows returned</span>
        <span>${data.columns.length} columns</span>
      </div>
    </div>
    <div class="sql-toggle">
      <button class="sql-toggle-btn" onclick="this.nextElementSibling.classList.toggle('open'); this.textContent = this.textContent.includes('Show') ? 'Hide SQL ▴' : 'Show SQL ▾'">Show SQL ▾</button>
      <div class="sql-block">${escapeHtml(data.sql)}</div>
    </div>
    <div class="view-tabs" data-card-id="${cardId}">
      <button class="view-tab active" onclick="switchView('${cardId}', 'table', this)">Table View</button>
      <button class="view-tab" onclick="switchView('${cardId}', 'chart', this)">Chart Visualization</button>
    </div>
    <div id="${cardId}-table" class="view-content">
      ${buildTable(data.columns, data.rows)}
    </div>
    <div id="${cardId}-chart" class="view-content" style="display:none">
      <div class="chart-container">
        <canvas id="${cardId}-canvas"></canvas>
      </div>
    </div>`;

  requestAnimationFrame(() => renderChart(cardId, data));
  return card;
}

function buildTable(columns, rows) {
  if (!rows || !rows.length) {
    return '<p style="padding:20px;color:var(--text-dim)">No rows returned for this query.</p>';
  }

  const header = columns.map((c) => `<th>${escapeHtml(c)}</th>`).join("");
  const body = rows
    .map(
      (row) =>
        "<tr>" +
        row
          .map((val) => {
            const display = val === null ? '<span style="color:var(--text-dim)">NULL</span>' : escapeHtml(String(val));
            return `<td>${display}</td>`;
          })
          .join("") +
        "</tr>"
    )
    .join("");

  return `<div class="table-container"><table class="data-table"><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function renderChart(cardId, data) {
  const canvas = document.getElementById(`${cardId}-canvas`);
  if (!canvas || !data.rows.length) return;

  let labelIdx = -1;
  let valueIdx = -1;

  for (let i = 0; i < data.columns.length; i++) {
    const val = data.rows[0][i];
    if (labelIdx === -1 && typeof val === "string") labelIdx = i;
    if (valueIdx === -1 && typeof val === "number") valueIdx = i;
  }

  if (labelIdx === -1) labelIdx = 0;
  if (valueIdx === -1) valueIdx = data.columns.length > 1 ? 1 : 0;

  const chartRows = data.rows.slice(0, 30);
  const labels = chartRows.map((r) => String(r[labelIdx] ?? ""));
  const values = chartRows.map((r) => {
    const v = r[valueIdx];
    return typeof v === "number" ? v : parseFloat(v) || 0;
  });

  const chartType = labels.length <= 8 ? "doughnut" : "bar";
  const colors = [
    "#6366f1", "#10b981", "#f59e0b", "#ef4444", "#38bdf8",
    "#ec4899", "#8b5cf6", "#f97316", "#14b8a6", "#64748b",
  ];

  new Chart(canvas, {
    type: chartType,
    data: {
      labels,
      datasets: [
        {
          label: data.columns[valueIdx] || "Metric",
          data: values,
          backgroundColor:
            chartType === "doughnut" ? colors.slice(0, labels.length) : "rgba(99, 102, 241, 0.7)",
          borderColor:
            chartType === "doughnut" ? colors.slice(0, labels.length) : "#6366f1",
          borderRadius: 4,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          display: chartType === "doughnut",
          position: "right",
          labels: { color: "#8e95ac", font: { size: 12 } },
        },
      },
      scales:
        chartType === "bar"
          ? {
              x: { ticks: { color: "#8e95ac", maxRotation: 45 }, grid: { color: "#282d42" } },
              y: { ticks: { color: "#8e95ac" }, grid: { color: "#282d42" } },
            }
          : undefined,
    },
  });
}

function switchView(cardId, view, btn) {
  const tabs = btn.parentElement.querySelectorAll(".view-tab");
  tabs.forEach((t) => t.classList.remove("active"));
  btn.classList.add("active");

  document.getElementById(`${cardId}-table`).style.display = view === "table" ? "" : "none";
  document.getElementById(`${cardId}-chart`).style.display = view === "chart" ? "" : "none";
}

// ── Helpers ──────────────────────────────────────────────────────────

function setQuery(text) {
  questionInput.value = text;
  autoResize(questionInput);
  runQuery();
}

function autoResize(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 120) + "px";
}

function addToHistory(q) {
  state.history = [q, ...state.history.filter((x) => x !== q)].slice(0, 30);
  localStorage.setItem("nl-analytics-history", JSON.stringify(state.history));
  renderHistory();
}

function renderHistory() {
  historyList.innerHTML = state.history
    .slice(0, 15)
    .map((q) => `<li onclick="setQuery('${escapeJs(q)}')">${escapeHtml(q)}</li>`)
    .join("");
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s || "";
  return div.innerHTML;
}

function escapeJs(s) {
  return (s || "").replace(/'/g, "\\'").replace(/"/g, '\\"');
}

// ── Listeners ────────────────────────────────────────────────────────

runBtn.addEventListener("click", runQuery);

questionInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    runQuery();
  }
});

questionInput.addEventListener("input", () => autoResize(questionInput));
refreshSchemaBtn.addEventListener("click", () => loadSchema(true));

document.querySelectorAll(".chip[data-question]").forEach((chip) => {
  chip.addEventListener("click", () => setQuery(chip.dataset.question));
});

// Init
renderHistory();
loadSchema();
