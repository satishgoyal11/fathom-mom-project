import base64
import hashlib
import hmac
import os
import requests
import markdown
from google import genai
from fastapi import FastAPI, HTTPException, Request

app = FastAPI()

client = genai.Client()

SYSTEM_PROMPT = """
You are an executive assistant creating high-grade Minutes of Meeting (MOM).
Analyze the provided transcript and produce a detailed, highly structured summary.

Structure the response with clear headings:
1. Executive Summary: High-level overview of the meeting purpose and outcomes.
2. Meeting Details & Attendees: Extract topic, date/time (if available), and participant list.
3. Key Discussion Points: Bulleted breakdown of major topics, insights, and updates shared.
4. Decisions Made: Clear bulleted list of finalized decisions.
5. Action Items Table: A markdown table with columns: Action Item, Owner, Deadline, Priority.
6. Risks & Open Questions: Any unresolved issues, dependencies, or items for next meeting.

Be thorough, professional, and clear. Avoid generic placeholder text.
"""

def send_html_email_via_resend(mom_markdown: str):
    resend_api_key = os.getenv("RESEND_API_KEY")
    destination_email = os.getenv("MY_EMAIL")

    if not resend_api_key or not destination_email:
        print("Error: Missing RESEND_API_KEY or MY_EMAIL environment variables.")
        return

    # Convert Markdown output to rich HTML
    mom_body_html = markdown.markdown(mom_markdown, extensions=['tables', 'fenced_code'])

    # Styled HTML Template for professional look inside Gmail / Outlook
    full_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; line-height: 1.6; color: #2c3e50; background-color: #f4f6f9; padding: 20px; }}
            .container {{ max-width: 750px; background: #ffffff; padding: 30px; border-radius: 8px; box-shadow: 0 4px 10px rgba(0,0,0,0.05); margin: 0 auto; border-top: 5px solid #2563eb; }}
            h1 {{ color: #1e293b; font-size: 22px; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; margin-top: 0; }}
            h2 {{ color: #2563eb; font-size: 17px; margin-top: 24px; font-weight: 600; border-left: 4px solid #2563eb; padding-left: 10px; }}
            p, li {{ font-size: 14px; color: #334155; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 15px; font-size: 13px; }}
            th {{ background-color: #f1f5f9; color: #1e293b; text-align: left; padding: 10px; border: 1px solid #cbd5e1; font-weight: 600; }}
            td {{ padding: 10px; border: 1px solid #cbd5e1; color: #334155; }}
            tr:nth-child(even) {{ background-color: #f8fafc; }}
            .footer {{ margin-top: 30px; font-size: 12px; color: #94a3b8; text-align: center; border-top: 1px solid #e2e8f0; padding-top: 15px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📋 Minutes of Meeting (MOM)</h1>
            {mom_body_html}
            <div class="footer">Generated automatically via Fathom & Gemini AI</div>
        </div>
    </body>
    </html>
    """

    url = "https://api.resend.com/emails"
    headers = {
        "Authorization": f"Bearer {resend_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "from": "Fathom MOM System <onboarding@resend.dev>",
        "to": [destination_email],
        "subject": "📄 Executive Minutes of Meeting (MOM)",
        "html": full_html,
    }

    try:
        response = requests.post(url, json=payload, headers=headers)
        print(f"Resend Status Code: {response.status_code}")
        print(f"Resend Response Body: {response.text}")
    except Exception as err:
        print(f"Failed to connect to Resend API: {err}")


@app.post("/webhook")
async def handle_webhook(request: Request):
    webhook_id = request.headers.get("webhook-id")
    webhook_timestamp = request.headers.get("webhook-timestamp")
    webhook_signature = request.headers.get("webhook-signature")

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
    transcript = body.get("transcript", "")

    if not transcript:
        raise HTTPException(status_code=400, detail="No transcript found")

    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=f"{SYSTEM_PROMPT}\n\nTranscript:\n{transcript}"
    )
    mom_result = response.text

    send_html_email_via_resend(mom_result)

    return {"status": "success", "mom": mom_result}
