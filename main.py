import mimetypes
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import requests
from flask import Flask, render_template, request
from werkzeug.utils import secure_filename

from text_to_audio import text_to_speech_file

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
LOCAL_REELS_DIR = STATIC_DIR / "reels"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg"}
MAX_UPLOAD_SIZE = 4 * 1024 * 1024  # Keep the Flask request small enough for Vercel.

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE


def allowed_file(filename: str) -> bool:
    return (
        bool(filename)
        and "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


def is_vercel() -> bool:
    return bool(os.environ.get("VERCEL"))


def blob_token() -> str:
    token = os.environ.get("BLOB_READ_WRITE_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "BLOB_READ_WRITE_TOKEN is missing. Create/connect a Vercel Blob store "
            "and add this environment variable in Vercel."
        )
    return token


def upload_to_vercel_blob(file_path: Path, pathname: str, content_type: str) -> str:
    """Upload a file to Vercel Blob using its HTTP API and return the public URL."""
    token = blob_token()
    url = f"https://blob.vercel-storage.com/{pathname.lstrip('/')}"

    with file_path.open("rb") as file_handle:
        response = requests.put(
            url,
            data=file_handle,
            headers={
                "Authorization": f"Bearer {token}",
                "access": "public",
                "x-api-version": "7",
                "x-content-type": content_type,
                "x-cache-control-max-age": "31536000",
                "x-add-random-suffix": "0",
                "x-allow-overwrite": "true",
            },
            timeout=120,
        )

    if response.status_code >= 400:
        raise RuntimeError(
            f"Vercel Blob upload failed ({response.status_code}): "
            f"{response.text[:500]}"
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("Vercel Blob returned an invalid response.") from exc

    blob_url = data.get("url")
    if not blob_url:
        raise RuntimeError(f"Vercel Blob did not return a URL: {data}")

    return blob_url


def list_vercel_reels():
    """Return public URLs for reels stored in Vercel Blob."""
    token = blob_token()
    response = requests.get(
        "https://blob.vercel-storage.com/",
        params={"prefix": "reels/", "limit": 100},
        headers={
            "Authorization": f"Bearer {token}",
            "x-api-version": "7",
        },
        timeout=30,
    )

    if response.status_code >= 400:
        raise RuntimeError(
            f"Unable to read Vercel Blob ({response.status_code}): "
            f"{response.text[:500]}"
        )

    data = response.json()
    return [blob["url"] for blob in data.get("blobs", []) if blob.get("url")]


def create_reel(image_paths, audio_path: Path, output_path: Path):
    """Create a vertical MP4 using the FFmpeg binary bundled by imageio-ffmpeg."""
    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise RuntimeError("imageio-ffmpeg is not installed.") from exc

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    concat_file = output_path.parent / "input.txt"

    with concat_file.open("w", encoding="utf-8") as file_handle:
        for image_path in image_paths:
            # FFmpeg concat format needs escaped absolute paths.
            escaped = str(image_path).replace("'", "'\\''")
            file_handle.write(f"file '{escaped}'\n")
            file_handle.write("duration 1\n")
        # Repeat the final frame so the last duration is respected.
        if image_paths:
            escaped = str(image_paths[-1]).replace("'", "'\\''")
            file_handle.write(f"file '{escaped}'\n")

    command = [
        ffmpeg,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-i",
        str(audio_path),
        "-vf",
        "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-c:a",
        "aac",
        "-shortest",
        "-r",
        "30",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]

    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )

    if completed.returncode != 0:
        error = (completed.stderr or completed.stdout or "FFmpeg failed")[-2500:]
        raise RuntimeError(f"FFmpeg could not create the reel:\n{error}")

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("FFmpeg finished without producing a video file.")


@app.errorhandler(413)
def request_too_large(_error):
    return (
        "The upload is too large for this Vercel deployment. "
        "Please keep the total image upload under 4 MB.",
        413,
    )


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/health")
def health():
    return {"status": "ok"}


@app.route("/create", methods=["GET", "POST"])
def create():
    if request.method == "GET":
        return render_template("create.html", myid=uuid.uuid4())

    description = (request.form.get("text") or "").strip()
    if not description:
        return render_template(
            "create.html",
            myid=request.form.get("uuid") or uuid.uuid4(),
            error="Please enter the text for the AI voice.",
        ), 400

    uploaded = []
    for file in request.files.values():
        if not file or not file.filename:
            continue
        if not allowed_file(file.filename):
            return render_template(
                "create.html",
                myid=request.form.get("uuid") or uuid.uuid4(),
                error="Only PNG, JPG and JPEG images are supported.",
            ), 400
        uploaded.append(file)

    if not uploaded:
        return render_template(
            "create.html",
            myid=request.form.get("uuid") or uuid.uuid4(),
            error="Please upload at least one image.",
        ), 400

    reel_id = secure_filename(str(request.form.get("uuid") or uuid.uuid4()))

    try:
        with tempfile.TemporaryDirectory(prefix="ai-reel-") as temp_dir:
            temp_dir_path = Path(temp_dir)
            image_paths = []

            for index, file in enumerate(uploaded, start=1):
                extension = Path(secure_filename(file.filename)).suffix.lower()
                image_path = temp_dir_path / f"image_{index}{extension}"
                file.save(image_path)
                image_paths.append(image_path)

            audio_path = temp_dir_path / "audio.mp3"
            text_to_speech_file(description, str(audio_path))

            output_path = temp_dir_path / f"{reel_id}.mp4"
            create_reel(image_paths, audio_path, output_path)

            if is_vercel():
                video_url = upload_to_vercel_blob(
                    output_path,
                    f"reels/{reel_id}.mp4",
                    "video/mp4",
                )
            else:
                LOCAL_REELS_DIR.mkdir(parents=True, exist_ok=True)
                local_output = LOCAL_REELS_DIR / f"{reel_id}.mp4"
                shutil.copy2(output_path, local_output)
                video_url = f"/static/reels/{local_output.name}"

        return render_template(
            "result.html",
            video_url=video_url,
            reel_id=reel_id,
        )

    except Exception as exc:
        app.logger.exception("Reel generation failed")
        return render_template(
            "create.html",
            myid=reel_id,
            error=f"Reel generation failed: {exc}",
        ), 500


@app.route("/gallery")
def gallery():
    local_reels = []
    if LOCAL_REELS_DIR.exists():
        local_reels = [
            f"/static/reels/{path.name}"
            for path in sorted(LOCAL_REELS_DIR.glob("*.mp4"), reverse=True)
        ]

    blob_reels = []
    if os.environ.get("BLOB_READ_WRITE_TOKEN"):
        try:
            blob_reels = list_vercel_reels()
        except Exception:
            app.logger.exception("Unable to list Vercel Blob reels")

    return render_template("gallery.html", reels=blob_reels + local_reels)


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=int(os.environ.get("PORT", 5000)))
