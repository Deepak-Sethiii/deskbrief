Attribute VB_Name = "Refresh"
'==============================================================================
' DeskBrief - Refresh.bas
'
' NOTE FOR THE REPO OWNER: you said you already had a vba/Refresh.bas. No such
' file existed anywhere on this machine when this stage was built, so this
' module was written from scratch to unblock the pipeline. If you still have
' your original, replace this file with it -- the only contract src/report and
' run_refresh.bat depend on is a public Sub named RefreshDeskBrief.
'
' WHY THIS MACRO EXISTS AT ALL
' Excel holds an exclusive write lock on an open workbook, so the Python side
' cannot overwrite the file you are looking at. Instead it writes a NEW
' timestamped workbook into output\ and records its absolute path in
' output\latest.txt. This macro runs the pipeline, then swaps you over to that
' new file and closes the stale one. Do not try to make Python write in place.
'==============================================================================
Option Explicit

Private Const LATEST_REL As String = "\output\latest.txt"
Private Const RUNNER_REL As String = "\run_refresh.bat"


'------------------------------------------------------------------
' Entry point. Assign this to the button/shape on the Summary sheet.
'------------------------------------------------------------------
Public Sub RefreshDeskBrief()
    Dim root As String
    Dim exitCode As Long
    Dim newPath As String

    On Error GoTo Failed

    root = RepoRoot()
    If Len(root) = 0 Then
        MsgBox "Could not locate run_refresh.bat by walking up from:" & vbCrLf & _
               ThisWorkbook.Path & vbCrLf & vbCrLf & _
               "Open this workbook from inside the DeskBrief repo.", _
               vbExclamation, "DeskBrief"
        Exit Sub
    End If

    Application.StatusBar = "DeskBrief: running pipeline, this takes a minute..."
    Application.Cursor = xlWait

    exitCode = RunPipeline(root)

    Application.Cursor = xlDefault
    Application.StatusBar = False

    If exitCode <> 0 Then
        MsgBox "The refresh failed with exit code " & exitCode & "." & vbCrLf & vbCrLf & _
               "See " & root & "\logs\deskbrief.log for the traceback.", _
               vbCritical, "DeskBrief"
        Exit Sub
    End If

    newPath = ReadFirstLine(root & LATEST_REL)
    If Len(newPath) = 0 Then
        MsgBox "The pipeline reported success but wrote no path to" & vbCrLf & _
               root & LATEST_REL, vbExclamation, "DeskBrief"
        Exit Sub
    End If

    If Dir$(newPath) = "" Then
        MsgBox "output\latest.txt points at a file that does not exist:" & vbCrLf & _
               newPath, vbExclamation, "DeskBrief"
        Exit Sub
    End If

    ' Already looking at the freshest file (can happen on a re-click).
    If StrComp(newPath, ThisWorkbook.FullName, vbTextCompare) = 0 Then
        MsgBox "Already showing the latest workbook.", vbInformation, "DeskBrief"
        Exit Sub
    End If

    SwapTo newPath
    Exit Sub

Failed:
    Application.Cursor = xlDefault
    Application.StatusBar = False
    MsgBox "DeskBrief refresh error " & Err.Number & ": " & Err.Description, _
           vbCritical, "DeskBrief"
End Sub


'------------------------------------------------------------------
' Open the freshly written workbook, then close this stale one.
' Open-then-close (not close-then-open): if the open fails we still
' have something on screen rather than an empty Excel.
'------------------------------------------------------------------
Private Sub SwapTo(ByVal newPath As String)
    Dim keepOpen As Boolean

    Workbooks.Open Filename:=newPath
    keepOpen = ThisWorkbook.Path Like "*\templates"   ' never close the template

    If Not keepOpen Then
        Application.DisplayAlerts = False
        ThisWorkbook.Close SaveChanges:=False          ' last statement: this ends the macro
        Application.DisplayAlerts = True
    End If
End Sub


'------------------------------------------------------------------
' Run run_refresh.bat synchronously and return its errorlevel.
'------------------------------------------------------------------
Private Function RunPipeline(ByVal root As String) As Long
    Dim shell As Object
    Set shell = CreateObject("WScript.Shell")
    ' 1 = show the console window so a long run does not look like a hang.
    ' True = wait for it, so we only read latest.txt after the file is written.
    RunPipeline = shell.Run("""" & root & RUNNER_REL & """", 1, True)
End Function


'------------------------------------------------------------------
' Walk up from the workbook until we find run_refresh.bat. Works whether
' the workbook was opened from templates\ or from output\.
'------------------------------------------------------------------
Private Function RepoRoot() As String
    Dim path As String
    Dim cut As Long

    path = ThisWorkbook.Path
    Do While Len(path) > 3
        If Dir$(path & RUNNER_REL) <> "" Then
            RepoRoot = path
            Exit Function
        End If
        cut = InStrRev(path, "\")
        If cut <= 3 Then Exit Do
        path = Left$(path, cut - 1)
    Loop

    RepoRoot = ""
End Function


'------------------------------------------------------------------
' First non-empty line of a text file, trimmed. "" if unreadable.
'------------------------------------------------------------------
Private Function ReadFirstLine(ByVal filePath As String) As String
    Dim handle As Integer
    Dim line As String

    ReadFirstLine = ""
    If Dir$(filePath) = "" Then Exit Function

    handle = FreeFile
    Open filePath For Input As #handle
    Do While Not EOF(handle)
        Line Input #handle, line
        line = Trim$(line)
        If Len(line) > 0 Then
            ReadFirstLine = line
            Exit Do
        End If
    Loop
    Close #handle
End Function
