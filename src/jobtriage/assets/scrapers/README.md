User scraper plugins, one .py per board (plan 04). The contract and
the research playbook live in the jobtriage repo: docs/scrapers.md.

`_example.py` is the template `jobtriage sources new NAME` copies; files
whose name starts with `_` are never loaded, so it can stay here. Start
a board with:

    jobtriage sources new my-board
    jobtriage sources test my-board
