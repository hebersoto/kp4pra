"""
KP4PRA TNC - Web Email Interface strings (English / Spanish).

Per the project decision: no translation framework. The public mail
pages simply exist in two languages. This module holds a plain dict of
strings for each language; the route passes the selected dict to the
template as `t`. English is the default. The user's own message content
is never translated.
"""

LANGUAGES = ("en", "es")
DEFAULT_LANG = "en"

STRINGS = {
    "en": {
        "lang_code": "en",
        "page_title": "Web Email — KP4PRA TNC",
        "heading": "Web Email",
        "language": "Language",
        "english": "English",
        "spanish": "Español",

        # Notice & agreement (spec section 7)
        "notice_heading": "Before you continue — please read",
        "privacy_heading": "No Privacy",
        "privacy_body": (
            "Amateur radio signals are not encrypted and may be monitored by "
            "anyone with the appropriate receiving equipment. Messages stored "
            "or transmitted by this system may be reviewed by station trustees, "
            "administrators, and licensed amateur radio operators responsible "
            "for operating the system. Do not expect privacy when using this "
            "service."
        ),
        "rules_heading": "Amateur Radio Rules",
        "rules_body": (
            "Messages transmitted through amateur radio must comply with "
            "applicable amateur-radio regulations, including FCC Part 97 when "
            "operating under United States jurisdiction. Messages must not "
            "contain indecent or obscene content, prohibited commercial or "
            "business communications, communications for direct financial gain, "
            "or other content prohibited from transmission over amateur radio. "
            "The station trustee may reject any message that cannot legally be "
            "transmitted over the selected amateur-radio path."
        ),
        "agree_label": "I have read and agree to the notice above.",
        "agree_button": "I Agree — Continue",

        # Reply handling (spec section 6)
        "reply_notice": (
            "Replies and direct incoming email are not received by this "
            "KP4PRA TNC web interface. Any reply from the recipient will be "
            "sent to the personal email address you provide in the Reply-To "
            "field."
        ),

        # Compose form (spec section 4/5)
        "compose_heading": "Compose a message",
        "to_label": "Destination email address",
        "replyto_label": "Your Reply-To email address",
        "subject_label": "Subject",
        "body_label": "Message",
        "subject_hint": "Keep it short — under 50 characters is best.",
        "body_hint": "Keep it concise — about 300 characters is typical.",
        "chars": "characters",
        "submit_button": "Submit for review",

        # Validation errors
        "email_required": "A destination email address is required.",
        "email_invalid": "Please enter a valid email address.",
        "email_intl_unsupported": (
            "Internationalized (non-ASCII) email addresses are not supported "
            "by this radio transport. Please use a standard email address."
        ),
        "replyto_required": "Your Reply-To email address is required.",
        "subject_too_long": "The subject is too long.",
        "body_required": "Please enter a message.",
        "body_too_long": "The message is too long.",
        "agree_required": "You must accept the notice before submitting.",
        "csrf_error": "Your session expired. Please reload the page and try again.",
        "queue_error": "The message could not be saved. Please try again later.",
        "disabled_notice": "The Web Email Interface is currently unavailable.",

        # Confirmation (spec section 15)
        "confirm_heading": "Message submitted",
        "confirm_body": (
            "Your message has been submitted for review by the station "
            "trustee. It has not yet been transmitted. The trustee must "
            "approve the message before it can be sent through Winlink."
        ),
        "compose_another": "Compose another message",
        "back_home": "Back to home",
    },

    "es": {
        "lang_code": "es",
        "page_title": "Correo Web — KP4PRA TNC",
        "heading": "Correo Web",
        "language": "Idioma",
        "english": "English",
        "spanish": "Español",

        "notice_heading": "Antes de continuar — por favor lea",
        "privacy_heading": "Sin Privacidad",
        "privacy_body": (
            "Las señales de radioaficionado no están cifradas y pueden ser "
            "monitoreadas por cualquier persona con el equipo receptor "
            "adecuado. Los mensajes almacenados o transmitidos por este "
            "sistema pueden ser revisados por los fideicomisarios de la "
            "estación, administradores y operadores de radioaficionado con "
            "licencia responsables de operar el sistema. No espere privacidad "
            "al usar este servicio."
        ),
        "rules_heading": "Reglas de Radioaficionado",
        "rules_body": (
            "Los mensajes transmitidos por radioaficionado deben cumplir con "
            "las regulaciones de radioaficionado aplicables, incluida la Parte "
            "97 de la FCC cuando se opera bajo jurisdicción de los Estados "
            "Unidos. Los mensajes no deben contener contenido indecente u "
            "obsceno, comunicaciones comerciales o de negocios prohibidas, "
            "comunicaciones para beneficio económico directo, ni otro "
            "contenido cuya transmisión por radioaficionado esté prohibida. El "
            "fideicomisario de la estación puede rechazar cualquier mensaje que "
            "no pueda transmitirse legalmente por la vía de radioaficionado "
            "seleccionada."
        ),
        "agree_label": "He leído y acepto el aviso anterior.",
        "agree_button": "Acepto — Continuar",

        "reply_notice": (
            "Esta interfaz web de KP4PRA TNC no recibe respuestas ni correo "
            "entrante directo. Cualquier respuesta del destinatario se enviará "
            "a la dirección de correo personal que usted indique en el campo "
            "Responder-a (Reply-To)."
        ),

        "compose_heading": "Redactar un mensaje",
        "to_label": "Correo electrónico de destino",
        "replyto_label": "Su correo de respuesta (Reply-To)",
        "subject_label": "Asunto",
        "body_label": "Mensaje",
        "subject_hint": "Que sea breve — menos de 50 caracteres es lo ideal.",
        "body_hint": "Que sea conciso — unos 300 caracteres es lo habitual.",
        "chars": "caracteres",
        "submit_button": "Enviar para revisión",

        "email_required": "Se requiere una dirección de correo de destino.",
        "email_invalid": "Por favor ingrese una dirección de correo válida.",
        "email_intl_unsupported": (
            "Este transporte de radio no admite direcciones de correo "
            "internacionalizadas (no ASCII). Por favor use una dirección de "
            "correo estándar."
        ),
        "replyto_required": "Se requiere su correo de respuesta (Reply-To).",
        "subject_too_long": "El asunto es demasiado largo.",
        "body_required": "Por favor escriba un mensaje.",
        "body_too_long": "El mensaje es demasiado largo.",
        "agree_required": "Debe aceptar el aviso antes de enviar.",
        "csrf_error": "Su sesión expiró. Recargue la página e intente de nuevo.",
        "queue_error": "No se pudo guardar el mensaje. Intente más tarde.",
        "disabled_notice": "La interfaz de Correo Web no está disponible por ahora.",

        "confirm_heading": "Mensaje enviado",
        "confirm_body": (
            "Su mensaje ha sido enviado para revisión por el fideicomisario de "
            "la estación. Aún no ha sido transmitido. El fideicomisario debe "
            "aprobar el mensaje antes de que pueda enviarse por Winlink."
        ),
        "compose_another": "Redactar otro mensaje",
        "back_home": "Volver al inicio",
    },
}


def get_strings(lang: str) -> dict:
    return STRINGS.get(lang if lang in LANGUAGES else DEFAULT_LANG,
                       STRINGS[DEFAULT_LANG])


def normalize_lang(lang: str) -> str:
    return lang if lang in LANGUAGES else DEFAULT_LANG
