# Worker propio SISTEMA MD. ASCII: compatible con Windows PowerShell 5.1.
# No login, claves API, rotacion de usuarios ni reintentos.
param([Parameter(Mandatory=$true)][string]$Folder)
$ErrorActionPreference = 'Stop'
$utf8 = New-Object Text.UTF8Encoding $false
$process = $null
$outFile = $null
$errFile = $null
$nonce = ''
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$state = 'failed'
$exitCode = -1
$outHash = ''

function Stop-OwnProcess {
    if ($null -ne $process -and -not $process.HasExited) {
        # PID del proceso creado por ESTE worker, nunca busqueda por nombre de proceso.
        $stop = New-Object Diagnostics.ProcessStartInfo
        $stop.FileName = Join-Path $env:SystemRoot 'System32\taskkill.exe'
        $stop.Arguments = '/PID ' + $process.Id + ' /T /F'
        $stop.UseShellExecute = $false
        $stop.CreateNoWindow = $true
        $stop.RedirectStandardOutput = $true
        $stop.RedirectStandardError = $true
        $killer = [Diagnostics.Process]::Start($stop)
        $null = $killer.StandardOutput.ReadToEndAsync()
        $null = $killer.StandardError.ReadToEndAsync()
        $null = $killer.WaitForExit(5000)
        if (-not $process.HasExited) { $process.Kill() }
        $null = $process.WaitForExit(5000)
    }
}

function Arg-Win([string]$value) {
    $value = $value -replace '(\\*)"', '$1$1\"'
    $value = $value -replace '(\\+)$', '$1$1'
    return '"' + $value + '"'
}

