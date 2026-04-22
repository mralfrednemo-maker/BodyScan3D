Archived — built from single-LLM (ChatGPT only) Ein Design run. Not valid.

These files were produced during an overnight automated session that used a compromised
Ein Design synthesis (only ChatGPT participated — Gemini and Claude did not complete).
The resulting implementation does not meet the 3-LLM zero-tolerance standard.

Files:
- capture.html    — was served at /capture.html (phone-side video capture UI)
- prompt.html     — was served at /prompt.html (phone-side prompt selection UI)
- process_scan.py — was launched as WSL worker by server.js launchWorker()

These files, along with the capture routes in server.js and the capture DB tables,
have been removed from the live codebase. A proper re-run of Ein Design with all
3 engines (ChatGPT + Gemini + Claude) will produce the replacement implementation.
