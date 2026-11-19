# Services for the public: accounts, sign-up, landing page, status

Status: proposal, 16 November 2026. Nothing of it is built yet except the look of the pages.

A network may open Jabber, Forgejo, Nextcloud, a pastebin and more to strangers, the way
Disroot, systemli, Riseup, tchncs.de, envs.net and nixnet do. Then it needs what they have:
a front page that lists the services, one account for all of them, sign-up rules, and a status
page. This page says what they do, what software exists for it, and what kiwi-server should
build on.

## What community hosters do

| | Accounts | Sign-up | Onion | Front page |
|---|---|---|---|---|
| Riseup | one account service | invite codes, not to be traded | every service | grouped service list, donate, status page |
| systemli | own user app (userli) | invite codes, three per user after a week | ten services, the user portal too | service grid, news, terms |
| tchncs.de | one account for most services | review with a reason, a small payment, or a tracked invite | none found | intro, status, donate, recommended services with activity, a "playground" |
| envs.net | Linux accounts | request form, answered by hand | none found | service table, code of conduct, terms |
| nixnet | one per service | open, captcha, one sign-up an hour, no email | the site | service list, donate |

What they share:

- **One account** for the services that have logins, not one per service.
- **Sign-up is never wide open** for the bigger ones: invites that record who invited whom,
  review by a person, or a small payment. Open sign-up only with a captcha and a rate limit.
- **Terms** forbid spam, malware, harassment and illegal content, and keep the right to close
  an account. Without an email address a lost password is lost: they say so.
- **Onion addresses** for most services, the account page included. No hoster found refuses
  sign-up over Tor.

## Accounts: what to build on

The apps and what they speak:

| App | Login with an account provider |
|---|---|
| Forgejo, Nextcloud, Immich, Paperless | OpenID Connect |
| Vaultwarden | OpenID Connect since 1.35 |
| ejabberd (Jabber) | LDAP, or an external script; no OpenID Connect |
| PrivateBin, Nostr relay | no accounts: nothing to do |

The candidates:

| | Weight | Sign-up rules | Speaks | Set up from files |
|---|---|---|---|---|
| Authentik | 2 GB with its database | invites, captcha, approval: the richest | OpenID Connect, LDAP | blueprints (YAML) |
| Kanidm | about 80 MB | self-service portal; no open sign-up found | OpenID Connect, LDAP (read only) | config file, users by command |
| Rauthy | about 50 MB | open sign-up, by email domain | OpenID Connect | config file, environment |
| Pocket ID | about 130 MB | passkeys only, sign-up tokens | OpenID Connect | environment |
| Keycloak | 1 GB and more | flows | OpenID Connect, LDAP | import files |

**Proposal: an `accounts` module, with Kanidm or Authentik behind it, chosen by a test.**
Kanidm is light enough for any node and covers Jabber through its LDAP. Its gap is sign-up
rules, which kiwi-server could add as its own small page: an invite code, or a request that
the people who run the network accept on the control page (as joining machines are accepted
today). Authentik has the rules built in but needs 2 GB and a database. It belongs on a node
with room, never on a small master.

The test, on the test network:

1. Kanidm and Authentik side by side on papaya.
2. Forgejo and Nextcloud log in with each.
3. ejabberd checks passwords over LDAP.
4. A stranger signs up with an invite code, over the Internet and over Tor.
5. Memory at rest and while logging in.

### The test (16 November 2026, papaya)

| | Kanidm 1.12.1 | Authentik 2026.8.5 |
|---|---|---|
| Memory at rest | 59 MB, one container | about 800 MB: server 414, worker 311, database 70 (and an LDAP container on top) |
| An account made by the network, a link to set its password, no email | yes (`person create`, `credential create-reset-token`) | yes (invitations API) |
| Sign-up flows by invite | ours to build: a small page | to be built from flow blueprints (none for invites came with it) |
| Jabber's password check over LDAP | yes, built in: the right password in, a wrong one out | needs its LDAP outpost container |

**Kanidm it is.** It runs on any node, Jabber can use it over LDAP, and the sign-up rules the
owner chose (an invite code, or a request accepted on the control page) are kiwi-server's own
small page either way: it makes the account in Kanidm and hands over the link to set a password.
The drill: `tests/drills/accounts-spike.sh`.

## Status: what to build on

kiwi-server already asks every service every ten minutes, from the network and the way a
stranger reaches it, and keeps 90 days of it. The status page and the messages come from that.

- **Gatus** (Go, Apache-2.0, configured by a YAML file) fits kiwi-server's way best, if a
  public status page with more than ours is wanted: maintenance windows, its own alerts.
- **Uptime Kuma** has the nicer status pages, but is configured by clicking only.

