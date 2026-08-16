import os
from pathlib import Path

from elevenlabs import VoiceSettings
from elevenlabs.client import ElevenLabs


def text_to_speech_file(text: str, output_path: str) -> str:
    api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "ELEVENLABS_API_KEY is missing. Add it to your Vercel environment variables."
        )

    client = ElevenLabs(api_key=api_key)

    response = client.text_to_speech.convert(
        voice_id=os.environ.get("ELEVENLABS_VOICE_ID", "pNInz6obpgDQGcFmaJgB"),
        output_format="mp3_22050_32",
        text=text,
        model_id=os.environ.get("ELEVENLABS_MODEL_ID", "eleven_turbo_v2_5"),
        voice_settings=VoiceSettings(
            stability=0.0,
            similarity_boost=1.0,
            style=0.0,
            use_speaker_boost=True,
            speed=1.0,
        ),
    )

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with output.open("wb") as file_handle:
        for chunk in response:
            if chunk:
                file_handle.write(chunk)

    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError("ElevenLabs returned an empty audio file.")

    return str(output)
