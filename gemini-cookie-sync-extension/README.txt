Gemini Cookie Sync v1.0

A Chrome extension that exports the current signed-in Gemini session for use
with gemini-web2api.

What it exports into gemini-auth.json:
  - Google session cookies (including SAPISID)
  - the XSRF token, exposed in the page as SNlM0e
  - the build tag, from cfb2h when available
  - the signed-in account index (auth_user)

Install and use:
  see SETUP.md in this folder. It is the single source of truth for the install
  steps and for how the exported file is consumed; this file is only a summary
  so the two cannot drift apart.

Security:
  gemini-auth.json IS a real Google session and grants access to the whole
  account, not just Gemini. Treat it as a secret: chmod 600 it, never print it,
  never paste it into an issue, and never commit it to Git. It is gitignored.
