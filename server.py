# -*- coding: utf-8 -*-
"""
軍人将棋オンライン対戦の「審判役」サーバー。

役割:
  - 部屋(ルーム)の作成・入室を管理する
  - 盤面の「正解データ」をここだけで保持する（＝審判）
  - 各プレイヤーには「自分の駒の正体だけ」を渡し、相手の駒は裏向きで渡す
  - 駒がぶつかったときに勝敗を判定し、結果だけを両者に知らせる

Python3.13の標準機能だけで動くので、追加インストールは不要です。
起動: python3 server.py   → ブラウザで http://localhost:8000 を開く
"""

import json
import os
import random
import string
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import rules

PORT = 8090

# この秒数のあいだ音沙汰がない席は「抜けた」とみなし、入り直しを許可する
SEAT_TIMEOUT = 30
PUBLIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")

# すべての部屋を保持する（メモリ上のみ。サーバーを止めると消える）
rooms = {}
lock = threading.Lock()  # 複数アクセスが同時に来ても壊れないようにする鍵

# 一時的な通信の記録（原因調査用）
DEBUG_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug_requests.log")


def dlog(msg):
    # 通信の記録を server.log（標準出力）に残す。何かあったとき事実で追えるように。
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# 部屋（ルーム）まわり
# ---------------------------------------------------------------------------

def new_code():
    """他と重複しない4文字の部屋コードを作る。"""
    while True:
        code = "".join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ", k=4))
        if code not in rooms:
            return code


def new_token():
    """本人確認用のランダムな合言葉（プレイヤーごと）。"""
    return "".join(random.choices(string.ascii_letters + string.digits, k=16))


def empty_board():
    return [[None for _ in range(rules.COLS)] for _ in range(rules.ROWS)]


def snapshot_board(board):
    """盤面を“正体つき”で丸ごと複製する（感想戦の記録用）。
    ※この正体つきデータは対戦が終わるまで相手には渡さない（view_forで制御）。"""
    snap = []
    for r in range(rules.ROWS):
        row = []
        for c in range(rules.COLS):
            cell = board[r][c]
            row.append({"owner": cell["owner"], "kind": cell["kind"]} if cell else None)
        snap.append(row)
    return snap


def create_room():
    code = new_code()
    token = new_token()
    rooms[code] = {
        "code": code,
        "players": {"A": token, "B": None},
        "phase": "waiting",          # waiting → setup → play → over
        "board": empty_board(),
        "ready": {"A": False, "B": False},
        "turn": "A",
        "winner": None,
        "resigned": None,            # 投了した軍（A/B）。無ければ None
        "log": [],
        "last_move": None,
        "last_battle": None,
        # 直前の戦闘を「各プレイヤー視点」で持つ（自分の駒名だけ見せる。相手の駒名は伏せる）
        "battle_view": {"A": None, "B": None},
        # 感想戦用：対戦開始からの各局面（正体つき）を1手ごとに記録していく
        "history": [],
        "version": 1,
        # 各席が最後に通信してきた時刻（抜けた席を空けるために使う）
        "last_seen": {"A": time.time(), "B": 0},
    }
    return code, token


def home_cells(seat):
    """そのプレイヤーが駒を置ける自陣のマス一覧（総司令部を含む）。"""
    return rules.home_cells(seat)


def bump(room):
    room["version"] += 1


def add_log(room, text):
    room["log"].append(text)
    # ログが長くなりすぎないよう直近30件だけ残す
    room["log"] = room["log"][-30:]


# ---------------------------------------------------------------------------
# プレイヤーごとの「見える盤面」を作る（ここが審判ありの肝）
# ---------------------------------------------------------------------------

