import base64
import hashlib
import hmac
import json
import os
import time
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from google import genai

load_dotenv()

app = FastAPI()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


def verify_fathom_webhook(secret: str, headers: dict, raw_body: str) -> bool:
    webhook_id = headers.get("webhook-id")
    webhook_timestamp = headers.get("webhook-timestamp")
    webhook_signature = headers.get("webhook-signature")

    if not webhook_id or not webhook_timestamp or not webhook_signature:
        return False

    if abs(int(time.time()) - int(webhook_timestamp)) > 300:
        return False

    signed_content = f"{webhook_id}.{webhook_timestamp}.{raw_body}"
    
    if not secret or "_" not in secret:
        return False
    secret_bytes = base64.b64decode(secret.split("_")[1])

    expected_signature = base64.b64encode(
        hmac.new(
            secret_bytes, signed_content.encode(), hashlib.sha256
        ).digest()
    ).decode()

    signatures = [
        sig.split(",")[1] if "," in sig else sig
        for sig in webhook_signature.split(" ")
    ]
    return any(
        hmac.compare_digest(expected_signature, sig) for sig in signatures
    )


def generate_mom_with_gemini(transcript_text: str) -> str:
    prompt = f"""
    You are an executive assistant. Process the following meeting transcript and produce 
    a structured Minutes of Meeting (MOM).
    
    Format output with the following bold sections:
    - Executive Summary
    - Key Decisions Made
    - Action Items (Include Assignee, Task, and Deadline)
    - Discussion Highlights

    Transcript:
    {transcript_text}
    """

    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=prompt,
    )
    return response.text


@app.post("/webhook")
async def fathom_webhook_listener(request: Request):
    try:
        raw_body = (await request.body()).decode("utf-8")
        headers = request.headers
        webhook_secret = os.getenv("FATHOM_WEBHOOK_SECRET")

        if not verify_fathom_webhook(webhook_secret, headers, raw_body):
            raise HTTPException(
                status_code=401, detail="Invalid webhook signature"
            )

        payload = await request.json()
        transcript = payload.get("transcript")

        if not transcript:
            return {"status": "ignored", "reason": "No transcript present"}

        mom_result = generate_mom_with_gemini(transcript)

        print("\n==========================================")
        print("         GENERATED MINUTES OF MEETING     ")
        print("==========================================\n")
        print(mom_result)
        print("\n==========================================\n")

        return {"status": "success", "mom": mom_result}
    except Exception as e:
        print(f"\n[ERROR INSIDE WEBHOOK]: {str(e)}\n")
        raise HTTPException(status_code=500, detail=str(e))
