Set WShell = CreateObject("WScript.Shell")
Set SC = WShell.CreateShortcut(WShell.ExpandEnvironmentStrings("%USERPROFILE%") & "\Desktop\有声书合成工作台.lnk")
SC.TargetPath = "C:\Users\rakliang\WorkBuddy\2026-06-28-19-01-02\index-tts\.venv\Scripts\python.exe"
SC.Arguments = "C:\Users\rakliang\WorkBuddy\2026-06-29-18-28-53\audiobook-studio\launcher.py"
SC.WorkingDirectory = "C:\Users\rakliang\WorkBuddy\2026-06-29-18-28-53\audiobook-studio"
SC.IconLocation = "C:\Users\rakliang\WorkBuddy\2026-06-29-18-28-53\audiobook-studio\icon.ico"
SC.Description = "有声书合成工作台"
SC.Save()
