// 軍人将棋 クライアント（画面側）
// サーバーと通信し、盤面の描画・駒の配置・移動を担当します。

"use strict";

// ---- セッション情報（自分がどの部屋のどちら側か）----
// sessionStorage に保存する（タブ／端末ごとに独立して持つ）。
// ★重要：ここを localStorage（ブラウザ全体で共有）にすると、同じブラウザで2人が開いたとき
//   “正体”を共有してしまい、2人目の操作で1人目の席が退室させられる不具合が起きる。
//   タブ／端末ごとに独立させることで、2人が絶対に正体を共有しないようにする。
//   （1台で2人試すときは、タブを2つ開けばそれぞれ別プレイヤーになる）
// プライベートモード等で使えなくても落ちないよう、必ず try で包む。
const SKEY = "gunjin";
function loadSession() {
  try { return JSON.parse(sessionStorage.getItem(SKEY) || "null"); }
  catch (e) { return null; }
}
function saveSession(s) {
  try { sessionStorage.setItem(SKEY, JSON.stringify(s)); } catch (e) { /* 使えなくても続行 */ }
}
function clearSession() {
  try { sessionStorage.removeItem(SKEY); } catch (e) { /* 無視 */ }
}

let session = loadSession();   // {code, token, seat}
let state = null;              // サーバーから来た最新の状態
let lastVersion = -1;
let failStreak = 0;            // 状態確認が連続で失敗した回数（一瞬の通信の揺れで即退出しないため）

// 配置フェーズ用の一時データ
let placement = [];            // [{row, col, kind}]  ※実座標
let selectedKind = null;       // 駒置き場で選んでいる駒
let selectedPlace = null;      // 盤上で持ち上げた駒 {row, col}

// 対戦フェーズ用
let selectedCell = null;       // {r, c} 実座標

const $ = (id) => document.getElementById(id);

// ---- サーバーへの通信 ----
async function api(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || "通信に失敗しました。");
    err.code = res.status;
    throw err;
  }
  return data;
}

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => t.classList.add("hidden"), 2600);
}

// ---- 座標変換：自分の駒がいつも手前（下側）に見えるようにする ----
// A軍は盤の下側なのでそのまま。B軍は180度回して表示する。
function toReal(dr, dc) {
  if (session.seat === "A") return [dr, dc];
  return [state.rows - 1 - dr, state.cols - 1 - dc];
}
function toDisplay(rr, rc) {
  if (session.seat === "A") return [rr, rc];
  return [state.rows - 1 - rr, state.cols - 1 - rc];
}

// ===========================================================================
// トップ画面の操作
// ===========================================================================
$("btn-create").addEventListener("click", async () => {
  const btn = $("btn-create");
  btn.disabled = true;
  try {
    const r = await api("/api/create", {});
    session = { code: r.code, token: r.token, seat: r.seat };
    saveSession(session);
    lastVersion = -1;
    await refresh();          // 成功したら待ち画面へ。ボタンは押せないまま（二重作成防止）。
  } catch (e) {
    toast(e.message);
    btn.disabled = false;     // 失敗したときだけ押し直せる
  }
});

$("btn-join").addEventListener("click", async () => {
  const code = $("join-code").value.trim().toUpperCase();
  if (code.length !== 4) { toast("あいことばは4文字です。"); return; }
  const btn = $("btn-join");
  btn.disabled = true;
  try {
    const r = await api("/api/join", { code });
    session = { code, token: r.token, seat: r.seat };
    saveSession(session);
    lastVersion = -1;
    await refresh();          // 成功したら配置画面へ。ボタンは押せないまま（二重入室で「満員」になるのを防ぐ）。
  } catch (e) {
    toast(e.message);
    btn.disabled = false;     // 「満員」等で失敗したときだけ押し直せる
  }
});

