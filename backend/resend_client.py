"""
resend_client.py
Thin wrapper around Resend for transactional email (password reset today;
any future verification email would reuse this same function).
"""

import os

import resend

resend.api_key = os.getenv("RESEND_API_KEY", "")
FROM_ADDRESS = os.getenv("RESEND_FROM_ADDRESS", "Jahvi <noreply@jahvi.com>")


def send_password_reset_email(to_email: str, reset_link: str) -> None:
    resend.Emails.send({
        "from": FROM_ADDRESS,
        "to": to_email,
        "subject": "Reset your Jahvi password",
        "html": f"""
            <p>Click the link below to reset your password. This link expires in 30 minutes.</p>
            <p><a href="{reset_link}">Reset Password</a></p>
            <p>If you didn't request this, you can safely ignore this email.</p>
        """,
    })
