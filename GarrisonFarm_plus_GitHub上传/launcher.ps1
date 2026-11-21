[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding

function Write-FilteredSdkOutput($Reader) {
    $buffer = ''
    $passLine = $false
    $dropLine = $false
    while (($value = $Reader.Read()) -ne -1) {
        $char = [char]$value
        if ($dropLine) {
            if ($char -eq "`n") { $dropLine = $false; $buffer = ''; $passLine = $false }
            continue
        }
        if ($passLine) {
            [Console]::Write($char)
            if ($char -eq "`n") { $passLine = $false }
            continue
        }
        $buffer += $char
        if (($buffer -match '^x: -?\d') -or ($buffer -eq 'after convert, x_point:')) {
            $dropLine = $true
            $buffer = ''
            continue
        }
        $possible = 'x: '.StartsWith($buffer) -or 'x: -'.StartsWith($buffer) -or 'after convert, x_point:'.StartsWith($buffer)
        if (-not $possible) {
            [Console]::Write($buffer)
            $passLine = ($char -ne "`n")
            $buffer = ''
        }
    }
    if (-not $dropLine -and $buffer) { [Console]::Write($buffer) }
}

if ($env:GF_FILTER_TEST) {
    Write-FilteredSdkOutput ([System.IO.StringReader]::new("normal`nx: 790, y: 920, width: 1920`nafter convert, x_point: 160, y_point: 790`nERROR stays`nPrompt: "))
    exit 0
}

$ErrorActionPreference = 'Stop'
$connection = $null
if ($env:GF_LAUNCH_MODE -ne 'help') {
    try {
        $helper = [IO.File]::ReadAllText((Join-Path (Get-Location).Path 'connection.ps1'), [Text.Encoding]::UTF8)
        . ([scriptblock]::Create($helper))
        $connection = Get-GarrisonConnection -Root (Get-Location).Path -Reconfigure:($env:GF_LAUNCH_MODE -eq 'configure')
    } catch {
        Write-Host ('连接配置未完成：' + $_.Exception.Message) -ForegroundColor Red
        exit 1
    }
    if ($env:GF_LAUNCH_MODE -eq 'configure') { exit 0 }
}
# Expand directory paths inside quoted environment variables in cmd.exe. This
# handles spaces, &, %, parentheses and non-ASCII paths without shell injection.
$connectionArguments = '--maa "%GF_CONNECTION_MAA%" --adb "%GF_CONNECTION_ADB%" --address ' + $connection.address
$env:GF_FIXED_FIRST = '0'
$env:GF_FIXED_ROW = '1'
$env:GF_FIXED_COL = '1'
$env:GF_TOTAL_ATTEMPTS = ''
if ($env:GF_LAUNCH_MODE -eq 'help') {
    $arguments = '--help'
} elseif ($env:GF_LAUNCH_MODE -eq 'check') {
    # The original SDK probe reads the saved MAA connection. Bind it explicitly
    # to this launcher's instance as well, without changing the MAA settings.
    $env:GF_CHECK_ADB = $connection.adb_path
    $env:GF_CHECK_ADDRESS = $connection.address
    $arguments = '--mumu-check'
} else {
    Write-Host ''
    Write-Host '请选择刷取方式：'
    Write-Host '  1. 普通刷角标：达到所选角标要求后换角色（默认）'
    Write-Host '  2. 固定第一角色：忽略角标与通关次数，反复刷列表第一项'
    Write-Host '  3. 固定指定角色：按列表的行、列选择（每行 4 个）'
    do {
        $choice = Read-Host '请输入 1、2 或 3，直接回车选 1'
        if ($null -eq $choice) { throw '输入已关闭，取消启动。' }
        $choice = $choice.Trim()
        if ($choice -eq '') { $choice = '1' }
        if ($choice -notin @('1', '2', '3')) { Write-Host '请输入 1、2 或 3。' }
    } until ($choice -in @('1', '2', '3'))
    $env:GF_FIXED_FIRST = if ($choice -in @('2', '3')) { '1' } else { '0' }
    if ($choice -eq '3') {
        Write-Host '从整张角色列表顶部开始计数：行从上到下，列从左到右，均从 1 开始。'
        Write-Host '例如第 5 行、第 2 列是列表第 18 个角色；脚本会自动滚动到该位置。'
        $fixedRow = 0
        do {
            $rowText = Read-Host '固定第几行？请输入正整数'
            if ($null -eq $rowText) { throw '输入已关闭，取消启动。' }
            $valid = [int]::TryParse($rowText.Trim(), [ref]$fixedRow) -and $fixedRow -gt 0
            if (-not $valid) { Write-Host '行号必须是正整数。' }
        } until ($valid)
        $fixedCol = 0
        do {
            $colText = Read-Host '固定第几列？请输入 1～4'
            if ($null -eq $colText) { throw '输入已关闭，取消启动。' }
            $valid = [int]::TryParse($colText.Trim(), [ref]$fixedCol) -and $fixedCol -ge 1 -and $fixedCol -le 4
            if (-not $valid) { Write-Host '每行只有 4 列，请输入 1～4。' }
        } until ($valid)
        $env:GF_FIXED_ROW = [string]$fixedRow
        $env:GF_FIXED_COL = [string]$fixedCol
    }

    $attemptLimit = 0
    do {
        $answer = Read-Host '本次总共打几局？请输入正整数（胜负都算，回车默认 20）'
        if ($null -eq $answer) { throw '输入已关闭，取消启动。' }
        $answer = $answer.Trim()
        if ($answer -eq '') { $answer = '20' }
        $valid = [int]::TryParse($answer, [ref]$attemptLimit) -and $attemptLimit -gt 0
        if (-not $valid) { Write-Host '局数必须是 1 到 2147483647 之间的整数。' }
    } until ($valid)
    $env:GF_TOTAL_ATTEMPTS = [string]$attemptLimit
    $label = switch ($choice) {
        '2' { '固定第一角色' }
        '3' { "固定第 $fixedRow 行、第 $fixedCol 列角色" }
        default { '普通刷角标' }
    }
    Write-Host "本次设置：$label，共 $attemptLimit 局（胜负都算）。"
    Write-Host '接下来仍可选择原来的 1～4 模式。'
    if ($choice -in @('2', '3')) {
        Write-Host '固定模式下，1/2 使用标准编队，3/4 使用低配编队；金/彩角标不会触发换人。'
    }
    # Wins cannot exceed attempts. Equal limits preserve the original result
    # handling and guarantee that --runs cannot stop a finite session early.
    $arguments = "--runs $attemptLimit --attempts $attemptLimit --menu $connectionArguments"
}

$info = New-Object System.Diagnostics.ProcessStartInfo
$info.FileName = $env:ComSpec
$info.WorkingDirectory = (Get-Location).Path
$info.Arguments = $arguments
# Merge native stderr into the same filtered stream, preserving all non-coordinate lines.
$info.Arguments = '/d /v:off /s /c ' + [char]34 + 'GarrisonFarm.exe ' + $info.Arguments + ' 2>&1' + [char]34
$info.UseShellExecute = $false
if ($connection) {
    # Append \. so a directory ending in a backslash cannot escape the quote.
    $info.EnvironmentVariables['GF_CONNECTION_MAA'] = Join-Path $connection.maa_dir '.'
    $info.EnvironmentVariables['GF_CONNECTION_ADB'] = $connection.adb_path
}
$info.RedirectStandardOutput = $true
$info.StandardOutputEncoding = New-Object System.Text.UTF8Encoding($false)
# stdin and stderr remain attached to the console; prompts are streamed without waiting for newline.
$process = [System.Diagnostics.Process]::Start($info)
try {
    Write-FilteredSdkOutput $process.StandardOutput
    $process.WaitForExit()
    $result = $process.ExitCode
} finally {
    $process.Dispose()
}
exit $result
