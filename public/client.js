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

// 感想戦用：いま何手目の局面を見ているか（over のときだけ使う。null=未設定）
let reviewIndex = null;

// いま画面に描いてある「生の盤」（駒がすべるアニメーションで、動く前の駒の正体を知るために使う）
let shownBoard = null;

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

// ---- 駒を動かしたときの効果音（ブラウザ内蔵の音源で短く「コッ」）----
// 外部の音ファイルは使わず、その場で音を作って鳴らす（素材ファイル不要で確実）。
// 音のオン/オフはブラウザに覚えさせる（次に開いたときも設定が残る）。
function loadSoundPref() {
  try { return localStorage.getItem("gunjin_sound") !== "off"; } catch (e) { return true; }
}
function saveSoundPref(on) {
  try { localStorage.setItem("gunjin_sound", on ? "on" : "off"); } catch (e) { /* 無視 */ }
}
let soundOn = loadSoundPref();

let audioCtx = null;
function getAudio() {
  if (!soundOn) return null;
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return null;
  audioCtx = audioCtx || new AC();
  if (audioCtx.state === "suspended") audioCtx.resume();  // 一度クリックした後は鳴らせる
  return audioCtx;
}
// 駒が動くときの音は3段階。人が「今、動いた」と分かりやすいように、
//   直前＝持ち上げる「ピッ」（高く短い）／途中＝すべる「シュッ」（息のような音）／終わり＝着地の「コッ」（木の駒）
function playPickSound() {
  try {
    const ctx = getAudio(); if (!ctx) return;
    const now = ctx.currentTime;
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.type = "sine";
    o.frequency.setValueAtTime(880, now);
    o.frequency.exponentialRampToValueAtTime(1180, now + 0.05);
    g.gain.setValueAtTime(0.0001, now);
    g.gain.exponentialRampToValueAtTime(0.10, now + 0.006);
    g.gain.exponentialRampToValueAtTime(0.0001, now + 0.07);
    o.connect(g); g.connect(ctx.destination);
    o.start(now); o.stop(now + 0.08);
  } catch (e) { /* 音が出せない環境でも黙ってスルー */ }
}
function playSlideSound(durationSec) {
  try {
    const ctx = getAudio(); if (!ctx) return;
    const now = ctx.currentTime;
    const dur = Math.max(0.15, durationSec || 0.26);
    // ノイズを帯域フィルタに通して「シュッ」。音の高さを上げながら消す
    const len = Math.floor(ctx.sampleRate * dur);
    const buf = ctx.createBuffer(1, len, ctx.sampleRate);
    const d = buf.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
    const src = ctx.createBufferSource(); src.buffer = buf;
    const f = ctx.createBiquadFilter();
    f.type = "bandpass"; f.Q.value = 1.2;
    f.frequency.setValueAtTime(500, now);
    f.frequency.exponentialRampToValueAtTime(1800, now + dur);
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, now);
    g.gain.exponentialRampToValueAtTime(0.09, now + dur * 0.3);
    g.gain.exponentialRampToValueAtTime(0.0001, now + dur);
    src.connect(f); f.connect(g); g.connect(ctx.destination);
    src.start(now); src.stop(now + dur + 0.01);
  } catch (e) { /* 無視 */ }
}
// ---- 着地の音：拍子木 ----
// renが用意した音ファイル（拍子木1.mp3）を、別ファイルにせずこの中に埋め込んである
// （サーバーの設定を変えずに済み、反映もいつもの3ファイルで足りるため）。
// 音ファイルが使えない環境では、下の合成音「コッ」で代用する。
const HYOSHIGI_DATA = "data:audio/mpeg;base64,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qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqp35Xm/yvI1ebGtQkJZeE4bm7dXlNCkRDQ2ULqLqFyhRHCcNZWTuvKKIhEQOjJRdKq4kjkNInlQzPH4lRidFYvnC/GToplRZHWc3lxylSL0So9MiSPRCK5wvcjdttq5vfLq46Q2H6a1DAw/E86uMRyBUHR4M1i9iFCKaGkX0v3ztcvE8tOiqXiknYhaOSsPI1CcSzx9yN1qHaf0zvWahcjdeWtQ377NScWWZcbNeTTi2dnYssxNVSSEmCVC//uybF2P9c55rwksN1JQa1aGBEOqAAABpAAAACAAADSAAAAEgxu4SGtn/whIwcjNNOxgoYEcjt6f0Rf+zsYoYGDIdn9PKZSM/sa2GRlLJ//tKjkZe1n//8sUMFsv9lqORkyyX/awEHSyygoLN9ISF8VFTVVMQU1FMy4xMDBVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV";
let hyoshigiBase = null;
function playMoveSound() {   // 着地の音
  if (!soundOn) return;
  try {
    if (!hyoshigiBase) { hyoshigiBase = new Audio(HYOSHIGI_DATA); hyoshigiBase.preload = "auto"; }
    const a = hyoshigiBase.cloneNode();   // 連続で鳴っても重ねて鳴らせるように複製する
    a.volume = 0.9;
    const p = a.play();
    if (p && p.catch) p.catch(() => playKnockSynth());
    return;
  } catch (e) { /* 下の合成音へ */ }
  playKnockSynth();
}
function playKnockSynth() {   // 合成音の「コッ」（音ファイルが鳴らせないときの代用）
  try {
    const ctx = getAudio(); if (!ctx) return;
    const now = ctx.currentTime;
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.type = "triangle";
    o.frequency.setValueAtTime(320, now);
    o.frequency.exponentialRampToValueAtTime(180, now + 0.09); // 少し下がる＝木の駒っぽい
    g.gain.setValueAtTime(0.0001, now);
    g.gain.exponentialRampToValueAtTime(0.18, now + 0.008);
    g.gain.exponentialRampToValueAtTime(0.0001, now + 0.12);
    o.connect(g); g.connect(ctx.destination);
    o.start(now);
    o.stop(now + 0.13);
  } catch (e) { /* 音が出せない環境でも黙ってスルー */ }
}
// 直近で音を鳴らした手（同じ手で二重に鳴らさない用）。"init"＝まだ観測前で鳴らさない
let lastMoveSig = "init";

