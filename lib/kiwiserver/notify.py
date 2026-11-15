"""Messages to the people who run the network: by mail, as a push message, or to a webhook.

`notify:` in the fleet's defaults says where they go:

    notify:
      mail: { to: [me@example.org], host: smtp.example.org, user: me, password: ..., from: kiwi@example.org }
      ntfy: { url: https://ntfy.sh/a-topic-only-you-know }
      webhook: { url: https://example.org/hook }       # an SMS gateway, a chat room: it gets JSON
      heartbeat: https://hc-ping.com/...               # asked after every look: who stops hearing it, tells you
      after: 10                                        # minutes something must be wrong before it is told

Nothing here needs more than Python's own library. A channel that fails says why and does not
stop the others.
"""
import base64
import email.message
import email.utils
import json
import smtplib
import ssl
import urllib.error
import urllib.request

from .util import KiwiError

TIMEOUT = 20


def channels(cfg):
    """The channels that are set, in the order they are tried."""
    cfg = cfg or {}
    out = []
    mail = cfg.get("mail") or {}
    if mail.get("to") and mail.get("host"):
        out.append("mail")
    if (cfg.get("ntfy") or {}).get("url"):
        out.append("ntfy")
    if (cfg.get("webhook") or {}).get("url"):
        out.append("webhook")
    return out


def _mail(c, title, text):
    to = [str(x) for x in (c["to"] if isinstance(c["to"], list) else [c["to"]]) if str(x).strip()]
    msg = email.message.EmailMessage()
    sender = str(c.get("from") or c.get("user") or "kiwi@localhost")
    msg["From"], msg["To"], msg["Subject"] = sender, ", ".join(to), title
    msg["Date"], msg["Message-ID"] = email.utils.formatdate(), email.utils.make_msgid(domain=sender.rsplit("@", 1)[-1])
    msg.set_content(text + "\n")
    how = str(c.get("tls") or "starttls")
    port = int(c.get("port") or {"tls": 465, "none": 25}.get(how, 587))
    ctx = ssl.create_default_context()
    if how == "tls":
        server = smtplib.SMTP_SSL(str(c["host"]), port, timeout=TIMEOUT, context=ctx)
    else:
        server = smtplib.SMTP(str(c["host"]), port, timeout=TIMEOUT)
    with server:
        if how == "starttls":
            server.starttls(context=ctx)
        if c.get("user"):
            server.login(str(c["user"]), str(c.get("password") or ""))
        server.send_message(msg)


def _post(url, data, headers):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        r.read(1000)


def _header(text):
    """A header's value: as it is when it is plain, else the way mail writes other letters."""
    try:
        text.encode("ascii")
        return text
    except UnicodeEncodeError:
        return "=?UTF-8?B?%s?=" % base64.b64encode(text.encode()).decode()


def _ntfy(c, title, text, level):
    headers = {"Title": _header(title), "Priority": {"bad": "high", "warn": "default"}.get(level, "low"),
               "Tags": {"bad": "rotating_light", "warn": "warning"}.get(level, "white_check_mark")}
    if c.get("token"):
        headers["Authorization"] = "Bearer %s" % c["token"]
    _post(str(c["url"]), text.encode(), headers)


def _webhook(c, title, text, level, network):
    body = {"title": title, "text": text, "level": level, "network": network}
    _post(str(c["url"]), json.dumps(body).encode(), {"Content-Type": "application/json"})


def send(cfg, title, text, level="bad", network=""):
    """The message on every channel that is set: [(channel, went out, why not)]."""
    out = []
    for name in channels(cfg):
        try:
            if name == "mail":
                _mail(cfg["mail"], title, text)
            elif name == "ntfy":
                _ntfy(cfg["ntfy"], title, text, level)
            else:
                _webhook(cfg["webhook"], title, text, level, network)
            out.append((name, True, ""))
        except (OSError, smtplib.SMTPException, urllib.error.URLError, ValueError, KeyError, KiwiError) as e:
            out.append((name, False, str(e)[:200] or type(e).__name__))
    return out


def heartbeat(url):
    """The look happened: said to a service outside that tells someone when it stops hearing this."""
    try:
        with urllib.request.urlopen(str(url), timeout=TIMEOUT) as r:
            r.read(100)
        return True
    except (OSError, urllib.error.URLError, ValueError):
        return False
