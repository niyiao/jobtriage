"""jobtriage: scrape remote job boards, keep the relevant roles,
remember them in SQLite, triage them from the keyboard.

This module holds the version and nothing else. The CLI lives in
jobtriage.cli (one module per subcommand group), the merge step in
jobtriage.pipeline, and every path the tool touches belongs to
jobtriage.workspace.
"""

__version__ = '0.3.0'
