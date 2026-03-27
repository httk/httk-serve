# Modern Search Example

Modernized replacement for the old search app example.

- Dynamic function injection from query parameters
- Jinja2 templates for page and fragment rendering
- In-memory sample dataset (no legacy `httk.db` dependency)

Run:

```bash
python serve.py
```

Then open `http://127.0.0.1:8080/` and search for terms like `si` or `oxide`.
