<#
.SYNOPSIS
    Runs the OrcaSlicer 2.5.0-alpha end-to-end test of the 3D Print Log plugin in Docker.

.DESCRIPTION
    Builds the plugin wheel from this checkout, downloads the OrcaSlicer AppImage (once, into
    e2e/cache), builds the test-bench image, runs scripts/e2e.sh in a container against the
    local API, then checks the stored rows with verify.py. Screenshots, logs and results land
    in e2e/out.

    Needs: Docker Desktop (Linux engine), gh (for the AppImage download), Python 3.12, the API
    running on -ApiPort with ASPNETCORE_ENVIRONMENT=E2ETesting, and its SQL Server container.
    See README.md.

.EXAMPLE
    .\e2e\run.ps1
    .\e2e\run.ps1 -FreshWizard          # run Orca's first-run wizard instead of the cached datadir
    .\e2e\run.ps1 -PromptAnswer yes     # answer Yes to any plugin permission prompt
    .\e2e\run.ps1 -Mode permissions     # the prompt spike with variants/permission_probe.py (answers Yes)
#>
param(
    [string]$OrcaTag = "v2.5.0-alpha",
    [string]$AppImage = "OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.5.0-alpha.AppImage",
    [int]$ApiPort = 5101,
    [string]$WebBase = "http://localhost:4310",
    [string]$SqlContainer = "3d-print-log-api-sqlserver-1",
    [string]$Database = "PrintLogDb_OrcaE2E",
    [ValidateSet("no", "yes")][string]$PromptAnswer = "no",
    [ValidateSet("scenarios", "permissions")][string]$Mode = "scenarios",
    [switch]$FreshWizard
)

$ErrorActionPreference = "Stop"
$e2e = $PSScriptRoot
$repo = Resolve-Path (Join-Path $e2e "..")
$cache = Join-Path $e2e "cache"
$out = Join-Path $e2e "out"
$image = "printlog-orca-e2e"
$name = "printlog-orca-e2e-run"
New-Item -ItemType Directory -Force $cache, $out | Out-Null

# 1. The AppImage (cached on D: next to this script, never in the image layers).
if (-not (Test-Path (Join-Path $cache $AppImage))) {
    Write-Host "Downloading $AppImage ($OrcaTag)"
    gh release download $OrcaTag -R OrcaSlicer/OrcaSlicer -p $AppImage -D $cache
    if ($LASTEXITCODE -ne 0) { throw "gh release download failed" }
}

# 2. The wheel, under the name releases use (Orca keys a local plugin by its file name).
$venv = Join-Path $repo ".venv"
$python = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $python)) { python -m venv $venv; & $python -m pip install -q build }
$dist = Join-Path $cache "dist"
Remove-Item -Recurse -Force $dist -ErrorAction SilentlyContinue
& $python -m build --wheel --outdir $dist $repo | Out-Null
if ($LASTEXITCODE -ne 0) { throw "python -m build failed" }
$wheel = Join-Path $cache "3d-print-log.whl"
Copy-Item (Get-ChildItem $dist -Filter *.whl | Select-Object -First 1).FullName $wheel -Force

# 3. The API must answer, from the host and (via host.docker.internal) from the container.
try { Invoke-WebRequest -UseBasicParsing "http://localhost:$ApiPort/" -TimeoutSec 5 | Out-Null }
catch { if (-not $_.Exception.Response) { throw "No API on http://localhost:$ApiPort. Start it first (README.md)." } }

Write-Host "Building $image"
docker build -q -t $image --build-arg APPIMAGE=$AppImage $e2e | Out-Null
if ($LASTEXITCODE -ne 0) { throw "docker build failed" }

if ($Mode -eq "permissions") {
    # Keeps the scenario run's artifacts: the prompts go to out/prompts, the rest to out/permissions.
    $PromptAnswer = "yes"
    Remove-Item -Recurse -Force (Join-Path $out "prompts"), (Join-Path $out "permissions") -ErrorAction SilentlyContinue
} else {
    Get-ChildItem $out -Force | Remove-Item -Recurse -Force
}
$since = (Get-Date).ToUniversalTime().AddSeconds(-5).ToString("yyyy-MM-ddTHH:mm:ss")
docker rm -f $name 2>$null | Out-Null
# Files go in and out with docker cp: no bind mounts, so no Docker Desktop file-sharing prompts.
docker create --name $name --add-host=host.docker.internal:host-gateway `
    -e API_BASE="http://host.docker.internal:$ApiPort" -e WEB_BASE=$WebBase -e PROMPT_ANSWER=$PromptAnswer -e MODE=$Mode `
    $image | Out-Null
docker cp $wheel "${name}:/in/3d-print-log.whl"
$snapshot = Join-Path $cache "datadir-after-wizard.tgz"
if (-not $FreshWizard -and (Test-Path $snapshot)) { docker cp $snapshot "${name}:/in/datadir.tgz" }
docker cp (Join-Path $e2e "scripts\.") "${name}:/e2e/"
docker cp (Join-Path $e2e "variants") "${name}:/e2e/variants"

Write-Host "Running ($Mode, about 6 minutes)"
docker start -a $name
$exit = $LASTEXITCODE
if ($Mode -eq "permissions") {
    $permOut = Join-Path $out "permissions"
    New-Item -ItemType Directory -Force $permOut | Out-Null
    docker cp "${name}:/out/." $permOut
    Move-Item (Join-Path $permOut "prompts") (Join-Path $out "prompts") -Force
    docker rm -f $name | Out-Null
    if ($exit -ne 0) { throw "permissions run FAILED ($exit); see $permOut" }
    Write-Host "Done; prompts in $(Join-Path $out 'prompts'), logs in $permOut"
    return
}
docker cp "${name}:/out/." $out
docker rm -f $name | Out-Null
if (Test-Path (Join-Path $out "datadir-after-wizard.tgz")) { Copy-Item (Join-Path $out "datadir-after-wizard.tgz") $snapshot -Force }

Write-Host "Checking the stored rows"
& $python -I (Join-Path $e2e "verify.py") --out $out --since $since --sql-container $SqlContainer --database $Database
$verify = $LASTEXITCODE

if ($exit -ne 0 -or $verify -ne 0) { throw "e2e FAILED (container: $exit, verify: $verify); see $out" }
Write-Host "PASS; screenshots and logs in $out"
