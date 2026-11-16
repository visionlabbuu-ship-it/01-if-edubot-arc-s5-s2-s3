<#
.SYNOPSIS
Runs ARC against the MaxPlus gateway with a selected provider profile.

.EXAMPLE
.\tools\arc-maxplus.ps1 gpt
.\tools\arc-maxplus.ps1 claude
.\tools\arc-maxplus.ps1 gpt -Prompt "Reply exactly: MAXPLUS_GPT_OK"
#>
param(
    [Parameter(Mandatory, Position = 0)]
    [ValidateSet('gpt', 'claude')]
    [string]$MaxPlusProfile,

    [string]$Prompt,

    [string[]]$ArcArguments
)

$ErrorActionPreference = 'Stop'
$apiKey = [Environment]::GetEnvironmentVariable('MAXPLUS_API_KEY', 'User')
if ([string]::IsNullOrWhiteSpace($apiKey)) {
    throw 'MAXPLUS_API_KEY is not configured at User scope.'
}

switch ($MaxPlusProfile) {
    'gpt' {
        $env:OPENAI_BASE_URL = 'https://api.maxplus-ai.cc'
        $env:OPENAI_API_FORMAT = 'chat_completions'
        $env:OPENAI_API_KEY = $apiKey
        $model = 'gpt-5.6-terra'
        $provider = 'openai'
    }
    'claude' {
        $env:ANTHROPIC_BASE_URL = 'https://api.maxplus-ai.cc'
        $env:ANTHROPIC_API_KEY = $apiKey
        $model = 'claude-sonnet-5'
        $provider = 'anthropic'
    }
}

$launchArguments = @('--provider', $provider, '--model', $model)
if (-not [string]::IsNullOrWhiteSpace($Prompt)) {
    $launchArguments += @('--print', $Prompt)
}
if ($ArcArguments) {
    $launchArguments += $ArcArguments
}

& arc @launchArguments
exit $LASTEXITCODE