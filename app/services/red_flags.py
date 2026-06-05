# app/services/red_flags.py
"""
Deterministic red-flag layer — SRS M4 hard requirement.

This layer ALWAYS runs and always wins: if a red-flag phrase appears in the
patient's message, the triage level is forced to "high" regardless of what the
LLM classifier says (or whether it is even reachable). It is the named
mitigation for the SRS's top risk: "Message triage misses an emergency."

It runs on the ENGLISH pivot of the message (chat_core translates first), so
one keyword list covers every patient language. Matching is conservative by
design — over-triage is acceptable, under-triage is not.

Keyword list owner: Dr Elvira (clinical lead). Keep phrases lowercase.
"""

import re

# Obstetric danger signs (WHO + SRS examples) — substring matched, lowercase.
PHYSICAL_RED_FLAGS: list[str] = [
    # bleeding
    "bleeding", "blood coming out", "blood is coming", "blood clots",
    "bleed a lot", "soaked a pad", "soaking pads",
    # pain
    "severe pain", "serious pain", "strong pain", "unbearable pain",
    "sharp pain", "pain is too much", "stomach is paining me seriously",
    "cramping badly", "severe cramp",
    # fetal movement
    "no fetal movement", "baby is not moving", "baby has not moved",
    "baby stopped moving", "can't feel the baby", "cannot feel the baby",
    "no movement of the baby",
    # pre-eclampsia / eclampsia signs
    "severe headache", "serious headache", "blurred vision", "blurry vision",
    "seeing double", "swollen face", "face is swelling", "hands are swelling",
    "convulsion", "seizure", "fits",
    # infection / sepsis
    "high fever", "fever", "smelly discharge", "foul-smelling discharge",
    "foul smelling discharge",
    # labour / rupture
    "water broke", "my water has broken", "waters broke", "fluid gushing",
    # collapse
    "fainted", "fainting", "dizzy and falling", "unconscious",
    "can't breathe", "cannot breathe", "difficulty breathing", "chest pain",
]

# Mental-health crisis signals (SRS 2.7.1 item 3) — always high, always alert.
CRISIS_RED_FLAGS: list[str] = [
    "i want to die", "want to die", "i don't want to live", "dont want to live",
    "do not want to live", "kill myself", "hurt myself", "harm myself",
    "end my life", "end it all", "suicide", "suicidal",
    "no reason to live", "better off dead", "better off without me",
]

_ALL_FLAGS = PHYSICAL_RED_FLAGS + CRISIS_RED_FLAGS

# "fever" alone is broad; require it as a whole word to avoid e.g. "feverfew".
_WORD_BOUNDED = {"fever", "fits", "bleeding", "suicide", "suicidal"}


def match_red_flags(english_text: str) -> list[str]:
    """Return the list of red-flag phrases found in the message ([] if none)."""
    text = (english_text or "").lower()
    hits: list[str] = []
    for phrase in _ALL_FLAGS:
        if phrase in _WORD_BOUNDED:
            if re.search(rf"\b{re.escape(phrase)}\b", text):
                hits.append(phrase)
        elif phrase in text:
            hits.append(phrase)
    return hits


def is_crisis_signal(flags: list[str]) -> bool:
    """True if any matched flag is a mental-health crisis signal."""
    return any(f in CRISIS_RED_FLAGS for f in flags)
