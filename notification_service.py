import winsound

try:
    from winotify import Notification
except ImportError:
    Notification = None


class WindowsNotifier:
    """Optional Windows toast notifications with a sound-only fallback."""

    def __init__(self, app_id="MAF"):
        self.app_id = str(app_id)

    @property
    def available(self):
        return Notification is not None

    def notify(self, title, message):
        if Notification is not None:
            toast = Notification(app_id=self.app_id, title=str(title), msg=str(message), duration="short")
            toast.show()
            return True
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
        return False
