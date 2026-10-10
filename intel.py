# -*- coding: utf-8 -*-
"""
相手の駒について「見て分かったこと」と「自分で書いたメモ」を、駒ごとに覚えておく。

候補を絞り込んだり正体を当てたりはしない（予想する面白さを残すため。2026-10-10にrenが決めた）。
覚えるのは、その駒について実際に起きたことだけ。
  - 戦闘：自分のどの駒に勝ったか（負けた・相打ちの駒は盤から消えるので残らない）
  - メモ：プレイヤーが自分で書いたもの。相手には送らない
動き方（「動いた」など）は残さない。気になればプレイヤーがメモに書く（2026-10-10にrenが決めた）。

記録の形（部屋ごと）：
  room["intel"][見る側の席][相手の駒の番号] = [勝った相手の自分の駒]
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


def record_battle(room, R, seat, fr, fc, tr, tc, result):
    """戦闘の直後（盤を書き換える前）に呼ぶ。勝って盤に残る相手の駒に「自分のどの駒に勝ったか」を書く。
    seat が攻めた側。result は rules.resolve_battle の戻り値。"""
    board = room["board"]
    att, dfn = board[fr][fc], board[tr][tc]
    if result == "defender":     # 守った駒が勝った → 攻めた側から見て、相手の駒が自分の駒に勝った
        wins = room["intel"][seat].setdefault(dfn["id"], [])
        mine = att["kind"]
    elif result == "attacker":   # 攻めた駒が勝った → 守った側から見て、相手の駒が自分の駒に勝った
        wins = room["intel"][_flip(seat)].setdefault(att["id"], [])
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
    """viewer に渡す、盤の上の相手の駒ごとの記録とメモ。{"r,c": {"wins", "memo"}}。
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
            wins = facts.get(p, [])
            memo = memos.get(p, "")
            if wins or memo:
                out[f"{r},{c}"] = {"wins": wins, "memo": memo}
    return out
