# Deployment notes

Written after a first attempt at Hugging Face Spaces, so a second attempt does not
rediscover the same things. The application code is already deployment-ready; everything
below is about the platforms.

## What the app needs from a host

| | |
|---|---|
| Python | 3.10+ |
| Memory | ~1 GB. Gradio, LangChain and CTranslate2 are all resident |
| Disk | ~2.3 GB image, or ~900 MB of site-packages without the container |
| System packages | `ffmpeg` — faster-whisper shells out to it for anything not already PCM WAV |
| Port | `GRADIO_SERVER_PORT`, default 7860 |
| Bind address | `GRADIO_SERVER_NAME` — must be `0.0.0.0` in a container, since `127.0.0.1` is the *container's* loopback and a published port would map to nothing |
| User | Runs as UID 1000. Several platforms require non-root |
| Secrets | One provider key. `LANGSMITH_API_KEY` is optional |
| Storage | Ephemeral is fine. SQLite resets, metrics restart from zero, and nothing is lost that matters |

## Hugging Face Spaces

**Status: attempted, blocked on cost.** Everything technical was solved; the free tier was not
available.

What had to change, all now in the codebase:

- **Python 3.10.** The Gradio SDK's base image is 3.10 and `python_version` in the frontmatter is
  ignored. `StrEnum` and `datetime.UTC` are 3.11-only — `state.py` now carries a `StrEnum` backport
  and `timezone.utc` is used throughout.
- **`requirements.txt` and `packages.txt`.** The Gradio SDK reads neither `pyproject.toml` nor a
  Dockerfile. `packages.txt` **cannot contain comments** — it is piped through `xargs` into
  `apt-get install`, so a comment becomes a list of package names and the build fails.
- **`ssr_mode=False`.** Gradio 5's server-side rendering spawns a Node process alongside Python.
  Unnecessary behind a platform proxy.
- **UID 1000.** The Dockerfile creates the user and owns `/app`, or the SQLite database and model
  cache are unwritable.

The blockers, in order encountered:

1. **Docker SDK is paid.** The Gradio SDK is the free-tier route.
2. **Creating a Gradio Space offers only ZeroGPU hardware**, which requires a `@spaces.GPU`
   function. This app is CPU-only by design, so it fails startup with
   `No @spaces.GPU function detected`.
3. **ZeroGPU cannot be downgraded to CPU basic without PRO.**
4. **Workaround that did work:** create the Space with the *static* template, which offers CPU
   basic, then change `sdk: static` to `sdk: gradio` in the README frontmatter. The Space accepted
   it and reported `sdk: gradio`, `requested: cpu-basic`.
5. **Free CPU Basic is quota-limited per account.** With a single Space and nothing else running,
   the quota was already exhausted. PRO ($9/month) is the only route from there.

### Space README frontmatter

Kept because it is the fiddly part:

```yaml
---
title: callsense
emoji: 📞
colorFrom: blue
colorTo: gray
sdk: gradio
sdk_version: 5.50.0
app_file: app.py
pinned: false
license: mit
short_description: Call analysis with injection and PII guards before the LLM
---
```

## Options if this is picked up again

| | cost | effort | notes |
|---|---|---|---|
| **Fly.io** | ~$3/mo | ~20 min | Existing Dockerfile works unchanged. Suspends and resumes in about a second, or `min_machines_running = 1` for always-on |
| **HF PRO** | $9/mo | none | Everything above is already done; subscribe and unpause. Discoverable by people browsing Hugging Face |
| **AWS Lightsail Containers** | $10/mo | ~1 hour | HTTPS and a stable URL included. The proportionate AWS answer |
| **AWS ECS Fargate + ALB** | ~$35–40/mo | half a day | The load balancer costs more than the compute. Worth it only if the AWS experience is itself the goal |

Whichever is chosen: point the demo at **Groq**. It has a free tier, and the provider spike measured
it at 1.84 s against OpenAI's 5.32 s — a public demo should be fast and should not bill a personal
OpenAI account.
