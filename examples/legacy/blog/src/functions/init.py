import datetime
import os
from pathlib import Path


def execute(global_data, **kargs):
    prefix = Path(__file__).resolve().parents[1] / "content"
    filterlist = [".md", ".rst", ".html"]
    path = "blogposts"

    # Chicken-or-egg problem of having to partially render blog posts to sort them, but their renders refer to the other blogposts
    global_data['blogposts'] = []
    global_data['blogposts_latest'] = []

    def listdirsorted(path):
        return [
            x[0]
            for x in sorted(
                [
                    (
                        fn,
                        datetime.datetime.strptime(
                            str(global_data['pages'](os.path.join(path, fn), 'date')), "%Y-%m-%d"
                        ),
                    )
                    for fn in os.listdir(prefix / path)
                ],
                key=lambda x: x[1],
                reverse=True,
            )
        ]

    global_data['blogposts'] = [
        os.path.join(path, f)
        for f in listdirsorted(path)
        if os.path.isfile(prefix / path / f) and any([f.endswith(t) for t in filterlist])
    ]

    global_data['blogposts_latest'] = global_data['blogposts'][:5]
