# Shared by the normal launcher and the read-only SDK check. UTF-8, PowerShell 5.1.
function Read-GFJson([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    return ([IO.File]::ReadAllText($Path, [Text.Encoding]::UTF8) | ConvertFrom-Json -ErrorAction Stop)
}

function Set-GFProperty($Object, [string]$Name, $Value) {
    $Object | Add-Member -MemberType NoteProperty -Name $Name -Value $Value -Force
}

function Write-GFJson([string]$Path, $Object) {
    $text = ($Object | ConvertTo-Json -Depth 20) + "`r`n"
    if ((Test-Path -LiteralPath $Path) -and [IO.File]::ReadAllText($Path, [Text.Encoding]::UTF8) -eq $text) { return }
    $temp = $Path + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
    try {
        [IO.File]::WriteAllText($temp, $text, [Text.UTF8Encoding]::new($false))
        if (Test-Path -LiteralPath $Path) {
            [IO.File]::Replace($temp, $Path, [NullString]::Value)
        } else {
            [IO.File]::Move($temp, $Path)
        }
    } finally {
        if (Test-Path -LiteralPath $temp) { [IO.File]::Delete($temp) }
    }
}

function Read-GFAnswer([string]$Prompt, [string]$Default = '') {
    $caption = if ($Default) { "$Prompt（回车使用：$Default）" } else { $Prompt }
    $answer = Read-Host $caption
    if ($null -eq $answer) { throw '输入已关闭，未完成配置。请重新运行启动文件。' }
    $answer = $answer.Trim()
    if (-not $answer) { return $Default }
    return $answer
}

function ConvertTo-GFDirectory([string]$Value) {
    $value = $Value.Trim().Trim('"')
    if (-not $value -or $value.IndexOfAny([char[]]@('"', "`r", "`n")) -ge 0) { return $null }
    try {
        if (-not [IO.Path]::IsPathRooted($value)) { return $null }
        return [IO.Path]::GetFullPath($value)
    } catch { return $null }
}

function Find-GFMuMuFiles([string]$Root) {
    foreach ($relative in @('nx_main', 'nx_device\15.0\shell', 'nx_device\12.0\shell', 'shell')) {
        $base = Join-Path $Root $relative
        $adb = Join-Path $base 'adb.exe'
        $sdk = Join-Path $base 'sdk\external_renderer_ipc.dll'
        if ((Test-Path -LiteralPath $adb -PathType Leaf) -and (Test-Path -LiteralPath $sdk -PathType Leaf)) {
            return [pscustomobject]@{ adb = $adb; sdk = $sdk }
        }
    }
    return $null
}

function Get-GFMuMuInstances([string]$Root) {
    $vms = Join-Path $Root 'vms'
    if (-not (Test-Path -LiteralPath $vms -PathType Container)) { return }
    foreach ($dir in (Get-ChildItem -LiteralPath $vms -Directory)) {
        if ($dir.Name -notmatch '^MuMuPlayer-\d+\.\d+-(\d+)$') { continue }
        $index = [int]$Matches[1]
        try {
            $vm = Read-GFJson (Join-Path $dir.FullName 'configs\vm_config.json')
            $port = 0
            if (-not [int]::TryParse([string]$vm.vm.nat.port_forward.adb.host_port, [ref]$port)) { continue }
            if ($port -lt 1 -or $port -gt 65535) { continue }
            $extra = Read-GFJson (Join-Path $dir.FullName 'configs\extra_config.json')
            $label = if ($extra.playerName) { [string]$extra.playerName } else { $dir.Name }
            [pscustomobject]@{ index = $index; port = $port; name = $label }
        } catch {
            # A damaged instance entry must not hide the other valid devices.
            continue
        }
    }
}

function Test-GFConnection($Connection) {
    if ($Connection.version -ne 1) { throw '连接配置版本不正确，请运行「重新配置连接.bat」。' }
    $mumu = ConvertTo-GFDirectory ([string]$Connection.mumu_path)
    $maa = ConvertTo-GFDirectory ([string]$Connection.maa_dir)
    if (-not $mumu -or -not (Find-GFMuMuFiles $mumu)) {
        throw '保存的 MuMu 路径已失效，请运行「重新配置连接.bat」。'
    }
    if (-not $maa -or -not (Test-Path -LiteralPath (Join-Path $maa 'MAA.exe') -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $maa 'resource\PaddleOCR') -PathType Container)) {
        throw '保存的 MAA 路径或 OCR 资源已失效，请运行「重新配置连接.bat」。'
    }
    $files = Find-GFMuMuFiles $mumu
    if ([string]$Connection.adb_path -ne $files.adb -or [string]$Connection.sdk_path -ne $files.sdk) {
        throw '保存的 ADB/SDK 路径与 MuMu 安装目录不匹配，请重新配置连接。'
    }
    if ([string]$Connection.address -notmatch '^127\.0\.0\.1:(\d+)$') { throw '保存的连接地址格式不正确，请重新配置连接。' }
    $port = 0
    if (-not [int]::TryParse($Matches[1], [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
        throw '保存的端口不正确，请重新配置连接。'
    }
    $index = -1
    if (-not [int]::TryParse([string]$Connection.instance_index, [ref]$index) -or $index -lt 0) {
        throw '保存的增强触控实例编号不正确，请重新配置连接。'
    }
    $instances = @(Get-GFMuMuInstances $mumu)
    $matchesByPort = @($instances | Where-Object { $_.port -eq $port })
    if ($matchesByPort.Count -gt 1 -or ($matchesByPort.Count -eq 1 -and $matchesByPort[0].index -ne $index)) {
        throw '连接端口和增强触控实例编号不一致，请重新配置连接。'
    }
    $matchesByIndex = @($instances | Where-Object { $_.index -eq $index })
    if ($matchesByIndex.Count -gt 0 -and $port -notin @($matchesByIndex | ForEach-Object { $_.port })) {
        throw '该实例的实际端口已变化，请重新配置连接。'
    }
}

function Sync-GFRuntimeConfig([string]$Root, $Connection) {
    $touchPath = Join-Path $Root 'mumu_touch.json'
    $touch = Read-GFJson $touchPath
    if (-not $touch) {
        $touch = [pscustomobject]@{ enabled = $true; fast_capture = $true; move_interval = 0.016; package_name = 'auto' }
    }
    Set-GFProperty $touch 'emulator_path' $Connection.mumu_path
    Set-GFProperty $touch 'instance_index' ([int]$Connection.instance_index)
    Set-GFProperty $touch 'sdk_path' $Connection.sdk_path
    $settingsPath = Join-Path $Root 'settings.json'
    $settings = Read-GFJson $settingsPath
    if (-not $settings) { $settings = [pscustomobject]@{} }
    Set-GFProperty $settings 'maa_dir' $Connection.maa_dir
    Write-GFJson $touchPath $touch
    Write-GFJson $settingsPath $settings
}

function Get-GarrisonConnection([string]$Root, [switch]$Reconfigure) {
    $path = Join-Path $Root 'connection.json'
    if ((Test-Path -LiteralPath $path -PathType Leaf) -and -not $Reconfigure) {
        try {
            $saved = Read-GFJson $path
            Test-GFConnection $saved
        } catch { throw ('读取已保存连接失败：' + $_.Exception.Message + ' 请运行「重新配置连接.bat」。') }
        Sync-GFRuntimeConfig $Root $saved
        Write-Host ('使用已保存连接：{0}，地址 {1}，增强触控实例 {2}' -f $saved.device_name, $saved.address, $saved.instance_index)
        return $saved
    }

    Write-Host ''
    Write-Host '========== 首次连接配置 =========='
    Write-Host '本步骤只需完成一次。以后启动自动读取本目录的 connection.json。'
    Write-Host '请填写这台电脑的实际路径；粘贴带双引号的路径也可以。'
    $oldTouch = Read-GFJson (Join-Path $Root 'mumu_touch.json')
    $oldSettings = Read-GFJson (Join-Path $Root 'settings.json')
    $mumuDefault = [string]$oldTouch.emulator_path
    do {
        $mumu = ConvertTo-GFDirectory (Read-GFAnswer 'MuMu 安装文件夹（包含 nx_main、vms）' $mumuDefault)
        $files = if ($mumu) { Find-GFMuMuFiles $mumu } else { $null }
        if (-not $files) { Write-Host '此目录中找不到 MuMu 的 adb.exe 和增强触控 SDK，请重新输入安装根目录。' }
    } until ($files)

    do {
        $maa = ConvertTo-GFDirectory (Read-GFAnswer 'MAA 文件夹（包含 MAA.exe）' ([string]$oldSettings.maa_dir))
        $maaValid = $maa -and (Test-Path -LiteralPath (Join-Path $maa 'MAA.exe') -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $maa 'resource\PaddleOCR') -PathType Container)
        if (-not $maaValid) { Write-Host '此目录中找不到 MAA.exe 或 resource\PaddleOCR，请输入完整的 MAA 程序目录。' }
    } until ($maaValid)

    Write-Host ''
    Write-Host '在 MuMu 界面查端口：'
    Write-Host '目标设备窗口右上角「⋯」→「设备设置」→左下角「问题诊断」'
    Write-Host '→向下找到「网络信息」→「ADB 调试端口」。'
    Write-Host '例如显示 5557,16416 时，只输入 16416，不要输入整串；也可输入 127.0.0.1:16416。'
    $instances = @(Get-GFMuMuInstances $mumu | Sort-Object index)
    if ($instances.Count) {
        Write-Host '从该 MuMu 安装目录读取到的设备（不代表当前在线）：'
        foreach ($item in $instances) {
            Write-Host ('  {0}：端口 {1}，增强触控实例 {2}' -f $item.name, $item.port, $item.index)
        }
    }
    $suggested = @($instances | Where-Object { $_.index -eq $oldTouch.instance_index })
    $portDefault = if ($suggested.Count -eq 1) { [string]$suggested[0].port } else { '' }
    while ($true) {
        $portText = Read-GFAnswer '目标设备的 ADB 端口' $portDefault
        $portText = $portText -replace '^127\.0\.0\.1:', ''
        $port = 0
        if (-not [int]::TryParse($portText, [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
            Write-Host '端口必须是 1～65535 的整数，请按提示输入一个端口。'
            continue
        }
        $matching = @($instances | Where-Object { $_.port -eq $port })
        if ($matching.Count -eq 1) {
            $index = $matching[0].index
            $deviceName = $matching[0].name
            break
        }
        if ($instances.Count) {
            Write-Host '此端口未唯一对应以上设备，请使用上面列出的目标设备端口，避免增强触控连接错设备。'
            continue
        }
        Write-Host '无法读取实例列表，需要手动填写编号。可在 MuMu 多开列表中查看设备的 #编号。'
        Write-Host '编号不是设备名称末尾的数字；请使用实例本身的编号。'
        do {
            $indexText = Read-GFAnswer '增强触控实例编号（从 0 开始的非负整数）'
            $index = -1
            $indexValid = [int]::TryParse($indexText, [ref]$index) -and $index -ge 0
            if (-not $indexValid) { Write-Host '请输入非负整数实例编号。' }
        } until ($indexValid)
        $deviceName = 'MuMu 实例 ' + $index
        break
    }
    $connection = [pscustomobject][ordered]@{
        version = 1
        mumu_path = $mumu
        maa_dir = $maa
        adb_path = $files.adb
        sdk_path = $files.sdk
        address = '127.0.0.1:' + $port
        instance_index = $index
        device_name = $deviceName
    }
    Test-GFConnection $connection
    # Write the completion marker last so interrupted setup asks again next time.
    Sync-GFRuntimeConfig $Root $connection
    Write-GFJson $path $connection
    Write-Host ('已保存：{0}，{1}，增强触控实例 {2}。以后启动不再询问路径和端口。' -f $deviceName, $connection.address, $index)
    Write-Host '如需改设备或换电脑，请运行「重新配置连接.bat」。'
    return $connection
}
