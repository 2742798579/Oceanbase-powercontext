const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  queue: [],
  index: 0,
  revealed: false,
  graded: new Set(),
};

async function api(path, options) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${text}`);
  }
  return res.json();
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function highlight(text, terms) {
  let html = esc(text);
  const list = (terms || []).filter((t) => t && t.length >= 2).sort((a, b) => b.length - a.length);
  for (const t of list) {
    const safe = esc(t).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    html = html.replace(new RegExp(safe, "g"), (m) => `<mark>${m}</mark>`);
  }
  return html;
}

// ---------- 状态 ----------

async function loadHealth() {
  try {
    const h = await api("/api/health");
    const s = h.seekdb.stats;
    $("#pillSeekdb").className = "pill pill-ok";
    $("#pillSeekdb").textContent = `seekdb 就绪 · ${s.chunks} 块 / ${s.docs} 篇`;
    const pc = h.powercontext;
    if (pc.ok) {
      $("#pillPc").className = "pill pill-ok";
      $("#pillPc").textContent = "PowerContext 已连接";
    } else {
      $("#pillPc").className = "pill pill-bad";
      $("#pillPc").textContent = "PowerContext 未连接（记忆降级）";
      $("#pillPc").title = pc.error || "";
    }
  } catch (e) {
    $("#pillSeekdb").className = "pill pill-bad";
    $("#pillSeekdb").textContent = "服务异常";
  }
}

async function loadLibrary() {
  const data = await api("/api/library");
  const box = $("#docList");
  if (!data.documents.length) {
    box.innerHTML = `<p class="muted">还没有资料，点右上角「重新导入示例资料」。</p>`;
  } else {
    box.innerHTML = data.documents
      .map(
        (d) => `<div class="doc-item">
          <div><div>${esc(d.doc_title)}</div>
          <div class="meta">${esc(d.course)} · ${d.chunks} 块 · ${esc(d.source_path)}</div></div>
          <div class="meta">${esc(d.tags || "")}</div>
        </div>`
      )
      .join("");
  }
  const courses = [...new Set(data.documents.map((d) => d.course))];
  const sel = $("#courseFilter");
  sel.innerHTML = `<option value="">全部科目</option>` + courses.map((c) => `<option value="${esc(c)}">${esc(c)}</option>`).join("");
}

// ---------- 检索 ----------

async function doSearch(query, course) {
  const box = $("#searchResults");
  box.innerHTML = `<div class="panel"><span class="spinner"></span> 正在用 seekdb 做混合检索…</div>`;
  try {
    const data = await api("/api/search", {
      method: "POST",
      body: JSON.stringify({ query, k: 6, course: course || null }),
    });

    const mem = data.memory;
    const banner = $("#memoryBanner");
    banner.classList.remove("hidden");
    if (mem.status === "ready") {
      const d = mem.directives;
      const applied = [];
      if (d.preferred_course) applied.push(`优先科目 <b>${esc(d.preferred_course)}</b>`);
      if (d.weak_topics?.length) applied.push(`薄弱点 <b>${esc(d.weak_topics.join("、"))}</b>`);
      const lines = (mem.entries || []).map((e) => `· ${esc(e.text)}`).join("<br/>");
      banner.innerHTML =
        `PowerContext 读到 <b>${(mem.entries || []).length}</b> 条长期记忆：<br/>${lines}` +
        (applied.length ? `<br/>已用于本次排序加权：${applied.join("；")}` : `<br/>（还没有可用的偏好，本次不加权）`);
    } else if (mem.status === "empty") {
      banner.innerHTML = `PowerContext 已连接，但这个 Scope 还没有任何记忆。去「学习档案」写一条偏好试试。`;
    } else {
      banner.innerHTML = `记忆层不可用，本次仅用 seekdb 检索结果。`;
    }

    if (!data.results.length) {
      box.innerHTML = `<div class="panel muted">没有命中。换个说法，或先在资料库导入资料。</div>`;
      return;
    }

    const terms = [query, ...(mem.directives?.weak_topics || [])];
    box.innerHTML = data.results
      .map((r, i) => {
        const scores = [];
        if (r.vec_rank) scores.push(`<span class="score score-vec">向量 #${r.vec_rank} · d=${r.dist?.toFixed(3)}</span>`);
        if (r.ft_rank) scores.push(`<span class="score score-ft">全文 #${r.ft_rank} · s=${r.ft_score?.toFixed(2)}</span>`);
        (r.memory_reasons || []).forEach((m) => scores.push(`<span class="score score-mem">${esc(m)}</span>`));
        scores.push(`<span class="score score-final">RRF ${r.score?.toFixed(4)}</span>`);
        return `<div class="result">
          <div class="result-top">
            <div class="result-title">${i + 1}. ${esc(r.doc_title)} <span class="muted">/ ${esc(r.topic)}</span></div>
            <div class="result-src">${esc(r.course)} · ${esc(r.source_path)}</div>
          </div>
          <div class="result-body">${highlight(r.content, terms)}</div>
          <div class="scores">${scores.join("")}</div>
        </div>`;
      })
      .join("");

    const dbg = data.debug;
    box.innerHTML += `<details class="panel"><summary class="muted">查看实际执行的两条 seekdb SQL</summary>
      <pre class="code-block">-- 向量召回（HNSW 近似最近邻 + 标量过滤）
${esc(dbg.vector_sql)}

-- 全文召回（ik 分词 MATCH AGAINST）
${esc(dbg.fulltext_sql)}

-- 一条 SQL 同时用上向量 + 全文 + 标量条件（对照用，命中 ${dbg.single_sql_hits} 条）
${esc(dbg.single_sql)}</pre></details>`;
  } catch (e) {
    box.innerHTML = `<div class="panel">检索失败：${esc(e.message)}</div>`;
  }
}