// ---- BGM ----
// 配置中と対戦中で曲を変える。曲のファイルは public/ に置いてある（大きいので埋め込まない）。
//   配置中：モーツァルト「レクイエム 怒りの日」／対戦中：ブラームス「交響曲第1番 第1楽章」
//   いずれも CMSL クラシック名曲サウンドライブラリー（CC BY 2.1 JP）。
// オン／オフは効果音とは別に覚える（効果音は欲しいがBGMは要らない人のため）。
const BGM_FILES = { setup: "bgm_setup.mp3", play: "bgm_play.m4a" };
const BGM_VOLUME = 0.35;
function loadBgmPref() {
  try { return localStorage.getItem("gunjin_bgm") !== "off"; } catch (e) { return true; }
}
function saveBgmPref(on) {
  try { localStorage.setItem("gunjin_bgm", on ? "on" : "off"); } catch (e) { /* 無視 */ }
}
let bgmOn = loadBgmPref();
let bgmWanted = null;        // いま流すべき曲（"setup"／"play"／null＝無音）
let bgmAudio = null;         // 再生中の Audio
let bgmTrack = null;         // bgmAudio が何の曲か
// 「今この場面で流すべき曲」を受け取り、実際の再生状態をそれに合わせる
function setBgm(track) {
  bgmWanted = track;
  syncBgm();
}
function syncBgm() {
  try {
    const want = bgmOn ? bgmWanted : null;
    if (!want) {
      if (bgmAudio) { bgmAudio.pause(); bgmAudio.currentTime = 0; }
      return;
    }
    if (bgmTrack !== want) {
      if (bgmAudio) { bgmAudio.pause(); }
      bgmAudio = new Audio(BGM_FILES[want]);
      bgmAudio.loop = true;
      bgmAudio.volume = BGM_VOLUME;
      bgmAudio.preload = "auto";
      bgmTrack = want;
    }
    if (bgmAudio.paused) {
      const p = bgmAudio.play();
      // ページを開き直した直後などは、ブラウザが「操作があるまで音を出さない」ことがある。
      // その場合は次にどこかをクリックしたときにもう一度だけ試す。
      if (p && p.catch) p.catch(() => {
        document.addEventListener("click", () => syncBgm(), { once: true });
      });
    }
  } catch (e) { /* 音が出せない環境でも黙ってスルー */ }
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
    const picked = document.querySelector('input[name="variant"]:checked');
    const variant = picked ? picked.value : "standard";
    const r = await api("/api/create", { variant });
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
  reviewIndex = null;
  lastMoveSig = "init";
  shownBoard = null;
  setBgm(null);
  $("join-code").value = "";
  $("btn-create").disabled = false;   // トップに戻ったら作成・入室を押せるように戻す
  $("btn-join").disabled = false;
  hide("board-wrap");
  hide("log-wrap");
  hide("play-controls");
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

  setBgm(phase === "setup" ? "setup" : phase === "play" ? "play" : null);

  if (phase === "waiting") {
    showScreen("screen-wait");
    $("room-code").textContent = state.code;
    $("wait-variant").textContent = `盤：${state.variant_name || "標準"}（${state.total_pieces || 23}枚・${state.cols || 6}列）`;
    hide("board-wrap"); hide("log-wrap"); hide("rules-ref"); hide("play-controls");
    return;
  }

  applyVariantTexts();
  if (phase === "setup") {
    showScreen("screen-setup");
    show("board-wrap"); show("log-wrap"); show("rules-ref"); show("play-controls");
    hide("btn-resign");
    renderLog();
    renderTray();
    renderBoard();
    updateSetupStatus();
    $("btn-rematch").classList.add("hidden");
    return;
  }

  if (phase === "play" || phase === "over") {
    showScreen("screen-play");
    show("board-wrap"); show("log-wrap"); show("rules-ref"); show("play-controls");
    // 直前の1手が変わっていたら（自分の手・相手の手どちらも）、描いたあとに駒をすべらせる。
    // 感想戦の巻き戻しでは動かさない（lastMoveSig は生の盤の最新手だけを追う）。
    const lm = state.last_move;
    const sig = lm ? JSON.stringify(lm) : "none";
    const moved = (lastMoveSig !== "init" && sig !== "none" && sig !== lastMoveSig);
    const boardBefore = shownBoard;   // 動く前に画面に出ていた盤（動いた駒の正体を知るため）
    lastMoveSig = sig;
    renderTurnBanner();
    if (phase === "over") {
      // 感想戦モード：投了ボタンは隠し、振り返りパネルを出す
      hide("btn-resign");
      const total = (state.history || []).length;
      // 初めて感想戦に入ったとき（未設定/範囲外）は「最後の局面」から見せる
      if (reviewIndex === null || reviewIndex >= total) reviewIndex = Math.max(0, total - 1);
      show("review-panel");
      renderReview();
    } else {
      // 対戦中：投了ボタンを出し、振り返りパネルは隠す
      show("btn-resign");
      hide("review-panel");
      reviewIndex = null;
    }
    renderBoard();
    renderLog();
    $("btn-rematch").classList.toggle("hidden", phase !== "over");
    if (moved && boardBefore) {
      const piece = boardBefore[lm.from[0]] && boardBefore[lm.from[0]][lm.from[1]];
      animateMove(lm, piece, piece && piece.owner === session.seat);
    }
    return;
  }
}

