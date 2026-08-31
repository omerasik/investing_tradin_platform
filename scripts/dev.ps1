# PowerShell wrapper for scripts/dev.py
$python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
& $python "scripts/dev.py" @args
