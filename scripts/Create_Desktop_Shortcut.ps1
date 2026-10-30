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
$Shortcut.TargetPath = $PythonW
$Shortcut.Arguments = '"' + $App + '"'
$Shortcut.WorkingDirectory = $Root
$Shortcut.Description = "Capture or import title screens and build Wii U Virtual Console artwork"
if (Test-Path $Icon) {
    $Shortcut.IconLocation = $Icon + ",0"
}
$Shortcut.Save()
Write-Host "Desktop shortcut created: $ShortcutPath"
