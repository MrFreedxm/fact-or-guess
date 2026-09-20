#!/usr/bin/env python3
"""fact-or-guess: "is that a fact or a guess?" - asked at the exit, mechanically.

Hook event: Stop.

Why. An assistant goes and checks where it doubts and does NOT go where it is
sure, and from the inside certainty and knowledge are indistinguishable. Asking
it to "remember to verify" nudges the intention, not the confident answer. This
gate looks at the finished answer instead.

What it catches: exactly one mechanical fact, with no attempt to understand
meaning. The final answer of the turn contains a concrete claim about YOUR
system (a name of one of your memory entries or registry cards, a path, an
address, a "there is no X right now" statement), and in the whole turn there
was not a single read of a file or a command. So no source was opened and what
was said is a recollection, not a check.

What it deliberately does not catch: whether the claim is true. A hook cannot
know that, and pretending it can is how gates lose trust.

It stays silent when the answer itself marks the claim as unverified
("hypothesis", "I did not check", "need to verify"). That is the rule working,
not a loophole: either verify, or call it a guess.

Sources opened earlier in the same session count: an answer about a file read
five messages ago is not a fabrication. A gate that nags on every step gets
ignored, and an ignored gate is worse than none.

Configuration (environment):
  FACT_GATE_ENTITY_DIRS  colon-separated directories whose *.md file names are
                         "entities" of your system (default: the project's
                         auto-memory directory next to the transcript)
  FACT_GATE_PATH_RE      regex for paths and addresses that count as concrete
                         (default: ~/.claude, /opt/<x>, IPv4, com.<vendor>.<id>)
  FACT_GATE_LOG          jsonl log of decisions (default: ~/.claude/state/fact-or-guess.jsonl)
"""
import json
import os
import re
import sys
import time

LOG = os.path.expanduser(os.environ.get("FACT_GATE_LOG") or "~/.claude/state/fact-or-guess.jsonl")
READ_TOOLS = {"Read", "Grep", "Glob", "Bash", "WebFetch", "WebSearch", "NotebookRead"}

HEDGE = re.compile(
    r"(hypothes|not verified|did not check|didn't check|haven't checked|need to (?:check|verify)|"
    r"from memory|if memory serves|may be wrong|might be wrong|not sure|i don't know|i'll check|"
    r"гипотез|не проверя|не проверил|надо проверить|нужно проверить|не сходил|по памяти|"
    r"если верить памяти|могу ошибаться|не уверен|не знаю|уточню|проверю)", re.I)

PATHS = re.compile(os.environ.get("FACT_GATE_PATH_RE") or
                   r"(~/\.claude|/opt/[a-z]|\b\d{1,3}(?:\.\d{1,3}){3}\b|\bcom\.[a-z]+\.[a-z-]+)")

STATE_CLAIMS = re.compile(
    r"((?:there is|there's|we have)\s+no\b|(?:is|are)n't (?:set up|configured|running)|"
    r"(?:does|do)n't exist|not (?:configured|implemented|set up) (?:yet|right now)|"
    r"listens? on (?:port )?\d+|by default|"
    r"(?:сейчас|у нас|пока)\s+(?:нет|не\s+настроен|не\s+сделан|отсутству)|"
    r"механизма\s+нет|нет\s+механизма|такого\s+нет|не\s+существует|"
    r"(?:сейчас|пока)\s+у\s+(?:него|неё|них|нас)\s+нет|не\s+работает\b|слушает\s+(?:порт\s+)?\d|"
    r"\bдефолт\b|по\s+умолчанию\s+сто)", re.I)

GENERIC_NAMES = {"memory", "archive", "readme", "index", "notes", "todo"}


def log(outcome, found=(), session=""):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%F %T"), "outcome": outcome,
                                "blocked": outcome == "block", "found": sorted(found)[:6],
                                "session": session}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def entity_dirs(transcript_path):
    env = os.environ.get("FACT_GATE_ENTITY_DIRS")
    if env:
        return [os.path.expanduser(d) for d in env.split(":") if d]
    # Claude Code keeps auto-memory next to the project's transcripts: <project>/memory
    return [os.path.join(os.path.dirname(transcript_path), "memory")]


