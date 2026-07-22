# -*- coding: utf-8 -*-
"""
軍人将棋（本格ルール）の中核。盤・駒・動き・戦闘・勝敗をここで定義する。

このファイルを直せば、ルールを調整できます。

■ 盤の形について（大事なところ）
  - 6列 × 9行のマス目をベースにする。
  - 中央の行(4)は「川（国境）」。ここには駒は止まれない。
  - 川を渡れるのは2か所の「橋（突入口）」だけ。橋は通り道であって止まれない。
    駒は橋の手前マスから、川の向こう側のマスへ一気に1歩で渡る。
    （ヒコーキだけは橋がなくても、どこでも川を飛び越えられる）
  - 各軍のいちばん奥の中央に「総司令部」がある。見た目は横長だが“1マス”。
    そこに置ける駒は1つだけ。相手にそこを占領されたら負け。
"""

# ---------------------------------------------------------------------------
# 盤（6列 × 9行）
# ---------------------------------------------------------------------------
COLS = 6
ROWS = 9

BORDER_ROW = 4        # 川（国境）の行。ここには止まれない。
HOME_ROWS = 4         # 各軍の陣地の行数

# 橋（突入口）がある列。川はこの2列でだけ渡れる。
GATE_COLS = {1, 4}

# 総司令部（“1マス”）。見た目は横長だが、駒が入れるのはこの1マスだけ。
#   A軍は盤の下、B軍は盤の上。左右反転しても互いに同じ位置に見えるよう配置。
HQ = {"A": (8, 2), "B": (0, 3)}
# 総司令部の“横長の相方”マス。見た目上つながって見えるだけで、駒は入れない。
HQ_PHANTOM = {"A": (8, 3), "B": (0, 2)}
PHANTOM = set(HQ_PHANTOM.values())   # 駒を置けない“見た目だけ”のマス
# 総司令部がまたがる中央2列。総司令部の駒は、この左右どちらの列へも縦に出入りできる。
HQ_COLS = (2, 3)

# 総司令部を「占領」できる駒（これ以外がHQに入っても勝ちにならない）
CAN_CAPTURE_HQ = {"大将", "中将", "少将", "大佐", "中佐", "少佐"}


def in_board(r, c):
    return 0 <= r < ROWS and 0 <= c < COLS


def is_cell(r, c):
    """駒が“止まれる”マスなら True。川(行4)と、総司令部の相方マスは False。"""
    if not in_board(r, c):
        return False
    if r == BORDER_ROW:
        return False               # 川には止まれない
    if (r, c) in PHANTOM:
        return False               # 総司令部の“見た目だけ”マスには入れない
    return True


def _hq_of_row(r):
    """その行に総司令部の本体マスがあれば、その座標を返す。無ければ None。"""
    for _seat, (hr, hc) in HQ.items():
        if hr == r:
            return (hr, hc)
    return None


def normalize(r, c):
    """(r,c)が指す“本当のマス”を返す。総司令部の相方マスは本体に読み替える。
    止まれないマスなら None。"""
    if (r, c) in PHANTOM:
        return _hq_of_row(r)
    if is_cell(r, c):
        return (r, c)
    return None


def step(r, c, dr, dc):
    """(r,c)から上下左右のいずれか1歩進んだ“次のマス”を返す。
    ・川は橋の列でだけ渡れる（渡るときは川を飛び越して向こう側のマスへ）。
    ・総司令部の相方マスは本体マスに読み替える。
    進めない（盤外・壁・川で塞がれ）なら None。"""
    if dr == 0:
        # 横移動。相方マスに当たったら1つ飛ばして本体の隣へ。
        nr, nc = r, c + dc
        if (nr, nc) in PHANTOM:
            nc += dc
        return normalize(nr, nc)

    # 縦移動。
    nr, nc = r + dr, c
    if nr == BORDER_ROW:
        # 川に踏み込むところ → 橋の列でだけ、向こう側のマスへ渡れる。
        if c in GATE_COLS:
            return normalize(BORDER_ROW + dr, c)
        return None
    return normalize(nr, nc)


def is_hq_body(r, c):
    """(r,c) が総司令部の本体マスなら True。"""
    return (r, c) == HQ["A"] or (r, c) == HQ["B"]


def step_vert_multi(r, c, dr):
    """縦方向に1歩。総司令部の駒は中央2列（左前・右前）どちらへも出られるので最大2マス返す。"""
    cols = HQ_COLS if is_hq_body(r, c) else (c,)
    outs = []
    for cc in cols:
        nr = r + dr
        if nr == BORDER_ROW:
            p = normalize(BORDER_ROW + dr, cc) if cc in GATE_COLS else None
        else:
            p = normalize(nr, cc)
        if p and p != (r, c) and p not in outs:
            outs.append(p)
    return outs


