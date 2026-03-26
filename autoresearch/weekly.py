from __future__ import annotations

from autoresearch.cli import build_parser, run_weekly_stage


def main() -> None:
    parser = build_parser("Run the TraderJojo weekly autoresearch workflow.")
    args = parser.parse_args()
    run_weekly_stage(args)


if __name__ == "__main__":
    main()
