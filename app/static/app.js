function enable(id) { document.getElementById(id).classList.remove("disabled"); }

async function doPassword() {
  const password = document.getElementById("password").value;
  const res = await fetch("/api/auth/password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  if (res.ok) {
    document.getElementById("passwordStatus").textContent = "認証OK";
    enable("step-ms");
  } else {
    document.getElementById("passwordStatus").textContent = "パスワードが違います";
  }
}

async function startMsLogin() {
  const res = await fetch("/api/ms/login/start", { method: "POST" });
  if (!res.ok) {
    document.getElementById("msStatus").textContent = "開始に失敗しました";
    return;
  }
  const data = await res.json();
  const box = document.getElementById("deviceInfo");
  box.style.display = "block";
  box.innerHTML = `<a href="${data.verification_uri}" target="_blank">${data.verification_uri}</a> を開き、コード <b>${data.user_code}</b> を入力してください。`;

  document.getElementById("msStatus").textContent = "ログイン待機中...";
  const pollRes = await fetch("/api/ms/login/poll", { method: "POST" });
  if (pollRes.ok) {
    document.getElementById("msStatus").textContent = "ログイン成功";
    enable("step-explore");
  } else {
    document.getElementById("msStatus").textContent = "ログインに失敗しました";
  }
}

async function doExplore() {
  const site_url = document.getElementById("siteUrl").value;
  document.getElementById("exploreStatus").textContent = "探索中...(件数が多いと時間がかかります)";
  const res = await fetch("/api/explore", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ site_url }),
  });
  if (res.ok) {
    const data = await res.json();
    document.getElementById("exploreStatus").textContent =
      `${data.site_name}: ${data.file_count}件のファイルを検出`;
    enable("step-select");
  } else {
    const err = await res.json();
    document.getElementById("exploreStatus").textContent = "エラー: " + err.detail;
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
    if (data.selected.length > 0) enable("step-download");
  } else {
    const err = await res.json();
    list.innerHTML = `<li>エラー: ${err.detail}</li>`;
  }
}

function doDownload() {
  window.location.href = "/api/download";
}
