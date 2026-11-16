# Build AI With Dinesh — Local Social Intelligence + Video Production Engine

This project is a local, schema-driven Remotion production engine plus a local social-intelligence layer for vertical short-form and 16:9 long-form YouTube. A JSON storyboard is the production source of truth: it drives scenes, narration timing, animated subtitles, thumbnails, package metadata, and output validation.

## What is included

- Strict Zod storyboard validation, plus matching Python guardrails with exact errors.
- Centralized Build AI With Dinesh tokens and reusable core motion components.
- Schema-driven scene registry covering architecture/data flow, terminal and permission events, evidence boards, human review, technical metrics, AI City, chapters, comparisons, and branded outros.
- Remotion compositions for `AI-Detective`, `Inside-The-Machine`, `AI-City`, `Long-Form-YouTube`, and vertical/YouTube thumbnail stills.
- Local WAV narration via Windows SAPI; narration duration scales the scene timeline, and phrase-style subtitle timing is explicitly marked `estimated`.
- A Python CLI for validation, preview, production, inspection, thumbnail recovery, and packaging.
- H.264/AAC rendering, FFprobe metadata checks, decode-frame checks, representative frame extraction, resumable manifests, no implicit overwrite, and bounded render concurrency.
- Local SQLite knowledge store for owned-account performance, permitted public competitor observations, ideas, hooks, creative drafts, and feedback snapshots.
- Official Meta/YouTube connector commands, plus a platform-export JSON import path that works without an always-on service.
- Evidence-backed research briefs and a reusable ChatGPT Desktop prompt for writing separate Shorts/Reels and long-form scripts/storyboards without an OpenAI API key.

## Prerequisites

From `D:\Dinesh\AI SM Manager`:

```powershell
cd remotion
npm install
npx remotion browser ensure
cd ..
```

The engine requires Node.js, Python 3.12+, Windows PowerShell, and a local Windows SAPI voice. Remotion bundles the FFmpeg/FFprobe binaries it uses; a system FFmpeg installation is not required.

Kokoro remains an optional provider hook. It was not usable in the checked environment, so the engine uses the free local Windows SAPI fallback. The installed machine voice is `Microsoft Zira Desktop`; no male-presenting local voice was installed, and the sample manifests state that actual fallback rather than claiming otherwise.

## Create a production

Validate first:

```powershell
python -m social_manager validate --storyboard examples\ai-system-visualization.json
```

Render a final package:

```powershell
python -m social_manager produce --storyboard path\to\storyboard.json
```

For an intentionally supplied local WAV (useful when the native voice engine is managed separately):

```powershell
python -m social_manager produce --storyboard path\to\storyboard.json --narration-wav path\to\narration.wav --narration-voice-name "Actual installed voice"
```

Other commands:

```powershell
python -m social_manager preview --storyboard path\to\storyboard.json
python -m social_manager render --storyboard path\to\storyboard.json
python -m social_manager package --storyboard path\to\storyboard.json
python -m social_manager inspect --output output\YYYY-MM-DD\video-slug
python -m social_manager thumbnail --output output\YYYY-MM-DD\video-slug
python -m social_manager finalize --output output\YYYY-MM-DD\video-slug
```

`produce` refuses to overwrite a non-empty package unless `--overwrite` is passed. The renderer uses a hardware-aware bounded default (maximum four workers); set `SOCIAL_MANAGER_RENDER_CONCURRENCY` to override it deliberately.

Start an interactive Remotion preview:

```powershell
cd remotion
npm run dev -- --no-open
```

## Local social-intelligence workflow

All collected observations, SQLite data, research briefs, storyboards, and rendered files stay in this project. Meta and Google API requests necessarily communicate with their platforms, and ChatGPT Desktop is the interactive creative control plane; neither is a local language model.

The intelligence layer does **not** scrape platforms or claim access to competitor private analytics. It stores your authorized analytics and permitted public competitor metadata/counts, then produces testable creative hypotheses. It never promises virality.

Initialize the local SQLite database, import the included synthetic example, and create a brief for **both** short-form and long-form:

```powershell
python -m social_manager intelligence-init
python -m social_manager import-analytics --input examples\analytics-sample.json
python -m social_manager analyze --track both
```

