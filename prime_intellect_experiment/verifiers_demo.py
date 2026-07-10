"""
Prime Intellect Verifiers Experiment: RL Environment Demo
=========================================================
This demonstrates how to create RL training environments using
Prime Intellect's verifiers library — the same foundation used
to train INTELLECT-3 (100B+ MoE model) and other frontier models.

A "verifier" environment packages everything needed to train/evaluate an LLM:
- A dataset of task inputs
- A harness for the model (tools, sandboxes, context management)
- A reward function/rubric to score the model's performance

Usage:
    pip install verifiers  # if not already installed
    python verifiers_demo.py
"""

import sys
import os

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "verifiers")
)

try:
    import verifiers as vf

    VERIFIERS_AVAILABLE = True
except ImportError:
    VERIFIERS_AVAILABLE = False


def demo_verifiers_concepts():
    """Walk through the key concepts of Prime Intellect's verifiers library."""

    print("=" * 70)
    print("  PRIME INTELLECT VERIFIERS - RL ENVIRONMENT CONCEPTS")
    print("=" * 70)
    print()

    concepts = [
        (
            "vf.Environment",
            "Base class for all RL training environments. Contains dataset,"
            " harness (how the model interacts), and rubric (scoring).",
        ),
        (
            "vf.SingleTurnEnv",
            "For simple Q&A tasks: model gets a question, gives an answer,"
            " gets scored. Used for math (GSM8K), factual QA, etc.",
        ),
        (
            "vf.ToolEnv",
            "For agentic tasks: model can call tools (calculator, search,"
            " file ops). Multi-turn with tool-use loops.",
        ),
        (
            "vf.SandboxEnv",
            "For code execution: model writes code in a sandboxed env,"
            " sees results. Used for SWE-bench, code tasks.",
        ),
        (
            "vf.Taskset / vf.Harness / vf.Env",
            "The modern v1 architecture: Taskset owns data + prompts,"
            " Harness owns execution + sandboxing, Env wires them together.",
        ),
        (
            "vf.Rubric",
            "The reward function. Can be simple (exact match) or complex"
            " (multiple judges, partial credit, execution-based verification).",
        ),
    ]

    for name, desc in concepts:
        print(f"  [{name}]")
        print(f"    {desc}")
        print()

    print("-" * 70)
    print("  ARCHITECTURE FLOW:")
    print("  -----------------")
    print("""
    Dataset --> Task Generation --> Model Interaction --> Scoring
      |                                    |                   |
      |  Questions, problems, etc.         |  Single-turn,     |  Rubric,
      |  Loaded from HF datasets or        |  multi-turn,      |  reward
      |  custom sources                    |  tool-use,        |  functions,
      |                                    |  sandboxed        |  partial credit
      v                                    v                   v
    vf.load_example_dataset()    vf.SingleTurnEnv()    vf.Rubric(funcs=[...])
    """)


def create_simple_math_env():
    """Create a minimal verifiers environment for math problems."""

    if not VERIFIERS_AVAILABLE:
        print("  Verifiers library not installed. Run: pip install verifiers")
        return

    print("=" * 70)
    print("  CREATING A SIMPLE MATH VERIFIER ENVIRONMENT")
    print("=" * 70)
    print()

    try:
        # Load the GSM8K dataset (grade-school math word problems)
        dataset = vf.load_example_dataset("gsm8k")
        print(f"  Loaded dataset: {len(dataset)} examples")
        print(f"  Sample keys: {list(dataset[0].keys())}")
        print()

        # Define a reward function: check if the model's answer matches
        async def correct_answer(completion, answer) -> float:
            completion_ans = completion[-1]["content"]
            return 1.0 if completion_ans == answer else 0.0

        # Create the rubric
        rubric = vf.Rubric(funcs=[correct_answer])

        # Create the environment
        env = vf.SingleTurnEnv(dataset=dataset, rubric=rubric)

        print("  Created environment successfully!")
        print(f"  Environment type: {type(env).__name__}")
        print()
        print("  This environment can now be used with prime-rl for")
        print("  reinforcement learning training at ANY scale -- from")
        print("  a single GPU to 1,000+ GPU clusters.")
        print()
        print("  Integration with prime-rl:")
        print("    prime-rl reads this environment and:")
        print("    1. Samples tasks from the dataset")
        print("    2. Generates model completions via vLLM")
        print("    3. Scores completions with the rubric")
        print("    4. Computes advantages and updates model weights")
        print("    5. Repeats asynchronously at massive scale")

    except Exception as e:
        print(f"  Note: {e}")
        print("  The concepts are valid but the environment library")
        print("  may need additional system dependencies (torch, etc.)")


def demo_tool_env():
    """Show how a ToolEnv works conceptually."""

    print()
    print("-" * 70)
    print("  TOOL-BASED AGENTIC ENVIRONMENT (Conceptual)")
    print("  ------------------------------------------")
    print("""
  # This is what a ToolEnv setup looks like:
  
  def calculator(expression: str) -> float:
      '''Evaluate mathematical expressions.'''
      return eval(expression)  # In production, use safe evaluation
  
  def search(query: str) -> str:
      '''Search a knowledge base.'''
      return knowledge_base.search(query)
  
  # The environment lets the model decide WHEN to use tools
  env = vf.ToolEnv(
      dataset=dataset,
      tools=[calculator, search],
      rubric=rubric,
  )
  
  # During training, the model's conversation might look like:
  # Model: "Let me calculate 125 * 3.14..."
  # Tool:  calculator("125 * 3.14") -> 392.5
  # Model: "Now let me search for the formula..."
  # Tool:  search("circle area formula") -> "A = pi * r^2"
  # Model: "The answer is 392.5 square units."
  
  This is how Prime Intellect trains SWE agents and
  tool-using models at scale.
    """)


def run():
    demo_verifiers_concepts()
    create_simple_math_env()
    demo_tool_env()

    print()
    print("=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    print()
    print("  Prime Intellect provides three integrated open-source libraries:")
    print()
    print("  1. rlm (Recursive Language Models)")
    print("     - Inference engine for infinite-context exploration")
    print("     - MCP server for Cursor IDE integration")
    print("     - Used by: experiment.py, rlm-mcp-server")
    print()
    print("  2. verifiers (RL Environments)")
    print("     - Create datasets, harnesses, and rubrics")
    print("     - Powers the Environments Hub at app.primeintellect.ai")
    print("     - Used by: verifiers_demo.py")
    print()
    print("  3. prime-rl (Async RL Training)")
    print("     - Train 1T+ parameter models on 1000+ GPUs")
    print("     - Powers INTELLECT-3, GLM-5, and other frontier models")
    print("     - Integrated with vLLM, FSDP2, DeepEP")
    print()


if __name__ == "__main__":
    run()
