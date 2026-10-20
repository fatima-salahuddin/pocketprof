"""Google Classroom connector: the primary material source (supervisor change of 15 Oct 2026).

A teacher connects their Google account (OAuth, read-only scopes) and links a class by its class code.
The Classroom API only returns `enrollmentCode` to that class's teachers, so a class can only be linked
by someone who teaches it.

Reading a class lists its materials, assignments and announcements and every Drive attachment.
Listing is cheap; nothing is downloaded unless the document's version (Drive md5Checksum or
modifiedTime, Classroom updateTime) differs from the materials ledger. Google-native Docs and Slides
are exported as DOCX and PPTX so headings and slide numbers survive.
"""
from __future__ import annotations

import io
import json
import logging
import os
import ssl
import time
from datetime import datetime, timedelta

import jwt
import requests
from dateutil import parser as dateparser
from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.errors import Problem
from ..core.security import decrypt_secret, encrypt_secret, now_utc
from ..db.models import GoogleAccount
from . import parsers
from .sources import SkipDocument, SourceDocument

log = logging.getLogger("pocketprof.ingestion.google")

# Localhost runs on http; Google may also return granted scopes in a different order.
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.courseworkmaterials.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.students.readonly",
    "https://www.googleapis.com/auth/classroom.announcements.readonly",
    "https://www.googleapis.com/auth/classroom.topics.readonly",
    "https://www.googleapis.com/auth/classroom.rosters.readonly",
    "https://www.googleapis.com/auth/classroom.profile.emails",
    "https://www.googleapis.com/auth/drive.readonly",
]
RETRIES = 5
EXPORT_LIMIT_MB = 10  # Drive refuses to export Google-native files above this size


class ClassroomError(Problem):
    def __init__(self, detail: str, status: int = 400):
        super().__init__(status, "Google Classroom", detail, "/problems/google-classroom")


def _retry(fn, what: str):
    """Some networks intermittently cut TLS handshakes to Google. A handshake that fails never reached
    Google, so retrying is safe, even for the one-time OAuth code exchange."""
    for attempt in range(RETRIES):
        try:
            return fn()
        except (requests.exceptions.SSLError, requests.exceptions.ConnectionError, ssl.SSLError,
                TransportError, ConnectionResetError, TimeoutError) as e:
            if attempt == RETRIES - 1:
                raise
            log.warning("connection to Google dropped; retrying",
                        extra={"op": what, "error": type(e).__name__, "attempt": attempt + 1})
            time.sleep(0.5 * (attempt + 1))


# ---------- OAuth ----------

def _client_config() -> dict:
    s = get_settings()
    return {"web": {"client_id": s.google_client_id, "client_secret": s.google_client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [s.google_redirect_uri]}}


def _flow(state: str | None = None) -> Flow:
    if not get_settings().google_configured:
        raise ClassroomError("Google sign-in is not configured on this server (GOOGLE_CLIENT_ID).", 503)
    return Flow.from_client_config(_client_config(), scopes=SCOPES, state=state,
                                   redirect_uri=get_settings().google_redirect_uri)


def authorization_url(user_id: int, return_to: str) -> str:
    """The OAuth state is a short-lived signed token naming the user and the PKCE verifier, so the
    callback needs no server-side session and cannot be replayed for another account."""
    flow = _flow()
    flow.autogenerate_code_verifier = True
    url, _ = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
    state_token = jwt.encode({"uid": user_id, "cv": flow.code_verifier, "ret": return_to,
                              "exp": now_utc() + timedelta(minutes=10), "typ": "gstate"},
                             get_settings().jwt_secret, algorithm="HS256")
    # Replace the state the library generated with ours.
    from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
    parts = urlparse(url)
    q = dict(parse_qsl(parts.query))
    q["state"] = state_token
    return urlunparse(parts._replace(query=urlencode(q)))


