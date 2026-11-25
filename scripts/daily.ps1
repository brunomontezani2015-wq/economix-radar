# Economix Radar - rotina de coleta (Agendador de Tarefas do Windows: de hora em hora, a partir das 08:00).
# 1) coleta todas as redes ativas  2) exporta os arquivos do painel (quedas das ultimas 24h)
# 3) quedas desde a coleta anterior -> Telegram, somente se houver queda e se o .env tiver o token.
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root '.venv\Scripts\python.exe'
$logDir = Join-Path $root 'data\logs'
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir ("daily_{0:yyyy-MM-dd}.log" -f (Get-Date))
$env:PYTHONIOENCODING = 'utf-8'

function Write-Step($msg) { "[{0:HH:mm:ss}] {1}" -f (Get-Date), $msg | Out-File $log -Append -Encoding utf8 }

# Mantem o computador acordado enquanto este script roda (sem isso, apos um despertar automatico o Windows
# volta a suspender em ~2 minutos e interrompe a coleta). O efeito acaba quando o processo termina.
Add-Type -Namespace Economix -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);'
[void][Economix.Power]::SetThreadExecutionState([uint32]'0x80000001')  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED

function Invoke-Logged([string[]]$cmdArgs) {
    # cmd /c junta stdout+stderr sem o embrulho de erro do PowerShell; Out-File grava em UTF-8
    cmd /c "`"$py`" $($cmdArgs -join ' ') 2>&1" | Out-File $log -Append -Encoding utf8
}

Write-Step 'inicio'
Invoke-Logged @('-m', 'economix', 'collect')
Write-Step "coleta terminou (exit $LASTEXITCODE)"
Invoke-Logged @('scripts\export_dashboard.py')
Write-Step "export terminou (exit $LASTEXITCODE)"

$envFile = Join-Path $root '.env'
$hasTelegram = (Test-Path $envFile) -and (Select-String -Path $envFile -Pattern '^TELEGRAM_BOT_TOKEN=.+' -Quiet)
if ($hasTelegram) { Invoke-Logged @('-m', 'economix', 'oportunidades', '--enviar') } else { Invoke-Logged @('-m', 'economix', 'oportunidades') }
Write-Step "oportunidades terminou (exit $LASTEXITCODE)"

# Segura o computador acordado ate os :40 da hora, para a tarefa do Claude (:25) enviar os dados ao painel.
$holdUntil = (Get-Date).Date.AddHours((Get-Date).Hour).AddMinutes(40)
if ((Get-Date) -lt $holdUntil) {
    Write-Step ("mantendo o computador acordado ate {0:HH:mm} para a atualizacao do painel" -f $holdUntil)
    Start-Sleep -Seconds ([int]($holdUntil - (Get-Date)).TotalSeconds)
}
Write-Step 'fim'
