Option Explicit

Dim shell, fso, appDirectory, pythonPath, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
appDirectory = fso.GetParentFolderName(WScript.ScriptFullName)

pythonPath = FindPython(appDirectory)
If pythonPath = "" Then
    MsgBox "Python 3 is required to run BlenderCMD Render GUI, but it could not be found on this computer." & vbCrLf & vbCrLf & _
        "Install Python 3.9 or newer from python.org, select the 'Add python.exe to PATH' option during installation, and then start the application again.", _
        vbCritical, "BlenderCMD Render GUI - Python required"
    WScript.Quit 1
End If

command = Quote(pythonPath) & " " & Quote(appDirectory & "\app.py")
shell.CurrentDirectory = appDirectory
shell.Run command, 0, False

Function FindPython(baseDirectory)
    Dim candidates, candidate, resolved
    candidates = Array( _
        baseDirectory & "\.venv\Scripts\pythonw.exe", _
        baseDirectory & "\.venv\Scripts\python.exe", _
        "pyw.exe", _
        "pythonw.exe", _
        "py.exe", _
        "python.exe" _
    )

    For Each candidate In candidates
        If InStr(candidate, "\") > 0 Then
            If fso.FileExists(candidate) Then
                FindPython = candidate
                Exit Function
            End If
        Else
            resolved = FindOnPath(candidate)
            If resolved <> "" Then
                FindPython = resolved
                Exit Function
            End If
        End If
    Next

    FindPython = FindInstalledPython()
End Function

Function FindOnPath(fileName)
    Dim process, output
    On Error Resume Next
    Set process = shell.Exec(Quote(shell.ExpandEnvironmentStrings("%ComSpec%")) & " /d /c where.exe " & fileName)
    If Err.Number <> 0 Then
        Err.Clear
        FindOnPath = ""
        On Error GoTo 0
        Exit Function
    End If
    output = Trim(process.StdOut.ReadLine())
    If Err.Number = 0 And fso.FileExists(output) Then
        FindOnPath = output
    Else
        Err.Clear
        FindOnPath = ""
    End If
    On Error GoTo 0
End Function

Function FindInstalledPython()
    Dim roots, root, folder, child, executable
    roots = Array( _
        shell.ExpandEnvironmentStrings("%LocalAppData%\Programs\Python"), _
        shell.ExpandEnvironmentStrings("%ProgramFiles%"), _
        shell.ExpandEnvironmentStrings("%ProgramFiles(x86)%") _
    )

    For Each root In roots
        If fso.FolderExists(root) Then
            Set folder = fso.GetFolder(root)
            For Each child In folder.SubFolders
                If LCase(Left(child.Name, 6)) = "python" Then
                    executable = child.Path & "\pythonw.exe"
                    If fso.FileExists(executable) Then
                        FindInstalledPython = executable
                        Exit Function
                    End If
                    executable = child.Path & "\python.exe"
                    If fso.FileExists(executable) Then
                        FindInstalledPython = executable
                        Exit Function
                    End If
                End If
            Next
        End If
    Next
    FindInstalledPython = ""
End Function

Function Quote(value)
    Quote = Chr(34) & value & Chr(34)
End Function
