"""Operator CLI for mission_config.

Usage:
  python -m megalodon_ui.mission_config init [--mission-dir PATH] [--force]
  python -m megalodon_ui.mission_config validate PATH
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _cmd_init(mission_dir: Path, force: bool, live_repl: bool) -> int:
    import yaml
    from megalodon_ui.mission_config import default_v9_0_shape, default_v9_3_live_repl

    target = mission_dir / ".mission-config.yaml"
    tmp = mission_dir / ".mission-config.yaml.tmp"

    if target.exists() and not force:
        print(
            f"error: {target} already exists. Use --force to overwrite.",
            file=sys.stderr,
        )
        return 1

    if live_repl:
        config = default_v9_3_live_repl.synthesize(mission_dir)
    else:
        config = default_v9_0_shape.synthesize(mission_dir)
    payload = yaml.safe_dump(
        config.model_dump(mode="json"),
        sort_keys=False,
        default_flow_style=False,
    )

    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, target)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise

    print(f"wrote {target}")
    return 0


def _cmd_validate(yaml_path: Path) -> int:
    import yaml
    from pydantic import ValidationError
    from megalodon_ui.mission_config.schema import MissionConfig

    try:
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        print(f"YAML parse error: {exc}", file=sys.stderr)
        return 1

    try:
        config = MissionConfig.model_validate(raw)
    except ValidationError as exc:
        print(f"Validation error: {exc}", file=sys.stderr)
        return 1

    n_lanes = len(config.lanes)
    n_phases = len(config.phases)
    print(f"OK: {n_lanes} lanes, {n_phases} phases")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m megalodon_ui.mission_config",
        description="Mission config operator CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Write default .mission-config.yaml")
    p_init.add_argument(
        "--mission-dir",
        type=Path,
        default=Path("."),
        metavar="PATH",
        help="Directory to write into (default: .)",
    )
    p_init.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing .mission-config.yaml",
    )
    p_init.add_argument(
        "--live-repl",
        action="store_true",
        help="Use the v9.3 live-REPL template (claude REPL + /loop autonomous per lane)",
    )

    p_validate = sub.add_parser("validate", help="Validate a .mission-config.yaml file")
    p_validate.add_argument("path", type=Path, metavar="PATH", help="Path to YAML file")

    args = parser.parse_args(argv)

    if args.command == "init":
        return _cmd_init(args.mission_dir, args.force, args.live_repl)
    if args.command == "validate":
        return _cmd_validate(args.path)

    parser.print_help(sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
