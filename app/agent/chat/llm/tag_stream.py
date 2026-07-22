from app.agent.chat.llm.thinking import DEFAULT_TAG_PAIRS, ThinkTagPair, ThinkTagStreamState, TokenPiece


def _find_earliest_open(text: str, pairs: tuple[ThinkTagPair, ...]) -> tuple[int, ThinkTagPair] | None:
    best: tuple[int, ThinkTagPair] | None = None
    for pair in pairs:
        index = text.find(pair.open)
        if index >= 0 and (best is None or index < best[0]):
            best = (index, pair)
    return best


def _holdback_partial_tag(text: str, opens: list[str]) -> tuple[str, str]:
    max_len = max(len(item) for item in opens) if opens else 0
    if max_len <= 1:
        return text, ""
    for size in range(min(max_len - 1, len(text)), 0, -1):
        tail = text[-size:]
        for open_tag in opens:
            if open_tag.startswith(tail):
                return text[:-size], tail
    return text, ""


def push_tag_aware_text(
    text: str,
    state: ThinkTagStreamState,
    pairs: tuple[ThinkTagPair, ...] = DEFAULT_TAG_PAIRS,
) -> list[TokenPiece]:
    pieces: list[TokenPiece] = []
    remain = state.pending + text
    state.pending = ""

    while remain:
        if not state.think_open:
            found = _find_earliest_open(remain, pairs)
            if not found:
                safe, tail = _holdback_partial_tag(remain, [p.open for p in pairs])
                if safe:
                    pieces.append(TokenPiece(lane="answer", text=safe))
                state.pending = tail
                break
            index, pair = found
            if index > 0:
                pieces.append(TokenPiece(lane="answer", text=remain[:index]))
            remain = remain[index + len(pair.open) :]
            state.think_open = True
            state.active_close = pair.close
            continue

        close_idx = remain.find(state.active_close)
        if close_idx < 0:
            safe, tail = _holdback_partial_tag(remain, [state.active_close])
            if safe:
                pieces.append(TokenPiece(lane="think", text=safe))
            state.pending = tail
            break
        if close_idx > 0:
            pieces.append(TokenPiece(lane="think", text=remain[:close_idx]))
        remain = remain[close_idx + len(state.active_close) :]
        state.think_open = False
        state.active_close = ""

    return pieces