def view_for(room, seat):
    """seat のプレイヤーに送る、フィルタ済みの状態を作る。"""
    board_view = []
    for r in range(rules.ROWS):
        row_view = []
        for c in range(rules.COLS):
            cell = room["board"][r][c]
            if cell is None:
                row_view.append(None)
            elif cell["owner"] == seat:
                # 自分の駒 → 正体を見せる
                row_view.append({"owner": seat, "kind": cell["kind"], "mine": True})
            else:
                # 相手の駒 → 位置だけ。正体は絶対に送らない
                row_view.append({"owner": cell["owner"], "hidden": True})
        board_view.append(row_view)

    opponent = "B" if seat == "A" else "A"
    v = {
        "code": room["code"],
        "seat": seat,
        "phase": room["phase"],
        "turn": room["turn"],
        "ready": room["ready"],
        "winner": room["winner"],
        "resigned": room.get("resigned"),
        "log": room["log"],
        "last_move": room["last_move"],
        # 戦闘結果は「このプレイヤー視点」の文言だけ渡す（自分の駒名のみ・相手の駒名は伏せる）
        "last_battle": room.get("battle_view", {}).get(seat),
        "version": room["version"],
        "opponent_joined": room["players"][opponent] is not None,
        "board": board_view,
        "rows": rules.ROWS,
        "cols": rules.COLS,
        "home_cells": [list(c) for c in home_cells(seat)],
        # 地雷・軍旗を置けないマス（突入口の手前）。画面での配置ガード用。
        "no_immovable_cells": [list(c) for c in rules.gate_entry_cells(seat)],
        "piece_set": rules.PIECE_SET,
        # 盤の形（画面が川・橋・総司令部を描くために渡す）
        "border_row": rules.BORDER_ROW,
        "gate_cols": sorted(rules.GATE_COLS),
        "hq": {k: list(val) for k, val in rules.HQ.items()},
        "hq_phantom": {k: list(val) for k, val in rules.HQ_PHANTOM.items()},
    }
    # 感想戦の記録（正体つき）は、対戦が終わってからだけ両者に渡す。
    # 対戦中に渡すと相手の駒がバレるので、over のときに限定する。
    if room["phase"] == "over":
        v["history"] = room.get("history", [])
    return v


def seat_of(room, token):
    """合言葉(token)からどちらの席(A/B)か調べる。無ければ None。"""
    for seat, t in room["players"].items():
        if t and t == token:
            return seat
    return None


# ---------------------------------------------------------------------------
# 各操作の処理
# ---------------------------------------------------------------------------

def handle_join(code):
    room = rooms.get(code)
    if not room:
        dlog(f"JOIN 失敗: 部屋コード {code!r} が見つからない")
        return None, "その部屋コードは見つかりませんでした。"
    # すでにBが埋まっている場合の扱い。
    if room["players"]["B"] is not None:
        idle = time.time() - room["last_seen"].get("B", 0)
        dlog(f"JOIN 判定: 部屋 {code} の席B埋まり済み・B無通信 {idle:.1f}秒 (制限 {SEAT_TIMEOUT}秒)")
        # ★席の奪い合い防止：席Bに“今も通信している人”がいる間は、別人の横取り入室を禁止する。
        #   フェーズに関係なく満員扱いにする。これで「後から入った人が、先にいた人を弾いて
        #   切断させる」不具合を根本から防ぐ。
        #   一定時間(SEAT_TIMEOUT)まったく音沙汰がなければ＝本当に抜けたとみなし、空席として入れる。
        if idle < SEAT_TIMEOUT:
            return None, "その部屋はすでに満員です。"
        # 抜けたとみなして席Bを空ける。前の人の置きかけの駒は消しておく。
        for r in range(rules.ROWS):
            for c in range(rules.COLS):
                cell = room["board"][r][c]
                if cell is not None and cell["owner"] == "B":
                    room["board"][r][c] = None
    token = new_token()
    room["players"]["B"] = token
    room["last_seen"]["B"] = time.time()
    room["ready"]["B"] = False        # 入り直したら配置はやり直しから
    if room["phase"] in ("waiting", "setup"):
        room["phase"] = "setup"       # 2人そろったので配置フェーズへ
    bump(room)
    add_log(room, "対戦相手が入室しました。")
    dlog(f"JOIN 成功: 部屋 {code} の席B に入室 → phase={room['phase']}")
    return {"token": token, "seat": "B"}, None


