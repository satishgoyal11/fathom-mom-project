import base64
import hashlib
import hmac
import os
import time
import json
import re
from datetime import datetime, timedelta
import zoneinfo
import requests
import markdown
import gspread
from google.oauth2.service_account import Credentials
from google import genai
from fastapi import FastAPI, HTTPException, Request

app = FastAPI()

PROCESSED_WEBHOOKS = {}

SYSTEM_PROMPT = """
You are an executive assistant creating high-grade Minutes of Meeting (MOM).
Analyze the provided transcript and produce a detailed, highly structured summary.

Do NOT repeat the Meeting Title, Date/Time, or Attendees header at the top.

Structure your response starting directly from these sections:
1. Executive Summary: High-level overview of the meeting purpose and key outcomes.
2. Key Discussion Points: Detailed bulleted breakdown of major topics, insights, and updates shared.
3. Decisions Made: Clear bulleted list of finalized decisions.
4. Action Items Table: A markdown table with columns: Action Item | Owner | Deadline | Priority.
   CRITICAL FOR DEADLINE: The Deadline column MUST be an explicit calendar date formatted strictly as YYYY-MM-DD. Calculate the target date relative to today's date based on the discussion (e.g., if the transcript says "in 2 weeks", output the exact date two weeks from today). NEVER use text like "1 Week", "3-4 Weeks", or "Immediate".
5. Risks & Open Questions: Any unresolved issues, dependencies, or items for the next meeting.

Be thorough, professional, and clear. Avoid generic placeholder text.
"""

def parse_deadline_to_date(deadline_str: str) -> str:
    """Extracts ISO date or converts text ranges like '3-4 weeks' to explicit dates."""
    today = datetime.now()
    text = deadline_str.strip().lower()

    # 1. Check if Gemini already provided a valid YYYY-MM-DD date
    iso_match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", text)
    if iso_match:
        return iso_match.group(0)

    if not text or "immediate" in text or "asap" in text or "today" in text:
        return today.strftime("%Y-%m-%d")

    # 2. Extract digits from single numbers or ranges (e.g. "3-4 weeks", "2 weeks", "1 month")
    numbers = [int(n) for n in re.findall(r"\d+", text)]
    
    if numbers:
        # If a range like "3-4 weeks" is given, take the upper bound (4 weeks)
        num = numbers[-1]
        
        if "day" in text:
            target_date = today + timedelta(days=num)
        elif "week" in text:
            target_date = today + timedelta(weeks=num)
        elif "month" in text:
            target_date = today + timedelta(days=num * 30)
        else:
            target_date = today + timedelta(days=num)
            
        return target_date.strftime("%Y-%m-%d")

    return (today + timedelta(days=7)).strftime("%Y-%m-%d")

def generate_mom_with_gemini(prompt: str, transcript: str) -> str:
    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not gemini_key:
        print("ERROR: Neither GEMINI_API_KEY nor GOOGLE_API_KEY was found in environment variables.")
        raise RuntimeError("GEMINI_API_KEY is missing from environment variables.")

    client = genai.Client(api_key=gemini_key)
    models = ["gemini-3.6-flash", "gemini-3.1-pro-preview"]
    
    # Inject current date explicitly into Gemini's runtime context
    today_str = datetime.now().strftime("%B %d, %Y (%Y-%m-%d)")
    dynamic_prompt = f"CRITICAL CONTEXT: Today's date is {today_str}. All calculated deadlines MUST be based on this current year and date.\n\n" + prompt
    full_prompt = f"{dynamic_prompt}\n\nTranscript:\n{transcript}"

    for model_name in models:
        try:
            print(f"Attempting MOM generation with Gemini model: {model_name}...")
            response = client.models.generate_content(
                model=model_name,
                contents=full_prompt,
            )
            if response.text:
                print(f"Successfully generated response using {model_name}")
                return response.text
        except Exception as e:
            print(f"FAILED on model {model_name}: {type(e).__name__} - {e}")
            time.sleep(1)

    raise RuntimeError("All Gemini model generation attempts failed.")

