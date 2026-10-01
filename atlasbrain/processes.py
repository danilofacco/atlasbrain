"""Spawn background Python processes without depending on a Unix session."""
import os
import subprocess


def background_options():
    if os.name == 'nt':
        return {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {'start_new_session': True}
