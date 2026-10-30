$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$PythonW = Join-Path $Root ".venv\Scripts\pythonw.exe"
$App = Join-Path $Root "app.pyw"
$Icon = Join-Path $Root "assets\vc_title_extractor.ico"

if (-not (Test-Path $PythonW)) {
    throw "The local Python environment has not been created yet. Run Setup_and_Run.bat first."
}

$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "Wii U VC Image Studio.lnk"
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
if (Test-Path $ShortcutPath) {
    if ($Shortcut.TargetPath -eq $PythonW -and $Shortcut.Arguments -eq ('"' + $App + '"')) {
        Write-Host "The existing Studio shortcut already points to this source installation."
        exit 0
    }
    throw "An existing Studio shortcut points elsewhere. Keep or rename it before creating this shortcut; it has not been changed."
}
$Shortcut.TargetPath = $PythonW
$Shortcut.Arguments = '"' + $App + '"'
$Shortcut.WorkingDirectory = $Root
$Shortcut.Description = "Capture or import title screens and build Wii U Virtual Console artwork"
if (Test-Path $Icon) {
    $Shortcut.IconLocation = $Icon + ",0"
}
$Shortcut.Save()
Write-Host "Desktop shortcut created: $ShortcutPath"
