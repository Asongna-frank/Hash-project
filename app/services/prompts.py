# app/services/prompts.py
# All LLM prompt strings and keyword lists live here.
# Clinical lead (Dr Elvira) reviews and approves changes to these strings.

LOSS_DETECTION_SYSTEM_PROMPT = """
You are a medical assistant reviewing a patient message from a maternal
health platform used in sub-Saharan Africa. Patients write in informal
English, French, or local expressions from Cameroon, Nigeria, or Kenya.

Determine if the patient is reporting that they have experienced a pregnancy
loss (miscarriage, stillbirth, or fetal death).

Reply with ONLY one of these three words — no punctuation, no explanation:
CONFIRMED  — the patient is clearly reporting a pregnancy loss
AMBIGUOUS  — the message is unclear or could mean something else
NOT_A_LOSS — the patient is not reporting a pregnancy loss
""".strip()

# Layer 1 keyword list — checked before any LLM call (free, instant)
LOSS_KEYWORDS: list[str] = [
    "lost my baby",
    "lost the baby",
    "lost my pregnancy",
    "had a miscarriage",
    "i miscarried",
    "miscarriage",
    "pregnancy loss",
    "lost my child",
    "stillbirth",
    "my baby died",
    "baby did not make it",
    "baby didn't make it",
    "i lost my pregnancy",
    "lost the pregnancy",
    "fausse couche",        # French — miscarriage
    "j'ai perdu mon bébé",  # French — I lost my baby
]

# Sent when loss detection result is AMBIGUOUS
LOSS_AMBIGUOUS_FOLLOWUP = (
    "I want to make sure I understand what you're going through. "
    "Are you telling me that you've experienced a pregnancy loss? "
    "Please reply yes or no — I'm here with you either way."
)

# Sent when loss is CONFIRMED — placeholder until M5 content library
# TODO M5: replace with Dr Elvira's approved opening message
POST_LOSS_OPENING_MESSAGE = (
    "I'm so deeply sorry for your loss. I'm here with you. "
    "You don't have to go through this alone. Take all the time you need — "
    "I'll check in gently, and you can pause messages any time by replying PAUSE."
)

# Placeholder reply for non-loss messages until M4 triage is built
# TODO M4: replace with full triage pipeline
PLACEHOLDER_TRIAGE_REPLY = (
    "Thank you for your message. A member of your care team has been notified. "
    "If this is an emergency, please go to your hospital immediately."
)