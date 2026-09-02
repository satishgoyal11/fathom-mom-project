import base64
import hashlib
import hmac
import os
from datetime import datetime
import requests
import markdown
from google import genai
from fastapi import FastAPI, HTTPException, Request

app = FastAPI()
client = genai.Client()

# -------------------------------------------------------------------
# PROMPTS
# -------------------------------------------------------------------

CLASSIFIER_PROMPT = """
Analyze the following transcript of an IT project meeting. 
Determine whether this meeting is primarily:
1. 'FRS' - A business requirements gathering / scoping / discovery session focused on defining project scope, user features, workflows, software requirements, or technical constraints.
2. 'MOM' - A standard project sync, general discussion, execution update, or operational status call.

Respond with EXACTLY ONE word: either 'FRS' or 'MOM'. Do not include extra punctuation or explanation.
"""

FRS_SYSTEM_PROMPT = """
You are an expert IT Project Manager specializing in vendor procurement and business requirements gathering.
Analyze the provided meeting transcript and extract requirements into a formal Functional Requirement Specifications (FRS) document.

Strictly adhere to this 6-part structure:

# 1. Project Overview & Objectives
- Core Problem Statement: (Describe current pain points or manual processes discussed)
- Key Business Objectives: (Measurable success metrics or business outcomes)

# 2. Business Scope & Boundaries
- In-Scope: (Features, process steps, user roles, or departments directly included)
- Out-of-Scope: (Specific capabilities or systems explicitly excluded to prevent scope creep)

# 3. User Roles & Access Matrix
Generate a markdown table:
| Role Name | Access Level / Responsibilities | Expected Users |

# 4. Functional Requirements Checklist
Generate a markdown table:
| Requirement ID | Module / Area | Business Requirement Description | Priority (Must / Should / Could) |
(Assign IDs like FR-01, FR-02, FR-03 sequentially)

# 5. Technical & Integration Constraints
- Legacy Systems Integration: (Existing platforms mentioned, e.g., SAP ERP, Salesforce)
- Deployment Preference: (Cloud-hosted, On-Premise, or SaaS if discussed)
- Data & Security Compliance: (SSO, encryption, or corporate security needs)

# 6. Assumptions, Dependencies & Risks
- Assumptions: (e.g., Timeline or API availability assumptions)
- Dependencies: (Dependencies on internal teams or budget sign-offs)
- Known Risks: (Technical or operational risks identified)

Be detailed, technical, concise, and professional. Avoid generic placeholder text.
"""

MOM_SYSTEM_PROMPT = """
You are an executive assistant creating high-grade Minutes of Meeting (MOM).
Analyze the provided transcript and produce a detailed, highly structured summary.

Do NOT repeat the Meeting Title, Date/Time, or Attendees header at the top, as those will be inserted dynamically by the system.

Structure your response starting directly from these sections:
1. Executive Summary: High-level overview of the meeting purpose and key outcomes.
2. Key Discussion Points: Detailed bulleted breakdown of major topics, insights, and updates shared.
3. Decisions Made: Clear bulleted list of finalized decisions.
4. Action Items Table: A markdown table with columns: Action Item | Owner | Deadline | Priority.
5. Risks & Open Questions: Any unresolved issues, dependencies, or items for the next meeting.

Be thorough, professional, and clear. Avoid generic placeholder text.
"""

# -------------------------------------------------------------------
# EMAIL SENDER FUNCTION
# -------------------------------------------------------------------