def handle_setup(room, seat, placement):
    if room["phase"] not in ("setup",):
        return "いまは駒を配置できる場面ではありません。"
    if room["ready"][seat]:
        return "すでに配置は完了しています。"

    allowed = set(home_cells(seat))
    seen = set()
    kinds_count = {}
    # 入力の検証
    for p in placement:
        r, c, kind = p.get("row"), p.get("col"), p.get("kind")
        if not isinstance(r, int) or not isinstance(c, int):
            return "駒の位置が正しくありません。"
        if (r, c) not in allowed:
            return "自陣の中（総司令部を含む）に置いてください。"
        if (r, c) in seen:
            return "同じマスに2つ置くことはできません。"
        if kind not in rules.PIECE_SET:
            return "知らない駒があります。"
        if not rules.can_place(seat, kind, r, c):
            return "地雷・軍旗は、突入口（橋）の前のマスには置けません。"
        seen.add((r, c))
        kinds_count[kind] = kinds_count.get(kind, 0) + 1

    # 駒の枚数がぴったり一致するか
    if kinds_count != dict(rules.PIECE_SET):
        return "すべての駒をちょうど1組ずつ置いてください。"

    # 盤に配置
    for p in placement:
        room["board"][p["row"]][p["col"]] = {"owner": seat, "kind": p["kind"]}
    room["ready"][seat] = True
    add_log(room, f"{seat}軍の配置が完了しました。")

    # 両者そろったら対戦開始
    if room["ready"]["A"] and room["ready"]["B"]:
        room["phase"] = "play"
        # 先攻はA/Bランダムで決める（部屋を作った人が必ず先攻…にならないように）
        room["turn"] = random.choice(["A", "B"])
        # 感想戦の記録を開始（0手目＝配置直後の局面）
        room["history"] = [{"board": snapshot_board(room["board"]), "move": None, "battle": None}]
        add_log(room, f"対戦開始！ 先攻は{room['turn']}軍です。")
    bump(room)
    return None


def has_movable_piece(room, seat):
    """seat に、まだ動かせて、実際に動かせる先が1つでもある駒があるか。"""
    board = room["board"]
    for r in range(rules.ROWS):
        for c in range(rules.COLS):
            cell = board[r][c]
            if cell and cell["owner"] == seat and rules.is_movable(cell["kind"]):
                if rules.legal_destinations(board, seat, r, c):
                    return True
    return False


def flag_behind_kind(board, flag_owner, fr, fc):
    """軍旗のすぐ後ろ（持ち主の総司令部側）にある味方駒の種類。無ければ None。"""
    back = -rules.forward_dir(flag_owner)  # 前(相手側)の逆＝後ろ
    pos = rules.normalize(fr + back, fc)
    if pos:
        t = board[pos[0]][pos[1]]
        if t and t["owner"] == flag_owner:
            return t["kind"]
    return None


