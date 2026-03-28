# Site Template Repository

A ready-to-use template repository for new `httk.web` sites is available at:

- https://github.com/httk/example_website_httk

## Create a new site on GitHub

1. Open the template repository URL in your browser.
2. Click **Use this template** (top-right on GitHub).
3. Choose **Create a new repository**.
4. Set repository name/visibility and create it.

## Clone and run locally

After creating your repository, clone it and run:

```bash
git clone https://github.com/<your-user>/<your-new-repo>.git
cd <your-new-repo>
python -m pip install -e .
make serve
```

## Edit site content

The main content is under `src/content`:

- Edit existing pages like `src/content/index.md` and `src/content/contact.md`.
- Add new pages by creating `.md` files in `src/content`.
- Blog posts are in `src/content/blogposts`.

Then regenerate/publish:

```bash
make generate
```

This writes output to `public/`.

If you need `.html` links for a static host, pass `use_urls_without_ext=False`
to `httk.web.publish(...)` in your publish script.

## GitHub Pages publishing

The template repository includes a GitHub Actions workflow for Pages publishing.
After enabling GitHub Pages for the repository, pushes to `main` will build and publish the site automatically.
