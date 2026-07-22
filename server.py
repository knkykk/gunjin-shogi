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
        "log": [],
        "last_move": None,
        "last_battle": None,
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
    return {
        "code": room["code"],
        "seat": seat,
        "phase": room["phase"],
        "turn": room["turn"],
        "ready": room["ready"],
        "winner": room["winner"],
        "log": room["log"],
        "last_move": room["last_move"],
        "last_battle": room["last_battle"],
        "version": room["version"],
        "opponent_joined": room["players"][opponent] is not None,
        "board": board_view,
        "rows": rules.ROWS,
        "cols": rules.COLS,
        "home_cells": [list(c) for c in home_cells(seat)],
        "piece_set": rules.PIECE_SET,
        # 盤の形（画面が川・橋・総司令部を描くために渡す）
        "border_row": rules.BORDER_ROW,
        "gate_cols": sorted(rules.GATE_COLS),
        "hq": {k: list(v) for k, v in rules.HQ.items()},
        "hq_phantom": {k: list(v) for k, v in rules.HQ_PHANTOM.items()},
    }


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
        dlog(f"JOIN 満員判定: 部屋 {code} の席B埋まり済み・B無通信 {idle:.1f}秒 (制限 {SEAT_TIMEOUT}秒)")
        # 対戦がもう始まっている部屋だけは、横取りを防ぐため満席にする。
        # （ただし相手が長時間 音沙汰なしなら、抜けたとみなして空席にする）
        if room["phase"] in ("play", "over") and idle < SEAT_TIMEOUT:
            return None, "その部屋はすでに満員です。"
        # まだ準備中（waiting / setup）なら、同じ人が二度押し・入り直しをしても
        # 弾かずに席を渡し直す。これで「入れたのにボタンを二度押して“満員”」が起きない。
        # 前に置きかけた駒が残っていると混乱するので、Bの駒と準備状態は一度きれいにする。
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
        room["turn"] = "A"
        add_log(room, "対戦開始！ A軍の手番です。")
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

        if result == "attacker":
            board[tr][tc] = mover
            board[fr][fc] = None
            room["last_battle"] = f"{seat}軍 の勝ち（{opponent}軍 の駒を取った）"
            add_log(room, "⚔ " + room["last_battle"])
        elif result == "defender":
            board[fr][fc] = None
            room["last_battle"] = f"{opponent}軍 の勝ち（{seat}軍 の駒が取られた）"
            add_log(room, "⚔ " + room["last_battle"])
        else:  # both（相打ち）
            board[fr][fc] = None
            board[tr][tc] = None
            room["last_battle"] = "相打ち（両軍の駒が1つずつ取られた）"
            add_log(room, "⚔ " + room["last_battle"])

    room["last_move"] = {"from": [fr, fc], "to": [tr, tc]}

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
    """その席のプレイヤーが部屋を出る。席をすぐ空けて、相手が入り直せるようにする。"""
    dlog(f"LEAVE: 部屋 {room['code']} の席{seat} が退室 → phase={room['phase']}")
    room["players"][seat] = None
    room["last_seen"][seat] = 0
    room["ready"][seat] = False
    # 配置中・対戦中に抜けたら、部屋を「相手待ち」に戻す（残った人がやり直せる）
    if room["phase"] in ("setup", "play", "over"):
        room["phase"] = "waiting"
        room["board"] = empty_board()
        room["ready"] = {"A": False, "B": False}
        room["turn"] = "A"
        room["winner"] = None
        room["last_move"] = None
        room["last_battle"] = None
        add_log(room, f"{seat}軍が退室しました。もう一度そろうと再開できます。")
    bump(room)


def handle_rematch(room):
    """もう一局。盤面を空にして配置フェーズからやり直す。"""
    room["board"] = empty_board()
    room["ready"] = {"A": False, "B": False}
    room["phase"] = "setup"
    room["turn"] = "A"
    room["winner"] = None
    room["last_move"] = None
    room["last_battle"] = None
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
}


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
