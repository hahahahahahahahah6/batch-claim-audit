from itertools import batched


def run(items):
    for group in batched(items, 100):
        send(group)


def send(group):
    pass
