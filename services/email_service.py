import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
from jinja2 import Environment, FileSystemLoader

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

# Jinja environment
env = Environment(loader=FileSystemLoader("templates"))

def render_form_submit_th(context: dict):
    template = env.get_template("form_submit_th.html")
    return template.render(context)

def send_email(to_email: str, subject: str, body: str, cc: list[str] | None = None):

    msg = MIMEMultipart()
    msg["From"] = SMTP_USER
    msg["To"] = to_email
    msg["Subject"] = subject

    if cc:
        msg["Cc"] = ", ".join(cc)

    msg.attach(MIMEText(body, "html"))

    recipients = [to_email] + (cc if cc else [])

    server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
    server.starttls()
    server.login(SMTP_USER, SMTP_PASSWORD)
    server.sendmail(SMTP_USER, recipients, msg.as_string())
    server.quit()
