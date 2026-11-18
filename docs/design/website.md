# Design: a public website

Status: designed, not built.

A network that offers services to the public needs a front page: what it is, which services
there are and how to join, its terms. The model is a small community host's site: a few plain
pages, the services as cards, a way to ask for an account. [Services for the
public](public-services.md#the-front-page-for-strangers) says what the front page shows; this page
says how kiwi-server makes it, with the operator's own text, without a CMS on a machine. The first
one is dlhost.eu's (owner, 18 November 2026).

## Content in a repository

The site's text lives in a git repository of its own, pinned like a module source: kiwi-server
fetches it at a tag or commit, never at a moving branch.

```text
site.yaml            name, tagline, colour, languages, the pages in their order
pages/home.md        one Markdown file per page
pages/join.md
pages/terms.md
pages/privacy.md
assets/              images and the logo
```

The repository is a template: another network starts its site from a copy. Nothing in it is
secret, and nothing in it is generated.

## What kiwi-server adds

- **Services**: the cards come from the public registry (`/api/registry/public`): name, what it
  is for, its address, whether it works, its Android app. A service the network does not offer
  to the public does not appear, whatever a page says.
- **Join**: the sign-up page's address (`id.<domain>` or the network's choice) and its rules.
- **Tor**: the hidden services' addresses, from the seed.
- **Status**: a link to the status page.

The control node serves the site the way it serves the public status page today, whose root is
already a small front page (`/start/public`): the master's reverse proxy hands the site's name
(`www.<domain>` and the bare domain) to the control node, which renders the pinned pages with
the look of the status page, the site's name and colour, and the registry's live data. No new
container, no PHP, no database, no editor on a machine: a change is a commit, a new pin and an
apply. The pages are Markdown in a small, safe subset (headings, paragraphs, lists, links,
emphasis, code), rendered by kiwi-server itself: no raw HTML from the repository reaches a page.

## Apart from the control part

The control part holds the network: the fleet, the secret store, the keys to every machine. A page
strangers can reach does not run there (built in 2.211.0). A second container of the control
module, `kiwi-server serve-public`, serves the website, the status page for the Internet and the
public registry. It has no fleet and no key, its root is read-only, and what it shows comes from a
folder the control part writes after each look: the services open to the Internet with their days
and incidents, the network's domain, the CA's certificate. The website's repository it fetches
itself. A fault in a public page finds nothing of the network behind it.

## Static files through a CDN

Built in 2.207.0 (`website_cdn_hostname`).

Optional: the CSS, images and fonts from another name (for dlhost.eu: `cdn.dlhost.eu`, a
Cloudflare-proxied name), with long cache headers. Never the pages, the sign-up or anything over
Tor. The site says that the CDN sees the address of whoever loads those files.

## Order

1. Settings of the `control` module: the site's name, the repository and its pinned commit;
   the master hands the name and `www.` on to the control node.
2. The control node fetches the pin and renders `site.yaml` and `pages/*.md` with the registry's
   services at that name; `/start/public` stays the front page where there is no site.
3. The template repository with its own docs, release by tag.
4. The CDN setting.
