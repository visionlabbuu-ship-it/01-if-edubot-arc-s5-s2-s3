# ARC MaxPlus Model Switch Notes

This note documents the working ARC provider setup and how to switch between
MaxPlus Claude and MaxPlus GPT models without triggering the ARC login/onboarding
fallback.

## Current State

- Active ARC profile: `anthropic`
- Active model: `claude-haiku-4-5`
- Active base URL: `https://api.maxplus-ai.cc`
- API format: Anthropic Messages API through the Anthropic SDK
- Profile path: `C:\Users\korn\.arc\.arc-profile.json`
- Global ARC config path: `C:\Users\korn\.arc\.arc.json`
- Working backup before switching from Claude Opus 4.8 to GPT 5.4 mini:
  `C:\Users\korn\.arc\backups\before_gpt54mini_20260621-164030`
- Working backup before switching from GPT 5.4 mini to Claude Haiku:
  `C:\Users\korn\.arc\backups\before_haiku_switch_20260621-165820`

Do not store the API key in repo files. The live profile and backups under
`C:\Users\korn\.arc` contain local credentials.

## Important Rules

- Write ARC JSON files as UTF-8 without BOM. A BOM at the start of
  `.arc-profile.json` makes Node fail `JSON.parse`, and ARC falls back to
  `codexplan`, which can look like a login/auth problem.
- Keep `C:\Users\korn\.arc\.arc.json` with:
  - `hasCompletedOnboarding: true`
  - the last 20 characters of the active key in
    `customApiKeyResponses.approved`
  - the active key suffix removed from `customApiKeyResponses.rejected`
- For MaxPlus Claude through the Anthropic SDK, use base URL
  `https://api.maxplus-ai.cc` without `/v1`. The SDK appends `/v1/messages`.
- For MaxPlus GPT/OpenAI-compatible Responses API, use base URL
  `https://api.maxplus-ai.cc`, model `gpt-5.4-mini`, and
  `OPENAI_API_FORMAT=responses`.

## Backup Current ARC Config

```powershell
$ts = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupDir = Join-Path $HOME ".arc\backups\before_model_switch_$ts"
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null

foreach ($name in '.arc.json', '.arc-profile.json') {
  $src = Join-Path (Join-Path $HOME '.arc') $name
  if (Test-Path -LiteralPath $src) {
    Copy-Item -LiteralPath $src -Destination (Join-Path $backupDir $name) -Force
  }
}

$backupDir
```

## Switch To MaxPlus GPT 5.4 Mini

```powershell
$ErrorActionPreference = 'Stop'
$arcDir = Join-Path $HOME '.arc'
$profilePath = Join-Path $arcDir '.arc-profile.json'

$key = [Environment]::GetEnvironmentVariable('MAXPLUS_API_KEY', 'User')
if ([string]::IsNullOrWhiteSpace($key)) {
  $key = [Environment]::GetEnvironmentVariable('ANTHROPIC_API_KEY', 'User')
}
if ([string]::IsNullOrWhiteSpace($key)) {
  throw 'No MAXPLUS_API_KEY/ANTHROPIC_API_KEY configured at User scope.'
}

$profile = [ordered]@{
  profile = 'openai'
  env = [ordered]@{
    OPENAI_BASE_URL = 'https://api.maxplus-ai.cc'
    OPENAI_MODEL = 'gpt-5.4-mini'
    OPENAI_API_FORMAT = 'responses'
    OPENAI_API_KEY = $key
  }
  createdAt = (Get-Date).ToUniversalTime().ToString('o')
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText(
  $profilePath,
  ($profile | ConvertTo-Json -Depth 20),
  $utf8NoBom
)
```

## Switch Back To MaxPlus Claude Opus 4.8

```powershell
$ErrorActionPreference = 'Stop'
$arcDir = Join-Path $HOME '.arc'
$profilePath = Join-Path $arcDir '.arc-profile.json'

$key = [Environment]::GetEnvironmentVariable('MAXPLUS_API_KEY', 'User')
if ([string]::IsNullOrWhiteSpace($key)) {
  $key = [Environment]::GetEnvironmentVariable('ANTHROPIC_API_KEY', 'User')
}
if ([string]::IsNullOrWhiteSpace($key)) {
  throw 'No MAXPLUS_API_KEY/ANTHROPIC_API_KEY configured at User scope.'
}

$profile = [ordered]@{
  profile = 'anthropic'
  env = [ordered]@{
    ANTHROPIC_BASE_URL = 'https://api.maxplus-ai.cc'
    ANTHROPIC_MODEL = 'claude-opus-4-8'
    ANTHROPIC_API_KEY = $key
  }
  createdAt = (Get-Date).ToUniversalTime().ToString('o')
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText(
  $profilePath,
  ($profile | ConvertTo-Json -Depth 20),
  $utf8NoBom
)
```

## Switch To MaxPlus Claude Haiku 4.5

```powershell
$ErrorActionPreference = 'Stop'
$arcDir = Join-Path $HOME '.arc'
$profilePath = Join-Path $arcDir '.arc-profile.json'

$key = [Environment]::GetEnvironmentVariable('MAXPLUS_API_KEY', 'User')
if ([string]::IsNullOrWhiteSpace($key)) {
  $key = [Environment]::GetEnvironmentVariable('ANTHROPIC_API_KEY', 'User')
}
if ([string]::IsNullOrWhiteSpace($key)) {
  throw 'No MAXPLUS_API_KEY/ANTHROPIC_API_KEY configured at User scope.'
}

$profile = [ordered]@{
  profile = 'anthropic'
  env = [ordered]@{
    ANTHROPIC_BASE_URL = 'https://api.maxplus-ai.cc'
    ANTHROPIC_MODEL = 'claude-haiku-4-5'
    ANTHROPIC_API_KEY = $key
  }
  createdAt = (Get-Date).ToUniversalTime().ToString('o')
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText(
  $profilePath,
  ($profile | ConvertTo-Json -Depth 20),
  $utf8NoBom
)
```

## Restore From Backup

Replace the backup directory with the backup you want to restore.

```powershell
$backupDir = 'C:\Users\korn\.arc\backups\before_gpt54mini_20260621-164030'
Copy-Item -LiteralPath (Join-Path $backupDir '.arc.json') `
  -Destination "$HOME\.arc\.arc.json" -Force
Copy-Item -LiteralPath (Join-Path $backupDir '.arc-profile.json') `
  -Destination "$HOME\.arc\.arc-profile.json" -Force
```

If the restored files were written by PowerShell with BOM, rewrite them:

```powershell
foreach ($path in @("$HOME\.arc\.arc-profile.json", "$HOME\.arc\.arc.json")) {
  $obj = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
  $json = $obj | ConvertTo-Json -Depth 20
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($path, $json, $utf8NoBom)
}
```

## Verify

```powershell
arc --help
```

There should be no warning about `codexplan`.

```powershell
arc -p "reply exactly: HAIKU_OK" --permission-mode bypassPermissions
```

Expected output:

```text
HAIKU_OK
```
