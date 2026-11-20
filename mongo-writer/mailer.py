"""Envoi d'e-mails SMTP asynchrone avec fallback si SMTP_HOST n'est pas configuré."""
import html
import logging
import os
from email.message import EmailMessage

import aiosmtplib

log = logging.getLogger(__name__)

SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "").strip()
SMTP_FROM = os.environ.get("SMTP_FROM", "Videolog <noreply@videolog.app>").strip()
SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "false").strip().lower() == "true"


def smtp_configured() -> bool:
    return bool(SMTP_HOST)


async def send_invitation_email(
    to_email: str,
    org_name: str,
    role: str,
    invite_url: str,
    inviter_name: str,
) -> bool:
    """Envoie l'e-mail d'invitation. Retourne True si envoyé, False si fallback manuel."""
    if not smtp_configured():
        log.info(
            "SMTP non configuré ; lien d'invitation généré pour %s (%s) : %s",
            to_email,
            org_name,
            invite_url,
        )
        return False

    role_label = "coach" if role == "coach" else "athlète"
    subject = f"Invitation à rejoindre {org_name} sur Videolog"

    text_body = (
        f"Bonjour,\n\n"
        f"{inviter_name} vous invite à rejoindre {org_name} sur Videolog en tant que {role_label}.\n\n"
        f"Ouvrez ce lien pour créer ou relier votre compte (valable 7 jours) :\n"
        f"{invite_url}\n\n"
        f"Videolog est réservé aux personnes âgées de 15 ans et plus.\n"
    )

    safe_org = html.escape(org_name)
    safe_inviter = html.escape(inviter_name)
    safe_url = html.escape(invite_url, quote=True)

    html_body = f"""<!DOCTYPE html>
<html lang="fr">
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #1a1d21; line-height: 1.5; max-width: 560px; margin: 0 auto; padding: 24px;">
  <h2 style="margin-bottom: 12px;">Rejoindre {safe_org} sur Videolog</h2>
  <p><strong>{safe_inviter}</strong> vous invite à rejoindre l'organisation <strong>{safe_org}</strong> en tant que <strong>{role_label}</strong>.</p>
  <p style="margin: 24px 0;">
    <a href="{safe_url}" style="background-color: #1971c2; color: #ffffff; padding: 12px 20px; border-radius: 6px; text-decoration: none; display: inline-block; font-weight: 600;">
      Accepter l'invitation
    </a>
  </p>
  <p style="font-size: 13px; color: #495057;">
    Si le bouton ne fonctionne pas, copiez ce lien dans votre navigateur :<br>
    <a href="{safe_url}">{safe_url}</a>
  </p>
  <hr style="border: none; border-top: 1px solid #e9ecef; margin: 24px 0;">
  <p style="font-size: 12px; color: #868e96;">
    Ce lien expire dans 7 jours. L'inscription est réservée aux personnes âgées de 15 ans et plus.
  </p>
</body>
</html>"""

    return await _send_email(to_email, subject, text_body, html_body)


async def _send_email(to_email: str, subject: str, text_body: str, html_body: str) -> bool:
    if not smtp_configured():
        log.info("SMTP non configuré ; e-mail ignoré pour %s (%s)", to_email, subject)
        return False
    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    try:
        await aiosmtplib.send(
            msg,
            hostname=SMTP_HOST,
            port=SMTP_PORT,
            username=SMTP_USER or None,
            password=SMTP_PASSWORD or None,
            use_tls=SMTP_USE_TLS,
            start_tls=not SMTP_USE_TLS,
            timeout=10,
        )
        return True
    except Exception:
        log.exception("Échec de l'envoi SMTP vers %s", to_email)
        return False


async def send_comment_notification_email(
    to_email: str,
    author_name: str,
    item_title: str,
    comment_body: str,
    item_url: str,
) -> bool:
    """Envoie un e-mail lors d'un nouveau commentaire coach/athlète."""
    subject = f"Nouveau message sur « {item_title} » — Videolog"
    text_body = (
        f"Bonjour,\n\n"
        f"{author_name} a commenté « {item_title} » :\n\n"
        f"\"{comment_body}\"\n\n"
        f"Voir dans Videolog : {item_url}\n"
    )
    safe_author = html.escape(author_name)
    safe_title = html.escape(item_title)
    safe_comment = html.escape(comment_body)
    safe_url = html.escape(item_url, quote=True)
    html_body = f"""<!DOCTYPE html>
<html lang="fr">
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #1a1d21; line-height: 1.5; max-width: 560px; margin: 0 auto; padding: 24px;">
  <h2 style="margin-bottom: 12px;">Nouveau message sur {safe_title}</h2>
  <p><strong>{safe_author}</strong> a écrit :</p>
  <blockquote style="margin: 12px 0; padding: 12px 16px; background: #f1f3f5; border-left: 4px solid #18abf8; border-radius: 4px;">
    {safe_comment}
  </blockquote>
  <p style="margin: 20px 0;">
    <a href="{safe_url}" style="background-color: #1971c2; color: #ffffff; padding: 10px 18px; border-radius: 6px; text-decoration: none; display: inline-block; font-weight: 600;">
      Ouvrir dans Videolog
    </a>
  </p>
</body>
</html>"""
    return await _send_email(to_email, subject, text_body, html_body)


