"""Entry point. Has a deliberate off-by-one for M3+ to detect."""


def first_n(xs, n):
    return xs[: n + 1]  # off-by-one: returns n+1 items
