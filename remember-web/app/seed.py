"""Demo presets and the one-click sample room (Akim, Priya, Sam on bands A/B/C)."""
from typing import Any, Dict, List

import time

from . import ai, db

PRESET_TRANSCRIPTS: List[Dict[str, Any]] = [
    {
        "id": "career-fair-fpga",
        "title": "Career fair · FPGA hiring manager",
        "band": "A",
        "text": (
            "Priya: Hi! I'm Priya Raman, I'm an engineering manager at Lumen Silicon.\n"
            "Akim: Nice to meet you, I'm Akim. I'm building Remember, a wristband that saves who you met.\n"
            "Priya: Oh fun. We're hiring FPGA interns for next summer, by the way.\n"
            "Akim: No way, I'm looking for an FPGA internship! I've been writing a RISC-V core in Verilog.\n"
            "Priya: Perfect. Send me your GitHub and I'll forward it to the hiring team. My LinkedIn is priya-raman-silicon.\n"
            "Akim: Will do, I'll send you my GitHub repo tonight.\n"
            "Priya: Great. And honestly, you should meet Sam, he's on our FPGA team and lives and breathes RISC-V."
        ),
    },
    {
        "id": "hackathon-robotics-recruiter",
        "title": "Hackathon · robotics recruiter",
        "band": "C",
        "text": (
            "Dana: Hey, I'm Dana Whitfield. I'm a recruiter at Northwind Robotics.\n"
            "Sam: Hi Dana, I'm Sam. I'm an FPGA engineer at Lumen Silicon.\n"
            "Dana: We're hiring firmware engineers for our wearables line, and we really need someone who knows BLE.\n"
            "Sam: I'm looking for a cofounder for a RISC-V side project, honestly.\n"
            "Dana: Ha! I can intro you to our CTO, who is deep into RISC-V. My email is dana@northwind.example.\n"
            "Sam: Amazing. I'll send you a one-pager about the project.\n"
            "Dana: And I'll email you the job description for the firmware role."
        ),
    },
    {
        "id": "hackathon-ml-student",
        "title": "Hackathon · on-device ML student",
        "band": "B",
        "text": (
            "Jordan: Hi, I'm Jordan Lee, I'm a PhD student at Stanford working on on-device ML for wearables.\n"
            "Priya: Very cool. I lead the silicon team at Lumen. What are you looking for this weekend?\n"
            "Jordan: I'm looking for low-power chips to run tiny models on. I can help with model quantization if your team needs it.\n"
            "Priya: We need wearable UX feedback on our dev kit, actually.\n"
            "Jordan: Happy to help with that. My GitHub is github.com/jordanlee-ml.\n"
            "Priya: I'll ship you a dev kit next week."
        ),
    },
    {
        "id": "career-fair-designer",
        "title": "Career fair · product designer",
        "band": "A",
        "text": (
            "Maya: Hey there, I'm Maya Chen. I'm a product designer at Orbit Health.\n"
            "Akim: Hi Maya! I'm Akim, I'm working on a wearable for networking.\n"
            "Maya: Love that. I'm looking for hardware folks to prototype a haptic reminder band.\n"
            "Akim: I can help with BLE firmware and the backend. I'll send you our demo video.\n"
            "Maya: Deal. I'll intro you to our head of research. My website is mayachen.design."
        ),
    },
]

PRESET_INTROS: List[Dict[str, Any]] = [
    {
        "id": "priya-sam",
        "title": "Priya → Akim: “You should meet Sam”",
        "speaker_band_id": "B",
        "listener_band_id": "A",
        "utterance": "Akim, you should meet Sam, he's on the FPGA team — talk to him about RISC-V.",
    },
    {
        "id": "sam-priya",
        "title": "Sam: “Priya and Dana should talk”",
        "speaker_band_id": "C",
        "listener_band_id": "B",
        "utterance": "Priya and Dana should talk about hiring firmware engineers.",
    },
]