def append_action_items_to_sheets(mom_markdown: str, meeting_title: str):
    credentials_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    spreadsheet_id = os.getenv("SPREADSHEET_ID")

    if not credentials_json or not spreadsheet_id:
        print("Google Sheets credentials or Spreadsheet ID missing.")
        return

    try:
        creds_dict = json.loads(credentials_json)
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        gc = gspread.authorize(creds)
        sheet = gc.open_by_key(spreadsheet_id).sheet1

        today_str = datetime.now().strftime("%Y-%m-%d")
        lines = mom_markdown.split("\n")
        
        for line in lines:
            if "|" in line:
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) >= 4:
                    first_col = parts[0].lower()
                    if "action item" in first_col or "---" in first_col or "task" in first_col:
                        continue
                    
                    action_item = parts[0]
                    owner = parts[1]
                    raw_deadline = parts[2]
                    priority = parts[3]
                    
                    calculated_deadline = parse_deadline_to_date(raw_deadline)
                    
                    sheet.append_row([
                        meeting_title,
                        today_str,
                        action_item,
                        owner,
                        calculated_deadline,
                        priority,
                        "Pending",
                        "No"
                    ])
        print("Successfully synced action items to Google Sheets.")
    except Exception as e:
        print(f"Error appending to Google Sheets: {e}")

def send_html_email_via_resend(mom_markdown: str, meeting_title: str, meeting_date: str, attendees_str: str):
    resend_api_key = os.getenv("RESEND_API_KEY")
    destination_email = os.getenv("MY_EMAIL")

    if not resend_api_key or not destination_email:
        print("Error: Missing RESEND_API_KEY or MY_EMAIL environment variables.")
        return

    clean_markdown = mom_markdown.replace("<hr />", "").replace("---", "")
    mom_body_html = markdown.markdown(clean_markdown, extensions=['tables', 'fenced_code'])

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
            h2, h3 {{ color: #1d4ed8; font-size: 18px; margin-top: 24px; font-weight: 600; border-left: 4px solid #2563eb; padding-left: 10px; }}
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
            <div class="footer">Generated automatically via AI Engine</div>
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
        "from": "Executive MOM System <onboarding@resend.dev>",
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

    current_time = time.time()
    expired_keys = [k for k, v in PROCESSED_WEBHOOKS.items() if current_time - v > 600]
    for k in expired_keys:
        del PROCESSED_WEBHOOKS[k]

    if webhook_id:
        if webhook_id in PROCESSED_WEBHOOKS:
            return {"status": "ignored", "reason": "Duplicate webhook payload"}
        PROCESSED_WEBHOOKS[webhook_id] = current_time

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
    
    created_at_raw = body.get("created_at") or body.get("started_at")
    ist_tz = zoneinfo.ZoneInfo("Asia/Kolkata")
    if created_at_raw:
        try:
            dt_utc = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
            dt_ist = dt_utc.astimezone(ist_tz)
            meeting_date = dt_ist.strftime("%B %d, %Y at %I:%M %p IST")
        except Exception:
            meeting_date = created_at_raw
    else:
        meeting_date = datetime.now(ist_tz).strftime("%B %d, %Y at %I:%M %p IST")

    attendees_list = []
    attendees_data = body.get("recording_attendees") or body.get("attendees") or []
    
    if isinstance(attendees_data, list):
        for att in attendees_data:
            if isinstance(att, dict):
                name = att.get("name") or att.get("display_name") or att.get("email")
                if name:
                    attendees_list.append(name)
            elif isinstance(att, str):
                attendees_list.append(att)

    if not attendees_list:
        speakers_data = body.get("speakers") or []
        for spk in speakers_data:
            if isinstance(spk, dict) and spk.get("name"):
                attendees_list.append(spk.get("name"))

    attendees_str = ", ".join(list(set(attendees_list))) if attendees_list else "Extracted from Call"

    transcript = body.get("transcript", "")
    if not transcript:
        raise HTTPException(status_code=400, detail="No transcript found")

    mom_result = generate_mom_with_gemini(SYSTEM_PROMPT, transcript)

    # Send HTML Email
    send_html_email_via_resend(mom_result, meeting_title, meeting_date, attendees_str)

    # Sync action items to Google Sheets
    append_action_items_to_sheets(mom_result, meeting_title)

    return {"status": "success", "mom": mom_result}