// ---------- 复习教练 ----------

async function loadQueue() {
  const data = await api("/api/coach/queue");
  state.queue = data.cards;
  state.index = 0;
  state.revealed = false;
  state.graded = new Set();

  const d = data.memory.directives || {};
  const applied = data.memory.applied || {};
  $("#coachSub").textContent =
    `共 ${data.cards.length} 张卡 · 上限 ${applied.limit}${applied.course ? ` · 优先科目 ${applied.course}` : ""}` +
    (data.memory.status === "ready" ? " · 参数来自 PowerContext 记忆" : " · 记忆未连接，用默认参数");

  $("#queueList").innerHTML = data.cards
    .map((c, i) => `<div class="queue-item" data-i="${i}">${i + 1}. ${esc(c.topic)} <span class="muted">· ${esc(c.course)}</span></div>`)
    .join("");
  renderCard();
  await loadWeak();
}

async function loadWeak() {
  const p = await api("/api/profile");
  const items = p.weak_topics || [];
  $("#weakList").innerHTML = items.length
    ? items.map((w) => `<div class="weak-item"><span>${esc(w.topic)} <span class="muted">${esc(w.course)}</span></span><span>${w.miss}/${w.miss + w.hit}</span></div>`).join("")
    : `<p class="muted">还没有复习记录。</p>`;
  $("#profileWeak").innerHTML = items.length
    ? `<div class="weak-list">${items
        .map((w) => `<div class="weak-item"><span>${esc(w.topic)} <span class="muted">${esc(w.course)}</span></span><span>未掌握 ${w.miss} / 掌握 ${w.hit}</span></div>`)
        .join("")}</div>`
    : `<p class="muted">还没有复习记录，去「复习教练」标记几张卡片。</p>`;
}

function renderCard() {
  const area = $("#cardArea");
  const card = state.queue[state.index];
  if (!card) {
    area.innerHTML = `<p class="muted">队列已空。去资料库导入更多资料，或点「重新排队」。</p>`;
    return;
  }
  const done = state.graded.has(card.chunk_id);
  area.innerHTML = `
    <div class="card-progress">第 ${state.index + 1} / ${state.queue.length} 张 · ${esc(card.course)} · ${esc(card.doc_title)}</div>
    <div class="card-front">${esc(card.front).replace(/＿＿＿＿/g, '<span class="blank">＿＿＿＿</span>')}</div>
    ${
      state.revealed
        ? `<div class="card-back"><b>答案</b><br/>${esc(card.back)}<div class="card-meta">出处：${esc(card.source_path)} · 主题 ${esc(card.topic)}</div></div>`
        : ""
    }
    <div class="card-btns">
      ${state.revealed ? "" : `<button class="btn btn-primary" id="revealBtn">显示答案</button>`}
      ${
        state.revealed && !done
          ? `<button class="btn btn-good" id="goodBtn">记住了</button>
             <button class="btn btn-bad" id="badBtn">还不熟</button>`
          : ""
      }
      ${done ? `<span class="muted">已记录</span>` : ""}
      <button class="btn" id="skipBtn">下一张</button>
    </div>`;

  $("#revealBtn")?.addEventListener("click", () => {
    state.revealed = true;
    renderCard();
  });
  $("#skipBtn")?.addEventListener("click", () => {
    state.index = (state.index + 1) % state.queue.length;
    state.revealed = false;
    renderCard();
  });
  $("#goodBtn")?.addEventListener("click", () => gradeCard(card.chunk_id, true));
  $("#badBtn")?.addEventListener("click", () => gradeCard(card.chunk_id, false));
}

