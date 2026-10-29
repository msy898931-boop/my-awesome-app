## What this changes

<!-- One or two sentences. What was wrong, and what the change makes right. -->

## Why

<!-- Reference the failure being prevented, not just the change made. If it
     fixes an issue, link it: "Fixes #123". -->

## How it was checked

- [ ] `make check` is green (lint, format, types, security, audit, tests)
- [ ] New behaviour has a test that fails without the change
- [ ] Any new `# noqa` / `# nosec` carries a written reason

## Does it keep the bot honest?

<!-- The project's spine: a failure is reported, never rendered as a calm
     default; a number carries its provenance; advisory only, no order path.
     Say how this change respects that, or note the exception and why. -->
