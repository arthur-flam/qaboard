# /// script
# requires-python = ">=3.11"
# dependencies = ["inspect-ai==0.3.292"]
# ///
"""
Writes inspect-new.json and inspect-reference.json next to this file: the logs of a small Inspect eval
(https://inspect.aisi.org.uk/) with a mock model, 2 samples x 2 epochs, a tool call each, one tool error.
The reference has another system prompt and other answers. The transcript viewer's tests read them
(webapp/src/viewers/transcript/__tests__/).

    uv run tests/fixtures/transcripts/make_inspect.py

Ids and times change at each run: the tests don't depend on them.
"""
import glob
import json
import shutil
import tempfile
from pathlib import Path

import tiktoken


class _Words:
    """Inspect counts tokens with tiktoken, which downloads its vocabulary: count words instead, offline."""
    def encode(self, text, **kwargs):
        return text.split()


tiktoken.get_encoding = lambda name: _Words()
tiktoken.encoding_for_model = lambda name: _Words()

from inspect_ai import Task, eval  # noqa: E402
from inspect_ai.dataset import Sample  # noqa: E402
from inspect_ai.model import ModelOutput, get_model  # noqa: E402
from inspect_ai.scorer import includes  # noqa: E402
from inspect_ai.solver import generate, system_message, use_tools  # noqa: E402
from inspect_ai.tool import ToolError, tool  # noqa: E402


@tool
def battery():
    async def execute(robot: str):
        """Read a robot's battery level.

        Args:
            robot: The robot's id.
        """
        if robot == "r9":
            raise ToolError("unknown robot r9")
        return {"robot": robot, "battery": 0.42}
    return execute


def run(name, system, answers):
    task = Task(
        name="fleet",
        dataset=[Sample(id="battery-r2", input="What is r2's battery?", target="42%"),
                 Sample(id="battery-r9", input="What is r9's battery?", target="unknown")],
        solver=[system_message(system), use_tools(battery()), generate()],
        scorer=includes(),
    )
    # one sample at a time (max_samples=1): the mock model's answers are used in this order
    outputs = []
    for epoch in range(2):
        for robot in ["r2", "r9"]:
            outputs += [
                ModelOutput.for_tool_call("mockllm/model", "battery", {"robot": robot}, tool_call_id=f"call_{robot}_{epoch + 1}"),
                ModelOutput.from_content("mockllm/model", answers[robot][epoch]),
            ]
    log_dir = tempfile.mkdtemp()
    try:
        eval(task, model=get_model("mockllm/model", custom_outputs=outputs), epochs=2, log_format="json", log_dir=log_dir, display="none", max_samples=1)
        log = json.loads(Path(glob.glob(f"{log_dir}/*.json")[0]).read_text())
    finally:
        shutil.rmtree(log_dir, ignore_errors=True)
    (Path(__file__).parent / f"inspect-{name}.json").write_text(json.dumps(log, indent=1) + "\n")
    print(name, log["status"], [(s["id"], s["epoch"], s["scores"]["includes"]["value"]) for s in log["samples"]])


run("reference", "You help robot fleet operators. Read a robot's state with the tools before you answer.", {
    "r2": ["r2 is at 42%.", "r2's battery is at 42%."],
    "r9": ["I can't find r9: the fleet says it is unknown.", "r9 is unknown to the fleet."],
})
run("new", "You help robot fleet operators.", {
    "r2": ["r2 is at 42%.", "The battery of r2 is fine."],
    "r9": ["r9 is at 42%.", "r9 is unknown to the fleet."],
})
