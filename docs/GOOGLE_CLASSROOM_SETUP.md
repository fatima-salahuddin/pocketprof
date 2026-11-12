# Connecting PocketProf to a real Google Classroom class (development)

PocketProf reads a class through the teacher's own Google account (read-only OAuth). The code for this is
complete (`ingestion/google_classroom.py`, `api/routes_google.py`, the *My classes* page); each developer only
has to register PocketProf with Google once and put two values in their `.env`. About 15 minutes.

## 1. Register PocketProf with Google (once per developer)

1. Open <https://console.cloud.google.com> with the Google account you will teach with.
2. **Create a project** (top bar, project picker, *New project*), e.g. `pocketprof-dev`, and select it.
3. **Enable two APIs** (*APIs & Services*, *Library*): **Google Classroom API** and **Google Drive API**.
4. **Consent screen** (*Google Auth Platform* in the newer console, *OAuth consent screen* in the older one):
   - App name `PocketProf (dev)`, your email as support and developer contact.
   - Audience / user type: **External**. Leave the publishing status as **Testing**.
   - **Test users**: add every Google account that will sign in (yours, and any teammate's). Only test
     users can connect while the app is in Testing.
   - Scopes / *Data access*: optional in Testing; PocketProf asks for them itself.
5. **Create the OAuth client** (*Clients*, or *Credentials*, *Create credentials*, *OAuth client ID*):
   - Application type: **Web application**, name `PocketProf local`.
   - **Authorized redirect URIs**: `http://localhost:8000/api/v1/google/callback` (exactly; no trailing slash).
     The phase-1 prototype used `/api/google/callback`; a client created for it needs this new URI added.
   - Create, then copy the **Client ID** and **Client secret**.
6. In `pocketprof/.env` (never committed):
   ```
   GOOGLE_CLIENT_ID=1234567890-xxxx.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=GOCSPX-xxxx
   ```
   Restart the API. *My classes* now shows **Connect Google Classroom** instead of "not configured".

## 2. Make a test class in Google Classroom

1. <https://classroom.google.com> with the same account, **+**, *Create class* (a personal Gmail account can
   create classes; choose "I'm a teacher" if asked).
2. **Classwork**, *Create*, *Topic*: make one topic per week, **named with the week**, e.g. "Week 1 - Relational
   Model", "Week 2 - ER Modelling". PocketProf turns Classroom topics into its topics (notes and practice are
   organised by them), and a week (or "Lecture N") in a topic's name sets the week of everything posted under it.
3. Post a mix, each under a topic: *Material* with an attached PDF, PPTX, DOCX or Google Doc/Slides; an
   *Assignment* with instructions; and a couple of **Announcements** on the Stream (e.g. "Quiz 1 is on
   Friday 20 November and covers stacks"). Posts must be published, not drafts or scheduled.
4. The **class code** is on the Stream page header (class settings must have the class code turned on).

Supported attachments: PDF, PPTX, DOCX, plain text, Google Docs and Slides (exported, up to 10 MB). Links,
YouTube videos and Forms are recorded by title only. Image-only scanned pages need OCR (not installed by
default) and are reported as skipped.

## 3. Link the class in PocketProf

1. Sign in as a **teacher** (in development, the *Teacher* demo-account button), *My classes*,
   **Connect Google Classroom**. Choose your Google account.
2. Google warns **"Google hasn't verified this app"**: this is expected for an app in Testing. *Continue*.
3. On the permissions screen **tick every box** (Classroom and Drive). Unticked permissions make reading fail.
4. Back on *My classes*, your Classroom classes are listed: **Link** the test class (or type its class code).
   The first read starts at once; the class card shows *Reading*, then *Ready*. Open it to see *What
   PocketProf read*, with the reason for anything skipped.
5. New posts are picked up automatically within about a minute (`SYNC_SECONDS`, default 30). **Sync now**
   makes it immediate.

## 4. Students

Students do not need to be in the Google Classroom class while `ENFORCE_ROSTER=false` (the default). Sign
up a student account on the sign-in page (or use the *Student* demo account), then *My classes*, join with
the **same class code**.

## Things that surprise people

- **Where week numbers come from.** In order: a week typed when uploading a file; a week in the Classroom
  topic's name ("Week 5 - Normalisation", "Wk 5", "week-05"); a Material's or Assignment's title or attached
  file name that **starts with** the week ("WEEK 3", "Week3_notes.pdf"; never announcements); otherwise the
  post date, where week 1 is the week of the class's first post (or the *Term starts* date in the course's
  *Settings* tab). Classroom cannot backdate posts, so a test class posted all at once needs week-named topics,
  titles or file names for "week 5" questions to work.
  Renaming a topic re-files its posts on the next sync. Relative questions ("last week") still count from
  today's date, so they match week-named topics only when the topics follow the real calendar.
- **Practice questions only come from material already posted** (FR-10), so a post scheduled for the future is
  not used until it appears.
- **Testing-mode Google tokens expire after 7 days.** If a class stops updating with "Reconnect your Google
  account", press *Disconnect*, then *Connect Google Classroom* again. Publishing the consent screen removes
  this, but needs Google's verification for the Drive scope.
- **Only a teacher of the class can link it**: Google gives the class code only to its teachers.
- `drive.readonly` is a *restricted* scope; it works for test users without verification.