def first_steps(r, c, dr, dc):
    """1歩目の候補（横＝1マス、縦＝総司令部なら最大2マス）。"""
    if dr == 0:
        p = step(r, c, 0, dc)
        return [p] if p else []
    return step_vert_multi(r, c, dr)


# ---------------------------------------------------------------------------
# 駒
# ---------------------------------------------------------------------------
# 階級（数字が大きいほど強い）
RANKS = {
    "大将": 9, "中将": 8, "少将": 7,
    "大佐": 6, "中佐": 5, "少佐": 4,
    "大尉": 3, "中尉": 2, "少尉": 1,
}
GENERALS = {"大将", "中将", "少将"}          # 将官
OFFICERS = set(RANKS.keys())                 # 大将〜少尉すべて

SPY = "スパイ"
MINE = "地雷"
FLAG = "軍旗"
ENGINEER = "工兵"
TANK = "タンク"
PLANE = "ヒコーキ"
CAVALRY = "騎兵"

IMMOVABLE = {MINE, FLAG}   # 動かせない駒

# 1人あたりの駒（種類: 枚数）＝合計23枚
PIECE_SET = {
    "大将": 1, "中将": 1, "少将": 1,
    "大佐": 1, "中佐": 1, "少佐": 1,
    "大尉": 2, "中尉": 2, "少尉": 2,
    CAVALRY: 1, SPY: 1, FLAG: 1,
    MINE: 2, ENGINEER: 2, TANK: 2, PLANE: 2,
}


def piece_list():
    """1人分の駒を1枚ずつ並べたリスト。"""
    out = []
    for kind, n in PIECE_SET.items():
        out.extend([kind] * n)
    return out


def is_movable(kind):
    return kind not in IMMOVABLE


def home_cells(seat):
    """その軍が駒を置ける自陣のマス一覧（総司令部を含む・相方マスは除く）。"""
    if seat == "A":
        rows = range(ROWS - HOME_ROWS, ROWS)   # 5,6,7,8
    else:
        rows = range(0, HOME_ROWS)             # 0,1,2,3
    return [(r, c) for r in rows for c in range(COLS) if is_cell(r, c)]


def forward_dir(seat):
    """その軍の『前（相手側）』へ進むときの行の増分。A軍は上(-1)、B軍は下(+1)。"""
    return -1 if seat == "A" else +1


def gate_entry_cells(seat):
    """突入口（橋）のすぐ手前マス＝自陣の最前列で、橋と同じ列のマス一覧。
    A軍は(5,1)(5,4)、B軍は(3,1)(3,4)。ここには地雷・軍旗を置けない。"""
    front = ROWS - HOME_ROWS if seat == "A" else HOME_ROWS - 1   # A=5, B=3
    return [(front, c) for c in sorted(GATE_COLS)]


def can_place(seat, kind, r, c):
    """配置フェーズで (r,c) に kind を置いてよいか。
    動かない駒（地雷・軍旗）は、突入口の手前マスに置けない（＝橋を塞げない）。"""
    if kind in IMMOVABLE and (r, c) in gate_entry_cells(seat):
        return False
    return True


