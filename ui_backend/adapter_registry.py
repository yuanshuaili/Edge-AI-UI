"""Explicit adapter allowlist; backend JSON never imports Python modules."""

from .adapters import MockAdapter, TinyChatAdapter


ADAPTER_FACTORIES = {
    "tinychat": TinyChatAdapter,
    "mock": MockAdapter,
}


def registered_adapter_kinds() -> frozenset[str]:
    return frozenset(ADAPTER_FACTORIES)


def build_adapter(profile):
    try:
        factory = ADAPTER_FACTORIES[profile.adapter]
    except KeyError as exc:
        raise ValueError(f"Unsupported adapter: {profile.adapter}") from exc
    return factory(profile)