// 「最初に戻る」ボタン（あいことば画面・配置画面）→ その場で部屋を出る
$("btn-leave-wait").addEventListener("click", leaveRoom);
$("btn-leave-setup").addEventListener("click", leaveRoom);

// ===========================================================================
// 状態の取得（1秒ごとに最新を見に行く。ポーリングは常時オンで止まらない）
// ===========================================================================
let refreshing = false;   // 前回の確認が終わる前に重ねて走らないようにする
async function refresh() {
  if (!session || refreshing) return;   // 部屋にいなければ何もしない
  refreshing = true;
  const usedToken = session.token;      // この確認が使ったトークン（返事が来る頃の取り違え防止）
  try {
    const s = await api("/api/state", { code: session.code, token: usedToken });
    failStreak = 0;                     // 1回でも成功したら失敗カウントはリセット
    state = s;
    if (state.version !== lastVersion) {
      lastVersion = state.version;
      try {
        render();
      } catch (e) {
        console.error("描画エラー:", e);
        toast("画面の描画でエラー: " + (e && e.message ? e.message : e));
      }
    }
  } catch (e) {
    // 返事が返る頃にセッションが変わっていたら（部屋を作り直した等）、
    // その“古い返事”で今の状態を壊さない。
    if (!session || session.token !== usedToken) return;
    // 部屋が無い/席が確認できない。ただし一瞬の通信の揺れやアプリ切り替え直後にも
    // 起きうるので、1回では退出しない。数回連続で初めて諦める。
    if (e.code === 403 || e.code === 404) {
      failStreak++;
      if (failStreak >= 6) {            // 約6秒つながらなければ本当に切れたとみなす
        backToTop(false);               // 相手を巻き込まないよう、サーバーへ退室は伝えない
        toast("接続が切れたため、最初の画面に戻りました。");
      }
    }
    // それ以外（一時的な通信エラー等）は黙ってスルーし、次の確認に任せる
  } finally {
    refreshing = false;
  }
}
// 最初のトップ画面に戻す。tellServer=true のときだけサーバーに退室を伝える。
//   ・ユーザーが自分で「最初に戻る」を押したとき → true（席をすぐ空ける）
//   ・通信不調で諦めて戻るとき → false（部屋は生かしたまま自分だけ戻る。復帰の余地を残す）
function backToTop(tellServer) {
  if (tellServer && session) {
    const s = session;
    api("/api/leave", { code: s.code, token: s.token }).catch(() => {});
  }
  clearSession();
  session = null;
  state = null;
  lastVersion = -1;
  failStreak = 0;
  placement = [];
  selectedKind = null;
  selectedPlace = null;
  selectedCell = null;
  $("join-code").value = "";
  $("btn-create").disabled = false;   // トップに戻ったら作成・入室を押せるように戻す
  $("btn-join").disabled = false;
  hide("board-wrap");
  hide("log-wrap");
  showScreen("screen-top");
}
// ユーザーが自分で部屋を出るとき（ボタン）。誤タップで対戦を壊さないよう確認を挟む。
// ここでだけサーバーに退室を伝える（席をすぐ空ける）。自動復帰では退室を送らない。
function leaveRoom() {
  if (!confirm("この部屋から抜けて、最初の画面に戻りますか？")) return;
  backToTop(true);
}

// ===========================================================================
// 画面の切り替えと描画
// ===========================================================================
function showScreen(id) {
  for (const s of document.querySelectorAll(".screen")) s.classList.add("hidden");
  if (id) $(id).classList.remove("hidden");
}

function render() {
  if (!state) return;
  const phase = state.phase;

  if (phase === "waiting") {
    showScreen("screen-wait");
    $("room-code").textContent = state.code;
    hide("board-wrap"); hide("log-wrap"); hide("rules-ref");
    return;
  }

  if (phase === "setup") {
    showScreen("screen-setup");
    show("board-wrap"); show("log-wrap"); show("rules-ref");
    renderLog();
    renderTray();
    renderBoard();
    updateSetupStatus();
    $("btn-rematch").classList.add("hidden");
    return;
  }

  if (phase === "play" || phase === "over") {
    showScreen("screen-play");
    show("board-wrap"); show("log-wrap"); show("rules-ref");
    renderTurnBanner();
    renderBoard();
    renderLog();
    $("btn-rematch").classList.toggle("hidden", phase !== "over");
    return;
  }
}