# ---------------------------------------------------------------------------
# 動き：ある駒が動ける先マスの一覧
# ---------------------------------------------------------------------------
def legal_destinations(board, seat, fr, fc):
    """(fr,fc)にある seat の駒が動ける先マスの一覧。駒ごとの動き・川・橋・総司令部を反映。"""
    cell = board[fr][fc]
    if not cell or cell["owner"] != seat or not is_movable(cell["kind"]):
        return []
    kind = cell["kind"]

    def can_land(pos):
        # 空きマス、または敵の駒 → 着地できる
        if pos is None:
            return False
        r, c = pos
        t = board[r][c]
        return t is None or t["owner"] != seat

    def is_empty(pos):
        if pos is None:
            return False
        r, c = pos
        return board[r][c] is None

    dests = []
    fdir = forward_dir(seat)
    DIRS = ((-1, 0), (1, 0), (0, -1), (0, 1))

    if kind == PLANE:
        # ヒコーキ：縦は何マスでも（川・壁・駒を飛び越える）、横は1マス。
        # 総司令部にいるときは中央2列（左右）どちらも縦に飛べる。
        cols = HQ_COLS if is_hq_body(fr, fc) else (fc,)
        for cc in cols:
            for rr in range(ROWS):
                if rr == fr:
                    continue
                pos = normalize(rr, cc)
                if pos and pos != (fr, fc) and can_land(pos):
                    dests.append(pos)
        for dc in (-1, 1):
            pos = step(fr, fc, 0, dc)
            if can_land(pos):
                dests.append(pos)

    elif kind == ENGINEER:
        # 工兵：縦横に何マスでも（飛び越せない・壁や川で止まる）
        for dr, dc in DIRS:
            for start in first_steps(fr, fc, dr, dc):
                r, c = start
                t0 = board[r][c]
                if t0 is not None:
                    if t0["owner"] != seat:
                        dests.append(start)
                    continue
                dests.append(start)
                while True:
                    pos = step(r, c, dr, dc)
                    if pos is None:
                        break
                    t = board[pos[0]][pos[1]]
                    if t is None:
                        dests.append(pos)
                    elif t["owner"] != seat:
                        dests.append(pos)     # 敵を取れる。ここで止まる
                        break
                    else:
                        break                 # 自分の駒で塞がれている
                    r, c = pos

    elif kind in (TANK, CAVALRY):
        # タンク・騎兵：周囲1マス、または前に2マス（間が空いているとき）
        for dr, dc in DIRS:
            for pos in first_steps(fr, fc, dr, dc):
                if can_land(pos):
                    dests.append(pos)
        for one in first_steps(fr, fc, fdir, 0):
            if is_empty(one):
                two = step(one[0], one[1], fdir, 0)
                if can_land(two):
                    dests.append(two)

    else:
        # 将官・佐官・尉官・スパイ：上下左右に1マス
        for dr, dc in DIRS:
            for pos in first_steps(fr, fc, dr, dc):
                if can_land(pos):
                    dests.append(pos)

    # 重複を除く（総司令部への読み替えで同じマスが二重に入ることがある）
    uniq = []
    for p in dests:
        if p not in uniq:
            uniq.append(p)
    return uniq


# ---------------------------------------------------------------------------
# 戦闘：まず通常駒同士の勝敗 fight(a, b)
# ---------------------------------------------------------------------------
def _beats(x, y):
    """通常駒 x が y に一方的に勝つなら True（相打ち・負けは False）。"""
    # スパイは大将にだけ勝つ
    if x == SPY:
        return y == "大将"
    if y == SPY:
        # 相手がスパイ → 大将以外は勝つ（大将はスパイに負ける）
        return x != "大将"

    # ヒコーキ・タンク vs 階級
    if x == PLANE:
        return y in ({"大佐", "中佐", "少佐", "大尉", "中尉", "少尉"} | {TANK, CAVALRY, ENGINEER})
    if x == TANK:
        return y in ({"大佐", "中佐", "少佐", "大尉", "中尉", "少尉"} | {CAVALRY, ENGINEER})
    if y == PLANE:
        return x in GENERALS
    if y == TANK:
        return x in GENERALS or x == PLANE

    # 騎兵・工兵
    if x == CAVALRY:
        return y == ENGINEER            # 騎兵は工兵に勝つ（スパイは上で処理済み）
    if y == CAVALRY:
        return x in OFFICERS or x in (TANK, PLANE)
    if x == ENGINEER:
        return False                    # 工兵は通常駒ではスパイにしか勝てない（上で処理済み）
    if y == ENGINEER:
        return x in OFFICERS or x in (TANK, PLANE, CAVALRY)

    # 階級同士
    if x in RANKS and y in RANKS:
        return RANKS[x] > RANKS[y]
    return False


def fight(a, b):
    """
    通常駒 a（攻撃）と b（守備）がぶつかったときの結果。
    戻り値: "attacker" / "defender" / "both"（相打ち）
    """
    ab = _beats(a, b)
    ba = _beats(b, a)
    if ab and not ba:
        return "attacker"
    if ba and not ab:
        return "defender"
    return "both"   # 同格・同種など


def resolve_battle(attacker, defender, behind_kind=None):
    """
    攻撃側 attacker が、守備マスの defender にぶつかったときの審判結果。
    behind_kind: 守備が軍旗のとき、その『すぐ後ろ』にある味方駒の種類（無ければ None）。
    戻り値: "attacker" / "defender" / "both"
    """
    # --- 地雷（守備・不動）---
    if defender == MINE:
        # 工兵・ヒコーキは一方的に勝つ（生き残る）
        if attacker in (ENGINEER, PLANE):
            return "attacker"
        # それ以外は相打ち（地雷は一度きりの罠）
        return "both"

    # --- 軍旗（守備・不動）---
    if defender == FLAG:
        if behind_kind is None:
            # 後ろに駒が無ければ無力 → 攻撃側が奪う
            return "attacker"
        # 後ろの駒の強さで戦う（軍旗自身が勝敗を負う）
        if behind_kind == MINE:
            # 後ろが地雷なら地雷の判定を借りる
            return resolve_battle(attacker, MINE)
        return fight(attacker, behind_kind)

    # --- 通常駒同士 ---
    return fight(attacker, defender)
