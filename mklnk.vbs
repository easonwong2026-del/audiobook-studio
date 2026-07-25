Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
venvPath = fso.GetAbsolutePathName(fso.BuildPath(scriptDir, "..\index-tts\.venv\Scripts\python.exe"))
launcherPath = fso.GetAbsolutePathName(fso.BuildPath(scriptDir, "launcher.py"))
iconPath = fso.GetAbsolutePathName(fso.BuildPath(scriptDir, "icon.ico"))
Set WShell = CreateObject("WScript.Shell")
Set SC = WShell.CreateShortcut(WShell.ExpandEnvironmentStrings("%USERPROFILE%") & "\Desktop\有声书合成工作台.lnk")
SC.TargetPath = venvPath
SC.Arguments = launcherPath
SC.WorkingDirectory = fso.GetAbsolutePathName(scriptDir)
SC.IconLocation = iconPath
SC.Save
WScript.Echo "Done"
