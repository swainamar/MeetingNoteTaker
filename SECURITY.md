# Privacy & security

## Your data

| Data | Where | Leaves the PC? |
|---|---|---|
| Audio, video, transcripts, notes | `MeetingNoteTaker/meetings/<date>_<title>/` | No |
| Voice profiles (biometric) | `MeetingNoteTaker/voices/*.npy` | No |
| Audit log | `MeetingNoteTaker/meetings/audit.jsonl` | No |
| Transcript text for notes | — | **Only** if you set `ANTHROPIC_API_KEY` (sent to the Anthropic API) or paste the prompt into claude.ai yourself |
| Outlook meeting title / attendees | Read from classic Outlook on this PC | No |

Models are downloaded once from Hugging Face and the sherpa-onnx GitHub releases. Speaker models are checked against a SHA-256 hash before use.

## Good practice

- **Tell participants before recording.** Recording laws differ by country and company policy.
- Keep the folder on a **BitLocker-encrypted** drive. The files are not encrypted by the app.
- Set **retention** limits in **Settings → Privacy & Compliance**, so old audio and meetings are deleted automatically.
- Keep voice profiles only with the person's consent, and delete them on request (**Voices → Delete**).
- Turn on **Hide Note-Taker windows during screen sharing** if you present often.
- Copies made by the app are kept out of Windows clipboard history and cloud clipboard sync.
- The Odia community mirror runs code from its model repository. It is off unless `INDIC_ASR_ALLOW_MIRROR=1`, and it should be pinned with `INDIC_ASR_REVISION`.

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Use GitHub's **[Report a vulnerability](https://github.com/swainamar/MeetingNoteTaker/security/advisories/new)** (private advisory). Include the steps to reproduce and the version shown in the app title bar. You will get a reply as soon as possible.

## Supported versions

Only the latest release receives fixes.
