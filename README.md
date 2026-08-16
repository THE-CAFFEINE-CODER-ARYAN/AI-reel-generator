# AI Reel Generator — Vercel-ready

This version removes the original server-side background loop and local persistent storage. Uploaded images are processed in a temporary directory, ElevenLabs creates the voice-over, FFmpeg creates the MP4, and the finished reel is stored in Vercel Blob when deployed on Vercel.

## Required Vercel environment variables

Set these in **Vercel → Project → Settings → Environment Variables**:

- `ELEVENLABS_API_KEY` — your ElevenLabs API key
- `BLOB_READ_WRITE_TOKEN` — automatically provided when you connect a Vercel Blob store to the project

Optional:

- `ELEVENLABS_VOICE_ID` — defaults to `pNInz6obpgDQGcFmaJgB`
- `ELEVENLABS_MODEL_ID` — defaults to `eleven_turbo_v2_5`

## Important

Do not put API keys in `config.py`, GitHub, or the ZIP. The previous hard-coded ElevenLabs key was removed.

Vercel serverless functions do not provide persistent application storage. This project therefore uses `/tmp` only during reel generation and Vercel Blob for the finished MP4. The gallery reads reels from Vercel Blob.

The current Flask form is intentionally kept under 4 MB total request size. For larger uploads, move the browser upload step to Vercel Blob client uploads.

## Deploy

1. Create/connect a Vercel Blob store to the project.
2. Add `ELEVENLABS_API_KEY` in Vercel environment variables.
3. Push this folder to GitHub.
4. Import the GitHub repository into Vercel.
5. Redeploy after setting the environment variables.
6. Test `/health` first; it should return `{"status":"ok"}`.
