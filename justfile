lint:
    uv run ruff format
    uv run ruff check --fix
    uv run pyright --level error
    uv run pytest

nb:
    uv run nbstripout nbs/**.ipynb

sync:
    uv sync --all-groups --all-extras