async def send_join_request_received_email(
    to_email: str,
    org_name: str,
    applicant_name: str,
    sports_labels: list[str],
    message: str | None,
    review_url: str,
) -> bool:
    """Notifie le staff d'un club/coach qu'une nouvelle demande d'adhésion mobile a été reçue."""
    subject = f"Nouvelle demande d'adhésion pour {org_name} — Videolog"
    sports_str = ", ".join(sports_labels) if sports_labels else "Non précisés"
    msg_txt = f'\nMessage : "{message}"\n' if message else ""
    text_body = (
        f"Bonjour,\n\n"
        f"{applicant_name} demande à rejoindre {org_name} sur Videolog.\n"
        f"Sport(s) souhaité(s) : {sports_str}.{msg_txt}\n"
        f"Consulter et traiter la demande : {review_url}\n"
    )
    safe_org = html.escape(org_name)
    safe_applicant = html.escape(applicant_name)
    safe_sports = html.escape(sports_str)
    safe_url = html.escape(review_url, quote=True)
    msg_html = (
        f'<blockquote style="margin: 12px 0; padding: 12px 16px; background: #f1f3f5; border-left: 4px solid #18abf8; border-radius: 4px;">'
        f"{html.escape(message)}</blockquote>"
        if message
        else ""
    )
    html_body = f"""<!DOCTYPE html>
<html lang="fr">
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #1a1d21; line-height: 1.5; max-width: 560px; margin: 0 auto; padding: 24px;">
  <h2 style="margin-bottom: 12px;">Nouvelle demande d'adhésion — {safe_org}</h2>
  <p><strong>{safe_applicant}</strong> souhaite rejoindre votre structure sur Videolog.</p>
  <p><strong>Sport(s) souhaité(s) :</strong> {safe_sports}</p>
  {msg_html}
  <p style="margin: 20px 0;">
    <a href="{safe_url}" style="background-color: #1971c2; color: #ffffff; padding: 10px 18px; border-radius: 6px; text-decoration: none; display: inline-block; font-weight: 600;">
      Examiner la demande
    </a>
  </p>
</body>
</html>"""
    return await _send_email(to_email, subject, text_body, html_body)


async def send_join_request_decision_email(
    to_email: str,
    org_name: str,
    approved: bool,
    decision_note: str | None,
    app_url: str,
) -> bool:
    """Notifie l'athlète mobile de l'acceptation ou du refus de sa demande d'adhésion."""
    status_word = "acceptée" if approved else "refusée"
    subject = f"Votre demande d'adhésion à {org_name} a été {status_word} — Videolog"
    note_txt = f'\nMessage du club/coach : "{decision_note}"\n' if decision_note else ""
    text_body = (
        f"Bonjour,\n\n"
        f"Votre demande d'adhésion à {org_name} sur Videolog a été {status_word}.{note_txt}\n"
        f"Ouvrir Videolog : {app_url}\n"
    )
    safe_org = html.escape(org_name)
    safe_url = html.escape(app_url, quote=True)
    note_html = (
        f'<blockquote style="margin: 12px 0; padding: 12px 16px; background: #f1f3f5; border-left: 4px solid {"#2b8a3e" if approved else "#868e96"}; border-radius: 4px;">'
        f"{html.escape(decision_note)}</blockquote>"
        if decision_note
        else ""
    )
    html_body = f"""<!DOCTYPE html>
<html lang="fr">
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #1a1d21; line-height: 1.5; max-width: 560px; margin: 0 auto; padding: 24px;">
  <h2 style="margin-bottom: 12px;">Demande d'adhésion {status_word}</h2>
  <p>Votre demande pour rejoindre <strong>{safe_org}</strong> a été <strong>{status_word}</strong>.</p>
  {note_html}
  <p style="margin: 20px 0;">
    <a href="{safe_url}" style="background-color: #1971c2; color: #ffffff; padding: 10px 18px; border-radius: 6px; text-decoration: none; display: inline-block; font-weight: 600;">
      Ouvrir Videolog
    </a>
  </p>
</body>
</html>"""
    return await _send_email(to_email, subject, text_body, html_body)
