"""Platform options for console-free commands and detached workers."""
import subprocess
import sys


def hidden_options():
    """Pipes alone do not prevent Windows from creating a console for a child."""
    if sys.platform == 'win32':
        return {'creationflags': subprocess.CREATE_NO_WINDOW}
    return {}


def background_options():
    if sys.platform == 'win32':
        return {'creationflags': hidden_options()['creationflags'] | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {'start_new_session': True}
