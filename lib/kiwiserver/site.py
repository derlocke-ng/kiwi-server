"""The network's website for strangers (docs/design/website.md): pages from a repository pinned
to a commit, rendered here with the network's own data. The control module's settings reach the
control part as its environment: KIWI_SITE_HOST, KIWI_SITE_REPO, KIWI_SITE_COMMIT.

    site.yaml       name, tagline, accent, pages: [home, join, terms, …] (their order in the menu)
    pages/NAME.md   one page each, its first "# " line its title
    assets/         images and the like, served as they are

The Markdown is a small subset (headings, paragraphs, lists, links, emphasis, code, rules) and
nothing in it reaches the page unescaped: a repository cannot put HTML or script on the site.
"""
import html
import os
import re

import yaml

from . import sources
from .util import KiwiError

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
ASSET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml", ".webp": "image/webp",
         ".ico": "image/x-icon", ".css": "text/css", ".woff2": "font/woff2", ".txt": "text/plain"}


def settings():
    """(host, repository, commit), empty strings when there is no website. KIWI_SITE_DIR, a folder,
    stands in for the repository: to look at a site while writing it (the commit is then "dir")."""
    host = os.environ.get("KIWI_SITE_HOST", "").strip().lower()
    folder = os.environ.get("KIWI_SITE_DIR", "").strip()
    if host and folder and os.path.isdir(folder):
        return host, folder, "dir"
    repo = os.environ.get("KIWI_SITE_REPO", "").strip()
    commit = os.environ.get("KIWI_SITE_COMMIT", "").strip().lower()
    if not (host and repo.startswith("https://") and re.fullmatch(r"[0-9a-f]{40}", commit)):
        return "", "", ""
    return host, repo, commit


def hosts(host):
    return {host, "www." + host} if host else set()


def cdn():
    """The name a content delivery network serves the website's files from (KIWI_SITE_CDN), or ""."""
    name = os.environ.get("KIWI_SITE_CDN", "").strip().lower()
    return name if re.fullmatch(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?", name) else ""


def assets_base(cdn_host=""):
    """Where the pages find the site's files: the CDN's name, else the site's own /assets/."""
    return "https://%s/assets/" % cdn_host if cdn_host else "/assets/"


def load(folder, assets="/assets/", version=""):
    """{name, tagline, accent, pages: [{slug, title, html}], dir}: the site as its repository says.
    `assets`: where its images are fetched from; `version` goes on their addresses (a pinned commit
    never changes, so a CDN may keep them for good)."""
    try:
        with open(os.path.join(folder, "site.yaml"), encoding="utf-8") as fh:
            spec = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as e:
        raise KiwiError("the website's site.yaml cannot be read: %s" % e)
    if not isinstance(spec, dict):
        raise KiwiError("the website's site.yaml is not a mapping")
    pages = []
    for slug in spec.get("pages") or ["home"]:
        slug = str(slug)
        if not SLUG.match(slug):
            continue
        path = os.path.join(folder, "pages", slug + ".md")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), slug.capitalize())
        pages.append({"slug": slug, "title": title, "html": markdown(text, assets, version)})
    accent = str(spec.get("accent") or "")
    return {"name": str(spec.get("name") or ""), "tagline": str(spec.get("tagline") or ""),
            "accent": accent if re.fullmatch(r"#[0-9a-fA-F]{6}", accent) else "", "pages": pages, "dir": folder}


def fetch(repo, commit, cache=None, assets="/assets/"):
    """The site at its pin, fetched once (a folder as it is, for KIWI_SITE_DIR)."""
    if commit == "dir":
        return load(repo, assets)
    return load(sources.checkout("website", repo, commit, cache), assets, commit[:10])


def asset(site, name):
    """(path, media type) of a file in assets/, or None: only plain names, only known types."""
    if not ASSET.match(name or "") or ".." in name:
        return None
    kind = TYPES.get(os.path.splitext(name)[1].lower())
    path = os.path.join(site["dir"], "assets", name)
    return (path, kind) if kind and os.path.isfile(path) else None


