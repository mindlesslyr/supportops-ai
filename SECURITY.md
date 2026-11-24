# Security Policy

## Supported versions

SupportOps AI is currently an unreleased MVP. Security fixes are applied to the latest commit on `main`; older commits and local forks are not supported.

## Reporting a vulnerability

Do not open a public issue containing exploit details, credentials, customer data, or a working proof of concept.

Use the repository's **Security** tab to submit a private vulnerability report. If private reporting is unavailable, open a minimal public issue asking the maintainer to establish a private contact channel, without including technical details.

Include, when possible:

- affected commit or configuration
- impact and realistic attack scenario
- minimal reproduction steps
- suggested mitigation
- whether the issue is already public

## Deployment scope

The included Docker Compose stack is for local development. It binds services to loopback, but it is not a complete internet-facing production deployment. Production operators must add TLS, network controls, secret management, rate limits, backups, monitoring, and a supported database and queue strategy.
