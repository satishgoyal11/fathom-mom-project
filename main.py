def generate_content_with_retry(prompt: str, transcript: str) -> str:
    """Generates content using updated Gemini Flash models with automatic retry logic."""
    models_to_try = [
        "gemini-3.6-flash",
        "models/gemini-1.5-flash",
        "models/gemini-2.0-flash"
    ]
    
    for model_name in models_to_try:
        for attempt in range(3):
            try:
                print(f"Attempting generation with {model_name} (Attempt {attempt + 1})...")
                response = client.models.generate_content(
                    model=model_name,
                    contents=f"{prompt}\n\nTranscript:\n{transcript}"
                )
                if response and response.text:
                    print(f"Successfully generated MOM using {model_name}")
                    return response.text
            except Exception as e:
                print(f"API Error on {model_name} (Attempt {attempt + 1}): {e}")
                time.sleep(2 * (attempt + 1))
                
    raise RuntimeError("All Gemini API model attempts failed.")
