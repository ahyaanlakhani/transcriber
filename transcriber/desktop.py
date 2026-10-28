"""Desktop notifications, so a reminder reaches you even when the browser is closed."""
import os
import subprocess
import sys
from xml.sax.saxutils import escape

# Windows PowerShell's own app id: Windows only shows toasts from apps it knows
APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($env:PIP_TOAST_XML)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:PIP_APP_ID).Show($toast)
"""
NO_WINDOW = 0x08000000


def toast_xml(title: str, body: str, url: str | None) -> str:
    launch = f" activationType='protocol' launch='{escape(url, {chr(39): '&apos;'})}'" if url else ""
    return (f"<toast{launch}><visual><binding template='ToastGeneric'><text>{escape(title)}</text>"
            f"<text>{escape(body)}</text></binding></visual></toast>")


def toast(title: str, body: str, url: str | None = None) -> bool:
    """Show a Windows notification (clicking it opens the app at `url`). False if it couldn't be shown."""
    if sys.platform != "win32":
        return False
    env = {**os.environ, "PIP_TOAST_XML": toast_xml(title, body, url), "PIP_APP_ID": APP_ID}
    try:
        done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                               "-Command", SCRIPT], env=env, capture_output=True, timeout=20, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0
