# Security Policy

## Reporting a vulnerability

Report privately through GitHub's
[Security Advisories](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
("Report a vulnerability" on the repository's **Security** tab). Please do not
open a public issue for a security problem.

Include what you did, what happened, and what you expected — a minimal
reproduction is the most useful thing you can send. You can expect an initial
reply within a few days. This is a small, single-maintainer project, so please
allow reasonable time before any public disclosure.

## Scope

The threat model is narrow, and it is worth stating what is *not* in it:

- **The bot never trades.** It holds no broker credentials and has no order
  path. Nothing here can move money.
- **It is advisory only.** A compromised or wrong read produces a bad *message*,
  not a bad *trade*. The blast radius of any bug is the chat it writes to.

The parts that do handle untrusted input, and where a finding is most valuable:

- **Feed parsing** — the RSS and calendar readers parse XML and JSON from third
  parties. Entity-expansion (`DOCTYPE`/`ENTITY`) is refused before parsing; a
  bypass is a real finding.
- **The single HTTP door** — every outbound request goes through
  `goldbot/net.py::open_url`, which refuses any scheme that is not `http`/
  `https`. A way to reach `file://` or another scheme through it is a real
  finding.
- **The chat allowlist** — the bot answers only `TELEGRAM_CHAT_ID`. A way to
  make it answer another chat (and so spend the owner's AI quota) is a finding.
- **Secret handling** — tokens come from the environment only. A path that
  writes a secret to disk, into the state file, or into a log is a finding.

## What the project already does

- `bandit` (static analysis) and `pip-audit` (dependency audit) run in CI on
  every change, and `gitleaks` scans for committed secrets.
- The runtime has **no third-party dependencies**, so the supply-chain surface
  is the standard library alone.
- The container runs as a non-root user with a read-only root filesystem and no
  shell or package manager.
- Every deliberate suppression in the source (`# nosec`, `# noqa`) carries a
  written reason next to it, so it can be reviewed rather than trusted.