// ---- 駒がすべって動くアニメーション ----
// 本物の駒は着地まで隠し、同じ見た目の「幽霊の駒」を元の位置から移動先まですべらせる。
// 自分の手：持ち上げの音は駒を選んだ時点で鳴っているので、ここでは「すべる」と「着地」だけ。
// 相手の手：持ち上げ→すべる→着地の3つを順に鳴らす（相手が動かしたと気づきやすいように）。
const SLIDE_MS = 260;
function cellEl(r, c) {
  return $("board").querySelector(`.cell[data-r="${r}"][data-c="${c}"]`);
}
function animateMove(lm, piece, isMine) {
  const fromEl = cellEl(lm.from[0], lm.from[1]);
  const toEl = cellEl(lm.to[0], lm.to[1]);
  if (!fromEl || !toEl || !piece) { playMoveSound(); return; }
  const wrap = $("board-wrap");
  const wr = wrap.getBoundingClientRect();
  const fr = fromEl.getBoundingClientRect(), tr = toEl.getBoundingClientRect();
  const size = Math.min(fr.height, tr.height) * 0.92;   // 総司令部（横長）でも駒は正方形のまま

  const enemy = piece.owner !== session.seat;
  const ghost = document.createElement("div");
  ghost.className = "pc ghost " + (piece.hidden ? "hidden-pc" : (piece.owner === "A" ? "a" : "b")) + (enemy ? " enemy" : "");
  if (!piece.hidden) ghost.textContent = piece.kind;
  ghost.style.width = ghost.style.height = size + "px";
  ghost.style.fontSize = getComputedStyle(toEl).fontSize;   // マスと同じ文字の大きさ
  const fx = fr.left - wr.left + (fr.width - size) / 2, fy = fr.top - wr.top + (fr.height - size) / 2;
  const tx = tr.left - wr.left + (tr.width - size) / 2, ty = tr.top - wr.top + (tr.height - size) / 2;
  ghost.style.left = fx + "px"; ghost.style.top = fy + "px";
  ghost.style.transform = "translate(0,0)";

  const realPc = toEl.querySelector(".pc");
  if (realPc) realPc.style.visibility = "hidden";
  wrap.appendChild(ghost);

  const start = () => {
    playSlideSound(SLIDE_MS / 1000);
    ghost.getBoundingClientRect();   // いったん描かせてから動かす（これがないと一瞬で移動する）
    ghost.style.transform = `translate(${tx - fx}px, ${ty - fy}px)`;
    setTimeout(() => {
      ghost.remove();
      if (realPc) realPc.style.visibility = "";
      playMoveSound();
    }, SLIDE_MS + 30);
  };
  if (isMine) start();
  else { playPickSound(); setTimeout(start, 140); }
}