def entity_names(dirs):
    names = set()
    for d in dirs:
        try:
            for f in os.listdir(d):
                if f.endswith(".md") and len(f) > 8:
                    names.add(f[:-3].lower())
        except OSError:
            continue
    return {n for n in names if n not in GENERIC_NAMES}


def read_records(path):
    recs = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    recs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return recs


def turn_records(recs):
    """Records of the current turn: everything after the last real user message."""
    start = 0
    for i, r in enumerate(recs):
        if r.get("type") != "user":
            continue
        c = (r.get("message") or {}).get("content")
        if isinstance(c, str) and c.strip():
            start = i
        elif isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "text" for b in c):
            start = i
    return recs[start:]


def opened_in_session(recs):
    """Inputs of every read tool call in the whole session, lowercased."""
    opened = set()
    for r in recs:
        if r.get("type") != "assistant":
            continue
        for block in (r.get("message") or {}).get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") in READ_TOOLS:
                opened.add(json.dumps(block.get("input", {}), ensure_ascii=False).lower())
    return opened


def confirmed(entity, opened):
    """Was a source about this entity opened? Names differ by separators: file names use
    dashes, speech uses spaces, so compare with separators removed."""
    def flat(s):
        return re.sub(r"[-_\s]+", "", s.lower())
    name = flat(entity)
    tail = flat(entity.split("-", 1)[-1])
    for inp in opened:
        p = flat(inp)
        if name and name in p:
            return True
        if len(tail) > 8 and tail in p:
            return True
    return False


def user_text(recs):
    for r in recs:
        if r.get("type") != "user":
            continue
        c = (r.get("message") or {}).get("content")
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return ""


def analyze(recs):
    """Whether a source was opened this turn, and the last answer text."""
    source = False
    text = ""
    for r in recs:
        if r.get("type") != "assistant":
            continue
        for block in (r.get("message") or {}).get("content", []) or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") in READ_TOOLS:
                source = True
            elif block.get("type") == "text":
                text = block.get("text", "")
    return source, text


def concrete_claims(text, names):
    found = set()
    low = text.lower()
    for name in names:
        tail = name.split("-", 1)[-1].replace("-", " ")
        if name in low or (len(tail) > 10 and tail in low):
            found.add(name)
    for m in PATHS.findall(text):
        found.add(m if isinstance(m, str) else m[0])
    for m in STATE_CLAIMS.findall(text):
        piece = m if isinstance(m, str) else m[0]
        if piece.strip():
            found.add(piece.strip().lower())
    return found


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    path = data.get("transcript_path")
    if not path:
        sys.exit(0)
    session = os.path.basename(path)[:-6] if path.endswith(".jsonl") else ""

    all_recs = read_records(path)
    recs = turn_records(all_recs)
    if not recs:
        sys.exit(0)

    source, text = analyze(recs)
    if source or not text.strip():
        if source:
            log("source opened", session=session)
        sys.exit(0)
    if HEDGE.search(text):
        log("hedged", session=session)
        sys.exit(0)

    names = entity_names(entity_dirs(path))
    found = concrete_claims(text, names)
    echo = user_text(recs).lower()
    found = {f for f in found if f.lower() not in echo}  # repeating the user's own words is not a claim

    opened = opened_in_session(all_recs)
    entities = {f for f in found if f in names}
    if entities and all(confirmed(e, opened) for e in entities):
        log("confirmed by earlier read", entities, session)
        sys.exit(0)
    found = {f for f in found if not confirmed(f, opened)}
    if not found:
        log("nothing", session=session)
        sys.exit(0)

    log("block", found, session)
    examples = ", ".join(sorted(found)[:4])
    print("Fact or guess? The answer states something concrete about the system (%s) without "
          "opening a source in this session. Check it with one command, or mark it as a guess, "
          "and continue." % examples, file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
