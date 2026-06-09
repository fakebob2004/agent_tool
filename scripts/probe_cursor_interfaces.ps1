param(
    [string]$OutputPath = "docs/cursor_interface_probe.json"
)

$ErrorActionPreference = "SilentlyContinue"

function Get-CommandInfo {
    param([string]$Name)

    $commands = @(Get-Command $Name -All -ErrorAction SilentlyContinue)
    return @($commands | ForEach-Object {
        [ordered]@{
            name = $_.Name
            command_type = "$($_.CommandType)"
            source = "$($_.Source)"
            path = "$($_.Path)"
            definition = "$($_.Definition)"
        }
    })
}

function Invoke-ProbeCommand {
    param(
        [string]$Command,
        [string[]]$Arguments
    )

    $executable = Get-Command $Command -ErrorAction SilentlyContinue
    if (-not $executable) {
        return [ordered]@{
            attempted = $true
            found = $false
            command = $Command
            arguments = $Arguments
            exit_code = $null
            stdout = ""
            stderr = "command not found"
        }
    }

    try {
        $global:LASTEXITCODE = 0
        $output = & $Command @Arguments 2>&1 | Out-String
        $exitCode = $LASTEXITCODE

        return [ordered]@{
            attempted = $true
            found = $true
            command = $Command
            arguments = $Arguments
            exit_code = $exitCode
            stdout = $output
            stderr = ""
        }
    }
    catch {
        return [ordered]@{
            attempted = $true
            found = $true
            command = $Command
            arguments = $Arguments
            exit_code = $null
            stdout = ""
            stderr = "$($_.Exception.Message)"
        }
    }
}

function Select-FirstPath {
    param([object[]]$Commands)

    foreach ($command in $Commands) {
        if ($command.path) {
            return $command.path
        }
        if ($command.source) {
            return $command.source
        }
    }
    return $null
}

$cursorCommands = Get-CommandInfo "cursor"
$agentCommands = Get-CommandInfo "agent"
$cursorAgentCommands = Get-CommandInfo "cursor-agent"

$candidateRoots = @(
    "$env:USERPROFILE\.local\bin",
    "$env:USERPROFILE\.cursor\bin",
    "$env:LOCALAPPDATA\Programs",
    "$env:APPDATA\npm",
    "$env:USERPROFILE\bin"
)

$candidateExecutables = @()
foreach ($path in $candidateRoots) {
    if (Test-Path $path) {
        $candidateExecutables += Get-ChildItem $path -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^(agent|cursor-agent)(\.exe|\.cmd|\.ps1)?$' } |
            ForEach-Object { $_.FullName }
    }
}

$cursorVersion = Invoke-ProbeCommand "cursor" @("--version")
$cursorHelp = Invoke-ProbeCommand "cursor" @("--help")
$agentVersion = Invoke-ProbeCommand "agent" @("--version")
$agentHelp = Invoke-ProbeCommand "agent" @("--help")
$agentAcpHelp = Invoke-ProbeCommand "agent" @("acp", "--help")
$cursorAgentVersion = Invoke-ProbeCommand "cursor-agent" @("--version")
$cursorAgentHelp = Invoke-ProbeCommand "cursor-agent" @("--help")
$cursorAgentAcpHelp = Invoke-ProbeCommand "cursor-agent" @("acp", "--help")

$agentFound = ($agentCommands.Count -gt 0) -or ($cursorAgentCommands.Count -gt 0)
$acpAvailable = (
    ($agentAcpHelp.found -and $agentAcpHelp.exit_code -eq 0) -or
    ($cursorAgentAcpHelp.found -and $cursorAgentAcpHelp.exit_code -eq 0)
)

$result = [ordered]@{
    generated_at = (Get-Date).ToUniversalTime().ToString("o")
    desktop_cursor = [ordered]@{
        found = ($cursorCommands.Count -gt 0)
        path = (Select-FirstPath $cursorCommands)
        version_probe = $cursorVersion
    }
    agent_cli = [ordered]@{
        found = $agentFound
        agent_commands = $agentCommands
        cursor_agent_commands = $cursorAgentCommands
        candidate_executables = @($candidateExecutables | Sort-Object -Unique)
        agent_version_probe = $agentVersion
        cursor_agent_version_probe = $cursorAgentVersion
    }
    acp = [ordered]@{
        available = $acpAvailable
        agent_acp_help_probe = $agentAcpHelp
        cursor_agent_acp_help_probe = $cursorAgentAcpHelp
    }
    help = [ordered]@{
        cursor_help = $cursorHelp
        agent_help = $agentHelp
        cursor_agent_help = $cursorAgentHelp
    }
    path_entries = @($env:PATH -split ';' | Where-Object { $_ })
}

$output = $result | ConvertTo-Json -Depth 8
$parent = Split-Path -Parent $OutputPath
if ($parent) {
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
}
Set-Content -LiteralPath $OutputPath -Value $output -Encoding UTF8
Write-Output $output
