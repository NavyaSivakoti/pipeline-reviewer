"""
run.py — review a pipeline failure.

Usage:
    python run.py                                    # default sample
    python run.py sample_data/github_actions_failure.log
    python run.py NavyaSivakoti/demo-app 28689439589 # a live GitHub Actions run
"""

import sys

from agent_runner import run_agent, print_report

DEFAULT = ["sample_data/failing_pipeline.log", "sample_data/junit_results.xml"]


def _show_tool_calls(event) -> None:
    try:
        for part in (event.content.parts or []):
            if getattr(part, "function_call", None):
                print(f"   -> calling tool: {part.function_call.name}()", file=sys.stderr)
    except AttributeError:
        pass


def main() -> None:
    paths = sys.argv[1:] or DEFAULT
    print("\n>>> Reviewing pipeline...\n", file=sys.stderr)
    state = run_agent(paths, on_event=_show_tool_calls)
    print_report(state)


if __name__ == "__main__":
    main()
