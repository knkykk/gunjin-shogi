# -*- coding: utf-8 -*-
"""
相手の駒の「候補」を絞り込む（推理）。

各プレイヤーが対戦中に見て分かることだけを使う。
  - 相手の駒の位置と、どう動いたか（駒の正体は見えない）
  - 戦闘で、自分のどの駒とぶつかって、勝ったか・負けたか・相打ちか
  - 配置のとき、地雷・軍旗は突入口のマスに置けないこと

ひとつひとつの出来事について「相手の駒が何だったら、この結果・この動きになるか」を
審判と同じ判定（rules.py）に全種類当てはめ、つじつまの合う種類だけを残す。
そのうえで「駒の枚数はちょうど決まっている」ことを使って、ほかの駒の情報からも絞る
（例：別の駒が大将に決まっていれば、この駒は大将ではない）。

記録の形（部屋ごと）：
  room["intel"][見る側の席][相手の駒の番号] = [{"text": 理由の文, "allowed": [残る種類]}, ...]
相手の駒の番号は配置のときに振る通し番号で、画面には送らない（盤の位置で引き当てて渡す）。
"""

import rules


def _flip(seat):
    return "B" if seat == "A" else "A"


def start(room, R):
    """対戦開始のとき：駒に番号を振り、記録を空にして、配置から分かることを書く。"""
    n = 0
    room["piece_ids"] = {"A": [], "B": []}
    room["intel"] = {"A": {}, "B": {}}
    for r in range(R.ROWS):
        for c in range(R.COLS):
            cell = room["board"][r][c]
            if cell:
                n += 1
                cell["id"] = n
                room["piece_ids"][cell["owner"]].append(n)
    # 突入口のマスに最初からいる駒は、地雷でも軍旗でもない
    for owner in ("A", "B"):
        viewer = _flip(owner)
        for (r, c) in R.gate_entry_cells(owner):
            cell = room["board"][r][c]
            if cell and cell["owner"] == owner:
                allowed = [k for k in R.PIECE_SET if k not in rules.IMMOVABLE]
                _add(room, viewer, cell["id"], "最初に突入口のマスに置かれていた", allowed)


def _add(room, viewer, pid, text, allowed):
    room["intel"][viewer].setdefault(pid, []).append({"text": text, "allowed": list(allowed)})


def _with_kind(board, r, c, kind):
    """(r,c) の駒の種類だけを kind に差し替えた盤（動きの判定用。ほかの駒の種類は動きに関係しない）。"""
    b = [row[:] for row in board]
    b[r][c] = dict(board[r][c], kind=kind)
    return b


def _move_text(R, seat, fr, fc, tr, tc, board):
    """動きを、起きた順番に関係ない事実の文にする（「動いた」「前に2マス動いた」など）。"""
    dr, dc = tr - fr, tc - fc
    hq_move = R.is_hq_body(fr, fc) or R.is_hq_body(tr, tc)
    if dc == 0 or (hq_move and dr != 0):
        # 縦の動き。間に駒があれば飛び越えた
        step = 1 if dr > 0 else -1
        cols = R.HQ_COLS if R.is_hq_body(fr, fc) else (fc,)
        col = tc if tc in cols else fc
        if any(board[r][col] for r in range(fr + step, tr, step)):
            return "駒を飛び越えて動いた"
        crosses = min(fr, tr) < R.BORDER_ROW < max(fr, tr)
        if crosses and not (col in R.GATE_COLS):
            return "川を飛び越えて動いた"
        n = abs(dr) - (1 if crosses else 0)
        if n <= 1:
            return "動いた"
        word = "前" if (dr > 0) == (R.forward_dir(seat) > 0) else "後ろ"
        return f"{word}に{n}マス動いた"
    if dr == 0:
        n = abs(dc)
        lo, hi = min(fc, tc), max(fc, tc)
        n -= sum(1 for c in range(lo + 1, hi) if (fr, c) in R.PHANTOM)
        return "動いた" if n <= 1 else f"横に{n}マス動いた"
    return "動いた"   # 白い丸と突入口のあいだ（斜めの1歩）


def record_move(room, R, seat, fr, fc, tr, tc):
    """seat の駒が (fr,fc)→(tr,tc) に動く直前に呼ぶ。相手側の記録に「この動きができる種類」を書く。"""
    board = room["board"]
    cell = board[fr][fc]
    allowed = []
    for k in R.PIECE_SET:
        if (tr, tc) in R.legal_destinations(_with_kind(board, fr, fc, k), seat, fr, fc):
            allowed.append(k)
    _add(room, _flip(seat), cell["id"], _move_text(R, seat, fr, fc, tr, tc, board), allowed)


def _outcome_word(enemy_won, both):
    if both:
        return "と相打ちになった"
    return "に勝った" if enemy_won else "に負けた"


