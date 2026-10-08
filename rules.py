# -*- coding: utf-8 -*-
"""
軍人将棋（本格ルール）の中核。盤・駒・動き・戦闘・勝敗をここで定義する。

このファイルを直せば、ルールを調整できます。

■ 盤には2つの版がある（部屋を作るときに選ぶ）
  【標準】6列×9行・各軍23枚。
    - 中央の行(4)は「川（国境）」。ここには駒は止まれない。
    - 川を渡れるのは2か所の「橋（突入口）」だけ。橋は通り道であって止まれない。
      駒は橋の手前マスから、川の向こう側のマスへ一気に1歩で渡る。
  【大型】8列×9行・各軍31枚。昔の「大型行軍将棋」（8列31枚・X字型）の盤。
    - 中央の行(4)に「白い丸」が2つある（左から2列目と7列目）。丸は駒が止まれるマス。
    - 左の丸は、両軍の1列目と3列目の最前列マス（突入口）とつながる。
      右の丸は、両軍の6列目と8列目の最前列マスとつながる。
    - 川を渡るには、突入口のマス → 丸 → 向こう側の突入口のマス、と必ず丸で一回止まる。
      丸からは左右どちらの突入口へ出てもよい。
    - 丸への出入りは、どの駒も「隣の突入口のマスからの1歩」だけ。
      工兵の滑りも、タンク・騎兵の「前に2マス」も、丸をまたいだり丸に飛び込んだりはできない。
    - 丸にいる駒は攻撃できる。丸に何手いてもよい。
  どちらの版も、ヒコーキだけは橋も丸も関係なく、どこでも川を飛び越えられる。
  各軍のいちばん奥の中央に「総司令部」がある。見た目は横長だが“1マス”。
  そこに置ける駒は1つだけ。相手にそこを占領されたら負け。
"""


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

# 総司令部を「占領」できる駒（これ以外がHQに入っても勝ちにならない）
CAN_CAPTURE_HQ = {"大将", "中将", "少将", "大佐", "中佐", "少佐"}

# 1人あたりの駒（種類: 枚数）
PIECE_SET_STANDARD = {                      # 合計23枚
    "大将": 1, "中将": 1, "少将": 1,
    "大佐": 1, "中佐": 1, "少佐": 1,
    "大尉": 2, "中尉": 2, "少尉": 2,
    CAVALRY: 1, SPY: 1, FLAG: 1,
    MINE: 2, ENGINEER: 2, TANK: 2, PLANE: 2,
}
PIECE_SET_LARGE = {                         # 合計31枚（大型行軍将棋）
    "大将": 1, "中将": 1, "少将": 2,
    "大佐": 2, "中佐": 2, "少佐": 2,
    "大尉": 2, "中尉": 2, "少尉": 2,
    CAVALRY: 2, SPY: 1, FLAG: 1,
    MINE: 3, ENGINEER: 3, TANK: 3, PLANE: 2,
}


def is_movable(kind):
    return kind not in IMMOVABLE


