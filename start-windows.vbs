Option Explicit

Dim shell, fso, appDirectory, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
appDirectory = fso.GetParentFolderName(WScript.ScriptFullName)
command = """C:\Windows\pyw.exe"" """ & appDirectory & "\app.py"""

shell.CurrentDirectory = appDirectory
shell.Run command, 0, False