def read_state(state: str) -> dict:
    try:
        data = jwt.decode(state, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise ClassroomError("The Google sign-in link has expired. Please try again.") from None
    if data.get("typ") != "gstate":
        raise ClassroomError("Invalid sign-in state.")
    return data


def complete_authorization(db: Session, authorization_response: str, state: str) -> tuple[int, str]:
    data = read_state(state)
    flow = _flow(state=state)
    flow.code_verifier = data["cv"]
    _retry(lambda: flow.fetch_token(authorization_response=authorization_response), "oauth token exchange")
    creds = flow.credentials
    info = _retry(lambda: build("oauth2", "v2", credentials=creds, cache_discovery=False)
                  .userinfo().get().execute(num_retries=RETRIES), "userinfo")
    row = db.get(GoogleAccount, data["uid"])
    if row is None:
        row = GoogleAccount(user_id=data["uid"], google_email=info.get("email", ""), credentials_enc="")
        db.add(row)
    row.google_email, row.google_name = info.get("email", ""), info.get("name")
    row.credentials_enc = encrypt_secret(creds.to_json())
    row.scopes = sorted(creds.scopes or [])
    return data["uid"], data.get("ret") or "/"


def credentials(db: Session, user_id: int) -> Credentials:
    row = db.get(GoogleAccount, user_id)
    if row is None:
        raise ClassroomError("Connect your Google account first.", 409)
    creds = Credentials.from_authorized_user_info(json.loads(decrypt_secret(row.credentials_enc)))
    if not creds.valid and creds.refresh_token:
        try:
            _retry(lambda: creds.refresh(Request()), "token refresh")
        except RefreshError:
            raise ClassroomError("Google access was revoked or expired. Reconnect your Google account.", 409) from None
        row.credentials_enc = encrypt_secret(creds.to_json())
        db.flush()
    return creds


def _services(db: Session, user_id: int):
    creds = credentials(db, user_id)
    return (build("classroom", "v1", credentials=creds, cache_discovery=False),
            build("drive", "v3", credentials=creds, cache_discovery=False))


def _list_all(method, key: str, **kwargs) -> list[dict]:
    items, token = [], None
    while True:
        resp = method(pageToken=token, **kwargs).execute(num_retries=RETRIES)
        items.extend(resp.get(key, []))
        token = resp.get("nextPageToken")
        if not token:
            return items


# ---------- classes ----------

def teaching_classes(db: Session, user_id: int) -> list[dict]:
    classroom, _ = _services(db, user_id)
    courses = _list_all(classroom.courses().list, "courses", teacherId="me", pageSize=100)
    return [{"id": c["id"], "name": c.get("name", ""), "section": c.get("section"),
             "code": c.get("enrollmentCode"), "link": c.get("alternateLink"),
             "created": c.get("creationTime")}
            for c in courses if c.get("courseState") in ("ACTIVE", "PROVISIONED")]


def find_class_by_code(db: Session, user_id: int, code: str) -> dict:
    code = code.strip()
    classes = teaching_classes(db, user_id)
    for c in classes:
        if (c["code"] or "").lower() == code.lower():
            return c
    raise ClassroomError(f"No active class with code '{code}' is taught by this Google account "
                         f"({len(classes)} classes checked). Check the code or connect the class owner's account.", 404)


def roster_emails(db: Session, teacher_id: int, gc_course_id: str) -> set[str] | None:
    """Student emails on the Classroom roster, or None if the roster cannot be read (missing scope)."""
    try:
        classroom, _ = _services(db, teacher_id)
        students = _list_all(classroom.courses().students().list, "students", courseId=gc_course_id)
    except HttpError as e:
        log.warning("roster unavailable", extra={"status": e.status_code})
        return None
    return {s.get("profile", {}).get("emailAddress", "").lower() for s in students} - {""}


# ---------- reading a class ----------

def _download(drive, file_id: str, mime: str) -> tuple[bytes, str]:
    if mime in parsers.EXPORT_AS:
        out_mime = parsers.EXPORT_AS[mime]
        request = drive.files().export_media(fileId=file_id, mimeType=out_mime)
    else:
        out_mime = mime
        request = drive.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request, chunksize=4 * 1024 * 1024)
    done = False
    while not done:
        _, done = downloader.next_chunk(num_retries=RETRIES)
    return buf.getvalue(), out_mime


def _when(post: dict) -> datetime | None:
    stamp = post.get("scheduledTime") or post.get("creationTime")
    return dateparser.isoparse(stamp) if stamp else None


def _drive_document(drive, df: dict, base: dict) -> SourceDocument:
    file_id = df["id"]
    try:
        meta = drive.files().get(fileId=file_id, supportsAllDrives=True,
                                 fields="id,name,mimeType,size,md5Checksum,modifiedTime,webViewLink"
                                 ).execute(num_retries=RETRIES)
    except HttpError as e:
        return SourceDocument(**base, source_ref=f"drive:{file_id}", title=df.get("title") or "Drive file",
                              mime="", version="unavailable", fetch=None, url=df.get("alternateLink"),
                              skip_reason=f"cannot open file ({e.status_code}); is it shared with the class?")
    mime = meta["mimeType"]
    size_mb = int(meta.get("size", 0)) / 1e6
    version = meta.get("md5Checksum") or f"{meta.get('modifiedTime', '')}|export"
    doc = SourceDocument(**base, source_ref=f"drive:{file_id}", title=meta.get("name") or df.get("title", "file"),
                         mime=mime, version=version, fetch=None, url=meta.get("webViewLink") or df.get("alternateLink"))
    limit = get_settings().max_upload_mb
    if not parsers.is_supported(mime):
        doc.skip_reason = f"file type not supported yet ({mime})"
    elif size_mb > limit:
        doc.skip_reason = f"larger than {limit} MB"
    else:
        def fetch(drive=drive, file_id=file_id, mime=mime):
            try:
                return _download(drive, file_id, mime)
            except HttpError as e:
                if e.status_code == 403 and mime in parsers.EXPORT_AS:
                    raise SkipDocument(f"Google refuses to export files over {EXPORT_LIMIT_MB} MB") from None
                raise
        doc.fetch = fetch
    return doc


