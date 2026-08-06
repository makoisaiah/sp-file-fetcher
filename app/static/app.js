function enable(id) { document.getElementById(id).classList.remove("disabled"); }
function disable(id) { document.getElementById(id).classList.add("disabled"); }

async function checkStatus() {
  const res = await fetch("/api/status");
  const data = await res.json();
  const msg = document.getElementById("statusMsg");
  if (data.logged_in) {
    msg.textContent = "ログイン済みです。探索に進めます。";
    msg.className = "status";
    enable("step-explore");
  } else {
    msg.innerHTML = `未ログイン、または期限切れです。<br>ターミナルで <code>python scripts/login.py</code> を実行してログインしてから、もう一度「確認する」を押してください。`;
    msg.className = "status warn";
    disable("step-explore");
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
    enable("step-select");
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
    enable("step-select");
  } else {
    const err = await res.json();
    statusEl.textContent = "エラー: " + err.detail;
  }
}

async function doSelect() {
  const instruction = document.getElementById("instruction").value;
  const res = await fetch("/api/select", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ instruction }),
  });
  const list = document.getElementById("selectedList");
  list.innerHTML = "";
  if (res.ok) {
    const data = await res.json();
    data.selected.forEach((f) => {
      const li = document.createElement("li");
      li.textContent = f.path;
      list.appendChild(li);
    });
    const info = document.getElementById("selectInfo");
    const elementsText = data.elements
      .map((e) => `${e.category}[${e.keywords.join("/")}]`)
      .join(" AND ") || "(要素なし)";
    const fallbackNote = data.fell_back_to_full ? " ※AND一致なし、全件を対象に選定しました" : "";
    info.innerHTML = `要素分解: ${elementsText}<br>フォルダ一致: ${data.matched_count}件 → 候補: ${data.candidate_count}件${fallbackNote}`;
    if (data.selected.length > 0) enable("step-download");
  } else {
    const err = await res.json();
    list.innerHTML = `<li>エラー: ${err.detail}</li>`;
  }
}

function doDownload() {
  window.location.href = "/api/download";
}