def send_email_via_resend(doc_markdown: str, meeting_title: str, meeting_date: str, attendees_str: str, doc_type: str):
    resend_api_key = os.getenv("RESEND_API_KEY")
    destination_email = os.getenv("MY_EMAIL")

    if not resend_api_key or not destination_email:
        print("Error: Missing RESEND_API_KEY or MY_EMAIL environment variables.")
        return

    body_html = markdown.markdown(doc_markdown, extensions=['tables', 'fenced_code'])

    # Dynamic styling depending on document type
    theme_color = "#0284c7" if doc_type == "FRS" else "#2563eb"
    doc_label = "📑 Functional Requirement Specifications (FRS)" if doc_type == "FRS" else "📋 Minutes of Meeting (MOM)"
    file_prefix = "FRS" if doc_type == "FRS" else "MOM"

    full_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; line-height: 1.6; color: #1e293b; background-color: #f8fafc; padding: 20px; }}
            .container {{ max-width: 850px; background: #ffffff; padding: 35px; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); margin: 0 auto; border-top: 6px solid {theme_color}; }}
            h1 {{ color: #0f172a; font-size: 24px; margin-top: 0; margin-bottom: 5px; }}
            .meta-box {{ background-color: #f0f9ff; padding: 15px 20px; border-radius: 6px; margin-bottom: 25px; border-left: 4px solid {theme_color}; }}
            .meta-item {{ font-size: 14px; color: #334155; margin: 4px 0; }}
            .meta-item strong {{ color: #0f172a; }}
            h2 {{ color: {theme_color}; font-size: 18px; margin-top: 26px; font-weight: 600; border-bottom: 2px solid #e2e8f0; padding-bottom: 6px; }}
            p, li {{ font-size: 14px; color: #334155; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 15px; font-size: 13px; }}
            th {{ background-color: #f1f5f9; color: #0f172a; text-align: left; padding: 10px; border: 1px solid #cbd5e1; font-weight: 600; }}
            td {{ padding: 10px; border: 1px solid #cbd5e1; color: #334155; }}
            tr:nth-child(even) {{ background-color: #f8fafc; }}
            .footer {{ margin-top: 35px; font-size: 12px; color: #94a3b8; text-align: center; border-top: 1px solid #e2e8f0; padding-top: 15px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>{doc_label}</h1>
            <div class="meta-box">
                <div class="meta-item"><strong>Meeting Topic:</strong> {meeting_title}</div>
                <div class="meta-item"><strong>Date & Time:</strong> {meeting_date}</div>
                <div class="meta-item"><strong>Participants:</strong> {attendees_str}</div>
                <div class="meta-item"><strong>Document Type:</strong> Auto-classified as {doc_type}</div>
            </div>
            {body_html}
            <div class="footer">Generated automatically via AI PM Agent | IT Vendor Management Pipeline</div>
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
        "from": "AI PM Agent <onboarding@resend.dev>",
        "to": [destination_email],
        "subject": f"[{file_prefix}] {meeting_title}",
        "html": full_html,
        "attachments": [
            {
                "filename": f"{file_prefix}_{clean_title}.doc",
                "content": doc_base64
            }
        ]
    }

    try:
        response = requests.post(url, json=payload, headers=headers)
        print(f"Resend Status Code: {response.status_code}")
    except Exception as err:
        print(f"Failed to send email via Resend: {err}")


# -------------------------------------------------------------------
# WEBHOOK ENDPOINT
# -------------------------------------------------------------------

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
    
    meeting_title = body.get("title") or body.get("name") or "General Discussion"
    
    created_at_raw = body.get("created_at") or body.get("started_at")
    if created_at_raw:
        try:
            dt = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
            meeting_date = dt.strftime("%B %d, %Y at %I:%M %p UTC")
        except Exception:
            meeting_date = created_at_raw
    else:
        meeting_date = datetime.utcnow().strftime("%B %d, %Y")

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

    # -------------------------------------------------------------------
    # PASS 1: CLASSIFICATION
    # -------------------------------------------------------------------
    print("Classifying meeting intent...")
    classifier_response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=f"{CLASSIFIER_PROMPT}\n\nTranscript Preview:\n{transcript[:4000]}"
    )
    detected_type = classifier_response.text.strip().upper()
    
    if "FRS" in detected_type:
        doc_type = "FRS"
        selected_prompt = FRS_SYSTEM_PROMPT
    else:
        doc_type = "MOM"
        selected_prompt = MOM_SYSTEM_PROMPT

    print(f"Meeting classified as: {doc_type}")

    # -------------------------------------------------------------------
    # PASS 2: GENERATION
    # -------------------------------------------------------------------
    generation_response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=f"{selected_prompt}\n\nMeeting Title: {meeting_title}\nTranscript:\n{transcript}"
    )
    result_markdown = generation_response.text

    # Send dynamic output email and attachment
    send_email_via_resend(result_markdown, meeting_title, meeting_date, attendees_str, doc_type)

    return {"status": "success", "classified_as": doc_type, "content": result_markdown}
