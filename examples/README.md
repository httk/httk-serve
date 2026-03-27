# Examples

This directory contains runnable `httk-web` examples.

- `modern/`: recommended v2-first examples (Jinja2 templates, modern metadata).
- `legacy/`: compatibility-mode examples that use deprecated legacy syntax/features.

Modern includes:
- `minimal/`
- `rst_site/` (modern replacement for `rst_templator`)
- `blog/` (modern replacement for legacy blog example)
- `search_app/` (modern replacement for legacy search example)

Legacy now includes migrated variants of the original `6_website` examples,
including blog/search-style examples.

Legacy examples are kept to validate migration behavior and should not be used as the default style for new projects.