function show(id) { $(id).classList.remove("hidden"); }
function hide(id) { $(id).classList.add("hidden"); }

// ---- 手番・勝敗の表示 ----
function renderTurnBanner() {
  const b = $("turn-banner");
  const battle = state.last_battle
    ? `<div class="last-battle">⚔ 直前の戦闘：${state.last_battle}</div>` : "";
  if (state.phase === "over") {
    const win = state.winner === session.seat;
    b.className = "banner " + (win ? "your-turn" : "wait-turn");
    b.innerHTML = `<div class="result ${win ? "win" : "lose"}">` +
      (win ? "🎉 あなたの勝ち！" : "…あなたの負け") + "</div>" + battle;
    return;
  }
  const my = state.turn === session.seat;
  b.className = "banner " + (my ? "your-turn" : "wait-turn");
  b.innerHTML = `<div>${my ? "▶ あなたの手番です" : "相手の手番を待っています…"}</div>` + battle;
}

// ===========================================================================
// 配置フェーズ
// ===========================================================================
function remainingCounts() {
  // 置き場に残っている駒の枚数
  const remain = Object.assign({}, state.piece_set);
  for (const p of placement) remain[p.kind] = (remain[p.kind] || 0) - 1;
  return remain;
}

function renderTray() {
  const remain = remainingCounts();
  const tray = $("tray");
  tray.innerHTML = "";
  for (const kind of Object.keys(state.piece_set)) {
    const n = remain[kind];
    const btn = document.createElement("button");
    btn.className = "piece-btn" + (n <= 0 ? " empty" : "") +
      (selectedKind === kind ? " selected" : "");
    btn.innerHTML = `${kind}<span class="count">残り${n}</span>`;
    if (n > 0) {
      btn.addEventListener("click", () => {
        selectedKind = (selectedKind === kind) ? null : kind;
        selectedPlace = null;
        renderTray(); renderBoard();
      });
    }
    tray.appendChild(btn);
  }
}

function updateSetupStatus() {
  const remain = remainingCounts();
  const total = Object.values(remain).reduce((a, b) => a + b, 0);
  const ready = state.ready[session.seat];
  const btn = $("btn-ready");
  if (ready) {
    $("setup-status").textContent = "配置完了！ 相手の配置を待っています…";
    btn.disabled = true;
    btn.textContent = "相手を待っています…";
  } else {
    $("setup-status").textContent = total === 0 ? "すべて置けました。準備OK！" : `あと ${total} 個置いてください。`;
    btn.disabled = total !== 0;
    btn.textContent = "配置完了！";
  }
}

$("btn-clear").addEventListener("click", () => {
  if (state.ready[session.seat]) return;
  placement = [];
  selectedKind = null;
  selectedPlace = null;
  renderTray(); renderBoard(); updateSetupStatus();
});

$("btn-auto").addEventListener("click", () => {
  if (state.ready[session.seat]) return;
  placement = [];
  const cells = (state.home_cells || []).map(([r, c]) => [r, c]);
  // シャッフル
  for (let i = cells.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [cells[i], cells[j]] = [cells[j], cells[i]];
  }
  let idx = 0;
  for (const kind of Object.keys(state.piece_set)) {
    for (let k = 0; k < state.piece_set[kind]; k++) {
      const [r, c] = cells[idx++];
      placement.push({ row: r, col: c, kind });
    }
  }
  selectedKind = null;
  selectedPlace = null;
  renderTray(); renderBoard(); updateSetupStatus();
});

