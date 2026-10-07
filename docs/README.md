# gemini-web2api documentation

Everything about running, configuring and developing gemini-web2api.
Start with the [project README](../README.md) for the 60-second version.

| Document | What it covers |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | How a request flows from an OpenAI client to Gemini Web and back, module map, the wire protocol |
| [CONFIGURATION.md](CONFIGURATION.md) | Every option, its default, and how to set it via file, environment or CLI |
| [API.md](API.md) | Every HTTP endpoint, with request and response examples |
| [AUTHENTICATION.md](AUTHENTICATION.md) | Protecting the server with API keys; authenticating *to* Google with cookies, XSRF and `auth_user` |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Docker, Docker Compose, systemd, Cloudflare Workers, reverse proxies |
| [SECURITY.md](SECURITY.md) | Threat model, what is protected by default, and what you must decide yourself |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Symptom → cause → fix, including empty replies, HTTP 405/429, and Pro not routing |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Layout, running tests, adding a model, release checklist |
| [CHANGELOG.md](CHANGELOG.md) | What changed in each version |
| [AUDIT.md](AUDIT.md) | The full defect audit behind the 1.2.0 hardening pass, with reproductions |

## Related components

* [`gemini-cookie-sync-extension/`](../gemini-cookie-sync-extension/SETUP.md) — a
  Chrome extension that exports your Gemini session (cookies, XSRF token, build
  tag, account index) into `gemini-auth.json`.
* [`cloudflare/`](../cloudflare/README.MD) — an independent Cloudflare Workers
  port for serverless deployment (Chinese documentation).

## Quick navigation

**"It stopped working."** → [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

**"I want real Pro responses."** → [AUTHENTICATION.md](AUTHENTICATION.md#authenticating-to-google)

**"How do I lock this down before exposing it?"** → [SECURITY.md](SECURITY.md)

**"Which endpoint do I point my client at?"** → [API.md](API.md)

**"What does this config key do?"** → [CONFIGURATION.md](CONFIGURATION.md)
