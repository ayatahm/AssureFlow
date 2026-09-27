from django.contrib.auth.hashers import Argon2PasswordHasher


class FastArgon2PasswordHasher(Argon2PasswordHasher):
    """Argon2id with low cost parameters so the suite runs quickly. Tests only."""

    time_cost = 1
    memory_cost = 1024
    parallelism = 1
