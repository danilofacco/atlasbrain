"""Choose a local folder with the operating system's native dialog."""
import base64
import os
import subprocess
import sys
from pathlib import Path
from .processes import hidden_options

WINDOWS_PICKER = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::EnableVisualStyles()
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Escolha a pasta que vai virar um cérebro AtlasBrain'
$dialog.ShowNewFolderButton = $false
try {
    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        [Console]::WriteLine($dialog.SelectedPath)
    }
} finally { $dialog.Dispose() }
'''


def choose_folder():
    """Return a native path, or None when the user cancels the dialog."""
    if sys.platform == 'darwin':
        command = ['/usr/bin/osascript', '-e',
                   'POSIX path of (choose folder with prompt "Escolha a pasta que vai virar um cérebro AtlasBrain")']
        options = {}
    elif sys.platform == 'win32':
        powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        # A fixed encoded script preserves Portuguese text in Windows PowerShell
        # 5.1, without depending on console encoding or interpolating user input.
        script = base64.b64encode(WINDOWS_PICKER.encode('utf-16-le')).decode('ascii')
        command = [str(powershell), '-NoProfile', '-STA', '-EncodedCommand', script]
        options = hidden_options()
    else:
        raise RuntimeError('O seletor nativo não está disponível neste sistema. Digite o caminho da pasta.')
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8-sig',
                                timeout=600, **options)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError('Não consegui abrir o seletor de pastas. Digite o caminho da pasta.') from exc
    if result.returncode:
        if sys.platform == 'darwin' and '-128' in result.stderr:
            return None
        raise RuntimeError('Não consegui abrir o seletor de pastas. Digite o caminho da pasta.')
    folder = result.stdout.rstrip('\r\n')
    if sys.platform == 'darwin':
        folder = folder.rstrip('/') or ('/' if folder else '')
    return folder or None
