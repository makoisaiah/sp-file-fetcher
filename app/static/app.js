const FORM_STORAGE_KEY = "sp_fetcher_last_query";

let currentFacets = { "分野": [], "担当科": [], "ノイズ": [] };
let advancedShown = false;
let loginPollTimer = null;

function show(id) { document.getElementById(id).classList.remove("hidden"); }
function hide(id) { document.getElementById(id).classList.add("hidden"); }
function enable(id) { document.getElementById(id).classList.remove("disabled"); }
function disable(id) { document.getElementById(id).classList.add("disabled"); }

function loadSavedSelections() {
  try {
    return JSON.parse(localStorage.getItem(FORM_STORAGE_KEY) || "{}");
  } catch (e) {
    return {};
  }
}

function applyFixedFieldDefaults() {
  const saved = loadSavedSelections();
  if (saved.yearsBack !== undefined) document.getElementById("yearsBack").value = saved.yearsBack;
  if (saved.semester !== undefined) document.getElementById("semester").value = saved.semester;
  if (saved.fileType !== undefined) document.getElementById("fileType").value = saved.fileType;
}

// ---------- 起動時の自動フロー ----------

window.addEventListener("DOMContentLoaded", async () => {
  applyFixedFieldDefaults();
  await checkStatusAndProceed();
});

async function checkStatusAndProceed() {
  const res = await fetch("/api/status");
  const data = await res.json();

  if (data.logged_in) {
    hide("step-login");
    stopLoginPolling();
    // ファイル構造がまだ準備できていなければオンボーディングを見せる
    if (document.getElementById("step-select").classList.contains("hidden") &&
        !document.getElementById("step-onboarding").dataset.done) {
      show("step-onboarding");
    }
  } else {
    show("step-login");
    const statusEl = document.getElementById("loginStatus");
    if (data.login_in_progress) {
      statusEl.textContent = "ブラウザでログインを待っています...";
      startLoginPolling();
    } else if (data.login_error) {
      statusEl.textContent = data.login_error;
      statusEl.className = "status warn";
    } else {
      statusEl.textContent = "";
    }
  }
}

async function startLogin() {
  const statusEl = document.getElementById("loginStatus");
  document.getElementById("loginStartBtn").disabled = true;
  statusEl.className = "status";
  statusEl.textContent = "ブラウザを開いています...";

  const res = await fetch("/api/login/start", { method: "POST" });
  const data = await res.json();
  if (data.started) {
    statusEl.textContent = "ブラウザでMicrosoftアカウントにログインしてください(自動検知します)";
    startLoginPolling();
  } else {
    statusEl.textContent = data.message || "開始できませんでした";
    document.getElementById("loginStartBtn").disabled = false;
  }
}

function startLoginPolling() {
  if (loginPollTimer) return;
  loginPollTimer = setInterval(checkStatusAndProceed, 3000);
}

function stopLoginPolling() {
  if (loginPollTimer) {
    clearInterval(loginPollTimer);
    loginPollTimer = null;
  }
  document.getElementById("loginStartBtn").disabled = false;
}

// ---------- オンボーディング(まとめて自動実行 or 手動) ----------

async function runOnboarding() {
  const statusEl = document.getElementById("onboardingStatus");
  statusEl.className = "status";
  statusEl.textContent = "ファイル構造を探索しています...";

  const exploreRes = await fetch("/api/explore", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode: "auto" }),
  });
  if (!exploreRes.ok) {
    const err = await exploreRes.json();
    statusEl.textContent = "探索でエラー: " + err.detail;
    statusEl.className = "status warn";
    return;
  }
  const exploreData = await exploreRes.json();
  statusEl.textContent = `${exploreData.source}: ${exploreData.file_count}件。検索メニューを準備しています...`;

  const facetsRes = await fetch("/api/facets/generate", { method: "POST" });
  if (!facetsRes.ok) {
    const err = await facetsRes.json();
    statusEl.textContent = "検索メニュー生成でエラー: " + err.detail;
    statusEl.className = "status warn";
    return;
  }
  const facetsData = await facetsRes.json();
  currentFacets = facetsData.facets;
  renderFacetChecklists();

  statusEl.textContent = "準備ができました。下の条件で検索できます。";
  finishOnboarding();
}

function skipOnboarding() {
  finishOnboarding();
  advancedShown = true;
  show("step-explore");
  show("step-facets");
  document.getElementById("advancedToggleLink").textContent = "詳細設定を隠す";
}

function finishOnboarding() {
  document.getElementById("step-onboarding").dataset.done = "1";
  hide("step-onboarding");
  show("advancedToggleWrap");
  show("step-select");
  show("step-download");
  // step-selectはファイル未探索/検索メニュー未生成の状態でも表示だけはしておく
  // (手動パスの場合はexplore/facets完了までは検索してもヒットしないだけ)
}

function toggleAdvanced() {
  advancedShown = !advancedShown;
  const link = document.getElementById("advancedToggleLink");
  if (advancedShown) {
    show("step-explore");
    show("step-facets");
    link.textContent = "詳細設定を隠す";
  } else {
    hide("step-explore");
    hide("step-facets");
    link.textContent = "詳細設定を表示";
  }
}

// ---------- 探索 ----------

async function afterExploreSuccess() {
  const res = await fetch("/api/facets");
  if (res.ok) {
    const data = await res.json();
    currentFacets = data.facets;
    renderFacetChecklists();
  }
}