def handle_move(room, seat, frm, to):
    if room["phase"] != "play":
        return "いまは駒を動かせる場面ではありません。"
    if room["turn"] != seat:
        return "いまはあなたの手番ではありません。"

    fr, fc = frm.get("r"), frm.get("c")
    tr, tc = to.get("r"), to.get("c")
    for v in (fr, fc, tr, tc):
        if not isinstance(v, int):
            return "移動先の指定が正しくありません。"
    if not (rules.in_board(fr, fc) and rules.in_board(tr, tc)):
        return "盤の外は選べません。"

    board = room["board"]
    mover = board[fr][fc]
    if not mover or mover["owner"] != seat:
        return "自分の駒を選んでください。"
    if not rules.is_movable(mover["kind"]):
        return f"{mover['kind']}は動かせません。"

    # 駒の種類ごとの動きとして正しい移動先か
    if (tr, tc) not in rules.legal_destinations(board, seat, fr, fc):
        return "その駒はそこへは動かせません。"

    opponent = "B" if seat == "A" else "A"
    target = board[tr][tc]

    move_battle = None   # この1手で起きた戦闘の結果（感想戦の記録用。空き移動なら None）
    # 駒の種類は伏せるルールなので、ログ・戦闘結果に駒名は一切出さない（勝った軍だけ書く）
    if target is None:
        # 空きマスへ移動
        board[tr][tc] = mover
        board[fr][fc] = None
        add_log(room, f"{seat}軍 が動いた。")
    else:
        # 敵の駒とぶつかる → 審判が判定
        behind = None
        if target["kind"] == rules.FLAG:
            behind = flag_behind_kind(board, opponent, tr, tc)
        result = rules.resolve_battle(mover["kind"], target["kind"], behind)
        mk = mover["kind"]     # 自分（攻撃側 seat）の駒の種類
        tk = target["kind"]    # 相手（守備側 opponent）の駒の種類
        bv = room.setdefault("battle_view", {"A": None, "B": None})

        if result == "attacker":
            board[tr][tc] = mover
            board[fr][fc] = None
            room["last_battle"] = f"{seat}軍 の勝ち（{opponent}軍 の駒を取った）"
            # 各プレイヤーには「自分の駒名」だけを見せる（相手の駒名は伏せる）
            bv[seat] = f"{mk}で相手の駒を取りました"
            bv[opponent] = f"{tk}が相手の駒に取られました"
        elif result == "defender":
            board[fr][fc] = None
            room["last_battle"] = f"{opponent}軍 の勝ち（{seat}軍 の駒が取られた）"
            bv[seat] = f"{mk}が相手の駒に取られました"
            bv[opponent] = f"{tk}で相手の駒を取りました"
        else:  # both（相打ち）
            board[fr][fc] = None
            board[tr][tc] = None
            room["last_battle"] = "相打ち（両軍の駒が1つずつ取られた）"
            bv[seat] = f"{mk}が相手の駒と相打ちになりました"
            bv[opponent] = f"{tk}が相手の駒と相打ちになりました"
        add_log(room, "⚔ " + room["last_battle"])
        move_battle = room["last_battle"]

    room["last_move"] = {"from": [fr, fc], "to": [tr, tc]}
    # 感想戦の記録に、この1手を指したあとの局面を追加（勝敗が決まる手も含めて残す）
    room["history"].append({
        "board": snapshot_board(board),
        "move": {"from": [fr, fc], "to": [tr, tc]},
        "battle": move_battle,
    })

    # 勝敗チェック(1)：相手の総司令部を占領した
    lander = board[tr][tc]
    if lander and lander["owner"] == seat and (tr, tc) == rules.HQ[opponent] \
            and lander["kind"] in rules.CAN_CAPTURE_HQ:
        add_log(room, f"{seat}軍が{opponent}軍の総司令部を占領！ {seat}軍の勝ちです。")
        room["phase"] = "over"
        room["winner"] = seat
        bump(room)
        return None

    # 勝敗チェック(2)：相手が動かせる駒を失ったら勝ち
    if not has_movable_piece(room, opponent):
        add_log(room, f"{opponent}軍は動かせる駒がなくなりました。{seat}軍の勝ちです。")
        room["phase"] = "over"
        room["winner"] = seat
        bump(room)
        return None

    # 手番を交代
    room["turn"] = opponent
    bump(room)
    return None


def handle_leave(room, seat):
    """「退室」処理。ただし“対戦中の本人を巻き添えで切断しない”ことを最優先する。
    ここでは席のトークンも盤面も消さない（＝この操作で誰も強制退場させない）。
    本当に去った席は、一定時間ポーリングが来なければ last_seen により自然に空く。

    ※以前はここで席を None にしていたため、同じ席を握った別タブ等が1つでもあると、
      片方の退室で“まだ遊んでいる本人”が 403 を食らって切断される不具合があった。
      その根本対策として、退室を「席を即消す」から「離席ぎみと記録するだけ」に無害化する。"""
    dlog(f"LEAVE(無害化): 部屋 {room['code']} 席{seat} → 席・盤面は保持（last_seenのみ更新）")
    room["last_seen"][seat] = 0
    bump(room)


def handle_resign(room, seat):
    """投了（負けを認める）。押した本人の負けで対戦を終了する。"""
    if room["phase"] != "play":
        return "対戦中だけ投了できます。"
    opponent = "B" if seat == "A" else "A"
    room["phase"] = "over"
    room["winner"] = opponent
    room["resigned"] = seat
    add_log(room, f"{seat}軍が投了しました。{opponent}軍の勝ちです。")
    bump(room)
    return None


def handle_rematch(room):
    """もう一局。盤面を空にして配置フェーズからやり直す。"""
    room["board"] = empty_board()
    room["ready"] = {"A": False, "B": False}
    room["phase"] = "setup"
    room["turn"] = "A"
    room["winner"] = None
    room["resigned"] = None
    room["last_move"] = None
    room["last_battle"] = None
    room["battle_view"] = {"A": None, "B": None}
    room["history"] = []
    room["log"] = []
    add_log(room, "もう一局！ 駒を配置してください。")
    bump(room)