**Proposal: keep our own checks and page.** Adopting Gatus would ask every service twice. Look
at it again if people want maintenance notices or alerts that ours do not send.

## Names: on the VPN, on the Internet, over Tor

A service can have three doors: its name on the VPN (`git.medlar.home`), a public name the master
hands on (`git.example.org`), and a hidden service (`….onion`). An app knows one address of its
own (Forgejo's ROOT_URL, Nextcloud's trusted domains, Kanidm's origin, to which passkeys belong),
so the rule is:

1. **One name per service: the public one if it has one, else the VPN name.** Inside the network
   the public name already leads to the machine's mesh address (the network's own DNS), so people
   at home and on the VPN use the same name as strangers, and never pass the master's doors
   (no rate limit, no ban).
2. **The VPN name stays as an alias** that sends the browser on to the public name (301): old
   bookmarks and apps keep working.
3. **Tor is the second door.** Every app that can list more than one address of its own is told
   its onion address (as Paperless, PrivateBin and BTCPay are today); the pages show it. An app
   that knows one address only does not work over Tor: Kanidm takes one origin per installation,
   and its hidden service on quince was taken away on 19 November. Such an app gets a public
   instance of its own instead (below).
4. **The pages show every door of a service**: the control page's Services, the status page,
   the front page for strangers, each with the VPN name, the public name and the onion address.

## The front page for strangers

A page for each public name of the network, made from what kiwi-server knows:

- each service with one line on what it is for;
- its addresses: on the Internet, and as a hidden service;
- whether it works now (from the status page);
- how to get an account (from the `accounts` module);
- the terms;
- the operator's name, logo and colour (settings, so that other people who run kiwi-server
  make it theirs).

The Jabber sign-up page becomes part of it: it carries the terms, and the same rules as every
other account.

## Decided (owner, 16 November 2026)

1. **Sign-up: both.** An invite code from someone who has an account, or a request that the
   people who run the network accept on the control page.
2. **Jabber accounts stay separate**, and can be joined to the network's accounts where the
   operator wants it (ejabberd checks passwords over LDAP then).
3. **No email address** at sign-up for now: the network runs no mail server. A lost password
   is reset by the people who run the network (a reset link they hand over), and the sign-up
   page says so.

## Decided (owner, 19 November 2026): public and internal may be separate

- **Tor and the VPN can be separate worlds.** A service can run as two instances that share no
  accounts and no data: a **public** one (the Internet and/or Tor, open to strangers, as Jabber
  is) and an **internal** one (the VPN, closed to the public). `xmpp.separate` does this
  today; other modules follow the same shape.
- **Accounts:** the internal Kanidm stays at `id.<hostname>`. The public side gets its own
  accounts: a second Kanidm whose one origin is its public name or its onion address, or
  another identity server that takes more than one address. Which one: compared first
  (Keycloak, Authentik, Zitadel), then chosen.
- **First** where a public service needs accounts: git over Tor (Forgejo signs in through
  Kanidm, which does not work there). Services with accounts of their own (Jabber, BTCPay)
  need nothing.
- **Which identity server for the public side** (compared 19 November; Keycloak 26.8, authentik
  2026.8, Zitadel 4.20, Kanidm 1.12):

  | | One instance, several names (public + onion) | Sign-up without email | LDAP for Jabber | Size |
  |---|---|---|---|---|
  | Kanidm | no: one origin | our sign-up page does it (invite, approval, reset link) | built in | small, no database server |
  | authentik | yes: issuer and passkeys follow the request's name | invite links, inactive users, recovery links | an extra outpost container | 2 GB at least, plus Postgres |
  | Keycloak | yes (`hostname-strict false`) | invites by email only | none | 1.25 GB, plus a database |
  | Zitadel | partly (one domain, more only by API) | email required | none | 0.5 GB, plus Postgres and a login container |

  **Proposal:** a second Kanidm per public door (one for the public name, one for the onion),
  with our sign-up page in front, as the internal one has. Small enough for a board, nothing
  new to run, LDAP for a public Jabber built in; accounts on Tor and on the Internet are then
  separate, which is allowed. authentik if one account for both doors matters more than 2 GB.
  Passkeys belong to one name in every one of them: over a second name people use a password.
- **A VPN name for a public instance**, so people at home do not take the long way through the
  master or Tor: `<site>-public.<machine>.home`, on the mesh only. The public name itself keeps
  resolving to the master everywhere (decided 16 November). Only where it works: Jabber (an app
  connects there and still speaks to its public domain, with its real certificate) and apps
  that take several addresses (PrivateBin, Paperless, BTCPay). An app with one address
  (Forgejo, Kanidm) would only send the browser on to its public name or onion: none for it.