$("btn-ready").addEventListener("click", async () => {
  try {
    state = await api("/api/setup", { code: session.code, token: session.token, placement });
    lastVersion = state.version;
    render();
  } catch (e) { toast(e.message); }
});

function placedAt(r, c) {
  return placement.find((p) => p.row === r && p.col === c);
}

function handleSetupClick(rr, rc) {
  if (state.ready[session.seat]) return;
  if (!isHomeCell(rr, rc)) { toast("色つきの自陣マス（総司令部を含む）に置いてください。"); return; }
  const ex = placedAt(rr, rc);
  const redraw = () => { renderTray(); renderBoard(); updateSetupStatus(); };

  // すでに盤の駒を持ち上げている場合
  if (selectedPlace) {
    const cur = placedAt(selectedPlace.row, selectedPlace.col);
    if (!cur) { selectedPlace = null; }
    else if (rr === selectedPlace.row && rc === selectedPlace.col) {  // 同じ駒 → 置き場に戻す
      placement = placement.filter((p) => p !== cur); selectedPlace = null; redraw(); return;
    } else if (ex) {                                                  // 別の駒 → 位置を入れ替え
      const ar = cur.row, ac = cur.col; cur.row = ex.row; cur.col = ex.col; ex.row = ar; ex.col = ac;
      selectedPlace = null; redraw(); return;
    } else {                                                         // 空きマス → そこへ移動
      cur.row = rr; cur.col = rc; selectedPlace = null; redraw(); return;
    }
  }

  // 何も持ち上げていない場合
  if (ex) { selectedPlace = { row: rr, col: rc }; selectedKind = null; redraw(); return; }  // 盤の駒を持ち上げる
  if (!selectedKind) { toast("置き場から駒を選ぶか、盤の駒をタップしてください。"); return; }
  const remain = remainingCounts();
  if (remain[selectedKind] <= 0) { toast("その駒はもうありません。"); return; }
  placement.push({ row: rr, col: rc, kind: selectedKind });
  if (remainingCounts()[selectedKind] <= 0) selectedKind = null;
  redraw();
}

// ===========================================================================
// 対戦フェーズ：駒の移動
// ===========================================================================
const IMMOVABLE = new Set(["地雷", "軍旗"]);

// ---- 盤の形の判定（サーバーから来た geometry を使う。rules.py と同じ考え方）----
function inBoard(r, c) { return r >= 0 && r < state.rows && c >= 0 && c < state.cols; }
function isGateCol(c) { return (state.gate_cols || []).includes(c); }

// 総司令部の“相方マス”（駒を置けない見た目だけのマス）の集合
function phantomSet() {
  const s = new Set();
  const p = state.hq_phantom || {};
  for (const k of Object.keys(p)) s.add(p[k][0] + "," + p[k][1]);
  return s;
}
function isPhantom(r, c) { return phantomSet().has(r + "," + c); }

// その行に総司令部の本体マスがあれば、その座標を返す
function hqOfRow(r) {
  const hq = state.hq || {};
  for (const k of Object.keys(hq)) if (hq[k][0] === r) return [hq[k][0], hq[k][1]];
  return null;
}

// 駒が“止まれる”マスか（川と、総司令部の相方マスは不可）
function isCell(r, c) {
  if (!inBoard(r, c)) return false;
  if (r === state.border_row) return false;
  if (isPhantom(r, c)) return false;
  return true;
}

// (r,c)が指す“本当のマス”。相方マスは総司令部の本体に読み替える。止まれないなら null。
function normalize(r, c) {
  if (isPhantom(r, c)) return hqOfRow(r);
  if (isCell(r, c)) return [r, c];
  return null;
}