async function gradeCard(chunkId, known) {
  await api("/api/coach/grade", { method: "POST", body: JSON.stringify({ chunk_id: chunkId, known }) });
  state.graded.add(chunkId);
  await loadWeak();
  await loadHealth();
  setTimeout(() => {
    state.index = (state.index + 1) % state.queue.length;
    state.revealed = false;
    renderCard();
  }, 260);
}

// ---------- 学习档案 ----------

async function loadProfile() {
  const p = await api("/api/profile");
  const entries = p.entries || [];
  const head = `长期记忆（active）共 ${entries.length} 条：\n` +
    (entries.length ? entries.map((e, i) => `  ${i + 1}. [${e.kind}] ${e.text}`).join("\n") : "  （空）");
  const prepared = p.memory?.content || "(空)";
  $("#memoryRaw").textContent =
    `${head}\n\n===== POST /v1/context/prepare 的返回（字节预算内召回） =====\n` +
    `status: ${p.memory?.status}\nscope_id: ${p.memory?.scope_id || "(未创建)"}\n\n${prepared}`;
  await loadWeak();
}

// ---------- 绑定 ----------

$$(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    $$(".tab").forEach((t) => t.classList.remove("active"));
    $$(".view").forEach((v) => v.classList.remove("active"));
    tab.classList.add("active");
    $(`#view-${tab.dataset.view}`).classList.add("active");
    if (tab.dataset.view === "profile") loadProfile();
    if (tab.dataset.view === "coach") loadQueue();
    if (tab.dataset.view === "library") loadLibrary();
  });
});

$("#searchBtn").addEventListener("click", () => {
  const q = $("#queryInput").value.trim();
  if (q) doSearch(q, $("#courseFilter").value);
});
$("#queryInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("#searchBtn").click();
});
$$(".chip").forEach((c) =>
  c.addEventListener("click", () => {
    $("#queryInput").value = c.dataset.q;
    doSearch(c.dataset.q, $("#courseFilter").value);
  })
);

$("#seedBtn").addEventListener("click", async () => {
  const r = await api("/api/seed", { method: "POST" });
  await loadLibrary();
  await loadHealth();
  alert(`已导入 ${r.imported.length} 篇资料，共 ${r.stats.chunks} 个知识块。`);
});

$("#ingestBtn").addEventListener("click", async () => {
  const title = $("#ingTitle").value.trim();
  const text = $("#ingText").value.trim();
  if (!title || !text) {
    $("#ingestMsg").textContent = "标题和正文都要填";
    return;
  }
  try {
    const r = await api("/api/ingest", {
      method: "POST",
      body: JSON.stringify({ title, course: $("#ingCourse").value.trim() || "未分类", tags: $("#ingTags").value.trim(), text }),
    });
    $("#ingestMsg").textContent = `已写入 ${r.inserted} 个知识块`;
    $("#ingTitle").value = "";
    $("#ingText").value = "";
    await loadLibrary();
    await loadHealth();
  } catch (e) {
    $("#ingestMsg").textContent = "导入失败：" + e.message;
  }
});

$("#prefBtn").addEventListener("click", async () => {
  const course = $("#prefCourse").value.trim() || null;
  const limit = parseInt($("#prefLimit").value, 10) || null;
  if (!course && !limit) {
    $("#prefMsg").textContent = "至少填一项";
    return;
  }
  try {
    const r = await api("/api/profile/preference", { method: "POST", body: JSON.stringify({ course, card_limit: limit }) });
    $("#prefMsg").textContent = "已写入 PowerContext：" + r.memory_text;
    await loadProfile();
  } catch (e) {
    $("#prefMsg").textContent = "写入失败：" + e.message;
  }
});

$("#refreshProfile").addEventListener("click", loadProfile);
$("#reloadQueue").addEventListener("click", loadQueue);

(async function init() {
  await loadHealth();
  await loadLibrary();
})();
