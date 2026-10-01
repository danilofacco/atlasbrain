import base64
import subprocess
import sys
from types import SimpleNamespace

import pytest
from atlasbrain import folder_picker


def result(stdout='', stderr='', returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_windows_selection_preserves_native_unicode_path(monkeypatch):
    calls = []
    monkeypatch.setattr(folder_picker.sys, 'platform', 'win32')
    monkeypatch.setattr(folder_picker.subprocess, 'CREATE_NO_WINDOW', 0x08000000, raising=False)
    monkeypatch.setenv('SystemRoot', 'C:/Windows')
    def run(command, **options):
        calls.append((command, options))
        return result("C:\\Projects\\Pasta com acentuação é\\\r\n")
    monkeypatch.setattr(folder_picker.subprocess, 'run', run)
    assert folder_picker.choose_folder() == 'C:\\Projects\\Pasta com acentuação é\\'
    command, options = calls[0]
    assert command[0].replace('\\', '/').endswith('/System32/WindowsPowerShell/v1.0/powershell.exe')
    assert '-STA' in command and '-NoProfile' in command
    script = base64.b64decode(command[-1]).decode('utf-16-le')
    assert 'FolderBrowserDialog' in script and 'ShowDialog()' in script
    assert 'Projects' not in script  # the selected path is output, never shell source
    assert options['encoding'] == 'utf-8-sig'
    assert options['creationflags'] == 0x08000000


@pytest.mark.parametrize('output,expected', [('/Users/test/Notes/\n', '/Users/test/Notes'), ('/\n', '/')])
def test_macos_selection_and_root(monkeypatch, output, expected):
    monkeypatch.setattr(folder_picker.sys, 'platform', 'darwin')
    def run(command, **options):
        assert command[0] == '/usr/bin/osascript'
        assert 'creationflags' not in options
        return result(output)
    monkeypatch.setattr(folder_picker.subprocess, 'run', run)
    assert folder_picker.choose_folder() == expected


@pytest.mark.parametrize('platform,output', [('win32', result()), ('darwin', result(stderr='User canceled. (-128)', returncode=1))])
def test_cancel_is_not_an_error(monkeypatch, platform, output):
    monkeypatch.setattr(folder_picker.sys, 'platform', platform)
    monkeypatch.setattr(folder_picker.subprocess, 'CREATE_NO_WINDOW', 0x08000000, raising=False)
    monkeypatch.setattr(folder_picker.subprocess, 'run', lambda *args, **kwargs: output)
    assert folder_picker.choose_folder() is None


@pytest.mark.parametrize('failure', [result(stderr='Unexpected dialog error', returncode=1), FileNotFoundError(), subprocess.TimeoutExpired('picker', 600)])
def test_dialog_failures_do_not_look_like_cancellation(monkeypatch, failure):
    monkeypatch.setattr(folder_picker.sys, 'platform', 'darwin')
    def run(*args, **kwargs):
        if isinstance(failure, Exception):
            raise failure
        return failure
    monkeypatch.setattr(folder_picker.subprocess, 'run', run)
    with pytest.raises(RuntimeError, match='seletor de pastas'):
        folder_picker.choose_folder()


def test_unsupported_system_keeps_manual_path_available(monkeypatch):
    monkeypatch.setattr(folder_picker.sys, 'platform', 'linux')
    with pytest.raises(RuntimeError, match='Digite o caminho'):
        folder_picker.choose_folder()


@pytest.mark.skipif(sys.platform != 'win32', reason='native PowerShell and Windows Forms')
def test_native_windows_picker_script_without_modal_interaction(tmp_path, monkeypatch):
    folder = tmp_path / 'Pasta com acentuação é'
    folder.mkdir()
    monkeypatch.setenv('ATLASBRAIN_TEST_FOLDER', str(folder))
    encoded = base64.b64encode(folder_picker.WINDOWS_PICKER.encode('utf-16-le')).decode('ascii')
    validation = '''
$source = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('%s'))
$tokens = $null
$errors = $null
[System.Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors) | Out-Null
if ($errors.Count) { throw ($errors | Out-String) }
''' % encoded
    # Construct the real native dialog and test UTF-8 output; CI cannot click
    # a modal dialog, so replace only its user interaction with a selection.
    script = folder_picker.WINDOWS_PICKER.replace(
        '$dialog.ShowDialog()', '[System.Windows.Forms.DialogResult]::OK').replace(
        'try {', '$dialog.SelectedPath = $env:ATLASBRAIN_TEST_FOLDER\ntry {', 1)
    monkeypatch.setattr(folder_picker, 'WINDOWS_PICKER', validation + script)
    assert folder_picker.choose_folder() == str(folder)
