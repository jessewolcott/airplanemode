# 0019. The homepage goes on GitHub Pages under its own domain, airplanemode.xin

Date: 2026-11-22 · Status: accepted (supersedes 0012)

## Context

Decision 0012 turned GitHub Pages off for `airplanemode-site`: without a domain of its own,
the project site was served under the account's Pages site (its custom domain, at
`/airplanemode-site`), and the github.io address redirected there over plain http. The plan
was a server of its own, which was never set up.

The problem was the shared address, not Pages itself. A project site with its own custom
domain is served at the root of that domain, separately from the account's site.

## Decision

- `airplanemode-site` is published with GitHub Pages at **`https://airplanemode.xin`**
  (Porkbun DNS: the four GitHub A/AAAA records on the apex, `www` CNAME to
  `jessewolcott.github.io`; the domain is verified on the GitHub account so no other repo can
  claim it).
- Built by GitHub Actions, not from the branch: the `pages` job in `check.yml` runs only on
  pushes to `main`, only after the installer checks pass, and publishes only `index.html`,
  `install.sh`, `assets/` and `CNAME` (not `tests/`, `llm/` or the notes).
- **Enforce HTTPS** is on. The install command is `curl ... | sh` as root on a router, so the
  page still uses its own address for the command only over https.
- The install URL becomes `https://airplanemode.xin/install.sh`. The raw GitHub URL keeps
  working (same file on `main`) for anyone who copied the old command.

## Consequences

- A push to `main` that passes CI updates the site and the installer within a minute or two.
  Treat `main` as production (it already was, through the raw URL).
- OpenWrt's built-in wget can't do TLS with GitHub's servers (decision 0011), and Pages runs
  on them; the page's command installs curl first, as before.
- Static files only. The apps stay off the internet.
- If Pages is ever turned off again, remove the DNS records too, or the domain could be
  claimed by another repo (the account-level verification guards against this meanwhile).
