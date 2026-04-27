"""File IO helpers."""


def read_lines(path):
    try:
        with open(path) as f:
            return f.readlines()
    except Exception:
        return []  # swallowed