The last command prints a path like:

```text
research/YYYY-MM-DD/analysis-.../analysis.json
research/YYYY-MM-DD/analysis-.../creative_brief.md
research/YYYY-MM-DD/analysis-.../chatgpt-desktop-prompt.md
```

Open the prompt in ChatGPT Desktop. It tells ChatGPT to use the local evidence, create one Shorts/Reels storyboard and one long-form YouTube storyboard under `examples/drafts/`, validate them, and wait for approval before rendering or publishing.

### Official API syncs

No OpenAI API key is needed. To connect live platform data, enable the relevant APIs in your Meta/Google developer projects and load tokens only into the local shell or Windows Credential Manager. `.env.example` names the required values; do not commit a populated `.env` file.

For YouTube, one Google OAuth token needs both Analytics read access and Data API read access: Analytics supplies your private channel performance, while the Data API supplies public channel/video metadata for competitor research.

```powershell
$env:YOUTUBE_ACCESS_TOKEN = "your-short-lived-oauth-token"
python -m social_manager sync-youtube --start-date 2026-08-17 --end-date 2026-11-15
python -m social_manager sync-competitor --platform youtube --handle "@examplechannel"
```

For Instagram, use your professional account and Meta access token. Meta determines which public professional accounts and fields are eligible for Business Discovery.

```powershell
$env:META_ACCESS_TOKEN = "your-meta-access-token"
$env:INSTAGRAM_ACCOUNT_ID = "your-professional-account-id"
python -m social_manager sync-instagram
python -m social_manager sync-competitor --platform instagram --handle "examplecreator"
```

To maintain a local competitor list, copy and edit `examples/competitors.example.json`, then run:

```powershell
python -m social_manager sync-competitors --config path\to\competitors.json
```

### Improvement loop

1. Sync or import performance data.
2. Run `analyze --track both` to generate separate evidence-backed short-form and long-form concepts, hooks, and a ChatGPT Desktop writing prompt.
3. In ChatGPT Desktop, turn the chosen concepts into validated storyboard JSON, then `register-draft` it locally.
4. Approve and run `produce` to render with the existing Remotion pipeline.
5. After publishing, link the platform item to the production and sync metrics again. The next local brief uses that new evidence.

```powershell
python -m social_manager register-draft --storyboard examples\drafts\my-short.json
python -m social_manager produce --storyboard examples\drafts\my-short.json
python -m social_manager register-publication --video-id my-short --platform youtube --external-id PLATFORM_VIDEO_ID --account-external-id YOUR_CHANNEL_ID
python -m social_manager sync-youtube
python -m social_manager analyze --track both
```

For a metric that is not exposed by a sync, add it locally to an already tracked item:

```powershell
python -m social_manager record-metric --platform youtube --external-id PLATFORM_VIDEO_ID --metric views --value 12000
```

## Storyboard contract

See [examples/ai-system-visualization.json](examples/ai-system-visualization.json) and [examples/ai-agent-broke-rules.json](examples/ai-agent-broke-rules.json). Important fields include:

- `series`, `platform`, `aspect_ratio`, `fps`, `narration_script`, and `narration_voice`
- scene IDs, typed scene types, headings, narration, duration weights, assets, and transition type
- fictional status and source references (non-fiction AI Detective videos require sources)
- thumbnail specification and platform metadata

Actual audio duration is measured from the generated WAV. Scene duration weights are proportionally resolved to that duration, with a short branded ending added after narration, so speech is never trimmed to meet an arbitrary timeline.

## Output package

Each completed job writes:

```text
output/YYYY-MM-DD/VIDEO-SLUG/
  video.mp4
  thumbnail.png
  caption.txt
  script.md
  storyboard.json
  narration.wav
  sources.md
  manifest.json
  inspection/frame-01.png ... frame-05.png
```

For 16:9 YouTube jobs, `youtube_description.txt` and `youtube_tags.txt` are also produced.

## Tests

```powershell
python -m unittest discover -s tests -v
cd remotion
npm run lint
```

The tests cover storyboard rules, duration resolution, output paths, caption length, and manifest serialization. The two supplied production examples are also rendered, inspected, and FFprobe-validated as end-to-end smoke tests.
