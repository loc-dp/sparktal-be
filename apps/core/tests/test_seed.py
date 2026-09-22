from apps.core.seed import SeedContext, isolated_registry, register_seeder, run_seeders


def test_seeders_run_in_order_and_share_context() -> None:
    order: list[str] = []

    with isolated_registry():

        @register_seeder(order=99)
        def seed_late(ctx: SeedContext) -> None:
            order.append("late")
            ctx.refs["late"] = True

        @register_seeder(order=98)
        def seed_early(ctx: SeedContext) -> None:
            order.append("early")
            ctx.refs["early"] = True

        ctx = SeedContext()
        ran = run_seeders(ctx)

    assert order.index("early") < order.index("late")
    assert ctx.refs["early"] is True
    assert ctx.refs["late"] is True
    assert any(name.endswith("seed_early") for name in ran)