def record_battle(room, R, seat, fr, fc, tr, tc, result):
    """戦闘の直後（盤を書き換える前）に呼ぶ。両方の側の記録に、相手の駒について分かったことを書く。
    seat が攻めた側。result は rules.resolve_battle の戻り値。"""
    board = room["board"]
    att, dfn = board[fr][fc], board[tr][tc]
    opponent = _flip(seat)
    both = result == "both"

    # 攻めた側（seat）から見た、守った相手の駒
    # 守った駒が軍旗だった場合、強さは「すぐ後ろの駒」で決まる。後ろの駒の種類は見えないので、
    # 後ろにマスの駒がいれば「どの駒でもありうる」、いなければ「無力」として判定する。
    back = -R.forward_dir(opponent)
    bpos = R.normalize(tr + back, tc)
    bcell = board[bpos[0]][bpos[1]] if bpos else None
    behind_options = [None]
    if bcell and bcell["owner"] == opponent:
        behind_options = [k for k in R.PIECE_SET if k != rules.FLAG]
    allowed = []
    for k in R.PIECE_SET:
        opts = behind_options if k == rules.FLAG else [None]
        if any(rules.resolve_battle(att["kind"], k, b) == result for b in opts):
            allowed.append(k)
    enemy_won = result == "defender"
    _add(room, seat, dfn["id"], "あなたの" + att["kind"] + _outcome_word(enemy_won, both), allowed)

    # 守った側（opponent）から見た、攻めてきた相手の駒。自分の軍旗の後ろの駒は自分で分かる。
    my_behind = None
    if dfn["kind"] == rules.FLAG and bcell and bcell["owner"] == opponent:
        my_behind = bcell["kind"]
    allowed = [k for k in R.PIECE_SET
               if rules.is_movable(k) and rules.resolve_battle(k, dfn["kind"], my_behind) == result]
    enemy_won = result == "attacker"
    _add(room, opponent, att["id"], "あなたの" + dfn["kind"] + _outcome_word(enemy_won, both), allowed)


def _own_candidates(R, facts):
    cands = list(R.PIECE_SET)
    for f in facts:
        cands = [k for k in cands if k in f["allowed"]]
    return cands


def _possible_with_counts(R, ids, cands):
    """駒の枚数がちょうど決まっていることを使って、各駒の候補をさらに絞る。
    「この駒がこの種類だとしても、残りの駒すべてに矛盾なく種類を割り当てられるか」を調べ、
    割り当てられる種類だけを残す。"""
    kinds = list(R.PIECE_SET)
    cap = dict(R.PIECE_SET)
    # 1. まず矛盾のない割り当てを1つ作る（枚数つきの二部マッチング）
    assign = {}
    load = {k: [] for k in kinds}

    def place(p, current, seen):
        """p を current 以外の種類に置く。満杯の種類なら、そこにいる駒を別の種類へ押し出せるか試す。"""
        for k in cands[p]:
            if k == current or k in seen:
                continue
            seen.add(k)
            if len(load[k]) < cap[k]:
                load[k].append(p); assign[p] = k
                return True
            for q in list(load[k]):
                if place(q, k, seen):
                    load[k].remove(q); load[k].append(p); assign[p] = k
                    return True
        return False

    for p in ids:
        if not place(p, None, set()):
            return cands   # 割り当てが作れない（起こらないはず）。各駒の候補のまま返す
    # 2. 種類 a → b の矢印：a に割り当てた駒のどれかが b にもなれる
    edges = {k: set() for k in kinds}
    for q in ids:
        for k in cands[q]:
            if k != assign[q]:
                edges[assign[q]].add(k)
    # 空きのある種類（枚数より割り当てが少ない）。ここに行き着ければ押し出せる
    free = {k for k in kinds if len(load[k]) < cap[k]}

    def reach(src):
        seen = {src}; stack = [src]
        while stack:
            a = stack.pop()
            for b in edges[a]:
                if b not in seen:
                    seen.add(b); stack.append(b)
        return seen

    reach_of = {k: reach(k) for k in kinds}
    out = {}
    for p in ids:
        k0 = assign[p]
        # p を k にする → k にいた誰かが押し出され、矢印をたどって最後に k0（p が空けた枠）か空き枠に収まればよい
        out[p] = [k for k in cands[p]
                  if k == k0 or k0 in reach_of[k] or (reach_of[k] & free)]
    return out


def view(room, R, viewer):
    """viewer に渡す、盤の上の相手の駒それぞれの候補と理由。{"r,c": {...}}。
    候補が全種類のまま（何も分かっていない）駒は入れない。"""
    if "intel" not in room:
        return {}
    enemy = _flip(viewer)
    facts = room["intel"][viewer]
    ids = room["piece_ids"][enemy]
    own = {p: _own_candidates(R, facts.get(p, [])) for p in ids}
    full = _possible_with_counts(R, ids, own)
    all_kinds = list(R.PIECE_SET)
    out = {}
    for r in range(R.ROWS):
        for c in range(R.COLS):
            cell = room["board"][r][c]
            if not cell or cell["owner"] != enemy:
                continue
            p = cell["id"]
            cands = full[p]
            if cands == all_kinds:
                continue
            reasons = []
            for f in facts.get(p, []):
                if f["text"] not in reasons:
                    reasons.append(f["text"])
            dropped = [k for k in own[p] if k not in cands]
            if dropped:
                reasons.append("ほかの駒の情報から外れた：" + "・".join(dropped))
            out[f"{r},{c}"] = {"candidates": cands, "reasons": reasons}
    return out