# ---------------------------------------------------------------------------
# HTTPサーバー本体
# ---------------------------------------------------------------------------

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
}
AUDIO_EXTS = {".mp3", ".m4a"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # アクセスログは静かに

    # --- 画面ファイル(HTML/CSS/JS)を返す ---
    def do_GET(self):
        path = self.path.split("?", 1)[0]

        if path == "/":
            path = "/index.html"
        # publicフォルダの中に限定（安全のため）
        safe = os.path.normpath(path).lstrip("/")
        full = os.path.join(PUBLIC_DIR, safe)
        if not full.startswith(PUBLIC_DIR) or not os.path.isfile(full):
            self.send_error(404, "Not Found")
            return
        ext = os.path.splitext(full)[1]
        ctype = CONTENT_TYPES.get(ext, "application/octet-stream")
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if ext in AUDIO_EXTS:
            # 音楽ファイルは大きいので、ブラウザに1日覚えさせて毎回読み直さない
            self.send_header("Cache-Control", "public, max-age=86400")
        else:
            # 画面ファイル(HTML/CSS/JS)は毎回最新を渡す（ブラウザに古い版を使い回させない）
            self.send_header("Cache-Control", "no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    # --- ゲーム操作(API)を処理する ---
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self.reply(400, {"error": "リクエストが正しくありません。"})
            return

        path = self.path.split("?", 1)[0]

        with lock:
            if path == "/api/create":
                code, token = create_room()
                dlog(f"CREATE 成功: 部屋 {code} を作成（席A）")
                self.reply(200, {"code": code, "token": token, "seat": "A"})
                return

            if path == "/api/join":
                dlog(f"JOIN 要求: code={ (data.get('code') or '').upper()!r }")
                result, err = handle_join((data.get("code") or "").upper())
                if err:
                    self.reply(400, {"error": err})
                else:
                    self.reply(200, result)
                return

            # ここから先は部屋と本人確認が必要
            code_up = (data.get("code") or "").upper()
            tok_head = str(data.get("token"))[:6]
            room = rooms.get(code_up)
            if not room:
                dlog(f"404 部屋なし: path={path} code={code_up!r} token={tok_head}…")
                self.reply(404, {"error": "部屋が見つかりません。"})
                return
            seat = seat_of(room, data.get("token"))
            if not seat:
                dlog(f"403 席不一致: path={path} code={code_up} token={tok_head}… "
                     f"A={str(room['players']['A'])[:6]}… B={str(room['players']['B'])[:6]}…")
                self.reply(403, {"error": "この部屋の参加者として確認できませんでした。"})
                return

            # この席は生きている、と記録（抜けた席の判定に使う）
            room["last_seen"][seat] = time.time()

            if path == "/api/state":
                self.reply(200, view_for(room, seat))
                return

            if path == "/api/setup":
                err = handle_setup(room, seat, data.get("placement") or [])
                if err:
                    self.reply(400, {"error": err})
                else:
                    self.reply(200, view_for(room, seat))
                return

            if path == "/api/move":
                err = handle_move(room, seat, data.get("from") or {}, data.get("to") or {})
                if err:
                    self.reply(400, {"error": err})
                else:
                    self.reply(200, view_for(room, seat))
                return

            if path == "/api/leave":
                handle_leave(room, seat)
                self.reply(200, {"ok": True})
                return

            if path == "/api/resign":
                err = handle_resign(room, seat)
                if err:
                    self.reply(400, {"error": err})
                else:
                    self.reply(200, view_for(room, seat))
                return

            if path == "/api/rematch":
                if room["phase"] != "over":
                    self.reply(400, {"error": "対戦が終わってから押してください。"})
                    return
                handle_rematch(room)
                self.reply(200, view_for(room, seat))
                return

        self.reply(404, {"error": "不明な操作です。"})

    def reply(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    # 本番(クラウド)では環境変数PORTが指定されることがあるので対応
    port = int(os.environ.get("PORT", PORT))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"軍人将棋サーバーを起動しました。")
    print(f"ブラウザで http://localhost:{port} を開いてください。")
    print("止めるときは Control + C を押してください。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nサーバーを止めました。")
        server.shutdown()


if __name__ == "__main__":
    main()