async function doExplore(mode) {
  const statusEl = document.getElementById("exploreStatus");
  statusEl.textContent = "探索中...(全体探索は件数が多いと時間がかかります)";
  const res = await fetch("/api/explore", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode }),
  });
  if (res.ok) {
    const data = await res.json();
    statusEl.textContent = `${data.source}: ${data.file_count}件のファイル`;
    await afterExploreSuccess();
  } else {
    const err = await res.json();
    statusEl.textContent = "エラー: " + err.detail;
  }
}

async function doExploreSubfolder() {
  const path = document.getElementById("subfolderPath").value.trim();
  if (!path) return;
  const statusEl = document.getElementById("exploreStatus");
  statusEl.textContent = "部分再探索中...";
  const res = await fetch("/api/explore", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode: "subfolder", path }),
  });
  if (res.ok) {
    const data = await res.json();
    statusEl.textContent = `${data.source}: 合計${data.file_count}件のファイル`;
    await afterExploreSuccess();
  } else {
    const err = await res.json();
    statusEl.textContent = "エラー: " + err.detail;
  }
}

// ---------- 検索メニュー(ファセット) ----------

async function generateFacets() {
  const statusEl = document.getElementById("facetsStatus");
  statusEl.textContent = "候補を抽出し、AIに仕分けさせています...(数十秒かかることがあります)";
  const res = await fetch("/api/facets/generate", { method: "POST" });
  if (res.ok) {
    const data = await res.json();
    currentFacets = data.facets;
    statusEl.textContent = `候補${data.candidate_count}件から、分野${currentFacets["分野"].length}件・担当科${currentFacets["担当科"].length}件を抽出しました`;
    renderFacetChecklists();
  } else {
    const err = await res.json();
    statusEl.textContent = "エラー: " + err.detail;
  }
}

function toggleFacetEditor() {
  const box = document.getElementById("facetEditorBox");
  if (box.style.display === "none") {
    document.getElementById("facetEditorText").value = JSON.stringify(currentFacets, null, 2);
    box.style.display = "block";
  } else {
    box.style.display = "none";
  }
}

async function saveFacetEdits() {
  const statusEl = document.getElementById("facetsStatus");
  let parsed;
  try {
    parsed = JSON.parse(document.getElementById("facetEditorText").value);
  } catch (e) {
    statusEl.textContent = "JSONの形式が正しくありません: " + e.message;
    return;
  }
  const res = await fetch("/api/facets", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(parsed),
  });
  if (res.ok) {
    currentFacets = parsed;
    statusEl.textContent = "保存しました";
    renderFacetChecklists();
    document.getElementById("facetEditorBox").style.display = "none";
  } else {
    const err = await res.json();
    statusEl.textContent = "エラー: " + err.detail;
  }
}

function renderFacetChecklists() {
  const saved = loadSavedSelections();
  const savedSelections = saved.selections || {};

  renderOneChecklist("organChecks", "分野", currentFacets["分野"] || [], savedSelections["分野"] || []);
  renderOneChecklist("departmentChecks", "担当科", currentFacets["担当科"] || [], savedSelections["担当科"] || []);
}

function renderOneChecklist(containerId, category, options, savedLabels) {
  const container = document.getElementById(containerId);
  container.innerHTML = "";
  if (options.length === 0) {
    container.innerHTML = `<span style="font-size:12px; color:#94a3b8;">(未生成。「検索メニューの準備」で生成してください)</span>`;
    return;
  }
  options.forEach((opt) => {
    const label = document.createElement("label");
    const checked = savedLabels.includes(opt.label) ? "checked" : "";
    label.innerHTML = `<input type="checkbox" data-category="${category}" value="${opt.label}" ${checked}> ${opt.label}`;
    container.appendChild(label);
  });
}

function getCheckedLabels(containerId) {
  return Array.from(document.querySelectorAll(`#${containerId} input:checked`)).map((el) => el.value);
}

// ---------- 選定 ----------

async function doSelect() {
  const fields = {
    years_back: parseInt(document.getElementById("yearsBack").value || "0", 10),
    semester: document.getElementById("semester").value,
    file_type: document.getElementById("fileType").value,
    selections: {
      "分野": getCheckedLabels("organChecks"),
      "担当科": getCheckedLabels("departmentChecks"),
    },
  };

  localStorage.setItem(FORM_STORAGE_KEY, JSON.stringify({
    yearsBack: fields.years_back,
    semester: fields.semester,
    fileType: fields.file_type,
    selections: fields.selections,
  }));

  const info = document.getElementById("selectInfo");
  const list = document.getElementById("selectedList");
  info.textContent = "検索中...";
  list.innerHTML = "";

  const res = await fetch("/api/select", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fields),
  });

  if (res.ok) {
    const data = await res.json();
    data.selected.forEach((f) => {
      const li = document.createElement("li");
      li.textContent = f.path;
      list.appendChild(li);
    });
    const fallbackNote = data.fell_back_to_full ? " ※条件に一致するファイルなし、全件を対象に選定しました" : "";
    info.innerHTML = `条件: ${data.instruction}<br>条件一致: ${data.matched_count}件 → 候補: ${data.candidate_count}件${fallbackNote}`;
    document.getElementById("downloadBtn").disabled = data.selected.length === 0;
  } else {
    const err = await res.json();
    info.textContent = "";
    list.innerHTML = `<li>エラー: ${err.detail}</li>`;
  }
}

function doDownload() {
  window.location.href = "/api/download";
}
