"""AI layer: contact extraction + room matching.

Uses OpenAI when OPENAI_API_KEY is set. If the key is missing, the call fails, or
it times out, every function falls back to a deterministic heuristic so the demo
never dies on stage because of conference Wi-Fi.
"""
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("remember.ai")

MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
TIMEOUT_S = float(os.getenv("OPENAI_TIMEOUT", "20"))

_client = None


def ai_enabled() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip()) and os.getenv("REMEMBER_FORCE_FALLBACK", "") != "1"


def _get_client():
    global _client
    if _client is None:
        from openai import AsyncOpenAI

        _client = AsyncOpenAI(timeout=TIMEOUT_S, max_retries=1)
    return _client


async def _chat_json(system: str, user: str) -> Optional[Dict[str, Any]]:
    if not ai_enabled():
        return None
    try:
        resp = await _get_client().chat.completions.create(
            model=MODEL,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:  # network, auth, bad JSON, rate limit...
        log.warning("OpenAI call failed, using heuristic fallback: %s", e)
        return None


# =================================================================== extraction
EXTRACT_SYSTEM = """You turn a raw conversation transcript captured by a networking wristband into a
structured contact card. The band's wearer is named in the request; extract the OTHER person
(the counterpart). Respond with JSON only, exactly this shape:
{
  "counterpart": {
    "name": "Full name if said, else first name, else 'Unknown'",
    "company": "", "role": "", "email": "", "phone": "",
    "socials": {"linkedin": "", "github": "", "twitter": "", "website": ""},
    "needs": ["short phrases: what THEY are looking for"],
    "offers": ["short phrases: what THEY can give (hiring, mentorship, intros, skills)"]
  },
  "wearer": {"needs": ["what the WEARER said they are looking for"], "offers": ["what the WEARER can offer"]},
  "conversation_summary": "2 sentences, second person ('You talked about...'), concrete specifics",
  "why_remember": "one punchy hook under 12 words that will jog memory later",
  "promises_made": [{"by": "me" or "them", "text": "short imperative, e.g. 'Send GitHub repo'"}],
  "intros": [{"name": "person someone said the wearer should meet", "reason": "short topic phrase"}],
  "topics": ["2-5 short tags"]
}
Rules: never invent facts that are not in the transcript; leave unknown strings empty. Socials should be
full URLs when a handle is given (github.com/x, linkedin.com/in/x). Needs/offers are 2-6 words each,
written so they can be matched against other people's needs/offers ("FPGA internship", "hiring FPGA interns")."""


async def extract_contact(transcript: str, wearer_name: str, room_names: List[str]) -> Tuple[Dict[str, Any], str]:
    """Returns (extraction, engine) where engine is 'openai' or 'heuristic'."""
    user = (
        f"Wearer: {wearer_name or 'the wearer'}\n"
        f"People currently in the room (for resolving names in intros): {', '.join(room_names) or 'none'}\n\n"
        f"Transcript:\n{transcript.strip()}"
    )
    data = await _chat_json(EXTRACT_SYSTEM, user)
    if data and isinstance(data.get("counterpart"), dict):
        return _normalize_extraction(data), "openai"
    return heuristic_extract(transcript, wearer_name), "heuristic"


def _s(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def _ls(v: Any) -> List[str]:
    return [x.strip() for x in v if isinstance(x, str) and x.strip()][:6] if isinstance(v, list) else []


def _normalize_extraction(d: Dict[str, Any]) -> Dict[str, Any]:
    cp = d.get("counterpart") or {}
    soc = cp.get("socials") or {}
    wearer = d.get("wearer") or {}
    promises = []
    for p in d.get("promises_made") or []:
        if isinstance(p, dict) and _s(p.get("text")):
            promises.append({"by": "me" if p.get("by") == "me" else "them", "text": _s(p["text"])})
        elif isinstance(p, str) and p.strip():
            promises.append({"by": "them", "text": p.strip()})
    intros = [
        {"name": _s(i.get("name")), "reason": _s(i.get("reason"))}
        for i in d.get("intros") or []
        if isinstance(i, dict) and _s(i.get("name"))
    ]
    return {
        "counterpart": {
            "name": _s(cp.get("name")) or "Unknown",
            "company": _s(cp.get("company")),
            "role": _s(cp.get("role")),
            "email": _s(cp.get("email")),
            "phone": _s(cp.get("phone")),
            "socials": {k: _s(soc.get(k)) for k in ("linkedin", "github", "twitter", "website") if _s(soc.get(k))},
            "needs": _ls(cp.get("needs")),
            "offers": _ls(cp.get("offers")),
        },
        "wearer": {"needs": _ls(wearer.get("needs")), "offers": _ls(wearer.get("offers"))},
        "conversation_summary": _s(d.get("conversation_summary")),
        "why_remember": _s(d.get("why_remember")),
        "promises_made": promises[:8],
        "intros": intros[:4],
        "topics": _ls(d.get("topics")),
    }


# ------------------------------------------------------------------ heuristic
TOPIC_WORDS = [
    "FPGA", "RISC-V", "Verilog", "BLE", "Bluetooth", "wearable", "firmware", "embedded", "robotics", "ML",
    "machine learning", "LLM", "AI", "computer vision", "React", "Rust", "Python", "FastAPI", "startup",
    "internship", "hiring", "recruiting", "hackathon", "chip", "silicon", "battery", "PCB", "sensor",
    "climate", "fintech", "health", "design", "UX", "iOS", "Android", "blockchain", "security", "GPU",
]
NAME_RE = r"([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)"
STOP_AFTER_NAME = {"I", "And", "The", "So", "But", "At", "On", "From", "With", "Here"}


def _split_speakers(text: str) -> List[Tuple[Optional[str], str]]:
    out = []
    for raw in re.split(r"\n+", text.strip()):
        m = re.match(r"^\s*([A-Za-z][\w .'-]{0,24}):\s*(.+)$", raw)
        if m:
            out.append((m.group(1).strip(), m.group(2).strip()))
        elif raw.strip():
            out.append((None, raw.strip()))
    return out


def split_utterances(text: str, wearer_name: str, counterpart_name: str) -> List[Dict[str, str]]:
    """Turn 'Name: line' transcripts into chat bubbles from the wearer's point of view."""
    wearer = (wearer_name or "").split()[0].lower() if wearer_name else ""
    them = (counterpart_name or "").split()[0].lower() if counterpart_name and counterpart_name != "Unknown" else ""
    out = []
    for label, line in _split_speakers(text):
        key = label.lower().split()[0] if label else ""
        if key and key == them:
            who = "them"
        elif key and (key == wearer or key in ("me", "i", "wearer")):
            who = "me"
        elif key and not wearer:
            who = "me"  # unpaired band: anyone who isn't the extracted counterpart is the wearer
        else:
            who = "them"
        out.append({"who": who, "speaker": label or (counterpart_name if who == "them" else wearer_name) or "", "text": line})
    return out[:400]


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _clip(s: str, n: int = 48) -> str:
    s = re.sub(r"\s+", " ", s).strip(" .,;:-—")
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def _phrases(text: str, patterns: List[str]) -> List[str]:
    out = []
    for pat in patterns:
        for m in re.finditer(pat, text, flags=re.I):
            p = re.split(r"\s+(?:for (?:our|next|this)|if your|so that|because|and we)\b", m.group(1), flags=re.I)[0]
            p = _clip(p, 52)
            p = re.sub(r"^(intro(?:duce)?)\s+you\s+to\b", r"\1 to", p, flags=re.I)
            p = re.sub(r"^(a|an|the|some|any)\s+", "", p, flags=re.I)
            p = re.sub(r"^(?:we're|we are|I'm|I am)\s+", "", p, flags=re.I)
            if len(p) > 2 and p.lower() not in PRONOUNS and p.lower() not in [x.lower() for x in out]:
                out.append(p[0].upper() + p[1:])
    return out[:5]


PRONOUNS = {"that", "this", "it", "them", "you", "us", "me", "him", "her", "those", "these"}

NEED_PATTERNS = [
    r"(?:I'm|I am|we're|we are)\s+(?:currently\s+|really\s+)?(?:looking|searching|hunting)\s+for\s+([^.,!?;]+)",
    r"(?:I|we)\s+(?:really\s+)?need\s+([^.,!?;]+)",
    r"(?:trying|hoping)\s+to\s+(?:find|get|land)\s+([^.,!?;]+)",
]
OFFER_PATTERNS = [
    r"((?:we're|we are|I'm|I am)\s+hiring\s+[^.,!?;]+)",
    r"(?:I|we)\s+can\s+(?:help\s+(?:you\s+)?with|offer|teach)\s+([^.,!?;]+)",
    r"happy\s+to\s+(?:help\s+(?:you\s+)?with|share|review|mentor\s+on)\s+([^.,!?;]+)",
    r"(?:I|we)\s+(?:can|could)\s+(intro(?:duce)?\s+you\s+to\s+[^.,!?;]+)",
]
PROMISE_RE = re.compile(
    r"\b(?:I'll|I will|I'm going to|let me|I can send|I'll make sure to|we'll)\s+([^.!?]+)", re.I
)


def heuristic_extract(transcript: str, wearer_name: str) -> Dict[str, Any]:
    lines = _split_speakers(transcript)
    wearer_first = (wearer_name or "").split()[0].lower() if wearer_name else ""
    me_aliases = {"me", "i", "wearer", "you", wearer_first}
    labels = [l for l, _ in lines if l]
    cp_label = next((l for l in labels if l.lower().split()[0] not in me_aliases), None)

    def is_me(label: Optional[str]) -> bool:
        return bool(label) and label.lower().split()[0] in me_aliases

    them_text = " ".join(t for l, t in lines if not is_me(l)) if labels else transcript
    me_text = " ".join(t for l, t in lines if is_me(l))

    # ---- name
    name = ""
    m = re.search(r"\b(?:I'm|I am|my name is|this is|call me)\s+" + NAME_RE, them_text)
    if m and m.group(1).split()[0] not in STOP_AFTER_NAME:
        name = m.group(1)
        # "I'm Priya Raman" vs "I'm Priya and I..." -> drop trailing stop word
        parts = name.split()
        if len(parts) == 2 and parts[1] in STOP_AFTER_NAME:
            name = parts[0]
    if not name and cp_label and cp_label.lower() not in ("them", "other", "speaker 2"):
        name = cp_label
    if cp_label and name and len(name.split()) == 1 and cp_label.lower().startswith(name.lower()):
        name = cp_label if len(cp_label) > len(name) else name

    # ---- company / role
    company = ""
    m = re.search(
        r"\b(?:work(?:ing)?\s+(?:at|for)|I'm\s+at|I am\s+at|(?:engineer|manager|lead|founder|recruiter|intern|designer|scientist|student|researcher|director)\s+(?:at|for))\s+"
        r"([A-Z][\w&\-]*(?:[ ]+[A-Z][\w&\-]*){0,3})",
        them_text,
    )
    if m:
        company = m.group(1).strip().rstrip(".")
    role = ""
    m = re.search(
        r"\b(?:I'm|I am)\s+(?:a|an|the)\s+([A-Za-z][A-Za-z\- ]{2,40}?)\s+(?:at|for|on|with|in)\b", them_text
    ) or re.search(r"\b(?:I'm|I am)\s+(?:a|an|the)\s+([A-Za-z][A-Za-z\- ]{2,40}?)[.,!]", them_text)
    if m:
        role = m.group(1).strip().title().replace("Fpga", "FPGA").replace("Ml ", "ML ").replace("Phd", "PhD")
    m = re.search(r"\bI\s+(lead|run|manage)\s+(?:the\s+)?([A-Za-z\- ]{2,30}?)\s+team", them_text)
    if m and not role:
        role = f"{m.group(2).strip()} team lead"

    # ---- contact details
    socials = {}
    em = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", them_text)
    email = em.group(0) if em else ""
    phone_m = re.search(r"(\+?\d[\d\s().-]{7,}\d)", them_text)
    phone = phone_m.group(1).strip() if phone_m else ""
    gh = re.search(r"github\.com/([\w-]+)", them_text, re.I) or re.search(
        r"github\s+(?:is|handle is|username is|at)\s+@?([\w-]+)", them_text, re.I
    )
    if gh:
        socials["github"] = f"https://github.com/{gh.group(1)}"
    li = re.search(r"linkedin\.com/in/([\w-]+)", them_text, re.I) or re.search(
        r"linkedin\s+(?:is|at)\s+([\w-]+)", them_text, re.I
    )
    if li:
        socials["linkedin"] = f"https://linkedin.com/in/{li.group(1)}"
    tw = re.search(r"(?:twitter|x)\s+(?:is|at|handle is)\s+@?(\w+)", them_text, re.I)
    if tw:
        socials["twitter"] = f"https://x.com/{tw.group(1)}"
    web = re.search(r"\b((?:https?://)?(?:www\.)?[a-z0-9-]+\.(?:dev|io|ai|com|app|xyz)(?:/[\w/-]*)?)\b", them_text, re.I)
    if web and "github" not in web.group(1).lower() and "linkedin" not in web.group(1).lower() and "@" not in web.group(0):
        if not email or web.group(1) not in email:
            socials["website"] = web.group(1) if web.group(1).startswith("http") else "https://" + web.group(1)

    # ---- needs / offers
    needs = _phrases(them_text, NEED_PATTERNS)
    offers = _phrases(them_text, OFFER_PATTERNS)
    w_needs = _phrases(me_text, NEED_PATTERNS)
    w_offers = _phrases(me_text, OFFER_PATTERNS)

    # ---- promises
    promises = []
    for label, text in (lines if labels else [(None, transcript)]):
        for m in PROMISE_RE.finditer(text):
            t = _clip(m.group(1), 52)
            t = re.sub(r"\byou\b\s*", "", t, count=1) if t.lower().startswith(("send you", "intro you", "email you", "text you")) else t
            t = t[0].upper() + t[1:] if t else t
            if len(t) > 3:
                promises.append({"by": "me" if is_me(label) else "them", "text": t})

    # ---- intros
    intros = []
    for m in re.finditer(
        r"you\s+(?:should|need to|have to|gotta)\s+(?:meet|talk to|connect with|chat with|find)\s+" + NAME_RE + r"([^.!?]*)",
        transcript,
    ):
        tail = _clip(m.group(2), 60)
        intros.append({"name": m.group(1).split()[0] if m.group(1).split()[-1] in STOP_AFTER_NAME else m.group(1),
                       "reason": tail})

    # ---- topics / summary
    lower = transcript.lower()
    topics = [w for w in TOPIC_WORDS if re.search(r"(?<![\w-])" + re.escape(w.lower()) + r"(?![\w-])", lower)][:5]
    who = name or "them"
    where = f" ({role}{' at ' if role and company else ''}{company})" if (role or company) else ""
    summary = f"You talked with {who}{where}"
    summary += f" about {', '.join(topics[:3])}." if topics else "."
    if needs:
        summary += f" They're looking for {needs[0].lower()}."
    elif offers:
        summary += f" They mentioned: {offers[0].lower()}."
    them_lower = them_text.lower()
    them_topics = [w for w in topics if w.lower() in them_lower and w.lower() not in company.lower()] or topics
    hook_bits = [b for b in (them_topics[0] if them_topics else "", company) if b]
    why = f"{who.split()[0]} — {' @ '.join(hook_bits)}" if hook_bits else f"Met {who} at the event"

    return {
        "counterpart": {
            "name": name or "Unknown",
            "company": company,
            "role": role,
            "email": email,
            "phone": phone,
            "socials": socials,
            "needs": needs,
            "offers": offers,
        },
        "wearer": {"needs": w_needs, "offers": w_offers},
        "conversation_summary": summary,
        "why_remember": why,
        "promises_made": promises[:6],
        "intros": intros[:3],
        "topics": topics,
    }


# =================================================================== room matching
MATCH_SYSTEM = """You are the matchmaker for a live networking room. Given participants with their needs and
offers, find pairs where one person's OFFER clearly satisfies the other's NEED (or both complement each
other). Only return strong, specific matches; skip vague ones. Respond with JSON only:
{"matches": [{"a_id": int, "b_id": int, "reason": "under 14 words, e.g. 'Sam can help Akim with RISC-V core design'", "score": 0-1}]}"""


async def match_room(people: List[Dict[str, Any]], skip_pairs: set) -> Tuple[List[Dict[str, Any]], str]:
    candidates = [p for p in people if p.get("needs") or p.get("offers")]
    if len(candidates) < 2:
        return [], "heuristic"
    payload = [{"id": p["id"], "name": p["name"], "role": p["role"], "company": p["company"],
                "needs": p["needs"], "offers": p["offers"]} for p in candidates]
    data = await _chat_json(MATCH_SYSTEM, json.dumps({"participants": payload}))
    ids = {p["id"] for p in candidates}
    if data and isinstance(data.get("matches"), list):
        out = []
        for m in data["matches"]:
            try:
                a, b = int(m["a_id"]), int(m["b_id"])
            except (KeyError, TypeError, ValueError):
                continue
            if a in ids and b in ids and a != b and (min(a, b), max(a, b)) not in skip_pairs and float(m.get("score", 1)) >= 0.5:
                out.append({"a": a, "b": b, "reason": _s(m.get("reason")) or "Complementary needs and offers"})
        return out, "openai"
    return heuristic_match(candidates, skip_pairs), "heuristic"


GENERIC = {
    "a", "an", "the", "for", "to", "with", "and", "or", "of", "on", "in", "my", "our", "your", "some",
    "help", "looking", "need", "needs", "hiring", "find", "get", "someone", "people", "who", "can", "we",
    "i", "im", "are", "is", "new", "good", "great", "team", "role", "roles", "folks", "advice", "intro",
    "intros", "introduction", "really", "currently", "this", "that", "summer", "next", "year",
}


def _norm_tokens(s: str) -> set:
    toks = set()
    for t in re.findall(r"[a-z0-9][a-z0-9+\-]*", s.lower()):
        t = {"interns": "intern", "internship": "intern", "internships": "intern", "engineers": "engineer",
             "engineering": "engineer", "wearables": "wearable", "mentors": "mentor", "mentorship": "mentor",
             "mentoring": "mentor", "designers": "designer", "founders": "founder", "cofounder": "founder",
             "co-founder": "founder", "investors": "investor", "investment": "investor", "funding": "investor"}.get(t, t)
        if t.endswith("s") and len(t) > 4 and not t.endswith("ss"):
            t = t[:-1]
        if t not in GENERIC and len(t) > 1:
            toks.add(t)
    return toks


def heuristic_match(people: List[Dict[str, Any]], skip_pairs: set) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for p in people:
        for q in people:
            if p["id"] == q["id"]:
                continue
            pair = (min(p["id"], q["id"]), max(p["id"], q["id"]))
            if pair in skip_pairs or pair in seen:
                continue
            best: Optional[Tuple[int, str, str]] = None
            for need in p.get("needs") or []:
                for offer in q.get("offers") or []:
                    need_toks = _norm_tokens(need)
                    overlap = need_toks & _norm_tokens(offer)
                    # Strong signal only: 2+ shared terms, or the offer covers every term of the need.
                    strong = len(overlap) >= 2 or (bool(need_toks) and need_toks <= overlap)
                    if strong and (best is None or len(overlap) > best[0]):
                        best = (len(overlap), need, offer)
            if best:
                seen.add(pair)
                out.append({
                    "a": p["id"], "b": q["id"],
                    "reason": f"{p['name'].split()[0]} needs “{best[1]}” — {q['name'].split()[0]} offers “{best[2]}”",
                })
    return out


# =================================================================== spoken intros
INTRO_SYSTEM = """A wristband overheard someone speaking at a networking event. Decide whether the utterance is an
introduction like "you should meet X" or "X and Y need to talk". Map names to the participant list (fuzzy first
names are fine). Respond with JSON only:
{"is_intro": bool, "person_a_id": int|null, "person_b_id": int|null, "topic": "short phrase like 'about RISC-V' or 'about the FPGA team'"}
If the utterance says "you should meet X", person_a is the LISTENER and person_b is X."""


async def parse_intro(utterance: str, people: List[Dict[str, Any]], speaker: Optional[Dict], listener: Optional[Dict]
                      ) -> Tuple[Optional[Dict[str, Any]], str]:
    payload = {
        "utterance": utterance,
        "speaker": speaker and {"id": speaker["id"], "name": speaker["name"]},
        "listener": listener and {"id": listener["id"], "name": listener["name"]},
        "participants": [{"id": p["id"], "name": p["name"], "role": p["role"], "company": p["company"]} for p in people],
    }
    data = await _chat_json(INTRO_SYSTEM, json.dumps(payload))
    ids = {p["id"] for p in people}
    if data is not None:
        if data.get("is_intro") and data.get("person_a_id") in ids and data.get("person_b_id") in ids \
                and data["person_a_id"] != data["person_b_id"]:
            return {"a": data["person_a_id"], "b": data["person_b_id"], "topic": _s(data.get("topic"))}, "openai"
        if data.get("is_intro") is False:
            return None, "openai"
    return heuristic_intro(utterance, people, speaker, listener), "heuristic"


def _find_by_first(name: str, people: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    n = name.lower().split()[0]
    for p in people:
        if p["name"].lower().split()[0] == n:
            return p
    return None


def topic_from_tail(tail: str) -> str:
    """Turn the words after 'you should meet X' into a toast-friendly phrase."""
    tail = tail.strip(" ,.—-")
    if not tail:
        return ""
    idx = tail.lower().rfind("about ")
    if idx != -1:
        return _clip("about " + tail[idx + 6:], 60)
    return f"— “{_clip(tail, 60)}”"


def heuristic_intro(utterance: str, people: List[Dict[str, Any]], speaker: Optional[Dict], listener: Optional[Dict]
                    ) -> Optional[Dict[str, Any]]:
    m = re.search(
        r"you\s+(?:two\s+)?(?:should|need to|have to|gotta|must)\s+(?:meet|talk to|connect with|chat with|find)\s+"
        + NAME_RE + r"([^.!?]*)", utterance)
    if m:
        target = _find_by_first(m.group(1), people)
        # "Akim, you should meet Sam" -> listener can also be named in the vocative
        voc = re.match(r"^\s*([A-Z][a-z]+)\s*,", utterance)
        lst = _find_by_first(voc.group(1), people) if voc else None
        lst = lst or listener
        if target and lst and target["id"] != lst["id"]:
            return {"a": lst["id"], "b": target["id"], "topic": topic_from_tail(m.group(2))}
    m = re.search(NAME_RE + r"\s+and\s+" + NAME_RE + r"\s+(?:should|need to|have to|gotta|must)\s+(?:talk|meet|connect)([^.!?]*)", utterance)
    if m:
        a, b = _find_by_first(m.group(1), people), _find_by_first(m.group(2), people)
        if a and b and a["id"] != b["id"]:
            return {"a": a["id"], "b": b["id"], "topic": topic_from_tail(m.group(3))}
    return None
