from __future__ import annotations


def truncate_history(
    turns: list[dict[str, str]],
    *,
    max_tokens: int,
    tokens_per_turn: int = 200,
) -> list[dict[str, str]]:
    """Drop oldest complete user+assistant turns until under max_tokens."""
    if not turns:
        return []
    # Group into pairs (user, assistant)
    pairs: list[list[dict[str, str]]] = []
    i = 0
    while i < len(turns):
        if turns[i]["role"] == "user" and i + 1 < len(turns) and turns[i + 1]["role"] == "assistant":
            pairs.append([turns[i], turns[i + 1]])
            i += 2
        else:
            pairs.append([turns[i]])
            i += 1

    # Keep newest turns that fit
    available = max_tokens
    result: list[dict[str, str]] = []
    for pair in reversed(pairs):
        cost = len(pair) * tokens_per_turn
        if cost <= available:
            result = pair + result
            available -= cost
        else:
            break
    return result
