"""
Publishes to YouTube via the Data API v3 -- the same OAuth setup you already
did once (client_secret.json + token.json). No AI involved.
"""
import os

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def get_service(client_secret_path: str, token_path: str):
    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(client_secret_path):
                # A plain exception, not SystemExit -- must be catchable by
                # the watcher's error handling, not just the interactive CLI.
                raise FileNotFoundError(
                    f"{client_secret_path} not found. Copy it over from your original "
                    "YouTube API setup, or create a new OAuth Desktop client in Google "
                    "Cloud Console (see the original project's README)."
                )
            flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)


def upload_video(client_secret_path: str, token_path: str, file_path: str, title: str,
                  description: str, tags: list, privacy: str = "private",
                  publish_at: str = None) -> str:
    """`publish_at`, if given, is an RFC3339 UTC timestamp (e.g.
    "2026-09-10T13:30:00Z", see lib/schedule.py). YouTube only allows
    scheduled publishing on videos uploaded as private, so when publish_at
    is set this always uploads as private with that publishAt time and
    ignores whatever `privacy` was passed -- YouTube itself automatically
    flips the video to public at that exact moment. Leave publish_at unset
    for the old immediate-privacy behavior."""
    youtube = get_service(client_secret_path, token_path)

    status = {"privacyStatus": "private", "publishAt": publish_at} if publish_at else {"privacyStatus": privacy}
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "22",
        },
        "status": status,
    }
    media = MediaFileUpload(file_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status_progress, response = request.next_chunk()
        if status_progress:
            print(f"Uploaded {int(status_progress.progress() * 100)}%")

    video_id = response["id"]
    if publish_at:
        print(f"Done. https://youtube.com/watch?v={video_id} (scheduled public at {publish_at} UTC)")
    else:
        print(f"Done. https://youtube.com/watch?v={video_id} (privacy: {privacy})")
    return video_id
