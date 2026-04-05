# Contributing to Odoo Uva Connector

This is a proprietary module licensed under OPL-1. Contributions are accepted only from authorized team members of Spark Workforce LLC.

## Branching

- `18.0` — primary development branch (Odoo 18)
- `17.0` — Odoo 17 stable
- `19.0` — Odoo 19 stable

Features are developed on `18.0` first, then backported/forward-ported.

## Branch Naming

```
18.0-feature/short-description
18.0-fix/short-description
```

## Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add delivery cost estimation wizard
fix: correct webhook signature validation
refactor: simplify retry queue processing
```

## Code Standards

- Follow [Odoo coding guidelines](https://www.odoo.com/documentation/18.0/contributing/development/coding_guidelines.html)
- All Python files must include the OPL-1 license header
- Run `./build.sh <version>` to verify headers before committing
- Never use raw SQL — always use the Odoo ORM
- Never hardcode API keys or credentials
- Add `ir.model.access.csv` entries for every new model

## Testing

Run tests before submitting:

```bash
odoo-bin -d test_db --test-enable --stop-after-init -i odoo_uva_connector
```

## Pull Requests

1. Create a feature branch from the target version branch
2. Keep PRs focused on a single change
3. Ensure all tests pass
4. Request review from a team member
