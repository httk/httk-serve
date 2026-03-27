from pathlib import Path


def execute(global_data, **kwargs):
    content_root = Path(__file__).resolve().parents[1] / "content" / "blogposts"
    posts = []
    for path in sorted(content_root.glob("*.md")):
        rel = f"blogposts/{path.stem}"
        date = global_data["pages"](rel, "date")
        posts.append((str(date or ""), rel))

    posts.sort(reverse=True)
    global_data["blogposts"] = [rel for _, rel in posts]
    global_data["blogposts_latest"] = global_data["blogposts"][:5]
