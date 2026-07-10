"""
Prime Intellect RLM Experiment: Standalone Demo
================================================
This demonstrates Recursive Language Models (RLMs) — the core innovation
from Prime Intellect's research — in a self-contained script.

RLM treats large contexts as external environments. Instead of stuffing
everything into the LLM's context window, the LLM writes Python code to
programmatically explore the data. This enables analyzing arbitrarily large
documents that would never fit in a normal context window.

Requirements:
    pip install openai python-dotenv

Usage:
    1. Set OPENAI_API_KEY in your environment or a .env file
    2. python experiment.py
"""

import os
import sys

# Allow running from any directory
sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "rlm-mcp-server", "src"),
)

from dotenv import load_dotenv

load_dotenv()

import asyncio
from rlm_core import RLM, RLMConfig


# ============================================================================
# Step 1: A sample dataset for the RLM to explore (simulating a large corpus)
# ============================================================================

SAMPLE_DATA = """
# Global Energy Production Report 2025

## Executive Summary
This report details global energy production trends across multiple sectors
including solar, wind, nuclear, hydroelectric, and fossil fuels. Data covers
the period from 2020 to 2025 across 47 countries.

## Solar Energy
Solar photovoltaic capacity reached 1,200 GW globally in 2025, up from 580 GW
in 2020. China leads with 420 GW installed, followed by the USA with 180 GW,
India with 95 GW, Japan with 78 GW, and Germany with 72 GW.

### Efficiency Improvements
Average panel efficiency rose from 20.5% in 2020 to 25.3% in 2025. Perovskite
tandem cells achieved 33.7% in lab conditions. Manufacturing costs dropped 42%
over the five-year period.

### Notable Projects
- Desert Sun (Morocco): 4,000 MW, completed Q3 2024
- Gobi Solar Farm (China): 8,500 MW, phase 1 operational Q1 2025
- Texas Sunbelt (USA): 3,200 MW, under construction
- Rajasthan Array (India): 2,800 MW, completed Q4 2024

## Wind Energy
Global wind capacity reached 980 GW in 2025. Offshore wind grew 180% from 2020
levels. The UK leads offshore with 35 GW, followed by China at 32 GW.

### Turbine Technology
Average turbine rating increased from 3.4 MW (2020) to 5.8 MW (2025). The
largest commercial turbine, the Vestas V236-15.0 MW, entered service in 2024.

### Key Installations
- Dogger Bank (UK): 3,600 MW offshore, phases A-B operational
- Gansu Wind Farm (China): 20,000 MW total capacity (onshore)
- Hollandse Kust (Netherlands): 1,500 MW offshore
- Markbygden (Sweden): 4,000 MW onshore

## Nuclear Energy
Global nuclear capacity stands at 396 GW across 440 reactors. France generates
68% of its electricity from nuclear. China has 22 reactors under construction.

### New Builds
- Hinkley Point C (UK): 3,260 MW, expected 2027
- Akkuyu (Turkey): 4,800 MW, first unit operational 2024
- Barakah (UAE): 5,600 MW, fully operational 2025
- Vogtle 3&4 (USA): 2,500 MW, operational 2024

## Hydroelectric
Global hydro capacity: 1,330 GW. The Three Gorges Dam (China) remains the
largest at 22,500 MW. Brazil's Itaipu Dam produces 14,000 MW.

## Fossil Fuels
Coal: 2,100 GW capacity, declining 2.8% annually. China and India account for
64% of global coal consumption. Gas: 1,800 GW, growing 1.2% annually. Oil:
450 GW for power generation, declining 1.5% annually.

### Emissions Impact
Energy sector CO2 emissions: 36.8 billion tonnes in 2025, up 0.9% from 2024.
Renewables displaced approximately 2.3 billion tonnes of CO2 annually.

## Investment Trends
Global energy investment reached $2.8 trillion in 2025. Clean energy attracted
$1.8 trillion (64%), fossil fuels $1.0 trillion (36%). Solar alone drew $500B.

### Regional Breakdown
- Asia-Pacific: $1.2 trillion (43% of global)
- Europe: $650 billion (23%)
- North America: $550 billion (20%)
- Middle East & Africa: $250 billion (9%)
- Latin America: $150 billion (5%)

## Employment
The energy sector employs approximately 67 million people globally:
- Solar: 4.9 million jobs
- Wind: 1.4 million jobs
- Nuclear: 1.1 million jobs
- Hydro: 2.3 million jobs
- Fossil fuels: 18.5 million jobs
- Other/transitioning: 38.8 million jobs

## Future Outlook
Projections for 2030:
- Solar: 2,500 GW target
- Wind: 1,400 GW target
- Nuclear: 450 GW target
- Coal phase-down: 1,500 GW target
- Global investment needed: $4.5 trillion/year by 2030
"""

