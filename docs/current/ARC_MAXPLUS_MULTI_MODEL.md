# ARC MaxPlus Multi-Model Setup

Updated: 2026-08-11

## Goal

Start ARC normally from CMD or PowerShell:

```powershell
arc
```

Then use the interactive command below to select a MaxPlus model:

```text
/model
```

## Current Models

| Model | MaxPlus API route | Status |
|---|---|---|
| `gpt-5.6-terra` | OpenAI-compatible Chat Completions | Verified |
| `claude-sonnet-5` | OpenAI-compatible Chat Completions | Verified |

Both models use one ARC provider profile named `MaxPlus`. MaxPlus accepts both
models through its OpenAI-compatible route, so changing models does not require
changing provider or restarting ARC.

## Install Or Refresh The Profile

The API key must be stored in the Windows User environment as
`MAXPLUS_API_KEY`. Do not put the key in repository files.

From the repository root:

```powershell
.\tools\setup-arc-maxplus.ps1
```

The setup script:

1. Reads [maxplus-models.json](../../tools/maxplus-models.json).
2. Backs up the current `.arc.json` and `.arc-profile.json` files.
3. Creates or updates the `MaxPlus` provider profile.
4. Makes MaxPlus the active provider used by the normal `arc` command.
5. Registers all listed models for the `/model` picker.

ARC stores active provider credentials in the local user config under
`%USERPROFILE%\.arc`. These files are outside the repository and must not be
committed or shared.

## Add More Models

Add another entry to `tools/maxplus-models.json`:

```json
{
  "id": "model-id-from-maxplus",
  "label": "Readable model name",
  "verified": "YYYY-MM-DD"
}
```

Then rerun:

```powershell
.\tools\setup-arc-maxplus.ps1
```

Restart ARC and open `/model`. The new model will appear in the picker. Put the
desired startup model in `default_model`; it must also exist in `models`.

## Verification

From a new CMD window:

```cmd
arc -p "Reply exactly: MAXPLUS_OK"
```

For interactive verification:

```cmd
arc
```

Then run `/model`, select each model, and send a short prompt. The footer should
show the selected model before running robot tasks.

## Protocol Note

Use `chat_completions` for the current MaxPlus GPT route. Its Responses API
returns message content under `output[].content[].text` but leaves the aggregate
`output_text` field empty, which causes this ARC version to render a blank reply.
