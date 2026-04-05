# Security Policy

## Supported Versions

| Branch | Supported |
|--------|-----------|
| `18.0` | ✅ Active |
| `19.0` | ✅ Active |
| `17.0` | ⚠️ Security fixes only |

## Reporting a Vulnerability

If you discover a security vulnerability in this module, please report it responsibly:

1. **Do not** open a public GitHub issue
2. Email **info+fleet@pideuva.com** with:
   - Description of the vulnerability
   - Steps to reproduce
   - Affected version(s)
   - Potential impact

We will acknowledge receipt within 48 hours and provide an initial assessment within 5 business days.

## Security Practices

- API credentials are stored with `groups='base.group_system'` (system admin only)
- Webhook endpoints validate request signatures before processing
- No raw SQL — all database access through Odoo ORM
- Sensitive payloads are automatically purged via scheduled cron job