// 上下左右に1歩進んだ“次のマス”（川は橋の列だけ渡れる・総司令部の相方は読み替え）
function step(r, c, dr, dc) {
  if (dr === 0) {
    let nr = r, nc = c + dc;
    if (isPhantom(nr, nc)) nc += dc;   // 相方マスは飛ばして本体の隣へ
    return normalize(nr, nc);
  }
  const nr = r + dr;
  if (nr === state.border_row) {
    if (isGateCol(c)) return normalize(state.border_row + dr, c);  // 橋で渡る
    return null;                                                   // 川で止まる
  }
  return normalize(nr, c);
}

function forwardDir(seat) { return seat === "A" ? -1 : 1; } // 前（相手側）へ進む行の増分

// 総司令部の本体マスか（前の2マス＋左右へ動ける“横長1マス”）
function isHQBody(r, c) {
  const hq = state.hq || {};
  for (const k of Object.keys(hq)) if (hq[k][0] === r && hq[k][1] === c) return true;
  return false;
}
// 総司令部がまたがる中央2列（本体列と相方列）
function hqCols() {
  const a = state.hq.A, pa = state.hq_phantom.A;
  return [a[1], pa[1]].sort((x, y) => x - y);
}
// 縦1歩。総司令部の駒は中央2列（左前・右前）どちらへも出られる＝最大2マス返す
function stepVertMulti(r, c, dr) {
  const cols = isHQBody(r, c) ? hqCols() : [c];
  const outs = [];
  for (const cc of cols) {
    let p; const nr = r + dr;
    if (nr === state.border_row) p = isGateCol(cc) ? normalize(state.border_row + dr, cc) : null;
    else p = normalize(nr, cc);
    if (p && !(p[0] === r && p[1] === c) && !outs.some((q) => q[0] === p[0] && q[1] === p[1])) outs.push(p);
  }
  return outs;
}
// 1歩目の候補（横＝1マス、縦＝総司令部なら最大2マス）
function firstSteps(r, c, dr, dc) {
  if (dr === 0) { const p = step(r, c, 0, dc); return p ? [p] : []; }
  return stepVertMulti(r, c, dr);
}

function isHomeCell(r, c) {
  return (state.home_cells || []).some(([hr, hc]) => hr === r && hc === c);
}

// ---- (r,c)の自分の駒が動ける先を、駒の種類ごとの動きで返す（サーバーと同じ判定）----
function legalTargets(r, c) {
  const cell = state.board[r][c];
  if (!cell || cell.owner !== session.seat || IMMOVABLE.has(cell.kind)) return [];
  const kind = cell.kind;
  const seat = session.seat;

  const canLand = (pos) => {
    if (!pos) return false;
    const t = state.board[pos[0]][pos[1]];
    return !t || t.owner !== seat;            // 空きか敵
  };
  const isEmpty = (pos) => pos && !state.board[pos[0]][pos[1]];

  const out = [];
  const push = (pos) => {
    if (pos && !out.some(([a, b]) => a === pos[0] && b === pos[1])) out.push([pos[0], pos[1]]);
  };
  const fdir = forwardDir(seat);

  const DIRS = [[-1, 0], [1, 0], [0, -1], [0, 1]];

  if (kind === "ヒコーキ") {
    // 縦は何マスでも（川・壁・駒を飛び越える）、横は1マス。総司令部なら中央2列とも縦に飛べる
    const cols = isHQBody(r, c) ? hqCols() : [c];
    for (const cc of cols) {
      for (let rr = 0; rr < state.rows; rr++) {
        if (rr === r) continue;
        const pos = normalize(rr, cc);
        if (pos && !(pos[0] === r && pos[1] === c) && canLand(pos)) push(pos);
      }
    }
    for (const dc of [-1, 1]) { const pos = step(r, c, 0, dc); if (canLand(pos)) push(pos); }

  } else if (kind === "工兵") {
    // 縦横に何マスでも（飛び越せない・壁や川で止まる）
    for (const [dr, dc] of DIRS) {
      for (const start of firstSteps(r, c, dr, dc)) {
        let cr = start[0], cc = start[1];
        const t0 = state.board[cr][cc];
        if (t0) { if (t0.owner !== seat) push(start); continue; }
        push(start);
        while (true) {
          const pos = step(cr, cc, dr, dc);
          if (!pos) break;
          const t = state.board[pos[0]][pos[1]];
          if (!t) { push(pos); }
          else { if (t.owner !== seat) push(pos); break; }
          cr = pos[0]; cc = pos[1];
        }
      }
    }

  } else if (kind === "タンク" || kind === "騎兵") {
    // 周囲1マス、または前に2マス（間が空いているとき）
    for (const [dr, dc] of DIRS) {
      for (const pos of firstSteps(r, c, dr, dc)) if (canLand(pos)) push(pos);
    }
    for (const one of firstSteps(r, c, fdir, 0)) {
      if (isEmpty(one)) { const two = step(one[0], one[1], fdir, 0); if (canLand(two)) push(two); }
    }

  } else {
    // 将官・佐官・尉官・スパイ：上下左右に1マス
    for (const [dr, dc] of DIRS) {
      for (const pos of firstSteps(r, c, dr, dc)) if (canLand(pos)) push(pos);
    }
  }
  return out;
}

