"""A machine's story (docs/design/ui.md, step 1): what was done to it last, beside what the last
look saw. The owner, 19 Nov 2026: a machine installed anew from fresh media waited at boot for
its disk's key, and every place only said "unreachable".

Evidence, all of it in the fleet's folder: when its install media were made (the newest file in
its media folder), and when its role was last applied (the fleet's state). Media newer than the
last apply mean: it is being installed from them, or was and has not been set up since.
"""
import glob
import os
import time

MEDIA = ("*.iso", "*.img")


def media_time(media_dir, name):
    """When this machine's newest install media were made, or None."""
    times = [os.path.getmtime(p) for pat in MEDIA for p in glob.glob(os.path.join(media_dir, name, pat))]
    return int(max(times)) if times else None


def _ago(seconds):
    from . import util
    s = max(0, int(seconds))
    return (util.count(max(1, s // 60), "minute") if s < 5400 else util.count(s // 3600, "hour") if s < 172800
            else util.count(s // 86400, "day"))


def story(name, applied_at, media_at, answers, waits_at=None, encrypted=False, now=None):
    """{stage, text} for a machine whose media are newer than its last apply, else None.
    `answers`: the look reached its system. `waits_at`: where its boot system answers."""
    now = now or time.time()
    if not media_at or (applied_at and applied_at >= media_at):
        return None
    made = "Its install media were made %s ago" % _ago(now - media_at)
    again = " (it was set up before: this is a reinstall)" if applied_at else ""
    if answers:
        return {"stage": "installed", "text": "%s%s. It runs from them and is not set up yet: Apply it." % (made, again)}
    if waits_at:
        return {"stage": "waits", "text": "%s%s. Its first start waits at %s for its disk's key." % (made, again, waits_at)}
    wait = ("it is being installed, or it waits at boot for its disk's key" if encrypted
            else "it is being installed, or has not started from them yet")
    return {"stage": "installing", "text": "%s%s and it has not answered since: %s." % (made, again, wait)}
