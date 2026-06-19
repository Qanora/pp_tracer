"""CLI entry point for PP Tracer."""

import click

from pptracer.config import Settings


@click.group()
@click.version_option(package_name="pp-tracer", message="%(prog)s %(version)s")
@click.pass_context
def main(ctx: click.Context):
    """PP Tracer — Point-to-Point latency tracing and network path analysis toolkit."""
    ctx.ensure_object(dict)
    ctx.obj["settings"] = Settings()


@main.command()
@click.option("--quick", is_flag=True, help="Quick mode: sample only")
@click.pass_context
def run(ctx: click.Context, quick: bool):
    """Execute tracer data collection."""
    settings: Settings = ctx.obj["settings"]
    mode = "quick" if quick else "full"
    click.echo(f"Running tracer in {mode} mode...")
    click.echo(f"  Data dir: {settings.data_dir}")
    click.echo(f"  Log level: {settings.log_level}")


@main.command()
@click.option("--last", is_flag=True, help="Show last report only")
@click.pass_context
def report(ctx: click.Context, last: bool):
    """Generate or view tracer reports."""
    click.echo("Reports: (no data yet)")


@main.command()
@click.option("--full", is_flag=True, help="Full collection mode")
@click.pass_context
def collect(ctx: click.Context, full: bool):
    """Collect trace data from configured sources."""
    click.echo("Collecting trace data...")


@main.command()
@click.option("--latency", is_flag=True, help="Focus on latency analysis")
@click.pass_context
def analyze(ctx: click.Context, latency: bool):
    """Analyze collected trace data."""
    click.echo("Analyzing trace data...")


@main.command()
@click.pass_context
def health_check(ctx: click.Context):
    """Run system health check."""
    click.echo("Health check: OK")


if __name__ == "__main__":
    main()
