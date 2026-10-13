"""Offline random legal chess-game simulator for testing move-log output.

This script NEVER opens Chess.com, a browser, or an online game. It generates a
local game using python-chess and writes a complete CSV move table plus PGN.
"""
from __future__ import annotations

import argparse
import csv
import random
from datetime import datetime
from pathlib import Path

try:
    import chess
    import chess.pgn
except ImportError as exc:
    raise SystemExit(
        "python-chess missing. Install with: "
        "python -m pip install python-chess"
    ) from exc


BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR / "random_legal_game_log.csv"
PGN_PATH = BASE_DIR / "random_legal_game.pgn"

FIELDS = [
    "ply",
    "move_number",
    "side",
    "san",
    "uci",
    "from_square",
    "to_square",
    "captured_piece",
    "check_after_move",
    "checkmate_after_move",
    "fen_after_move",
    "game_result",
    "termination",
]


def simulate(seed: int | None, max_plies: int) -> tuple[int, str, str]:
    rng = random.Random(seed)
    board = chess.Board()
    moves: list[chess.Move] = []
    rows: list[dict[str, object]] = []

    while not board.is_game_over(claim_draw=True) and len(moves) < max_plies:
        legal_moves = list(board.legal_moves)
        if not legal_moves:
            break

        move = rng.choice(legal_moves)
        side = "White" if board.turn == chess.WHITE else "Black"
        san = board.san(move)
        captured = board.piece_at(move.to_square)
        if board.is_en_passant(move):
            captured = chess.Piece(chess.PAWN, not board.turn)

        board.push(move)
        moves.append(move)
        ply = len(moves)

        rows.append({
            "ply": ply,
            "move_number": (ply + 1) // 2,
            "side": side,
            "san": san,
            "uci": move.uci(),
            "from_square": chess.square_name(move.from_square),
            "to_square": chess.square_name(move.to_square),
            "captured_piece": (
                ("White " if captured.color == chess.WHITE else "Black ") + captured.symbol().upper()
                if captured else ""
            ),
            "check_after_move": board.is_check(),
            "checkmate_after_move": board.is_checkmate(),
            "fen_after_move": board.fen(),
            "game_result": "",
            "termination": "",
        })

    outcome = board.outcome(claim_draw=True)
    if outcome is None:
        result = "*"
        termination = "MAX_PLIES_REACHED"
    else:
        result = outcome.result()
        termination = outcome.termination.name

    for row in rows:
        row["game_result"] = result
        row["termination"] = termination

    with CSV_PATH.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    game = chess.pgn.Game()
    game.headers["Event"] = "Offline Random Legal Move Test"
    game.headers["Date"] = datetime.now().strftime("%Y.%m.%d")
    game.headers["Result"] = result
    game.headers["Termination"] = termination
    node = game
    for move in moves:
        node = node.add_variation(move)
    with PGN_PATH.open("w", encoding="utf-8") as handle:
        handle.write(str(game))
        handle.write("\n")

    return len(moves), result, termination


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Simulate an offline random legal chess game and export a full move log."
    )
    parser.add_argument("--seed", type=int, default=None, help="Optional seed for repeatable tests.")
    parser.add_argument("--max-plies", type=int, default=500, help="Safety limit; default 500 half-moves.")
    args = parser.parse_args()

    if args.max_plies < 1:
        parser.error("--max-plies must be at least 1")

    plies, result, termination = simulate(args.seed, args.max_plies)
    print(f"[SIM] plies={plies} | result={result} | end={termination}")
    print(f"[CSV] {CSV_PATH}")
    print(f"[PGN] {PGN_PATH}")
    if result == "*":
        print("[NOTE] Safety limit reached; this simulated game did not naturally finish.")


if __name__ == "__main__":
    main()