function handlePlayClick(rr, rc) {
  if (state.phase !== "play") return;
  if (state.turn !== session.seat) { toast("いまは相手の手番です。"); return; }

  const cell = state.board[rr][rc];

  // すでに選んでいる駒があれば、そこへ動かせるか判定
  if (selectedCell) {
    const targets = legalTargets(selectedCell.r, selectedCell.c);
    const ok = targets.some(([tr, tc]) => tr === rr && tc === rc);
    if (ok) {
      sendMove(selectedCell, { r: rr, c: rc });
      selectedCell = null;
      return;
    }
    // 同じ駒をもう一度→選択解除
    if (selectedCell.r === rr && selectedCell.c === rc) {
      selectedCell = null; renderBoard(); return;
    }
  }

  // 新しく自分の駒を選ぶ
  if (cell && cell.owner === session.seat) {
    if (IMMOVABLE.has(cell.kind)) { toast(`${cell.kind}は動かせません。`); return; }
    selectedCell = { r: rr, c: rc };
    renderBoard();
    return;
  }
  selectedCell = null;
  renderBoard();
}

async function sendMove(frm, to) {
  try {
    state = await api("/api/move", {
      code: session.code, token: session.token,
      from: { r: frm.r, c: frm.c }, to: { r: to.r, c: to.c },
    });
    lastVersion = state.version;
    render();
  } catch (e) { toast(e.message); }
}

