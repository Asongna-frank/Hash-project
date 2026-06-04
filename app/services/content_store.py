# app/services/content_store.py
"""
Pre-approved static content store, keyed by content key + language.

The CRISIS carve-out depends on this: crisis/loss messages must come from a
pre-approved, clinically-reviewed store in the patient's language — they are
NEVER produced by live translation, because a mistranslation of a loss or
emergency message is unacceptable.

Opt-out confirmations and the loss-confirmation follow-up are also served from
here so both channels (app/sms) speak with one approved voice.

Languages: "en" (canonical), "fr", "pt". Unknown language → English fallback
(logged). All English strings mirror app/services/prompts.py so there is a
single approved source of truth per language.

NOTE: tips/check-ins are LLM-generated per patient and are NOT static content;
they live in their generators. Appointment/reminder templates can be added here
later in fr/pt the same way — the mechanism is identical.
"""

import logging

logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = ("en", "fr", "pt")

_CONTENT: dict[str, dict[str, str]] = {
    # CRISIS — confirmed pregnancy loss opening message (pre-approved, never live-translated)
    "post_loss_opening": {
        "en": (
            "I'm so deeply sorry for your loss. I'm here with you. "
            "You don't have to go through this alone. Take all the time you need — "
            "I'll check in gently, and you can pause messages any time by replying PAUSE."
        ),
        "fr": (
            "Je suis profondément désolée pour votre perte. Je suis là, avec vous. "
            "Vous n'avez pas à traverser cela seule. Prenez tout le temps qu'il vous faut — "
            "je prendrai de vos nouvelles avec douceur, et vous pouvez suspendre les messages "
            "à tout moment en répondant PAUSE."
        ),
        "pt": (
            "Lamento profundamente a sua perda. Estou aqui consigo. "
            "Não precisa de passar por isto sozinha. Leve o tempo que precisar — "
            "vou acompanhá-la com cuidado, e pode pausar as mensagens a qualquer momento "
            "respondendo PAUSE."
        ),
    },
    # Loss-detection AMBIGUOUS follow-up (sensitive — served from store, not live-translated)
    "loss_ambiguous_followup": {
        "en": (
            "I want to make sure I understand what you're going through. "
            "Are you telling me that you've experienced a pregnancy loss? "
            "Please reply yes or no — I'm here with you either way."
        ),
        "fr": (
            "Je veux être sûre de bien comprendre ce que vous vivez. "
            "Êtes-vous en train de me dire que vous avez subi une perte de grossesse ? "
            "Répondez oui ou non — je suis là pour vous dans tous les cas."
        ),
        "pt": (
            "Quero ter a certeza de que compreendo o que está a viver. "
            "Está a dizer-me que sofreu uma perda gestacional? "
            "Responda sim ou não — estou aqui consigo de qualquer forma."
        ),
    },
    # Opt-out confirmations
    "pause_confirmation": {
        "en": (
            "Understood. I will pause messages for the next 7 days. "
            "You can reply RESUME any time you want to hear from me again."
        ),
        "fr": (
            "Entendu. Je vais suspendre les messages pendant les 7 prochains jours. "
            "Vous pouvez répondre RESUME à tout moment pour avoir de mes nouvelles à nouveau."
        ),
        "pt": (
            "Compreendido. Vou pausar as mensagens durante os próximos 7 dias. "
            "Pode responder RESUME quando quiser voltar a ter notícias minhas."
        ),
    },
    "stop_confirmation": {
        "en": (
            "Understood. I will stop sending you messages. "
            "You can reply RESUME any time you want to hear from me again. "
            "I am always here when you are ready."
        ),
        "fr": (
            "Entendu. Je vais arrêter de vous envoyer des messages. "
            "Vous pouvez répondre RESUME à tout moment pour avoir de mes nouvelles à nouveau. "
            "Je suis toujours là quand vous serez prête."
        ),
        "pt": (
            "Compreendido. Vou parar de lhe enviar mensagens. "
            "Pode responder RESUME quando quiser voltar a ter notícias minhas. "
            "Estarei sempre aqui quando estiver pronta."
        ),
    },
    "resume_confirmation": {
        "en": (
            "Welcome back. I am glad you are here. "
            "I will continue checking in with you as before."
        ),
        "fr": (
            "Bon retour. Je suis contente que vous soyez là. "
            "Je continuerai à prendre de vos nouvelles comme avant."
        ),
        "pt": (
            "Bem-vinda de volta. Fico feliz por estar aqui. "
            "Vou continuar a acompanhá-la como antes."
        ),
    },
    # Inbound SMS from an unknown number (optional, non-leaking)
    "not_registered": {
        "en": "This number is not registered. Please contact your hospital to sign up.",
        "fr": "Ce numéro n'est pas enregistré. Veuillez contacter votre hôpital pour vous inscrire.",
        "pt": "Este número não está registado. Contacte o seu hospital para se inscrever.",
    },
}


def get_content(key: str, language: str | None) -> str:
    """
    Return the pre-approved message for `key` in `language`, falling back to
    English (logged) when the language is missing. Raises KeyError on an
    unknown content key (a programming error, not a runtime input).
    """
    if key not in _CONTENT:
        raise KeyError(f"Unknown content key: {key!r}")

    lang = (language or "en").lower()
    entry = _CONTENT[key]

    if lang in entry:
        return entry[lang]

    logger.warning(
        "No %r content for language %r — falling back to English", key, lang
    )
    return entry["en"]