# ---------------------------------------------------------------------------
# 盤の形（標準・大型）
# ---------------------------------------------------------------------------
class Variant:
    """盤の形と駒の枚数をひとまとめにしたもの。標準と大型の2つを作る。"""

    def __init__(self, key, name, cols, piece_set, hq_cols, gate_cols=(), circle_cols=()):
        self.key = key                  # "standard" / "large"
        self.name = name                # 画面に出す名前
        self.COLS = cols
        self.ROWS = 9
        self.BORDER_ROW = 4             # 川（国境）の行
        self.HOME_ROWS = 4              # 各軍の陣地の行数
        self.PIECE_SET = dict(piece_set)
        # 標準：橋（突入口）がある列。川はこの列でだけ渡れる。
        self.GATE_COLS = set(gate_cols)
        # 大型：白い丸。{丸のマス: つながる突入口のマス4つ}
        self.CIRCLES = {}
        for c in circle_cols:
            self.CIRCLES[(self.BORDER_ROW, c)] = [
                (self.BORDER_ROW - 1, c - 1), (self.BORDER_ROW - 1, c + 1),
                (self.BORDER_ROW + 1, c - 1), (self.BORDER_ROW + 1, c + 1),
            ]
        # 総司令部（“1マス”）。見た目は横長だが、駒が入れるのはこの1マスだけ。
        #   A軍は盤の下、B軍は盤の上。左右反転しても互いに同じ位置に見えるよう配置。
        self.HQ_COLS = tuple(hq_cols)   # 総司令部がまたがる中央2列
        self.HQ = {"A": (self.ROWS - 1, hq_cols[0]), "B": (0, hq_cols[1])}
        self.HQ_PHANTOM = {"A": (self.ROWS - 1, hq_cols[1]), "B": (0, hq_cols[0])}
        self.PHANTOM = set(self.HQ_PHANTOM.values())

    # ---- マスの性質 ----
    def in_board(self, r, c):
        return 0 <= r < self.ROWS and 0 <= c < self.COLS

    def is_circle(self, r, c):
        return (r, c) in self.CIRCLES

    def is_cell(self, r, c):
        """駒が“止まれる”マスなら True。川（白い丸は除く）と、総司令部の相方マスは False。"""
        if not self.in_board(r, c):
            return False
        if r == self.BORDER_ROW:
            return self.is_circle(r, c)   # 川には止まれない。白い丸だけは止まれる。
        if (r, c) in self.PHANTOM:
            return False
        return True

    def _hq_of_row(self, r):
        for _seat, (hr, hc) in self.HQ.items():
            if hr == r:
                return (hr, hc)
        return None

    def normalize(self, r, c):
        """(r,c)が指す“本当のマス”を返す。総司令部の相方マスは本体に読み替える。
        止まれないマスなら None。"""
        if (r, c) in self.PHANTOM:
            return self._hq_of_row(r)
        if self.is_cell(r, c):
            return (r, c)
        return None

    def is_hq_body(self, r, c):
        return (r, c) == self.HQ["A"] or (r, c) == self.HQ["B"]

    def circle_of_gate(self, r, c):
        """(r,c)が白い丸につながる突入口のマスなら、その丸の座標。違えば None。"""
        for circle, gates in self.CIRCLES.items():
            if (r, c) in gates:
                return circle
        return None

    # ---- 1歩の動き ----
    def _vert_from(self, r, c, dr):
        """(r,c)から縦に1歩進んだ先（1マス）。川に踏み込むなら、橋か丸の決まりに従う。"""
        nr = r + dr
        if nr == self.BORDER_ROW:
            if c in self.GATE_COLS:                       # 標準：橋で向こう岸へ
                return self.normalize(self.BORDER_ROW + dr, c)
            return self.circle_of_gate(r, c)              # 大型：突入口のマスから丸へ
        return self.normalize(nr, c)

    def step(self, r, c, dr, dc):
        """(r,c)から上下左右のいずれか1歩進んだ“次のマス”を返す（工兵の滑りや
        タンクの2歩目の“続き”に使う）。白い丸からの続きの1歩は無い（丸の出入りは
        first_steps で扱う）。進めないなら None。"""
        if r == self.BORDER_ROW:
            return None
        if dr == 0:
            nr, nc = r, c + dc
            if (nr, nc) in self.PHANTOM:                  # 相方マスは1つ飛ばして本体の隣へ
                nc += dc
            return self.normalize(nr, nc)
        return self._vert_from(r, c, dr)

    def first_steps(self, r, c, dr, dc):
        """1歩目の候補の一覧。横＝1マス。縦＝総司令部なら中央2列へ最大2マス、
        白い丸からはその方向の突入口2マス。"""
        if self.is_circle(r, c):
            if dr == 0:
                return []
            return [g for g in self.CIRCLES[(r, c)] if g[0] == r + dr]
        if dr == 0:
            p = self.step(r, c, 0, dc)
            return [p] if p else []
        cols = self.HQ_COLS if self.is_hq_body(r, c) else (c,)
        outs = []
        for cc in cols:
            p = self._vert_from(r, cc, dr)
            if p and p != (r, c) and p not in outs:
                outs.append(p)
        return outs

    # ---- 陣地・配置 ----
    def piece_list(self):
        out = []
        for kind, n in self.PIECE_SET.items():
            out.extend([kind] * n)
        return out

    def total_pieces(self):
        return sum(self.PIECE_SET.values())

    def home_cells(self, seat):
        """その軍が駒を置ける自陣のマス一覧（総司令部を含む・相方マスは除く）。"""
        if seat == "A":
            rows = range(self.ROWS - self.HOME_ROWS, self.ROWS)   # 5,6,7,8
        else:
            rows = range(0, self.HOME_ROWS)                       # 0,1,2,3
        return [(r, c) for r in rows for c in range(self.COLS) if self.is_cell(r, c)]

    @staticmethod
    def forward_dir(seat):
        """その軍の『前（相手側）』へ進むときの行の増分。A軍は上(-1)、B軍は下(+1)。"""
        return -1 if seat == "A" else +1

    def gate_entry_cells(self, seat):
        """突入口のマス＝自陣の最前列で、橋（標準）か丸（大型）につながるマス一覧。
        ここには地雷・軍旗を置けない。"""
        front = self.ROWS - self.HOME_ROWS if seat == "A" else self.HOME_ROWS - 1   # A=5, B=3
        cols = set(self.GATE_COLS)
        for gates in self.CIRCLES.values():
            cols.update(c for (r, c) in gates if r == front)
        return [(front, c) for c in sorted(cols)]

    def can_place(self, seat, kind, r, c):
        """配置フェーズで (r,c) に kind を置いてよいか。
        動かない駒（地雷・軍旗）は、突入口のマスに置けない（＝通り道を塞げない）。"""
        if kind in IMMOVABLE and (r, c) in self.gate_entry_cells(seat):
            return False
        return True

    def geometry(self):
        """画面が盤を描くために渡す、盤の形のデータ。"""
        return {
            "variant": self.key,
            "variant_name": self.name,
            "rows": self.ROWS,
            "cols": self.COLS,
            "border_row": self.BORDER_ROW,
            "gate_cols": sorted(self.GATE_COLS),
            "circles": [list(k) for k in self.CIRCLES.keys()],
            "circle_links": {f"{k[0]},{k[1]}": [list(g) for g in v] for k, v in self.CIRCLES.items()},
            "hq": {k: list(val) for k, val in self.HQ.items()},
            "hq_phantom": {k: list(val) for k, val in self.HQ_PHANTOM.items()},
            "hq_cols": list(self.HQ_COLS),
            "piece_set": self.PIECE_SET,
            "total_pieces": self.total_pieces(),
        }

    # ---- 動き：ある駒が動ける先マスの一覧 ----
    def legal_destinations(self, board, seat, fr, fc):
        """(fr,fc)にある seat の駒が動ける先マスの一覧。駒ごとの動き・川・橋・丸・総司令部を反映。"""
        cell = board[fr][fc]
        if not cell or cell["owner"] != seat or not is_movable(cell["kind"]):
            return []
        kind = cell["kind"]

        def can_land(pos):
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
        fdir = self.forward_dir(seat)
        DIRS = ((-1, 0), (1, 0), (0, -1), (0, 1))

        if kind == PLANE:
            # ヒコーキ：縦は何マスでも（川・壁・駒を飛び越える）、横は1マス。
            # 総司令部にいるときは中央2列（左右）どちらも縦に飛べる。
            cols = self.HQ_COLS if self.is_hq_body(fr, fc) else (fc,)
            for cc in cols:
                for rr in range(self.ROWS):
                    if rr == fr:
                        continue
                    pos = self.normalize(rr, cc)
                    if pos and pos != (fr, fc) and can_land(pos):
                        dests.append(pos)
            for dc in (-1, 1):
                pos = self.step(fr, fc, 0, dc)
                if can_land(pos):
                    dests.append(pos)

        elif kind == ENGINEER:
            # 工兵：縦横に何マスでも（飛び越せない・壁や川で止まる）。
            # 白い丸への出入りは隣の突入口のマスからの1歩だけ（滑りの途中で丸には入れない）。
            for dr, dc in DIRS:
                for start in self.first_steps(fr, fc, dr, dc):
                    r, c = start
                    t0 = board[r][c]
                    if t0 is not None:
                        if t0["owner"] != seat:
                            dests.append(start)
                        continue
                    dests.append(start)
                    if self.is_circle(fr, fc):
                        continue                  # 丸から出るのは1歩だけ（滑り出せない）
                    while True:
                        pos = self.step(r, c, dr, dc)
                        if pos is None or self.is_circle(*pos):
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
            # タンク・騎兵：周囲1マス、または前に2マス（間が空いているとき）。
            # 2マス目が白い丸になる動きはできない（丸へは1歩でしか入れない）。
            for dr, dc in DIRS:
                for pos in self.first_steps(fr, fc, dr, dc):
                    if can_land(pos):
                        dests.append(pos)
            if not self.is_circle(fr, fc):        # 丸から出るのは1歩だけ
                for one in self.first_steps(fr, fc, fdir, 0):
                    if is_empty(one):
                        two = self.step(one[0], one[1], fdir, 0)
                        if two and not self.is_circle(*two) and can_land(two):
                            dests.append(two)

        else:
            # 将官・佐官・尉官・スパイ：上下左右に1マス
            for dr, dc in DIRS:
                for pos in self.first_steps(fr, fc, dr, dc):
                    if can_land(pos):
                        dests.append(pos)

        # 重複を除く（総司令部への読み替えで同じマスが二重に入ることがある）
        uniq = []
        for p in dests:
            if p not in uniq:
                uniq.append(p)
        return uniq


STANDARD = Variant("standard", "標準", cols=6, piece_set=PIECE_SET_STANDARD,
                   hq_cols=(2, 3), gate_cols=(1, 4))
LARGE = Variant("large", "大型", cols=8, piece_set=PIECE_SET_LARGE,
                hq_cols=(3, 4), circle_cols=(1, 6))
VARIANTS = {"standard": STANDARD, "large": LARGE}
DEFAULT_VARIANT = "standard"


def get_variant(key):
    """部屋に保存した版の名前から Variant を返す。知らない名前なら標準。"""
    return VARIANTS.get(key, STANDARD)


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
