<#
.SYNOPSIS
Installs or updates separate ARC MaxPlus provider profiles.

.DESCRIPTION
Creates one Anthropic profile for Claude Sonnet 5 and one OpenAI-compatible
profile for GPT Terra so each model uses its correct tool-calling protocol.
#>
param(
    [string]$RegistryPath = (Join-Path $PSScriptRoot 'maxplus-models.json')
)

$ErrorActionPreference = 'Stop'
$registry = Get-Content -LiteralPath $RegistryPath -Raw | ConvertFrom-Json
$apiKey = [Environment]::GetEnvironmentVariable('MAXPLUS_API_KEY', 'User')
if ([string]::IsNullOrWhiteSpace($apiKey)) {
    throw 'MAXPLUS_API_KEY is not configured at User scope.'
}

$models = @($registry.models)
if ($models.Count -eq 0) {
    throw 'The MaxPlus model registry contains no models.'
}
$selectors = @($models | ForEach-Object { [string]$_.selector })
if ($selectors -notcontains [string]$registry.default_selector) {
    throw 'default_selector must be present in the models list.'
}
foreach ($model in $models) {
    if ([string]::IsNullOrWhiteSpace([string]$model.id) -or
        [string]::IsNullOrWhiteSpace([string]$model.profile_id) -or
        [string]::IsNullOrWhiteSpace([string]$model.provider)) {
        throw 'Every MaxPlus model requires id, profile_id, and provider.'
    }
}

$arcDir = Join-Path $env:USERPROFILE '.arc'
$configPath = Join-Path $arcDir '.arc.json'
$legacyProfilePath = Join-Path $arcDir '.arc-profile.json'
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupDir = Join-Path $arcDir "backups\before_maxplus_split_profiles_$timestamp"
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null

foreach ($configFile in @($configPath, $legacyProfilePath)) {
    if (Test-Path -LiteralPath $configFile) {
        Copy-Item -LiteralPath $configFile -Destination (Join-Path $backupDir (Split-Path $configFile -Leaf)) -Force
    }
}

if (Test-Path -LiteralPath $configPath) {
    $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
} else {
    $config = [pscustomobject]@{}
}

$managedProfileIds = @('provider_maxplus') + @(
    $models | ForEach-Object { [string]$_.profile_id }
)
$existingProfiles = @($config.providerProfiles | Where-Object {
    $_ -and $managedProfileIds -notcontains [string]$_.id
})
$newProfiles = @(
    $models | ForEach-Object {
        $profile = [ordered]@{
            id = [string]$_.profile_id
            provider = [string]$_.provider
            name = [string]$_.profile_name
            baseUrl = [string]$registry.base_url
            model = [string]$_.id
            apiKey = $apiKey
        }
        if ([string]$_.provider -eq 'openai') {
            $profile.apiFormat = if ($_.api_format) {
                [string]$_.api_format
            } else {
                'chat_completions'
            }
        }
        $profile
    }
)

$defaultModel = $models | Where-Object {
    [string]$_.selector -eq [string]$registry.default_selector
} | Select-Object -First 1
$defaultProfileId = [string]$defaultModel.profile_id

$config | Add-Member -NotePropertyName providerProfiles -NotePropertyValue @($existingProfiles + $newProfiles) -Force
$config | Add-Member -NotePropertyName activeProviderProfileId -NotePropertyValue $defaultProfileId -Force
$config | Add-Member -NotePropertyName hasCompletedOnboarding -NotePropertyValue $true -Force

$cacheByProfile = [ordered]@{}
if ($config.openaiAdditionalModelOptionsCacheByProfile) {
    foreach ($property in $config.openaiAdditionalModelOptionsCacheByProfile.psobject.Properties) {
        if ($managedProfileIds -notcontains $property.Name) {
            $cacheByProfile[$property.Name] = $property.Value
        }
    }
}
foreach ($model in @($models | Where-Object { [string]$_.provider -eq 'openai' })) {
    $cacheByProfile[[string]$model.profile_id] = @(
        [ordered]@{
            value = [string]$model.id
            label = [string]$model.label
            description = "Provider: $($model.profile_name)"
        }
    )
}
$config | Add-Member -NotePropertyName openaiAdditionalModelOptionsCache -NotePropertyValue @() -Force
$config | Add-Member -NotePropertyName openaiAdditionalModelOptionsCacheByProfile -NotePropertyValue $cacheByProfile -Force

$keySuffix = $apiKey.Substring([Math]::Max(0, $apiKey.Length - 20))
$approved = @($config.customApiKeyResponses.approved | Where-Object { $_ })
$rejected = @($config.customApiKeyResponses.rejected | Where-Object { $_ -and $_ -ne $keySuffix })
if ($approved -notcontains $keySuffix) {
    $approved += $keySuffix
}
$config | Add-Member -NotePropertyName customApiKeyResponses -NotePropertyValue ([ordered]@{
    approved = $approved
    rejected = $rejected
}) -Force

if ([string]$defaultModel.provider -eq 'anthropic') {
    $startupProfile = [ordered]@{
        profile = 'anthropic'
        env = [ordered]@{
            ANTHROPIC_BASE_URL = [string]$registry.base_url
            ANTHROPIC_MODEL = [string]$defaultModel.id
            ANTHROPIC_API_KEY = $apiKey
        }
        createdAt = (Get-Date).ToUniversalTime().ToString('o')
    }
} else {
    $startupProfile = [ordered]@{
        profile = 'openai'
        env = [ordered]@{
            OPENAI_BASE_URL = [string]$registry.base_url
            OPENAI_MODEL = [string]$defaultModel.id
            OPENAI_API_FORMAT = [string]$defaultModel.api_format
            OPENAI_API_KEY = $apiKey
        }
        createdAt = (Get-Date).ToUniversalTime().ToString('o')
    }
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($configPath, ($config | ConvertTo-Json -Depth 30), $utf8NoBom)
[System.IO.File]::WriteAllText($legacyProfilePath, ($startupProfile | ConvertTo-Json -Depth 20), $utf8NoBom)

Write-Output "Installed ARC MaxPlus profiles: $($newProfiles.name -join ', ')"
Write-Output "Default selector: $($registry.default_selector)"
Write-Output "Active profile: $defaultProfileId"
Write-Output "Backup: $backupDir"