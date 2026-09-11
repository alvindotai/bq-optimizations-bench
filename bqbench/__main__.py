"""Command-line entry point.

    python3 -m bqbench --help
    python3 -m bqbench estimate --matrix all
    python3 -m bqbench run --matrix core
    python3 -m bqbench analyze
"""
import argparse
import importlib
import sys

# Subcommand name -> module under bqbench.commands. Order is the order a first
# run should follow, and the order --help lists them in.
COMMANDS = [
    ("estimate", "estimate"),
    ("fixtures", "fixtures"),
    ("run", "run"),
    ("analyze", "analyze"),
    ("rebuild", "rebuild"),
    ("economics", "economics"),
    ("results-doc", "results_doc"),
    ("verify", "verify"),
    ("check-leaks", "leaks"),
]


def build_parser():
    parser = argparse.ArgumentParser(
        prog="bqbench",
        description="Benchmarks for BigQuery query-optimization folklore.",
        epilog="Run `bqbench <command> --help` for a command's own options. "
               "Set BENCH_PROJECT before any command that bills BigQuery.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    subs = parser.add_subparsers(dest="command", metavar="<command>")
    for name, module_name in COMMANDS:
        module = importlib.import_module(f".commands.{module_name}", __package__)
        sub = subs.add_parser(name, help=getattr(module, "HELP", None),
                              description=module.__doc__,
                              formatter_class=argparse.RawDescriptionHelpFormatter)
        if hasattr(module, "add_arguments"):
            module.add_arguments(sub)
        sub.set_defaults(_run=module.main)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "_run", None):
        parser.print_help()
        return 2
    return args._run(args)


if __name__ == "__main__":
    sys.exit(main())