SAMPLE_PEOPLE = {
    "A": {
        "name": "Akim",
        "company": "Remember (VexoHacks)",
        "role": "Hardware hacker & founder",
        "socials": {},
        "needs": ["FPGA internship", "RISC-V mentorship"],
        "offers": ["BLE wearable firmware help", "FastAPI backend help"],
    },
    "B": {
        "name": "Priya Raman",
        "company": "Lumen Silicon",
        "role": "Engineering Manager",
        "email": "priya@lumen-silicon.example",
        "socials": {"linkedin": "https://linkedin.com/in/priya-raman-silicon"},
        "needs": ["Wearable UX feedback"],
        "offers": ["Hiring FPGA interns", "Intros to silicon recruiters"],
    },
    "C": {
        "name": "Sam Okafor",
        "company": "Lumen Silicon",
        "role": "FPGA Engineer",
        "socials": {"github": "https://github.com/sam-okafor-fpga"},
        "needs": ["BLE firmware help for side project", "RISC-V side-project cofounder"],
        "offers": ["RISC-V core design mentorship", "FPGA toolchain help"],
    },
}

DANA = {
    "name": "Dana Whitfield",
    "company": "Northwind Robotics",
    "role": "Recruiter",
    "email": "dana@northwind.example",
    "socials": {},
    "needs": ["BLE firmware engineers"],
    "offers": ["Hiring firmware engineers", "Intro to Northwind CTO"],
}


# Placeholder headshots for the demo (randomuser.me stock portraits). Paste a real LinkedIn photo link
# from the camera button on any card to replace one.
PHOTOS = {
    "Akim": "https://randomuser.me/api/portraits/men/32.jpg",
    "Priya Raman": "https://randomuser.me/api/portraits/women/44.jpg",
    "Sam Okafor": "https://randomuser.me/api/portraits/men/75.jpg",
    "Dana Whitfield": "https://randomuser.me/api/portraits/women/68.jpg",
    "Jordan Lee": "https://randomuser.me/api/portraits/men/22.jpg",
    "Maya Chen": "https://randomuser.me/api/portraits/women/65.jpg",
    "Leo Martins": "https://randomuser.me/api/portraits/men/54.jpg",
    "Aisha Bello": "https://randomuser.me/api/portraits/women/90.jpg",
    "Ravi Patel": "https://randomuser.me/api/portraits/men/86.jpg",
    "Elena Petrova": "https://randomuser.me/api/portraits/women/17.jpg",
    "Tom Nguyen": "https://randomuser.me/api/portraits/men/11.jpg",
}

# Other attendees on the floor (no wristband): they show up on the room map and in matching.
ATTENDEES = [
    {"name": "Jordan Lee", "company": "Stanford", "role": "PhD Student",
     "needs": ["Low-power ML chips"], "offers": ["Model quantization help"]},
    {"name": "Maya Chen", "company": "Orbit Health", "role": "Product Designer",
     "needs": ["Hardware prototyping partner"], "offers": ["Wearable UX research"]},
    {"name": "Leo Martins", "company": "Kinetic Labs", "role": "Founder",
     "needs": ["Pre-seed funding"], "offers": ["Seed-stage startup advice"]},
    {"name": "Aisha Bello", "company": "Foundry North", "role": "Investor",
     "needs": ["Hardware startups to back"], "offers": ["Pre-seed funding intros"]},
    {"name": "Ravi Patel", "company": "Pulse Wearables", "role": "Senior PM",
     "needs": ["BLE latency benchmarks"], "offers": ["Hiring hardware PM interns"]},
    {"name": "Elena Petrova", "company": "Lumen Silicon", "role": "ML Engineer",
     "needs": ["Labeled sensor datasets for wearables"], "offers": ["Low-power ML chip dev kits"]},
    {"name": "Tom Nguyen", "company": "UC Berkeley", "role": "Student",
     "needs": ["Hardware PM internship", "Hackathon teammates"], "offers": ["React frontend help"]},
]

# A model of the event floor on a 12x12 grid: zones are furniture, seats are where people stand.
ROOM_ZONES = [
    {"kind": "stage", "label": "Main stage", "c0": 4, "r0": 0, "c1": 7, "r1": 1},
    {"kind": "booth", "label": "Lumen Silicon", "color": "#0a66c2", "c0": 0, "r0": 3, "c1": 1, "r1": 4},
    {"kind": "booth", "label": "Northwind Robotics", "color": "#e4572e", "c0": 0, "r0": 7, "c1": 1, "r1": 8},
    {"kind": "booth", "label": "Foundry North", "color": "#7b61ff", "c0": 10, "r0": 3, "c1": 11, "r1": 4},
    {"kind": "bar", "label": "Coffee", "color": "#a0673a", "c0": 10, "r0": 8, "c1": 11, "r1": 9},
    {"kind": "table", "c0": 5, "r0": 8, "c1": 5, "r1": 8},
    {"kind": "table", "c0": 10, "r0": 10, "c1": 10, "r1": 10},
]
SEATS = {
    "Akim": (5, 4), "Priya Raman": (9, 5), "Sam Okafor": (8, 8), "Dana Whitfield": (3, 10),
    "Jordan Lee": (11, 6), "Leo Martins": (8, 11), "Maya Chen": (6, 11), "Aisha Bello": (11, 1),
    "Ravi Patel": (9, 1), "Elena Petrova": (3, 6), "Tom Nguyen": (0, 11),
}


