# Скачивает портативный Python (официальная embeddable-сборка с python.org, ~11 МБ) в папку python\ рядом с программой.
# Запускается из start.bat, только если Python в папке программы и в системе не нашёлся.
param([string]$Dest = (Join-Path (Split-Path $PSScriptRoot -Parent) "python"))
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$ver = "3.12.10"
$url = "https://www.python.org/ftp/python/$ver/python-$ver-embed-amd64.zip"
$sha = "4ACBED6DD1C744B0376E3B1CF57CE906F9DC9E95E68824584C8099A63025A3C3"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$zip = Join-Path $env:TEMP "okno-python-$ver.zip"
$tmp = "$Dest.tmp"
try {
    Invoke-WebRequest $url -OutFile $zip -UseBasicParsing
    if ((Get-FileHash $zip -Algorithm SHA256).Hash -ne $sha) { throw "Контрольная сумма скачанного файла не совпала" }
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    Expand-Archive $zip -DestinationPath $tmp -Force
    if (Test-Path $Dest) { Remove-Item $Dest -Recurse -Force }
    Move-Item $tmp $Dest
} finally {
    if (Test-Path $zip) { Remove-Item $zip -Force }
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
}
