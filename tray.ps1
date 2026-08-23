Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class TrayNativeMethods {
    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    public static extern bool DestroyIcon(IntPtr handle);
}
'@

$menu = New-Object System.Windows.Forms.ContextMenuStrip
$openItem = $menu.Items.Add('Open Blender Render Queue')
$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null
$exitItem = $menu.Items.Add('Exit')

$icon = New-Object System.Windows.Forms.NotifyIcon
$iconPath = Join-Path $PSScriptRoot 'static\icon.png'
if (Test-Path -LiteralPath $iconPath) {
    $sourceBitmap = [System.Drawing.Bitmap]::FromFile($iconPath)
    $trayBitmap = New-Object System.Drawing.Bitmap($sourceBitmap, 32, 32)
    $iconHandle = $trayBitmap.GetHicon()
    $icon.Icon = ([System.Drawing.Icon]::FromHandle($iconHandle)).Clone()
    [TrayNativeMethods]::DestroyIcon($iconHandle) | Out-Null
    $trayBitmap.Dispose()
    $sourceBitmap.Dispose()
} else {
    $icon.Icon = [System.Drawing.SystemIcons]::Application
}
$icon.Text = 'Blender Render Queue'
$icon.ContextMenuStrip = $menu
$icon.Visible = $true

$openAction = {
    Start-Process 'http://127.0.0.1:5050/'
}
$openItem.add_Click($openAction)
$icon.add_DoubleClick($openAction)

$exitItem.add_Click({
    try {
        Invoke-WebRequest -UseBasicParsing -Method Post -ContentType 'application/json' -Body '{}' 'http://127.0.0.1:5050/api/exit' -TimeoutSec 2 | Out-Null
    } catch {}
    $icon.Visible = $false
    $icon.Dispose()
    [System.Windows.Forms.Application]::Exit()
})

$icon.ShowBalloonTip(1800, 'Blender Render Queue', 'Application is running locally.', [System.Windows.Forms.ToolTipIcon]::Info)
[System.Windows.Forms.Application]::Run()