// ===========================================================================
// 盤面の描画（配置・対戦 共用）
// ===========================================================================
function renderBoard() {
  const board = $("board");
  board.style.gridTemplateColumns = `repeat(${state.cols}, 1fr)`;
  board.style.gridTemplateRows = `repeat(${state.rows}, 1fr)`;
  board.innerHTML = "";

  const targets = (state.phase === "play" && selectedCell)
    ? legalTargets(selectedCell.r, selectedCell.c) : [];
  const inTargets = (rr, rc) => targets.some(([tr, tc]) => tr === rr && tc === rc);
  const lm = state.last_move;

  // 表示は「自分が手前（下側）」になるよう変換して並べる
  for (let dr = 0; dr < state.rows; dr++) {
    for (let dc = 0; dc < state.cols; dc++) {
      const [rr, rc] = toReal(dr, dc);

      // 総司令部の“相方マス”は独立して描かない（本体セルが2マス分をまたいで覆う）
      if (isPhantom(rr, rc)) continue;

      const div = document.createElement("div");
      div.className = "cell";
      div.style.gridRow = String(dr + 1);
      div.style.gridColumn = String(dc + 1);

      // 川（国境の行）＝止まれないマス。橋の列だけ渡れる通り道。
      if (rr === state.border_row) {
        div.classList.add(isGateCol(rc) ? "bridge" : "river");
        board.appendChild(div);
        continue;
      }

      // 総司令部の本体マス → 横2マス分にまたがる“ひとつのマス”として描く
      let hqOwner = null;
      for (const owner of ["A", "B"]) {
        const h = state.hq[owner];
        if (h && h[0] === rr && h[1] === rc) hqOwner = owner;
      }
      if (hqOwner) {
        const ph = state.hq_phantom[hqOwner];
        const [, pdc] = toDisplay(ph[0], ph[1]);   // 相方マスの表示列
        const left = Math.min(dc, pdc);
        div.style.gridColumn = `${left + 1} / span 2`;
        div.classList.add(hqOwner === session.seat ? "hq-mine" : "hq-enemy");
        const lbl = document.createElement("div");
        lbl.className = "hq-label";
        lbl.textContent = (hqOwner === session.seat ? "★" : "");
        div.appendChild(lbl);
      }

      // 自陣マスの色付け（配置フェーズ）
      if (state.phase === "setup" && isHomeCell(rr, rc)) {
        div.classList.add("home");
        if (!state.ready[session.seat]) div.classList.add("selectable-home");
      }

      // 直前の移動：元位置（点線の丸）と移動先（金の枠）を別々に印付け
      if (lm) {
        if (lm.from[0] === rr && lm.from[1] === rc) div.classList.add("moved-from");
        if (lm.to[0] === rr && lm.to[1] === rc) div.classList.add("moved-to");
      }

      // 駒の表示
      let piece = null;
      if (state.phase === "setup") {
        const p = placedAt(rr, rc);
        if (p) piece = { owner: session.seat, kind: p.kind, mine: true };
        // 相手の配置は見えない（サーバーからも来ない）ので表示しない
      } else {
        piece = state.board[rr][rc];
      }

      if (piece) {
        const pc = document.createElement("div");
        const enemy = piece.owner !== session.seat;   // 相手の駒は下向き（自分に向く）
        if (piece.hidden) {
          pc.className = "pc hidden-pc" + (enemy ? " enemy" : "");
        } else {
          pc.className = "pc " + (piece.owner === "A" ? "a" : "b") + (enemy ? " enemy" : "");
          pc.textContent = piece.kind;
        }
        div.appendChild(pc);
      }

      // 選択中・移動可能マスの装飾
      if (selectedCell && selectedCell.r === rr && selectedCell.c === rc) {
        div.classList.add("selected");
      }
      if (state.phase === "setup" && selectedPlace && selectedPlace.row === rr && selectedPlace.col === rc) {
        div.classList.add("selected");
      }
      if (inTargets(rr, rc)) div.classList.add("movable");

      div.addEventListener("click", () => {
        if (state.phase === "setup") handleSetupClick(rr, rc);
        else if (state.phase === "play") handlePlayClick(rr, rc);
      });

      board.appendChild(div);
    }
  }
}

// ---- ログ ----
function renderLog() {
  const ul = $("log");
  ul.innerHTML = "";
  for (const line of state.log) {
    const li = document.createElement("li");
    li.textContent = line;
    ul.appendChild(li);
  }
  ul.scrollTop = ul.scrollHeight;
}

// ---- もう一局 ----
$("btn-rematch").addEventListener("click", async () => {
  try {
    state = await api("/api/rematch", { code: session.code, token: session.token });
    lastVersion = state.version;
    placement = []; selectedKind = null; selectedPlace = null; selectedCell = null;
    render();
  } catch (e) { toast(e.message); }
});

// ===========================================================================
// 起動時：定期確認(ポーリング)を常時オンにする。これは二度と止めない。
//   ・部屋に居ないときは refresh() が即 return するだけ（無害）。
//   ・部屋を作る/入ると、次の確認で自動的に最新状態から画面が描かれる。
// ===========================================================================
setInterval(refresh, 1000);
if (session) {
  refresh();               // すぐ1回
} else {
  showScreen("screen-top");
}
