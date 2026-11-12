# issn_pc: puts the previous version of the port back over this folder (issn 11-12).
# Started by "Voltar para a versao anterior.bat" next to it; works when the launcher does not open.
# 1. previous-version\ (kept by the launcher's last update): copied back, no download.
# 2. Otherwise the newest GitHub release older than this one (issn-windows.zip) is downloaded.
# Never touched: user\ (saves, caches, trophies), out\, mods\, issn.ini, mods.json,
# mods_presets.json, patches.json, last_run.log. Messages are plain ASCII (any console code page).
# -Check: says what it would do and stops before any download or copy (to test it).
param([switch]$Check)
$ErrorActionPreference = 'Stop'
$port = $PSScriptRoot
$keep = @('user', 'out', 'mods', 'previous-version', 'previous-version.part')
$keepFiles = @('issn.ini', 'mods.json', 'mods_presets.json', 'patches.json', 'last_run.log')
$repo = 'Liondarkz/issn_pc'

function Say($pt, $en) { Write-Host "$pt`n  ($en)" }
function Version-Of($folder) {
    $readme = Join-Path $folder 'README-Windows.txt'
    if (-not (Test-Path $readme)) { return '' }
    $first = (Get-Content -LiteralPath $readme -TotalCount 1)
    if ($first -match '(\d+(\.\d+)+)\s*$') { return $Matches[1] }
    return ''
}
function Version-Key($text) {
    # 0.3.7.10 > 0.3.7.9: each number padded
    return (($text -split '\.') | ForEach-Object { '{0:D6}' -f [int]$_ }) -join '.'
}
function Copy-Over($from) {
    $xd = @($keep | ForEach-Object { Join-Path $from $_ })
    & robocopy $from $port /E /NFL /NDL /NJH /NJS /NP /R:5 /W:1 /XD @xd /XF @keepFiles | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE)" }
}
function Copy-Seed($from) {
    # The older version's shader seed, when its folder is not on this PC yet (user\ is never overwritten).
    $list = Join-Path $from 'shader-seed.txt'
    if (-not (Test-Path $list)) { return }
    $name = (Get-Content -LiteralPath $list -TotalCount 1).Trim()
    $seed = Join-Path $from "user\cache\$name"
    $dest = Join-Path $port "user\cache\$name"
    if ($name -and (Test-Path $seed) -and -not (Test-Path $dest)) {
        New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
        Copy-Item -LiteralPath $seed -Destination $dest -Recurse
    }
}

try {
    $running = Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -in @('inFAMOUS Second Son', 'Play inFAMOUS Second Son', 'issn-probe') }
    if ($running) {
        Say 'Feche o jogo e o launcher antes de continuar.' 'Close the game and the launcher first.'
        exit 1
    }
    $current = Version-Of $port
    $previous = Join-Path $port 'previous-version'
    if (Test-Path (Join-Path $previous 'inFAMOUS Second Son.exe')) {
        $old = Version-Of $previous
        Say "Versao instalada: $current. Versao guardada: $old (da ultima atualizacao)." "Installed: $current. Kept: $old (from the last update)."
        if ($Check) { Say '-Check: nada foi copiado.' '-Check: nothing was copied.'; exit 0 }
        if ((Read-Host 'Voltar para ela? / Go back to it? (s/n)') -notmatch '^[sSyY]') { exit 0 }
        Copy-Over $previous
        Copy-Seed $previous
        Say "Pronto: versao $old instalada. Saves e configuracoes ficaram." "Done: version $old installed. Saves and settings were kept."
        exit 0
    }

    Say 'Nenhuma versao guardada aqui; procurando a anterior no GitHub...' 'No kept version here; looking for the previous one on GitHub...'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $releases = Invoke-RestMethod -Uri "https://api.github.com/repos/$repo/releases?per_page=30" -Headers @{ 'User-Agent' = 'issn-rollback' }
    $pick = $null
    foreach ($release in $releases) {
        if ($release.draft) { continue }
        $version = ([regex]::Matches($release.tag_name, '\d+') | ForEach-Object { $_.Value }) -join '.'
        $asset = $release.assets | Where-Object { $_.name -eq 'issn-windows.zip' } | Select-Object -First 1
        if (-not $asset -or -not $version) { continue }
        if ($current -and ((Version-Key $version) -ge (Version-Key $current))) { continue }
        if (-not $pick -or ((Version-Key $version) -gt (Version-Key $pick.version))) {
            $pick = [pscustomobject]@{ version = $version; url = $asset.browser_download_url; size = $asset.size }
        }
    }
    if (-not $pick) {
        Say 'Nao achei uma versao anterior a esta no GitHub.' 'No release older than this one was found on GitHub.'
        exit 1
    }
    $mb = [math]::Round($pick.size / 1MB)
    Say "Versao instalada: $current. Anterior no GitHub: $($pick.version) ($mb MB)." "Installed: $current. Previous on GitHub: $($pick.version) ($mb MB)."
    if ($Check) { Say '-Check: nada foi baixado nem copiado.' '-Check: nothing was downloaded or copied.'; exit 0 }
    if ((Read-Host 'Baixar e instalar? / Download and install? (s/n)') -notmatch '^[sSyY]') { exit 0 }
    $work = Join-Path $env:TEMP 'issn-rollback'
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $work | Out-Null
    $zip = Join-Path $work 'issn-windows.zip'
    $ProgressPreference = 'SilentlyContinue'  # Invoke-WebRequest is many times slower with its bar
    Invoke-WebRequest -Uri $pick.url -OutFile $zip -Headers @{ 'User-Agent' = 'issn-rollback' }
    Expand-Archive -LiteralPath $zip -DestinationPath (Join-Path $work 'new') -Force
    $exe = Get-ChildItem -LiteralPath (Join-Path $work 'new') -Recurse -Filter 'inFAMOUS Second Son.exe' | Select-Object -First 1
    if (-not $exe) { throw 'inFAMOUS Second Son.exe is missing from the download' }
    Copy-Over $exe.DirectoryName
    Copy-Seed $exe.DirectoryName
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    Say "Pronto: versao $($pick.version) instalada. Saves e configuracoes ficaram." "Done: version $($pick.version) installed. Saves and settings were kept."
    exit 0
} catch {
    Say "Nao deu certo: $($_.Exception.Message)" "It did not work: $($_.Exception.Message)"
    Say 'Baixe a versao que quiser em github.com/Liondarkz/issn_pc/releases e extraia por cima.' 'Download the version you want from the releases page and extract it over this folder.'
    exit 1
}