function show(id) { $(id).classList.remove("hidden"); }
function hide(id) { $(id).classList.add("hidden"); }

// 盤の種類（標準／大型）で変わる文言
function applyVariantTexts() {
  const large = state.variant === "large";
  $("setup-title").textContent = `駒を配置しましょう（${state.variant_name || "標準"}・全${state.total_pieces || 23}枚）`.replace("標準・", "");
  $("rules-board-standard").classList.toggle("hidden", large);
  $("rules-board-large").classList.toggle("hidden", !large);
  $("rules-pieces-standard").classList.toggle("hidden", large);
  $("rules-pieces-large").classList.toggle("hidden", !large);
}

// ---- 手番・勝敗の表示 ----
function renderTurnBanner() {
  const b = $("turn-banner");
  const battle = state.last_battle
    ? `<div class="last-battle">⚔ 直前の戦闘：${state.last_battle}</div>` : "";
  if (state.phase === "over") {
    const win = state.winner === session.seat;
    const byResign = !!state.resigned;
    const iResigned = state.resigned === session.seat;
    let msg;
    if (win) msg = byResign ? "🎉 相手が投了。あなたの勝ち！" : "🎉 あなたの勝ち！";
    else msg = iResigned ? "🏳 投了しました（あなたの負け）" : "…あなたの負け";
    b.className = "banner " + (win ? "your-turn" : "wait-turn");
    b.innerHTML = `<div class="result ${win ? "win" : "lose"}">${msg}</div>` + battle;
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
  const all = (state.home_cells || []).map(([r, c]) => [r, c]);
  // 突入口の前（地雷・軍旗を置けないマス）とそれ以外に分ける
  const forbidden = all.filter(([r, c]) => isNoImmovableCell(r, c));
  const free = shuffleArray(all.filter(([r, c]) => !isNoImmovableCell(r, c)));
  // 駒を「動く駒」「動かない駒(地雷・軍旗)」に分ける
  const movable = [], immovable = [];
  for (const kind of Object.keys(state.piece_set)) {
    for (let k = 0; k < state.piece_set[kind]; k++) {
      (IMMOVABLE.has(kind) ? immovable : movable).push(kind);
    }
  }
  shuffleArray(movable);
  placement = [];
  // まず突入口の前には必ず「動く駒」を置く（地雷・軍旗が来ないように）
  let mi = 0;
  for (const [r, c] of forbidden) placement.push({ row: r, col: c, kind: movable[mi++] });
  // 残りの駒（残った動く駒＋地雷・軍旗）を、突入口以外のマスに配る
  const rest = shuffleArray(movable.slice(mi).concat(immovable));
  for (let i = 0; i < free.length; i++) {
    placement.push({ row: free[i][0], col: free[i][1], kind: rest[i] });
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

  const NG = "地雷・軍旗は、突入口のマス（赤い枠）には置けません。";

  // すでに盤の駒を持ち上げている場合
  if (selectedPlace) {
    const cur = placedAt(selectedPlace.row, selectedPlace.col);
    if (!cur) { selectedPlace = null; }
    else if (rr === selectedPlace.row && rc === selectedPlace.col) {  // 同じ駒 → 置き場に戻す
      placement = placement.filter((p) => p !== cur); selectedPlace = null; redraw(); return;
    } else if (ex) {                                                  // 別の駒 → 位置を入れ替え
      // 入れ替えで地雷・軍旗が突入口の前に来てしまう場合は止める
      if (IMMOVABLE.has(cur.kind) && isNoImmovableCell(ex.row, ex.col)) { toast(NG); return; }
      if (IMMOVABLE.has(ex.kind) && isNoImmovableCell(cur.row, cur.col)) { toast(NG); return; }
      const ar = cur.row, ac = cur.col; cur.row = ex.row; cur.col = ex.col; ex.row = ar; ex.col = ac;
      selectedPlace = null; redraw(); return;
    } else {                                                         // 空きマス → そこへ移動
      if (IMMOVABLE.has(cur.kind) && isNoImmovableCell(rr, rc)) { toast(NG); return; }
      cur.row = rr; cur.col = rc; selectedPlace = null; redraw(); return;
    }
  }

  // 何も持ち上げていない場合
  if (ex) { selectedPlace = { row: rr, col: rc }; selectedKind = null; redraw(); return; }  // 盤の駒を持ち上げる
  if (!selectedKind) { toast("置き場から駒を選ぶか、盤の駒をタップしてください。"); return; }
  const remain = remainingCounts();
  if (remain[selectedKind] <= 0) { toast("その駒はもうありません。"); return; }
  if (IMMOVABLE.has(selectedKind) && isNoImmovableCell(rr, rc)) { toast(NG); return; }
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
// 大型版の白い丸（川の行にある、駒が止まれるマス）
function isCircle(r, c) { return (state.circles || []).some(([a, b]) => a === r && b === c); }
// 丸(r,c)につながる突入口のマス4つ
function circleLinks(r, c) { return (state.circle_links || {})[r + "," + c] || []; }
// 突入口のマス(r,c)からつながる丸。突入口でなければ null
function gateCircle(r, c) {
  for (const [cr, cc] of (state.circles || [])) {
    if (circleLinks(cr, cc).some(([a, b]) => a === r && b === c)) return [cr, cc];
  }
  return null;
}

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

// 駒が“止まれる”マスか（川と、総司令部の相方マスは不可。白い丸だけは止まれる）
function isCell(r, c) {
  if (!inBoard(r, c)) return false;
  if (r === state.border_row) return isCircle(r, c);
  if (isPhantom(r, c)) return false;
  return true;
}

// (r,c)が指す“本当のマス”。相方マスは総司令部の本体に読み替える。止まれないなら null。
function normalize(r, c) {
  if (isPhantom(r, c)) return hqOfRow(r);
  if (isCell(r, c)) return [r, c];
  return null;
}

// 縦に1歩進んだ先。川に踏み込むなら、橋（標準）か白い丸（大型）の決まりに従う
function vertFrom(r, c, dr) {
  const nr = r + dr;
  if (nr === state.border_row) {
    if (isGateCol(c)) return normalize(state.border_row + dr, c);  // 標準：橋で向こう岸へ
    return gateCircle(r, c);                                       // 大型：突入口のマスから丸へ（他は川で止まる）
  }
  return normalize(nr, c);
}
// 上下左右に1歩進んだ“次のマス”。工兵の滑りやタンクの2歩目の“続き”に使う。
// 白い丸からの続きの1歩は無い（丸の出入りは firstSteps で扱う）。
function step(r, c, dr, dc) {
  if (r === state.border_row) return null;
  if (dr === 0) {
    let nr = r, nc = c + dc;
    if (isPhantom(nr, nc)) nc += dc;   // 相方マスは飛ばして本体の隣へ
    return normalize(nr, nc);
  }
  return vertFrom(r, c, dr);
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
// 縦1歩。総司令部の駒は中央2列（左前・右前）どちらへも出られる＝最大2マス返す。
// 白い丸の駒は、その方向の突入口2マスへ出られる。
function stepVertMulti(r, c, dr) {
  if (isCircle(r, c)) return circleLinks(r, c).filter(([a]) => a === r + dr).map(([a, b]) => [a, b]);
  const cols = isHQBody(r, c) ? hqCols() : [c];
  const outs = [];
  for (const cc of cols) {
    const p = vertFrom(r, cc, dr);
    if (p && !(p[0] === r && p[1] === c) && !outs.some((q) => q[0] === p[0] && q[1] === p[1])) outs.push(p);
  }
  return outs;
}
// 1歩目の候補（横＝1マス、縦＝総司令部なら最大2マス・丸なら突入口2マス）
function firstSteps(r, c, dr, dc) {
  if (dr === 0) { if (isCircle(r, c)) return []; const p = step(r, c, 0, dc); return p ? [p] : []; }
  return stepVertMulti(r, c, dr);
}

function isHomeCell(r, c) {
  return (state.home_cells || []).some(([hr, hc]) => hr === r && hc === c);
}

// 地雷・軍旗を置けないマス（突入口の手前）か
function isNoImmovableCell(r, c) {
  return (state.no_immovable_cells || []).some(([hr, hc]) => hr === r && hc === c);
}

// 配列をその場でシャッフルする（おまかせ配置で使う）
function shuffleArray(arr) {
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
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
    // 縦横に何マスでも（飛び越せない・壁や川で止まる）。
    // 白い丸への出入りは隣の突入口のマスからの1歩だけ（滑りの途中で丸には入れず、丸から滑り出せない）
    for (const [dr, dc] of DIRS) {
      for (const start of firstSteps(r, c, dr, dc)) {
        let cr = start[0], cc = start[1];
        const t0 = state.board[cr][cc];
        if (t0) { if (t0.owner !== seat) push(start); continue; }
        push(start);
        if (isCircle(r, c)) continue;
        while (true) {
          const pos = step(cr, cc, dr, dc);
          if (!pos || isCircle(pos[0], pos[1])) break;
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
    if (!isCircle(r, c)) {
      for (const one of firstSteps(r, c, fdir, 0)) {
        if (!isEmpty(one)) continue;
        const two = step(one[0], one[1], fdir, 0);
        if (two && !isCircle(two[0], two[1]) && canLand(two)) push(two);
      }
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
    playPickSound();
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
// いま描くべき盤面。感想戦(over)のときは記録した局面を、それ以外は生の盤面を使う。
function activeBoard() {
  if (state.phase === "over" && state.history && state.history.length && reviewIndex !== null) {
    return state.history[reviewIndex].board;
  }
  return state.board;
}
// いま強調すべき「直前の1手」。感想戦のときはその局面を作った手を使う。
function activeLastMove() {
  if (state.phase === "over" && state.history && state.history.length && reviewIndex !== null) {
    return state.history[reviewIndex].move;
  }
  return state.last_move;
}

// ---- 盤の大きさを画面に合わせる ----
// パソコン（横幅900px以上）では、右側の領域の縦と横の余白から「マス1つの大きさ」を決め、
// 盤がはみ出さない最大の大きさにする。スマホでは従来どおり横幅いっぱい（CSSに任せる）。
const GAP = 3, PAD = 3;   // style.css の #board の gap と padding と同じ値
let lastCellPx = 40;      // fitBoard が決めた、マス1つの大きさ（突入口の帯を描くときに使う）
function fitBoard() {
  const board = $("board");
  if (!state) return;
  const cols = state.cols, rows = state.rows;
  const wrap = $("board-wrap");
  const area = board.closest(".boardarea");
  let cell;
  if (area && window.innerWidth >= 900) {
    // パソコン：右側の領域の縦横の余白から、はみ出さない最大のマスにする
    const rect = area.getBoundingClientRect();
    const availW = rect.width - 24, availH = rect.height - 24;   // 領域のpadding(12px×2)ぶんを引く
    const cw = (availW - PAD * 2 - GAP * (cols - 1)) / cols;
    const ch = (availH - PAD * 2 - GAP * (rows - 1)) / rows;
    cell = Math.min(cw, ch);
  } else {
    // スマホ：盤の置き場の横幅いっぱいにする
    cell = (wrap.clientWidth - PAD * 2 - GAP * (cols - 1)) / cols;
  }
  cell = Math.max(24, Math.floor(cell));
  lastCellPx = cell;
  // 行と列の幅を「等分（1fr）」ではなくピクセルで直接指定する。
  // 等分だとブラウザによってはマスの中身に引っぱられて幅が伸び、盤の右と下がはみ出す。
  board.style.gridTemplateColumns = `repeat(${cols}, ${cell}px)`;
  board.style.gridTemplateRows = `repeat(${rows}, ${cell}px)`;
  board.style.width = (cell * cols + GAP * (cols - 1) + PAD * 2) + "px";
  board.style.height = (cell * rows + GAP * (rows - 1) + PAD * 2) + "px";
  board.style.setProperty("--cell-font", Math.round(cell * 0.3) + "px");
}
window.addEventListener("resize", () => { if (state) fitBoard(); });

// 大型版：突入口のマスと白い丸をつなぐ斜めの帯を、マスの下に描く
function drawCircleBands(board) {
  const circles = state.circles || [];
  if (!circles.length) return;
  const w = board.style.width, h = board.style.height;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "bands");
  svg.setAttribute("width", w); svg.setAttribute("height", h);
  const center = (rr, rc) => {
    const [dr, dc] = toDisplay(rr, rc);
    return [PAD + dc * (lastCellPx + GAP) + lastCellPx / 2, PAD + dr * (lastCellPx + GAP) + lastCellPx / 2];
  };
  const wide = Math.max(8, lastCellPx * 0.46), inner = Math.max(5, lastCellPx * 0.36);
  for (const pass of [["#8fa3b1", wide], ["#dfe8ee", inner]]) {
    for (const [cr, cc] of circles) {
      const [x2, y2] = center(cr, cc);
      for (const [gr, gc] of circleLinks(cr, cc)) {
        const [x1, y1] = center(gr, gc);
        const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
        line.setAttribute("x1", x1); line.setAttribute("y1", y1);
        line.setAttribute("x2", x2); line.setAttribute("y2", y2);
        line.setAttribute("stroke", pass[0]); line.setAttribute("stroke-width", pass[1]);
        line.setAttribute("stroke-linecap", "round");
        svg.appendChild(line);
      }
    }
  }
  board.appendChild(svg);
}

function renderBoard() {
  const board = $("board");
  fitBoard();   // 行と列の幅と盤の大きさを決める（パソコンはピクセル指定、スマホは等分）
  board.innerHTML = "";
  drawCircleBands(board);

  const targets = (state.phase === "play" && selectedCell)
    ? legalTargets(selectedCell.r, selectedCell.c) : [];
  const inTargets = (rr, rc) => targets.some(([tr, tc]) => tr === rr && tc === rc);
  const lm = activeLastMove();
  const liveBoard = activeBoard();

  // 表示は「自分が手前（下側）」になるよう変換して並べる
  for (let dr = 0; dr < state.rows; dr++) {
    for (let dc = 0; dc < state.cols; dc++) {
      const [rr, rc] = toReal(dr, dc);

      // 総司令部の“相方マス”は独立して描かない（本体セルが2マス分をまたいで覆う）
      if (isPhantom(rr, rc)) continue;

      const div = document.createElement("div");
      div.className = "cell";
      div.dataset.r = String(rr); div.dataset.c = String(rc);   // 実座標（アニメーションでマスを探す用）
      div.style.gridRow = String(dr + 1);
      div.style.gridColumn = String(dc + 1);

      // 川（国境の行）＝止まれないマス。標準は橋の列だけ渡れる通り道。大型の白い丸は止まれるマス。
      if (rr === state.border_row) {
        if (isCircle(rr, rc)) {
          div.classList.add("circle");
        } else {
          div.classList.add(isGateCol(rc) ? "bridge" : "river");
          board.appendChild(div);
          continue;
        }
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
        // 突入口の前＝地雷・軍旗を置けないマスに目印
        if (isNoImmovableCell(rr, rc)) div.classList.add("gate-front");
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
        piece = liveBoard[rr][rc];
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
  // 感想戦で過去の局面を見ているときは、生の盤は変わっていないので覚え直さない
  if (state.phase !== "setup" && liveBoard === state.board) shownBoard = state.board;
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

// ---- 感想戦（1手ずつ振り返る）----
function renderReview() {
  const h = state.history || [];
  const n = h.length;
  const info = $("review-info");
  if (!n) { info.textContent = ""; return; }
  const idx = reviewIndex;
  const frame = h[idx];
  let desc;
  if (idx === 0) {
    desc = "対戦開始（配置直後）の局面";
  } else {
    const bt = frame && frame.battle ? "　⚔ " + frame.battle : "";
    desc = `${idx}手目の局面${bt}`;
  }
  info.textContent = `${desc}　［全${n - 1}手］`;
  // 端では戻す/すすむを押せなくする
  $("rv-first").disabled = idx <= 0;
  $("rv-prev").disabled = idx <= 0;
  $("rv-next").disabled = idx >= n - 1;
  $("rv-last").disabled = idx >= n - 1;
}

function gotoReview(idx) {
  const n = (state.history || []).length;
  if (!n) return;
  reviewIndex = Math.max(0, Math.min(n - 1, idx));
  renderBoard();
  renderReview();
}
$("rv-first").addEventListener("click", () => gotoReview(0));
$("rv-prev").addEventListener("click", () => gotoReview((reviewIndex || 0) - 1));
$("rv-next").addEventListener("click", () => gotoReview((reviewIndex || 0) + 1));
$("rv-last").addEventListener("click", () => gotoReview((state.history || []).length - 1));

// ---- 音のオン/オフ スイッチ ----
function updateSoundBtn() {
  const b = $("btn-sound");
  b.textContent = soundOn ? "🔊 音 オン" : "🔇 音 オフ";
  b.classList.toggle("off", !soundOn);
}
$("btn-sound").addEventListener("click", () => {
  soundOn = !soundOn;
  saveSoundPref(soundOn);
  updateSoundBtn();
  if (soundOn) playMoveSound();   // オンにしたら一度鳴らして確認（＆音声を有効化）
});
updateSoundBtn();

// ---- BGMのオン/オフ スイッチ ----
function updateBgmBtn() {
  const b = $("btn-bgm");
  b.textContent = bgmOn ? "♪ BGM オン" : "♪ BGM オフ";
  b.classList.toggle("off", !bgmOn);
}
$("btn-bgm").addEventListener("click", () => {
  bgmOn = !bgmOn;
  saveBgmPref(bgmOn);
  updateBgmBtn();
  syncBgm();
});
updateBgmBtn();

// ---- 投了 ----
$("btn-resign").addEventListener("click", async () => {
  if (!state || state.phase !== "play") return;
  if (!confirm("旗を巻いて（投了して）負けを認めますか？\nこのあと感想戦で1手ずつ振り返れます。")) return;
  try {
    state = await api("/api/resign", { code: session.code, token: session.token });
    lastVersion = state.version;
    render();
  } catch (e) { toast(e.message); }
});

// ---- もう一局 ----
$("btn-rematch").addEventListener("click", async () => {
  try {
    state = await api("/api/rematch", { code: session.code, token: session.token });
    lastVersion = state.version;
    placement = []; selectedKind = null; selectedPlace = null; selectedCell = null;
    reviewIndex = null; lastMoveSig = "init"; shownBoard = null;
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