try {
    if (-not [IO.Path]::IsPathRooted($Folder) -or $Folder.StartsWith('\\') -or
        (Split-Path $Folder -Leaf) -cnotmatch '^[a-f0-9]{32}$') { throw 'folder_invalid' }
    $dir = Get-Item -LiteralPath $Folder
    if (($dir.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'reparse' }
    $controlPath = Join-Path $Folder 'control.json'
    if ((Get-Item -LiteralPath $controlPath).Length -gt 8192) { throw 'control_size' }
    $control = [IO.File]::ReadAllText($controlPath, $utf8) | ConvertFrom-Json
    $nonce = $control.nonce
    if ($nonce -cne (Split-Path $Folder -Leaf) -or $nonce -notmatch '^[a-f0-9]{32}$') { throw 'nonce' }
    if ($control.fab_sid -cne $sid) { throw 'identity' }
    if ($control.model -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') { throw 'model' }
    if ($control.timeout -isnot [int] -or $control.timeout -lt 5 -or $control.timeout -gt 1200) { throw 'timeout' }
    if (@($control.allowed_sids).Count -ne 4) { throw 'acl_allowlist' }
    $acl = Get-Acl -LiteralPath $Folder
    $rules = @($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
    if (-not $acl.AreAccessRulesProtected -or $rules.Count -ne 4) { throw 'acl_protection' }
    foreach ($rule in $rules) {
        if ($rule.IsInherited -or $rule.AccessControlType -ne 'Allow' -or
            [int]$rule.FileSystemRights -ne 2032127 -or [int]$rule.InheritanceFlags -ne 3 -or
            [int]$rule.PropagationFlags -ne 0 -or $rule.IdentityReference.Value -notin $control.allowed_sids) {
            throw 'acl_rule'
        }
    }
    foreach ($name in @('solicitud.md','respuesta.schema.json','pagina.png')) {
        $path = Join-Path $Folder $name
        $info = Get-Item -LiteralPath $path
        if ($info.Length -gt 33554432 -or ($info.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'package_invalid'
        }
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $control.package_sha256.$name) {
            throw 'package_hash'
        }
    }
    $binary = Join-Path $Folder 'agy.exe'
    if ((Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash.ToLowerInvariant() -cne $control.binary_sha256) {
        throw 'binary_hash'
    }
    # Prompt constante: nunca texto del documento ni secretos en la linea de comandos.
    $prompt = 'Lee solo solicitud.md, respuesta.schema.json y la imagen pagina.png de esta carpeta. Cumple el encargo de conversion visual y devuelve el JSON. No ejecutes comandos ni modifiques archivos.'
    $arguments = @('--model', $control.model, '--output-format', 'json', '--mode', 'plan',
        '--disable-slash-commands', '--json-schema', 'respuesta.schema.json',
        '--print-timeout', ($control.timeout.ToString() + 's'), '-p', $prompt)
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = $binary
    $start.Arguments = ($arguments | ForEach-Object { Arg-Win $_ }) -join ' '
    $start.WorkingDirectory = $Folder
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.RedirectStandardInput = $true
    $start.EnvironmentVariables.Clear()
    foreach ($key in @('SystemRoot','WINDIR','ComSpec','PATH','SystemDrive','TEMP','TMP','USERPROFILE',
            'APPDATA','LOCALAPPDATA','USERNAME','USERDOMAIN','HOMEDRIVE','HOMEPATH','PROGRAMDATA',
            'PROGRAMFILES','PROGRAMFILES(X86)','COMMONPROGRAMFILES','COMMONPROGRAMFILES(X86)')) {
        $value = [Environment]::GetEnvironmentVariable($key)
        if ($null -ne $value) { $start.EnvironmentVariables[$key] = $value }
    }
    $outPath = Join-Path $Folder 'stdout.json'
    $errPath = Join-Path $Folder 'stderr.txt'
    $outFile = New-Object IO.FileStream($outPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    $errFile = New-Object IO.FileStream($errPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    $process = [Diagnostics.Process]::Start($start)
    $process.StandardInput.Close()
    $outBuffer = New-Object byte[] 8192
    $errBuffer = New-Object byte[] 8192
    $outTask = $process.StandardOutput.BaseStream.ReadAsync($outBuffer, 0, $outBuffer.Length)
    $errTask = $process.StandardError.BaseStream.ReadAsync($errBuffer, 0, $errBuffer.Length)
    $deadline = [DateTime]::UtcNow.AddSeconds($control.timeout)
    $outDone = $false
    $errDone = $false
    while (-not ($outDone -and $errDone)) {
        if ([DateTime]::UtcNow -gt $deadline) { $state='timeout'; throw 'timeout' }
        if (-not $outDone -and $outTask.IsCompleted) {
            $size = $outTask.GetAwaiter().GetResult()
            if ($size -eq 0) { $outDone=$true }
            else {
                if ($outFile.Length + $size -gt 4000000) { $state='output_limit'; throw 'output_limit' }
                $outFile.Write($outBuffer,0,$size)
                $outTask = $process.StandardOutput.BaseStream.ReadAsync($outBuffer,0,$outBuffer.Length)
            }
        }
        if (-not $errDone -and $errTask.IsCompleted) {
            $size = $errTask.GetAwaiter().GetResult()
            if ($size -eq 0) { $errDone=$true }
            else {
                if ($errFile.Length + $size -gt 1000000) { $state='output_limit'; throw 'output_limit' }
                $errFile.Write($errBuffer,0,$size)
                $errTask = $process.StandardError.BaseStream.ReadAsync($errBuffer,0,$errBuffer.Length)
            }
        }
        Start-Sleep -Milliseconds 10
    }
    if (-not $process.WaitForExit(5000)) { $state='timeout'; throw 'timeout' }
    $exitCode = $process.ExitCode
    $outFile.Dispose(); $outFile=$null
    $errFile.Dispose(); $errFile=$null
    $outHash=(Get-FileHash -LiteralPath $outPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $state='finished'
} catch {
    # No texto de errores del proveedor ni datos del usuario en el recibo.
    try { Stop-OwnProcess } catch { $state='termination_unknown' }
} finally {
    if ($null -ne $outFile) { $outFile.Dispose() }
    if ($null -ne $errFile) { $errFile.Dispose() }
    if ($nonce -match '^[a-f0-9]{32}$' -and $nonce -ceq (Split-Path $Folder -Leaf)) {
        $receipt=@{nonce=$nonce;sid=$sid;state=$state;exit_code=$exitCode;stdout_sha256=$outHash} | ConvertTo-Json -Compress
        $temporary=Join-Path $Folder 'fin.tmp'
        $final=Join-Path $Folder 'fin.json'
        $file=New-Object IO.FileStream($temporary,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
        try { $bytes=$utf8.GetBytes($receipt); $file.Write($bytes,0,$bytes.Length); $file.Flush($true) }
        finally { $file.Dispose() }
        [IO.File]::Move($temporary,$final)
    }
}