# ============================================================================
# Step 2: The RLM Query Engine
# ============================================================================


async def run_rlm_query(question: str, context: str) -> None:
    """Run a single RLM query and display the full reasoning trace."""

    config = RLMConfig(
        model=os.getenv("RLM_MODEL", "gpt-4o-mini"),
        api_key=os.getenv("OPENAI_API_KEY", ""),
        max_iterations=10,
        verbose=True,  # Show the full reasoning trace
    )

    rlm = RLM(config)
    try:
        print(f"\n{'=' * 70}")
        print(f"QUESTION: {question}")
        print(f"{'=' * 70}")

        result = await rlm.query(question, context)

        print(f"\n{'-' * 70}")
        print("FINAL ANSWER:")
        print(f"{'-' * 70}")
        print(result.answer)
        print(f"\nIterations: {result.iterations} | Tokens used: {result.total_tokens}")

        if result.error:
            print(f"\nERROR: {result.error}")

    finally:
        await rlm.close()


async def main():
    """Run the RLM experiment with multiple queries."""

    # Check for API key
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key or api_key == "sk-your-key-here":
        print("=" * 70)
        print("  RLM EXPERIMENT - SETUP REQUIRED")
        print("=" * 70)
        print()
        print("  To run this experiment, you need an OpenAI API key.")
        print()
        print("  Option 1: Set environment variable")
        print("    $env:OPENAI_API_KEY = 'sk-...'     # PowerShell")
        print()
        print("  Option 2: Create a .env file in this directory")
        print("    Copy .env.example to .env and add your key")
        print()
        print("=" * 70)

        # Still show what RLM can do without an API key
        print()
        print("  WHAT RLM DOES (no API key needed to read this):")
        print("  " + "-" * 48)
        print(f"  Context loaded: {len(SAMPLE_DATA):,} characters")
        print(f"  Context lines:  {SAMPLE_DATA.count(chr(10)) + 1}")
        print()
        print("  The RLM engine would:")
        print("  1. Load this {:,}-char document as external CONTEXT".format(len(SAMPLE_DATA)))
        print("  2. Have an LLM write Python code to explore it")
        print("  3. Execute the code and feed results back")
        print("  4. Iterate until it can answer the question")
        print()
        print("  Example queries RLM could handle:")
        print('  - "What is the total global solar capacity in 2025?"')
        print('  - "Which country leads in offshore wind?"')
        print('  - "How many people work in solar vs fossil fuels?"')
        print('  - "What percentage of investment goes to clean energy?"')
        print()
        print("  Add your API key and run again for the full demo!")
        return

    print("=" * 70)
    print("  PRIME INTELLECT RLM EXPERIMENT")
    print("  Recursive Language Models in Action")
    print("=" * 70)
    print(f"\n  Context: {len(SAMPLE_DATA):,} chars of energy report data")
    print(f"  Model:   {os.getenv('RLM_MODEL', 'gpt-4o-mini')}")
    print()

    # Run three queries demonstrating RLM capabilities

    queries = [
        "What is the total global solar capacity in 2025 across all countries mentioned?",
        "Which country leads in offshore wind capacity, and by how much?",
        "Compare clean energy investment ($1.8T) to fossil fuel investment ($1.0T). What percentage of total investment is clean energy?",
    ]

    for q in queries:
        await run_rlm_query(q, SAMPLE_DATA)


if __name__ == "__main__":
    asyncio.run(main())
