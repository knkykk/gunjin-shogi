# -*- coding: utf-8 -*-
"""
相手の駒について「見て分かったこと」と「自分で書いたメモ」を、駒ごとに覚えておく。

候補を絞り込んだり正体を当てたりはしない（予想する面白さを残すため。2026-10-10にrenが決めた）。
覚えるのは、その駒について実際に起きたことだけ。
  - 戦闘：自分のどの駒に勝ったか（負けた・相打ちの駒は盤から消えるので残らない）
  - 動き：「動いた」「前に2マス動いた」「駒を飛び越えて動いた」など、盤を見ていれば分かる動き方
  - メモ：プレイヤーが自分で書いたもの。相手には送らない

記録の形（部屋ごと）：
  room["intel"][見る側の席][相手の駒の番号] = {"wins": [勝った相手の自分の駒], "moves": [動き方の文]}
  room["memos"][見る側の席][相手の駒の番号] = メモの文
相手の駒の番号は配置のときに振る通し番号で、画面には送らない（盤の位置で引き当てて渡す）。
駒が動くと盤の上の位置は変わるが、番号は駒と一緒に動くので、記録もメモも駒に付いていく。
"""

MEMO_MAX = 40   # メモの最大の文字数


def _flip(seat):
    return "B" if seat == "A" else "A"


def start(room, R):
    """対戦開始のとき：駒に番号を振り、記録とメモを空にする。"""
    n = 0
    room["intel"] = {"A": {}, "B": {}}
    room["memos"] = {"A": {}, "B": {}}
    for r in range(R.ROWS):
        for c in range(R.COLS):
            cell = room["board"][r][c]
            if cell:
                n += 1
                cell["id"] = n


def _entry(room, viewer, pid):
    return room["intel"][viewer].setdefault(pid, {"wins": [], "moves": []})


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
        if crosses and col not in R.GATE_COLS:
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
    """seat の駒が (fr,fc)→(tr,tc) に動く直前に呼ぶ。相手側の記録に動き方を書く。"""
    board = room["board"]
    text = _move_text(R, seat, fr, fc, tr, tc, board)
    moves = _entry(room, _flip(seat), board[fr][fc]["id"])["moves"]
    if text not in moves:
        moves.append(text)


def record_battle(room, R, seat, fr, fc, tr, tc, result):
    """戦闘の直後（盤を書き換える前）に呼ぶ。勝って盤に残る相手の駒に「自分のどの駒に勝ったか」を書く。
    seat が攻めた側。result は rules.resolve_battle の戻り値。"""
    board = room["board"]
    att, dfn = board[fr][fc], board[tr][tc]
    if result == "defender":     # 守った駒が勝った → 攻めた側から見て、相手の駒が自分の駒に勝った
        wins = _entry(room, seat, dfn["id"])["wins"]
        mine = att["kind"]
    elif result == "attacker":   # 攻めた駒が勝った → 守った側から見て、相手の駒が自分の駒に勝った
        wins = _entry(room, _flip(seat), att["id"])["wins"]
        mine = dfn["kind"]
    else:
        return                   # 相打ちは両方消えるので残すものがない
    if mine not in wins:
        wins.append(mine)


def set_memo(room, R, viewer, r, c, text):
    """viewer が (r,c) にある相手の駒にメモを書く。空なら消す。エラーなら文言を返す。"""
    if room.get("phase") != "play" or "memos" not in room:
        return "対戦中だけメモを書けます。"
    if not (isinstance(r, int) and isinstance(c, int) and R.in_board(r, c)):
        return "マスの位置が正しくありません。"
    cell = room["board"][r][c]
    if not cell or cell["owner"] != _flip(viewer):
        return "メモは相手の駒にだけ書けます。"
    text = str(text or "").strip()[:MEMO_MAX]
    if text:
        room["memos"][viewer][cell["id"]] = text
    else:
        room["memos"][viewer].pop(cell["id"], None)
    return None


def view(room, R, viewer):
    """viewer に渡す、盤の上の相手の駒ごとの記録とメモ。{"r,c": {"wins", "moves", "memo"}}。
    何も無い駒は入れない。"""
    if "intel" not in room:
        return {}
    enemy = _flip(viewer)
    facts = room["intel"][viewer]
    memos = room["memos"][viewer]
    out = {}
    for r in range(R.ROWS):
        for c in range(R.COLS):
            cell = room["board"][r][c]
            if not cell or cell["owner"] != enemy:
                continue
            p = cell["id"]
            f = facts.get(p)
            memo = memos.get(p, "")
            if not f and not memo:
                continue
            out[f"{r},{c}"] = {
                "wins": f["wins"] if f else [],
                "moves": f["moves"] if f else [],
                "memo": memo,
            }
    return out
