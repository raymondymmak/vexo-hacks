"""RFC 6350 (vCard 4.0) generation."""
import base64
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

SOCIAL_LABELS = {"linkedin": "LinkedIn", "github": "GitHub", "twitter": "X", "website": "Website"}


def _esc(v: str) -> str:
    """Escape TEXT values per RFC 6350 §3.4."""
    return (
        (v or "")
        .replace("\\", "\\\\")
        .replace("\r\n", "\n")
        .replace("\n", "\\n")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


def _fold(line: str) -> str:
    """Fold lines longer than 75 octets (RFC 6350 §3.2), never splitting a UTF-8 sequence."""
    out, cur, cur_len = [], "", 0
    limit = 75
    for ch in line:
        n = len(ch.encode("utf-8"))
        if cur_len + n > limit:
            out.append(cur)
            cur, cur_len, limit = " " + ch, 1 + n, 75
        else:
            cur += ch
            cur_len += n
    out.append(cur)
    return "\r\n".join(out)


def _split_name(full: str) -> List[str]:
    parts = (full or "").strip().split()
    if not parts:
        return ["", "", "", "", ""]
    if len(parts) == 1:
        return ["", parts[0], "", "", ""]
    return [parts[-1], parts[0], " ".join(parts[1:-1]), "", ""]


def build_note(c: Dict[str, Any]) -> str:
    lines = []
    how = {
        "handshake": "Swapped cards with a handshake",
        "intro": "Introduced through the room",
        "match": "Matched by Remember (needs ↔ offers)",
        "conversation": "Captured from a conversation",
    }.get(c.get("source"), "Met via Remember")
    lines.append(f"{how} (Remember · VexoHacks).")
    if c.get("why_remember"):
        lines.append(f"Why you'll remember them: {c['why_remember']}")
    if c.get("conversation_summary"):
        lines.append(f"What you talked about: {c['conversation_summary']}")
    if c.get("match_reason"):
        lines.append(f"Why you should talk: {c['match_reason']}")
    promises = c.get("promises_made") or []
    if promises:
        lines.append("Promises:")
        for p in promises:
            who = "You" if p.get("by") == "me" else (c.get("name") or "They").split()[0]
            lines.append(f"• {who}: {p.get('text', '')}")
    if c.get("needs"):
        lines.append("Looking for: " + "; ".join(c["needs"]))
    if c.get("offers"):
        lines.append("Can offer: " + "; ".join(c["offers"]))
    return "\n".join(lines)


def build_vcard(c: Dict[str, Any], photo: Optional[Tuple[bytes, str]] = None) -> str:
    name = c.get("name") or "Unknown"
    rev = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    props = [
        "BEGIN:VCARD",
        "VERSION:4.0",
        "PRODID:-//Remember//VexoHacks//EN",
        f"FN:{_esc(name)}",
        "N:" + ";".join(_esc(x) for x in _split_name(name)),
        "KIND:individual",
    ]
    if c.get("company"):
        props.append(f"ORG:{_esc(c['company'])}")
    if c.get("role"):
        props.append(f"TITLE:{_esc(c['role'])}")
    if c.get("email"):
        props.append(f"EMAIL;TYPE=work:{_esc(c['email'])}")
    if c.get("phone"):
        tel = re.sub(r"[^\d+]", "", c["phone"])
        props.append(f"TEL;VALUE=uri;TYPE=cell:tel:{tel}")
    for key, url in (c.get("socials") or {}).items():
        if not url:
            continue
        if not re.match(r"^https?://", url):
            url = "https://" + url.lstrip("@/")
        label = SOCIAL_LABELS.get(key, key.title())
        # URL values are URIs, not TEXT — commas/semicolons must not be backslash-escaped.
        props.append(f"URL;TYPE={label.lower()}:{url}")
        # Apple Contacts renders this as a tappable social profile.
        props.append(f"X-SOCIALPROFILE;TYPE={label.lower()}:{url}")
    if photo:
        data, ctype = photo
        props.append(f"PHOTO:data:{ctype};base64,{base64.b64encode(data).decode('ascii')}")
    props.append(f"NOTE:{_esc(build_note(c))}")
    props.append("CATEGORIES:" + ",".join(_esc(x) for x in ["Remember", "VexoHacks", c.get("source") or "conversation"]))
    props.append(f"REV:{rev}")
    props.append("END:VCARD")
    return "\r\n".join(_fold(p) for p in props) + "\r\n"


def vcard_filename(c: Dict[str, Any]) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", "-", c.get("name") or "contact").strip("-") or "contact"
    return f"{base}.vcf"
