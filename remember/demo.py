"""Scripted career-fair conversations. They go through the same pipeline as the band."""

SCRIPTS = [
    {
        "lines": [
            "Hi, I'm Maya Chen. I'm working on a soil sensor for small farms.",
            "I care about field deployments and battery life.",
            "The pilot has to be in the ground by Friday.",
            "I'll send you the firmware notes by Friday.",
        ],
        "tap_after": [2],
    },
    {
        "lines": [
            "Hello, I'm Jordan Hale. I'm building a recruiting pipeline for a robotics lab.",
            "I care about embedded systems and students who ship hardware.",
            "The application deadline is by Thursday.",
            "I'll introduce you to the lab lead by Thursday.",
        ],
        "tap_after": [2],
    },
    {
        "lines": [
            "Hey, it's Maya Chen again. I'm working on a soil sensor that now rides a drone.",
            "I care about flight time.",
            "I'll bring the drone to the demo by tomorrow.",
        ],
        "tap_after": [2],
    },
]


def play(remember, scripts=None, pace: float = 0.0, sleep=None) -> list:
    """Feed each script as a conversation. Returns the saved person cards, in order.

    pace is the seconds to wait between lines (the web demo uses a short pause so
    the cards appear while you watch). sleep is the function that waits; tests pass
    one that does not.
    """
    import time

    wait = sleep or time.sleep
    saved = []
    for script in scripts or SCRIPTS:
        taps = set(script.get("tap_after") or [])
        for index, line in enumerate(script["lines"]):
            remember.hear(line, True)
            if index in taps:
                remember.mark()
            if pace > 0:
                wait(pace)
        person = remember.end()
        if person:
            saved.append(person)
    return saved
