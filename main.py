import base64
import hashlib
import hmac
import os
import requests
from google import genai
from fastapi import FastAPI, HTTPException, Request

app = FastAPI()

# Automatically initializes using GEMINI_API_KEY environment variable
client = genai.Client()


def send_email_via_resend(mom_text: str):
    resend_api_key = os.getenv("RESEND_API_KEY")
    destination_email = os.getenv("MY_EMAIL")

    if not resend_api_key or not destination_email:
        print("Error: Missing RESEND_API_KEY or MY_EMAIL environment variables.")
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

    # Model name updated to match the active Google GenAI SDK requirement
    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=f"Generate structured Minutes of Meeting for:\n{transcript}"
    )
    mom_result = response.text

    send_email_via_resend(mom_result)

    return {"status": "success", "mom": mom_result}
