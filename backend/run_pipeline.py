"""Run AgentForge's frontend-first pipeline for any project idea.

Examples (PowerShell, from the backend2 project root):
  python run_pipeline.py --requirements-file project_requirements.txt
  python run_pipeline.py --requirements "Build a fitness coaching website with workout plans and progress tracking"
  python run_pipeline.py --requirements-file project_requirements.txt --output-dir generated_projects --no-browser

If neither --requirements nor --requirements-file is supplied, the script
accepts a multiline project brief interactively. Finish input with an empty line.
"""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from generation.project_pipeline import generate_project_pipeline


def _read_requirements(args: argparse.Namespace) -> str:
    if args.requirements_file:
        path = Path(args.requirements_file).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Requirements file not found: {path}")
        requirements = path.read_text(encoding="utf-8").strip()
    elif args.requirements:
        requirements = args.requirements.strip()
    else:
        print("Enter your project requirements. Press Enter on an empty line to finish:\n")
        lines: list[str] = []
        while True:
            try:
                line = input()
            except EOFError:
                break
            if not line.strip():
                if lines:
                    break
                continue
            lines.append(line)
        requirements = "\n".join(lines).strip()

    if not requirements:
        raise ValueError("Project requirements cannot be empty.")
    return requirements


async def _run(args: argparse.Namespace) -> None:
    if not os.getenv("GROQ_API_KEY"):
        raise RuntimeError(
            "GROQ_API_KEY is not configured. Set it in this PowerShell session first."
        )

    requirements = _read_requirements(args)
    output_dir = Path(args.output_dir).expanduser()

    print("\nStarting AgentForge project generation...")
    print(f"Output directory: {output_dir.resolve()}")
    print(f"Connected backend validation: {'enabled' if not args.skip_connected else 'disabled'}")
    print(f"Browser validation: {'enabled' if not args.no_browser and not args.skip_connected else 'disabled'}\n")

    result = await generate_project_pipeline(
        requirements=requirements,
        output_dir=output_dir,
        overwrite_frontend=args.overwrite_frontend,
        strict_frontend_validation=not args.relaxed_frontend_validation,
        seed_data=not args.no_seed_data,
        validate_connected=not args.skip_connected,
        validate_browser=not args.no_browser,
    )

    print("\n========== PIPELINE RESULT ==========")
    print(f"Project : {result['project_name']}")
    print(f"Business: {result['business_name']}")
    print(f"Frontend: {result['frontend_dir']}")
    print(f"Backend : {result['backend_dir']}")
    print(f"Seeded  : {result['seed_summary']}")
    print("Backend endpoints:")
    for endpoint in result["api_contract"]:
        print(f"  {endpoint['method']:6} {endpoint['path']}")
    print(f"API contract valid: {result['contract_result']['valid']}")
    print("\nProject generation completed successfully.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a new AgentForge project from any project brief."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--requirements",
        help="Project brief as a single quoted command-line string.",
    )
    source.add_argument(
        "--requirements-file",
        help="UTF-8 text file containing the complete project brief.",
    )
    parser.add_argument(
        "--output-dir",
        default="generated_projects",
        help="Parent directory for generated projects (default: generated_projects).",
    )
    parser.add_argument(
        "--overwrite-frontend",
        action="store_true",
        help="Allow replacing an existing generated frontend. Use carefully.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Skip Playwright browser validation but keep connected API validation.",
    )
    parser.add_argument(
        "--skip-connected",
        action="store_true",
        help="Skip running the generated backend and connected validation.",
    )
    parser.add_argument(
        "--no-seed-data",
        action="store_true",
        help="Do not generate starter JSON records for catalog collections.",
    )
    parser.add_argument(
        "--relaxed-frontend-validation",
        action="store_true",
        help="Disable strict frontend validation during generation.",
    )
    args = parser.parse_args()

    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\nGeneration cancelled by user.")
        raise SystemExit(130)
    except Exception as exc:
        print(f"\nERROR: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