def load_sample_room() -> None:
    db.reset()
    ids = {}
    for band, person in SAMPLE_PEOPLE.items():
        ids[band] = db.create_person(person)
        db.set_band_owner(band, ids[band])
    dana = db.create_person(DANA)

    # Pre-captured conversations so the handshake swap has a private context note on each side.
    a_conv = {
        "summary": "You talked about Lumen's FPGA internship and your RISC-V core in Verilog. Priya will forward your GitHub to the hiring team.",
        "why": "Priya — hiring FPGA interns @ Lumen Silicon",
        "promises": [{"by": "me", "text": "Send my GitHub repo tonight"},
                     {"by": "them", "text": "Forward GitHub to the hiring team"}],
    }
    b_conv = {
        "summary": "Akim is building Remember, a networking wristband, and wants an FPGA internship. Strong RISC-V/Verilog side project.",
        "why": "Akim — building the Remember wristband, wants an FPGA internship",
        "promises": [{"by": "me", "text": "Forward Akim's GitHub to the hiring team"},
                     {"by": "them", "text": "Send GitHub repo tonight"}],
    }
    c_conv = {
        "summary": "Dana recruits for Northwind's wearables line and needs BLE firmware engineers. Dana offered an intro to Northwind's CTO for your RISC-V side project.",
        "why": "Dana — Northwind recruiter, CTO loves RISC-V",
        "promises": [{"by": "me", "text": "Send one-pager about the project"},
                     {"by": "them", "text": "Email the firmware job description"}],
    }
    fpga_chat = PRESET_TRANSCRIPTS[0]["text"]
    robotics_chat = PRESET_TRANSCRIPTS[1]["text"]
    now = time.time()
    recordings = (
        # band, counterpart id, card, transcript, wearer, counterpart, minutes ago
        ("A", ids["B"], a_conv, fpga_chat, "Akim", "Priya Raman", 12),
        ("B", ids["A"], b_conv, fpga_chat, "Priya Raman", "Akim", 12),
        ("C", dana, c_conv, robotics_chat, "Sam Okafor", "Dana Whitfield", 31),
    )
    for band, other, conv, text, wearer, them, mins in recordings:
        db.add_conversation(
            band, other, conv["summary"], {"seeded": True, "promises_made": conv["promises"]}, "seed",
            transcript=ai.split_utterances(text, wearer, them), created_at=now - mins * 60,
        )
        db.upsert_contact(band, other, source="conversation", conversation_summary=conv["summary"],
                          why_remember=conv["why"], promises=conv["promises"])

    # The rest of the floor, plus a few connections that already happened before the demo starts.
    by_name = {"Akim": ids["A"], "Priya Raman": ids["B"], "Sam Okafor": ids["C"], "Dana Whitfield": dana}
    for a in ATTENDEES:
        by_name[a["name"]] = db.create_person(a)
    for name, url in PHOTOS.items():
        db.set_photo_link(by_name[name], url)
    db.add_match(by_name["Jordan Lee"], by_name["Elena Petrova"], "need_offer",
                 "Jordan needs “Low-power ML chips” — Elena offers “Low-power ML chip dev kits”")
    db.add_match(by_name["Leo Martins"], by_name["Aisha Bello"], "intro",
                 "Ravi says Leo & Aisha should talk about pre-seed funding", by_name["Ravi Patel"])
    db.add_match(by_name["Tom Nguyen"], by_name["Ravi Patel"], "need_offer",
                 "Tom needs “Hardware PM internship” — Ravi offers “Hiring hardware PM interns”")
    db.set_setting("room_layout", {
        "name": "VexoHacks main hall",
        "grid": 12,
        "zones": ROOM_ZONES,
        "seats": {str(by_name[n]): list(cr) for n, cr in SEATS.items()},
    })
