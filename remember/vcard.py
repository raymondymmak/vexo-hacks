"""Reviewed contact cards as portable vCard 3.0 files."""


def _escape(value):
    return str(value or "").replace("\\", "\\\\").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def _fold(line):
    """Fold at 75 UTF-8 octets without splitting a code point."""
    lines, current, size = [], "", 0
    for char in line:
        width = len(char.encode("utf-8"))
        if size + width > 75:
            lines.append(current)
            current, size = " ", 1
        current += char
        size += width
    lines.append(current)
    return "\r\n".join(lines)


def render_contact(person):
    uid = person.get("contact_uid")
    if not uid:
        raise ValueError("This contact has no stable identifier.")
    lines = ["BEGIN:VCARD", "VERSION:3.0", "UID:" + _escape(uid),
             "FN:" + _escape(person.get("name") or "Someone you met"),
             "N:;" + _escape(person.get("name") or "Someone you met") + ";;;"]
    for field, key in (("company", "ORG"), ("role", "TITLE"), ("email", "EMAIL;TYPE=INTERNET"), ("phone", "TEL")):
        if person.get(field):
            lines.append(key + ":" + _escape(person[field]))
    if person.get("social_url"):
        lines.append("URL:" + _escape(person["social_url"]))
    notes = [person.get("summary") or ""]
    for promise in person.get("promises") or []:
        note = "You promised: " + promise["what"]
        if promise.get("by_when"):
            note += " (" + promise["by_when"] + ")"
        notes.append(note)
    if any(notes):
        lines.append("NOTE:" + _escape("\n".join(filter(None, notes))))
    lines.append("END:VCARD")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
