# The fixture repositories hold a deliberately failing test (the story's red commit): pytest must never collect it.
collect_ignore_glob = ["fixtures/*"]
