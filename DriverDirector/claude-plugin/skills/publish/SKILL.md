---
name: publish
description: Publish a finished DriverDirector video to the user's YouTube channel (set up and connect the account once, draft the title, description and chapters, and upload only after the user confirms the exact title, description and visibility). Use when the user wants to put a video on YouTube or set up or fix the YouTube connection.
argument-hint: "[video path]"
disable-model-invocation: true
---

# Publish to YouTube

Helper: `python "${CLAUDE_PLUGIN_ROOT}/scripts/dd.py" <command>` (called `dd` below). Uploading publishes to the user's own channel, so nothing is uploaded until the user has agreed to the details below, in this chat, for this video.

## 1. Connect (once)
Run `dd youtube`. It says where the setup stands; follow its `next_step`.
- `configured: false`: the user needs a Google Cloud OAuth client. Give them the one-time steps (also in `setup_steps`);
  they do these in their own Google account, and **every step in the same Cloud project**:
  1. console.cloud.google.com: pick or create one project and note its name in the project picker.
  2. APIs & Services > Library: enable **YouTube Data API v3**.
  3. Google Auth Platform (OAuth consent screen): External, any app name, their email as contact.
  4. **Audience > Test users > Add users: the Google account that owns the YouTube channel.** This is the step
     people miss. Without it Google shows "Access blocked" / "Error 403: access_denied" after they approve, and
     the sign-in never completes.
  5. Clients (Credentials) > Create client > **OAuth client ID, type Desktop app**, then download the JSON.
  Then run `dd youtube setup` (it takes the newest `client_secret*.json` in Downloads, or `dd youtube setup PATH`).
  It prints the file's `project_id`: check that it is the project where steps 2 to 4 were done (a client file from
  another project is the usual cause of "access blocked"). Never ask for secrets in the chat, and do not use an API
  key (`AIza...`): it cannot upload or edit, and one pasted into the chat is exposed (tell the user to restrict or
  delete it).
- `authorized: false`: run `dd youtube connect`. A browser opens on the user's machine; they sign in and approve
  within 5 minutes (Google warns that the app is unverified: "Continue" is fine for their own test app). Wait for
  them, then `dd youtube` shows `authorized: true` and the channel name; read the channel name back so they know
  which account receives the video. If it is still waiting, ask what the browser shows: "Access blocked" means step 4
  (or the wrong project); "can't reach 127.0.0.1" means the link expired, so connect again. While an old sign-in is
  still waiting, connect answers "a job is already running": it ends by itself after 5 minutes.
- `can_edit: false`: the connection can upload but not change existing videos (descriptions, visibility). Run
  `dd youtube connect` again and have the user approve the extra permission.
- Quota: Google gives 10,000 units a day, reset at midnight US Pacific time. An upload costs 1,600, a change to an
  existing video 50, a list 1. Plan big batches over several days.

## 2. Pick the video
Use the path the user gave, or the latest finished video (`dd status` shows `final` after a run; otherwise ask). Confirm it is the file they mean.

## 3. Draft and confirm
Run `dd youtube draft "<video path>"`. It needs the replay loaded and planned; if it says so, run the load and plan steps from the direct skill first, or write the title and description with the user by hand.
Show the user the title, description (including chapters) and the visibility. The default is **private**; offer unlisted or public as their choice. Mention that the voice and music are marked as AI-generated, and that Google keeps uploads from unaudited API projects private whatever is asked, so a public video may need switching to public in YouTube Studio.
Let them edit the title and description. Then ask a direct yes/no question that names the channel, the title and the visibility. Do not upload on anything less than a clear yes.

## 4. Upload
Run `dd youtube upload '<json>'` with `{"video": "...", "title": "...", "description": "...", "privacy": "private", "confirm": true}`, then `dd watch` until it prints FINAL (the video address). A multi-gigabyte upload takes a while; if the connection drops it resumes by itself, and running the command again is safe.
Report the link and the visibility that YouTube actually applied.
