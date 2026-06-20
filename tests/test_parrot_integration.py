"""Smoke test for the Parrot-native flow factory.

The flow no longer builds a native ``AgentsFlow`` topology object (the local
implementation targeted an older API); it now runs its node handlers
sequentially with ``flow.flow is None``. This test pins the current
structural contract instead of the obsolete ``flow.flow.nodes`` shape.
"""
from multicoders.factory import create_multicoders_flow
from multicoders.storage import Storage


def test_parrot_flow_factory_builds_sequential_flow():
    storage = Storage(":memory:")
    flow = create_multicoders_flow(storage=storage)

    assert flow.name == "MulticodersFlow"
    # Native AgentsFlow topology is intentionally not constructed anymore.
    assert flow.flow is None
    # The five pipeline stages are present as handlers.
    for handler in (
        "_run_research",
        "_run_dispatch",
        "_run_arena",
        "_run_qa",
        "_run_commit",
    ):
        assert callable(getattr(flow, handler))


if __name__ == "__main__":
    test_parrot_flow_factory_builds_sequential_flow()
    print("ok")
