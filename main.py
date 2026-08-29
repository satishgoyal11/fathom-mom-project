import base64
import hashlib
import hmac
import os
import requests
import google.generativeai as genai
from fastapi import FastAPI, HTTPException, Request

app = FastAPI()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-1.5-flash")


def send_email_via_resend(mom_text: str):
    resend_api_key = os.getenv("RESEND_API_KEY")
    destination_email = os.getenv("MY_EMAIL")

    if not resend_api_key or not destination_email:
        print("Missing RESEND_API_KEY or MY_EMAIL environment variables.")
        return

    url = "https://api.resend.com/emails"
    headers = {
        "Authorization": f"Bearer {resend_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "from": "Fathom MOM System <onboarding@resend.dev>",
        "to": [destination_email],
        "subject": "📋 New Minutes of Meeting (MOM) Generated",
        "text": mom_text,
    }

    response = requests.post(url, json=payload, headers=headers)
    if response.status_code in [200, 201]:
        print("Email sent successfully via Resend!")
    else:
        print(f"Resend Error: {response.text}")


@app.post("/webhook")
async def handle_webhook(request: Request):
    # Retrieve Fathom webhook signature headers
    webhook_id = request.headers.get("webhook-id")
    webhook_timestamp = request.headers.get("webhook-timestamp")
    webhook_signature = request.headers.get("webhook-signature")

    raw_body = await request.body()
    secret = os.getenv("FATHOM_WEBHOOK_SECRET")

    # Verify signature if secret is provided
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

    # Generate MOM using Gemini
    response = model.generate_content(
        f"Generate structured Minutes of Meeting for:\n{transcript}"
    )
    mom_result = response.text

    # Automatically send to your inbox
    try:
        send_email_via_resend(mom_result)
    except Exception as e:
        print(f"Failed to trigger email: {e}")

    return {"status": "success", "mom": mom_result}