_INLINE = [
    (re.compile(r"`([^`]+)`"), lambda m: "<code>%s</code>" % m.group(1)),
    (re.compile(r"\*\*([^*]+)\*\*"), lambda m: "<strong>%s</strong>" % m.group(1)),
    (re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])"), lambda m: "<em>%s</em>" % m.group(1)),
]
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_IMAGE = re.compile(r"!\[([^\]]*)\]\(/?assets/([^)\s]+)\)")


def _href(url):
    url = html.unescape(url)
    if re.match(r"^(https?://|mailto:|xmpp:|/|#)", url) and not url.startswith("//"):
        return html.escape(url, quote=True)
    if re.fullmatch(r"[a-z0-9-]+", url):          # another page of the site
        return "/" + url
    return ""


def inline(text, assets="/assets/", version=""):
    out = html.escape(text, quote=False)
    links = []

    def image(m):
        # only the site's own files: an image from elsewhere would tell that server who reads the page
        name = html.unescape(m.group(2))
        if not ASSET.match(name) or ".." in name or not TYPES.get(os.path.splitext(name)[1].lower(), "").startswith("image/"):
            return m.group(1)
        links.append('<img src="%s%s%s" alt="%s" loading="lazy">' % (assets, html.escape(name, quote=True),
                                                                   "?v=" + version if version else "", m.group(1)))
        return "\x00%d\x00" % (len(links) - 1)
    out = _IMAGE.sub(image, out)

    def keep(m):
        href = _href(m.group(2))
        links.append('<a href="%s">%s</a>' % (href, m.group(1)) if href else m.group(1))
        return "\x00%d\x00" % (len(links) - 1)
    out = _LINK.sub(keep, out)
    for pat, rep in _INLINE:
        out = pat.sub(rep, out)
    return re.sub("\x00(\\d+)\x00", lambda m: links[int(m.group(1))], out)


def markdown(text, assets="/assets/", version=""):
    """The subset as HTML: # to ### headings, paragraphs, - and 1. lists, > quotes, --- rules,
    ``` blocks, and inline links, images of the site's own assets/, **strong**, *em*, `code`.
    Everything else is text."""
    out, para, items, kind, code = [], [], [], "", None

    def flush():
        nonlocal para, items, kind
        if para:
            out.append("<p>%s</p>" % inline(" ".join(para), assets, version))
            para = []
        if items:
            out.append("<%s>%s</%s>" % (kind, "".join("<li>%s</li>" % inline(i, assets, version) for i in items), kind))
            items, kind = [], ""
    for line in text.splitlines():
        if code is not None:
            if line.strip().startswith("```"):
                out.append("<pre><code>%s</code></pre>" % html.escape("\n".join(code), quote=False))
                code = None
            else:
                code.append(line)
            continue
        s = line.strip()
        if s.startswith("```"):
            flush()
            code = []
        elif not s:
            flush()
        elif re.match(r"^#{1,3} ", s):
            flush()
            level = len(s.split(" ", 1)[0])
            out.append("<h%d>%s</h%d>" % (level, inline(s[level + 1:].strip(), assets, version), level))
        elif re.fullmatch(r"-{3,}|\*{3,}", s):
            flush()
            out.append("<hr>")
        elif re.match(r"^[-*] ", s) or re.match(r"^\d+\. ", s):
            want = "ul" if s[0] in "-*" else "ol"
            if para or (kind and kind != want):
                flush()
            kind = want
            items.append(re.sub(r"^([-*]|\d+\.) ", "", s))
        elif s.startswith("> "):
            flush()
            out.append("<blockquote><p>%s</p></blockquote>" % inline(s[2:], assets, version))
        else:
            if items:
                flush()
            para.append(s)
    if code is not None:
        out.append("<pre><code>%s</code></pre>" % html.escape("\n".join(code), quote=False))
    flush()
    return "\n".join(out)
