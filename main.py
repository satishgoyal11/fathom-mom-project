import base64
import hashlib
import hmac
import os
import time
from datetime import datetime
import zoneinfo
import requests
import markdown
from google import genai
from fastapi import FastAPI, HTTPException, Request

app = FastAPI()
client = genai.Client()

# Deduplication cache: stores processed webhook IDs and their timestamp
PROCESSED_WEBHOOKS = {}

SYSTEM_PROMPT = """
You are an executive assistant creating high-grade Minutes of Meeting (MOM).
Analyze the provided transcript and produce a detailed, highly structured summary.

Do NOT repeat the Meeting Title, Date/Time, or Attendees header at the very top, as those will be inserted dynamically by the system.

Structure your response starting directly from these sections:
1. Executive Summary: High-level overview of the meeting purpose and key outcomes.
2. Key Discussion Points: Detailed bulleted breakdown of major topics, insights, and updates shared.
3. Decisions Made: Clear bulleted list of finalized decisions.
4. Action Items Table: A markdown table with columns: Action Item | Owner | Deadline | Priority.
5. Risks & Open Questions: Any unresolved issues, dependencies, or items for the next meeting.

Be thorough, professional, and clear. Avoid generic placeholder text.
"""

def send_html_email_via_resend(mom_markdown: str, meeting_title: str, meeting_date: str, attendees_str: str):
    resend_api_key = os.getenv("RESEND_API_KEY")
    destination_email = os.getenv("MY_EMAIL")

    if not resend_api_key or not destination_email:
        print("Error: Missing RESEND_API_KEY or MY_EMAIL environment variables.")
        return

    mom_body_html = markdown.markdown(mom_markdown, extensions=['tables', 'fenced_code'])

    full_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; line-height: 1.6; color: #1e293b; background-color: #f8fafc; padding: 20px; }}
            .container {{ max-width: 800px; background: #ffffff; padding: 35px; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); margin: 0 auto; border-top: 6px solid #2563eb; }}
            h1 {{ color: #0f172a; font-size: 24px; margin-top: 0; margin-bottom: 5px; }}
            .meta-box {{ background-color: #f1f5f9; padding: 15px 20px; border-radius: 6px; margin-bottom: 25px; border-left: 4px solid #2563eb; }}
            .meta-item {{ font-size: 14px; color: #334155; margin: 4px 0; }}
            .meta-item strong {{ color: #0f172a; }}
            h2 {{ color: #2563eb; font-size: 18px; margin-top: 24px; font-weight: 600; border-left: 4px solid #2563eb; padding-left: 10px; }}
            p, li {{ font-size: 14px; color: #334155; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 15px; font-size: 13px; }}
            th {{ background-color: #f1f5f9; color: #0f172a; text-align: left; padding: 12px; border: 1px solid #cbd5e1; font-weight: 600; }}
            td {{ padding: 10px; border: 1px solid #cbd5e1; color: #334155; }}
            tr:nth-child(even) {{ background-color: #f8fafc; }}
            .footer {{ margin-top: 35px; font-size: 12px; color: #94a3b8; text-align: center; border-top: 1px solid #e2e8f0; padding-top: 15px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📋 Minutes of Meeting: {meeting_title}</h1>
            <div class="meta-box">
                <div class="meta-item"><strong>Meeting Topic:</strong> {meeting_title}</div>
                <div class="meta-item"><strong>Date & Time:</strong> {meeting_date}</div>
                <div class="meta-item"><strong>Participants:</strong> {attendees_str}</div>
            </div>
            {mom_body_html}
            <div class="footer">Generated automatically via Fathom & Gemini AI</div>
        </div>
    </body>
    </html>
    """

    doc_base64 = base64.b64encode(full_html.encode('utf-8')).decode('utf-8')
    clean_title = "".join([c if c.isalnum() else "_" for c in meeting_title])

    url = "https://api.resend.com/emails"
    headers = {
        "Authorization": f"Bearer {resend_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "from": "Fathom MOM System <onboarding@resend.dev>",
        "to": [destination_email],
        "subject": f"📄 MOM: {meeting_title}",
        "html": full_html,
        "attachments": [
            {
                "filename": f"MOM_{clean_title}.doc",
                "content": doc_base64
            }
        ]
    }

    try:
        response = requests.post(url, json=payload, headers=headers)
        print(f"Resend Status Code: {response.status_code}")
    except Exception as err:
        print(f"Failed to connect to Resend API: {err}")


@app.post("/webhook")
async def handle_webhook(request: Request):
    webhook_id = request.headers.get("webhook-id") or request.headers.get("x-request-id")
    webhook_timestamp = request.headers.get("webhook-timestamp")
    webhook_signature = request.headers.get("webhook-signature")

    # --- DEDUPLICATION CHECK ---
    current_time = time.time()
    # Clean up entries older than 10 minutes (600 seconds)
    expired_keys = [k for k, v in PROCESSED_WEBHOOKS.items() if current_time - v > 600]
    for k in expired_keys:
        del PROCESSED_WEBHOOKS[k]

    if webhook_id:
        if webhook_id in PROCESSED_WEBHOOKS:
            print(f"Duplicate webhook detected ({webhook_id}). Skipping execution.")
            return {"status": "ignored", "reason": "Duplicate webhook payload"}
        PROCESSED_WEBHOOKS[webhook_id] = current_time

    # --- SIGNATURE VERIFICATION ---
    raw_body = await request.body()
    secret = os.getenv("FATHOM_WEBHOOK_SECRET")

    if secret and webhook_signature:
        signed_content = f"{webhook_id}.{webhook_timestamp}.{raw_body.decode('utf-8')}"
        secret_bytes = base64.b64decode(secret.split("_")[1])
        expected_sig = base64.b64encode(
            hmac.new(secret_bytes, signed_content.encode("utf-8"), hashlib.sha256).digest()
        ).decode("utf-8")

        if not any(
            sig.strip() == f"v1,{expected_sig}"
            for sig in webhook_signature.split(" ")
        ):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    body = await request.json()
    
    meeting_title = body.get("title") or body.get("name") or "General Discussion"
    
    # --- IST TIME ZONE CONVERSION ---
    created_at_raw = body.get("created_at") or body.get("started_at")
    if created_at_raw:
        try:
            dt_utc = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
            ist_tz = zoneinfo.ZoneInfo("Asia/Kolkata")
            dt_ist = dt_utc.astimezone(ist_tz)
            meeting_date = dt_ist.strftime("%B %d, %Y at %I:%M %p IST")
        except Exception as e:
            print(f"Timestamp parsing error: {e}")
            meeting_date = created_at_raw
    else:
        ist_tz = zoneinfo.ZoneInfo("Asia/Kolkata")
        meeting_date = datetime.now(ist_tz).strftime("%B %d, %Y at %I:%M %p IST")

    attendees_data = body.get("recording_attendees") or body.get("attendees") or []
    attendees_list = []
    if isinstance(attendees_data, list):
        for att in attendees_data:
            if isinstance(att, dict):
                name = att.get("name") or att.get("email")
                if name:
                    attendees_list.append(name)
            elif isinstance(att, str):
                attendees_list.append(att)
    
    attendees_str = ", ".join(attendees_list) if attendees_list else "Not Specified"

    transcript = body.get("transcript", "")
    if not transcript:
        raise HTTPException(status_code=400, detail="No transcript found")

    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=f"{SYSTEM_PROMPT}\n\nMeeting Title: {meeting_title}\nTranscript:\n{transcript}"
    )
    mom_result = response.text

    send_html_email_via_resend(mom_result, meeting_title, meeting_date, attendees_str)

    return {"status": "success", "mom": mom_result}