def _post_documents(drive, kind: str, post: dict, topics: dict[str, str], seen: set[str]) -> list[SourceDocument]:
    if kind == "Announcement":
        text = (post.get("text") or "").strip()
        first = text.splitlines()[0] if text else "Announcement"
        title = first[:80] + ("..." if len(first) > 80 else "")
    else:
        title = post.get("title") or "Untitled"
    base = dict(source="google", kind=kind, post_title=title, topic=topics.get(post.get("topicId"), None),
                post_url=post.get("alternateLink"), published_at=_when(post))
    docs: list[SourceDocument] = []

    body = (post.get("description") or post.get("text") or "").strip()
    if body:
        due = ""
        if post.get("dueDate"):
            d = post["dueDate"]
            due = f"\nDue: {d.get('year')}-{d.get('month'):02d}-{d.get('day'):02d}"
            if post.get("maxPoints"):
                due += f" | Points: {post['maxPoints']}"
        content = f"{title}\n\n{body}{due}".encode()
        docs.append(SourceDocument(**base, source_ref=f"post:{post['id']}", title="Post text", mime="text/plain",
                                   version=post.get("updateTime", ""), url=post.get("alternateLink"),
                                   fetch=lambda content=content: (content, "text/plain")))

    for att in post.get("materials", []):
        if "driveFile" in att:
            df = att["driveFile"]["driveFile"]
            if df["id"] in seen:  # the same file attached twice is indexed once (DQ-1)
                continue
            seen.add(df["id"])
            docs.append(_drive_document(drive, df, base))
        elif "youtubeVideo" in att:
            v = att["youtubeVideo"]
            content = f"{title}: video '{v.get('title', '')}' ({v.get('alternateLink', '')})".encode()
            docs.append(SourceDocument(**base, source_ref=f"video:{post['id']}:{v.get('id')}",
                                       title=f"Video: {v.get('title', '')}", mime="video/youtube",
                                       version=post.get("updateTime", ""), url=v.get("alternateLink"),
                                       fetch=lambda content=content: (content, "text/plain"),
                                       note="title only; video is not transcribed"))
        elif "link" in att:
            ln = att["link"]
            content = f"{title}: reference link '{ln.get('title') or ''}' {ln.get('url', '')}".encode()
            docs.append(SourceDocument(**base, source_ref=f"link:{post['id']}:{ln.get('url', '')[:150]}",
                                       title=f"Link: {ln.get('title') or ln.get('url')}", mime="text/uri-list",
                                       version=post.get("updateTime", ""), url=ln.get("url"),
                                       fetch=lambda content=content: (content, "text/plain"),
                                       note="title only; the web page is not read"))
        elif "form" in att:
            f = att["form"]
            docs.append(SourceDocument(**base, source_ref=f"form:{post['id']}:{f.get('formUrl', '')[-40:]}",
                                       title=f"Form: {f.get('title', '')}", mime="form", version="form",
                                       url=f.get("formUrl"), fetch=None, skip_reason="Google Forms are not read"))
    return docs


def collect(db: Session, teacher_id: int, gc_course_id: str) -> list[SourceDocument]:
    classroom, drive = _services(db, teacher_id)
    try:
        topics = {t["topicId"]: t["name"] for t in
                  _list_all(classroom.courses().topics().list, "topic", courseId=gc_course_id)}
        posts: list[tuple[str, dict]] = []
        posts += [("Material", p) for p in _list_all(classroom.courses().courseWorkMaterials().list,
                                                     "courseWorkMaterial", courseId=gc_course_id,
                                                     courseWorkMaterialStates=["PUBLISHED"])]
        posts += [("Assignment", p) for p in _list_all(classroom.courses().courseWork().list, "courseWork",
                                                       courseId=gc_course_id, courseWorkStates=["PUBLISHED"])]
        posts += [("Announcement", p) for p in _list_all(classroom.courses().announcements().list,
                                                         "announcements", courseId=gc_course_id,
                                                         announcementStates=["PUBLISHED"])]
    except HttpError as e:
        if e.status_code in (401, 403):
            raise ClassroomError("Google denied access to this class. Reconnect the teacher's Google account.", 409) from None
        if e.status_code == 404:
            raise ClassroomError("This class no longer exists on Google Classroom.", 404) from None
        raise
    posts.sort(key=lambda kp: kp[1].get("creationTime", ""))  # oldest first: first post owns a shared file
    seen: set[str] = set()
    docs: list[SourceDocument] = []
    for kind, post in posts:
        docs.extend(_post_documents(drive, kind, post, topics, seen))
    return docs
