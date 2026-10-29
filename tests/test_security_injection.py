import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from metamuse import facebook, media

def test_facebook_argument_injection():
    with patch("subprocess.Popen") as mock_popen:
        mock_process = MagicMock()
        mock_process.wait.return_value = 0
        mock_process.pid = 1234
        mock_popen.return_value = mock_process
        facebook._observed_sandbox_mode = lambda b: "enabled"

        # Test the real subprocess call location in facebook.py
        # which is inside _sandbox_launch_check
        facebook._sandbox_launch_check("'; ls -la; '", 0, 0)

        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        command = args[0]
        assert "'; ls -la; '" in command
        assert kwargs.get("shell") is not True

def test_media_ffprobe_injection():
    with patch("subprocess.Popen") as mock_popen:
        mock_process = MagicMock()
        mock_process.returncode = 0
        # Mock file-like objects for stdout/stderr
        mock_stdout = MagicMock()
        mock_stdout.fileno.return_value = 1
        mock_stdout.read.return_value = b"{}"

        mock_stderr = MagicMock()
        mock_stderr.fileno.return_value = 2
        mock_stderr.read.return_value = b""

        mock_process.stdout = mock_stdout
        mock_process.stderr = mock_stderr
        mock_process.poll.return_value = 0

        mock_popen.return_value = mock_process

        # Override _read_capped to avoid fileno issues with MagicMock in threads
        with patch("metamuse.media._read_capped"):
            media._run_ffprobe(media.ffprobe_command("ffprobe", Path("file.mp4'; echo bad; '")), timeout=5)

        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        command = args[0]
        assert "file.mp4'; echo bad; '" in command[-1]
        assert kwargs.get("shell") is False
