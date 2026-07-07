param(
    [switch]$Clean,
    [switch]$NoZip,
    [switch]$UpdateOnly
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot

if ($Clean) {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue "$ProjectRoot\build"
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue "$ProjectRoot\dist"
}

if (-not (Test-Path "$ProjectRoot\.env")) {
    Write-Warning "未找到 .env。打包仍会继续，但成品不会自带千问/OpenAI API Key。"
}

if (-not (Test-Path "$ProjectRoot\.venv-win")) {
    py -3.11 -m venv "$ProjectRoot\.venv-win"
}

$Python = "$ProjectRoot\.venv-win\Scripts\python.exe"

& $Python -m pip install --upgrade pip setuptools wheel
& $Python -m pip install -e ".[desktop]"
& $Python -m pip install "pyinstaller>=6.10"

& $Python -m PyInstaller "$ProjectRoot\packaging\liepin_agent_windows.spec" --noconfirm --clean

$AppDir = "$ProjectRoot\dist\LiepinRecruitingAgent"
New-Item -ItemType Directory -Force "$AppDir\data\recordings" | Out-Null
New-Item -ItemType Directory -Force "$AppDir\profiles\app" | Out-Null

if (Test-Path "$ProjectRoot\.env") {
    Copy-Item "$ProjectRoot\.env" "$AppDir\.env" -Force
}
if (Test-Path "$ProjectRoot\.env.example") {
    Copy-Item "$ProjectRoot\.env.example" "$AppDir\.env.example" -Force
}

@"
猎聘招聘智能体 Windows 便携版

启动方式：
1. 双击 LiepinRecruitingAgent.exe
2. 数据库会自动创建在 data\app.db
3. 猎聘账号登录态会保存在 profiles\app
4. .env 已随包复制，用于读取千问/OpenAI API Key
5. 首次运行如果提示未授权，请复制机器码给授权管理员，拿到 license.json 后放到本目录或 data 目录

注意：
- 请不要把包含 .env 的软件包发给无关人员。
- license.json 绑定具体电脑，不能直接复制给另一台电脑使用。
- 如需迁移已有账号登录态和数据库，可复制旧机器的 data 和 profiles 目录覆盖本目录。
"@ | Set-Content -Encoding UTF8 "$AppDir\使用说明.txt"

if (-not $NoZip -and -not $UpdateOnly) {
    $ZipPath = "$ProjectRoot\dist\LiepinRecruitingAgent-win64.zip"
    Remove-Item -Force -ErrorAction SilentlyContinue $ZipPath
    Compress-Archive -Path "$AppDir\*" -DestinationPath $ZipPath
    Write-Host "已生成压缩包：$ZipPath"
}

if (-not $NoZip) {
    $UpdateDir = "$ProjectRoot\dist\LiepinRecruitingAgent-update"
    $UpdateZipPath = "$ProjectRoot\dist\LiepinRecruitingAgent-update-win64.zip"
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $UpdateDir
    Remove-Item -Force -ErrorAction SilentlyContinue $UpdateZipPath
    New-Item -ItemType Directory -Force $UpdateDir | Out-Null

    Copy-Item "$AppDir\LiepinRecruitingAgent.exe" "$UpdateDir\LiepinRecruitingAgent.exe" -Force
    if (Test-Path "$AppDir\_internal") {
        Copy-Item "$AppDir\_internal" "$UpdateDir\_internal" -Recurse -Force
    }
    if (Test-Path "$ProjectRoot\.env.example") {
        Copy-Item "$ProjectRoot\.env.example" "$UpdateDir\.env.example" -Force
    }

    New-Item -ItemType Directory -Force "$UpdateDir\data\recordings" | Out-Null
    foreach ($StaticDataFile in @("liepin_search_schema.json", "liepin_search_schema_light.json")) {
        $SourcePath = "$ProjectRoot\data\$StaticDataFile"
        if (Test-Path $SourcePath) {
            Copy-Item $SourcePath "$UpdateDir\data\$StaticDataFile" -Force
        }
    }
    if (Test-Path "$ProjectRoot\data\recordings") {
        Copy-Item "$ProjectRoot\data\recordings\*.json" "$UpdateDir\data\recordings\" -Force -ErrorAction SilentlyContinue
    }

@"
猎聘招聘智能体 Windows 更新包

适用场景：
- 已经安装过旧版本，想升级程序但保留历史数据、账号登录态和授权。

更新方式：
1. 先关闭正在运行的 LiepinRecruitingAgent.exe。
2. 把本更新包解压到旧软件目录，也就是原来 LiepinRecruitingAgent.exe 所在目录。
3. Windows 提示是否覆盖同名文件时，选择“全部替换/全部覆盖”。
4. 重新双击 LiepinRecruitingAgent.exe。

本更新包不会包含、不会主动覆盖：
- data\app.db：候选人、岗位、任务、账号等业务数据
- profiles\app：猎聘登录态
- .env：API Key 配置
- license.json / data\license.json：授权文件
- logs：历史日志

注意：
- 不要先删除旧软件目录再解压更新包；删除旧目录会丢失历史数据。
- 如果要给一台全新电脑使用，请使用完整包 LiepinRecruitingAgent-win64.zip。
"@ | Set-Content -Encoding UTF8 "$UpdateDir\更新说明.txt"

    Compress-Archive -Path "$UpdateDir\*" -DestinationPath $UpdateZipPath
    Write-Host "已生成更新包：$UpdateZipPath"
}

Write-Host "Windows 应用目录：$AppDir"
